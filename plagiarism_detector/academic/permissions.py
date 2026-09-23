"""
Role-based access control (thesis section 2.1.1).

The original build had no authorization at all beyond ``@login_required``: any
authenticated user could delete another teacher's course by visiting a URL, and
``upload_submission`` had no decorator whatsoever, so any user could overwrite
any student's file by guessing an id.
"""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def profile_of(user):
    return getattr(user, 'userprofile', None)


def is_student(user) -> bool:
    profile = profile_of(user)
    return bool(profile and profile.is_student)


def is_teacher(user) -> bool:
    profile = profile_of(user)
    return bool(profile and profile.is_teacher)


def is_admin(user) -> bool:
    if getattr(user, 'is_superuser', False):
        return True
    profile = profile_of(user)
    return bool(profile and profile.is_admin)


def _require(test, message):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            if not test(request.user):
                messages.error(request, message)
                raise PermissionDenied(message)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


teacher_required = _require(
    lambda u: is_teacher(u) or is_admin(u),
    'This page is available to teachers only.',
)

student_required = _require(
    lambda u: is_student(u) or is_admin(u),
    'This page is available to students only.',
)

admin_required = _require(
    is_admin,
    'This page is available to administrators only.',
)


def owns_course(user, course) -> bool:
    return is_admin(user) or course.teacher_id == user.id


def owns_assignment(user, assignment) -> bool:
    return owns_course(user, assignment.course)


def assert_owns_course(user, course):
    if not owns_course(user, course):
        raise PermissionDenied('You do not teach this course.')


def assert_owns_assignment(user, assignment):
    if not owns_assignment(user, assignment):
        raise PermissionDenied('You do not teach the course this assignment belongs to.')

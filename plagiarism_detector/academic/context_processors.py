"""Template context shared across every page."""

from .permissions import is_admin, is_student, is_teacher


def role_flags(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {'is_student_user': False, 'is_teacher_user': False, 'is_admin_user': False}
    return {
        'is_student_user': is_student(user),
        'is_teacher_user': is_teacher(user),
        'is_admin_user': is_admin(user),
    }

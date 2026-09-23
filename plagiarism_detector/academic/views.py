"""
Views for SmartAnalytica.

Every view that touches a course, assignment or submission now checks
ownership. The report views are new: the original ``plagiarism_reports``
queried a model that did not exist, and ``generate_report`` called the
comparison function with the wrong arguments and read a field
(``total_marks``) that was never on the model.
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Max, Q
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .detection.pipeline import METHOD_LABELS, run_detection_for_assignment
from .forms import (
    AssignmentForm,
    CourseForm,
    DetectionRunForm,
    EnrollStudentsForm,
    RegistrationForm,
    SubmissionForm,
)
from .models import (
    ActivityLog,
    Assignment,
    Course,
    Match,
    PlagiarismReport,
    Submission,
    SubmissionResult,
    UserProfile,
)
from .permissions import (
    admin_required,
    assert_owns_assignment,
    assert_owns_course,
    is_admin,
    is_student,
    is_teacher,
    student_required,
    teacher_required,
)
from .services import ensure_extracted, log_activity


# --------------------------------------------------------------------------
# Public pages and authentication
# --------------------------------------------------------------------------

def home(request):
    return render(request, 'academic/home.html')


def register(request):
    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            log_activity(request, 'register', f'New {form.cleaned_data["role"]}: {user.username}')
            messages.success(request, 'Account created. You can sign in now.')
            return redirect('login')

        # A failed attempt to claim the teacher role is security relevant, so
        # it goes in the audit log even though no account was created.
        if request.POST.get('role') == 'teacher' and 'teacher_access_code' in form.errors:
            log_activity(
                request, 'error',
                'Rejected teacher registration for username '
                f'"{request.POST.get("username", "")[:40]}": bad access code',
            )
        messages.error(request, 'Please correct the errors below.')
    else:
        form = RegistrationForm()
    return render(request, 'academic/register.html', {'form': form})


def login_user(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = authenticate(
                username=form.cleaned_data['username'],
                password=form.cleaned_data['password'],
            )
            if user is not None:
                login(request, user)
                log_activity(request, 'login', f'{user.username} signed in')
                return redirect('dashboard')
        # TC1: invalid credentials must show validation errors, not a bare
        # HttpResponse (which the original returned without importing it).
        messages.error(request, 'Invalid username or password.')
    else:
        form = AuthenticationForm()
    return render(request, 'academic/login.html', {'form': form})


def logout_user(request):
    if request.user.is_authenticated:
        log_activity(request, 'logout', f'{request.user.username} signed out')
    logout(request)
    return redirect('home')


@login_required
def dashboard(request):
    """Send each user to the dashboard matching their role."""
    if is_admin(request.user):
        return redirect('admin_dashboard')
    if is_teacher(request.user):
        return redirect('teacher_dashboard')
    if is_student(request.user):
        return redirect('student_dashboard')
    messages.warning(request, 'Your account has no role assigned. Contact an administrator.')
    return redirect('home')


# --------------------------------------------------------------------------
# Teacher
# --------------------------------------------------------------------------

@teacher_required
def teacher_dashboard(request):
    courses = Course.objects.filter(teacher=request.user).annotate(
        student_count=Count('students', distinct=True),
        assignment_count=Count('assignments', distinct=True),
    )
    assignments = Assignment.objects.filter(course__teacher=request.user).select_related('course')
    recent_reports = PlagiarismReport.objects.filter(
        assignment__course__teacher=request.user
    ).select_related('assignment')[:5]

    return render(request, 'academic/teacher_dashboard.html', {
        'courses': courses,
        'assignment_count': assignments.count(),
        'submission_count': Submission.objects.filter(
            assignment__course__teacher=request.user
        ).count(),
        'flagged_count': SubmissionResult.objects.filter(
            report__assignment__course__teacher=request.user, flagged=True
        ).count(),
        'recent_reports': recent_reports,
        'upcoming': assignments.filter(due_date__gte=timezone.now()).order_by('due_date')[:5],
    })


@teacher_required
def create_course(request):
    if request.method == 'POST':
        form = CourseForm(request.POST)
        if form.is_valid():
            course = form.save(commit=False)
            course.teacher = request.user
            course.save()
            log_activity(request, 'course_create', f'Created course {course.course_code}',
                         course_id=course.pk)
            messages.success(request, f'Course "{course.name}" created.')
            return redirect('manage_courses')
    else:
        form = CourseForm()
    return render(request, 'academic/create_course.html', {'form': form})


@teacher_required
def manage_courses(request):
    courses = Course.objects.filter(teacher=request.user).annotate(
        student_count=Count('students', distinct=True),
        assignment_count=Count('assignments', distinct=True),
    )
    return render(request, 'academic/manage_courses.html', {'courses': courses})


@teacher_required
def edit_course(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    assert_owns_course(request.user, course)

    if request.method == 'POST':
        form = CourseForm(request.POST, instance=course)
        if form.is_valid():
            form.save()
            messages.success(request, 'Course updated.')
            return redirect('manage_courses')
    else:
        form = CourseForm(instance=course)
    return render(request, 'academic/edit_course.html', {'form': form, 'course': course})


@teacher_required
@require_POST
def delete_course(request, course_id):
    """POST only: the original accepted GET, so any link could delete a course."""
    course = get_object_or_404(Course, id=course_id)
    assert_owns_course(request.user, course)
    name = course.name
    course.delete()
    log_activity(request, 'course_delete', f'Deleted course {name}')
    messages.success(request, f'Course "{name}" deleted.')
    return redirect('manage_courses')


@teacher_required
def course_details(request, course_id):
    course = get_object_or_404(
        Course.objects.prefetch_related('assignments', 'students'), pk=course_id
    )
    assert_owns_course(request.user, course)
    return render(request, 'academic/course_details.html', {'course': course})


@teacher_required
def create_assignment(request):
    if request.method == 'POST':
        form = AssignmentForm(request.POST, request.FILES, teacher=request.user)
        if form.is_valid():
            assignment = form.save()
            log_activity(request, 'assignment_create', f'Created assignment {assignment.title}',
                         assignment_id=assignment.pk)
            messages.success(request, f'Assignment "{assignment.title}" created.')
            return redirect('course_details', course_id=assignment.course_id)
    else:
        form = AssignmentForm(teacher=request.user)

    if not form.fields['course'].queryset.exists():
        messages.info(request, 'Create a course before adding assignments.')
    return render(request, 'academic/create_assignment.html', {'form': form})


@teacher_required
def view_assignments(request, course_id=None):
    assignments = Assignment.objects.filter(
        course__teacher=request.user
    ).select_related('course').annotate(submission_count=Count('submissions'))

    if course_id:
        course = get_object_or_404(Course, id=course_id)
        assert_owns_course(request.user, course)
        assignments = assignments.filter(course=course)

    return render(request, 'academic/view_assignments.html', {'assignments': assignments})


@teacher_required
def edit_assignment(request, assignment_id):
    assignment = get_object_or_404(Assignment, id=assignment_id)
    assert_owns_assignment(request.user, assignment)

    if request.method == 'POST':
        form = AssignmentForm(request.POST, request.FILES, instance=assignment, teacher=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Assignment updated.')
            return redirect('view_assignments')
    else:
        form = AssignmentForm(instance=assignment, teacher=request.user)
    return render(request, 'academic/edit_assignment.html',
                  {'form': form, 'assignment': assignment})


@teacher_required
@require_POST
def delete_assignment(request, assignment_id):
    assignment = get_object_or_404(Assignment, id=assignment_id)
    assert_owns_assignment(request.user, assignment)
    title = assignment.title
    assignment.delete()
    log_activity(request, 'assignment_delete', f'Deleted assignment {title}')
    messages.success(request, f'Assignment "{title}" deleted.')
    return redirect('view_assignments')


@teacher_required
def enroll_students(request):
    if request.method == 'POST':
        form = EnrollStudentsForm(request.POST, teacher=request.user)
        if form.is_valid():
            course = form.cleaned_data['course']
            assert_owns_course(request.user, course)
            students = form.cleaned_data['students']
            already = set(course.students.values_list('id', flat=True))
            added = [s for s in students if s.id not in already]
            course.students.add(*added)
            log_activity(request, 'enroll', f'Enrolled {len(added)} student(s) in {course.name}',
                         course_id=course.pk)
            if added:
                messages.success(request, f'Enrolled {len(added)} student(s) in {course.name}.')
            else:
                messages.info(request, 'Those students were already enrolled.')
            return redirect('enroll_students')
    else:
        form = EnrollStudentsForm(teacher=request.user)

    return render(request, 'academic/add_students.html', {
        'form': form,
        'courses': Course.objects.filter(teacher=request.user).prefetch_related('students'),
    })


# --------------------------------------------------------------------------
# Student
# --------------------------------------------------------------------------

@student_required
def student_dashboard(request):
    courses = Course.objects.filter(students=request.user).prefetch_related('assignments')
    submissions = {
        s.assignment_id: s
        for s in Submission.objects.filter(student=request.user)
    }

    course_rows = []
    for course in courses:
        rows = []
        for assignment in course.assignments.all():
            rows.append({
                'assignment': assignment,
                'submission': submissions.get(assignment.id),
                'is_past_due': assignment.is_past_due,
            })
        course_rows.append({'course': course, 'rows': rows})

    return render(request, 'academic/student_dashboard.html', {
        'course_rows': course_rows,
        'submission_count': len(submissions),
    })


@student_required
def submit_assignment(request, assignment_id):
    """
    Upload or replace this student's own submission.

    The original ``upload_submission`` took a submission id, had no login
    requirement and no ownership check, and called ``get_or_create`` on that
    id, so any user could overwrite any other student's file.
    """
    assignment = get_object_or_404(Assignment, id=assignment_id)

    if not assignment.course.students.filter(id=request.user.id).exists():
        raise PermissionDenied('You are not enrolled in this course.')

    existing = Submission.objects.filter(assignment=assignment, student=request.user).first()

    if request.method == 'POST':
        form = SubmissionForm(request.POST, request.FILES, instance=existing)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.assignment = assignment
            submission.student = request.user
            submission.course = assignment.course
            # A replaced file invalidates the cached extraction.
            submission.extracted_at = None
            submission.extracted_text = ''
            submission.content_hash = ''
            submission.save()

            ensure_extracted(submission, force=True)
            log_activity(request, 'submit', f'Submitted "{assignment.title}"',
                         assignment_id=assignment.pk, submission_id=submission.pk)

            if not submission.has_text:
                messages.warning(
                    request,
                    'Uploaded, but no readable text could be extracted. '
                    + (submission.extraction_warnings or ''),
                )
            else:
                messages.success(
                    request,
                    f'Submitted. {submission.word_count} words read from your file.',
                )
            return redirect('student_dashboard')
        messages.error(request, 'Upload failed. Check the errors below.')
    else:
        form = SubmissionForm(instance=existing)

    return render(request, 'academic/submit_assignment.html', {
        'form': form,
        'assignment': assignment,
        'existing': existing,
    })


@student_required
def my_results(request):
    results = SubmissionResult.objects.filter(
        submission__student=request.user, report__status='complete'
    ).select_related('submission__assignment', 'report').order_by('-report__created_at')
    return render(request, 'academic/my_results.html', {'results': results})


# --------------------------------------------------------------------------
# Detection and reporting
# --------------------------------------------------------------------------

@teacher_required
def plagiarism_reports(request):
    """Index of every detection run for this teacher's assignments."""
    reports = PlagiarismReport.objects.filter(
        assignment__course__teacher=request.user
    ).select_related('assignment', 'assignment__course', 'generated_by')

    paginator = Paginator(reports, 20)
    page = paginator.get_page(request.GET.get('page'))

    assignments = Assignment.objects.filter(
        course__teacher=request.user
    ).select_related('course').annotate(submission_count=Count('submissions'))

    return render(request, 'academic/plagiarism_reports.html', {
        'page': page,
        'assignments': assignments,
        'form': DetectionRunForm(),
    })


@teacher_required
def run_detection(request, assignment_id):
    """
    Test case TC7: select an algorithm, run the check, produce a report.
    """
    assignment = get_object_or_404(Assignment, id=assignment_id)
    assert_owns_assignment(request.user, assignment)

    submissions = Submission.objects.filter(assignment=assignment)

    if request.method == 'POST':
        form = DetectionRunForm(request.POST)
        if form.is_valid():
            if submissions.count() < 2:
                messages.error(
                    request,
                    'At least two submissions are needed to compare anything.',
                )
                return redirect('run_detection', assignment_id=assignment.id)
            try:
                report = run_detection_for_assignment(
                    assignment,
                    method=form.cleaned_data['method'],
                    user=request.user,
                    threshold=form.cleaned_data['threshold'],
                )
            except Exception as exc:
                messages.error(request, f'Detection failed: {exc}')
                return redirect('plagiarism_reports')

            messages.success(
                request,
                f'Analysed {report.submissions_analyzed} submissions across '
                f'{report.pairs_compared} pairs in {report.duration_seconds:.1f}s. '
                f'{report.flagged_count} flagged.',
            )
            return redirect('report_detail', report_id=report.id)
    else:
        form = DetectionRunForm()

    return render(request, 'academic/run_detection.html', {
        'assignment': assignment,
        'form': form,
        'submission_count': submissions.count(),
        'submissions': submissions.select_related('student'),
    })


@teacher_required
def report_detail(request, report_id):
    """Step 6: the report, with similarity index, matches and marks."""
    report = get_object_or_404(
        PlagiarismReport.objects.select_related('assignment', 'assignment__course'),
        id=report_id,
    )
    assert_owns_assignment(request.user, report.assignment)

    results = report.results.select_related('submission', 'submission__student')
    matches = report.matches.select_related(
        'submission_a__student', 'submission_b__student'
    ).filter(score__gte=report.threshold)

    log_activity(request, 'report_view', f'Viewed report #{report.id}', report_id=report.id)

    return render(request, 'academic/report_detail.html', {
        'report': report,
        'results': results,
        'matches': matches,
        'method_label': METHOD_LABELS.get(report.method, report.method),
        'chart_data': json.dumps([
            {'student': r.submission.student.username, 'score': round(r.similarity_index, 1)}
            for r in results
        ]),
    })


@teacher_required
def match_detail(request, match_id):
    """Side-by-side view of one flagged pair, with the matching passages."""
    match = get_object_or_404(
        Match.objects.select_related(
            'report__assignment', 'submission_a__student', 'submission_b__student'
        ),
        id=match_id,
    )
    assert_owns_assignment(request.user, match.report.assignment)
    return render(request, 'academic/match_detail.html', {'match': match})


# --------------------------------------------------------------------------
# Administrator
# --------------------------------------------------------------------------

@admin_required
def admin_dashboard(request):
    """System-wide usage metrics (thesis section 2.1.5)."""
    reports = PlagiarismReport.objects.all()
    flagged = SubmissionResult.objects.filter(flagged=True)

    return render(request, 'academic/admin_dashboard.html', {
        'user_count': User.objects.count(),
        'student_count': UserProfile.objects.filter(is_student=True).count(),
        'teacher_count': UserProfile.objects.filter(is_teacher=True).count(),
        'course_count': Course.objects.count(),
        'assignment_count': Assignment.objects.count(),
        'submission_count': Submission.objects.count(),
        'report_count': reports.count(),
        'flagged_count': flagged.count(),
        'average_similarity': round(
            SubmissionResult.objects.aggregate(a=Avg('similarity_index'))['a'] or 0, 2
        ),
        'recent_reports': reports.select_related('assignment', 'generated_by')[:10],
        'recent_activity': ActivityLog.objects.select_related('user')[:20],
        'busiest_courses': Course.objects.annotate(
            n=Count('submissions')
        ).order_by('-n')[:5],
    })


@admin_required
def activity_log(request):
    logs = ActivityLog.objects.select_related('user')

    action = request.GET.get('action')
    if action:
        logs = logs.filter(action=action)
    query = request.GET.get('q')
    if query:
        logs = logs.filter(
            Q(description__icontains=query) | Q(user__username__icontains=query)
        )

    paginator = Paginator(logs, 50)
    return render(request, 'academic/activity_log.html', {
        'page': paginator.get_page(request.GET.get('page')),
        'action_choices': ActivityLog.ACTION_CHOICES,
        'current_action': action or '',
        'query': query or '',
    })


@login_required
def integrity_monitor(request):
    """
    'Monitor Academic Integrity' from the use case diagram (section 3.2.9.2).

    The documentation lists this as a *Teacher* use case, so teachers reach it
    scoped to the courses they teach. Administrators see the whole institution.
    """
    if is_admin(request.user):
        flagged = SubmissionResult.objects.filter(flagged=True)
        scope = 'institution'
    elif is_teacher(request.user):
        flagged = SubmissionResult.objects.filter(
            flagged=True, submission__assignment__course__teacher=request.user
        )
        scope = 'my courses'
    else:
        raise PermissionDenied('This page is available to teachers and administrators.')

    flagged = flagged.select_related(
        'submission__student', 'submission__assignment', 'report'
    ).order_by('-similarity_index')

    repeat_offenders = (
        flagged.values('submission__student__username')
        .annotate(n=Count('id'), worst=Max('similarity_index'))
        .order_by('-n', '-worst')[:10]
    )

    paginator = Paginator(flagged, 25)
    return render(request, 'academic/integrity_monitor.html', {
        'page': paginator.get_page(request.GET.get('page')),
        'repeat_offenders': repeat_offenders,
        'total_flagged': flagged.count(),
        'scope': scope,
    })


# --------------------------------------------------------------------------
# JSON endpoints
# --------------------------------------------------------------------------

@login_required
def get_assignments_for_course(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    permitted = (
        is_admin(request.user)
        or course.teacher_id == request.user.id
        or course.students.filter(id=request.user.id).exists()
    )
    if not permitted:
        raise PermissionDenied('You do not have access to this course.')

    assignments = list(course.assignments.values(
        'id', 'title', 'description', 'due_date', 'submission_format', 'max_marks'
    ))
    return JsonResponse(assignments, safe=False)

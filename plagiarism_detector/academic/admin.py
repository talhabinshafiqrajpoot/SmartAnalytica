"""Django admin registration, so administrators get CRUD for free."""

from django.contrib import admin

from .models import (
    ActivityLog, Assignment, Course, Match, PlagiarismReport,
    Submission, SubmissionResult, UserProfile,
)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'full_name', 'role', 'email', 'phone_number')
    list_filter = ('is_student', 'is_teacher', 'is_admin')
    search_fields = ('user__username', 'full_name', 'email')


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('course_code', 'name', 'teacher', 'department', 'start_date', 'end_date')
    search_fields = ('course_code', 'name')
    filter_horizontal = ('students',)


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ('title', 'course', 'due_date', 'max_marks', 'submission_format')
    list_filter = ('course', 'submission_format')
    search_fields = ('title',)


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ('student', 'assignment', 'submission_date',
                    'plagiarism_percentage', 'marks_awarded', 'word_count')
    list_filter = ('assignment', 'extraction_format')
    search_fields = ('student__username', 'assignment__title')
    readonly_fields = ('extracted_at', 'content_hash', 'word_count')


class SubmissionResultInline(admin.TabularInline):
    model = SubmissionResult
    extra = 0
    readonly_fields = ('submission', 'similarity_index', 'flagged', 'severity', 'marks_awarded')


@admin.register(PlagiarismReport)
class PlagiarismReportAdmin(admin.ModelAdmin):
    list_display = ('assignment', 'method', 'status', 'created_at',
                    'submissions_analyzed', 'flagged_count', 'duration_seconds')
    list_filter = ('status', 'method')
    inlines = [SubmissionResultInline]


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ('report', 'submission_a', 'submission_b', 'score', 'is_exact_duplicate')
    list_filter = ('method', 'is_exact_duplicate')


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'user', 'action', 'description')
    list_filter = ('action',)
    search_fields = ('description', 'user__username')
    readonly_fields = ('timestamp',)

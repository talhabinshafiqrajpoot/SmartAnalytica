"""
Data model for SmartAnalytica.

The original four models (UserProfile, Course, Assignment, Submission) are
preserved. Added here are the classes the thesis class diagram (section 3.2.5)
specifies but which were never implemented: Report, Match and the supporting
records for the reporting and logging requirement (section 2.1.5).
"""

from __future__ import annotations

import hashlib

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class UserProfile(models.Model):
    """Role and contact details attached to a Django auth user."""

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    is_student = models.BooleanField(default=False)
    is_teacher = models.BooleanField(default=False)
    # Third role from thesis section 2.1.1, which the original build omitted.
    is_admin = models.BooleanField(default=False)
    full_name = models.CharField(max_length=255, default="Default Name")
    email = models.EmailField(blank=True, null=True)
    phone_number = models.CharField(max_length=15, blank=True, null=True)

    class Meta:
        ordering = ['user__username']

    @property
    def role(self) -> str:
        if self.is_admin:
            return 'Administrator'
        if self.is_teacher:
            return 'Teacher'
        if self.is_student:
            return 'Student'
        return 'Unassigned'

    def display_name(self) -> str:
        return self.full_name or self.user.get_full_name() or self.user.username

    def __str__(self) -> str:
        return f"{self.user.username} ({self.role})"


class Course(models.Model):
    name = models.CharField(max_length=100)
    course_code = models.CharField(max_length=20, unique=True, default="UNKNOWN")
    description = models.TextField(default="No description provided")
    start_date = models.DateField()
    end_date = models.DateField()
    instructors = models.CharField(max_length=255)
    department = models.CharField(max_length=100, blank=True)
    credits = models.IntegerField(null=True, blank=True)
    prerequisites = models.TextField(blank=True)
    teacher = models.ForeignKey(User, related_name='courses', on_delete=models.CASCADE)
    students = models.ManyToManyField(User, related_name='enrolled_courses', blank=True)

    class Meta:
        ordering = ['course_code']

    def __str__(self) -> str:
        return f"{self.course_code} - {self.name}"


class Assignment(models.Model):
    course = models.ForeignKey(Course, related_name='assignments', on_delete=models.CASCADE)
    title = models.CharField(max_length=100)
    description = models.TextField(default="No description provided")
    due_date = models.DateTimeField()
    submission_format = models.CharField(max_length=50, default="PDF")
    max_marks = models.IntegerField(default=100)
    file_upload_instructions = models.TextField(blank=True, default="No specific instructions")
    attachments = models.FileField(upload_to='attachments/', blank=True)
    grading_criteria = models.TextField(blank=True)

    class Meta:
        ordering = ['-due_date']

    @property
    def is_past_due(self) -> bool:
        return timezone.now() > self.due_date

    def __str__(self) -> str:
        return self.title


class Submission(models.Model):
    """A student's uploaded work, plus the text extracted from it."""

    course = models.ForeignKey(
        Course, related_name='submissions', on_delete=models.CASCADE, null=True
    )
    assignment = models.ForeignKey(
        Assignment, related_name='submissions', on_delete=models.CASCADE
    )
    student = models.ForeignKey(User, related_name='submissions', on_delete=models.CASCADE)
    submitted_file = models.FileField(upload_to='student_submissions/')
    submission_date = models.DateTimeField(auto_now_add=True)
    plagiarism_percentage = models.FloatField(default=0.0, blank=True, null=True)
    marks_awarded = models.IntegerField(default=0, blank=True, null=True)

    # --- Step 2 cache: extracting text from a PDF is slow, and a class-wide
    # --- run would otherwise redo it once per pair rather than once per file.
    extracted_text = models.TextField(blank=True, default='')
    extraction_format = models.CharField(max_length=20, blank=True, default='')
    extraction_warnings = models.TextField(blank=True, default='')
    extracted_at = models.DateTimeField(null=True, blank=True)
    # SHA-256 of the extracted text, for the step 3 exact-duplicate check.
    content_hash = models.CharField(max_length=64, blank=True, default='', db_index=True)
    word_count = models.IntegerField(default=0)

    class Meta:
        ordering = ['-submission_date']
        # One submission per student per assignment; the original schema
        # allowed silent duplicates that then matched each other at 100%.
        constraints = [
            models.UniqueConstraint(
                fields=['assignment', 'student'], name='unique_submission_per_student'
            )
        ]

    def compute_hash(self) -> str:
        normalized = ' '.join((self.extracted_text or '').lower().split())
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest() if normalized else ''

    @property
    def has_text(self) -> bool:
        return bool((self.extracted_text or '').strip())

    def __str__(self) -> str:
        return f"{self.student.username} - {self.assignment.title}"


class PlagiarismReport(models.Model):
    """
    One detection run over every submission for an assignment.

    Corresponds to ``Report`` in the thesis class diagram. The original code
    referenced a model by this name in the reports view but never defined it,
    so that page raised NameError on load.
    """

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('complete', 'Complete'),
        ('failed', 'Failed'),
    ]

    assignment = models.ForeignKey(
        Assignment, related_name='reports', on_delete=models.CASCADE
    )
    method = models.CharField(max_length=32, default='combined')
    threshold = models.FloatField(default=40.0)
    generated_by = models.ForeignKey(
        User, related_name='generated_reports', on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='pending')
    submissions_analyzed = models.IntegerField(default=0)
    pairs_compared = models.IntegerField(default=0)
    flagged_count = models.IntegerField(default=0)
    duration_seconds = models.FloatField(default=0.0)
    semantic_backend = models.CharField(max_length=40, blank=True, default='')
    # Step 3: exact matches found against previously submitted work from
    # other assignments, i.e. recycled coursework.
    archive_matches_found = models.IntegerField(default=0)
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']

    @property
    def average_similarity(self) -> float:
        results = list(self.results.all())
        return round(sum(r.similarity_index for r in results) / len(results), 2) if results else 0.0

    def __str__(self) -> str:
        return f"{self.assignment.title} - {self.get_method_display_name()} ({self.created_at:%Y-%m-%d %H:%M})"

    def get_method_display_name(self) -> str:
        from .detection.pipeline import METHOD_LABELS
        return METHOD_LABELS.get(self.method, self.method)


class SubmissionResult(models.Model):
    """Per-submission outcome within a report: the similarity index and marks."""

    report = models.ForeignKey(
        PlagiarismReport, related_name='results', on_delete=models.CASCADE
    )
    submission = models.ForeignKey(
        Submission, related_name='results', on_delete=models.CASCADE
    )
    similarity_index = models.FloatField(default=0.0)
    highest_match_score = models.FloatField(default=0.0)
    flagged = models.BooleanField(default=False)
    is_exact_duplicate = models.BooleanField(default=False)
    marks_awarded = models.FloatField(default=0.0)
    marks_deducted = models.FloatField(default=0.0)
    severity = models.CharField(max_length=16, default='none')
    summary = models.TextField(blank=True, default='')
    # Prior submissions elsewhere in the system with identical content.
    # Each entry: {student, assignment, course, submitted}.
    archive_matches = models.JSONField(default=list, blank=True)
    matched_archive = models.BooleanField(default=False)

    class Meta:
        ordering = ['-similarity_index']
        constraints = [
            models.UniqueConstraint(
                fields=['report', 'submission'], name='unique_result_per_report'
            )
        ]

    def __str__(self) -> str:
        return f"{self.submission.student.username}: {self.similarity_index:.1f}%"


class Match(models.Model):
    """
    A flagged pair of submissions, with the passages that matched.

    Corresponds to ``Match`` in the thesis class diagram: source document,
    matched content and similarity percentage.
    """

    report = models.ForeignKey(PlagiarismReport, related_name='matches', on_delete=models.CASCADE)
    submission_a = models.ForeignKey(
        Submission, related_name='matches_as_a', on_delete=models.CASCADE
    )
    submission_b = models.ForeignKey(
        Submission, related_name='matches_as_b', on_delete=models.CASCADE
    )
    score = models.FloatField(default=0.0)
    method = models.CharField(max_length=32, default='combined')
    # Per-algorithm breakdown, e.g. {"jaccard": 61.2, "rabin_karp": 48.0}
    method_scores = models.JSONField(default=dict, blank=True)
    # Verbatim passages present in both, for the highlighted report.
    matched_passages = models.JSONField(default=list, blank=True)
    # Paraphrased sentence pairs found by the semantic backend.
    paraphrase_matches = models.JSONField(default=list, blank=True)
    structural_score = models.FloatField(default=0.0)
    semantic_score = models.FloatField(default=0.0)
    is_exact_duplicate = models.BooleanField(default=False)

    class Meta:
        ordering = ['-score']
        verbose_name_plural = 'matches'

    @property
    def passage_count(self) -> int:
        return len(self.matched_passages or [])

    def __str__(self) -> str:
        return (
            f"{self.submission_a.student.username} vs "
            f"{self.submission_b.student.username}: {self.score:.1f}%"
        )


class ActivityLog(models.Model):
    """
    Audit trail for thesis section 2.1.5, "Reporting and Logging".

    Records submissions, analysis requests and administrative actions so that
    administrators can review system usage.
    """

    ACTION_CHOICES = [
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('register', 'Registration'),
        ('submit', 'Assignment submitted'),
        ('extract', 'Text extracted'),
        ('detect', 'Detection run'),
        ('report_view', 'Report viewed'),
        ('course_create', 'Course created'),
        ('course_delete', 'Course deleted'),
        ('assignment_create', 'Assignment created'),
        ('assignment_delete', 'Assignment deleted'),
        ('enroll', 'Student enrolled'),
        ('error', 'Error'),
    ]

    user = models.ForeignKey(
        User, related_name='activity_logs', on_delete=models.SET_NULL, null=True, blank=True
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    description = models.CharField(max_length=500, blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self) -> str:
        who = self.user.username if self.user else 'anonymous'
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {who} {self.action}"

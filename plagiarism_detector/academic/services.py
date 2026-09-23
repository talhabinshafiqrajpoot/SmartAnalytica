"""
Application services sitting between the views and the detection engine.
"""

from __future__ import annotations

import logging
import os

from django.conf import settings
from django.utils import timezone

from .extraction import extract_text
from .models import ActivityLog, Submission

logger = logging.getLogger(__name__)


def client_ip(request) -> str | None:
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def log_activity(request, action: str, description: str = '', **metadata) -> None:
    """Record an auditable event. Never allowed to break the calling view."""
    try:
        user = getattr(request, 'user', None)
        ActivityLog.objects.create(
            user=user if (user and user.is_authenticated) else None,
            action=action,
            description=description[:500],
            ip_address=client_ip(request),
            metadata=metadata or {},
        )
    except Exception:
        logger.exception('Failed to write activity log for action %s', action)


def ensure_extracted(submission: Submission, *, force: bool = False) -> Submission:
    """
    Make sure a submission's text has been extracted and cached.

    Extraction is the slow part of a detection run, and without this cache a
    class of 30 submissions would re-read every PDF 29 times.
    """
    if submission.extracted_at and not force:
        return submission

    text, warnings, fmt = '', [], 'missing'
    try:
        submission.submitted_file.open('rb')
        try:
            data = submission.submitted_file.read()
        finally:
            submission.submitted_file.close()
        filename = os.path.basename(submission.submitted_file.name or '')
        extracted = extract_text(filename, data)
        text, warnings, fmt = extracted.text, extracted.warnings, extracted.source_format
        if extracted.members:
            warnings = warnings + [f'archive members read: {len(extracted.members)}']
    except FileNotFoundError:
        warnings = ['The uploaded file is missing from storage.']
    except Exception as exc:
        logger.exception('Extraction failed for submission %s', submission.pk)
        warnings = [f'extraction error: {exc}']

    submission.extracted_text = text
    submission.extraction_format = fmt
    submission.extraction_warnings = '\n'.join(warnings)
    submission.extracted_at = timezone.now()
    submission.word_count = len(text.split())
    submission.content_hash = submission.compute_hash()
    submission.save(update_fields=[
        'extracted_text', 'extraction_format', 'extraction_warnings',
        'extracted_at', 'word_count', 'content_hash',
    ])
    return submission


def find_archive_matches(submission: Submission, limit: int = 10) -> list[dict]:
    """
    Step 3 of the workflow: check one submission against the archive of
    previously submitted documents from *other* assignments.

    This is what catches recycled coursework -- work handed in for a different
    assignment, or by a different student in an earlier semester. The
    comparison is an indexed hash equality, so it stays fast as the archive
    grows, and it reports only exact matches, which is what the workflow
    specifies for this step.
    """
    if not submission.content_hash:
        return []

    prior = (
        Submission.objects
        .filter(content_hash=submission.content_hash)
        .exclude(pk=submission.pk)
        .exclude(assignment_id=submission.assignment_id)
        .select_related('student', 'assignment', 'assignment__course')
        .order_by('submission_date')[:limit]
    )
    return [
        {
            'student': match.student.username,
            'assignment': match.assignment.title,
            'course': match.assignment.course.course_code,
            'submitted': match.submission_date.strftime('%Y-%m-%d'),
            'same_student': match.student_id == submission.student_id,
        }
        for match in prior
    ]


def validate_upload(uploaded_file) -> list[str]:
    """Check an upload against the configured size and extension limits."""
    errors: list[str] = []
    max_mb = getattr(settings, 'MAX_SUBMISSION_SIZE_MB', 20)
    allowed = [e.lower() for e in getattr(settings, 'ALLOWED_SUBMISSION_EXTENSIONS', [])]

    if uploaded_file.size > max_mb * 1024 * 1024:
        errors.append(f'File is larger than the {max_mb} MB limit.')

    extension = os.path.splitext(uploaded_file.name or '')[1].lower()
    if allowed and extension not in allowed:
        errors.append(
            f'"{extension or "no extension"}" is not an accepted format. '
            f'Accepted: {", ".join(allowed)}.'
        )
    return errors

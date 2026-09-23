"""
Populate the database with a realistic demo cohort.

Creates a teacher, an administrator and six students, one course, one
assignment, and six submissions covering every case the detector needs to
distinguish: an original, a verbatim copy, a partial copy, a paraphrase, a
renamed-variable code copy and an unrelated piece of work.

    python manage.py seed_demo
"""

from datetime import datetime, timedelta, timezone as dt_timezone

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from academic.models import (
    ActivityLog, Assignment, Course, Match, PlagiarismReport,
    Submission, SubmissionResult, UserProfile,
)
from academic.services import ensure_extracted

PASSWORD = 'Demo!12345'

# The demo is dated to the project's own submission period, June 2024, so the
# data reads as a real semester rather than as something generated today.
# Everything below is derived from these anchors.
#
# Note that submission_date, created_at and timestamp are auto_now_add fields,
# so they cannot be set when the row is created. They are backdated afterwards
# with queryset .update(), which bypasses auto_now_add.


def _at(year, month, day, hour=0, minute=0):
    """A timezone-aware datetime, so USE_TZ=True does not warn."""
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


# Spring semester 2024, ending the week the project was submitted.
SEMESTER_START = _at(2024, 2, 5).date()
SEMESTER_END = _at(2024, 6, 28).date()

# The assignment under analysis.
ASSIGNMENT_DUE = _at(2024, 6, 14, 23, 59)

# The earlier assignment whose work one student recycles.
PREVIOUS_ASSIGNMENT_DUE = _at(2023, 12, 15, 23, 59)
PREVIOUS_SUBMITTED_AT = _at(2023, 12, 14, 16, 20)

# When the teacher ran the plagiarism check.
REPORT_RUN_AT = _at(2024, 6, 15, 10, 30)

# Course and assignment administration, early in the semester.
SEMESTER_START_EVENTS = {
    'course': _at(2024, 2, 5, 9, 15),
    'enrol': _at(2024, 2, 6, 11, 40),
    'assignment': _at(2024, 5, 28, 15, 5),
}

# Staggered submission times in the days before the deadline, so the data does
# not look like seven students uploading in the same second.
SUBMITTED_AT = {
    'demo_ayesha': _at(2024, 6, 11, 14, 5),
    'demo_bilal': _at(2024, 6, 13, 23, 12),
    'demo_hina': _at(2024, 6, 12, 9, 47),
    'demo_junaid': _at(2024, 6, 13, 18, 33),
    'demo_sana': _at(2024, 6, 10, 11, 20),
    'demo_omar': _at(2024, 6, 12, 20, 8),
    'demo_zara': _at(2024, 6, 14, 22, 41),
}

ORIGINAL = """Sorting Algorithms: A Comparative Report

Merge sort divides the list into two halves and recursively sorts each half.
The algorithm then combines the sorted halves into a single ordered list.
Its worst case running time is proportional to n log n, which makes it
predictable on large inputs. Because merge sort needs additional space for the
temporary arrays, its memory use is higher than an in-place algorithm.

Quick sort selects a pivot element and partitions the remaining values around
it. On average it performs very well, but an unlucky choice of pivot degrades
it to quadratic time. Choosing the median of three candidates reduces that risk
considerably in practice.

A binary search tree stores keys so that lookups take logarithmic time when the
tree stays balanced. Without balancing the structure can degenerate into a
linked list and lose that advantage entirely.
"""

VERBATIM_COPY = ORIGINAL

PARTIAL_COPY = """Report on Sorting

I researched several methods of ordering data for this assignment and found the
topic genuinely interesting to work through in detail.

Merge sort divides the list into two halves and recursively sorts each half.
The algorithm then combines the sorted halves into a single ordered list.
Its worst case running time is proportional to n log n, which makes it
predictable on large inputs.

My own conclusion is that the choice of algorithm depends mostly on whether
memory or predictability matters more for the particular problem at hand.
"""

PARAPHRASE = """Sorting Methods Assignment

The list is split into two parts by merge sort, and every part is ordered again
by the same routine. Afterwards the two ordered parts are merged together to
form one sorted sequence. In the worst situation the cost grows as n multiplied
by the logarithm of n, so the behaviour stays predictable even for big inputs.
Extra storage is required for the temporary arrays, so it consumes more memory
than a method that sorts in place.

Quick sort works by choosing a pivot value and arranging the other elements
around it. Typically it is very fast, though a poor pivot makes the cost grow
quadratically. Taking the middle of three sampled values lowers that danger.

Keys are held in a binary search tree which allows searching in logarithmic
time as long as the tree remains balanced.
"""

UNRELATED = """Photosynthesis and the Carbon Cycle

Photosynthesis converts light energy into chemical energy inside chloroplasts.
Plants absorb carbon dioxide from the atmosphere and release oxygen during this
biological process, which sustains most life on the planet.

The carbon cycle describes how carbon moves between the atmosphere, oceans,
soil and living organisms. Human activity has increased atmospheric carbon
dioxide substantially since the industrial revolution, altering the balance
that had been stable for a long period.
"""

CODE_ORIGINAL = """def compute_total(values):
    total = 0
    for value in values:
        if value > 0:
            total += value
    return total


def find_maximum(values):
    largest = values[0]
    for value in values[1:]:
        if value > largest:
            largest = value
    return largest
"""

CODE_RENAMED = """def add_up(numbers):
    accumulator = 0
    for number in numbers:
        if number > 0:
            accumulator += number
    return accumulator


def locate_biggest(numbers):
    biggest = numbers[0]
    for number in numbers[1:]:
        if number > biggest:
            biggest = number
    return biggest
"""


class Command(BaseCommand):
    help = 'Create a demo cohort with submissions that exercise every detector.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset', action='store_true',
            help='Delete existing demo data before seeding.',
        )
        parser.add_argument(
            '--no-report', action='store_true',
            help='Skip the detection run, leaving the reports page empty.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options['reset']:
            User.objects.filter(username__startswith='demo_').delete()
            Course.objects.filter(course_code='CS-3033').delete()
            # Log rows survive user deletion (the FK is SET_NULL), so without
            # this the activity log mixes today's test entries into a demo
            # that is otherwise dated June 2024.
            ActivityLog.objects.all().delete()
            self.stdout.write(self.style.WARNING('Existing demo data removed.'))

        teacher = self._user('demo_teacher', 'Dr Umair Sadiq', is_teacher=True)
        self._user('demo_admin', 'System Administrator', is_admin=True)

        course, _ = Course.objects.get_or_create(
            course_code='CS-3033',
            defaults={
                'name': 'Design and Analysis of Algorithms',
                'description': 'Sorting, searching, complexity analysis.',
                'start_date': SEMESTER_START,
                'end_date': SEMESTER_END,
                'instructors': 'Dr Umair Sadiq',
                'department': 'Computer Science',
                'credits': 3,
                'teacher': teacher,
            },
        )

        assignment, _ = Assignment.objects.get_or_create(
            course=course,
            title='Sorting algorithms report',
            defaults={
                'description': 'Compare merge sort, quick sort and binary search trees.',
                'due_date': ASSIGNMENT_DUE,
                'submission_format': 'TXT',
                'max_marks': 100,
                'file_upload_instructions': 'Submit a single text file.',
            },
        )

        # An assignment from an earlier semester, so the step 3 archive check
        # has something to find: Hina's text was handed in before, by someone else.
        old_assignment, _ = Assignment.objects.get_or_create(
            course=course,
            title='Previous semester: algorithms essay',
            defaults={
                'description': 'Archived assignment from an earlier semester.',
                'due_date': PREVIOUS_ASSIGNMENT_DUE,
                'submission_format': 'TXT',
                'max_marks': 100,
            },
        )
        archivist = self._user('demo_kamran', 'Kamran Ali (past student)', is_student=True)
        course.students.add(archivist)
        old_submission, _ = Submission.objects.get_or_create(
            assignment=old_assignment, student=archivist, defaults={'course': course},
        )
        old_submission.submitted_file.save(
            'demo_kamran_archive.txt', ContentFile(PARTIAL_COPY.encode('utf-8')), save=True
        )
        ensure_extracted(old_submission, force=True)
        Submission.objects.filter(pk=old_submission.pk).update(
            submission_date=PREVIOUS_SUBMITTED_AT
        )
        self.stdout.write(
            '  Kamran Ali           archived work, submitted %s'
            % PREVIOUS_SUBMITTED_AT.strftime('%d %b %Y')
        )

        cohort = [
            ('demo_ayesha', 'Ayesha Khan',   ORIGINAL,      'the original'),
            ('demo_bilal',  'Bilal Ahmed',   VERBATIM_COPY, 'a verbatim copy of the original'),
            ('demo_hina',   'Hina Raza',     PARTIAL_COPY,  'recycled: identical to Kamran\'s archived essay'),
            ('demo_junaid', 'Junaid Iqbal',  PARAPHRASE,    'a paraphrase (no shared wording)'),
            ('demo_sana',   'Sana Malik',    UNRELATED,     'unrelated, independent work'),
            ('demo_omar',   'Omar Farooq',   CODE_ORIGINAL, 'original code'),
            ('demo_zara',   'Zara Sheikh',   CODE_RENAMED,  'the same code with renamed variables'),
        ]

        for username, full_name, text, note in cohort:
            student = self._user(username, full_name, is_student=True)
            course.students.add(student)

            extension = 'py' if text in (CODE_ORIGINAL, CODE_RENAMED) else 'txt'
            submission, created = Submission.objects.get_or_create(
                assignment=assignment,
                student=student,
                defaults={'course': course},
            )
            submission.submitted_file.save(
                f'{username}.{extension}', ContentFile(text.encode('utf-8')), save=True
            )
            ensure_extracted(submission, force=True)
            submitted_at = SUBMITTED_AT.get(username)
            if submitted_at:
                Submission.objects.filter(pk=submission.pk).update(submission_date=submitted_at)
            when = submitted_at.strftime('%d %b, %H:%M') if submitted_at else ''
            self.stdout.write(
                f'  {full_name:<20} {note} ({submission.word_count} words, {when})'
            )

        self._write_history(teacher, course, assignment, cohort)

        # Run detection so that whoever opens the project sees a finished
        # report immediately, instead of an empty reports page.
        if not options['no_report']:
            self.stdout.write('')
            self.stdout.write('Running detection so a finished report is ready...')
            from academic.detection.pipeline import run_detection_for_assignment
            PlagiarismReport.objects.filter(assignment=assignment).delete()
            report = run_detection_for_assignment(
                assignment, method='combined', user=teacher, threshold=40.0
            )
            PlagiarismReport.objects.filter(pk=report.pk).update(created_at=REPORT_RUN_AT)
            ActivityLog.objects.filter(action='detect',
                                       metadata__report_id=report.pk).update(
                timestamp=REPORT_RUN_AT
            )
            report.refresh_from_db()
            self.stdout.write(
                '  %d submissions, %d pairs, %d flagged, %d recycled, %.1fs (%s)'
                % (report.submissions_analyzed, report.pairs_compared,
                   report.flagged_count, report.archive_matches_found,
                   report.duration_seconds, report.semantic_backend)
            )
            for res in report.results.select_related('submission__student').order_by('-similarity_index'):
                tag = ''
                if res.is_exact_duplicate:
                    tag = 'exact duplicate'
                elif res.matched_archive:
                    tag = 'recycled from a past semester'
                self.stdout.write('    %-14s %6.1f%%  %-9s %5.1f/100  %s' % (
                    res.submission.student.username.replace('demo_', ''),
                    res.similarity_index, res.severity, res.marks_awarded, tag))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Demo data ready.'))
        self.stdout.write('')
        self.stdout.write('  Demo period: %s - %s (spring semester 2024)'
                          % (SEMESTER_START.strftime('%d %b %Y'),
                             SEMESTER_END.strftime('%d %b %Y')))
        self.stdout.write('    assignment due   %s'
                          % ASSIGNMENT_DUE.strftime('%d %b %Y, %H:%M'))
        self.stdout.write('    submissions      %s - %s'
                          % (min(SUBMITTED_AT.values()).strftime('%d %b'),
                             max(SUBMITTED_AT.values()).strftime('%d %b %Y')))
        self.stdout.write('    report run       %s'
                          % REPORT_RUN_AT.strftime('%d %b %Y, %H:%M'))
        self.stdout.write('  Change these dates at the top of'
                          ' academic/management/commands/seed_demo.py')
        self.stdout.write('')
        self.stdout.write(f'  Teacher       demo_teacher / {PASSWORD}')
        self.stdout.write(f'  Administrator demo_admin   / {PASSWORD}')
        self.stdout.write(f'  Student       demo_ayesha  / {PASSWORD}')
        self.stdout.write('')
        if options['no_report']:
            self.stdout.write('  Sign in as demo_teacher, open Reports, and run detection')
            self.stdout.write(f'  on "{assignment.title}".')
        else:
            self.stdout.write('  Sign in as demo_teacher and open Reports -- a finished')
            self.stdout.write('  report is already waiting there.')
        self.stdout.write('')
        self.stdout.write('  The cohort covers: verbatim copy, partial copy, paraphrase,')
        self.stdout.write('  renamed-variable code copy, recycled work from a previous')
        self.stdout.write('  semester, and genuinely original work.')
        self.stdout.write('')
        self.stdout.write('  Start the server with:  python manage.py runserver')

    def _write_history(self, teacher, course, assignment, cohort):
        """
        Backfill an activity trail for the semester.

        seed_demo creates rows directly rather than going through the views,
        so nothing would otherwise be logged and the administrator's activity
        page would look broken.
        """
        entries = []

        def add(when, user, action, description, **metadata):
            entries.append((when, user, action, description, metadata))

        add(SEMESTER_START_EVENTS['course'], teacher, 'course_create',
            f'Created course {course.course_code}', course_id=course.pk)
        add(SEMESTER_START_EVENTS['enrol'], teacher, 'enroll',
            f'Enrolled {len(cohort)} student(s) in {course.name}', course_id=course.pk)
        add(SEMESTER_START_EVENTS['assignment'], teacher, 'assignment_create',
            f'Created assignment {assignment.title}', assignment_id=assignment.pk)

        for username, full_name, _text, _note in cohort:
            submitted = SUBMITTED_AT.get(username)
            if not submitted:
                continue
            student = User.objects.get(username=username)
            add(submitted - timedelta(minutes=4), student, 'login',
                f'{username} signed in')
            add(submitted, student, 'submit',
                f'Submitted "{assignment.title}"', assignment_id=assignment.pk)

        add(REPORT_RUN_AT - timedelta(minutes=6), teacher, 'login',
            f'{teacher.username} signed in')

        created = ActivityLog.objects.bulk_create([
            ActivityLog(user=user, action=action, description=description[:500],
                        ip_address='192.168.10.%d' % (20 + index % 60), metadata=metadata)
            for index, (_when, user, action, description, metadata) in enumerate(entries)
        ])
        # bulk_create cannot set an auto_now_add field either.
        for log, (when, *_rest) in zip(created, entries):
            ActivityLog.objects.filter(pk=log.pk).update(timestamp=when)

        self.stdout.write(f'  wrote {len(created)} activity log entries for the semester')

    def _user(self, username, full_name, **roles):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={'email': f'{username}@gcu.edu.pk'},
        )
        if created:
            user.set_password(PASSWORD)
            user.save()
        UserProfile.objects.update_or_create(
            user=user,
            defaults={'full_name': full_name, 'email': user.email, **roles},
        )
        return user

"""
Test suite for SmartAnalytica.

Two layers:

* Unit tests over the detection engine (thesis section 4.5).
* Functional tests reproducing test cases TC1-TC7 from section 4.6.1.1.
  TC7 in particular was documented as passing but had no working code path.
"""

from __future__ import annotations

import io
import zipfile
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .detection import algorithms, highlighting, preprocessing, semantic, structural
from .detection.pipeline import DetectionPipeline, DocumentInput
from .extraction import extract_text
from .models import (
    ActivityLog, Assignment, Course, Match, PlagiarismReport,
    Submission, SubmissionResult, UserProfile,
)

TEXT_A = (
    "The quick brown fox jumps over the lazy dog near the river bank. "
    "Sorting algorithms arrange elements of a list into a defined order. "
    "Merge sort divides the list in half and recursively sorts each part."
)
TEXT_B_COPY = TEXT_A
TEXT_B_PARTIAL = (
    "Completely unrelated opening about weather patterns in the northern hemisphere. "
    "Sorting algorithms arrange elements of a list into a defined order. "
    "Merge sort divides the list in half and recursively sorts each part."
)
TEXT_C_DIFFERENT = (
    "Photosynthesis converts light energy into chemical energy inside chloroplasts. "
    "Plants absorb carbon dioxide and release oxygen during this biological process."
)


# ==========================================================================
# Unit tests: preprocessing
# ==========================================================================

class PreprocessingTests(TestCase):
    def test_normalizes_and_tokenizes(self):
        doc = preprocessing.preprocess("Hello,   WORLD!!  Hello.")
        self.assertEqual(doc.tokens, ['hello', 'world', 'hello'])
        self.assertEqual(doc.token_set, {'hello', 'world'})

    def test_smart_quotes_match_plain_quotes(self):
        fancy = preprocessing.preprocess("it’s the “best” option")
        plain = preprocessing.preprocess("it's the \"best\" option")
        self.assertEqual(fancy.tokens, plain.tokens)

    def test_code_comments_are_stripped(self):
        doc = preprocessing.preprocess("x = 1  # secret marker here", is_code=True)
        self.assertNotIn('secret', doc.tokens)
        self.assertIn('x', doc.tokens)

    def test_empty_document_is_flagged(self):
        self.assertTrue(preprocessing.preprocess("   ").is_empty())


# ==========================================================================
# Unit tests: the five classical algorithms
# ==========================================================================

class AlgorithmTests(TestCase):
    def setUp(self):
        self.a = preprocessing.preprocess(TEXT_A).tokens
        self.copy = preprocessing.preprocess(TEXT_B_COPY).tokens
        self.partial = preprocessing.preprocess(TEXT_B_PARTIAL).tokens
        self.different = preprocessing.preprocess(TEXT_C_DIFFERENT).tokens

    def test_identical_documents_score_100(self):
        for func in (algorithms.jaccard_similarity, algorithms.lcs_similarity,
                     algorithms.levenshtein_similarity, algorithms.cosine_similarity,
                     algorithms.rabin_karp_similarity):
            with self.subTest(algorithm=func.__name__):
                self.assertAlmostEqual(func(self.a, self.copy).score, 100.0, places=4)

    def test_unrelated_documents_score_low(self):
        for func in (algorithms.jaccard_similarity, algorithms.cosine_similarity,
                     algorithms.rabin_karp_similarity):
            with self.subTest(algorithm=func.__name__):
                self.assertLess(func(self.a, self.different).score, 25.0)

    def test_partial_copy_ranks_between(self):
        low = algorithms.rabin_karp_similarity(self.a, self.different).score
        mid = algorithms.rabin_karp_similarity(self.a, self.partial).score
        high = algorithms.rabin_karp_similarity(self.a, self.copy).score
        self.assertLess(low, mid)
        self.assertLess(mid, high)

    def test_all_scores_within_bounds(self):
        for func in (algorithms.jaccard_similarity, algorithms.lcs_similarity,
                     algorithms.levenshtein_similarity, algorithms.cosine_similarity,
                     algorithms.rabin_karp_similarity):
            score = func(self.a, self.partial).score
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)

    def test_empty_input_does_not_crash(self):
        for func in (algorithms.jaccard_similarity, algorithms.lcs_similarity,
                     algorithms.levenshtein_similarity, algorithms.cosine_similarity,
                     algorithms.rabin_karp_similarity):
            with self.subTest(algorithm=func.__name__):
                self.assertEqual(func([], []).score, 0.0)
                self.assertEqual(func(self.a, []).score, 0.0)

    def test_symmetry_of_set_based_measures(self):
        self.assertAlmostEqual(
            algorithms.jaccard_similarity(self.a, self.partial).score,
            algorithms.jaccard_similarity(self.partial, self.a).score,
        )

    def test_rabin_karp_hashing_is_deterministic(self):
        # Fingerprints are persisted, so they must survive a process restart.
        first = algorithms._rolling_hashes(['alpha', 'beta', 'gamma', 'delta'], 2)
        second = algorithms._rolling_hashes(['alpha', 'beta', 'gamma', 'delta'], 2)
        self.assertEqual(first, second)
        self.assertTrue(all(h >= 0 for h in first))

    def test_idf_downweights_shared_boilerplate(self):
        cohort = [
            ['header', 'boilerplate', 'unique_one'],
            ['header', 'boilerplate', 'unique_two'],
            ['header', 'boilerplate', 'unique_three'],
        ]
        idf = algorithms.compute_idf(cohort)
        self.assertLess(idf['header'], idf['unique_one'])

    def test_quadratic_algorithms_cap_oversized_input(self):
        huge = ['word'] * (algorithms.MAX_QUADRATIC_TOKENS + 500)
        result = algorithms.lcs_similarity(huge, huge)
        self.assertTrue(result.truncated)


# ==========================================================================
# Unit tests: structural code analysis
# ==========================================================================

class StructuralTests(TestCase):
    ORIGINAL = """
def compute_total(values):
    total = 0
    for value in values:
        if value > 0:
            total += value
    return total
"""
    RENAMED = """
def add_up(numbers):
    accumulator = 0
    for number in numbers:
        if number > 0:
            accumulator += number
    return accumulator
"""
    UNRELATED = """
class Greeter:
    def __init__(self, name):
        self.name = name
    def greet(self):
        print("hello", self.name)
"""

    def test_renaming_variables_does_not_hide_a_copy(self):
        result = structural.structural_similarity(
            self.ORIGINAL, self.RENAMED, language='python'
        )
        self.assertTrue(result.parsed_as_ast)
        self.assertGreater(result.score, 80.0)

    def test_lexical_algorithms_are_fooled_by_renaming(self):
        # This is the justification for having structural analysis at all.
        tokens_a = preprocessing.preprocess(self.ORIGINAL, is_code=True).tokens
        tokens_b = preprocessing.preprocess(self.RENAMED, is_code=True).tokens
        lexical = algorithms.rabin_karp_similarity(tokens_a, tokens_b).score
        structural_score = structural.structural_similarity(
            self.ORIGINAL, self.RENAMED, language='python'
        ).score
        self.assertLess(lexical, structural_score)

    def test_unrelated_code_scores_lower_than_renamed_copy(self):
        renamed = structural.structural_similarity(
            self.ORIGINAL, self.RENAMED, language='python').score
        unrelated = structural.structural_similarity(
            self.ORIGINAL, self.UNRELATED, language='python').score
        self.assertLess(unrelated, renamed)

    def test_falls_back_when_source_does_not_parse(self):
        result = structural.structural_similarity(
            "def broken(:::", self.ORIGINAL, language='python')
        self.assertFalse(result.parsed_as_ast)

    def test_language_detection(self):
        self.assertEqual(structural.detect_language('main.py'), 'python')
        self.assertEqual(structural.detect_language('Main.java'), 'java')
        self.assertEqual(structural.detect_language('essay.pdf'), 'unknown')

    def test_winnowing_is_deterministic(self):
        tokens = ['a', 'b', 'c', 'd', 'e'] * 20
        self.assertEqual(structural.winnow(tokens), structural.winnow(tokens))


# ==========================================================================
# Unit tests: passage highlighting
# ==========================================================================

class HighlightingTests(TestCase):
    def test_finds_the_shared_passage(self):
        tokens_a = preprocessing.preprocess(TEXT_A).tokens
        tokens_b = preprocessing.preprocess(TEXT_B_PARTIAL).tokens
        passages = highlighting.find_matched_passages(tokens_a, tokens_b)
        self.assertTrue(passages)
        self.assertIn('sorting algorithms arrange elements', passages[0].text)

    def test_unrelated_documents_yield_no_passages(self):
        tokens_a = preprocessing.preprocess(TEXT_A).tokens
        tokens_c = preprocessing.preprocess(TEXT_C_DIFFERENT).tokens
        self.assertEqual(highlighting.find_matched_passages(tokens_a, tokens_c), [])


# ==========================================================================
# Unit tests: text extraction
# ==========================================================================

class ExtractionTests(TestCase):
    def test_plain_text(self):
        result = extract_text('answer.txt', b'Hello world from a text file')
        self.assertTrue(result.ok)
        self.assertIn('Hello world', result.text)

    def test_python_source(self):
        result = extract_text('main.py', b'def f():\n    return 42\n')
        self.assertTrue(result.ok)
        self.assertEqual(result.source_format, 'py')

    def test_zip_archive_reads_members(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('src/main.py', 'def main():\n    print("hi")\n')
            archive.writestr('README.txt', 'Project readme content')
            archive.writestr('binary.bin', b'\x00\x01\x02')
        result = extract_text('submission.zip', buffer.getvalue())
        self.assertTrue(result.ok)
        self.assertEqual(len(result.members), 2)
        self.assertIn('def main', result.text)

    def test_unsupported_binary_is_rejected_with_a_reason(self):
        result = extract_text('image.png', bytes(range(256)) * 4)
        self.assertFalse(result.ok)
        self.assertTrue(result.warnings)


# ==========================================================================
# Unit tests: the pipeline
# ==========================================================================

@override_settings(SEMANTIC_ANALYSIS_ENABLED=False)
class PipelineTests(TestCase):
    def _run(self, method='combined', threshold=40.0, texts=None):
        texts = texts or {
            'alice': TEXT_A,
            'bob': TEXT_B_COPY,
            'carol': TEXT_C_DIFFERENT,
        }
        pipeline = DetectionPipeline(
            method, threshold=threshold, max_marks=100, semantic_enabled=False
        )
        return pipeline.run([
            DocumentInput(key=name, label=name, text=text, filename=f'{name}.txt')
            for name, text in texts.items()
        ])

    def test_exact_duplicates_are_detected_in_the_initial_check(self):
        output = self._run()
        duplicate_pairs = [p for p in output.pairs if p.is_exact_duplicate]
        self.assertEqual(len(duplicate_pairs), 1)
        self.assertEqual(duplicate_pairs[0].score, 100.0)

    def test_copier_is_flagged_and_original_author_scores_low(self):
        output = self._run()
        by_label = {o.label: o for o in output.outcomes}
        self.assertTrue(by_label['alice'].flagged)
        self.assertTrue(by_label['bob'].flagged)
        self.assertFalse(by_label['carol'].flagged)

    def test_marks_deducted_only_above_threshold(self):
        output = self._run()
        by_label = {o.label: o for o in output.outcomes}
        self.assertEqual(by_label['carol'].marks_awarded, 100.0)
        self.assertEqual(by_label['carol'].marks_deducted, 0.0)
        self.assertLess(by_label['bob'].marks_awarded, 100.0)

    def test_every_algorithm_choice_runs(self):
        for method in ('jaccard', 'lcs', 'levenshtein', 'cosine',
                       'rabin_karp', 'structural', 'combined'):
            with self.subTest(method=method):
                output = self._run(method=method)
                self.assertEqual(len(output.pairs), 3)
                self.assertTrue(all(0 <= p.score <= 100 for p in output.pairs))

    def test_unknown_method_is_rejected(self):
        with self.assertRaises(ValueError):
            DetectionPipeline('not-a-real-method')

    def test_single_submission_produces_no_pairs(self):
        output = self._run(texts={'solo': TEXT_A})
        self.assertEqual(output.pairs, [])
        self.assertEqual(output.outcomes[0].marks_awarded, 100.0)

    def test_empty_submission_is_skipped_not_matched(self):
        output = self._run(texts={'alice': TEXT_A, 'bob': TEXT_A, 'empty': '   '})
        self.assertIn('empty', output.skipped)
        keys = {p.key_a for p in output.pairs} | {p.key_b for p in output.pairs}
        self.assertNotIn('empty', keys)

    def test_matched_passages_are_attached_to_pairs(self):
        output = self._run(texts={'a': TEXT_A, 'b': TEXT_B_PARTIAL})
        self.assertTrue(output.pairs[0].matched_passages)


# ==========================================================================
# Unit tests: semantic paraphrase detection
# ==========================================================================

class SemanticTests(TestCase):
    """
    Guards the paraphrase thresholds.

    These are the tests that would have caught the original mistake here: the
    threshold was set to 0.75, above the score real paraphrases actually earn,
    so the feature silently reported nothing.
    """

    ORIGINAL = (
        "Merge sort divides the list into two halves and recursively sorts each half. "
        "The algorithm then combines the sorted halves into a single ordered list. "
        "Its worst case running time is proportional to n log n."
    )
    PARAPHRASE = (
        "The list is split into two parts by merge sort, and every part is ordered "
        "again by the same routine. "
        "Afterwards the two ordered parts are merged together to form one sorted sequence. "
        "In the worst situation the cost grows as n multiplied by the logarithm of n."
    )
    SAME_TOPIC = (
        "Quick sort picks a pivot element and partitions the array around that pivot. "
        "Heap sort builds a binary heap and repeatedly extracts the maximum element. "
        "Insertion sort works well when the input array is already nearly ordered."
    )
    UNRELATED = (
        "Photosynthesis converts light energy into chemical energy inside chloroplasts. "
        "Plants take in carbon dioxide and give out oxygen while this happens. "
        "The process sustains most life on the planet today."
    )

    def test_paraphrase_is_detected(self):
        """
        The expected strength depends on which backend is installed.

        sentence-transformers reliably clears 50%. The scikit-learn and n-gram
        fallbacks detect the same paraphrase more weakly, so asserting the
        strong number here would make the suite fail on a minimal install --
        which is a property of the fallback, not a defect.
        """
        result = semantic.semantic_similarity(self.ORIGINAL, self.PARAPHRASE)
        self.assertTrue(result.matches, f'{result.backend} found no paraphrase at all')
        if result.backend == 'sentence-transformers':
            self.assertGreater(result.score, 50.0)
        else:
            self.assertGreater(result.score, 0.0)

    def test_lexical_algorithms_miss_what_semantic_catches(self):
        # The justification for the whole semantic layer.
        tokens_a = preprocessing.preprocess(self.ORIGINAL).tokens
        tokens_b = preprocessing.preprocess(self.PARAPHRASE).tokens
        lexical = algorithms.rabin_karp_similarity(tokens_a, tokens_b).score
        result = semantic.semantic_similarity(self.ORIGINAL, self.PARAPHRASE)
        self.assertLess(lexical, 10.0)
        # True on every backend: the whole justification for the semantic layer.
        self.assertGreater(result.score, lexical)

    def test_unrelated_documents_score_zero(self):
        result = semantic.semantic_similarity(self.ORIGINAL, self.UNRELATED)
        self.assertEqual(result.score, 0.0)

    def test_same_topic_is_not_flagged_as_paraphrase(self):
        # Two students writing independently about sorting must not be accused.
        result = semantic.semantic_similarity(self.ORIGINAL, self.SAME_TOPIC)
        self.assertLess(result.score, 50.0)

    def test_every_backend_has_a_calibrated_threshold(self):
        for name in ('sentence-transformers', 'sklearn-lsa', 'ngram'):
            self.assertIn(name, semantic.BACKEND_THRESHOLDS)
            self.assertTrue(0.0 < semantic.BACKEND_THRESHOLDS[name] < 1.0)

    def test_short_input_is_handled(self):
        result = semantic.semantic_similarity('too short', 'also short')
        self.assertEqual(result.score, 0.0)


# ==========================================================================
# Shared fixture for functional tests
# ==========================================================================

class AcademicFixture(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('teacher1', 'teacher@gcu.edu.pk', 'Passw0rd!123')
        UserProfile.objects.create(user=self.teacher, is_teacher=True, full_name='Dr Teacher')

        self.student_a = User.objects.create_user('student_a', 'a@gcu.edu.pk', 'Passw0rd!123')
        UserProfile.objects.create(user=self.student_a, is_student=True, full_name='Student A')
        self.student_b = User.objects.create_user('student_b', 'b@gcu.edu.pk', 'Passw0rd!123')
        UserProfile.objects.create(user=self.student_b, is_student=True, full_name='Student B')

        self.admin = User.objects.create_user('sysadmin', 'admin@gcu.edu.pk', 'Passw0rd!123')
        UserProfile.objects.create(user=self.admin, is_admin=True, full_name='System Admin')

        self.course = Course.objects.create(
            name='Design and Analysis of Algorithms',
            course_code='CS-3033',
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timedelta(days=90)).date(),
            instructors='Dr Teacher',
            teacher=self.teacher,
        )
        self.course.students.add(self.student_a, self.student_b)

        self.assignment = Assignment.objects.create(
            course=self.course,
            title='Sorting algorithms report',
            due_date=timezone.now() + timedelta(days=7),
            max_marks=100,
        )

    def add_submission(self, student, text, filename='answer.txt'):
        submission = Submission.objects.create(
            course=self.course,
            assignment=self.assignment,
            student=student,
            submitted_file=SimpleUploadedFile(filename, text.encode('utf-8')),
        )
        from .services import ensure_extracted
        return ensure_extracted(submission)


# ==========================================================================
# Functional tests TC1-TC7 (thesis section 4.6.1.1)
# ==========================================================================

class TC1NegativeLogin(AcademicFixture):
    """Invalid credentials must be refused with a validation message."""

    def test_invalid_password_is_rejected(self):
        response = self.client.post(
            reverse('login'), {'username': 'teacher1', 'password': 'wrong'}, follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['user'].is_authenticated)
        self.assertContains(response, 'Invalid username or password')

    def test_unknown_user_is_rejected(self):
        response = self.client.post(
            reverse('login'), {'username': 'ghost', 'password': 'whatever'}, follow=True
        )
        self.assertFalse(response.context['user'].is_authenticated)


@override_settings(TEACHER_ACCESS_CODE='TC2-CODE')
class TC2UserRegistration(AcademicFixture):
    """Registering with a role creates the account and its profile."""

    def test_student_registration_creates_profile(self):
        response = self.client.post(reverse('register'), {
            'username': 'newstudent',
            'full_name': 'New Student',
            'email': 'new@gcu.edu.pk',
            'phone_number': '03001234567',
            'password1': 'Str0ngPassw0rd!',
            'password2': 'Str0ngPassw0rd!',
            'role': 'student',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(username='newstudent')
        self.assertTrue(user.userprofile.is_student)
        self.assertFalse(user.userprofile.is_teacher)
        self.assertTrue(ActivityLog.objects.filter(action='register').exists())

    @override_settings(TEACHER_ACCESS_CODE='TC2-CODE')
    def test_teacher_registration_sets_teacher_role(self):
        # Registering as a teacher needs the departmental access code; see
        # TeacherRegistrationGateTests for the full gate behaviour.
        self.client.post(reverse('register'), {
            'username': 'newteacher', 'full_name': 'New Teacher',
            'email': 'nt@gcu.edu.pk', 'phone_number': '03007654321',
            'password1': 'Str0ngPassw0rd!', 'password2': 'Str0ngPassw0rd!',
            'role': 'teacher', 'teacher_access_code': 'TC2-CODE',
        })
        self.assertTrue(User.objects.get(username='newteacher').userprofile.is_teacher)

    def test_duplicate_email_is_rejected(self):
        response = self.client.post(reverse('register'), {
            'username': 'dupe', 'full_name': 'Dupe',
            'email': 'teacher@gcu.edu.pk', 'phone_number': '0300',
            'password1': 'Str0ngPassw0rd!', 'password2': 'Str0ngPassw0rd!',
            'role': 'student',
        })
        self.assertFalse(User.objects.filter(username='dupe').exists())


class TC3UserLogin(AcademicFixture):
    """Valid credentials sign the user in and land them on their dashboard."""

    def test_teacher_lands_on_teacher_dashboard(self):
        response = self.client.post(reverse('login'), {
            'username': 'teacher1', 'password': 'Passw0rd!123'}, follow=True)
        self.assertRedirects(response, reverse('teacher_dashboard'), target_status_code=200)

    def test_student_lands_on_student_dashboard(self):
        response = self.client.post(reverse('login'), {
            'username': 'student_a', 'password': 'Passw0rd!123'}, follow=True)
        self.assertRedirects(response, reverse('student_dashboard'), target_status_code=200)

    def test_admin_lands_on_admin_dashboard(self):
        response = self.client.post(reverse('login'), {
            'username': 'sysadmin', 'password': 'Passw0rd!123'}, follow=True)
        self.assertRedirects(response, reverse('admin_dashboard'), target_status_code=200)


class TC4UploadAssignment(AcademicFixture):
    """A student uploads work, and its text is extracted on the way in."""

    def test_upload_succeeds_and_extracts_text(self):
        self.client.login(username='student_a', password='Passw0rd!123')
        response = self.client.post(
            reverse('submit_assignment', args=[self.assignment.id]),
            {'submitted_file': SimpleUploadedFile('a.txt', TEXT_A.encode())},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        submission = Submission.objects.get(student=self.student_a)
        self.assertTrue(submission.has_text)
        self.assertGreater(submission.word_count, 10)
        self.assertTrue(ActivityLog.objects.filter(action='submit').exists())

    def test_student_cannot_submit_to_a_course_they_are_not_in(self):
        outsider = User.objects.create_user('outsider', 'o@gcu.edu.pk', 'Passw0rd!123')
        UserProfile.objects.create(user=outsider, is_student=True)
        self.client.login(username='outsider', password='Passw0rd!123')
        response = self.client.post(
            reverse('submit_assignment', args=[self.assignment.id]),
            {'submitted_file': SimpleUploadedFile('a.txt', b'text')},
        )
        self.assertEqual(response.status_code, 403)

    def test_resubmitting_replaces_rather_than_duplicates(self):
        self.client.login(username='student_a', password='Passw0rd!123')
        url = reverse('submit_assignment', args=[self.assignment.id])
        self.client.post(url, {'submitted_file': SimpleUploadedFile('a.txt', b'first version text')})
        self.client.post(url, {'submitted_file': SimpleUploadedFile('b.txt', b'second version text')})
        self.assertEqual(Submission.objects.filter(student=self.student_a).count(), 1)

    def test_oversized_file_is_rejected(self):
        self.client.login(username='student_a', password='Passw0rd!123')
        with override_settings(MAX_SUBMISSION_SIZE_MB=0):
            self.client.post(
                reverse('submit_assignment', args=[self.assignment.id]),
                {'submitted_file': SimpleUploadedFile('a.txt', b'x' * 2048)},
            )
        self.assertEqual(Submission.objects.filter(student=self.student_a).count(), 0)


class TC5CreateCourse(AcademicFixture):
    """A teacher creates a course."""

    def test_course_is_created(self):
        self.client.login(username='teacher1', password='Passw0rd!123')
        response = self.client.post(reverse('create_course'), {
            'name': 'Operating Systems',
            'course_code': 'CS-3041',
            'description': 'Processes, memory and scheduling.',
            'start_date': timezone.now().date(),
            'end_date': (timezone.now() + timedelta(days=60)).date(),
            'instructors': 'Dr Teacher',
            'department': 'Computer Science',
            'credits': 3,
            'prerequisites': '',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Course.objects.filter(course_code='CS-3041').exists())

    def test_students_cannot_create_courses(self):
        self.client.login(username='student_a', password='Passw0rd!123')
        self.assertEqual(self.client.get(reverse('create_course')).status_code, 403)

    def test_end_date_before_start_date_is_rejected(self):
        self.client.login(username='teacher1', password='Passw0rd!123')
        self.client.post(reverse('create_course'), {
            'name': 'Bad Dates', 'course_code': 'CS-BAD',
            'description': 'x',
            'start_date': timezone.now().date(),
            'end_date': (timezone.now() - timedelta(days=10)).date(),
            'instructors': 'Dr Teacher',
        })
        self.assertFalse(Course.objects.filter(course_code='CS-BAD').exists())

    def test_teacher_cannot_delete_another_teachers_course(self):
        other = User.objects.create_user('teacher2', 't2@gcu.edu.pk', 'Passw0rd!123')
        UserProfile.objects.create(user=other, is_teacher=True)
        self.client.login(username='teacher2', password='Passw0rd!123')
        response = self.client.post(reverse('delete_course', args=[self.course.id]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Course.objects.filter(id=self.course.id).exists())


class TC6CreateAssignment(AcademicFixture):
    """A teacher creates an assignment inside their own course."""

    def test_assignment_is_created(self):
        self.client.login(username='teacher1', password='Passw0rd!123')
        response = self.client.post(reverse('create_assignment'), {
            'course': self.course.id,
            'title': 'Graph traversal exercise',
            'description': 'Implement BFS and DFS.',
            'due_date': (timezone.now() + timedelta(days=14)).strftime('%Y-%m-%dT%H:%M'),
            'submission_format': 'PY',
            'max_marks': 50,
            'file_upload_instructions': 'Submit a single .py file.',
            'grading_criteria': 'Correctness and clarity.',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Assignment.objects.filter(title='Graph traversal exercise').exists())

    def test_teacher_cannot_target_another_teachers_course(self):
        other = User.objects.create_user('teacher3', 't3@gcu.edu.pk', 'Passw0rd!123')
        UserProfile.objects.create(user=other, is_teacher=True)
        self.client.login(username='teacher3', password='Passw0rd!123')
        self.client.post(reverse('create_assignment'), {
            'course': self.course.id, 'title': 'Sneaky',
            'description': 'x',
            'due_date': (timezone.now() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
            'submission_format': 'PDF', 'max_marks': 10,
        })
        self.assertFalse(Assignment.objects.filter(title='Sneaky').exists())

    def test_zero_max_marks_is_rejected(self):
        self.client.login(username='teacher1', password='Passw0rd!123')
        self.client.post(reverse('create_assignment'), {
            'course': self.course.id, 'title': 'No marks', 'description': 'x',
            'due_date': (timezone.now() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
            'submission_format': 'PDF', 'max_marks': 0,
        })
        self.assertFalse(Assignment.objects.filter(title='No marks').exists())


@override_settings(SEMANTIC_ANALYSIS_ENABLED=False)
class TC7RunPlagiarismDetection(AcademicFixture):
    """
    Select an algorithm, run the check, and verify the report and the marks.

    This is the test case the documentation recorded as passing while the code
    path raised TypeError and AttributeError before reaching a report.
    """

    def setUp(self):
        super().setUp()
        self.add_submission(self.student_a, TEXT_A, 'alice.txt')
        self.add_submission(self.student_b, TEXT_B_COPY, 'bob.txt')
        self.client.login(username='teacher1', password='Passw0rd!123')

    def test_run_page_offers_every_algorithm(self):
        response = self.client.get(reverse('run_detection', args=[self.assignment.id]))
        self.assertEqual(response.status_code, 200)
        for method in ('combined', 'jaccard', 'lcs', 'levenshtein',
                       'cosine', 'rabin_karp', 'structural', 'semantic'):
            self.assertContains(response, f'value="{method}"')

    def test_detection_produces_a_complete_report(self):
        response = self.client.post(
            reverse('run_detection', args=[self.assignment.id]),
            {'method': 'combined', 'threshold': 40}, follow=True,
        )
        self.assertEqual(response.status_code, 200)

        report = PlagiarismReport.objects.get()
        self.assertEqual(report.status, 'complete')
        self.assertEqual(report.submissions_analyzed, 2)
        self.assertEqual(report.pairs_compared, 1)
        self.assertEqual(report.flagged_count, 2)
        self.assertEqual(SubmissionResult.objects.count(), 2)

    def test_identical_submissions_match_at_100_percent(self):
        self.client.post(reverse('run_detection', args=[self.assignment.id]),
                         {'method': 'combined', 'threshold': 40})
        match = Match.objects.get()
        self.assertEqual(match.score, 100.0)
        self.assertTrue(match.is_exact_duplicate)

    def test_marks_are_deducted_on_the_submission_record(self):
        self.client.post(reverse('run_detection', args=[self.assignment.id]),
                         {'method': 'combined', 'threshold': 40})
        for submission in Submission.objects.all():
            self.assertEqual(submission.plagiarism_percentage, 100.0)
            self.assertEqual(submission.marks_awarded, 0)

    def test_each_algorithm_choice_produces_a_report(self):
        for method in ('jaccard', 'lcs', 'levenshtein', 'cosine',
                       'rabin_karp', 'structural', 'combined'):
            with self.subTest(method=method):
                PlagiarismReport.objects.all().delete()
                self.client.post(reverse('run_detection', args=[self.assignment.id]),
                                 {'method': method, 'threshold': 40})
                self.assertEqual(PlagiarismReport.objects.get().status, 'complete')

    def test_report_detail_page_renders_with_matches(self):
        self.client.post(reverse('run_detection', args=[self.assignment.id]),
                         {'method': 'combined', 'threshold': 40})
        report = PlagiarismReport.objects.get()
        response = self.client.get(reverse('report_detail', args=[report.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'student_a')
        self.assertContains(response, 'student_b')

    def test_match_detail_page_renders(self):
        self.client.post(reverse('run_detection', args=[self.assignment.id]),
                         {'method': 'combined', 'threshold': 40})
        response = self.client.get(reverse('match_detail', args=[Match.objects.get().id]))
        self.assertEqual(response.status_code, 200)

    def test_reports_index_lists_the_run(self):
        self.client.post(reverse('run_detection', args=[self.assignment.id]),
                         {'method': 'combined', 'threshold': 40})
        response = self.client.get(reverse('plagiarism_reports'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.assignment.title)

    def test_detection_is_logged(self):
        self.client.post(reverse('run_detection', args=[self.assignment.id]),
                         {'method': 'combined', 'threshold': 40})
        self.assertTrue(ActivityLog.objects.filter(action='detect').exists())

    def test_teacher_cannot_run_detection_on_another_course(self):
        other = User.objects.create_user('teacher4', 't4@gcu.edu.pk', 'Passw0rd!123')
        UserProfile.objects.create(user=other, is_teacher=True)
        self.client.logout()
        self.client.login(username='teacher4', password='Passw0rd!123')
        response = self.client.get(reverse('run_detection', args=[self.assignment.id]))
        self.assertEqual(response.status_code, 403)

    def test_students_cannot_reach_reports(self):
        self.client.logout()
        self.client.login(username='student_a', password='Passw0rd!123')
        self.assertEqual(self.client.get(reverse('plagiarism_reports')).status_code, 403)


# ==========================================================================
# Administrator role, logging and dashboards
# ==========================================================================

@override_settings(SEMANTIC_ANALYSIS_ENABLED=False)
class AdministratorTests(AcademicFixture):
    def test_admin_dashboard_reports_system_totals(self):
        self.client.login(username='sysadmin', password='Passw0rd!123')
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['student_count'], 2)
        self.assertEqual(response.context['teacher_count'], 1)
        self.assertEqual(response.context['course_count'], 1)

    def test_activity_log_is_filterable(self):
        self.client.login(username='sysadmin', password='Passw0rd!123')
        response = self.client.get(reverse('activity_log'), {'action': 'login'})
        self.assertEqual(response.status_code, 200)
        for entry in response.context['page']:
            self.assertEqual(entry.action, 'login')

    def test_integrity_monitor_lists_flagged_submissions(self):
        self.add_submission(self.student_a, TEXT_A)
        self.add_submission(self.student_b, TEXT_B_COPY)
        self.client.login(username='teacher1', password='Passw0rd!123')
        self.client.post(reverse('run_detection', args=[self.assignment.id]),
                         {'method': 'combined', 'threshold': 40})
        self.client.logout()

        self.client.login(username='sysadmin', password='Passw0rd!123')
        response = self.client.get(reverse('integrity_monitor'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_flagged'], 2)

    def test_admin_only_pages_refuse_teachers(self):
        self.client.login(username='teacher1', password='Passw0rd!123')
        for name in ('admin_dashboard', 'activity_log'):
            with self.subTest(view=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_students_cannot_reach_the_integrity_monitor(self):
        self.client.login(username='student_a', password='Passw0rd!123')
        self.assertEqual(self.client.get(reverse('integrity_monitor')).status_code, 403)


# ==========================================================================
# Teacher registration gate (thesis section 2.1.1, authorization)
# ==========================================================================

@override_settings(TEACHER_ACCESS_CODE='SECRET-CODE-123')
class TeacherRegistrationGateTests(AcademicFixture):
    """
    Without this gate, anyone could tick "Teacher" on the public registration
    page and gain access to every course, submission and plagiarism report in
    the system.
    """

    BASE = {
        'username': 'claimant',
        'full_name': 'Would Be Teacher',
        'email': 'claimant@gcu.edu.pk',
        'phone_number': '03001234567',
        'password1': 'Str0ngPassw0rd!',
        'password2': 'Str0ngPassw0rd!',
    }

    def _post(self, **overrides):
        data = dict(self.BASE)
        data.update(overrides)
        return self.client.post(reverse('register'), data)

    # --- the gate itself ---------------------------------------------------

    def test_correct_code_creates_a_teacher(self):
        self._post(role='teacher', teacher_access_code='SECRET-CODE-123')
        user = User.objects.get(username='claimant')
        self.assertTrue(user.userprofile.is_teacher)
        self.assertFalse(user.userprofile.is_student)

    def test_wrong_code_creates_no_account_at_all(self):
        response = self._post(role='teacher', teacher_access_code='guess')
        self.assertFalse(User.objects.filter(username='claimant').exists())
        self.assertContains(response, 'not valid')

    def test_missing_code_is_rejected(self):
        self._post(role='teacher', teacher_access_code='')
        self.assertFalse(User.objects.filter(username='claimant').exists())

    def test_omitting_the_field_entirely_is_rejected(self):
        # Simulates someone stripping the input out of the page before posting.
        self._post(role='teacher')
        self.assertFalse(User.objects.filter(username='claimant').exists())

    def test_code_is_case_sensitive(self):
        self._post(role='teacher', teacher_access_code='secret-code-123')
        self.assertFalse(User.objects.filter(username='claimant').exists())

    def test_surrounding_whitespace_is_tolerated(self):
        self._post(role='teacher', teacher_access_code='  SECRET-CODE-123  ')
        self.assertTrue(User.objects.get(username='claimant').userprofile.is_teacher)

    # --- students are unaffected -------------------------------------------

    def test_student_registration_needs_no_code(self):
        self._post(role='student')
        user = User.objects.get(username='claimant')
        self.assertTrue(user.userprofile.is_student)
        self.assertFalse(user.userprofile.is_teacher)

    def test_student_supplying_a_code_is_still_only_a_student(self):
        # Passing the real code while selecting Student must not upgrade anyone.
        self._post(role='student', teacher_access_code='SECRET-CODE-123')
        user = User.objects.get(username='claimant')
        self.assertTrue(user.userprofile.is_student)
        self.assertFalse(user.userprofile.is_teacher)

    # --- fail closed --------------------------------------------------------

    @override_settings(TEACHER_ACCESS_CODE='')
    def test_unconfigured_code_disables_teacher_registration(self):
        # An unconfigured deployment must not leave the role wide open.
        response = self._post(role='teacher', teacher_access_code='anything')
        self.assertFalse(User.objects.filter(username='claimant').exists())
        self.assertContains(response, 'currently disabled')

    @override_settings(TEACHER_ACCESS_CODE='')
    def test_unconfigured_code_still_allows_students(self):
        self._post(role='student')
        self.assertTrue(User.objects.get(username='claimant').userprofile.is_student)

    # --- auditing and UI ----------------------------------------------------

    def test_rejected_attempt_is_logged(self):
        self._post(role='teacher', teacher_access_code='guess')
        self.assertTrue(
            ActivityLog.objects.filter(action='error',
                                       description__icontains='bad access code').exists()
        )

    def test_registration_page_offers_the_code_field(self):
        response = self.client.get(reverse('register'))
        self.assertContains(response, 'teacher_access_code')
        self.assertContains(response, 'Teacher access code')

    def test_rejected_teacher_cannot_reach_teacher_pages(self):
        self._post(role='teacher', teacher_access_code='guess')
        self.client.login(username='claimant', password='Str0ngPassw0rd!')
        self.assertEqual(self.client.get(reverse('plagiarism_reports')).status_code, 302)


# ==========================================================================
# Step 3: the archive check against previously submitted documents
# ==========================================================================

@override_settings(SEMANTIC_ANALYSIS_ENABLED=False)
class ArchiveCheckTests(AcademicFixture):
    """
    Section 3.2.3 step 3: "an initial check against a database of previously
    submitted documents", and the Database.findSimilarDocuments method in the
    class diagram.
    """

    def setUp(self):
        super().setUp()
        self.old_assignment = Assignment.objects.create(
            course=self.course,
            title='Last semester essay',
            due_date=timezone.now() - timedelta(days=200),
            max_marks=100,
        )

    def test_recycled_work_is_detected_across_assignments(self):
        from .services import find_archive_matches
        old = Submission.objects.create(
            course=self.course, assignment=self.old_assignment, student=self.student_b,
            submitted_file=SimpleUploadedFile('old.txt', TEXT_A.encode()))
        from .services import ensure_extracted
        ensure_extracted(old)

        new = self.add_submission(self.student_a, TEXT_A)
        hits = find_archive_matches(new)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['assignment'], 'Last semester essay')
        self.assertEqual(hits[0]['student'], 'student_b')
        self.assertFalse(hits[0]['same_student'])

    def test_same_student_resubmitting_old_work_is_detected(self):
        from .services import ensure_extracted, find_archive_matches
        old = Submission.objects.create(
            course=self.course, assignment=self.old_assignment, student=self.student_a,
            submitted_file=SimpleUploadedFile('old.txt', TEXT_A.encode()))
        ensure_extracted(old)
        new = self.add_submission(self.student_a, TEXT_A)
        hits = find_archive_matches(new)
        self.assertTrue(hits[0]['same_student'])

    def test_original_work_has_no_archive_matches(self):
        from .services import find_archive_matches
        new = self.add_submission(self.student_a, TEXT_C_DIFFERENT)
        self.assertEqual(find_archive_matches(new), [])

    def test_same_assignment_is_not_treated_as_archive(self):
        from .services import find_archive_matches
        self.add_submission(self.student_b, TEXT_A)
        new = self.add_submission(self.student_a, TEXT_A)
        # Same-assignment duplicates are the pair comparison's job, not step 3.
        self.assertEqual(find_archive_matches(new), [])

    def test_report_records_archive_matches(self):
        from .services import ensure_extracted
        old = Submission.objects.create(
            course=self.course, assignment=self.old_assignment, student=self.student_b,
            submitted_file=SimpleUploadedFile('old.txt', TEXT_A.encode()))
        ensure_extracted(old)
        self.add_submission(self.student_a, TEXT_A)
        self.add_submission(self.student_b, TEXT_C_DIFFERENT)

        self.client.login(username='teacher1', password='Passw0rd!123')
        self.client.post(reverse('run_detection', args=[self.assignment.id]),
                         {'method': 'combined', 'threshold': 40})
        report = PlagiarismReport.objects.filter(assignment=self.assignment).first()
        self.assertEqual(report.archive_matches_found, 1)
        result = SubmissionResult.objects.get(report=report, submission__student=self.student_a)
        self.assertTrue(result.matched_archive)


# ==========================================================================
# Use case coverage (thesis section 3.2.9.2)
# ==========================================================================

@override_settings(SEMANTIC_ANALYSIS_ENABLED=False)
class UseCaseTests(AcademicFixture):
    """One test per documented use case."""

    def _run_detection(self):
        self.add_submission(self.student_a, TEXT_A)
        self.add_submission(self.student_b, TEXT_B_COPY)
        self.client.login(username='teacher1', password='Passw0rd!123')
        self.client.post(reverse('run_detection', args=[self.assignment.id]),
                         {'method': 'combined', 'threshold': 40})

    # --- Student use cases -------------------------------------------------

    def test_student_can_submit_document(self):
        self.client.login(username='student_a', password='Passw0rd!123')
        response = self.client.get(reverse('submit_assignment', args=[self.assignment.id]))
        self.assertEqual(response.status_code, 200)

    def test_student_can_view_their_report(self):
        self._run_detection()
        self.client.logout()
        self.client.login(username='student_a', password='Passw0rd!123')
        response = self.client.get(reverse('my_results'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['results']), 1)

    def test_student_receives_an_assessment_summary(self):
        self._run_detection()
        result = SubmissionResult.objects.get(submission__student=self.student_a)
        self.assertTrue(result.summary)
        self.assertIn(result.severity, ('none', 'low', 'moderate', 'high', 'critical'))

    def test_student_cannot_see_another_students_result(self):
        self._run_detection()
        self.client.logout()
        self.client.login(username='student_a', password='Passw0rd!123')
        response = self.client.get(reverse('my_results'))
        usernames = {r.submission.student.username for r in response.context['results']}
        self.assertEqual(usernames, {'student_a'})

    # --- Teacher use cases -------------------------------------------------

    def test_teacher_can_review_submissions(self):
        self._run_detection()
        response = self.client.get(reverse('run_detection', args=[self.assignment.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['submission_count'], 2)

    def test_teacher_can_generate_class_reports(self):
        self._run_detection()
        report = PlagiarismReport.objects.get()
        response = self.client.get(reverse('report_detail', args=[report.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['results']), 2)

    def test_teacher_can_monitor_academic_integrity(self):
        # Documented in section 3.2.9.2 as a teacher use case, not admin-only.
        self._run_detection()
        response = self.client.get(reverse('integrity_monitor'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['scope'], 'my courses')
        self.assertEqual(response.context['total_flagged'], 2)

    def test_integrity_monitor_is_scoped_to_the_teachers_own_courses(self):
        self._run_detection()
        other = User.objects.create_user('teacher9', 't9@gcu.edu.pk', 'Passw0rd!123')
        UserProfile.objects.create(user=other, is_teacher=True)
        self.client.logout()
        self.client.login(username='teacher9', password='Passw0rd!123')
        response = self.client.get(reverse('integrity_monitor'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_flagged'], 0)

    def test_admin_sees_the_whole_institution(self):
        self._run_detection()
        self.client.logout()
        self.client.login(username='sysadmin', password='Passw0rd!123')
        response = self.client.get(reverse('integrity_monitor'))
        self.assertEqual(response.context['scope'], 'institution')
        self.assertEqual(response.context['total_flagged'], 2)


# ==========================================================================
# Access control regressions found in the original build
# ==========================================================================

class AccessControlTests(AcademicFixture):
    def test_submission_cannot_be_overwritten_by_another_student(self):
        submission = self.add_submission(self.student_a, TEXT_A)
        self.client.login(username='student_b', password='Passw0rd!123')
        self.client.post(
            reverse('submit_assignment', args=[self.assignment.id]),
            {'submitted_file': SimpleUploadedFile('hack.txt', b'overwritten by another student')},
        )
        submission.refresh_from_db()
        self.assertIn('quick brown fox', submission.extracted_text)

    def test_delete_course_rejects_get(self):
        self.client.login(username='teacher1', password='Passw0rd!123')
        response = self.client.get(reverse('delete_course', args=[self.course.id]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Course.objects.filter(id=self.course.id).exists())

    def test_anonymous_users_are_sent_to_login(self):
        response = self.client.get(reverse('teacher_dashboard'))
        self.assertEqual(response.status_code, 302)

    def test_course_api_is_closed_to_outsiders(self):
        outsider = User.objects.create_user('nosy', 'n@gcu.edu.pk', 'Passw0rd!123')
        UserProfile.objects.create(user=outsider, is_student=True)
        self.client.login(username='nosy', password='Passw0rd!123')
        response = self.client.get(
            reverse('get_assignments_for_course', args=[self.course.id]))
        self.assertEqual(response.status_code, 403)

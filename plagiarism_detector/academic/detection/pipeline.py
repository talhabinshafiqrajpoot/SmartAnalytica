"""
The seven-step detection workflow from thesis section 3.2.3, wired end to end.

``DetectionPipeline`` is deliberately free of database code so it can be tested
on plain strings. ``run_detection_for_assignment`` is the Django-facing entry
point that loads submissions, runs the pipeline and persists the report.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import algorithms, highlighting, preprocessing, semantic, structural

logger = logging.getLogger(__name__)

# Options offered to teachers in the "Run Plagiarism Detection" form.
METHOD_CHOICES: List[Tuple[str, str]] = [
    ('combined', 'Combined analysis (recommended)'),
    ('jaccard', 'Jaccard Similarity'),
    ('lcs', 'Longest Common Subsequence'),
    ('levenshtein', 'Levenshtein Distance'),
    ('cosine', 'Cosine Similarity'),
    ('rabin_karp', 'Rabin-Karp Fingerprinting'),
    ('structural', 'Structural code analysis (AST + winnowing)'),
    ('semantic', 'Semantic paraphrase detection (NLP)'),
]
METHOD_LABELS: Dict[str, str] = dict(METHOD_CHOICES)

LEXICAL_METHODS = {'jaccard', 'lcs', 'levenshtein', 'cosine', 'rabin_karp'}

# Weights for the combined score. Structural evidence is weighted highest for
# code because it survives renaming; verbatim fingerprints next; semantic
# similarity last, because paraphrase scores are the noisiest of the three.
COMBINED_WEIGHTS = {
    'rabin_karp': 0.30,
    'cosine': 0.15,
    'jaccard': 0.10,
    'structural': 0.30,
    'semantic': 0.15,
}

SEVERITY_BANDS = [
    (85.0, 'critical'),
    (65.0, 'high'),
    (45.0, 'moderate'),
    (25.0, 'low'),
]


@dataclass
class DocumentInput:
    """One submission entering the pipeline."""

    key: object
    label: str
    text: str
    filename: str = ''


@dataclass
class PreparedDocument:
    """A document after step 2, ready for comparison."""

    key: object
    label: str
    filename: str
    raw_text: str
    processed: preprocessing.PreprocessedDocument
    language: str = 'unknown'
    content_hash: str = ''

    @property
    def is_code(self) -> bool:
        return self.language != 'unknown'


@dataclass
class PairResult:
    """The outcome of comparing one pair of submissions."""

    key_a: object
    key_b: object
    label_a: str
    label_b: str
    score: float = 0.0
    method_scores: Dict[str, float] = field(default_factory=dict)
    structural_score: float = 0.0
    semantic_score: float = 0.0
    matched_passages: List[dict] = field(default_factory=list)
    paraphrase_matches: List[dict] = field(default_factory=list)
    is_exact_duplicate: bool = False


@dataclass
class DocumentOutcome:
    """Per-submission aggregate: step 5 (similarity index) and step 7 (feedback)."""

    key: object
    label: str
    similarity_index: float = 0.0
    highest_match_score: float = 0.0
    flagged: bool = False
    is_exact_duplicate: bool = False
    severity: str = 'none'
    summary: str = ''
    marks_awarded: float = 0.0
    marks_deducted: float = 0.0


@dataclass
class PipelineOutput:
    pairs: List[PairResult] = field(default_factory=list)
    outcomes: List[DocumentOutcome] = field(default_factory=list)
    semantic_backend: str = ''
    duration_seconds: float = 0.0
    skipped: List[str] = field(default_factory=list)

    @property
    def flagged_count(self) -> int:
        return sum(1 for o in self.outcomes if o.flagged)


def _hash_text(normalized: str) -> str:
    """Stable digest of normalized text, for the step 3 exact-match check."""
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest() if normalized else ''


def severity_for(score: float) -> str:
    for cutoff, label in SEVERITY_BANDS:
        if score >= cutoff:
            return label
    return 'none'


class DetectionPipeline:
    """Runs steps 2 through 7 over a cohort of documents."""

    def __init__(
        self,
        method: str = 'combined',
        *,
        threshold: float = 40.0,
        max_marks: float = 100.0,
        semantic_enabled: bool = True,
        semantic_model: str = 'all-MiniLM-L6-v2',
    ) -> None:
        if method not in METHOD_LABELS:
            raise ValueError(f'Unknown detection method: {method!r}')
        self.method = method
        self.threshold = threshold
        self.max_marks = max_marks
        self.semantic_enabled = semantic_enabled
        self.semantic_model = semantic_model
        self._semantic_backend = ''

    # -- Step 2 -----------------------------------------------------------
    def prepare(self, documents: Iterable[DocumentInput]) -> List[PreparedDocument]:
        prepared: List[PreparedDocument] = []
        for document in documents:
            language = structural.detect_language(document.filename)
            processed = preprocessing.preprocess(
                document.text,
                remove_stopwords=False,
                is_code=language != 'unknown',
            )
            prepared.append(PreparedDocument(
                key=document.key,
                label=document.label,
                filename=document.filename,
                raw_text=document.text,
                processed=processed,
                language=language,
                content_hash=_hash_text(processed.normalized),
            ))
        return prepared

    # -- Steps 3 and 4 ----------------------------------------------------
    def compare_pair(
        self,
        a: PreparedDocument,
        b: PreparedDocument,
        *,
        idf: Optional[Dict[str, float]] = None,
    ) -> PairResult:
        result = PairResult(
            key_a=a.key, key_b=b.key, label_a=a.label, label_b=b.label
        )

        # Step 3: exact-match check. Cheap, and short-circuits everything else.
        if a.content_hash and a.content_hash == b.content_hash:
            result.is_exact_duplicate = True
            result.score = 100.0
            result.method_scores = {'exact_match': 100.0}
            result.matched_passages = [{
                'text': a.processed.normalized[:500],
                'token_count': a.processed.word_count,
                'note': 'Documents are identical after normalization.',
            }]
            return result

        tokens_a = a.processed.tokens
        tokens_b = b.processed.tokens
        if not tokens_a or not tokens_b:
            return result

        # Step 4: lexical algorithms.
        wanted = LEXICAL_METHODS if self.method in ('combined',) else {self.method}
        for name in sorted(wanted & LEXICAL_METHODS):
            result.method_scores[name] = round(self._run_lexical(name, tokens_a, tokens_b, idf), 2)

        # Step 4: structural code analysis.
        run_structural = self.method in ('combined', 'structural')
        if run_structural and (a.is_code or b.is_code):
            language = a.language if a.language != 'unknown' else b.language
            structural_result = structural.structural_similarity(
                a.raw_text, b.raw_text, language=language
            )
            result.structural_score = round(structural_result.score, 2)
            result.method_scores['structural'] = result.structural_score
        elif self.method == 'structural':
            # Asked for explicitly on prose: still useful as a normalized
            # token-stream comparison, just not AST-backed.
            structural_result = structural.structural_similarity(a.raw_text, b.raw_text)
            result.structural_score = round(structural_result.score, 2)
            result.method_scores['structural'] = result.structural_score

        # Step 4: semantic paraphrase detection.
        if self.semantic_enabled and self.method in ('combined', 'semantic'):
            semantic_result = semantic.semantic_similarity(
                a.raw_text, b.raw_text, model_name=self.semantic_model
            )
            self._semantic_backend = semantic_result.backend
            result.semantic_score = round(semantic_result.score, 2)
            result.method_scores['semantic'] = result.semantic_score
            result.paraphrase_matches = [
                {
                    'sentence_a': m.sentence_a[:400],
                    'sentence_b': m.sentence_b[:400],
                    'similarity': m.similarity,
                }
                for m in semantic_result.matches
            ]

        # Step 5: collapse to a single similarity score for this pair.
        result.score = round(self._aggregate(result.method_scores), 2)

        # Step 6 input: locate the verbatim passages behind the number.
        passages = highlighting.find_matched_passages(tokens_a, tokens_b)
        result.matched_passages = [
            {
                'text': p.preview(),
                'token_count': p.token_count,
                'start_token_a': p.start_token_a,
                'start_token_b': p.start_token_b,
            }
            for p in passages[:20]
        ]
        return result

    def _run_lexical(
        self,
        name: str,
        tokens_a: Sequence[str],
        tokens_b: Sequence[str],
        idf: Optional[Dict[str, float]],
    ) -> float:
        if name == 'jaccard':
            return algorithms.jaccard_similarity(tokens_a, tokens_b).score
        if name == 'lcs':
            return algorithms.lcs_similarity(tokens_a, tokens_b).score
        if name == 'levenshtein':
            return algorithms.levenshtein_similarity(tokens_a, tokens_b).score
        if name == 'cosine':
            return algorithms.cosine_similarity(tokens_a, tokens_b, idf=idf).score
        if name == 'rabin_karp':
            return algorithms.rabin_karp_similarity(tokens_a, tokens_b).score
        return 0.0

    def _aggregate(self, scores: Dict[str, float]) -> float:
        if not scores:
            return 0.0
        if self.method != 'combined':
            return next(iter(scores.values()))

        total_weight = 0.0
        accumulated = 0.0
        for name, value in scores.items():
            weight = COMBINED_WEIGHTS.get(name)
            if weight is None:
                continue
            accumulated += value * weight
            total_weight += weight
        if total_weight == 0:
            return max(scores.values())

        weighted = accumulated / total_weight
        # A single very strong signal should not be averaged away by weaker
        # ones: verbatim copying detected by fingerprinting is conclusive even
        # if vocabulary overlap happens to look ordinary.
        strongest = max(scores.values())
        return max(weighted, strongest * 0.75)

    # -- Steps 5 to 7 -----------------------------------------------------
    def run(self, documents: Iterable[DocumentInput]) -> PipelineOutput:
        started = time.monotonic()
        prepared = self.prepare(documents)

        output = PipelineOutput()
        usable = [d for d in prepared if not d.processed.is_empty()]
        for document in prepared:
            if document.processed.is_empty():
                output.skipped.append(document.label)

        if len(usable) < 2:
            output.duration_seconds = time.monotonic() - started
            output.outcomes = [
                DocumentOutcome(
                    key=d.key,
                    label=d.label,
                    marks_awarded=self.max_marks,
                    summary='Not enough comparable submissions to analyse.',
                )
                for d in prepared
            ]
            return output

        idf = algorithms.compute_idf([d.processed.tokens for d in usable])

        best: Dict[object, float] = {d.key: 0.0 for d in prepared}
        exact: Dict[object, bool] = {d.key: False for d in prepared}

        for i in range(len(usable)):
            for j in range(i + 1, len(usable)):
                pair = self.compare_pair(usable[i], usable[j], idf=idf)
                output.pairs.append(pair)
                for key in (pair.key_a, pair.key_b):
                    if pair.score > best[key]:
                        best[key] = pair.score
                    if pair.is_exact_duplicate:
                        exact[key] = True

        output.semantic_backend = self._semantic_backend or semantic.active_backend()
        output.outcomes = [
            self._build_outcome(d, best[d.key], exact[d.key]) for d in prepared
        ]
        output.pairs.sort(key=lambda p: p.score, reverse=True)
        output.duration_seconds = round(time.monotonic() - started, 3)
        return output

    def _build_outcome(
        self, document: PreparedDocument, best_score: float, is_exact: bool
    ) -> DocumentOutcome:
        """Step 5 similarity index plus step 7 marks and feedback."""
        similarity = round(best_score, 2)
        flagged = similarity >= self.threshold

        # Marks policy: nothing is deducted below the threshold. Above it, the
        # deduction scales with how far past the threshold the submission sits,
        # so a borderline case is not punished like a verbatim copy.
        if flagged and self.threshold < 100:
            overage = (similarity - self.threshold) / (100.0 - self.threshold)
            deducted = round(self.max_marks * overage, 2)
        else:
            deducted = 0.0
        awarded = round(max(0.0, self.max_marks - deducted), 2)

        severity = severity_for(similarity) if flagged else 'none'

        if document.processed.is_empty():
            summary = 'No readable text could be extracted from this submission.'
        elif is_exact:
            summary = 'Identical to another submission for this assignment.'
        elif flagged:
            summary = (
                f'{similarity:.1f}% similarity to another submission '
                f'(threshold {self.threshold:.0f}%). Manual review recommended.'
            )
        else:
            summary = f'{similarity:.1f}% similarity. Below the flagging threshold.'

        return DocumentOutcome(
            key=document.key,
            label=document.label,
            similarity_index=similarity,
            highest_match_score=similarity,
            flagged=flagged,
            is_exact_duplicate=is_exact,
            severity=severity,
            summary=summary,
            marks_awarded=awarded,
            marks_deducted=deducted,
        )


# --------------------------------------------------------------------------
# Django-facing entry point
# --------------------------------------------------------------------------

def run_detection_for_assignment(assignment, method='combined', user=None, threshold=None):
    """
    Run detection over every submission for ``assignment`` and persist a report.

    Returns the saved :class:`~academic.models.PlagiarismReport`.
    """
    from django.conf import settings
    from django.db import transaction

    from ..models import ActivityLog, Match, PlagiarismReport, Submission, SubmissionResult
    from ..services import ensure_extracted, find_archive_matches

    if threshold is None:
        threshold = getattr(settings, 'PLAGIARISM_SIMILARITY_THRESHOLD', 40.0)

    submissions = list(
        Submission.objects.filter(assignment=assignment).select_related('student')
    )

    report = PlagiarismReport.objects.create(
        assignment=assignment,
        method=method,
        threshold=threshold,
        generated_by=user,
        status='running',
        submissions_analyzed=len(submissions),
    )

    try:
        documents = []
        for submission in submissions:
            ensure_extracted(submission)
            documents.append(DocumentInput(
                key=submission.pk,
                label=submission.student.username,
                text=submission.extracted_text or '',
                filename=submission.submitted_file.name or '',
            ))

        pipeline = DetectionPipeline(
            method,
            threshold=threshold,
            max_marks=float(assignment.max_marks or 100),
            semantic_enabled=getattr(settings, 'SEMANTIC_ANALYSIS_ENABLED', True),
            semantic_model=getattr(settings, 'SEMANTIC_MODEL_NAME', 'all-MiniLM-L6-v2'),
        )
        output = pipeline.run(documents)

        by_id = {s.pk: s for s in submissions}

        # Step 3, second half: compare each submission against the archive of
        # work submitted for other assignments.
        archive_hits = {s.pk: find_archive_matches(s) for s in submissions}
        archive_total = sum(1 for hits in archive_hits.values() if hits)

        with transaction.atomic():
            for outcome in output.outcomes:
                submission = by_id[outcome.key]
                hits = archive_hits.get(submission.pk, [])
                SubmissionResult.objects.create(
                    report=report,
                    submission=submission,
                    similarity_index=outcome.similarity_index,
                    highest_match_score=outcome.highest_match_score,
                    flagged=outcome.flagged,
                    is_exact_duplicate=outcome.is_exact_duplicate,
                    marks_awarded=outcome.marks_awarded,
                    marks_deducted=outcome.marks_deducted,
                    severity=outcome.severity,
                    summary=outcome.summary,
                    archive_matches=hits,
                    matched_archive=bool(hits),
                )
                submission.plagiarism_percentage = outcome.similarity_index
                submission.marks_awarded = int(round(outcome.marks_awarded))
                submission.save(update_fields=['plagiarism_percentage', 'marks_awarded'])

            for pair in output.pairs:
                if pair.score <= 0:
                    continue
                Match.objects.create(
                    report=report,
                    submission_a=by_id[pair.key_a],
                    submission_b=by_id[pair.key_b],
                    score=pair.score,
                    method=method,
                    method_scores=pair.method_scores,
                    matched_passages=pair.matched_passages,
                    paraphrase_matches=pair.paraphrase_matches,
                    structural_score=pair.structural_score,
                    semantic_score=pair.semantic_score,
                    is_exact_duplicate=pair.is_exact_duplicate,
                )

            report.status = 'complete'
            report.pairs_compared = len(output.pairs)
            report.flagged_count = output.flagged_count
            report.duration_seconds = output.duration_seconds
            report.semantic_backend = output.semantic_backend
            report.archive_matches_found = archive_total
            if output.skipped:
                report.notes = (
                    'No readable text extracted from: ' + ', '.join(output.skipped)
                )
            report.save()

        ActivityLog.objects.create(
            user=user,
            action='detect',
            description=f'Ran {METHOD_LABELS.get(method, method)} on "{assignment.title}"',
            metadata={
                'report_id': report.pk,
                'assignment_id': assignment.pk,
                'submissions': len(submissions),
                'pairs': len(output.pairs),
                'flagged': output.flagged_count,
                'archive_matches': archive_total,
                'seconds': output.duration_seconds,
            },
        )
        return report

    except Exception as exc:
        logger.exception('Detection run failed for assignment %s', assignment.pk)
        report.status = 'failed'
        report.notes = str(exc)[:2000]
        report.save(update_fields=['status', 'notes'])
        ActivityLog.objects.create(
            user=user,
            action='error',
            description=f'Detection failed for "{assignment.title}": {exc}'[:500],
            metadata={'assignment_id': assignment.pk, 'report_id': report.pk},
        )
        raise

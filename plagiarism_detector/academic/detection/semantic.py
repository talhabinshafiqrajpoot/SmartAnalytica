"""
Semantic / NLP analysis (thesis section 3.2.4, "Natural Language Processing":
analysing semantics and syntax to identify paraphrasing).

Lexical algorithms cannot see paraphrasing. "The loop iterates over every
record" and "each record is visited by the loop" share almost no tokens, so
Jaccard, Rabin-Karp and cosine all report near zero. Comparing sentence
*meaning* is what closes that gap.

Three backends, tried in order, so the system degrades instead of failing:

1. ``sentence-transformers`` -- real contextual embeddings. Best quality.
2. ``scikit-learn`` LSA -- TF-IDF reduced by truncated SVD. Captures
   co-occurrence-based synonymy, needs no model download, no network.
3. Pure-Python character n-grams -- always available. Catches reworded text
   that keeps shared word stems.

The backend is chosen once, lazily, on first use. It is never loaded at import
time, because that would add several seconds to every ``manage.py`` command.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)

# Sentence pairs at or above this cosine similarity are reported as paraphrase
# matches.
#
# The three backends score on different scales, so a single shared threshold
# would be wrong for two of them. These were calibrated by measuring, at
# document level, the best match per sentence for three kinds of document pair:
# a paraphrased copy, an independently written document on the same topic
# (which must NOT be flagged), and an unrelated document.
#
#   backend                paraphrase           same-topic max   unrelated max
#   sentence-transformers  0.59 - 0.85          0.44             0.09
#   sklearn-lsa            0.13 - 1.00          0.39             0.00
#   ngram                  0.09 - 0.50          0.17             0.17
#
# Each threshold sits above the same-topic ceiling, because the expensive
# error here is accusing a student who merely wrote about the same subject.
# That costs recall on the two weaker backends, which is the right trade: they
# are fallbacks used only when sentence-transformers cannot load.
#
#   sentence-transformers  0.55 -> caught 5/5 paraphrases, 0 false positives
#   sklearn-lsa            0.40 -> caught 1/5 paraphrases, 0 false positives
#   ngram                  0.22 -> caught 4/5 paraphrases, 0 false positives
BACKEND_THRESHOLDS = {
    'sentence-transformers': 0.55,
    'sklearn-lsa': 0.40,
    'ngram': 0.22,
}
PARAPHRASE_THRESHOLD = 0.55
MIN_SENTENCE_WORDS = 6
MAX_SENTENCES = 400


@dataclass
class ParaphraseMatch:
    sentence_a: str
    sentence_b: str
    similarity: float
    index_a: int = 0
    index_b: int = 0


@dataclass
class SemanticResult:
    score: float = 0.0
    backend: str = 'none'
    matches: List[ParaphraseMatch] = field(default_factory=list)
    detail: Dict[str, object] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Backend selection
# --------------------------------------------------------------------------

_backend_lock = threading.Lock()
_backend_name: str | None = None
_model = None


def _load_backend(model_name: str = 'all-MiniLM-L6-v2') -> str:
    """Pick and initialize the best available backend. Thread-safe, runs once."""
    global _backend_name, _model
    if _backend_name is not None:
        return _backend_name

    with _backend_lock:
        if _backend_name is not None:
            return _backend_name

        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(model_name)
            _backend_name = 'sentence-transformers'
            logger.info('Semantic backend: sentence-transformers (%s)', model_name)
            return _backend_name
        except Exception as exc:  # model missing, no network, incompatible torch
            logger.warning('sentence-transformers unavailable (%s); trying LSA', exc)

        try:
            import sklearn  # noqa: F401
            _backend_name = 'sklearn-lsa'
            logger.info('Semantic backend: scikit-learn LSA')
            return _backend_name
        except Exception as exc:
            logger.warning('scikit-learn unavailable (%s); using n-gram fallback', exc)

        _backend_name = 'ngram'
        return _backend_name


def active_backend() -> str:
    """Name of the backend in use, without forcing initialization."""
    return _backend_name or 'not-initialized'


def reset_backend() -> None:
    """Drop the cached backend. Used by the test suite."""
    global _backend_name, _model
    with _backend_lock:
        _backend_name = None
        _model = None


# --------------------------------------------------------------------------
# Sentence handling
# --------------------------------------------------------------------------

_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+|\n+')
_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def meaningful_sentences(text: str, limit: int = MAX_SENTENCES) -> List[str]:
    """Split into sentences long enough to carry meaning worth comparing."""
    sentences = []
    for raw in _SENTENCE_RE.split(text or ''):
        candidate = raw.strip()
        if len(_WORD_RE.findall(candidate)) >= MIN_SENTENCE_WORDS:
            sentences.append(candidate)
        if len(sentences) >= limit:
            break
    return sentences


# --------------------------------------------------------------------------
# Similarity matrices, one per backend
# --------------------------------------------------------------------------

def _matrix_transformers(sents_a: Sequence[str], sents_b: Sequence[str]):
    import numpy as np
    embeddings_a = _model.encode(list(sents_a), convert_to_numpy=True, normalize_embeddings=True)
    embeddings_b = _model.encode(list(sents_b), convert_to_numpy=True, normalize_embeddings=True)
    return np.asarray(embeddings_a) @ np.asarray(embeddings_b).T


def _matrix_lsa(sents_a: Sequence[str], sents_b: Sequence[str]):
    import numpy as np
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.preprocessing import normalize as sk_normalize

    corpus = list(sents_a) + list(sents_b)
    vectorizer = TfidfVectorizer(sublinear_tf=True, stop_words='english')
    tfidf = vectorizer.fit_transform(corpus)

    # SVD needs at least two components and fewer than the feature count.
    components = max(2, min(100, min(tfidf.shape) - 1))
    if components >= 2 and min(tfidf.shape) > 2:
        reduced = TruncatedSVD(n_components=components, random_state=42).fit_transform(tfidf)
        reduced = sk_normalize(reduced)
    else:
        reduced = sk_normalize(tfidf.toarray())

    split = len(sents_a)
    return np.asarray(reduced[:split]) @ np.asarray(reduced[split:]).T


def _char_ngram_vector(text: str, n: int = 4) -> Counter:
    cleaned = ' '.join(_WORD_RE.findall(text.lower()))
    return Counter(cleaned[i:i + n] for i in range(max(0, len(cleaned) - n + 1)))


def _cosine(vec_a: Counter, vec_b: Counter) -> float:
    shared = set(vec_a) & set(vec_b)
    if not shared:
        return 0.0
    numerator = sum(vec_a[g] * vec_b[g] for g in shared)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    return numerator / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _matrix_ngram(sents_a: Sequence[str], sents_b: Sequence[str]) -> List[List[float]]:
    vectors_a = [_char_ngram_vector(s) for s in sents_a]
    vectors_b = [_char_ngram_vector(s) for s in sents_b]
    return [[_cosine(va, vb) for vb in vectors_b] for va in vectors_a]


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def semantic_similarity(
    text_a: str,
    text_b: str,
    *,
    model_name: str = 'all-MiniLM-L6-v2',
    threshold: float | None = None,
    max_matches: int = 25,
) -> SemanticResult:
    """
    Compare two documents by meaning.

    The score is the share of sentences in the shorter document that have a
    close counterpart in the other document. That framing matters: a copied
    paragraph inside a long original essay should surface as a set of matched
    passages, not be diluted to nothing by the document length.
    """
    sents_a = meaningful_sentences(text_a)
    sents_b = meaningful_sentences(text_b)
    if not sents_a or not sents_b:
        return SemanticResult(backend=active_backend(), detail={'note': 'not enough prose'})

    backend = _load_backend(model_name)
    # Each backend scores on its own scale; pick the calibrated cut-off unless
    # the caller has asked for a specific one.
    effective_threshold = (
        threshold if threshold is not None
        else BACKEND_THRESHOLDS.get(backend, PARAPHRASE_THRESHOLD)
    )
    try:
        if backend == 'sentence-transformers':
            matrix = _matrix_transformers(sents_a, sents_b)
        elif backend == 'sklearn-lsa':
            matrix = _matrix_lsa(sents_a, sents_b)
        else:
            matrix = _matrix_ngram(sents_a, sents_b)
    except Exception as exc:
        logger.warning('Semantic backend %s failed (%s); falling back to n-grams', backend, exc)
        backend = 'ngram'
        matrix = _matrix_ngram(sents_a, sents_b)
        if threshold is None:
            effective_threshold = BACKEND_THRESHOLDS['ngram']

    matches: List[ParaphraseMatch] = []
    matched_rows = 0
    for i, row in enumerate(matrix):
        row_values = list(row)
        if not row_values:
            continue
        best = max(row_values)
        j = row_values.index(best)
        if best >= effective_threshold:
            matched_rows += 1
            matches.append(ParaphraseMatch(
                sentence_a=sents_a[i],
                sentence_b=sents_b[j],
                similarity=round(float(best) * 100, 2),
                index_a=i,
                index_b=j,
            ))

    score = matched_rows / min(len(sents_a), len(sents_b)) * 100
    matches.sort(key=lambda m: m.similarity, reverse=True)

    return SemanticResult(
        score=min(100.0, score),
        backend=backend,
        matches=matches[:max_matches],
        detail={
            'sentences_a': len(sents_a),
            'sentences_b': len(sents_b),
            'matched_sentences': matched_rows,
            'threshold': effective_threshold,
        },
    )

"""
The five classical similarity algorithms (thesis section 3.2.4, "Initial Check"
and the lexical half of "Advanced Analysis").

Every function returns an :class:`AlgorithmResult` whose ``score`` is a
percentage in the range 0-100, so the pipeline can treat them interchangeably.

Changes from the original prototype implementation:

* ``lcs`` used a full ``(m+1) x (n+1)`` table. Two 5,000-word submissions would
  have allocated 25 million Python ints. It now runs on two rolling rows.
* ``levenshtein_distance`` compared raw characters, so a 20,000-character pair
  cost 400 million cell updates. It now compares tokens by default.
* ``rabin_karp`` searched for whole single words, which measures vocabulary
  overlap rather than copied passages, and re-hashed the text once per pattern.
  It now fingerprints word n-grams in a single pass, which is what the
  algorithm is actually useful for here.
* ``cosine_similarity`` weighted every term equally. It now supports IDF
  weighting so that boilerplate shared by the whole class counts for less.
"""

from __future__ import annotations

import math
import zlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

# Guard rails. Both LCS and Levenshtein are quadratic; without a cap a single
# oversized pair can stall a whole class-wide detection run.
MAX_QUADRATIC_TOKENS = 4000


@dataclass
class AlgorithmResult:
    """Outcome of comparing two documents with one algorithm."""

    method: str
    score: float                      # 0-100
    matched_units: int = 0            # tokens/n-grams judged shared
    differing_units: int = 0
    detail: Dict[str, object] = field(default_factory=dict)
    truncated: bool = False           # a quadratic input was capped

    def __post_init__(self) -> None:
        self.score = max(0.0, min(100.0, float(self.score)))


def _cap(tokens: Sequence[str]) -> Tuple[List[str], bool]:
    if len(tokens) > MAX_QUADRATIC_TOKENS:
        return list(tokens[:MAX_QUADRATIC_TOKENS]), True
    return list(tokens), False


# --------------------------------------------------------------------------
# 1. Jaccard similarity
# --------------------------------------------------------------------------

def jaccard_similarity(tokens_a: Sequence[str], tokens_b: Sequence[str]) -> AlgorithmResult:
    """Vocabulary overlap: |A n B| / |A u B|. Order and frequency insensitive."""
    set_a, set_b = set(tokens_a), set(tokens_b)
    if not set_a and not set_b:
        return AlgorithmResult('jaccard', 0.0, detail={'note': 'both documents empty'})

    intersection = set_a & set_b
    union = set_a | set_b
    score = (len(intersection) / len(union) * 100) if union else 0.0
    return AlgorithmResult(
        method='jaccard',
        score=score,
        matched_units=len(intersection),
        differing_units=len(union) - len(intersection),
        detail={'unique_a': len(set_a - set_b), 'unique_b': len(set_b - set_a)},
    )


# --------------------------------------------------------------------------
# 2. Longest Common Subsequence
# --------------------------------------------------------------------------

def lcs_similarity(tokens_a: Sequence[str], tokens_b: Sequence[str]) -> AlgorithmResult:
    """
    Longest common subsequence over word tokens.

    Catches reordering and padding: inserting filler sentences between copied
    paragraphs leaves the subsequence intact even though the token sets shift.
    Normalized by the shorter document so that padding a copy does not deflate
    the score.
    """
    a, trunc_a = _cap(tokens_a)
    b, trunc_b = _cap(tokens_b)
    m, n = len(a), len(b)
    if m == 0 or n == 0:
        return AlgorithmResult('lcs', 0.0, truncated=trunc_a or trunc_b)

    # Two rolling rows instead of the full table.
    previous = [0] * (n + 1)
    current = [0] * (n + 1)
    for i in range(1, m + 1):
        ai = a[i - 1]
        for j in range(1, n + 1):
            if ai == b[j - 1]:
                current[j] = previous[j - 1] + 1
            else:
                current[j] = previous[j] if previous[j] >= current[j - 1] else current[j - 1]
        previous, current = current, previous
        current[0] = 0

    length = previous[n]
    score = (length / min(m, n) * 100) if min(m, n) else 0.0
    return AlgorithmResult(
        method='lcs',
        score=score,
        matched_units=length,
        differing_units=max(m, n) - length,
        detail={'lcs_length': length, 'len_a': m, 'len_b': n},
        truncated=trunc_a or trunc_b,
    )


# --------------------------------------------------------------------------
# 3. Levenshtein distance
# --------------------------------------------------------------------------

def levenshtein_similarity(
    tokens_a: Sequence[str],
    tokens_b: Sequence[str],
    *,
    unit: str = 'token',
) -> AlgorithmResult:
    """
    Edit distance, expressed as a similarity percentage.

    Operates on tokens by default. Set ``unit='char'`` to reproduce the
    original character-level behaviour on short inputs.
    """
    if unit == 'char':
        seq_a: Sequence = ' '.join(tokens_a)
        seq_b: Sequence = ' '.join(tokens_b)
    else:
        seq_a, seq_b = tokens_a, tokens_b

    a, trunc_a = _cap(list(seq_a))
    b, trunc_b = _cap(list(seq_b))
    if len(a) < len(b):
        a, b = b, a

    if not a:
        return AlgorithmResult('levenshtein', 0.0, truncated=trunc_a or trunc_b)
    if not b:
        return AlgorithmResult(
            'levenshtein', 0.0, differing_units=len(a), truncated=trunc_a or trunc_b
        )

    previous = list(range(len(b) + 1))
    for i, item_a in enumerate(a):
        current = [i + 1]
        for j, item_b in enumerate(b):
            current.append(min(
                previous[j + 1] + 1,          # deletion
                current[j] + 1,               # insertion
                previous[j] + (item_a != item_b),  # substitution
            ))
        previous = current

    distance = previous[-1]
    total = max(len(a), len(b))
    score = (1 - distance / total) * 100 if total else 0.0
    return AlgorithmResult(
        method='levenshtein',
        score=score,
        matched_units=total - distance,
        differing_units=distance,
        detail={'distance': distance, 'unit': unit, 'total_units': total},
        truncated=trunc_a or trunc_b,
    )


# --------------------------------------------------------------------------
# 4. Cosine similarity
# --------------------------------------------------------------------------

def cosine_similarity(
    tokens_a: Sequence[str],
    tokens_b: Sequence[str],
    *,
    idf: Dict[str, float] | None = None,
) -> AlgorithmResult:
    """
    Cosine of the angle between two term-frequency vectors.

    When ``idf`` is supplied, terms are weighted by inverse document frequency,
    so shared boilerplate (assignment headers, imports, the question text)
    contributes far less than distinctive shared wording.
    """
    vec_a, vec_b = Counter(tokens_a), Counter(tokens_b)
    if not vec_a or not vec_b:
        return AlgorithmResult('cosine', 0.0)

    if idf:
        weighted_a = {t: c * idf.get(t, 1.0) for t, c in vec_a.items()}
        weighted_b = {t: c * idf.get(t, 1.0) for t, c in vec_b.items()}
    else:
        weighted_a = dict(vec_a)
        weighted_b = dict(vec_b)

    shared = set(weighted_a) & set(weighted_b)
    numerator = sum(weighted_a[t] * weighted_b[t] for t in shared)
    norm_a = math.sqrt(sum(v * v for v in weighted_a.values()))
    norm_b = math.sqrt(sum(v * v for v in weighted_b.values()))
    denominator = norm_a * norm_b

    if not denominator:
        return AlgorithmResult('cosine', 0.0, differing_units=len(vec_a) + len(vec_b))

    score = (numerator / denominator) * 100
    return AlgorithmResult(
        method='cosine',
        score=score,
        matched_units=len(shared),
        differing_units=len(set(weighted_a) ^ set(weighted_b)),
        detail={'idf_weighted': bool(idf), 'terms_a': len(vec_a), 'terms_b': len(vec_b)},
    )


def compute_idf(token_lists: Sequence[Sequence[str]]) -> Dict[str, float]:
    """Smoothed inverse document frequency across a cohort of submissions."""
    total = len(token_lists)
    if total == 0:
        return {}
    doc_freq: Counter = Counter()
    for tokens in token_lists:
        doc_freq.update(set(tokens))
    return {term: math.log((total + 1) / (df + 1)) + 1.0 for term, df in doc_freq.items()}


# --------------------------------------------------------------------------
# 5. Rabin-Karp
# --------------------------------------------------------------------------

_BASE = 256
_MOD = 1_000_000_007


def _stable_token_hash(token: str) -> int:
    """
    Deterministic token hash.

    ``hash()`` is salted per interpreter process (PYTHONHASHSEED), so using it
    would make fingerprints stored for the step 3 initial check meaningless the
    moment the server restarts. CRC32 is stable across processes and runs.
    """
    return zlib.crc32(token.encode('utf-8')) % _MOD


def _rolling_hashes(tokens: Sequence[str], k: int) -> List[int]:
    """Rabin-Karp rolling hashes for every contiguous k-gram of tokens."""
    if len(tokens) < k or k <= 0:
        return []
    # Hash each token to an integer once, then roll over the token stream.
    values = [_stable_token_hash(t) for t in tokens]
    high = pow(_BASE, k - 1, _MOD)

    hashes = []
    current = 0
    for i in range(k):
        current = (current * _BASE + values[i]) % _MOD
    hashes.append(current)
    for i in range(k, len(values)):
        current = ((current - values[i - k] * high) % _MOD * _BASE + values[i]) % _MOD
        hashes.append(current)
    return hashes


def rabin_karp_similarity(
    tokens_a: Sequence[str],
    tokens_b: Sequence[str],
    *,
    k: int = 5,
) -> AlgorithmResult:
    """
    Fingerprint overlap over contiguous k-token passages.

    This is the substring-matching use of Rabin-Karp, and it is the algorithm
    most directly aimed at the thing teachers care about: verbatim copied
    passages. A score of 30 means roughly 30% of this submission's 5-word
    windows also appear in the other submission.
    """
    a, b = list(tokens_a), list(tokens_b)
    if len(a) < k or len(b) < k:
        # Fall back to a shorter window rather than reporting a false zero for
        # legitimately short submissions.
        k = max(1, min(len(a), len(b)))
        if k == 0:
            return AlgorithmResult('rabin_karp', 0.0)

    hashes_a = _rolling_hashes(a, k)
    hashes_b = set(_rolling_hashes(b, k))
    if not hashes_a or not hashes_b:
        return AlgorithmResult('rabin_karp', 0.0)

    matched_windows = sum(1 for h in hashes_a if h in hashes_b)
    score = matched_windows / len(hashes_a) * 100
    return AlgorithmResult(
        method='rabin_karp',
        score=score,
        matched_units=matched_windows,
        differing_units=len(hashes_a) - matched_windows,
        detail={'k': k, 'windows_a': len(hashes_a), 'windows_b': len(hashes_b)},
    )

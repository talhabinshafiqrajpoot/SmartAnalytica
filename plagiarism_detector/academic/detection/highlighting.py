"""
Step 6 support: locating the passages that actually match.

The documentation requires reports to contain "highlighted sections of the
document that are flagged for potential plagiarism" and the "sources from
which the matching content was identified". A single percentage does not let
a teacher defend an accusation; the matched text does.

This module finds maximal verbatim passages shared by two documents by
extending seed k-gram matches greedily, then merging overlaps.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

SEED_LENGTH = 5          # token window used to seed a match
MIN_PASSAGE_TOKENS = 8   # passages shorter than this are noise
MAX_PASSAGES = 50


@dataclass
class MatchedPassage:
    """A contiguous run of tokens present in both documents."""

    text: str
    token_count: int
    start_token_a: int
    start_token_b: int

    def preview(self, limit: int = 300) -> str:
        return self.text if len(self.text) <= limit else self.text[:limit].rstrip() + '...'


def _index_kgrams(tokens: Sequence[str], k: int) -> Dict[int, List[int]]:
    """Map each k-gram hash to every position where it starts."""
    index: Dict[int, List[int]] = {}
    for i in range(len(tokens) - k + 1):
        key = zlib.crc32(' '.join(tokens[i:i + k]).encode('utf-8'))
        index.setdefault(key, []).append(i)
    return index


def find_matched_passages(
    tokens_a: Sequence[str],
    tokens_b: Sequence[str],
    *,
    seed: int = SEED_LENGTH,
    min_tokens: int = MIN_PASSAGE_TOKENS,
    max_passages: int = MAX_PASSAGES,
) -> List[MatchedPassage]:
    """
    Find maximal shared token runs.

    Seeds on exact k-gram hits, then extends each seed forwards as far as the
    tokens keep agreeing. Positions already covered by an accepted passage are
    skipped, so one long copied paragraph yields one passage rather than
    dozens of overlapping fragments.
    """
    if len(tokens_a) < seed or len(tokens_b) < seed:
        return []

    index_b = _index_kgrams(tokens_b, seed)
    passages: List[MatchedPassage] = []
    consumed_until = -1

    i = 0
    while i <= len(tokens_a) - seed:
        if i <= consumed_until:
            i += 1
            continue

        key = zlib.crc32(' '.join(tokens_a[i:i + seed]).encode('utf-8'))
        candidates = index_b.get(key)
        if not candidates:
            i += 1
            continue

        best_length = 0
        best_j = candidates[0]
        for j in candidates:
            # Confirm the seed really matches; CRC32 collisions are rare but real.
            if list(tokens_a[i:i + seed]) != list(tokens_b[j:j + seed]):
                continue
            length = seed
            while (
                i + length < len(tokens_a)
                and j + length < len(tokens_b)
                and tokens_a[i + length] == tokens_b[j + length]
            ):
                length += 1
            if length > best_length:
                best_length = length
                best_j = j

        if best_length >= min_tokens:
            passages.append(MatchedPassage(
                text=' '.join(tokens_a[i:i + best_length]),
                token_count=best_length,
                start_token_a=i,
                start_token_b=best_j,
            ))
            consumed_until = i + best_length - 1
            i += best_length
            if len(passages) >= max_passages:
                break
        else:
            i += 1

    passages.sort(key=lambda p: p.token_count, reverse=True)
    return passages


def coverage_percentage(passages: Sequence[MatchedPassage], total_tokens: int) -> float:
    """Share of a document covered by matched passages."""
    if not total_tokens:
        return 0.0
    covered = sum(p.token_count for p in passages)
    return min(100.0, covered / total_tokens * 100)

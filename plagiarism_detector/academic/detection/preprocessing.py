"""
Step 2 of the detection workflow: preprocessing.

The documentation specifies three preprocessing duties (section 3.2.3):
removing unnecessary formatting and special characters, tokenizing the text
into manageable units, and normalizing to a consistent format.

The original prototype did all of this with ``text.lower().split()``, which
meant that ``foo();`` and ``foo()`` were treated as different tokens and any
difference in indentation changed the result. This module replaces that.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Sequence, Set

# Tokens carrying almost no discriminating signal. Keeping them inflates
# similarity scores for every pair of documents equally, which compresses the
# range that actually separates copied work from independent work.
STOPWORDS: Set[str] = {
    'a', 'about', 'an', 'and', 'are', 'as', 'at', 'be', 'been', 'but', 'by',
    'can', 'for', 'from', 'had', 'has', 'have', 'if', 'in', 'into', 'is', 'it',
    'its', 'of', 'on', 'or', 'that', 'the', 'their', 'then', 'there', 'these',
    'this', 'to', 'was', 'were', 'when', 'which', 'will', 'with', 'would',
}

_WORD_RE = re.compile(r"[A-Za-z0-9_']+")
_WHITESPACE_RE = re.compile(r'\s+')
_CODE_COMMENT_RE = re.compile(
    r'//[^\n]*|#[^\n]*|/\*.*?\*/|"""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\'',
    re.DOTALL,
)


@dataclass
class PreprocessedDocument:
    """A document after step 2, carrying every representation later steps need."""

    raw: str
    normalized: str
    tokens: List[str] = field(default_factory=list)
    token_set: Set[str] = field(default_factory=set)
    sentences: List[str] = field(default_factory=list)
    # Character offset in ``raw`` for each token, so step 6 can highlight the
    # matching passage in the document the user actually uploaded.
    offsets: List[int] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len(self.tokens)

    def is_empty(self) -> bool:
        return not self.tokens


def strip_formatting(text: str) -> str:
    """Remove control characters, normalize Unicode and collapse whitespace."""
    text = unicodedata.normalize('NFKC', text)
    text = ''.join(ch for ch in text if ch == '\n' or not unicodedata.category(ch).startswith('C'))
    # Smart quotes and dashes copied out of Word would otherwise read as
    # different characters to the same punctuation typed in a plain editor.
    for fancy, plain in (('‘', "'"), ('’', "'"), ('“', '"'),
                         ('”', '"'), ('–', '-'), ('—', '-')):
        text = text.replace(fancy, plain)
    return text


def strip_code_comments(text: str) -> str:
    """Drop comments so that renaming them cannot disguise copied code."""
    return _CODE_COMMENT_RE.sub(' ', text)


def normalize(text: str) -> str:
    """Lowercase and collapse runs of whitespace to single spaces."""
    return _WHITESPACE_RE.sub(' ', text.lower()).strip()


def tokenize(text: str) -> List[str]:
    """Split into word tokens, discarding punctuation."""
    return _WORD_RE.findall(text)


def split_sentences(text: str) -> List[str]:
    """Split on sentence terminators and newlines, for passage highlighting."""
    parts = re.split(r'(?<=[.!?])\s+|\n+', text)
    return [p.strip() for p in parts if p.strip()]


def preprocess(
    text: str,
    *,
    remove_stopwords: bool = False,
    is_code: bool = False,
) -> PreprocessedDocument:
    """Run the full step 2 pipeline over one document."""
    raw = text or ''
    cleaned = strip_formatting(raw)
    if is_code:
        cleaned = strip_code_comments(cleaned)

    normalized = normalize(cleaned)

    tokens: List[str] = []
    offsets: List[int] = []
    lowered_raw = raw.lower()
    cursor = 0
    for match in _WORD_RE.finditer(normalized):
        token = match.group(0)
        if remove_stopwords and token in STOPWORDS:
            continue
        tokens.append(token)
        # Best-effort mapping back into the raw text for highlighting. If the
        # token cannot be located we fall back to the previous offset rather
        # than failing the whole run.
        found = lowered_raw.find(token, cursor)
        if found == -1:
            found = offsets[-1] if offsets else 0
        else:
            cursor = found + len(token)
        offsets.append(found)

    return PreprocessedDocument(
        raw=raw,
        normalized=normalized,
        tokens=tokens,
        token_set=set(tokens),
        sentences=split_sentences(cleaned),
        offsets=offsets,
    )


def ngrams(tokens: Sequence[str], n: int = 3) -> List[str]:
    """Contiguous n-grams, joined with spaces. Used for fingerprinting."""
    if n <= 0 or len(tokens) < n:
        return []
    return [' '.join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]

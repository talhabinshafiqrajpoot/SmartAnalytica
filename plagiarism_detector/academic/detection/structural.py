"""
Structural code analysis (thesis section 3.2.4, "Code Analysis").

The literature review identifies why text matching is inadequate for code:
renaming variables, reordering functions and reformatting all defeat it while
leaving the program identical. MOSS and JPlag solve this by comparing token
streams rather than text, and by selecting fingerprints with *winnowing* so
that the comparison is robust to insertions.

This module implements that approach:

* Python sources are parsed with the standard library ``ast`` module and
  serialized into a structure-only token stream. Identifiers and literals are
  replaced by their kind, so ``total = 0`` and ``sum_value = 0`` produce the
  same tokens.
* Other languages fall back to a lexical tokenizer that normalizes identifiers
  and literals the same way, which still defeats renaming.
* Both streams are fingerprinted with winnowing (Schleimer, Wilkerson & Aiken,
  2003), and similarity is the proportion of shared fingerprints.
"""

from __future__ import annotations

import ast
import re
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Set, Tuple

# Winnowing parameters. k is the noise threshold: matches shorter than k
# tokens are ignored. w is the window size; the guarantee is that any match of
# at least k + w - 1 tokens is detected.
DEFAULT_K = 12
DEFAULT_WINDOW = 8

CODE_EXTENSIONS = {
    '.py': 'python', '.java': 'java', '.c': 'c', '.cpp': 'cpp', '.cc': 'cpp',
    '.h': 'c', '.hpp': 'cpp', '.js': 'javascript', '.ts': 'typescript',
    '.cs': 'csharp', '.go': 'go', '.rb': 'ruby', '.php': 'php',
}

# Reserved words kept verbatim, because they carry structure. Everything else
# that looks like an identifier is collapsed to a single placeholder.
_KEYWORDS = {
    'if', 'else', 'elif', 'for', 'while', 'do', 'switch', 'case', 'break',
    'continue', 'return', 'def', 'class', 'function', 'func', 'try', 'catch',
    'except', 'finally', 'throw', 'raise', 'new', 'delete', 'import', 'from',
    'package', 'public', 'private', 'protected', 'static', 'final', 'const',
    'let', 'var', 'int', 'float', 'double', 'char', 'void', 'bool', 'boolean',
    'string', 'struct', 'enum', 'interface', 'extends', 'implements', 'this',
    'self', 'super', 'null', 'none', 'true', 'false', 'and', 'or', 'not',
    'in', 'is', 'with', 'as', 'lambda', 'yield', 'await', 'async', 'elseif',
}

_TOKEN_RE = re.compile(r"[A-Za-z_]\w*|\d+\.?\d*|[{}()\[\];,.]|[+\-*/%=<>!&|^~]+")
_COMMENT_RE = re.compile(r'//[^\n]*|#[^\n]*|/\*.*?\*/', re.DOTALL)
_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')


@dataclass
class StructuralResult:
    score: float = 0.0
    shared_fingerprints: int = 0
    total_fingerprints: int = 0
    language: str = 'unknown'
    parsed_as_ast: bool = False
    detail: Dict[str, object] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Token stream construction
# --------------------------------------------------------------------------

class _AstTokenizer(ast.NodeVisitor):
    """Serialize a Python AST into a structure-only token stream."""

    def __init__(self) -> None:
        self.tokens: List[str] = []

    def generic_visit(self, node: ast.AST) -> None:
        self.tokens.append(type(node).__name__)
        super().generic_visit(node)

    # Identifiers and literals are deliberately reduced to their kind: this is
    # what makes the comparison immune to renaming.
    def visit_Name(self, node: ast.Name) -> None:
        self.tokens.append('NAME')

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.tokens.append('ATTR')
        self.visit(node.value)

    def visit_arg(self, node: ast.arg) -> None:
        self.tokens.append('ARG')

    def visit_Constant(self, node: ast.Constant) -> None:
        self.tokens.append(f'CONST_{type(node.value).__name__}')

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.tokens.append('FUNCTIONDEF')
        for child in node.body:
            self.visit(child)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.tokens.append('CLASSDEF')
        for child in node.body:
            self.visit(child)


def python_ast_tokens(source: str) -> List[str] | None:
    """Structure-only tokens via the AST, or None if the source will not parse."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    tokenizer = _AstTokenizer()
    tokenizer.visit(tree)
    return tokenizer.tokens


def lexical_tokens(source: str) -> List[str]:
    """
    Language-agnostic normalized token stream.

    Comments and string contents are removed, identifiers become ``ID`` and
    numbers become ``NUM``, so only keywords, operators and program shape
    remain.
    """
    source = _COMMENT_RE.sub(' ', source)
    source = _STRING_RE.sub(' STR ', source)

    tokens: List[str] = []
    for match in _TOKEN_RE.finditer(source):
        token = match.group(0)
        lowered = token.lower()
        if lowered in _KEYWORDS:
            tokens.append(lowered)
        elif token[0].isdigit():
            tokens.append('NUM')
        elif token[0].isalpha() or token[0] == '_':
            tokens.append('ID')
        else:
            tokens.append(token)
    return tokens


def build_token_stream(source: str, language: str = 'unknown') -> Tuple[List[str], bool]:
    """Return (tokens, parsed_as_ast)."""
    if language == 'python':
        tokens = python_ast_tokens(source)
        if tokens is not None:
            return tokens, True
    return lexical_tokens(source), False


# --------------------------------------------------------------------------
# Winnowing
# --------------------------------------------------------------------------

def _kgram_hashes(tokens: Sequence[str], k: int) -> List[int]:
    if len(tokens) < k:
        return []
    return [
        zlib.crc32(' '.join(tokens[i:i + k]).encode('utf-8'))
        for i in range(len(tokens) - k + 1)
    ]


def winnow(tokens: Sequence[str], k: int = DEFAULT_K, window: int = DEFAULT_WINDOW) -> Set[int]:
    """
    Select a representative subset of k-gram hashes.

    In each sliding window of ``window`` hashes, keep the minimum, breaking
    ties by choosing the rightmost occurrence. This bounds the density of
    fingerprints while guaranteeing that any sufficiently long shared passage
    contributes at least one shared fingerprint.
    """
    hashes = _kgram_hashes(tokens, k)
    if not hashes:
        return set()
    if len(hashes) < window:
        return {min(hashes)}

    fingerprints: Set[int] = set()
    previous_index = -1
    for start in range(len(hashes) - window + 1):
        chunk = hashes[start:start + window]
        smallest = min(chunk)
        # Rightmost occurrence of the minimum inside this window.
        offset = len(chunk) - 1 - chunk[::-1].index(smallest)
        index = start + offset
        if index != previous_index:
            fingerprints.add(smallest)
            previous_index = index
    return fingerprints


def structural_similarity(
    source_a: str,
    source_b: str,
    *,
    language: str = 'unknown',
    k: int = DEFAULT_K,
    window: int = DEFAULT_WINDOW,
) -> StructuralResult:
    """Compare two code submissions by program structure rather than text."""
    tokens_a, ast_a = build_token_stream(source_a, language)
    tokens_b, ast_b = build_token_stream(source_b, language)

    if not tokens_a or not tokens_b:
        return StructuralResult(language=language, detail={'note': 'no tokens extracted'})

    # Very short files never fill a winnowing window; compare k-grams directly.
    if min(len(tokens_a), len(tokens_b)) < k + window:
        k = max(3, min(len(tokens_a), len(tokens_b)) // 2)
        prints_a = set(_kgram_hashes(tokens_a, k))
        prints_b = set(_kgram_hashes(tokens_b, k))
    else:
        prints_a = winnow(tokens_a, k, window)
        prints_b = winnow(tokens_b, k, window)

    if not prints_a or not prints_b:
        return StructuralResult(language=language, detail={'note': 'no fingerprints'})

    shared = prints_a & prints_b
    # Normalized by the smaller fingerprint set: a copied file padded with
    # extra original work should still register as substantially copied.
    score = len(shared) / min(len(prints_a), len(prints_b)) * 100

    return StructuralResult(
        score=min(100.0, score),
        shared_fingerprints=len(shared),
        total_fingerprints=min(len(prints_a), len(prints_b)),
        language=language,
        parsed_as_ast=ast_a and ast_b,
        detail={
            'tokens_a': len(tokens_a),
            'tokens_b': len(tokens_b),
            'fingerprints_a': len(prints_a),
            'fingerprints_b': len(prints_b),
            'k': k,
            'window': window,
        },
    )


def detect_language(filename: str) -> str:
    """Map a filename to a language key, or 'unknown'."""
    lowered = (filename or '').lower()
    for extension, language in CODE_EXTENSIONS.items():
        if lowered.endswith(extension):
            return language
    return 'unknown'


def is_code_file(filename: str) -> bool:
    return detect_language(filename) != 'unknown'

"""
Step 1-2 support: getting readable text out of whatever students upload.

The documentation requires support for .zip, .rar, .py and .java submissions
(section 2.1.2), and assignment attachments in the existing system are PDFs.
The original code passed the uploaded file straight to the word-splitting
algorithms, so a PDF was compared as binary bytes and produced meaningless
scores. Nothing in the project extracted text at all.
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from dataclasses import dataclass, field
from typing import List, Tuple

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {
    '.txt', '.md', '.rst', '.csv', '.json', '.xml', '.html', '.htm',
    '.py', '.java', '.c', '.cpp', '.cc', '.h', '.hpp', '.js', '.ts',
    '.cs', '.go', '.rb', '.php', '.sql', '.sh', '.r', '.m', '.kt', '.swift',
}
ARCHIVE_EXTENSIONS = {'.zip', '.rar'}

# An archive bomb or a stray dataset should not exhaust memory.
MAX_ARCHIVE_MEMBERS = 200
MAX_TOTAL_CHARS = 2_000_000


@dataclass
class ExtractedDocument:
    text: str = ''
    source_format: str = 'unknown'
    members: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


def _decode(data: bytes) -> str:
    for encoding in ('utf-8', 'utf-16', 'latin-1'):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode('utf-8', errors='replace')


def extract_pdf(data: bytes) -> Tuple[str, List[str]]:
    warnings: List[str] = []
    try:
        from pypdf import PdfReader
    except ImportError:
        return '', ['pypdf is not installed; cannot read PDF submissions']

    try:
        reader = PdfReader(io.BytesIO(data))
        if getattr(reader, 'is_encrypted', False):
            try:
                reader.decrypt('')
            except Exception:
                return '', ['PDF is password protected']
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or '')
            except Exception as exc:
                warnings.append(f'page skipped: {exc}')
        text = '\n'.join(pages)
        if not text.strip():
            warnings.append(
                'No text layer found. This looks like a scanned PDF; '
                'it would need OCR before it can be analysed.'
            )
        return text, warnings
    except Exception as exc:
        return '', [f'could not read PDF: {exc}']


def extract_docx(data: bytes) -> Tuple[str, List[str]]:
    try:
        import docx
    except ImportError:
        return '', ['python-docx is not installed; cannot read .docx submissions']
    try:
        document = docx.Document(io.BytesIO(data))
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.extend(cell.text for cell in row.cells)
        return '\n'.join(parts), []
    except Exception as exc:
        return '', [f'could not read .docx: {exc}']


def _extract_archive_members(names, read_member, label: str) -> Tuple[str, List[str], List[str]]:
    chunks: List[str] = []
    members: List[str] = []
    warnings: List[str] = []
    total = 0

    for name in names:
        if len(members) >= MAX_ARCHIVE_MEMBERS:
            warnings.append(f'archive truncated at {MAX_ARCHIVE_MEMBERS} files')
            break
        base = os.path.basename(name)
        if not base or base.startswith('.') or '__MACOSX' in name:
            continue
        extension = os.path.splitext(base)[1].lower()
        if extension not in TEXT_EXTENSIONS and extension not in {'.pdf', '.docx'}:
            continue
        try:
            data = read_member(name)
        except Exception as exc:
            warnings.append(f'{base}: {exc}')
            continue

        if extension == '.pdf':
            text, member_warnings = extract_pdf(data)
            warnings.extend(f'{base}: {w}' for w in member_warnings)
        elif extension == '.docx':
            text, member_warnings = extract_docx(data)
            warnings.extend(f'{base}: {w}' for w in member_warnings)
        else:
            text = _decode(data)

        if text.strip():
            members.append(name)
            chunks.append(f'/* ===== {name} ===== */\n{text}')
            total += len(text)
            if total > MAX_TOTAL_CHARS:
                warnings.append('archive content truncated at the size limit')
                break

    if not members:
        warnings.append(f'no readable source or text files found in the {label}')
    return '\n\n'.join(chunks), members, warnings


def extract_zip(data: bytes) -> Tuple[str, List[str], List[str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:
        return '', [], [f'could not open zip: {exc}']
    names = [i.filename for i in archive.infolist() if not i.is_dir()]
    return _extract_archive_members(names, archive.read, 'zip')


def extract_rar(data: bytes) -> Tuple[str, List[str], List[str]]:
    try:
        import rarfile
    except ImportError:
        return '', [], ['rarfile is not installed; cannot read .rar submissions']
    try:
        archive = rarfile.RarFile(io.BytesIO(data))
        names = [i.filename for i in archive.infolist() if not i.isdir()]
        return _extract_archive_members(names, archive.read, 'rar')
    except Exception as exc:
        # rarfile needs an external `unar`/`bsdtar` binary for most archives.
        return '', [], [
            f'could not open rar: {exc}. '
            'Reading .rar needs the "unar" tool installed on the server.'
        ]


def extract_text(filename: str, data: bytes) -> ExtractedDocument:
    """Extract readable text from an uploaded file, dispatching on extension."""
    extension = os.path.splitext(filename or '')[1].lower()

    if extension == '.pdf':
        text, warnings = extract_pdf(data)
        return ExtractedDocument(text, 'pdf', [], warnings)

    if extension == '.docx':
        text, warnings = extract_docx(data)
        return ExtractedDocument(text, 'docx', [], warnings)

    if extension == '.zip':
        text, members, warnings = extract_zip(data)
        return ExtractedDocument(text, 'zip', members, warnings)

    if extension == '.rar':
        text, members, warnings = extract_rar(data)
        return ExtractedDocument(text, 'rar', members, warnings)

    if extension in TEXT_EXTENSIONS:
        return ExtractedDocument(_decode(data), extension.lstrip('.') or 'text')

    # Unknown extension: try decoding, and only accept it if it looks textual.
    decoded = _decode(data)
    printable = sum(1 for ch in decoded[:4000] if ch.isprintable() or ch.isspace())
    if decoded[:4000] and printable / max(1, len(decoded[:4000])) > 0.9:
        return ExtractedDocument(decoded, 'text', [], ['unrecognized extension, read as plain text'])

    return ExtractedDocument('', 'binary', [], [
        f'Unsupported file type "{extension or "none"}". '
        'Upload a PDF, DOCX, source file, or a zip archive.'
    ])


def extract_from_path(path: str) -> ExtractedDocument:
    """Read a file from disk and extract its text."""
    try:
        with open(path, 'rb') as handle:
            data = handle.read()
    except OSError as exc:
        return ExtractedDocument('', 'missing', [], [f'could not read file: {exc}'])
    return extract_text(os.path.basename(path), data)

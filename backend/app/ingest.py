"""File conversion for uploaded dataset sources.

Mirrors the offline `scripts/convert_dataset.py` flow but exposes it as a
library so the upload endpoint can convert in-process. Each public converter
returns markdown as a string; the caller decides where to write it.

Supported types (v1): .docx, .xlsx, .pdf, .txt, .md
"""
from __future__ import annotations

import io
import re
from pathlib import Path


# Lazy imports keep the dev environment lean and let tests stub modules out.
def _docx():
    import docx  # python-docx
    return docx


def _openpyxl():
    import openpyxl
    return openpyxl


def _pypdf():
    import pypdf
    return pypdf


def docx_to_md(data: bytes) -> str:
    doc = _docx().Document(io.BytesIO(data))
    parts: list[str] = []
    for p in doc.paragraphs:
        style = p.style.name if p.style else ""
        text = p.text
        if not text.strip():
            parts.append("")
            continue
        if style.startswith("Heading 1"):
            parts.append(f"# {text}")
        elif style.startswith("Heading 2"):
            parts.append(f"## {text}")
        elif style.startswith("Heading 3"):
            parts.append(f"### {text}")
        elif style.startswith("Heading"):
            parts.append(f"#### {text}")
        else:
            parts.append(text)
    for ti, table in enumerate(doc.tables):
        parts.append(f"\n[TABLE {ti}]")
        for row in table.rows:
            cells = [c.text.replace("\n", " ").strip() for c in row.cells]
            parts.append("| " + " | ".join(cells) + " |")
    return "\n".join(parts)


def xlsx_to_md(data: bytes, max_rows: int = 30) -> str:
    wb = _openpyxl().load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    parts: list[str] = []
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        parts.append(f"## {sheet}")
        rows = list(ws.iter_rows(values_only=True))
        truncated = False
        if len(rows) > max_rows:
            rows = rows[:max_rows]
            truncated = True
        for row in rows:
            cells = ["" if v is None else str(v).strip() for v in row]
            parts.append("| " + " | ".join(cells) + " |")
        if truncated:
            parts.append(f"\n_…(sheet truncated to {max_rows} rows)_")
        parts.append("")
    return "\n".join(parts)


def pdf_to_md(data: bytes) -> str:
    reader = _pypdf().PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            parts.append(f"## Page {i + 1}\n\n{text.strip()}")
    return "\n\n".join(parts)


def txt_to_md(data: bytes) -> str:
    # plain text already qualifies as markdown for our LLM pipeline
    return data.decode("utf-8", errors="replace")


# Routing -----------------------------------------------------------------

_SUFFIX_TO_CONVERTER = {
    ".docx": docx_to_md,
    ".xlsx": xlsx_to_md,
    ".pdf": pdf_to_md,
    ".txt": txt_to_md,
    ".md": txt_to_md,  # already markdown, passthrough
}

SUPPORTED_SUFFIXES = tuple(_SUFFIX_TO_CONVERTER.keys())


class UnsupportedFileType(ValueError):
    pass


def convert(filename: str, data: bytes) -> str:
    """Dispatch by extension. Raises UnsupportedFileType for unknown formats."""
    suffix = Path(filename).suffix.lower()
    fn = _SUFFIX_TO_CONVERTER.get(suffix)
    if fn is None:
        raise UnsupportedFileType(
            f"Unsupported file type {suffix!r}. Supported: {', '.join(SUPPORTED_SUFFIXES)}"
        )
    return fn(data)


# Naming helpers ----------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def slugify(name: str) -> str:
    """Normalize a filename stem into a safe markdown filename component.

    Lowercases, replaces non-alphanumerics with underscores, collapses runs,
    strips leading/trailing underscores. Empty input becomes 'untitled'.
    """
    s = _SLUG_RE.sub("_", name.strip().lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "untitled"


def md_filename(original_filename: str) -> str:
    """Pick the markdown filename for a converted upload. Preserves the stem."""
    stem = Path(original_filename).stem
    return f"{slugify(stem)}.md"

"""Read files no deterministic parser can — images and scanned PDFs.

`app.ingest.convert` handles everything with a real parser. What's left splits
in two:

  * Files a MODEL can read — an image, or a PDF whose pages are scans with no
    text layer. Claude reads both natively: an image rides an `image` content
    block, a PDF rides a `document` block (the API rasterizes its pages), so
    neither needs an OCR engine or a PDF rasterizer as a dependency.

  * Files a model genuinely CANNOT read — legacy binary Office formats
    (.doc/.xls/.ppt) and ODF. Handing Claude those bytes yields nothing; they
    need real parsers and stay on the placeholder path for now.

Reads go through `graph.gateway.llm_call` rather than a direct client call so
they are bound to the customer's own Claude key and land in the decision log
and cost telemetry like every other model call.

Everything here fails OPEN: a read that errors, is refused, or comes back empty
returns None, and the caller leaves the existing placeholder in place. A file
we couldn't read is a file we retry later — never a failed upload.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

#: Extensions Claude can read as images → the media_type the API expects.
#: Mirrors the design agent's vision allowlist plus GIF, which the API accepts.
IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

PDF_MEDIA_TYPE = "application/pdf"

#: Per-file ceiling for a model read. The API caps a whole request at 32 MB
#: base64-encoded, and base64 inflates by ~4/3 — 20 MB of raw bytes lands
#: around 27 MB encoded, leaving room for the prompt. It also happens to match
#: the upload routes' own per-file cap, so nothing accepted upstream is
#: rejected here for size alone.
MAX_READ_BYTES = 20 * 1024 * 1024

#: A PDF whose extracted text is shorter than this is treated as scanned —
#: pypdf found no real text layer, just stray artifacts. Deliberately small:
#: the cost of a needless vision read is one call, the cost of missing a
#: scanned document is that its content never reaches the knowledge graph.
SCANNED_PDF_TEXT_FLOOR = 200

_SYSTEM = (
    "You transcribe documents. Return the document's content as clean markdown: "
    "the text as written, tables as markdown tables, and headings preserved. "
    "Describe charts, diagrams and photographs in enough detail that someone "
    "who cannot see them understands what they show, including any numbers or "
    "labels they carry.\n\n"
    "Rules:\n"
    "1. NEVER GUESS. Transcribe only what is actually present. If part of the "
    "document is illegible, write [illegible] rather than inventing content.\n"
    "2. Return the content itself — no preamble, no commentary about the "
    "document, no 'Here is the transcription'.\n"
    "3. If the file has no readable content at all, return exactly: NO_CONTENT"
)

#: The model's way of saying "there is nothing here" — mapped to None so an
#: empty scan doesn't get stored as if it were content.
_EMPTY_SENTINEL = "NO_CONTENT"

_PROMPT_VERSION = "file-read-v1"


def attachment_for(filename: str, data: bytes) -> dict | None:
    """The content block for `data`, or None if no model can read this file.

    Images become `image` blocks; PDFs become `document` blocks (the API reads
    scanned pages natively). Anything else — including legacy .doc/.xls/.ppt —
    returns None.
    """
    if not data or len(data) > MAX_READ_BYTES:
        return None
    suffix = Path(filename).suffix.lower()
    media_type = IMAGE_MEDIA_TYPES.get(suffix)
    if media_type:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(data).decode(),
            },
        }
    if suffix == ".pdf":
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": PDF_MEDIA_TYPE,
                "data": base64.standard_b64encode(data).decode(),
            },
        }
    return None


def is_readable_by_model(filename: str, data: bytes) -> bool:
    """True iff `read_file` could plausibly extract something from this file."""
    return attachment_for(filename, data) is not None


def needs_model_read(filename: str, data: bytes, extracted_text: str | None) -> bool:
    """Should this stored file be (re-)read by the model?

    Two cases: the deterministic converter produced a placeholder (an image, a
    format with no parser), or it produced almost nothing for a PDF — the
    signature of a scan with no text layer, which `pypdf` returns as "".
    """
    from app.ingest import is_unparsed_stub

    if not is_readable_by_model(filename, data):
        return False
    if is_unparsed_stub(extracted_text):
        return True
    if Path(filename).suffix.lower() == ".pdf":
        return len((extracted_text or "").strip()) < SCANNED_PDF_TEXT_FLOOR
    return not (extracted_text or "").strip()


def read_file(company_id: str, filename: str, data: bytes) -> str | None:
    """Transcribe a file with the model. None when it couldn't be read.

    Never raises: a transport failure, a refusal, an oversized file, or an
    empty result all return None so the caller keeps the placeholder and the
    file stays eligible for a later retry.
    """
    attachment = attachment_for(filename, data)
    if attachment is None:
        return None

    from app.graph.gateway import llm_call

    try:
        result = llm_call(
            enterprise_id=company_id,
            agent="file-reader",
            purpose="read_unparseable_upload",
            system=_SYSTEM,
            input=(
                f"Transcribe the contents of {Path(filename).name!r} "
                "following the rules above."
            ),
            prompt_version=_PROMPT_VERSION,
            attachments=[attachment],
            max_tokens=8000,
        )
    except Exception:  # noqa: BLE001 — reading a file must never break ingest
        logger.exception("file-read: model read failed for %s", filename)
        return None

    text = (result.output or "").strip()
    if not text or text == _EMPTY_SENTINEL:
        return None
    return text

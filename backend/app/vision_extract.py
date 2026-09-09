"""Read a file that has no extractable text by LOOKING at it.

`app.ingest.convert` is a text extractor. Handed a screen-capture PDF — pages
that are one big image with no text layer — `pypdf` returns nothing, and the
upload routes turned that into "could not extract any text from the file". That
sentence is accurate about the bytes and useless to the person who attached it:
they attached a screenshot precisely because the picture is the content.

Reported as five files attached to one chat message, of which the two
screen-capture PDFs came back unreadable — and, because one failure aborted the
whole send, took the other three and the question with them.

So the routes fall through to here when extraction comes back empty. Claude is
multimodal; the file goes to the model as a `document` (PDF) or `image` block
and comes back as markdown, which is the same shape the text path produces. The
caller cannot tell which way a file was read, and nothing downstream needs to.

WHY THIS IS NOT IN `ingest.convert`. That function is synchronous, LLM-free and
called per-file by knowledge-graph ingestion, connector syncs and corpus
imports; an LLM call inside it would fire thousands of times on a single sync.
Here it is bounded to the interactive paths, one call per file, only for files
that would otherwise have contributed nothing at all.

Sources: platform.claude.com PDF support (base64 `document` block, 32 MB
request ceiling, 100 pages under a 1M context) and Vision (JPEG/PNG/GIF/WebP,
10 MB per image on the Claude API direct).
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

#: What the model can actually look at. A format absent from this map is not a
#: vision candidate and the caller's original "cannot read this" stands — the
#: point of the fallback is to read pictures, not to guess at every binary.
_MEDIA_TYPE_BY_EXT: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

#: Raw-byte ceiling for one vision read.
#:
#: The API's limits are on the ENCODED request — 32 MB overall, 10 MB per
#: image — and base64 inflates by 4/3, so 15 MB of file is ~20 MB on the wire
#: and leaves room for the prompt. Deliberately below the routes' own 25 MB
#: upload ceiling: a file between the two is still accepted and still gets its
#: honest "cannot read this", it just does not get an API rejection first.
MAX_VISION_BYTES = 15 * 1024 * 1024

#: Cap on what comes back. Matches the composer's own per-attachment clamp, so
#: a transcription cannot become the largest thing in the prompt.
MAX_VISION_CHARS = 50_000

_SYSTEM = (
    "You transcribe documents. You are given one file — a scan, a screenshot, "
    "or a PDF whose pages are images — and you return its content as markdown.\n\n"
    "Rules:\n"
    "- Transcribe what is actually there. Every heading, paragraph, label, "
    "number, table cell and caption, in reading order.\n"
    "- Render tables as markdown tables and keep headings as headings. The "
    "result is read by another model as evidence, so structure carries meaning.\n"
    "- Describe a chart, diagram or photo in one line of its own, including "
    "any axis labels and values you can read.\n"
    "- Do NOT summarize, interpret, editorialize, or add anything the file "
    "does not contain. A transcription that invents a plausible number is "
    "worse than no transcription at all.\n"
    "- Output the markdown only. No preamble, no 'Here is the transcription'.\n"
    "- If the file genuinely has no readable content, output nothing at all."
)


def vision_block(filename: str, data: bytes) -> dict | None:
    """The content block for this file, or None if it is not a candidate.

    None covers both "not a format the model can look at" and "too large to
    send" — the caller treats them the same way, because from the reader's
    side both mean the file was not read.
    """
    media_type = _MEDIA_TYPE_BY_EXT.get(Path(filename or "").suffix.lower())
    if media_type is None or not data or len(data) > MAX_VISION_BYTES:
        return None
    return {
        # A PDF is a `document`, an image is an `image` — the API takes them as
        # different block types even though both end up rendered as pictures.
        "type": "document" if media_type == "application/pdf" else "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
    }


def read_with_vision(*, filename: str, data: bytes, enterprise_id: str) -> str:
    """Transcribe `data` to markdown, or return "" if it cannot be read.

    NEVER RAISES. This is a fallback behind an extraction that already failed,
    so every way it can go wrong — an unsupported format, an oversized file, an
    encrypted PDF the API rejects, a timeout, no API key configured — has the
    same correct outcome: the caller's original refusal stands. A vision read
    that turns a "we could not read this" into a 500 would be strictly worse
    than not trying.
    """
    block = vision_block(filename, data)
    if block is None:
        return ""

    # Imported here rather than at module scope: this module is imported by
    # route modules at startup, and the gateway pulls in the whole LLM stack.
    from app.graph.gateway import llm_call
    from app.llm import FAST_MODEL

    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent="ingest",
            purpose="vision_extract",
            system=_SYSTEM,
            input=f"Transcribe this file: {filename}",
            prompt_version="vision-extract-v1",
            # Transcription, not reasoning. Haiku reads a page as well as the
            # larger models do and this fires on files that until now
            # contributed nothing, so the marginal cost should stay marginal.
            model=FAST_MODEL,
            input_blocks=[block],
            max_tokens=8000,
            # Faithfulness over fluency: the output is evidence.
            temperature=0,
            # A scan can be many pages, and many pages is a long generation —
            # stream it rather than race the default read timeout.
            long_output=True,
        )
    except Exception as exc:  # noqa: BLE001 — a fallback must not become a 500
        logger.info("vision_extract failed file=%s err=%s", filename, exc)
        return ""

    text = (result.output or "").strip()
    if not text:
        return ""
    logger.info("vision_extract read file=%s chars=%d", filename, len(text))
    return text[:MAX_VISION_CHARS]


#: How many members of ONE archive may fall through to a vision read.
#:
#: `datasets.expand_zip_members` admits up to 500 members, and a fallback that
#: fires per member would turn one upload of a photo album into 500 model
#: calls. Ten is enough for the case this exists for — someone zips the
#: screenshots for a question — and bounded enough that a pathological archive
#: cannot become a bill. Members past the budget keep the old behaviour: they
#: are skipped, and the readable files around them still import.
MAX_VISION_PER_ARCHIVE = 10


class Extracted(NamedTuple):
    """What came out, and whether the model had to look at the file for it.

    `used_vision` exists for the archive budget: a caller expanding a zip needs
    to charge only the members that actually spent a model call, so a zip of
    ordinary documents never touches the budget. Single-file callers ignore it.
    """

    text: str
    used_vision: bool


async def extract_text(
    *, filename: str, data: bytes, enterprise_id: str, allow_vision: bool = True
) -> Extracted:
    """The text of one uploaded file — read, or looked at. "" if neither works.

    The single entry point for "turn these bytes into something a model can
    read", shared by the chat-attachment extractor and the project document
    upload so the two surfaces cannot drift on what counts as readable.

    Three ways `convert` says nothing came out, all meaning the same thing to
    the person who uploaded the file:

      * it RAISES — a corrupt container (pypdf on a truncated PDF, a KeyError
        on a DOCX missing its content-types part);
      * it returns empty — an image-only PDF has a page count and no text;
      * it returns the UNPARSED STUB, a non-empty placeholder for a type it has
        no parser for, which a plain empty-check would wave through as content.

    Only then does the file go to the model. `allow_vision=False` is for
    callers that have spent their budget (see MAX_VISION_PER_ARCHIVE), and
    keeps the pure-text behaviour exactly as it was.
    """
    import asyncio

    from app.ingest import convert, is_unparsed_stub

    try:
        # `convert` is blocking (pdf/docx/BeautifulSoup parsing) — off the loop.
        markdown = await asyncio.to_thread(convert, filename, data)
    except Exception as exc:  # noqa: BLE001 — an unreadable file, not a bug
        logger.info("extract_text convert failed file=%s err=%s", filename, exc)
        markdown = ""

    if markdown.strip() and not is_unparsed_stub(markdown):
        return Extracted(markdown, False)

    if not allow_vision or vision_block(filename, data) is None:
        # Not a candidate, or the caller is out of budget — the original
        # "we could not read this" stands, exactly as it did before.
        return Extracted("", False)
    # Also blocking: `llm_call` is synchronous all the way down to the SDK.
    text = await asyncio.to_thread(
        read_with_vision, filename=filename, data=data, enterprise_id=enterprise_id
    )
    # `used_vision` is TRUE even when the read came back empty. The budget
    # bounds calls made, not calls that worked — charging only successes would
    # let an archive of unreadable scans make unlimited ones.
    return Extracted(text, True)

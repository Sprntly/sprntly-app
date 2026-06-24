"""Roadmap Doc — the company's uploaded roadmap (config/priorities entity).

A PM uploads their current roadmap (spreadsheet, deck, or doc) during the
onboarding strategy step. Sprntly stores the original file + the extracted
text and feeds it into weekly-brief composition as a HIGH-WEIGHT priorities
signal, so findings are ranked/justified against the stated roadmap (e.g.
"aligns with your Q3 self-serve onboarding goal"). It also renders read-only
as the `roadmapdoc` artifact view.

ONE roadmap per company (the latest upload wins) — same versioned-config-entity
shape as kpi_tree.py / business_context.py, but stored in its own table because
the payload (extracted text + the original bytes) is bulky and distinct from the
small jsonb config columns on `companies`.

Storage: `roadmap_doc` table, keyed by company_id (UNIQUE) — see
supabase/migrations/20260623120000_roadmap_doc.sql. The original file bytes are
base64-encoded into `raw_b64` so the artifact view can offer the source; the
LLM/brief only ever read `extracted_text` (markdown produced by app.ingest.convert,
the SAME converter the dataset upload path uses).
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel

from app.db.client import require_client
from app.ingest import convert

logger = logging.getLogger(__name__)

# Cap the extracted text that reaches the brief prompt. A roadmap is a priorities
# anchor, not the corpus — a few thousand chars is plenty to phrase findings
# against, and keeps it from crowding out the candidate evidence.
ROADMAP_PROMPT_MAX_CHARS = 4000


class RoadmapDoc(BaseModel):
    """A company's stored roadmap upload."""

    filename: str
    content_type: Optional[str] = None
    extracted_text: str = ""
    # base64 of the original upload bytes (so the artifact view can offer the
    # source download). Optional on read — older/larger rows may omit it.
    raw_b64: Optional[str] = None
    uploaded_at: Optional[str] = None
    version: int = 1

    def render_for_prompt(self, *, max_chars: int = ROADMAP_PROMPT_MAX_CHARS) -> str:
        """Compact text block the weekly-brief skill reads as the company's
        stated priorities. Empty string when there is no usable text."""
        text = (self.extracted_text or "").strip()
        if not text:
            return ""
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "\n…(roadmap truncated)"
        return text


def _extract_text(filename: str, data: bytes) -> str:
    """Convert the upload to markdown via the shared ingest converter.

    Reuses app.ingest.convert — the SAME extraction the dataset/corpus upload
    path uses (rich converters for .docx/.xlsx/.csv/.pdf/.txt/.md; anything else
    falls back to best-effort decode). Never raises: a conversion failure
    degrades to empty text so the upload itself still stores (the original bytes
    are kept regardless)."""
    try:
        return convert(filename, data) or ""
    except Exception:  # noqa: BLE001 — extraction is best-effort
        logger.warning("roadmap extraction failed for %s", filename, exc_info=True)
        return ""


def save_roadmap_doc(
    company_id: str,
    *,
    filename: str,
    data: bytes,
    content_type: Optional[str] = None,
) -> RoadmapDoc:
    """Store (or replace) the company's roadmap upload. Extracts the text and
    bumps the version past whatever is currently stored. Latest upload wins."""
    extracted = _extract_text(filename, data)
    current = load_roadmap_doc(company_id)
    version = (current.version + 1) if current else 1
    uploaded_at = datetime.now(timezone.utc).isoformat()
    row = {
        "company_id": company_id,
        "filename": filename,
        "content_type": content_type,
        "extracted_text": extracted,
        "raw_b64": base64.b64encode(data).decode("ascii"),
        "uploaded_at": uploaded_at,
        "version": version,
    }
    # One row per company: upsert on the UNIQUE company_id so a re-upload
    # replaces the prior roadmap rather than accumulating rows.
    require_client().table("roadmap_doc").upsert(
        row, on_conflict="company_id"
    ).execute()
    return RoadmapDoc(
        filename=filename,
        content_type=content_type,
        extracted_text=extracted,
        raw_b64=row["raw_b64"],
        uploaded_at=uploaded_at,
        version=version,
    )


def load_roadmap_doc(company_id: str) -> Optional[RoadmapDoc]:
    """Read the company's roadmap; None if none uploaded / invalid."""
    r = (
        require_client().table("roadmap_doc")
        .select("filename,content_type,extracted_text,raw_b64,uploaded_at,version")
        .eq("company_id", company_id)
        .execute()
    )
    if not r.data:
        return None
    raw = r.data[0]
    if not raw.get("filename"):
        return None
    try:
        return RoadmapDoc.model_validate(raw)
    except Exception:  # noqa: BLE001 — tolerate hand-edited rows
        logger.warning("invalid roadmap_doc for %s; ignoring", company_id, exc_info=True)
        return None

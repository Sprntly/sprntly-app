"""Roadmap Doc — the company's uploaded roadmap (config/priorities entity).

A PM uploads their current roadmap (spreadsheet, deck, or doc) during the
onboarding strategy step. Sprntly stores the original file + the extracted
text and feeds it into top-insights composition as a HIGH-WEIGHT priorities
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
        """Compact text block the top-insights skill reads as the company's
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


def _same_roadmap(current: "RoadmapDoc", extracted: str, raw_b64: str) -> bool:
    """Is this upload the same ROADMAP as the one already stored?

    Compares the extracted text, deliberately — not the bytes. That is the exact
    key the KG ingest ledger hashes (kg_ingest.roadmap.content_sha), so the
    user-facing version and the graph can never disagree about whether the
    roadmap changed: re-exporting the same deck to a fresh PDF changes every
    byte but neither the roadmap nor the ledger.

    Fallback: when NEITHER side has usable text (scanned-image PDF, unparseable
    binary), text equality is vacuously true and would freeze the version across
    genuinely different files — so compare the raw bytes instead. Ingest no-ops
    on both regardless (it refuses to extract from empty text / stub), so the
    two stay in agreement either way.

    Note this is intentionally NOT filename-sensitive: renaming a file does not
    make it a new roadmap. The stored filename still refreshes to the new one;
    only the version is held.
    """
    stored = (current.extracted_text or "").strip()
    incoming = (extracted or "").strip()
    if not stored and not incoming:
        # Nothing readable on either side — text equality proves nothing.
        return bool(current.raw_b64) and current.raw_b64 == raw_b64
    return stored == incoming


def save_roadmap_doc(
    company_id: str,
    *,
    filename: str,
    data: bytes,
    content_type: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> RoadmapDoc:
    """Store (or replace) the roadmap upload. Latest upload wins.

    The version bumps only when the roadmap's CONTENT changed. Re-uploading the
    same file is a metadata refresh at the same version, not a new version.

    Why content and not "an upload happened": the version is a user-facing claim
    that the roadmap changed, and it has to agree with the KG ingest ledger,
    which dedups on a hash of exactly this extracted text
    (kg_ingest.roadmap.content_sha). Bumping on every POST made the two disagree
    — a PM re-uploading the same deck watched "version 2" become "version 3"
    while ingest correctly no-opped and wrote no new kg_source row, so the label
    counted uploads while the graph counted roadmaps.

    Identity is the EXTRACTED TEXT, again to match the ledger: the same deck
    re-exported to a new PDF has different bytes but the same roadmap, and the
    ledger already treats it as unchanged. The one exception is when neither
    side yields text (scanned images, unparseable binaries) — text equality is
    vacuous there, so we fall back to byte identity.

    Metadata (filename, content_type, raw bytes, uploaded_at) always tracks the
    most recent upload even at an unchanged version, so the artifact view and
    Settings show what the PM actually last sent. Only `version` is frozen.

    Multi-workspace: one roadmap per WORKSPACE (unique(workspace_id) since
    20260716124000). Content comparison is against THIS workspace's row, so the
    same file uploaded to a second workspace is that workspace's v1.

    Routes pass the active workspace; the legacy no-workspace path (older
    callers/tests) replaces by company_id manually."""
    extracted = _extract_text(filename, data)
    current = load_roadmap_doc(company_id, workspace_id=workspace_id)
    raw_b64 = base64.b64encode(data).decode("ascii")
    version = (
        current.version
        if current is not None and _same_roadmap(current, extracted, raw_b64)
        else (current.version + 1) if current else 1
    )
    uploaded_at = datetime.now(timezone.utc).isoformat()
    row = {
        "company_id": company_id,
        "filename": filename,
        "content_type": content_type,
        "extracted_text": extracted,
        "raw_b64": raw_b64,
        "uploaded_at": uploaded_at,
        "version": version,
    }
    client = require_client()
    if workspace_id:
        row["workspace_id"] = workspace_id
        # One row per workspace: upsert on the UNIQUE workspace_id so a
        # re-upload replaces the prior roadmap rather than accumulating rows.
        client.table("roadmap_doc").upsert(row, on_conflict="workspace_id").execute()
    elif current:
        client.table("roadmap_doc").update(row).eq("company_id", company_id).execute()
    else:
        client.table("roadmap_doc").insert(row).execute()
    return RoadmapDoc(
        filename=filename,
        content_type=content_type,
        extracted_text=extracted,
        raw_b64=row["raw_b64"],
        uploaded_at=uploaded_at,
        version=version,
    )


def load_roadmap_doc(
    company_id: str, *, workspace_id: Optional[str] = None
) -> Optional[RoadmapDoc]:
    """Read the roadmap; None if none uploaded / invalid. With a workspace_id
    the read is workspace-exact; without one (legacy callers e.g. the
    synthesis agent, which is company-keyed) it returns the company's first
    row — in practice the default workspace's roadmap."""
    q = (
        require_client().table("roadmap_doc")
        .select("filename,content_type,extracted_text,raw_b64,uploaded_at,version")
        .eq("company_id", company_id)
    )
    if workspace_id:
        q = q.eq("workspace_id", workspace_id)
    r = q.execute()
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

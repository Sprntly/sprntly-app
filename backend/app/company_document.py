"""Company Documents — the strategy/context files a PM uploads during onboarding.

The onboarding strategy step (design scene onbstrat — the FINAL step) offers a
small grid of typed upload cards so the PM can hand the agents the documents that
shape priorities:

  - ceo_memo          — CEO memo / priorities for the half (leadership direction)
  - team_priorities   — what the team has committed to or is weighing
  - research          — user studies, market or competitive research
  - company_strategy  — OKRs, annual plan, strategy decks

This is the GENERALIZED sibling of roadmap_doc.py / company_template.py: same
"store the original bytes + the extracted text" shape, but a SINGLE table with a
`doc_type` discriminator instead of one table per kind. MANY documents per company
(each upload is its own row, listed and — like company_template — scoped to the
company), so a PM can drop several files under each card.

Storage: `company_document` table, keyed by `id`, scoped to `company_id`, with a
`doc_type` column constrained to the set above — see
supabase/migrations/20260626120000_company_document.sql (and the SQLite mirror in
backend/tests/conftest.py). The original file bytes are base64-encoded into
`raw_b64` for a future source-download affordance; the LLM only ever reads
`extracted_text` (markdown produced by app.ingest.convert, the SAME converter the
dataset / roadmap / template upload paths use).

NOTE (follow-up): these docs are STORED only. Unlike roadmap_doc (which feeds the
weekly brief) and company_template (which feeds prd-author), nothing here is wired
into synthesis yet. Feeding company_document text into agent context is a
deliberate follow-up.
"""
from __future__ import annotations

import base64
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel

from app.db.client import require_client
from app.ingest import convert

logger = logging.getLogger(__name__)

# The typed upload cards the onboarding strategy step offers. Each card uploads
# under one of these doc_types; the migration's CHECK constraint mirrors this set.
DOC_TYPES: tuple[str, ...] = (
    "ceo_memo",
    "team_priorities",
    "research",
    "company_strategy",
)


def is_valid_doc_type(doc_type: str) -> bool:
    return doc_type in DOC_TYPES


class CompanyDocument(BaseModel):
    """A company's stored strategy/context document upload."""

    id: str
    doc_type: str
    filename: str
    content_type: Optional[str] = None
    extracted_text: str = ""
    # base64 of the original upload bytes (so a future view can offer the source
    # download). Optional on read.
    raw_b64: Optional[str] = None
    uploaded_at: Optional[str] = None


def _extract_text(filename: str, data: bytes) -> str:
    """Convert the upload to markdown via the shared ingest converter.

    Reuses app.ingest.convert — the SAME extraction the dataset / roadmap /
    template upload paths use. Never raises: a conversion failure degrades to
    empty text so the upload itself still stores (the original bytes are kept
    regardless)."""
    try:
        return convert(filename, data) or ""
    except Exception:  # noqa: BLE001 — extraction is best-effort
        logger.warning("company_document extraction failed for %s", filename, exc_info=True)
        return ""


def save_company_document(
    company_id: str,
    *,
    doc_type: str,
    filename: str,
    data: bytes,
    content_type: Optional[str] = None,
) -> CompanyDocument:
    """Store a new strategy/context document for the company (many allowed).

    Each call inserts a new row — like company_template, unlike roadmap_doc's
    one-per-company upsert — so a company can accumulate several documents per
    doc_type."""
    extracted = _extract_text(filename, data)
    doc_id = str(uuid.uuid4())
    uploaded_at = datetime.now(timezone.utc).isoformat()
    row = {
        "id": doc_id,
        "company_id": company_id,
        "doc_type": doc_type,
        "filename": filename,
        "content_type": content_type,
        "extracted_text": extracted,
        "raw_b64": base64.b64encode(data).decode("ascii"),
        "uploaded_at": uploaded_at,
    }
    require_client().table("company_document").insert(row).execute()
    return CompanyDocument(
        id=doc_id,
        doc_type=doc_type,
        filename=filename,
        content_type=content_type,
        extracted_text=extracted,
        raw_b64=row["raw_b64"],
        uploaded_at=uploaded_at,
    )


def list_company_documents(
    company_id: str, *, doc_type: Optional[str] = None
) -> list[CompanyDocument]:
    """All strategy/context documents for the company, newest first. Optionally
    filtered by `doc_type`. Empty list when none / on read error."""
    q = (
        require_client().table("company_document")
        .select("id,doc_type,filename,content_type,extracted_text,uploaded_at")
        .eq("company_id", company_id)
    )
    if doc_type is not None:
        q = q.eq("doc_type", doc_type)
    try:
        r = q.execute()
    except Exception:  # noqa: BLE001 — fail open
        # The `company_document` table may not exist yet on a given environment
        # (its migration deploys independently of this code). A missing table —
        # or any transient read error — must degrade to "no documents" rather
        # than raising, so the onboarding UI never breaks when the table is
        # absent or empty.
        logger.warning(
            "company_document read failed for %s; treating as no documents",
            company_id,
            exc_info=True,
        )
        return []
    rows = r.data or []
    out: list[CompanyDocument] = []
    for raw in rows:
        if not raw.get("filename"):
            continue
        try:
            out.append(CompanyDocument.model_validate(raw))
        except Exception:  # noqa: BLE001 — tolerate hand-edited rows
            logger.warning(
                "invalid company_document for %s; ignoring", company_id, exc_info=True
            )
    out.sort(key=lambda d: (d.uploaded_at or ""), reverse=True)
    return out

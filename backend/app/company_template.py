"""Company Templates — the company's uploaded gold-standard PRD examples
("what good looks like").

A PM uploads one or more exemplar PRDs that represent the team's gold standard
for format/voice. Sprntly stores each original file + its extracted text and
feeds the extracted text into prd-author composition as FORMAT/STYLE EXEMPLARS,
so generated PRDs match the company's structure & voice.

This is the SIBLING of roadmap_doc.py (see that module), with two differences:
  - MANY templates per company (unlike roadmap's one-per-company): each upload
    is its own row, listed and individually deletable.
  - It shapes prd-author OUTPUT FORMAT (structure/voice), not brief priorities.

Storage: `company_template` table, keyed by `id`, scoped to `company_id` — see
supabase/migrations/20260623130000_company_template.sql. The original file bytes
are base64-encoded into `raw_b64` for a future source-download affordance; the
LLM only ever reads `extracted_text` (markdown produced by app.ingest.convert,
the SAME converter the dataset/roadmap upload paths use).
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

# Per-template cap on the text that reaches the prd-author prompt. An exemplar is
# a FORMAT/VOICE reference, not the corpus — a few thousand chars captures the
# structure & tone without crowding out the insight/evidence the PRD is built on.
TEMPLATE_PROMPT_MAX_CHARS = 4000

# How many templates' text we are willing to fold into a single prd-author
# compose call. A handful of exemplars is plenty to convey house style; more
# would just bloat the prompt.
MAX_TEMPLATES_IN_PROMPT = 3

DEFAULT_TYPE = "prd"


class CompanyTemplate(BaseModel):
    """A company's stored gold-standard example upload."""

    id: str
    label: Optional[str] = None
    type: str = DEFAULT_TYPE
    filename: str
    content_type: Optional[str] = None
    extracted_text: str = ""
    # base64 of the original upload bytes (so a future view can offer the source
    # download). Optional on read.
    raw_b64: Optional[str] = None
    uploaded_at: Optional[str] = None

    def render_for_prompt(self, *, max_chars: int = TEMPLATE_PROMPT_MAX_CHARS) -> str:
        """Compact text block prd-author reads as a format/voice exemplar.
        Empty string when there is no usable text."""
        text = (self.extracted_text or "").strip()
        if not text:
            return ""
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "\n…(exemplar truncated)"
        return text


def _extract_text(filename: str, data: bytes) -> str:
    """Convert the upload to markdown via the shared ingest converter.

    Reuses app.ingest.convert — the SAME extraction the dataset/roadmap upload
    paths use. Never raises: a conversion failure degrades to empty text so the
    upload itself still stores (the original bytes are kept regardless)."""
    try:
        return convert(filename, data) or ""
    except Exception:  # noqa: BLE001 — extraction is best-effort
        logger.warning("template extraction failed for %s", filename, exc_info=True)
        return ""


def save_company_template(
    company_id: str,
    *,
    filename: str,
    data: bytes,
    label: Optional[str] = None,
    type: str = DEFAULT_TYPE,
    content_type: Optional[str] = None,
) -> CompanyTemplate:
    """Store a new gold-standard template for the company (many allowed).

    Each call inserts a new row — unlike roadmap_doc's one-per-company upsert —
    so a company can accumulate several exemplars."""
    extracted = _extract_text(filename, data)
    template_id = str(uuid.uuid4())
    uploaded_at = datetime.now(timezone.utc).isoformat()
    row = {
        "id": template_id,
        "company_id": company_id,
        "label": label,
        "type": type or DEFAULT_TYPE,
        "filename": filename,
        "content_type": content_type,
        "extracted_text": extracted,
        "raw_b64": base64.b64encode(data).decode("ascii"),
        "uploaded_at": uploaded_at,
    }
    require_client().table("company_template").insert(row).execute()
    return CompanyTemplate(
        id=template_id,
        label=label,
        type=type or DEFAULT_TYPE,
        filename=filename,
        content_type=content_type,
        extracted_text=extracted,
        raw_b64=row["raw_b64"],
        uploaded_at=uploaded_at,
    )


def list_company_templates(
    company_id: str, *, type: Optional[str] = None
) -> list[CompanyTemplate]:
    """All gold-standard templates for the company, newest first. Optionally
    filtered by `type` (e.g. 'prd'). Empty list when none / on read error."""
    q = (
        require_client().table("company_template")
        .select("id,label,type,filename,content_type,extracted_text,uploaded_at")
        .eq("company_id", company_id)
    )
    if type is not None:
        q = q.eq("type", type)
    try:
        r = q.execute()
    except Exception:  # noqa: BLE001 — fail open
        # The `company_template` table may not exist yet on a given environment
        # (its migration deploys independently of this code). A missing table —
        # or any transient read error — must degrade to "no templates" rather
        # than raising, so PRD generation and the templates UI never break when
        # the table is absent or empty.
        logger.warning(
            "company_template read failed for %s; treating as no templates",
            company_id,
            exc_info=True,
        )
        return []
    rows = r.data or []
    out: list[CompanyTemplate] = []
    for raw in rows:
        if not raw.get("filename"):
            continue
        try:
            out.append(CompanyTemplate.model_validate(raw))
        except Exception:  # noqa: BLE001 — tolerate hand-edited rows
            logger.warning(
                "invalid company_template for %s; ignoring", company_id, exc_info=True
            )
    out.sort(key=lambda t: (t.uploaded_at or ""), reverse=True)
    return out


def delete_company_template(company_id: str, template_id: str) -> bool:
    """Delete one template owned by the company. Returns True if a row was
    removed. The company_id scope guards against deleting another tenant's row.

    Existence is checked first (a scoped select) so the result is reliable
    regardless of whether the backend echoes deleted rows — PostgREST's delete
    `data` representation is not depended on."""
    client = require_client()
    existing = (
        client.table("company_template")
        .select("id")
        .eq("company_id", company_id)
        .eq("id", template_id)
        .execute()
    )
    if not existing.data:
        return False
    client.table("company_template").delete().eq("company_id", company_id).eq(
        "id", template_id
    ).execute()
    return True


def render_templates_for_prompt(
    company_id: str,
    *,
    type: str = DEFAULT_TYPE,
    max_templates: int = MAX_TEMPLATES_IN_PROMPT,
) -> str:
    """The FORMAT/STYLE EXEMPLARS block prd-author reads, or "" when the company
    has uploaded no usable templates (a clean no-op — additive context only).

    Folds up to `max_templates` exemplars' extracted text into one labelled
    block instructing the model to MATCH the structure & voice, never to copy
    content or fabricate. Returns "" when there is nothing usable."""
    templates = list_company_templates(company_id, type=type)
    if not templates:
        return ""
    rendered: list[str] = []
    for i, t in enumerate(templates[:max_templates], start=1):
        body = t.render_for_prompt()
        if not body:
            continue
        heading = t.label or t.filename or f"Exemplar {i}"
        rendered.append(f"--- EXEMPLAR {i}: {heading} ---\n{body}")
    if not rendered:
        return ""
    joined = "\n\n".join(rendered)
    return (
        "FORMAT/STYLE EXEMPLARS — the company's gold-standard PRD examples "
        "('what good looks like'). MATCH their structure, section ordering, "
        "headings, level of detail, and VOICE in the PRD you write. These are "
        "format references ONLY: do NOT copy their content, and do NOT import "
        "their facts/numbers into this PRD — ground every claim in the supplied "
        "insight and evidence as instructed above.\n\n" + joined + "\n"
    )

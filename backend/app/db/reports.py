"""reports — durable skill-generated HTML report documents.

One row per captured report (voice-of-customer-report,
competitive-intelligence-review, public-feedback-report, …). The chat answer is
already rendered by the time capture runs, so `save_report` is BEST-EFFORT by
contract: a failed save costs the artifacts library one entry, it must never
break the answer the user is reading. Callers swallow its exceptions.

Reads filter by `company_id` (all workspaces in a company share one report
library — mirrors db/custom_skills.py); `workspace_id` records which workspace
generated the report and may be NULL on paths without workspace context.
"""
from __future__ import annotations

import logging

from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)


@retry_on_disconnect
def save_report(
    company_id: str,
    *,
    skill: str,
    title: str,
    html: str,
    question: str = "",
    workspace_id: str | None = None,
    ask_id: int | None = None,
    conversation_id: int | None = None,
    prd_id: int | None = None,
) -> int | None:
    """Persist a captured report and return its id (None when the insert yields
    no row).

    `conversation_id` / `prd_id` are the report's ATTACHMENT: pass whatever the
    originating ask carried and nothing more — a present id means the report
    hangs off that chat room or PRD, absent means it stands alone.
    """
    c = require_client()
    resp = c.table("reports").insert({
        "company_id": company_id,
        "workspace_id": workspace_id,
        "skill": skill,
        "title": title or "",
        "html": html or "",
        "question": question or "",
        "ask_id": ask_id,
        "conversation_id": conversation_id,
        "prd_id": prd_id,
    }).execute()
    return resp.data[0]["id"] if resp.data else None


@retry_on_disconnect
def get_report(report_id: int, company_id: str) -> dict | None:
    """One report by id, scoped to its company — the caller's tenant gate.

    Returns None for a missing row AND for a row owned by another company, so
    the route 404s either way and never discloses cross-tenant existence.
    """
    c = require_client()
    resp = (
        c.table("reports")
        .select(
            "id, company_id, workspace_id, skill, title, html, question, "
            "ask_id, conversation_id, prd_id, created_at"
        )
        .eq("id", report_id)
        .eq("company_id", company_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None

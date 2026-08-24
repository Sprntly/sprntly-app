"""HTTP layer for captured report artifacts.

  GET  /v1/reports?conversation_id=  -> the reports captured in one chat thread,
                                        without their bodies. Backs the chat
                                        panel's Reports tab.
  GET  /v1/reports/{report_id}       -> one report: its HTML document plus the
                                        attachment metadata the viewer's header needs.
  GET  /v1/reports/{report_id}/pdf   -> the same document rendered to PDF, as a
                                        file download. PDF is the only download
                                        format offered (see app/report_pdf.py).
  POST /v1/reports/{report_id}/share -> turn link sharing on/off for the report.

The unauthenticated side of sharing lives in routes/reports_public.py, kept in a
separate module so the no-auth surface is auditable on its own.

The artifact LIST (GET /v1/artifacts) deliberately omits `html` — a listing must
not carry N full documents — so the viewer fetches the body by id from here once
the user opens a row.

Tenant gate: `require_company` resolves the caller's company from the JWT and
`get_report` scopes the read to it, so a report belonging to another company
reads as missing and 404s exactly like a nonexistent id (no cross-tenant
existence disclosure — same posture as routes/artifacts.py). Reports are
COMPANY-scoped rather than per-workspace: every workspace in a company shares one
report library (see app/report_capture.py and db/reports.py).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.db import get_report, list_reports_for_conversation, set_report_share_config
from app.design_agent.rate_limit import SlidingWindowLimiter
from app.design_agent.url_slug import url_slugify
from app.report_pdf import render_report_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/reports", tags=["reports"])

# A PDF render launches headless Chromium — seconds of CPU per call, unlike every
# other read on this router. Keyed by USER (not company) so one person looping
# downloads cannot lock their colleagues out. Process-local; see
# design_agent/rate_limit.py for the Redis path under horizontal scaling.
PDF_LIMITER = SlidingWindowLimiter(max_events=15, window_seconds=300)


@router.get("")
def list_conversation_reports(
    conversation_id: int,
    company: CompanyContext = Depends(require_company),
):
    """The reports captured in one chat thread, newest first.

    A thread can accumulate several reports (each ask that runs a report skill
    captures one), which is why this returns a list rather than the single row a
    caller might expect — the panel shows them as a list and opens one at a time.

    Bodies are omitted: the reader opens one report, so shipping N documents to
    render a list of titles would be pure waste. `GET /v1/reports/{id}` serves the
    one they pick.

    Scoped to the caller's company, so an id from another tenant reads as a thread
    with no reports rather than 403ing (or leaking that it exists at all).
    """
    return {
        "reports": [
            {
                "id": r["id"],
                "skill": r.get("skill") or "",
                "title": r.get("title") or "",
                "question": r.get("question") or "",
                "created_at": r.get("created_at"),
                "conversation_id": r.get("conversation_id"),
                "prd_id": r.get("prd_id"),
                "share_mode": r.get("share_mode") or "private",
            }
            for r in list_reports_for_conversation(conversation_id, company.company_id)
        ]
    }


@router.get("/{report_id}")
def read_report(
    report_id: int,
    company: CompanyContext = Depends(require_company),
):
    """One captured report, including its rendered HTML document.

    `conversation_id` / `prd_id` are the report's ATTACHMENT — the chat room and
    PRD it was generated in. Either may be null (the ask carried no such context,
    or the chat/PRD was since deleted); the viewer treats a null as "not
    attached" rather than an error.
    """
    row = get_report(report_id, company.company_id)
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    return {
        "id": row["id"],
        "skill": row.get("skill") or "",
        "title": row.get("title") or "",
        "question": row.get("question") or "",
        "html": row.get("html") or "",
        "created_at": row.get("created_at"),
        "conversation_id": row.get("conversation_id"),
        "prd_id": row.get("prd_id"),
        # Share state so the viewer's Share menu opens already reflecting reality.
        # The token rides along only when sharing is actually on.
        "share_mode": row.get("share_mode") or "private",
        "share_token": row.get("share_token") if row.get("share_mode") != "private" else None,
    }


class ReportEdit(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)


@router.post("/{report_id}/chat-edit")
async def chat_edit_report(
    report_id: int,
    body: ReportEdit,
    company: CompanyContext = Depends(require_company),
):
    """Apply a free-form chat instruction to this report.

    THE TARGET IS THE URL'S REPORT, NOT AN ARGUMENT. The client names the report
    the user has open beside the chat; nothing in the request body can redirect
    the write. Same rule as `edit_prd` and the Goal Analysis report editor, same
    reason: a model — or a prompt-injected instruction sitting inside a
    customer's own document — must not be able to edit a document the user is
    not looking at.

    Live on call, no confirm gate: that gate was retired for PRDs in e05577dc,
    and two documents that read in the same panel should not be on two
    contracts.

    An instruction the editor judges is NOT an edit (a question about the
    report) writes nothing and comes back with `sections_changed: []`, which is
    what lets the chat answer instead of claiming a change it did not make.
    """
    from app.artifact_chat_edit import edit_report_scoped

    result = await asyncio.to_thread(
        edit_report_scoped, report_id, body.instruction, company
    )
    row = result["report"]
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "skill": row.get("skill") or "",
        "html": row.get("html") or "",
        "sections_changed": result["sections_changed"],
        "summary": result["summary"],
    }


@router.get("/{report_id}/pdf")
async def download_report_pdf(
    report_id: int,
    company: CompanyContext = Depends(require_company),
):
    """The report rendered to PDF, as an attachment download.

    Rendered server-side so every download is byte-identical regardless of the
    viewer's browser, and so the same renderer can serve a shared link later.

    503 (not an empty file) when the renderer is unavailable or fails: the user
    clicked Download, so a corrupt or blank PDF would be worse than a clear
    error. 429 when the per-user render budget is exhausted.
    """
    row = get_report(report_id, company.company_id)
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")

    key = company.user_id or company.company_id
    if not PDF_LIMITER.check(key):
        raise HTTPException(
            status_code=429,
            detail="Too many PDF downloads. Please wait a moment and try again.",
            headers={"Retry-After": str(PDF_LIMITER.retry_after(key))},
        )
    PDF_LIMITER.register(key)

    pdf = await render_report_pdf(row.get("html") or "")
    if not pdf:
        logger.warning(
            "report pdf unavailable report_id=%s company=%s", report_id, company.company_id
        )
        raise HTTPException(
            status_code=503,
            detail="Couldn't generate the PDF right now. Please try again.",
        )

    filename = f"{url_slugify(row.get('title') or '', fallback='report')}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # A report is immutable once captured, but the download is per-tenant
            # authenticated content — never let a shared cache hold it.
            "Cache-Control": "private, no-store",
        },
    )


class ShareIn(BaseModel):
    share_mode: Literal["private", "public", "passcode"]
    # Required iff share_mode == 'passcode'. Never stored or logged in plaintext —
    # db/reports.py keeps only the argon2id hash.
    passcode: str | None = Field(default=None, min_length=4, max_length=128)


@router.post("/{report_id}/share")
def configure_report_share(
    report_id: int,
    body: ShareIn,
    company: CompanyContext = Depends(require_company),
):
    """Turn link sharing on or off for a report.

    Sharing is OPT-IN and defaults to private: these documents quote customers
    verbatim and can carry competitive intelligence, so nothing is reachable by
    link until someone here asks for it.

    Returns the mode and the token (null while private and never yet shared). The
    token is PRESERVED across public→private→public, so pausing sharing does not
    invalidate a link already handed out — see db/reports.set_report_share_config.
    """
    if body.share_mode == "passcode" and not body.passcode:
        raise HTTPException(status_code=400, detail="A passcode is required for passcode sharing.")

    row = set_report_share_config(
        report_id=report_id,
        company_id=company.company_id,
        share_mode=body.share_mode,
        passcode=body.passcode,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    return {
        "share_mode": row.get("share_mode") or "private",
        # Only meaningful when sharing is on; the client builds /r/<token> from it.
        "share_token": row.get("share_token") if row.get("share_mode") != "private" else None,
    }

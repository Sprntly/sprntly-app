"""Render a client-assembled HTML document to PDF.

  POST /v1/documents/pdf -> the supplied document rendered to PDF, as a file
                            download.

WHY THE CLIENT SENDS THE HTML. The obvious shape would be
``POST /v1/prd/{id}/pdf``, with the server reading the document it already
stores. Two things make that the wrong call here:

  * The PRD and Evidence panels are ``contenteditable``. A server-side read
    exports what was last SAVED, so a user who edits and immediately downloads
    silently gets a stale document — the one failure mode a download must not
    have.
  * The Evidence + PRD export is one document built from two briefs, each with
    its own global CSS (``:root``/``body``/``.row``), scoped under a wrapper so
    the two cannot collide. That scoper lives in ``web/app/lib/combinedExport.ts``
    and is also what the Word export uses. Reimplementing it in Python would mean
    two brace-matching CSS rewriters that must agree forever.

So the client assembles, the server renders. This is the same renderer, and
therefore the same watermark and sprntly.ai footer, that the report PDF download
uses — see ``app/report_pdf.py`` and ``app/watermark.py``.

RENDERING CALLER-SUPPLIED HTML, SAFELY. This accepts a document body and draws it
in headless Chromium on our own host, so the guard rails are the point rather
than an afterthought. They are the renderer's, not this module's:

  * JavaScript is disabled for the render context, so nothing in the payload
    executes.
  * Subresource fetches are allowlisted to the Google Fonts hosts; every other
    URL — cloud metadata, internal services, ``file://`` — is aborted. That is
    the SSRF boundary, and it is tested directly in tests/test_report_pdf.py.

What is left is resource cost (a render is seconds of CPU), handled here: the
caller must be authenticated, the body is capped, and renders are rate limited
per user.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.design_agent.rate_limit import SlidingWindowLimiter
from app.design_agent.url_slug import url_slugify
from app.report_pdf import render_report_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/documents", tags=["documents"])

# Same budget and keying as the report PDF route: per USER, so one person looping
# downloads cannot lock their colleagues out.
PDF_LIMITER = SlidingWindowLimiter(max_events=15, window_seconds=300)

# A self-contained brief runs ~100KB with its inline stylesheet; the combined
# Evidence + PRD document roughly doubles that. 2MB is far above any real
# document and still bounds what one request can hand to Chromium.
_MAX_HTML_CHARS = 2_000_000


class DocumentPdfIn(BaseModel):
    html: str = Field(..., min_length=1, max_length=_MAX_HTML_CHARS)
    # Slugified server-side before it reaches a header — never interpolate a
    # caller-supplied string into Content-Disposition.
    filename: str = Field("document", max_length=200)


@router.post("/pdf")
async def render_document_pdf(
    body: DocumentPdfIn,
    company: CompanyContext = Depends(require_company),
):
    """The supplied document rendered to PDF, as an attachment download.

    503 (not an empty file) when the renderer is unavailable or fails: the user
    clicked Download, so a corrupt or blank PDF would be worse than a clear
    error. 429 when the per-user render budget is exhausted.
    """
    key = company.user_id or company.company_id
    if not PDF_LIMITER.check(key):
        raise HTTPException(
            status_code=429,
            detail="Too many PDF downloads. Please wait a moment and try again.",
            headers={"Retry-After": str(PDF_LIMITER.retry_after(key))},
        )
    PDF_LIMITER.register(key)

    pdf = await render_report_pdf(body.html)
    if not pdf:
        logger.warning("document pdf unavailable company=%s", company.company_id)
        raise HTTPException(
            status_code=503,
            detail="Couldn't generate the PDF right now. Please try again.",
        )

    filename = f"{url_slugify(body.filename, fallback='document')}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Per-tenant authenticated content — never let a shared cache hold it.
            "Cache-Control": "private, no-store",
        },
    )

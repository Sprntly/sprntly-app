"""Unauthenticated report share viewer — the `/r/<token>` surface.

  GET  /v1/public/reports/{token}         -> the document, when the link is public
  POST /v1/public/reports/{token}/unlock  -> the document, after a passcode check
  POST /v1/public/reports/{token}/pdf     -> the document as a PDF download

NO-AUTH BY DESIGN. The share_token IS the access primitive, so these routes carry
no session dependency and no company filter —
`db.find_report_by_share_token` is the one legitimate cross-tenant read (it
refuses any report whose sharing is off, so a revoked link is indistinguishable
from one that never existed).

Kept in its OWN module rather than alongside the authenticated report routes: a
no-auth surface should be reviewable in isolation, and nothing here should ever
gain an auth'd sibling by accident.

Three deliberate properties:

  - MINIMUM DISCLOSURE. Exactly four fields leave this surface (title, kind, the
    document, and when it was made). Never the report id, company_id,
    workspace_id, the originating question, or the chat/PRD it is attached to —
    that attachment names internal work and has no business on a public page.
    `response_model` enforces the key set even if the row carries more columns.
  - 404, NEVER 401/403, for an unknown or unshared token, so scanning tokens
    cannot distinguish "wrong" from "revoked" from "exists but private".
  - RATE LIMITED per token: a bounded read budget, and a much tighter passcode
    budget so a passcode-gated link cannot be brute-forced.

The passcode flow is intentionally stateless — no cookie, no server session. A
correct passcode returns the document in the same response, and the page holds it
in memory; a reload asks again. That keeps this surface free of session state at
the cost of one extra prompt on refresh.
"""
from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.db import find_report_by_share_token
from app.db.prototypes import verify_share_passcode
from app.design_agent.rate_limit import SlidingWindowLimiter
from app.design_agent.url_slug import url_slugify
from app.report_pdf import render_report_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/public/reports", tags=["reports-public"])

# Read budget per token. Generous — a shared report can legitimately be opened by
# a whole team at once — but bounded so a token cannot be used as free bandwidth.
PUBLIC_READ_LIMITER = SlidingWindowLimiter(max_events=60, window_seconds=60)
# Passcode budget per token. Tight: this is the brute-force gate.
PUBLIC_PASSCODE_LIMITER = SlidingWindowLimiter(max_events=10, window_seconds=3600)
# PDF budget per token — each render launches headless Chromium.
PUBLIC_PDF_LIMITER = SlidingWindowLimiter(max_events=10, window_seconds=300)


def _token_hash(token: str) -> str:
    """sha256 prefix of a share token, for log correlation ONLY.

    The full token must never reach log aggregation: it is the access primitive,
    so logging it verbatim is equivalent to logging the share URL.
    """
    return hashlib.sha256((token or "").encode()).hexdigest()[:8]


class PublicReport(BaseModel):
    """The entire public projection of a report. Adding a field here widens what
    an anonymous visitor can see — do it deliberately.

    `skill` is the raw skill id (not a secret — `kind` already discloses its
    humanised form): the viewer needs it as a DISCRIMINATOR, not a label, to
    tell a `saved-chat` report (raw markdown in `html`, rendered with
    `SavedChatMarkdown`) apart from every other report (a self-contained HTML
    document, rendered in `HtmlReportView`'s sandboxed iframe) — `html` alone
    no longer says which shape it is."""

    title: str
    kind: str
    skill: str
    html: str
    created_at: str | None = None


class UnlockIn(BaseModel):
    passcode: str = Field(..., min_length=1, max_length=128)


class PdfIn(BaseModel):
    # Present only for a passcode-gated link. Optional so one route serves both
    # modes (a public link sends nothing).
    passcode: str | None = Field(default=None, max_length=128)


def _not_found() -> HTTPException:
    """The single failure shape for this surface: unknown token, revoked link, and
    private report are all indistinguishable."""
    return HTTPException(status_code=404, detail="Not found")


def _require_read_budget(token: str) -> None:
    if not PUBLIC_READ_LIMITER.check(token):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again shortly.",
            headers={"Retry-After": str(PUBLIC_READ_LIMITER.retry_after(token))},
        )
    PUBLIC_READ_LIMITER.register(token)


def _resolve_shared(token: str) -> dict:
    """The shared report for `token`, or 404. Does NOT check the passcode gate."""
    row = find_report_by_share_token(token)
    if not row:
        logger.info("public report miss token=%s", _token_hash(token))
        raise _not_found()
    return row


def _projection(row: dict) -> PublicReport:
    """Narrow a report row to the public four fields.

    `kind` is the humanised skill label (`app.labels`), not the raw skill id —
    a visitor has no use for an internal identifier.
    """
    from app.labels import humanize_label

    return PublicReport(
        title=row.get("title") or "Report",
        kind=humanize_label(row.get("skill") or "") or "Report",
        skill=row.get("skill") or "",
        html=row.get("html") or "",
        created_at=row.get("created_at"),
    )


@router.get("/{token}", response_model=PublicReport)
def read_public_report(token: str):
    """The shared document, when the link is public.

    A passcode-gated link answers 401 with `passcode_required` — the ONE case
    where this surface distinguishes itself from 404, because the visitor holds a
    valid token and needs to be told what to do next.
    """
    _require_read_budget(token)
    row = _resolve_shared(token)
    if row.get("share_mode") == "passcode":
        raise HTTPException(status_code=401, detail="passcode_required")
    return _projection(row)


@router.post("/{token}/unlock", response_model=PublicReport)
def unlock_public_report(token: str, body: UnlockIn):
    """Exchange a passcode for the document.

    Rate-limited hard per token: this is the brute-force surface. A wrong passcode
    and a token that isn't passcode-gated both answer 401 with the same message,
    so a caller learns nothing from the difference.
    """
    if not PUBLIC_PASSCODE_LIMITER.check(token):
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please try again later.",
            headers={"Retry-After": str(PUBLIC_PASSCODE_LIMITER.retry_after(token))},
        )
    PUBLIC_PASSCODE_LIMITER.register(token)

    row = _resolve_shared(token)
    if row.get("share_mode") != "passcode":
        # Nothing to unlock. Answered as a failed unlock rather than a redirect so
        # the shape of the response never maps the link's mode.
        raise HTTPException(status_code=401, detail="Incorrect passcode")
    if not verify_share_passcode(body.passcode, row.get("share_passcode_hash")):
        logger.info("public report passcode reject token=%s", _token_hash(token))
        raise HTTPException(status_code=401, detail="Incorrect passcode")
    return _projection(row)


@router.post("/{token}/pdf")
async def download_public_report_pdf(token: str, body: PdfIn | None = None):
    """The shared document as a PDF download.

    POST (not GET) because a passcode-gated link must be able to carry its
    passcode in the body rather than in a URL that would land in access logs and
    browser history. One route serves both modes.
    """
    if not PUBLIC_PDF_LIMITER.check(token):
        raise HTTPException(
            status_code=429,
            detail="Too many downloads. Please try again shortly.",
            headers={"Retry-After": str(PUBLIC_PDF_LIMITER.retry_after(token))},
        )
    PUBLIC_PDF_LIMITER.register(token)

    row = _resolve_shared(token)
    if row.get("share_mode") == "passcode":
        passcode = (body.passcode if body else None) or ""
        if not verify_share_passcode(passcode, row.get("share_passcode_hash")):
            raise HTTPException(status_code=401, detail="Incorrect passcode")

    pdf = await render_report_pdf(row.get("html") or "")
    if not pdf:
        logger.warning("public report pdf unavailable token=%s", _token_hash(token))
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
            "Cache-Control": "private, no-store",
        },
    )

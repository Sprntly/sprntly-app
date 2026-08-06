"""Bare-link guest access routes — the token-less sibling of
`artifact_share.py`, keyed by a PRD's opaque `public_id` (UUID) instead of a
minted share token — NEVER the raw sequential `prds.id`, which would let a
domain-matched caller blind-enumerate every PRD the tenant has ever
generated (2026-08-02 — see the `prds.public_id`
migration's own comment for the full rationale).

Same trust-level shape as artifact_share.py, minus mint (there is nothing to
mint here — every prd is reachable this way, gated only by company-domain
match):
  - metadata — PUBLIC, pre-auth: title/company/domain-hint only.
  - resolve  — AUTHED (require_session only): decides routing for a caller
               who may have ZERO company memberships yet.
  - auto-join-company — AUTHED: the one-shot, signup-time-only company grant
               on a matching email domain.
  - join     — AUTHED: grants WORKSPACE-only membership into the prd's
               owning workspace. 404s (via a null workspace_id) for a prd on
               an unbound legacy dataset — nothing to join into.
  - content  — AUTHED: read-only guest access to the prd's rendered content,
               company-scoped (never workspace-scoped).

Every route resolves `public_id` to the real internal id FIRST (identical
404 either way — a malformed/unknown public_id looks exactly like a missing
prd, no existence disclosure) and hands that resolved int straight to the
SAME db-layer functions `prd_access.py`'s db module already exposes — those
functions are unchanged; only the external identifier shape at this
boundary is. Every deny path returns the IDENTICAL "Not found" body
regardless of reason, mirroring artifact_share.py's non-disclosure
convention."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_session, session_email
from app.db.prd_access import (
    auto_join_company_on_domain_match_for_prd,
    grant_workspace_membership_for_prd,
    prd_access_content,
    prd_public_metadata,
    resolve_prd_access,
)
from app.db.prds import resolve_prd_id_by_public_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/prd-access", tags=["prd-access"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not found")


def _resolve_or_404(public_id: str) -> int:
    """public_id -> real internal prd id, or 404 — the ONE place every route
    below translates the external identifier shape. Never discloses whether
    a malformed/unknown public_id "almost" matched something."""
    prd_id = resolve_prd_id_by_public_id(public_id)
    if prd_id is None:
        raise _not_found()
    return prd_id


def _session_identity(session: dict) -> tuple[str, str | None]:
    """Same real-Supabase-user-only requirement as artifact_share.py's
    identical helper — see that module's docstring for why a legacy demo
    cookie can't bind a grant."""
    user_id = session.get("sub")
    if not user_id or session.get("aud") != "supabase":
        raise HTTPException(403, "Signed-in user required")
    return user_id, session_email(session) or None


@router.get("/{public_id}")
def metadata(public_id: str) -> dict:
    """Pre-auth artifact metadata for the entry-gate screen, keyed by
    public_id. No `sharer_name` field — there is no share row here, so no
    "who shared this" to report."""
    prd_id = _resolve_or_404(public_id)
    meta = prd_public_metadata(prd_id)
    if meta is None:
        logger.info("prd_access_deny route=metadata gate=missing_or_unowned public_id=%s", public_id)
        raise _not_found()
    return meta


@router.get("/{public_id}/resolve")
def resolve(public_id: str, session: dict = Depends(require_session)) -> dict:
    """Decide the routing outcome for a signed-in caller. No mutation."""
    prd_id = _resolve_or_404(public_id)
    user_id, user_email = _session_identity(session)
    result = resolve_prd_access(prd_id=prd_id, user_id=user_id, user_email=user_email)
    if result["outcome"] == "not_found":
        logger.info("prd_access_deny route=resolve gate=not_found public_id=%s", public_id)
        raise _not_found()
    if result["outcome"] == "blocked":
        logger.info(
            "prd_access_deny route=resolve gate=blocked reason=%s public_id=%s",
            result["reason"],
            public_id,
        )
        return {"outcome": "blocked", "reason": result["reason"]}
    return {
        # "member" (caller can act in the owning workspace — the gate sends
        # them into the real, editable app) or "guest_view" (read-only shell
        # + Join prompt). See db.artifact_shares.resolve_share_access.
        "outcome": result["outcome"],
        "artifact_id": prd_id,
        "artifact_type": "prd",
        "owning_company_name": result["owning_company_name"],
        # The workspace the prd lives in — the gate stores it as the
        # caller's active workspace before handing over to the app, so a
        # `member` arriving from another workspace doesn't land on a 404.
        "owner_workspace_id": result["owner_workspace_id"],
    }


@router.post("/{public_id}/auto-join-company")
def auto_join_company(public_id: str, session: dict = Depends(require_session)) -> dict:
    """One-shot, signup-time-only company grant on a matching email domain —
    same contract as artifact_share.py's identical route. Always 200 (a
    malformed/unknown public_id resolves to a no-op, not a 404 — matching
    this route's own always-200, non-disclosure convention rather than the
    other routes' 404-on-unresolvable)."""
    prd_id = resolve_prd_id_by_public_id(public_id)
    if prd_id is None:
        return {"joined_company_id": None}
    user_id, user_email = _session_identity(session)
    company_id = auto_join_company_on_domain_match_for_prd(
        prd_id=prd_id, user_id=user_id, user_email=user_email
    )
    return {"joined_company_id": company_id}


@router.post("/{public_id}/join")
def join(public_id: str, session: dict = Depends(require_session)) -> dict:
    """Grant workspace access. Re-runs the FULL resolve check server-side.

    Accepts BOTH same-company outcomes for the deploy-window reason
    artifact_share.py's /join documents — a `member` join is substantively
    a no-op, but a browser on the pre-`member` bundle still offers the
    button and it must not 403."""
    prd_id = _resolve_or_404(public_id)
    user_id, _ = _session_identity(session)
    result = resolve_prd_access(prd_id=prd_id, user_id=user_id, user_email=None)
    if result["outcome"] == "not_found":
        logger.info("prd_access_deny route=join gate=not_found public_id=%s", public_id)
        raise _not_found()
    if result["outcome"] not in ("guest_view", "member"):
        logger.info(
            "prd_access_deny route=join gate=blocked reason=%s public_id=%s",
            result.get("reason"),
            public_id,
        )
        raise HTTPException(status_code=403, detail="not authorized to join")

    workspace_id = grant_workspace_membership_for_prd(prd_id=prd_id, user_id=user_id)
    if workspace_id is None:
        # Unbound legacy dataset — nothing to join into. Fail closed rather
        # than silently pick a workspace on the caller's behalf.
        logger.info("prd_access_deny route=join gate=no_workspace public_id=%s", public_id)
        raise HTTPException(status_code=409, detail="This document has no workspace to join")
    return {
        "owning_company_name": result["owning_company_name"],
        "workspace_id": workspace_id,
    }


@router.get("/{public_id}/content")
def content(public_id: str, session: dict = Depends(require_session)) -> dict:
    """Guest read of the prd's rendered content + evidence + tickets,
    company-scoped. Re-runs the FULL resolve check; anything but a
    same-company outcome 404s. `member` is accepted alongside `guest_view`
    — same deploy-window reason as /join, and it discloses nothing a
    `member` can't already read through the ordinary endpoints."""
    prd_id = _resolve_or_404(public_id)
    user_id, user_email = _session_identity(session)
    result = resolve_prd_access(prd_id=prd_id, user_id=user_id, user_email=user_email)
    if result["outcome"] not in ("guest_view", "member"):
        logger.info(
            "prd_access_deny route=content gate=%s public_id=%s",
            result["outcome"] if result["outcome"] == "not_found" else "blocked",
            public_id,
        )
        raise _not_found()

    return prd_access_content(prd_id, result["owner_company_id"])

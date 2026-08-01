"""Artifact share-grant primitive routes — mint / metadata / resolve.

Three trust levels:
  - mint     — AUTHED (require_workspace): the sharer must already own the
               artifact (require_owned_prd 404s a non-owned prd_id).
  - metadata — PUBLIC, pre-auth: possession of the token itself is the only
               gate. Discloses only the title/sharer/company/domain-hint,
               nothing else.
  - resolve  — AUTHED (require_session only, NOT require_company): decides
               the routing outcome for a caller who may have ZERO company
               memberships yet (a domain-matched fresh signup mid-flow).

Every deny path returns the IDENTICAL "Not found" body regardless of the
specific reason (missing token / revoked / different company / domain
mismatch is never disclosed) — mirrors design_agent_bundle.py's
`da_bundle_deny` non-disclosure pattern: the specific sub-reason is logged
server-side only, never in the response body.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import WorkspaceContext, require_session, require_workspace, session_email
from app.db.artifact_shares import (
    get_share_by_token,
    mint_share,
    owning_company_domain,
    resolve_share_access,
)
from app.db.companies import display_name_for_company_id, profile_name_for_user
from app.db.prds import get_prd_rendered
from app.deps.ownership import require_owned_prd

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/artifact-share", tags=["artifact-share"])


def _not_found() -> HTTPException:
    # 404 (never 403) so cross-tenant / invalid-token existence is never disclosed.
    return HTTPException(status_code=404, detail="Not found")


def _session_identity(session: dict) -> tuple[str, str | None]:
    """User id + best-effort email from a require_session() payload.

    /resolve, /join, and /content all run BEFORE a company membership can
    exist, so — like the sibling POST /v1/invites/accept — they accept only
    a real (Supabase) user session; a legacy demo cookie has no user identity
    to bind a grant to."""
    user_id = session.get("sub")
    if not user_id or session.get("aud") != "supabase":
        raise HTTPException(403, "Signed-in user required")
    return user_id, session_email(session) or None


class MintShareBody(BaseModel):
    artifact_type: str = "prd"
    artifact_id: int


@router.post("", status_code=201)
def mint(
    body: MintShareBody,
    workspace: WorkspaceContext = Depends(require_workspace),
) -> dict:
    """Mint a share token for an artifact the caller's active workspace owns.

    "Who can share" is intentionally NOT restricted to admins (deferred
    decision) — any workspace member who can already see the artifact can
    share it, matching today's un-gated Share behaviour on other surfaces."""
    if body.artifact_type != "prd":
        raise HTTPException(400, "Only 'prd' artifacts are shareable")
    # Never mint a token for an artifact the caller can't already see.
    require_owned_prd(body.artifact_id, workspace.company_id, workspace.workspace_id)
    share = mint_share(
        artifact_type=body.artifact_type,
        artifact_id=body.artifact_id,
        owner_company_id=workspace.company_id,
        owner_workspace_id=workspace.workspace_id,
        created_by_user_id=workspace.user_id,
    )
    return {"token": share["token"]}


@router.get("/{token}")
def metadata(token: str) -> dict:
    """Pre-auth artifact metadata for the entry-gate screen. Possession of
    the token is the only gate; discloses title/sharer/company + a
    copy-only email-domain hint (never an enforcement signal — resolve/join
    below are the real gate)."""
    share = get_share_by_token(token)
    if not share or share.get("revoked_at"):
        logger.info(
            "artifact_share_deny route=metadata gate=%s",
            "missing" if not share else "revoked",
        )
        raise _not_found()

    prd = get_prd_rendered(share["artifact_id"])
    title = (prd or {}).get("title") or ""
    domain = owning_company_domain(share["owner_company_id"])
    return {
        "artifact_type": share["artifact_type"],
        "title": title,
        "sharer_name": profile_name_for_user(share["created_by_user_id"]),
        "owning_company_name": display_name_for_company_id(share["owner_company_id"]),
        "required_email_domain": domain,
    }


@router.get("/{token}/resolve")
def resolve(token: str, session: dict = Depends(require_session)) -> dict:
    """Decide the routing outcome for a signed-in caller. No mutation — safe
    to call repeatedly (e.g. on every postLoginPath() evaluation)."""
    user_id, user_email = _session_identity(session)
    result = resolve_share_access(token=token, user_id=user_id, user_email=user_email)
    if result["outcome"] == "not_found":
        logger.info("artifact_share_deny route=resolve gate=not_found")
        raise _not_found()
    if result["outcome"] == "blocked":
        logger.info(
            "artifact_share_deny route=resolve gate=blocked reason=%s",
            result["reason"],
        )
        return {"outcome": "blocked", "reason": result["reason"]}
    share = result["share"]
    return {
        "outcome": "guest_view",
        "artifact_id": share["artifact_id"],
        "artifact_type": share["artifact_type"],
        "owning_company_name": result["owning_company_name"],
        "sharer_name": result["sharer_name"],
    }

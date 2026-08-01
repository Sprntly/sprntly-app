"""Artifact share-grant primitive routes — mint / metadata / resolve / join /
content.

Trust levels:
  - mint     — AUTHED (require_workspace): the sharer must already own the
               artifact (require_owned_prd 404s a non-owned prd_id).
  - metadata — PUBLIC, pre-auth: possession of the token itself is the only
               gate. Discloses only the title/sharer/company/domain-hint,
               nothing else.
  - resolve  — AUTHED (require_session only, NOT require_company): decides
               the routing outcome for a caller who may have ZERO company
               memberships yet (a domain-matched fresh signup mid-flow).
  - join     — AUTHED (require_session only): mutates — grants full Member
               role (fresh signup) or a workspace-only grant (same-company-
               different-workspace caller). Re-runs the FULL resolve check
               server-side; a client-side /resolve call is never trusted.
  - content  — AUTHED (require_session only): read-only guest access to the
               shared PRD's rendered content, scoped by COMPANY (not
               workspace) — the cross-workspace-same-company allowance this
               primitive exists to provide.

Every deny path returns the IDENTICAL "Not found" body regardless of the
specific reason (missing token / revoked / different company / domain
mismatch is never disclosed) — mirrors design_agent_bundle.py's
`da_bundle_deny` non-disclosure pattern: the specific sub-reason is logged
server-side only, never in the response body. join's blocked-outcome case is
the one deliberate exception (403, not 404) — the share itself was already
proven to exist by the caller having reached this far.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import WorkspaceContext, require_session, require_workspace, session_email
from app.db.artifact_shares import (
    get_share_by_token,
    mint_share,
    owning_company_domain,
    record_join,
    require_shared_prd,
    resolve_share_access,
)
from app.db.client import require_client
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


# ─── join (mutating) ──────────────────────────────────────────────────────


def _grant_membership(*, share: dict, user_id: str, same_company: bool) -> str:
    """Grant the membership /join promises. Mirrors
    `app.db.team.accept_invite_for_user`'s company_members insert shape +
    workspace grant (copied, not reimplemented from scratch) and its
    invalidate_user call sites (app/db/team.py's update_member_role /
    delete_member) — memberships_for_user is cached 30s, so skipping this
    would let a freshly-joined user 403 on their very next request for up to
    30s. Returns the workspace_id the caller was granted into."""
    from app.db.authcache import invalidate_user
    from app.db.workspaces import ensure_default_workspace, upsert_workspace_member

    if same_company:
        # Already a member of the owning company — grant the OWNING
        # workspace only (no company_members insert; they already have one).
        upsert_workspace_member(share["owner_workspace_id"], user_id, "member")
        invalidate_user(user_id)
        return share["owner_workspace_id"]

    # Fresh domain-matched signup: full Member role (locked, not
    # configurable) + the owning company's default workspace.
    client = require_client()
    try:
        client.table("company_members").insert(
            {
                "id": uuid.uuid4().hex,
                "company_id": share["owner_company_id"],
                "user_id": user_id,
                "role": "member",
            }
        ).execute()
    except Exception:  # noqa: BLE001 — already a member (idempotent double-click)
        pass
    default_ws = ensure_default_workspace(share["owner_company_id"])
    upsert_workspace_member(default_ws["id"], user_id, "member")
    invalidate_user(user_id)
    return default_ws["id"]


@router.post("/{token}/join")
def join(token: str, session: dict = Depends(require_session)) -> dict:
    """Grant access + record attribution. Re-runs the FULL resolve check
    server-side — a client that already called /resolve is never trusted;
    only a fresh `guest_view` outcome, computed here, may mutate."""
    user_id, user_email = _session_identity(session)
    result = resolve_share_access(token=token, user_id=user_id, user_email=user_email)
    if result["outcome"] == "not_found":
        logger.info("artifact_share_deny route=join gate=not_found")
        raise _not_found()
    if result["outcome"] != "guest_view":
        logger.info(
            "artifact_share_deny route=join gate=blocked reason=%s",
            result.get("reason"),
        )
        raise HTTPException(status_code=403, detail="not authorized to join")

    share = result["share"]
    workspace_id = _grant_membership(
        share=share, user_id=user_id, same_company=result["same_company"]
    )
    record_join(
        share_id=share["id"],
        joined_user_id=user_id,
        joined_company_id=share["owner_company_id"],
        joined_workspace_id=workspace_id,
    )
    return {
        "sharer_name": result["sharer_name"],
        "owning_company_name": result["owning_company_name"],
        "workspace_id": workspace_id,
    }


# ─── content (read-only, company-scoped) ─────────────────────────────────


def _prd_evidence(prd_row: dict) -> dict | None:
    """Mirrors GET /v1/prd/{id}/evidence's own lookup: chat-task evidence is
    keyed (brief_id, theme_id); every other PRD source has no evidence doc,
    so this returns None rather than a list (matching the underlying
    per-PRD evidence model — at most one doc, not a collection)."""
    from app.db.evidences import find_existing_evidence_for_theme

    theme_id = prd_row.get("theme_id") or ""
    if prd_row.get("source") != "chat" or not str(theme_id).startswith("chat:"):
        return None
    return find_existing_evidence_for_theme(prd_row["brief_id"], theme_id)


@router.get("/{token}/content")
def content(token: str, session: dict = Depends(require_session)) -> dict:
    """Guest read of the shared PRD's rendered content + evidence + tickets,
    scoped by COMPANY only via require_shared_prd (never workspace) — the
    cross-workspace-same-company allowance this primitive exists to
    provide. Re-runs the FULL resolve check; a non-guest_view outcome 404s
    (no existence disclosure of the content shape to a blocked caller).
    Read-only by construction — no PUT/POST sibling."""
    from app.db.prd_tickets import get_tickets

    user_id, user_email = _session_identity(session)
    result = resolve_share_access(token=token, user_id=user_id, user_email=user_email)
    if result["outcome"] != "guest_view":
        logger.info(
            "artifact_share_deny route=content gate=%s",
            result["outcome"] if result["outcome"] == "not_found" else "blocked",
        )
        raise _not_found()

    share = result["share"]
    prd = require_shared_prd(share["artifact_id"], share["owner_company_id"])
    rendered = get_prd_rendered(prd["id"]) or prd
    return {
        "prd": rendered,
        "evidence": _prd_evidence(rendered),
        "tickets": get_tickets(share["owner_company_id"], prd["id"]),
    }

"""Bare-link guest access — the token-less sibling of `artifact_shares.py`'s
share-grant primitive.

A minted `artifact_shares` token gives a per-link, revocable, attributed
grant. This module gives the SAME company-domain-matched guest experience
directly off a `prd_id` — no token, no revocability, no sharer attribution.
Anyone who reaches a bare `?prd={id}` link and whose verified email domain
matches the PRD's owning company's admin/owner domain gets the identical
guest_view outcome a minted share would produce. This is a deliberate scope
decision (2026-08-02): raw PRD links are as sensitive as a minted
share link once a recipient can verify their email domain, so gating this
class of access on domain match alone — same posture the share-grant
primitive already accepts — was chosen over restricting bare links to
previously-share-joined guests only.

Ownership resolution mirrors `app.deps.ownership.require_owned_prd`'s own
chain (prd -> brief -> dataset -> workspace) rather than reusing that
function directly — that function's job is to ASSERT a caller's existing
company_id owns a prd (raising 404 otherwise); this module's job is the
inverse — DISCOVER a prd's owning company/workspace with no caller context
yet, since the caller may not even have an account."""
from __future__ import annotations

from app.db.client import require_client, retry_on_disconnect


def _email_domain(email: str | None) -> str | None:
    """Lowercase domain of `email`, or None if malformed/absent. Local copy of
    `artifact_shares.py`'s private helper of the same name — not imported
    cross-module since that one is underscore-private there too."""
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[-1].strip().lower()
    return domain or None


def owning_info_for_prd(prd_id: int) -> dict | None:
    """{"company_id": str, "workspace_id": str | None} for `prd_id`'s owning
    tenant, or None when the prd/brief is missing or its dataset resolves to
    no company at all. `workspace_id` is None for an unbound legacy dataset
    (pre-multi-workspace rollout) — callers that need a real workspace to
    grant into (join) must treat that as "not joinable", not silently pick
    one."""
    from app.db import get_brief_by_id
    from app.db.prds import get_prd
    from app.deps.ownership import company_id_for_dataset
    from app.db.workspaces import workspace_for_dataset_slug

    prd = get_prd(prd_id)
    if not prd:
        return None
    brief = get_brief_by_id(prd["brief_id"])
    if not brief:
        return None
    slug = brief.get("dataset") or ""
    bound = workspace_for_dataset_slug(slug)
    if bound:
        return bound
    company_id = company_id_for_dataset(slug)
    if not company_id:
        return None
    return {"company_id": company_id, "workspace_id": None}


def prd_public_metadata(prd_id: int) -> dict | None:
    """Pre-auth metadata for the entry-gate screen, keyed by `prd_id` instead
    of a share token — same disclosure posture as
    `artifact_share.py`'s metadata route (title/company/domain-hint only).
    None when the prd doesn't exist or resolves to no company (both treated
    as "not found" by the route, same non-disclosure convention)."""
    from app.db.companies import display_name_for_company_id
    from app.db.prds import get_prd_rendered
    from app.db.artifact_shares import owning_company_domain

    owner = owning_info_for_prd(prd_id)
    if owner is None:
        return None
    prd = get_prd_rendered(prd_id)
    title = (prd or {}).get("title") or ""
    return {
        "title": title,
        "owning_company_name": display_name_for_company_id(owner["company_id"]),
        "required_email_domain": owning_company_domain(owner["company_id"]),
    }


def resolve_prd_access(*, prd_id: int, user_id: str, user_email: str | None) -> dict:
    """The bare-link equivalent of `resolve_share_access` — same three
    outcomes, resolved directly from the prd's owning company rather than a
    share row. `user_email` is accepted for signature parity with the
    token-keyed sibling but unused (company membership is the only signal,
    same as `resolve_share_access` — see that function's docstring for why).

    Returns one of:
      {"outcome": "not_found"}
      {"outcome": "blocked", "reason": "different_company"}
      {"outcome": "member", "owning_company_name": str|None,
       "owner_company_id": str, "owner_workspace_id": str|None}
      {"outcome": "guest_view", "owning_company_name": str|None,
       "owner_company_id": str, "owner_workspace_id": str|None}

    The `member` / `guest_view` split is the same one `resolve_share_access`
    documents at length — a same-company caller who can already act in the
    prd's owning workspace belongs in the real, EDITABLE app, not in the
    read-only guest shell. See that function's docstring for the rationale.
    """
    owner = owning_info_for_prd(prd_id)
    if owner is None:
        return {"outcome": "not_found"}

    from app.db.companies import memberships_for_user

    memberships = memberships_for_user(user_id)
    if not memberships or memberships[0].get("company_id") != owner["company_id"]:
        return {"outcome": "blocked", "reason": "different_company"}

    from app.db.companies import display_name_for_company_id
    from app.db.workspaces import user_can_act_in_workspace

    in_app = user_can_act_in_workspace(
        workspace_id=owner["workspace_id"],
        user_id=user_id,
        company_id=owner["company_id"],
        company_role=memberships[0].get("role") or "member",
    )
    return {
        "outcome": "member" if in_app else "guest_view",
        "owning_company_name": display_name_for_company_id(owner["company_id"]),
        "owner_company_id": owner["company_id"],
        "owner_workspace_id": owner["workspace_id"],
    }


@retry_on_disconnect
def auto_join_company_on_domain_match_for_prd(
    *, prd_id: int, user_id: str, user_email: str | None
) -> str | None:
    """Bare-link sibling of `auto_join_company_on_domain_match` — grants
    COMPANY membership (never workspace) on a matching email domain,
    resolved from the prd's owning company instead of a share row. Same
    one-shot/best-effort/no-op-on-mismatch contract; see that function's
    docstring for the full rationale (unchanged here)."""
    import uuid

    from app.db.artifact_shares import owning_company_domain
    from app.db.companies import memberships_for_user

    owner = owning_info_for_prd(prd_id)
    if owner is None:
        return None
    if memberships_for_user(user_id):
        return None

    domain = owning_company_domain(owner["company_id"])
    email_domain = _email_domain(user_email)
    if domain is None or not email_domain or email_domain != domain:
        return None

    client = require_client()
    try:
        client.table("company_members").insert(
            {
                "id": uuid.uuid4().hex,
                "company_id": owner["company_id"],
                "user_id": user_id,
                "role": "member",
            }
        ).execute()
    except Exception:  # noqa: BLE001 — already a member (race/double-call)
        pass

    from app.db.authcache import invalidate_user

    invalidate_user(user_id)
    return owner["company_id"]


def grant_workspace_membership_for_prd(*, prd_id: int, user_id: str) -> str | None:
    """Grant WORKSPACE membership into `prd_id`'s owning workspace. Returns
    the workspace_id granted into, or None when the prd's dataset is an
    unbound legacy dataset with no workspace to join (fail closed — callers
    must treat None as "not joinable", never silently pick a workspace).

    No attribution row is recorded (unlike the token-keyed `/join`'s
    `record_join`) — there is no share row to attribute this join against;
    a bare-link join has no "shared by" to record."""
    owner = owning_info_for_prd(prd_id)
    if owner is None or owner["workspace_id"] is None:
        return None

    from app.db.authcache import invalidate_user
    from app.db.workspaces import upsert_workspace_member

    upsert_workspace_member(owner["workspace_id"], user_id, "member")
    invalidate_user(user_id)
    return owner["workspace_id"]


def prd_access_content(prd_id: int, owner_company_id: str) -> dict:
    """Guest read of a bare-link-accessed PRD's rendered content + evidence +
    tickets — same shape and same company-scoped (never workspace-scoped)
    read `artifact_share.py`'s `/content` route returns, keyed by prd_id
    instead of a share token. Callers MUST have already proven the grant via
    `resolve_prd_access`'s `guest_view` outcome."""
    from app.db.artifact_shares import require_shared_prd, find_prd_evidence
    from app.db.prd_tickets import get_tickets
    from app.db.prds import get_prd_rendered

    prd = require_shared_prd(prd_id, owner_company_id)
    rendered = get_prd_rendered(prd["id"]) or prd
    return {
        "prd": rendered,
        "evidence": find_prd_evidence(rendered),
        "tickets": get_tickets(owner_company_id, prd["id"]),
    }

"""Artifact share-grant primitive — mint / lookup / domain-and-membership
resolution for the internal-link entry gate.

An `artifact_shares` row is a persistent, non-expiring, opaque-uuid4 token
bound to one artifact (currently only 'prd'). `resolve_share_access` is the
SINGLE decision function every route (`/resolve`, `/join`, `/content`) calls
to answer "what can this signed-in user do with this token" — it is always
re-run server-side, never trusted from a client.

Tenancy note: this module's tables use `owner_company_id` / `owner_workspace_id`
(this product's real tenancy model — company_id/workspace_id resolved via
require_company/require_workspace), NOT a Design-Agent-style `workspace_id`
'app'/'demo' environment column. See the ticket's naming-collision note.
"""
from __future__ import annotations

import uuid

from app.db.client import require_client, retry_on_disconnect


def _email_domain(email: str | None) -> str | None:
    """Lowercase domain of `email`, or None if malformed/absent."""
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[-1].strip().lower()
    return domain or None


@retry_on_disconnect
def owning_company_domain(company_id: str) -> str | None:
    """Domain (lowercase, no '@') of `company_id`'s resolved admin/owner
    member's profiles.email. None if unresolvable (no owner/admin member /
    no profile row / malformed email) — callers MUST treat None as "cannot
    verify, deny" (fail closed), never as "no domain requirement".

    Resolution: the `role = 'owner'` member; if none, the earliest-created
    `role = 'admin'` member; if neither exists, unresolvable. NOT the
    earliest-created member regardless of role (that was the pre-revision
    behaviour) — a real `role` column exists and is the honest signal now."""
    client = require_client()
    owners = (
        client.table("company_members")
        .select("user_id")
        .eq("company_id", company_id)
        .eq("role", "owner")
        .limit(1)
        .execute()
        .data
        or []
    )
    user_id = owners[0].get("user_id") if owners else None
    if not user_id:
        admins = (
            client.table("company_members")
            .select("user_id")
            .eq("company_id", company_id)
            .eq("role", "admin")
            .order("created_at", desc=False)
            .limit(1)
            .execute()
            .data
            or []
        )
        user_id = admins[0].get("user_id") if admins else None
    if not user_id:
        return None
    profiles = (
        client.table("profiles")
        .select("email")
        .eq("id", user_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not profiles:
        return None
    return _email_domain(profiles[0].get("email"))


@retry_on_disconnect
def mint_share(
    *,
    artifact_type: str,
    artifact_id: int,
    owner_company_id: str,
    owner_workspace_id: str,
    created_by_user_id: str,
) -> dict:
    """Insert a new artifact_shares row with a fresh uuid4 token. Does NOT
    dedupe against an existing share for the same artifact — a sharer can
    mint multiple links (e.g. to different recipients) and all remain valid
    (no TTL). Returns the inserted row including `token`."""
    client = require_client()
    row = {
        "token": str(uuid.uuid4()),
        "artifact_type": artifact_type,
        "artifact_id": artifact_id,
        "owner_company_id": owner_company_id,
        "owner_workspace_id": owner_workspace_id,
        "created_by_user_id": created_by_user_id,
    }
    inserted = client.table("artifact_shares").insert(row).execute().data
    return inserted[0]


@retry_on_disconnect
def get_share_by_token(token: str) -> dict | None:
    """The raw artifact_shares row for `token` (revoked or not — callers that
    need the identical-404-shape non-disclosure guarantee check
    `revoked_at` themselves). None on no match."""
    client = require_client()
    rows = (
        client.table("artifact_shares")
        .select("*")
        .eq("token", token)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def resolve_share_access(*, token: str, user_id: str, user_email: str | None) -> dict:
    """The single source of truth for what `user_id` may do with the
    artifact behind `token`. Re-run server-side by /resolve, /join, and
    /content — NEVER trusts a client-supplied outcome. Performs no mutation.

    Returns one of:
      {"outcome": "not_found"}
      {"outcome": "blocked", "reason": "different_company", "share": <row>}
      {"outcome": "guest_view", "share": <row>, "sharer_name": str|None,
       "owning_company_name": str|None, "same_company": True}

    Revision note: a caller with ZERO company memberships is now ALWAYS
    blocked, regardless of email domain — sign-in (or any call into this
    function) never grants NEW membership; only a genuine fresh signup
    through `auto_join_company_on_domain_match` does, and that mechanism
    runs BEFORE this function on the next call, so a domain-matched fresh
    signup arrives here already holding a real `company_members` row and
    takes the same_company branch naturally. `same_company` is therefore
    always True on a guest_view outcome now (kept in the return shape for
    self-documentation) — the reason "domain_mismatch" is retired from this
    function entirely; it now only ever originates from the sign-up form's
    client-side gate (web/app/sign-up/page.tsx), which never calls this
    function (no signup attempt is made on a domain mismatch, so there is
    nothing here to resolve).

    `user_email` is accepted for call-site/signature stability across
    /resolve, /join, /content but is no longer used to compute the
    outcome — company membership, not domain, is the only signal now.
    """
    share = get_share_by_token(token)
    if not share or share.get("revoked_at"):
        return {"outcome": "not_found"}

    from app.db.companies import memberships_for_user

    memberships = memberships_for_user(user_id)
    if not memberships:
        return {"outcome": "blocked", "reason": "different_company", "share": share}
    if memberships[0].get("company_id") != share["owner_company_id"]:
        return {"outcome": "blocked", "reason": "different_company", "share": share}

    from app.db.companies import display_name_for_company_id, profile_name_for_user

    return {
        "outcome": "guest_view",
        "share": share,
        "sharer_name": profile_name_for_user(share["created_by_user_id"]),
        "owning_company_name": display_name_for_company_id(share["owner_company_id"]),
        "same_company": True,
    }


@retry_on_disconnect
def auto_join_company_on_domain_match(
    *, token: str, user_id: str, user_email: str | None
) -> str | None:
    """One-shot, signup-time-only mechanism: grants COMPANY membership
    (role='member') — NEVER workspace membership — to a caller whose
    verified email domain matches the share's owning company's admin/owner
    domain (`owning_company_domain`). Intended to run exactly once, right
    after email verification succeeds, from postLoginPath()'s guest branch
    (web/app/lib/supabase/client.ts), BEFORE the next resolve_share_access()
    call — so resolve_share_access's same_company branch then fires
    naturally on that very next call.

    Deliberately separate from resolve_share_access (which performs no
    mutation and must stay safe to call repeatedly): this is the ONE place
    a fresh signup gains company membership through this primitive. A
    caller who already has ANY company membership is a no-op here — this
    only ever grants a FIRST company (the one-company-per-user invariant
    forbids a second anyway), and it is never the mechanism for an existing
    member's sign-in (see resolve_share_access's docstring).

    Returns the granted company_id on success, None on any no-op (missing/
    revoked token, caller already has a company, unresolvable/mismatched
    domain). Never raises — this is a best-effort convenience grant, not a
    security boundary; the real gate is resolve_share_access, re-run
    server-side by /resolve, /join, and /content regardless of whether this
    ran or what it returned.
    """
    share = get_share_by_token(token)
    if not share or share.get("revoked_at"):
        return None

    from app.db.companies import memberships_for_user

    if memberships_for_user(user_id):
        return None

    domain = owning_company_domain(share["owner_company_id"])
    email_domain = _email_domain(user_email)
    if domain is None or not email_domain or email_domain != domain:
        return None

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
    except Exception:  # noqa: BLE001 — already a member (race/double-call)
        pass

    from app.db.authcache import invalidate_user

    invalidate_user(user_id)
    return share["owner_company_id"]


@retry_on_disconnect
def record_join(
    *, share_id: int, joined_user_id: str, joined_company_id: str, joined_workspace_id: str
) -> None:
    """Attribution row for a completed /join. Idempotent: a repeat join for
    an already-joined user is a no-op-success — the unique(share_id,
    joined_user_id) constraint makes a double-click never error."""
    client = require_client()
    try:
        client.table("artifact_share_joins").insert(
            {
                "share_id": share_id,
                "joined_user_id": joined_user_id,
                "joined_company_id": joined_company_id,
                "joined_workspace_id": joined_workspace_id,
            }
        ).execute()
    except Exception:  # noqa: BLE001 — unique-violation on a repeat join is success
        pass


def require_shared_prd(prd_id: int, owner_company_id: str) -> dict:
    """Like `app.deps.ownership.require_owned_prd` but COMPANY-scoped only —
    no workspace check. This is the deliberate guest-read exception this
    ticket's share-grant exists to provide (a same-company-different-
    workspace guest may read a PRD outside any workspace they are a formal
    member of). Callers MUST have already proven the grant via
    `resolve_share_access`'s `guest_view` outcome; this function performs NO
    grant check itself — 404 (never 403) on a missing/foreign PRD, matching
    require_owned_prd's non-disclosure convention."""
    from fastapi import HTTPException

    from app.db import get_brief_by_id
    from app.db.prds import get_prd
    from app.deps.ownership import company_id_for_dataset

    prd = get_prd(prd_id)
    if not prd:
        raise HTTPException(status_code=404, detail="PRD not found")
    brief = get_brief_by_id(prd["brief_id"])
    if not brief:
        raise HTTPException(status_code=404, detail="PRD not found")
    owner = company_id_for_dataset(brief.get("dataset") or "")
    if owner is None or owner != owner_company_id:
        raise HTTPException(status_code=404, detail="PRD not found")
    return prd


def find_prd_evidence(prd_row: dict) -> dict | None:
    """Mirrors GET /v1/prd/{id}/evidence's own lookup: chat-task evidence is
    keyed (brief_id, theme_id); every other PRD source has no evidence doc,
    so this returns None rather than a list (matching the underlying
    per-PRD evidence model — at most one doc, not a collection). Shared by
    both artifact_share.py's and prd_access.py's `/content` routes."""
    from app.db.evidences import find_existing_evidence_for_theme

    theme_id = prd_row.get("theme_id") or ""
    if prd_row.get("source") != "chat" or not str(theme_id).startswith("chat:"):
        return None
    return find_existing_evidence_for_theme(prd_row["brief_id"], theme_id)

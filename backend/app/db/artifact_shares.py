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
    """Domain (lowercase, no '@') of the earliest company_members row's
    profiles.email for `company_id`. None if unresolvable (no members / no
    profile row / malformed email) — callers MUST treat None as "cannot
    verify, deny" (fail closed), never as "no domain requirement".

    Resolution is the earliest-created member ONLY (the company creator —
    stable across later membership churn), not a majority vote."""
    client = require_client()
    members = (
        client.table("company_members")
        .select("user_id")
        .eq("company_id", company_id)
        .order("created_at", desc=False)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not members:
        return None
    user_id = members[0].get("user_id")
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
      {"outcome": "blocked", "reason": "different_company"|"domain_mismatch", "share": <row>}
      {"outcome": "guest_view", "share": <row>, "sharer_name": str|None,
       "owning_company_name": str|None, "same_company": bool}

    `same_company` distinguishes a same-company-different-workspace caller
    (join grants a WORKSPACE membership only) from a fresh domain-matched
    signup (join grants company + workspace membership).
    """
    share = get_share_by_token(token)
    if not share or share.get("revoked_at"):
        return {"outcome": "not_found"}

    from app.db.companies import memberships_for_user

    memberships = memberships_for_user(user_id)
    same_company = False
    if memberships:
        if memberships[0].get("company_id") == share["owner_company_id"]:
            same_company = True
        else:
            return {"outcome": "blocked", "reason": "different_company", "share": share}
    else:
        domain = owning_company_domain(share["owner_company_id"])
        email_domain = _email_domain(user_email)
        if domain is None or not email_domain or email_domain != domain:
            return {"outcome": "blocked", "reason": "domain_mismatch", "share": share}

    from app.db.companies import display_name_for_company_id, profile_name_for_user

    return {
        "outcome": "guest_view",
        "share": share,
        "sharer_name": profile_name_for_user(share["created_by_user_id"]),
        "owning_company_name": display_name_for_company_id(share["owner_company_id"]),
        "same_company": same_company,
    }

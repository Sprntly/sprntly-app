"""Team management — members + workspace_invites lookups + mutations.

Owned by the Settings → Team page (Sprntly_Onboarding_Flow_Spec_v1
§ Settings → Team).

`companies` / `company_members` / `workspace_invites` rows are reached
exclusively through `require_client()` (service-role bypasses RLS — the
caller's route is the access boundary, not the DB policy).
"""
from __future__ import annotations

import uuid

from app.db.client import require_client


def list_company_members(company_id: str) -> list[dict]:
    """All company_members rows for `company_id`.

    Each row: {id, user_id, role, created_at}. The Settings page may
    later join in `profiles` (name/email) for display; routes do that
    on top, not the DB helper.
    """
    client = require_client()
    result = (
        client.table("company_members")
        .select("id, user_id, role, created_at")
        .eq("company_id", company_id)
        .execute()
    )
    return result.data or []


def list_pending_invites(company_id: str) -> list[dict]:
    """Pending workspace_invites for `company_id`.

    Each row: {id, email, role, invited_by, created_at}. "Pending" today
    means "row exists" — accept-flow deletes the row, so there is no
    accepted/declined state column.
    """
    client = require_client()
    result = (
        client.table("workspace_invites")
        .select("id, email, role, invited_by, created_at")
        .eq("company_id", company_id)
        .execute()
    )
    return result.data or []


def get_pending_invite_by_email(*, company_id: str, email: str) -> dict | None:
    """Return the pending invite for (company_id, email) or None."""
    client = require_client()
    rows = (
        client.table("workspace_invites")
        .select("id, email, role, created_at")
        .eq("company_id", company_id)
        .eq("email", email)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def get_invite(invite_id: str) -> dict | None:
    """Fetch a single invite by id, regardless of company."""
    client = require_client()
    rows = (
        client.table("workspace_invites")
        .select("id, company_id, email, role, invited_by, created_at")
        .eq("id", invite_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def member_exists_for_email(*, company_id: str, email: str) -> bool:
    """True iff someone in this company has a profile with this email
    (case-insensitive). Used to enforce 4-A: block invites at create time
    when the invitee is already a member of this company."""
    client = require_client()
    needle = email.strip().lower()
    if not needle:
        return False
    profile_rows = (
        client.table("profiles")
        .select("id, email")
        .execute()
        .data
        or []
    )
    matching_user_ids = {
        p["id"] for p in profile_rows
        if (p.get("email") or "").strip().lower() == needle
    }
    if not matching_user_ids:
        return False
    member_rows = (
        client.table("company_members")
        .select("user_id")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    return any(m["user_id"] in matching_user_ids for m in member_rows)


def create_invite(
    *,
    company_id: str,
    email: str,
    role: str,
    invited_by: str | None,
) -> dict:
    """Insert a workspace_invites row. Caller must have validated email +
    role; this helper performs no validation. Returns the created row.

    Raises if the (company_id, email) unique constraint is violated —
    routes should catch and translate to 409.
    """
    client = require_client()
    iid = uuid.uuid4().hex
    payload = {
        "id": iid,
        "company_id": company_id,
        "email": email,
        "role": role,
        "invited_by": invited_by,
    }
    client.table("workspace_invites").insert(payload).execute()
    # Re-read so we return the actual created_at the DB stamped.
    return get_invite(iid) or payload


def delete_invite(invite_id: str) -> None:
    """Delete an invite by id. No-op if it does not exist."""
    require_client().table("workspace_invites").delete().eq(
        "id", invite_id
    ).execute()


def touch_invite(invite_id: str) -> dict | None:
    """Bump created_at on an invite (placeholder for real re-send semantics
    once email infrastructure exists). Returns the updated row."""
    from datetime import datetime, timezone

    client = require_client()
    new_ts = datetime.now(timezone.utc).isoformat()
    client.table("workspace_invites").update({"created_at": new_ts}).eq(
        "id", invite_id
    ).execute()
    return get_invite(invite_id)

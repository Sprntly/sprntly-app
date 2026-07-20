"""Team management — members + workspace_invites lookups + mutations.

Owned by the Settings → Team page (Sprntly_Onboarding_Flow_Spec_v1
§ Settings → Team).

`companies` / `company_members` / `workspace_invites` rows are reached
exclusively through `require_client()` (service-role bypasses RLS — the
caller's route is the access boundary, not the DB policy).
"""
from __future__ import annotations

import uuid

from app.db.authcache import invalidate_user
from app.db.client import require_client, retry_on_disconnect


def _escape_like(value: str) -> str:
    """Escape LIKE/ILIKE metacharacters so a pattern matches the value
    literally (emails routinely contain `_`, which is a single-char
    wildcard). Backslash first — it is the escape character itself."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@retry_on_disconnect
def list_company_members(company_id: str) -> list[dict]:
    """All company_members rows for `company_id`, enriched with profile
    display data.

    Each row carries:
      id, user_id, role, created_at,
      display_name (full_name → first+last fallback → None),
      email, avatar_url

    The profile fields default to None when no `profiles` row exists for
    the user (brand-new auth.users without a profile, test fixtures
    that skip the profile seed, etc.). Routes pass these through to the
    Settings → Team & roles page (mockup needs name + email + avatar).
    """
    client = require_client()
    members = (
        client.table("company_members")
        .select("id, user_id, role, created_at")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    if not members:
        return []

    user_ids = [m["user_id"] for m in members]
    profiles_resp = (
        client.table("profiles")
        .select("id, email, full_name, first_name, last_name, avatar_url")
        .in_("id", user_ids)
        .execute()
    )
    by_id = {p["id"]: p for p in (profiles_resp.data or [])}

    enriched: list[dict] = []
    for m in members:
        prof = by_id.get(m["user_id"]) or {}
        full = (prof.get("full_name") or "").strip()
        first = (prof.get("first_name") or "").strip()
        last = (prof.get("last_name") or "").strip()
        display = full or (f"{first} {last}".strip() if (first or last) else None) or None
        enriched.append(
            {
                "id": m.get("id"),
                "user_id": m["user_id"],
                "role": m.get("role"),
                "created_at": m.get("created_at"),
                "display_name": display,
                "email": prof.get("email"),
                "avatar_url": prof.get("avatar_url"),
            }
        )
    return enriched


@retry_on_disconnect
def list_pending_invites(company_id: str) -> list[dict]:
    """Pending workspace_invites for `company_id`.

    Each row: {id, email, role, invited_by, created_at, workspace_ids}.
    "Pending" today means "row exists" — accept-flow deletes the row, so
    there is no accepted/declined state column.
    """
    client = require_client()
    result = (
        client.table("workspace_invites")
        .select("id, email, role, invited_by, created_at, workspace_ids")
        .eq("company_id", company_id)
        .execute()
    )
    return result.data or []


@retry_on_disconnect
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


@retry_on_disconnect
def get_invite(invite_id: str) -> dict | None:
    """Fetch a single invite by id, regardless of company."""
    client = require_client()
    rows = (
        client.table("workspace_invites")
        .select("id, company_id, email, role, invited_by, created_at, workspace_ids")
        .eq("id", invite_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


@retry_on_disconnect
def member_exists_for_email(*, company_id: str, email: str) -> bool:
    """True iff someone in this company has a profile with this email
    (case-insensitive — `.ilike` on the escaped needle, no wildcards). Used
    to enforce 4-A: block invites at create time when the invitee is already
    a member of this company."""
    client = require_client()
    needle = email.strip().lower()
    if not needle:
        return False
    profile_rows = (
        client.table("profiles")
        .select("id")
        .ilike("email", _escape_like(needle))
        .execute()
        .data
        or []
    )
    matching_user_ids = [p["id"] for p in profile_rows]
    if not matching_user_ids:
        return False
    member_rows = (
        client.table("company_members")
        .select("user_id")
        .eq("company_id", company_id)
        .in_("user_id", matching_user_ids)
        .limit(1)
        .execute()
        .data
        or []
    )
    return bool(member_rows)


@retry_on_disconnect
def email_belongs_to_other_company(*, company_id: str, email: str) -> bool:
    """True iff this email's profile is a member of a company OTHER than
    `company_id` (case-insensitive). Used to refuse invites at send time:
    the one-user-one-company invariant means such an invitee could never
    accept, so the inviter gets the clear reason immediately instead of an
    invite that dangles forever (its accept would 409)."""
    client = require_client()
    needle = email.strip().lower()
    if not needle:
        return False
    profile_rows = (
        client.table("profiles")
        .select("id")
        .ilike("email", _escape_like(needle))
        .execute()
        .data
        or []
    )
    matching_user_ids = [p["id"] for p in profile_rows]
    if not matching_user_ids:
        return False
    member_rows = (
        client.table("company_members")
        .select("user_id")
        .in_("user_id", matching_user_ids)
        .neq("company_id", company_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return bool(member_rows)


def create_invite(
    *,
    company_id: str,
    email: str,
    role: str,
    invited_by: str | None,
    workspace_ids: list[str] | None = None,
    job_role: str | None = None,
) -> dict:
    """Insert a workspace_invites row. Caller must have validated email +
    role + workspace ownership; this helper performs no validation. Returns
    the created row.

    `workspace_ids` are the workspaces the invitee joins on accept. Empty /
    None means "the company's default workspace, resolved at ACCEPT time"
    (not stored — so an invite created before extra workspaces exist still
    lands somewhere sensible).

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
        "workspace_ids": workspace_ids or [],
        # The invitee's JOB role (Data Science, Engineer…) from the v6 invite
        # step — display-only, distinct from the permission `role`.
        "job_role": job_role,
    }
    client.table("workspace_invites").insert(payload).execute()
    # Re-read so we return the actual created_at the DB stamped.
    return get_invite(iid) or payload


def delete_invite(invite_id: str) -> None:
    """Delete an invite by id. No-op if it does not exist."""
    require_client().table("workspace_invites").delete().eq(
        "id", invite_id
    ).execute()


@retry_on_disconnect
def get_member(*, company_id: str, user_id: str) -> dict | None:
    client = require_client()
    rows = (
        client.table("company_members")
        .select("id, user_id, role")
        .eq("company_id", company_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def update_member_role(*, company_id: str, user_id: str, role: str) -> dict | None:
    require_client().table("company_members").update({"role": role}).eq(
        "company_id", company_id
    ).eq("user_id", user_id).execute()
    # Invalidate at the write site so every caller (route, test, future code) —
    # not only the invalidating routes — sees the fresh role on the next read.
    invalidate_user(user_id)
    return get_member(company_id=company_id, user_id=user_id)


def delete_member(*, company_id: str, user_id: str) -> None:
    require_client().table("company_members").delete().eq(
        "company_id", company_id
    ).eq("user_id", user_id).execute()
    invalidate_user(user_id)


@retry_on_disconnect
def count_owners(company_id: str) -> int:
    """How many `owner`-role members in a given company."""
    rows = (
        require_client()
        .table("company_members")
        .select("user_id, role")
        .eq("company_id", company_id)
        .eq("role", "owner")
        .execute()
        .data
        or []
    )
    return len(rows)


@retry_on_disconnect
def find_pending_invite_for_email_anywhere(email: str) -> dict | None:
    """Return the earliest pending invite (across all companies) for `email`,
    or None. Used by the auto-accept-on-sign-in path so the user's first
    matching invite becomes their membership.

    Service-role read; the caller (the auth route) is responsible for the
    user-identity check (it only invokes this for the signed-in caller's
    own verified email)."""
    client = require_client()
    needle = email.strip().lower()
    if not needle:
        return None
    rows = (
        client.table("workspace_invites")
        .select("id, company_id, email, role, created_at, workspace_ids")
        .eq("email", needle)
        .execute()
        .data
        or []
    )
    if not rows:
        return None
    rows.sort(key=lambda r: r.get("created_at") or "")
    return rows[0]


def _workspace_role_for_invite(role: str) -> str:
    """Invite role → workspace_members role. 'owner' never appears on invites
    (DB CHECK); admin/member/viewer map straight through."""
    return role if role in ("admin", "member", "viewer") else "member"


def _grant_invite_workspaces(
    *, company_id: str, user_id: str, role: str, workspace_ids: list | None
) -> list[str]:
    """Materialise an invite's workspace grants into workspace_members rows.

    Stored ids are validated at accept time — an id that no longer exists or
    belongs to another company is skipped (invites are ephemeral; a workspace
    deleted between invite and accept must not break the accept). An empty
    surviving set falls back to the company's default workspace so every
    accepted member lands somewhere. Returns the granted workspace ids.
    """
    from app.db.workspaces import (
        ensure_default_workspace,
        get_workspace,
        upsert_workspace_member,
    )

    ws_role = _workspace_role_for_invite(role)
    granted: list[str] = []
    for wid in workspace_ids or []:
        ws = get_workspace(str(wid))
        if not ws or ws.get("company_id") != company_id:
            continue
        upsert_workspace_member(ws["id"], user_id, ws_role)
        granted.append(ws["id"])
    if not granted:
        default = ensure_default_workspace(company_id)
        upsert_workspace_member(default["id"], user_id, ws_role)
        granted.append(default["id"])
    return granted


def accept_invite_for_user(
    *,
    user_id: str,
    email: str,
) -> dict | None:
    """Materialise a pending invite into a company_members row for `user_id`
    plus workspace_members rows for the invite's target workspaces.

    Returns {company_id, role, workspace_ids} on success, or None if no
    matching invite. Raises ValueError("already_in_company") if the user
    already belongs to a different company (one-user-one-company invariant).

    The caller's email must already be verified (Supabase Auth handles
    this); this helper does not re-verify.
    """
    invite = find_pending_invite_for_email_anywhere(email)
    if not invite:
        return None

    company_id = invite["company_id"]
    role = invite.get("role") or "member"

    # Check the one-user-one-company invariant before we attempt insertion.
    from app.db.companies import memberships_for_user

    existing = memberships_for_user(user_id)
    if existing:
        already = existing[0].get("company_id")
        if already == company_id:
            # Same-company duplicate: idempotent accept. Still grant the
            # invite's workspaces (a SECOND invite to more workspaces must
            # work), then delete the dangling invite row.
            granted = _grant_invite_workspaces(
                company_id=company_id,
                user_id=user_id,
                role=role,
                workspace_ids=invite.get("workspace_ids"),
            )
            delete_invite(invite["id"])
            return {
                "company_id": company_id,
                "role": existing[0].get("role"),
                "workspace_ids": granted,
            }
        raise ValueError("already_in_company")

    client = require_client()
    client.table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": company_id,
            "user_id": user_id,
            "role": role,
        }
    ).execute()
    granted = _grant_invite_workspaces(
        company_id=company_id,
        user_id=user_id,
        role=role,
        workspace_ids=invite.get("workspace_ids"),
    )
    delete_invite(invite["id"])
    return {"company_id": company_id, "role": role, "workspace_ids": granted}


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

"""Organization invites (staff admin panel).

Sprntly staff invite a customer organization by email; the row snapshots the
deal's entitlements (seat_limit, prototype_enabled, use_platform_key,
feature_flags). When the invitee signs up and creates their company during
onboarding, the claim route copies the snapshot onto the companies row and
marks the invite accepted. Service-role only — the table has RLS enabled with
no policies, so /v1/staff and the claim route are the sole access paths.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.db.client import require_client, retry_on_disconnect

_COLUMNS = (
    "id, email, company_name, invited_by, seat_limit, prototype_enabled, "
    "use_platform_key, feature_flags, status, company_id, created_at, "
    "accepted_at"
)


@retry_on_disconnect
def list_org_invites() -> list[dict]:
    """All org invites, newest first (pending and settled — the panel shows
    history, the UI groups by status)."""
    rows = (
        require_client()
        .table("org_invites")
        .select(_COLUMNS)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    return rows


@retry_on_disconnect
def get_org_invite(invite_id: str) -> dict | None:
    rows = (
        require_client()
        .table("org_invites")
        .select(_COLUMNS)
        .eq("id", invite_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


@retry_on_disconnect
def get_pending_org_invite_by_email(email: str) -> dict | None:
    """The pending invite for an email (unique per the partial index)."""
    rows = (
        require_client()
        .table("org_invites")
        .select(_COLUMNS)
        .eq("email", email.strip().lower())
        .eq("status", "pending")
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


@retry_on_disconnect
def create_org_invite(
    *,
    email: str,
    company_name: str,
    invited_by: str | None,
    seat_limit: int | None,
    prototype_enabled: bool,
    use_platform_key: bool,
    feature_flags: dict,
) -> dict:
    """Insert a pending org invite. Caller validates email/company_name and
    pre-checks the one-pending-per-email rule (the partial unique index is
    the backstop). Returns the created row."""
    client = require_client()
    iid = str(uuid.uuid4())
    payload = {
        "id": iid,
        "email": email.strip().lower(),
        "company_name": company_name.strip(),
        "invited_by": invited_by,
        "seat_limit": seat_limit,
        "prototype_enabled": prototype_enabled,
        "use_platform_key": use_platform_key,
        "feature_flags": feature_flags or {},
        "status": "pending",
    }
    client.table("org_invites").insert(payload).execute()
    return get_org_invite(iid) or payload


@retry_on_disconnect
def revoke_org_invite(invite_id: str) -> None:
    """Mark a pending invite revoked (kept for history, frees the email for
    a fresh invite). No-op on missing/settled rows."""
    require_client().table("org_invites").update({"status": "revoked"}).eq(
        "id", invite_id
    ).eq("status", "pending").execute()


@retry_on_disconnect
def mark_org_invite_accepted(invite_id: str, *, company_id: str) -> None:
    """Settle a pending invite onto the company that claimed it."""
    require_client().table("org_invites").update(
        {
            "status": "accepted",
            "company_id": company_id,
            "accepted_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", invite_id).eq("status", "pending").execute()

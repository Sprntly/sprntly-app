"""Team management — members + workspace_invites lookups.

Owned by the Settings → Team page (Sprntly_Onboarding_Flow_Spec_v1
§ Settings → Team).

`companies` / `company_members` / `workspace_invites` rows are reached
exclusively through `require_client()` (service-role bypasses RLS — the
caller's route is the access boundary, not the DB policy).
"""
from __future__ import annotations

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

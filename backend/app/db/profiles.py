"""Lookups against the `profiles` table (id = auth user_id, plus email/name).

Service-role access via require_client(); used by trusted server-side code
(the scheduler, drip, team enrichment) to resolve a user's email from their id.
"""
from __future__ import annotations

from app.db.client import require_client


def emails_for_user_ids(user_ids: list[str | None]) -> dict[str, str]:
    """Map each user_id → email from `profiles`.

    Unknown ids, and ids whose profile has no email, are simply absent from the
    result (callers fall back as they see fit). Duplicate/None ids are
    de-duplicated; empty input returns an empty dict (no query).
    """
    ids = [u for u in dict.fromkeys(user_ids) if u]
    if not ids:
        return {}
    rows = (
        require_client()
        .table("profiles")
        .select("id, email")
        .in_("id", ids)
        .execute()
        .data
        or []
    )
    return {r["id"]: r["email"] for r in rows if r.get("id") and r.get("email")}

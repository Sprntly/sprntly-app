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


def first_name_for_user(user_id: str) -> str:
    """A user's first name from `profiles`, for personalising the welcome
    email. Prefers `first_name`; falls back to the first token of `full_name`;
    returns "" when neither is set (the caller substitutes a friendly default).
    """
    if not user_id:
        return ""
    rows = (
        require_client()
        .table("profiles")
        .select("first_name, full_name")
        .eq("id", user_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        return ""
    prof = rows[0]
    first = (prof.get("first_name") or "").strip()
    if first:
        return first
    full = (prof.get("full_name") or "").strip()
    return full.split()[0] if full else ""


def first_name_for_email(email: str) -> str:
    """A user's first name looked up by email (an invitee may already have a
    profile). Prefers `first_name`, falls back to the first token of
    `full_name`; "" when there's no matching profile (a brand-new invitee) so
    the caller substitutes a friendly default."""
    needle = (email or "").strip().lower()
    if not needle:
        return ""
    rows = (
        require_client()
        .table("profiles")
        .select("first_name, full_name")
        .ilike("email", needle)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        return ""
    prof = rows[0]
    first = (prof.get("first_name") or "").strip()
    if first:
        return first
    full = (prof.get("full_name") or "").strip()
    return full.split()[0] if full else ""

"""Company membership lookups (tenancy).

`companies` / `company_members` are owned by the onboarding flow (see
migration 20260525140000_companies_and_profiles.sql). This module only
*reads* membership — used by `app.auth.require_company` to resolve the
authenticated user's active company (the tenant everything else scopes by).
"""
from __future__ import annotations

from app.db.client import require_client, retry_on_disconnect


@retry_on_disconnect
def list_companies() -> list[dict]:
    """All companies (tenants), shaped {id, slug, display_name,
    notification_settings}.

    Used by the scheduler to iterate every tenant for the KG-synthesis cycle and
    to read each company's configured timezone (notification_settings.timezone)
    so the weekly brief fires Monday 09:00 in the company's local time.

    notification_settings is selected best-effort: the fake test Supabase + any
    older schema without the column would 400 on an explicit select, so we fall
    back to the legacy three-column select and default notification_settings to
    {} (resolve_timezone then uses the UTC default). The live schema has the
    JSONB column (20260525150000_onboarding_workspace.sql), so prod returns it.
    """
    client = require_client()
    try:
        result = (
            client.table("companies")
            .select("id, slug, display_name, notification_settings")
            .order("slug", desc=False)
            .execute()
        )
        return result.data or []
    except Exception:
        result = (
            client.table("companies")
            .select("id, slug, display_name")
            .order("slug", desc=False)
            .execute()
        )
        rows = result.data or []
        for row in rows:
            row.setdefault("notification_settings", {})
        return rows


@retry_on_disconnect
def company_id_for_slug(slug: str) -> str | None:
    """Resolve a company slug → company id (the KG enterprise_id). None if
    no company owns the slug."""
    client = require_client()
    result = (
        client.table("companies")
        .select("id")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    return result.data[0]["id"] if result.data else None


@retry_on_disconnect
def display_name_for_slug(slug: str) -> str | None:
    """Resolve a company slug → its human-readable display name. None if no
    company owns the slug (e.g. legacy demo datasets)."""
    client = require_client()
    result = (
        client.table("companies")
        .select("display_name")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    return result.data[0]["display_name"] if result.data else None


@retry_on_disconnect
def slug_for_company_id(company_id: str) -> str | None:
    """Resolve a company id → its slug (the dataset slug). None if not found."""
    client = require_client()
    result = (
        client.table("companies")
        .select("slug")
        .eq("id", company_id)
        .limit(1)
        .execute()
    )
    return result.data[0]["slug"] if result.data else None


@retry_on_disconnect
def get_notification_settings(company_id: str) -> dict:
    """Read a company's `notification_settings` JSONB (per-company delivery
    config). Returns `{}` when the company is missing or the column is unset —
    callers apply their own defaults (e.g. email_enabled, recipients).

    Shape consumed by brief email delivery:
      {"email_enabled": bool, "email_recipients": ["a@x.com", ...]}
    A missing `email_recipients` ⇒ default to the company's members' emails.
    """
    client = require_client()
    result = (
        client.table("companies")
        .select("notification_settings")
        .eq("id", company_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        return {}
    return result.data[0].get("notification_settings") or {}


@retry_on_disconnect
def memberships_for_user(user_id: str) -> list[dict]:
    """All company memberships for a Supabase user id.

    Returns rows shaped {company_id, role}. Empty list ⇒ the user has no
    company yet (e.g. mid-onboarding).
    """
    client = require_client()
    result = (
        client.table("company_members")
        .select("company_id, role")
        .eq("user_id", user_id)
        .execute()
    )
    return result.data or []

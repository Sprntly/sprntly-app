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
    """All companies (tenants), shaped {id, slug, display_name}.

    Used by the scheduler to iterate every tenant for the KG-synthesis cycle.
    """
    client = require_client()
    result = (
        client.table("companies")
        .select("id, slug, display_name")
        .order("slug", desc=False)
        .execute()
    )
    return result.data or []


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

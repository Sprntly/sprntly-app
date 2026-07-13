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
    notification_settings, owner_timezone}.

    Used by the scheduler to iterate every tenant for the KG-synthesis cycle and
    to read each company owner's timezone (profiles.timezone, resolved via the
    company's `owner`-role member) so the weekly brief fires Monday 06:00 in the
    owner's local time.

    notification_settings is selected best-effort: the fake test Supabase + any
    older schema without the column would 400 on an explicit select, so we fall
    back to the legacy three-column select and default notification_settings to
    {} (resolve_timezone then uses the UTC default). The live schema has the
    JSONB column (20260525150000_onboarding_workspace.sql), so prod returns it.

    owner_timezone is likewise best-effort: any failure (older schema, fake test
    client) leaves it None and the scheduler falls back to UTC.
    """
    client = require_client()
    try:
        result = (
            client.table("companies")
            .select("id, slug, display_name, notification_settings")
            .order("slug", desc=False)
            .execute()
        )
        rows = result.data or []
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
    return _attach_owner_timezones(rows)


def _attach_owner_timezones(companies: list[dict]) -> list[dict]:
    """Best-effort: set ``owner_timezone`` (IANA str or None) on each company.

    Resolves each company's `owner`-role member → that user's profiles.timezone
    in two bulk queries (no per-company round-trips). Any failure — legacy schema
    without profiles.timezone, the fake test Supabase, an empty list — leaves
    ``owner_timezone`` as None so the scheduler simply falls back to UTC.
    """
    for company in companies:
        company.setdefault("owner_timezone", None)
    company_ids = [c["id"] for c in companies if c.get("id")]
    if not company_ids:
        return companies

    try:
        client = require_client()
        owners = (
            client.table("company_members")
            .select("company_id, user_id")
            .eq("role", "owner")
            .in_("company_id", company_ids)
            .execute()
            .data
            or []
        )
        owner_user_by_company = {o["company_id"]: o["user_id"] for o in owners}
        user_ids = list({uid for uid in owner_user_by_company.values() if uid})
        tz_by_user: dict[str, str | None] = {}
        if user_ids:
            profiles = (
                client.table("profiles")
                .select("id, timezone")
                .in_("id", user_ids)
                .execute()
                .data
                or []
            )
            tz_by_user = {p["id"]: p.get("timezone") for p in profiles}
        for company in companies:
            owner = owner_user_by_company.get(company.get("id"))
            if owner:
                company["owner_timezone"] = tz_by_user.get(owner)
    except Exception:  # noqa: BLE001 — degrade to UTC, never wedge the scheduler
        pass
    return companies


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
def owner_name_for_company(company_id: str | None) -> str | None:
    """Resolve a company's account owner (or, failing that, an admin) → that
    user's human name (profiles.full_name, else "first last"). None when there's
    no company, no owner/admin member, or no name on file.

    Used as the PRD byline fallback for background/brief-generated PRDs, which
    carry no logged-in identity — the owner is the account's canonical author.
    Best-effort: any read failure returns None so generation never wedges on it.
    """
    if not company_id:
        return None
    try:
        client = require_client()
        members = (
            client.table("company_members")
            .select("user_id, role")
            .eq("company_id", company_id)
            .in_("role", ["owner", "admin"])
            .execute()
            .data
            or []
        )
        if not members:
            return None
        # Prefer the owner; fall back to any admin.
        chosen = next((m for m in members if m.get("role") == "owner"), members[0])
        user_id = chosen.get("user_id")
        if not user_id:
            return None
        profiles = (
            client.table("profiles")
            .select("full_name, first_name, last_name")
            .eq("id", user_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not profiles:
            return None
        p = profiles[0]
        return p.get("full_name") or f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or None
    except Exception:  # noqa: BLE001 — byline fallback must never break generation
        return None


@retry_on_disconnect
def display_name_for_user(user_id: str | None) -> str | None:
    """Best human label for a specific user: profiles.full_name → "first last"
    → email → None. Unlike owner_name_for_company (which resolves the account
    owner), this is scoped to the exact user — used to attribute MCP ticket
    comments to the token owner instead of a generic "mcp". Best-effort: any
    read failure returns None so the comment write never wedges on it."""
    if not user_id:
        return None
    try:
        profiles = (
            require_client()
            .table("profiles")
            .select("full_name, first_name, last_name, email")
            .eq("id", user_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not profiles:
            return None
        p = profiles[0]
        return (
            p.get("full_name")
            or f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip()
            or p.get("email")
            or None
        )
    except Exception:  # noqa: BLE001 — attribution must never break the write
        return None


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
def display_name_for_company_id(company_id: str) -> str | None:
    """Resolve a company id → its display_name. None if not found. Mirrors
    slug_for_company_id (id-keyed) but selects display_name instead of slug —
    added for the cosmetic /p/<company_display_slug>/<feature_slug>/<token>
    URL segment. companies.slug stays off-limits for this (opaque tenant key,
    see slug_for_company_id's callers)."""
    client = require_client()
    result = (
        client.table("companies")
        .select("display_name")
        .eq("id", company_id)
        .limit(1)
        .execute()
    )
    return result.data[0]["display_name"] if result.data else None


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
def get_company_llm_config(company_id: str) -> tuple[str | None, bool, bool]:
    """Everything the key resolver (app.llm_keys) needs in one read:

      (encrypted_key_or_None, use_platform_key, onboarding_complete)

    `onboarding_complete` is `companies.onboarding_completed_at IS NOT NULL`. A
    missing company row returns (None, False, False) — treated as still
    onboarding (lenient: platform allowed) by the resolver."""
    client = require_client()
    result = (
        client.table("companies")
        .select("llm_api_key_encrypted, use_platform_key, onboarding_completed_at")
        .eq("id", company_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        return (None, False, False)
    row = result.data[0]
    cipher = row.get("llm_api_key_encrypted") or None
    use_platform = bool(row.get("use_platform_key"))
    onboarding_complete = row.get("onboarding_completed_at") is not None
    return (cipher, use_platform, onboarding_complete)


@retry_on_disconnect
def get_llm_api_key_encrypted(company_id: str) -> str | None:
    """Read a company's Fernet-encrypted Claude API key ciphertext, or None when
    unset. Decryption happens at the point of use (app.llm_keys); this returns
    the raw ciphertext exactly as stored."""
    client = require_client()
    result = (
        client.table("companies")
        .select("llm_api_key_encrypted")
        .eq("id", company_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return result.data[0].get("llm_api_key_encrypted") or None


@retry_on_disconnect
def set_llm_api_key_encrypted(company_id: str, cipher: str) -> None:
    """Store a company's Fernet-encrypted Claude API key (ciphertext only — the
    column never holds plaintext)."""
    client = require_client()
    client.table("companies").update(
        {"llm_api_key_encrypted": cipher}
    ).eq("id", company_id).execute()


@retry_on_disconnect
def clear_llm_api_key(company_id: str) -> None:
    """Remove a company's Claude API key (revert to the platform key)."""
    client = require_client()
    client.table("companies").update(
        {"llm_api_key_encrypted": None}
    ).eq("id", company_id).execute()


# Entitlement columns managed by the staff admin panel (plus use_platform_key,
# which predates it). seat_limit NULL ⇒ unlimited.
_ENTITLEMENT_FIELDS = (
    "seat_limit",
    "prototype_enabled",
    "use_platform_key",
    "feature_flags",
)


@retry_on_disconnect
def get_company_entitlements(company_id: str) -> dict | None:
    """A company's entitlement snapshot for the staff panel, or None when the
    company doesn't exist. `llm_key_configured` says whether a BYOK key is
    stored (never the key itself)."""
    rows = (
        require_client()
        .table("companies")
        .select(
            "id, slug, display_name, created_at, seat_limit, "
            "prototype_enabled, use_platform_key, feature_flags, "
            "llm_api_key_encrypted"
        )
        .eq("id", company_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        return None
    return _entitlement_row(rows[0])


def _entitlement_row(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "slug": row.get("slug"),
        "display_name": row.get("display_name"),
        "created_at": row.get("created_at"),
        "seat_limit": row.get("seat_limit"),
        "prototype_enabled": bool(row.get("prototype_enabled")),
        "use_platform_key": bool(row.get("use_platform_key")),
        "feature_flags": row.get("feature_flags") or {},
        "llm_key_configured": bool(row.get("llm_api_key_encrypted")),
    }


@retry_on_disconnect
def update_company_entitlements(company_id: str, patch: dict) -> None:
    """Apply a staff-panel entitlement change. Only whitelisted columns are
    written — callers pass a pre-validated partial dict."""
    payload = {k: v for k, v in patch.items() if k in _ENTITLEMENT_FIELDS}
    if not payload:
        return
    require_client().table("companies").update(payload).eq(
        "id", company_id
    ).execute()


@retry_on_disconnect
def list_companies_for_staff() -> list[dict]:
    """Every company with its entitlement snapshot + member/pending-invite
    counts, for the staff admin panel's organizations table."""
    client = require_client()
    rows = (
        client.table("companies")
        .select(
            "id, slug, display_name, created_at, seat_limit, "
            "prototype_enabled, use_platform_key, feature_flags, "
            "llm_api_key_encrypted"
        )
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    companies = [_entitlement_row(r) for r in rows]
    # Bulk member / pending-invite counts (two reads, counted in-process —
    # fine at panel scale, and the fake test client has no group-by).
    member_counts: dict[str, int] = {}
    invite_counts: dict[str, int] = {}
    try:
        for m in (
            client.table("company_members").select("company_id").execute().data
            or []
        ):
            cid = m.get("company_id")
            member_counts[cid] = member_counts.get(cid, 0) + 1
        for i in (
            client.table("workspace_invites").select("company_id").execute().data
            or []
        ):
            cid = i.get("company_id")
            invite_counts[cid] = invite_counts.get(cid, 0) + 1
    except Exception:  # noqa: BLE001 — counts are display-only, never 500 the panel
        pass
    for c in companies:
        c["member_count"] = member_counts.get(c["id"], 0)
        c["pending_invite_count"] = invite_counts.get(c["id"], 0)
    return companies


@retry_on_disconnect
def get_seat_limit(company_id: str) -> int | None:
    """A company's seat limit, or None for unlimited (unset column, missing
    row, or a legacy schema without the column)."""
    try:
        rows = (
            require_client()
            .table("companies")
            .select("seat_limit")
            .eq("id", company_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001 — legacy schema/fake client ⇒ unlimited
        return None
    if not rows:
        return None
    limit = rows[0].get("seat_limit")
    return int(limit) if limit is not None else None


@retry_on_disconnect
def prototype_enabled_for_company(company_id: str) -> bool:
    """Per-company design-agent (prototype) gate. Lenient on READ FAILURE
    only (legacy schema without the column, fake test client ⇒ True, matching
    the grandfather backfill); an explicit false in the row is respected. The
    global DESIGN_AGENT_ENABLED env var remains the master switch upstream."""
    try:
        rows = (
            require_client()
            .table("companies")
            .select("prototype_enabled")
            .eq("id", company_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001
        return True
    if not rows:
        return True
    value = rows[0].get("prototype_enabled")
    if value is None:
        return True
    return bool(value)


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

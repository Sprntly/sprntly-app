"""Company membership lookups (tenancy).

`companies` / `company_members` are owned by the onboarding flow (see
migration 20260525140000_companies_and_profiles.sql). This module only
*reads* membership — used by `app.auth.require_company` to resolve the
authenticated user's active company (the tenant everything else scopes by).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from app.db.authcache import memberships_cache, profile_name_cache
from app.db.client import require_client, retry_on_disconnect
from app.llm_providers import (
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    normalize_provider,
)

logger = logging.getLogger(__name__)


@retry_on_disconnect
def list_companies() -> list[dict]:
    """All companies (tenants), shaped {id, slug, display_name,
    notification_settings, feature_flags, owner_timezone}.

    Used by the scheduler to iterate every tenant for the KG-synthesis cycle and
    to read each company owner's timezone (profiles.timezone, resolved via the
    company's `owner`-role member) so the Top Insights brief fires Monday 06:00 in the
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
            .select("id, slug, display_name, notification_settings, feature_flags")
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
    for row in rows:
        # feature_flags is best-effort like notification_settings: an older
        # schema (fallback select) or a NULL column defaults to {} — which the
        # entitlement resolvers treat as everything-ON (grandfathering).
        if not isinstance(row.get("feature_flags"), dict):
            row["feature_flags"] = {}
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
def _profile_row_for_user(user_id: str) -> dict | None:
    """The user's profiles name/email fields, via `profile_name_cache`.

    Caches the ROW (not a derived name) so both name-derivation chains —
    `display_name_for_user` (email fallback) and `profile_name_for_user`
    (no email fallback) — share one cache entry. A missing profile is not
    cached (TTLMap can't distinguish a stored None from a miss); that's
    fine — cosmetic lookups only, and profile-less users are rare."""
    cached = profile_name_cache.get(user_id)
    if cached is not None:
        return cached
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
    row = profiles[0] if profiles else None
    if row is not None:
        profile_name_cache.set(user_id, row)
    return row


def display_name_for_user(user_id: str | None) -> str | None:
    """Best human label for a specific user: profiles.full_name → "first last"
    → email → None. Unlike owner_name_for_company (which resolves the account
    owner), this is scoped to the exact user — used to attribute MCP ticket
    comments to the token owner instead of a generic "mcp". Best-effort: any
    read failure returns None so the comment write never wedges on it."""
    if not user_id:
        return None
    try:
        p = _profile_row_for_user(user_id)
        if not p:
            return None
        return (
            p.get("full_name")
            or f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip()
            or p.get("email")
            or None
        )
    except Exception:  # noqa: BLE001 — attribution must never break the write
        return None


def profile_name_for_user(user_id: str | None) -> str | None:
    """The user's display name for `require_company` (CompanyContext.user_name):
    profiles.full_name → "first last" → None. Deliberately NO email fallback —
    callers (PRD bylines et al.) fall back to user_email themselves when they
    want it. Best-effort: any read failure returns None so name resolution
    never fails the request."""
    if not user_id:
        return None
    try:
        p = _profile_row_for_user(user_id)
        if not p:
            return None
        return (
            p.get("full_name")
            or f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip()
            or None
        )
    except Exception:  # noqa: BLE001 — name resolution must never fail the request
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


# Where each provider's Fernet-encrypted key lives on `companies`. Keyed by the
# value stored in `companies.llm_provider` (see app.llm_providers for the
# provider identities themselves), so adding a provider is one entry here plus a
# client factory — no call site changes.
_KEY_COLUMNS = {
    PROVIDER_ANTHROPIC: "llm_api_key_encrypted",
    PROVIDER_OPENAI: "openai_api_key_encrypted",
}


def _key_column(provider: str) -> str:
    """The key column for `provider`, defaulting to Anthropic's.

    Falls back rather than raising: `llm_provider` is constrained in the schema,
    but this sits on the LLM hot path and an unexpected value must degrade to
    today's behaviour rather than take generation down.
    """
    return _KEY_COLUMNS.get(provider, _KEY_COLUMNS[PROVIDER_ANTHROPIC])


@dataclass(frozen=True)
class CompanyLLMConfig:
    """Everything the key resolver (app.llm_keys) needs, in one read.

    Was a 3-tuple until the OpenAI provider landed. Both ciphertexts are carried
    whichever provider is active, deliberately: an admin can hold a Claude key
    and an OpenAI key at once and flip `provider` between them, so the resolver
    reads the pair and picks — it never has to go back to the DB on a switch.
    """

    provider: str = PROVIDER_ANTHROPIC
    anthropic_cipher: str | None = None
    openai_cipher: str | None = None
    use_platform_key: bool = False
    onboarding_complete: bool = False

    def cipher_for(self, provider: str) -> str | None:
        return (
            self.openai_cipher
            if provider == PROVIDER_OPENAI
            else self.anthropic_cipher
        )


@retry_on_disconnect
def get_company_llm_config(company_id: str) -> CompanyLLMConfig:
    """The company's LLM posture: active provider, both keys, billing flags.

    `onboarding_complete` is `companies.onboarding_completed_at IS NOT NULL`. A
    missing company row returns the all-defaults config — treated as still
    onboarding (lenient: platform allowed) by the resolver. An id that isn't
    even a valid UUID (a legacy dataset slug or telemetry tag bound by an older
    gateway caller) is definitionally not a company: it gets the same lenient
    missing-row answer instead of erroring the uuid-typed query below — the
    error would otherwise fail the caller's whole LLM call (this is exactly
    what silently killed PRD input-question extraction)."""
    try:
        uuid.UUID(company_id)
    except (ValueError, TypeError):
        logger.warning(
            "get_company_llm_config: non-company id %r — treating as missing "
            "(platform-key posture). Fix the caller to bind a company id.",
            company_id,
        )
        return CompanyLLMConfig()
    client = require_client()
    result = (
        client.table("companies")
        .select(
            "llm_api_key_encrypted, openai_api_key_encrypted, llm_provider, "
            "use_platform_key, onboarding_completed_at"
        )
        .eq("id", company_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        return CompanyLLMConfig()
    row = result.data[0]
    return CompanyLLMConfig(
        # A pre-migration row read by a newer process has no value here; treat
        # it as Anthropic so the column's absence can never move a workspace
        # onto a provider nobody chose.
        provider=normalize_provider(row.get("llm_provider")),
        anthropic_cipher=row.get("llm_api_key_encrypted") or None,
        openai_cipher=row.get("openai_api_key_encrypted") or None,
        use_platform_key=bool(row.get("use_platform_key")),
        onboarding_complete=row.get("onboarding_completed_at") is not None,
    )


@retry_on_disconnect
def get_llm_api_key_encrypted(
    company_id: str, provider: str = PROVIDER_ANTHROPIC
) -> str | None:
    """Read a company's Fernet-encrypted key ciphertext for `provider`, or None
    when unset. Decryption happens at the point of use (app.llm_keys); this
    returns the raw ciphertext exactly as stored."""
    column = _key_column(provider)
    client = require_client()
    result = (
        client.table("companies")
        .select(column)
        .eq("id", company_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return result.data[0].get(column) or None


@retry_on_disconnect
def set_llm_api_key_encrypted(
    company_id: str, cipher: str, provider: str = PROVIDER_ANTHROPIC
) -> None:
    """Store a company's Fernet-encrypted key for `provider` (ciphertext only —
    the column never holds plaintext)."""
    client = require_client()
    client.table("companies").update(
        {_key_column(provider): cipher}
    ).eq("id", company_id).execute()


@retry_on_disconnect
def clear_llm_api_key(company_id: str, provider: str = PROVIDER_ANTHROPIC) -> None:
    """Remove a company's key for `provider` (revert to the platform key).

    Deliberately does NOT change `llm_provider`: "I no longer want you holding
    my OpenAI key" and "put me back on Claude" are separate decisions, and
    silently switching provider on a delete would move every subsequent call to
    a different model family without anyone asking for it. A workspace still
    pointed at a provider it has no key for runs on the platform key for that
    provider, exactly as a keyless Claude workspace always has."""
    client = require_client()
    client.table("companies").update(
        {_key_column(provider): None}
    ).eq("id", company_id).execute()


@retry_on_disconnect
def set_llm_provider(company_id: str, provider: str) -> None:
    """Set which provider this company's LLM calls run on."""
    if provider not in _KEY_COLUMNS:
        raise ValueError(f"Unknown LLM provider: {provider!r}")
    client = require_client()
    client.table("companies").update(
        {"llm_provider": provider}
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

    Cached via `memberships_cache` (30s TTL) — this runs on EVERY
    authenticated request (require_company + the LLM-key middleware). Only
    non-empty results are cached: onboarding inserts company_members from
    the browser via supabase-js, a write this backend never sees, so a
    cached empty list would 403 a freshly-onboarded user for a full TTL
    (see app.db.authcache's module docstring).
    """
    cached = memberships_cache.get(user_id)
    if cached is not None:
        return cached
    client = require_client()
    result = (
        client.table("company_members")
        .select("company_id, role")
        .eq("user_id", user_id)
        .execute()
    )
    rows = result.data or []
    if rows:
        memberships_cache.set(user_id, rows)
    return rows


@retry_on_disconnect
def company_created_at(company_id: str) -> str | None:
    """When this company was created, or None if it does not exist.

    Read by `app.warm_gate` for the onboarding grace period: a workspace whose
    first brief has just landed has no conversation history yet, and that is
    the single worst moment to serve a slow first click.
    """
    rows = (
        require_client().table("companies")
        .select("created_at")
        .eq("id", company_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return (rows[0].get("created_at") if rows else None) or None

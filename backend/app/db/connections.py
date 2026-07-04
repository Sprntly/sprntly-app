"""OAuth connector storage — multitenant.

Each row belongs to a company (`company_id` → `companies.id`); a
provider can be connected once per company, never globally. Every
helper requires the caller to pass the company id explicitly — there
is no implicit "current company" fallback. Silent defaults are how the
cross-tenant leak in this table came back the last time.

Per the one-user-one-company product invariant, callers should resolve
the company via `Depends(require_company)` and pass `company.company_id`
into these helpers, rather than letting the client supply it.

Tokens arrive Fernet-encrypted at the app layer (TOKEN_ENCRYPTION_KEY
env var) before they ever reach the database. `account_label` is the
generic identifier shown in the connectors UI ("alice@co.com" for
Figma, "@octocat" for GitHub, the user's email for Google Drive).
`google_email` is kept around for the existing Drive UI that reads it
directly; new providers should use account_label.

Back-compat note: the prior SQLite shape exposed `config` as a JSON
string under the key `config_json`. We preserve that key in returned
dicts so existing callers don't have to change.
"""
import json
import logging
import uuid
from typing import Any

from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)

# The connections table may use either `company_id` (new multitenant schema) or
# `workspace_id` (legacy schema). Detect once at first call and cache.
_OWNER_COL: str | None = None


def _is_undefined_column_error(e: Exception) -> bool:
    """True only when the probe failed because `company_id` genuinely does not
    exist (the legacy `workspace_id` schema) — NOT a transient failure.

    Covers sqlite ("no such column", tests) and Postgres/PostgREST
    (undefined_column / SQLSTATE 42703 / "column ... does not exist")."""
    if getattr(e, "code", None) == "42703":
        return True
    msg = str(e).lower()
    return (
        "no such column" in msg          # sqlite
        or "undefined_column" in msg      # postgrest error code text
        or "does not exist" in msg        # postgres: column "company_id" does not exist
    )


def _owner_column() -> str:
    """Return the column name used to scope connections to a tenant.

    Cache ONLY a definitive result. A transient probe failure (a closed/
    mid-reset DB, a network blip) must never be cached: doing so would pin
    the process to the legacy "workspace_id" and make every subsequent
    upsert_connection write a NULL company_id ("NOT NULL constraint failed:
    connections.company_id") until restart. On a transient failure we fall
    back to the current-schema default for this one call and re-probe next
    time, so the detection self-heals."""
    global _OWNER_COL
    if _OWNER_COL is not None:
        return _OWNER_COL
    c = require_client()
    try:
        c.table("connections").select("company_id").limit(0).execute()
    except Exception as e:  # noqa: BLE001
        if _is_undefined_column_error(e):
            _OWNER_COL = "workspace_id"          # genuine legacy schema — cache it
            return _OWNER_COL
        # Transient — don't poison the cache; default to the live schema column.
        logger.warning(
            "connections owner-column probe failed transiently; "
            "defaulting to company_id without caching", exc_info=True,
        )
        return "company_id"
    _OWNER_COL = "company_id"
    return _OWNER_COL


def _to_legacy_shape(row: dict) -> dict:
    """Add back-compat `config_json` (string) to rows that have `config`."""
    config = row.get("config")
    if isinstance(config, dict):
        row["config_json"] = json.dumps(config)
    elif isinstance(config, str):
        row["config_json"] = config
    elif config is None:
        row["config_json"] = "{}"
    return row


def upsert_connection(
    *,
    company_id: str,
    provider: str,
    token_encrypted: str,
    scopes: str,
    google_email: str | None = None,
    account_label: str | None = None,
    config_json: str = "{}",
    status: str = "active",
) -> dict:
    c = require_client()
    # Parse the legacy JSON-string config back to a dict for jsonb storage.
    try:
        config_obj: Any = json.loads(config_json) if config_json else {}
    except (TypeError, ValueError):
        config_obj = {}

    existing = get_connection(company_id, provider)
    now = utc_now()
    payload = {
        "status": status,
        "google_email": google_email,
        "account_label": account_label,
        "scopes": scopes,
        "token_json_encrypted": token_encrypted,
        "config": config_obj,
        "last_sync_error": None,
        "updated_at": now,
    }
    # Insert-or-update by hand. The company-scoped uniqueness is now a
    # PARTIAL index (where provider <> 'slack', see migration
    # 20260608000000_slack_per_user.sql), which an on_conflict=(cols)
    # target does not match. The get_connection above tells us the path.
    if existing:
        (
            c.table("connections")
            .update(payload)
            .eq(_owner_column(), company_id)
            .eq("provider", provider)
            .execute()
        )
    else:
        payload.update({
            "id": uuid.uuid4().hex,
            _owner_column(): company_id,
            "provider": provider,
            "created_at": now,
        })
        c.table("connections").insert(payload).execute()
    row = get_connection(company_id, provider)
    assert row is not None
    return row


def get_connection(company_id: str, provider: str) -> dict | None:
    c = require_client()
    resp = (
        c.table("connections")
        .select("*")
        .eq(_owner_column(), company_id)
        .eq("provider", provider)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    return _to_legacy_shape(resp.data[0])


def list_connections(company_id: str) -> list[dict]:
    c = require_client()
    resp = (
        c.table("connections")
        .select("*")
        .eq(_owner_column(), company_id)
        .order("provider", desc=False)
        .execute()
    )
    return [_to_legacy_shape(r) for r in (resp.data or [])]


def delete_connection(company_id: str, provider: str) -> bool:
    c = require_client()
    resp = (
        c.table("connections")
        .delete()
        .eq(_owner_column(), company_id)
        .eq("provider", provider)
        .execute()
    )
    return bool(resp.count) if resp.count is not None else True


def patch_connection_config(
    company_id: str, provider: str, config: dict
) -> dict | None:
    """Merge keys into config (jsonb). Returns the updated row in legacy shape."""
    existing = get_connection(company_id, provider)
    if not existing:
        return None
    current: dict = {}
    try:
        current = json.loads(existing.get("config_json") or "{}")
    except (TypeError, ValueError):
        current = {}
    current.update(config)
    c = require_client()
    (
        c.table("connections")
        .update({"config": current, "updated_at": utc_now()})
        .eq(_owner_column(), company_id)
        .eq("provider", provider)
        .execute()
    )
    return get_connection(company_id, provider)


def update_connection_tokens(
    company_id: str, provider: str, token_encrypted: str
) -> None:
    c = require_client()
    (
        c.table("connections")
        .update(
            {"token_json_encrypted": token_encrypted, "updated_at": utc_now()}
        )
        .eq(_owner_column(), company_id)
        .eq("provider", provider)
        .execute()
    )


def update_connection_sync(
    company_id: str,
    provider: str,
    *,
    last_sync_at: str | None = None,
    last_sync_error: str | None = None,
) -> None:
    now = utc_now()
    c = require_client()
    (
        c.table("connections")
        .update(
            {
                "last_sync_at": last_sync_at or now,
                "last_sync_error": last_sync_error,
                "updated_at": now,
            }
        )
        .eq(_owner_column(), company_id)
        .eq("provider", provider)
        .execute()
    )


def set_connection_health(
    connection_id: str,
    *,
    health: str,
    error: str | None,
    checked_at: str,
) -> None:
    """Persist the result of a token-health probe onto a single connection row
    (by primary key). Updates the three health columns added in migration
    20260623120000_connection_health.sql.

    `health` is 'connected' | 'disconnected'; `error` is the provider/probe
    detail on an unhealthy check (None when healthy); `checked_at` is the ISO
    timestamp the probe ran. Keyed by `id` rather than (company_id, provider)
    so the global health sweep can update any tenant's row directly."""
    c = require_client()
    (
        c.table("connections")
        .update(
            {
                "health": health,
                "last_health_error": error,
                "last_health_check_at": checked_at,
                "updated_at": utc_now(),
            }
        )
        .eq("id", connection_id)
        .execute()
    )


def list_all_active_connections() -> list[dict]:
    """Every active connection ACROSS ALL companies — the scheduled connector
    health monitor iterates globally, not per tenant. Returns rows in the legacy
    shape (with `config_json`). Slack rows are included; the monitor probes them
    the same way the per-user test does."""
    c = require_client()
    resp = (
        c.table("connections")
        .select("*")
        .eq("status", "active")
        .order("provider", desc=False)
        .execute()
    )
    return [_to_legacy_shape(r) for r in (resp.data or [])]


# ─────────────────────────── Slack: per-user ───────────────────────────
#
# Slack is the one connector that is personal to each user rather than
# shared across the company: each user installs the bot into their own
# Slack and picks their own channel, so notifications/DMs land in the
# right person's workspace and one member can never read or disconnect
# another member's Slack.
#
# These accessors mirror the company-scoped helpers above but additionally
# key on `user_id`. The company-scoped get_connection/upsert_connection
# remain untouched and continue to serve every other provider. NULL-user
# (legacy) Slack rows are never returned by these reads — those users
# reconnect through the per-user flow.

SLACK_PROVIDER = "slack"


def get_slack_connection(company_id: str, user_id: str) -> dict | None:
    """Return the Slack connection owned by `user_id` within `company_id`,
    or None. Legacy rows with user_id IS NULL are excluded."""
    c = require_client()
    resp = (
        c.table("connections")
        .select("*")
        .eq(_owner_column(), company_id)
        .eq("user_id", user_id)
        .eq("provider", SLACK_PROVIDER)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    return _to_legacy_shape(resp.data[0])


def list_slack_connections(company_id: str) -> list[dict]:
    """All per-user Slack connections within a company — one row per user
    who connected their own Slack. Used by notification/brief delivery to
    fan out to each recipient's own workspace. Legacy NULL-user rows are
    excluded (no owner to deliver to)."""
    c = require_client()
    resp = (
        c.table("connections")
        .select("*")
        .eq(_owner_column(), company_id)
        .eq("provider", SLACK_PROVIDER)
        .not_.is_("user_id", "null")
        .order("user_id", desc=False)
        .execute()
    )
    return [_to_legacy_shape(r) for r in (resp.data or [])]


def list_slack_connections_by_team(team_id: str) -> list[dict]:
    """All Slack connections (ACROSS companies) for a Slack workspace/team id.
    The app_uninstalled event is workspace-scoped, not company-scoped, so this
    isn't keyed by company. Matched on the stored config.team.id (filtered in
    Python — the set of Slack connections is small)."""
    if not team_id:
        return []
    c = require_client()
    resp = c.table("connections").select("*").eq("provider", SLACK_PROVIDER).execute()
    out: list[dict] = []
    for r in (resp.data or []):
        cfg = r.get("config") or {}
        if isinstance(cfg, dict) and (cfg.get("team") or {}).get("id") == team_id:
            out.append(_to_legacy_shape(r))
    return out


def upsert_slack_connection(
    *,
    company_id: str,
    user_id: str,
    token_encrypted: str,
    scopes: str,
    account_label: str | None = None,
    config_json: str = "{}",
    status: str = "active",
) -> dict:
    """Insert/update the Slack connection owned by `user_id` in `company_id`.
    Conflict target is (company_id, user_id, provider) so two users in one
    company can each hold their own Slack row."""
    c = require_client()
    try:
        config_obj: Any = json.loads(config_json) if config_json else {}
    except (TypeError, ValueError):
        config_obj = {}

    existing = get_slack_connection(company_id, user_id)
    now = utc_now()
    payload = {
        "status": status,
        "account_label": account_label,
        "scopes": scopes,
        "token_json_encrypted": token_encrypted,
        "config": config_obj,
        "last_sync_error": None,
        "updated_at": now,
    }
    # Insert-or-update by hand rather than ON CONFLICT: the Slack
    # uniqueness is a PARTIAL index (where provider = 'slack'), which the
    # plain on_conflict=(cols) target does not match. The (company_id,
    # user_id, provider) read above already tells us which path to take.
    if existing:
        (
            c.table("connections")
            .update(payload)
            .eq(_owner_column(), company_id)
            .eq("user_id", user_id)
            .eq("provider", SLACK_PROVIDER)
            .execute()
        )
    else:
        payload.update({
            "id": uuid.uuid4().hex,
            _owner_column(): company_id,
            "user_id": user_id,
            "provider": SLACK_PROVIDER,
            "created_at": now,
        })
        c.table("connections").insert(payload).execute()
    row = get_slack_connection(company_id, user_id)
    assert row is not None
    return row


def delete_slack_connection(company_id: str, user_id: str) -> bool:
    """Delete only this user's Slack connection. Other members' Slack rows
    in the same company are untouched."""
    c = require_client()
    resp = (
        c.table("connections")
        .delete()
        .eq(_owner_column(), company_id)
        .eq("user_id", user_id)
        .eq("provider", SLACK_PROVIDER)
        .execute()
    )
    return bool(resp.count) if resp.count is not None else True


def patch_slack_connection_config(
    company_id: str, user_id: str, config: dict
) -> dict | None:
    """Merge keys into this user's Slack connection config (jsonb)."""
    existing = get_slack_connection(company_id, user_id)
    if not existing:
        return None
    current: dict = {}
    try:
        current = json.loads(existing.get("config_json") or "{}")
    except (TypeError, ValueError):
        current = {}
    current.update(config)
    c = require_client()
    (
        c.table("connections")
        .update({"config": current, "updated_at": utc_now()})
        .eq(_owner_column(), company_id)
        .eq("user_id", user_id)
        .eq("provider", SLACK_PROVIDER)
        .execute()
    )
    return get_slack_connection(company_id, user_id)


def update_slack_connection_sync(
    company_id: str,
    user_id: str,
    *,
    last_sync_at: str | None = None,
    last_sync_error: str | None = None,
) -> None:
    now = utc_now()
    c = require_client()
    (
        c.table("connections")
        .update(
            {
                "last_sync_at": last_sync_at or now,
                "last_sync_error": last_sync_error,
                "updated_at": now,
            }
        )
        .eq(_owner_column(), company_id)
        .eq("user_id", user_id)
        .eq("provider", SLACK_PROVIDER)
        .execute()
    )

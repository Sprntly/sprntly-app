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


def _owner_column() -> str:
    """Return the column name used to scope connections to a tenant."""
    global _OWNER_COL
    if _OWNER_COL is not None:
        return _OWNER_COL
    c = require_client()
    try:
        c.table("connections").select("company_id").limit(0).execute()
        _OWNER_COL = "company_id"
    except Exception:
        _OWNER_COL = "workspace_id"
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
        _owner_column(): company_id,
        "provider": provider,
        "status": status,
        "google_email": google_email,
        "account_label": account_label,
        "scopes": scopes,
        "token_json_encrypted": token_encrypted,
        "config": config_obj,
        "last_sync_error": None,
        "updated_at": now,
    }
    if not existing:
        payload["id"] = uuid.uuid4().hex
        payload["created_at"] = now
    c.table("connections").upsert(
        payload, on_conflict=f"{_owner_column()},provider"
    ).execute()
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

"""OAuth connector storage (Google Drive / Figma / GitHub user OAuth).

Tokens arrive Fernet-encrypted at the app layer (TOKEN_ENCRYPTION_KEY
env var) before they ever reach the database. `account_label` is the
generic identifier shown in the connectors UI ("alice@co.com" for
Figma, "@octocat" for GitHub, the user's email for Google Drive).
`google_email` is kept around for the existing Drive UI that reads it
directly; new providers should use account_label.
"""
import json
import uuid

from app.db.client import conn, shadow_write, utc_now


def upsert_connection(
    *,
    provider: str,
    token_encrypted: str,
    scopes: str,
    google_email: str | None = None,
    account_label: str | None = None,
    config_json: str = "{}",
    status: str = "active",
) -> dict:
    now = utc_now()
    with conn() as c:
        existing = c.execute(
            "SELECT id FROM connections WHERE provider=?", (provider,)
        ).fetchone()
        if existing:
            c.execute(
                "UPDATE connections SET status=?, google_email=?, account_label=?, scopes=?, "
                "token_json_encrypted=?, config_json=?, last_sync_error=NULL, updated_at=? "
                "WHERE provider=?",
                (
                    status,
                    google_email,
                    account_label,
                    scopes,
                    token_encrypted,
                    config_json,
                    now,
                    provider,
                ),
            )
        else:
            row_id = uuid.uuid4().hex
            c.execute(
                "INSERT INTO connections "
                "(id, provider, status, google_email, account_label, scopes, "
                "token_json_encrypted, config_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row_id,
                    provider,
                    status,
                    google_email,
                    account_label,
                    scopes,
                    token_encrypted,
                    config_json,
                    now,
                    now,
                ),
            )
    row = get_connection(provider)
    assert row is not None
    # `provider` is UNIQUE in Supabase too; upsert by it. config_json
    # field name maps to jsonb `config` in the Supabase schema.
    config_obj: dict = {}
    try:
        config_obj = json.loads(row.get("config_json") or "{}")
    except (TypeError, ValueError):
        config_obj = {}
    shadow_write(
        "connections",
        {
            "provider": provider,
            "status": status,
            "google_email": google_email,
            "account_label": account_label,
            "scopes": scopes,
            "token_json_encrypted": token_encrypted,
            "config": config_obj,
        },
        on_conflict="provider",
    )
    return row


def get_connection(provider: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT id, provider, status, google_email, account_label, scopes, "
            "token_json_encrypted, config_json, last_sync_at, last_sync_error, "
            "created_at, updated_at "
            "FROM connections WHERE provider=?",
            (provider,),
        ).fetchone()
    return dict(row) if row else None


def list_connections() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT id, provider, status, google_email, account_label, scopes, "
            "token_json_encrypted, config_json, last_sync_at, last_sync_error, "
            "created_at, updated_at "
            "FROM connections ORDER BY provider ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_connection(provider: str) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM connections WHERE provider=?", (provider,))
        deleted = (cur.rowcount or 0) > 0
    # Mirror the delete to Supabase. We don't shadow_write() here since
    # the helper is insert/upsert-shaped; deletes go straight via the
    # client.
    _shadow_delete_connection(provider)
    return deleted


def patch_connection_config(provider: str, config: dict) -> dict | None:
    """Merge keys into config_json. Returns the updated row."""
    row = get_connection(provider)
    if not row:
        return None
    existing: dict = {}
    try:
        existing = json.loads(row.get("config_json") or "{}")
    except (TypeError, ValueError):
        existing = {}
    existing.update(config)
    now = utc_now()
    blob = json.dumps(existing)
    with conn() as c:
        c.execute(
            "UPDATE connections SET config_json=?, updated_at=? WHERE provider=?",
            (blob, now, provider),
        )
    # Mirror — upsert by provider so we don't have to know the Supabase ID.
    shadow_write(
        "connections",
        {
            "provider": provider,
            "config": existing,
        },
        on_conflict="provider",
    )
    return get_connection(provider)


def update_connection_tokens(provider: str, token_encrypted: str) -> None:
    now = utc_now()
    with conn() as c:
        c.execute(
            "UPDATE connections SET token_json_encrypted=?, updated_at=? WHERE provider=?",
            (token_encrypted, now, provider),
        )
    shadow_write(
        "connections",
        {"provider": provider, "token_json_encrypted": token_encrypted},
        on_conflict="provider",
    )


def update_connection_sync(
    provider: str,
    *,
    last_sync_at: str | None = None,
    last_sync_error: str | None = None,
) -> None:
    now = utc_now()
    with conn() as c:
        c.execute(
            "UPDATE connections SET last_sync_at=?, last_sync_error=?, updated_at=? "
            "WHERE provider=?",
            (last_sync_at or now, last_sync_error, now, provider),
        )
    shadow_write(
        "connections",
        {
            "provider": provider,
            "last_sync_at": last_sync_at or now,
            "last_sync_error": last_sync_error,
        },
        on_conflict="provider",
    )


def _shadow_delete_connection(provider: str) -> None:
    """Mirror a connection delete to Supabase. No-op when dual-write off
    or unconfigured. Errors are logged + swallowed.
    """
    from app.config import settings
    from app.db.client import supabase_client
    if not settings.supabase_dual_write:
        return
    client = supabase_client()
    if client is None:
        return
    try:
        client.table("connections").delete().eq("provider", provider).execute()
    except Exception as e:
        import logging
        logging.getLogger("app.db.connections").warning(
            "Supabase shadow-delete on connections failed: %s: %s",
            type(e).__name__, str(e)[:200],
        )

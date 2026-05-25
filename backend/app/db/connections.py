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
                "UPDATE connections SET status=?, google_email=?, scopes=?, "
                "token_json_encrypted=?, config_json=?, last_sync_error=NULL, updated_at=? "
                "WHERE provider=?",
                (
                    status,
                    google_email,
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
                "(id, provider, status, google_email, scopes, token_json_encrypted, "
                "config_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row_id,
                    provider,
                    status,
                    google_email,
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
            "SELECT id, provider, status, google_email, scopes, token_json_encrypted, "
            "config_json, last_sync_at, last_sync_error, created_at, updated_at "
            "FROM connections WHERE provider=?",
            (provider,),
        ).fetchone()
    return dict(row) if row else None


def list_connections() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT id, provider, status, google_email, scopes, token_json_encrypted, "
            "config_json, last_sync_at, last_sync_error, created_at, updated_at "
            "FROM connections ORDER BY provider ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_connection(provider: str) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM connections WHERE provider=?", (provider,))
        return (cur.rowcount or 0) > 0


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
    return get_connection(provider)


def update_connection_tokens(provider: str, token_encrypted: str) -> None:
    now = utc_now()
    with conn() as c:
        c.execute(
            "UPDATE connections SET token_json_encrypted=?, updated_at=? WHERE provider=?",
            (token_encrypted, now, provider),
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

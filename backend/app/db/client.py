"""SQLite + Supabase connection helpers, plus the shadow-write hook.

Shared by every domain submodule under app/db/. The SQLite `conn()`
context manager remains the authoritative write path. When
`SUPABASE_DUAL_WRITE=true`, each domain helper also fires its row off
to Supabase via `shadow_write()` — failures are logged and swallowed so
the SQLite path is never blocked by Supabase being slow or down.
"""
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


# ─────────────────────── SQLite (authoritative) ───────────────────────


@contextmanager
def conn():
    c = sqlite3.connect(settings.db_path)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def utc_now() -> str:
    """ISO-8601 UTC timestamp, second precision, suitable for SQLite TEXT columns."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# Kept as `_utc_now` for backward-compat with the previous monolithic db.py.
_utc_now = utc_now


# ─────────────────────── Supabase (shadow / future authoritative) ───────────────────────

# Module-level singleton. Lazy: we don't connect at import time so tests
# that don't touch the network can run without Supabase env set.
_supabase_client: Any | None = None


def supabase_client() -> Any | None:
    """Return a memoized supabase-py Client, or None if not configured.

    Uses the service-role key (bypasses RLS — the backend is a trusted
    server, not a browser). Returns None instead of raising so callers
    can `if client is None: skip()` cleanly.
    """
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return None
    try:
        from supabase import create_client
        _supabase_client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,
        )
    except Exception:
        logger.exception("Failed to create Supabase client")
        return None
    return _supabase_client


def _reset_supabase_client_for_tests() -> None:
    """Used by tests to drop the memoized client after env changes."""
    global _supabase_client
    _supabase_client = None


def shadow_write(table: str, row: dict, *, on_conflict: str | None = None) -> None:
    """Mirror a row to Supabase. No-op unless SUPABASE_DUAL_WRITE is on.

    Errors are logged at WARN and swallowed — the calling SQLite write
    is authoritative and must not fail because Supabase is unreachable.

    `on_conflict` triggers an upsert when set (must be a column with a
    unique constraint, e.g. "provider" for connections or "slug" for
    datasets). Otherwise an insert is attempted.

    During the dual-write phase Supabase auto-generates its own
    surrogate IDs — they will NOT match the SQLite ones. ID parity is
    restored in PR #6 (backfill) by truncating Supabase and re-inserting
    with explicit IDs once we have a unified ID strategy.
    """
    if not settings.supabase_dual_write:
        return
    client = supabase_client()
    if client is None:
        return
    try:
        q = client.table(table)
        if on_conflict:
            q.upsert(row, on_conflict=on_conflict).execute()
        else:
            q.insert(row).execute()
    except Exception as e:
        # Don't include row content in the log — could contain PII or
        # secrets (encrypted tokens, etc.). The table name + error class
        # is enough to triage.
        logger.warning(
            "Supabase shadow-write to %s failed: %s: %s",
            table, type(e).__name__, str(e)[:200],
        )

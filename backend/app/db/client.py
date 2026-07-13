"""Supabase client + a tiny timestamp helper.

This module previously held the SQLite connection context manager and a
shadow-write hook. Both are gone — the cutover is complete and the
backend reads + writes only Supabase now. The SQLite file on EC2 is
preserved as a frozen archive but is no longer touched by any code path
in this repo.

Tests substitute a `FakeSupabaseClient` (see tests/_fake_supabase.py)
by monkey-patching `supabase_client` to return it.
"""
import functools
import logging
from datetime import datetime, timezone
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def utc_now() -> str:
    """ISO-8601 UTC timestamp, second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# Kept as `_utc_now` for backward-compat with older imports.
_utc_now = utc_now


# Module-level singleton. Lazy: we don't connect at import time so tests
# can patch the factory before any helper touches it.
_supabase_client: Any | None = None


def supabase_client() -> Any | None:
    """Return a memoized supabase-py Client, or None if not configured.

    Uses the service-role key (bypasses RLS — the backend is trusted).
    Returns None instead of raising so callers can fail loudly with a
    clear error from the helper layer.
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
    """Drop the memoized client so tests can swap in a fake."""
    global _supabase_client
    _supabase_client = None


def reset_client() -> None:
    """Drop the memoised client, forcing reconnection on the next call.

    Called by `retry_on_disconnect` after an HTTP/2 idle-timeout error so
    the next `supabase_client()` call gets a fresh TCP connection.
    """
    global _supabase_client
    _supabase_client = None


def _is_stale_connection_error(exc: Exception) -> bool:
    """A dead/stale HTTP/2 connection to Supabase, fixable by reconnecting.

    Two shapes in production:
      * `httpx.RemoteProtocolError: Server disconnected` — Supabase closed the
        idle connection and the next request noticed.
      * `httpcore.LocalProtocolError: Invalid input ... in state
        ConnectionState.CLOSED` — the h2 state machine already knows the
        connection is closed but the pooled client tried to use it anyway
        (seen under concurrent use, e.g. the ticket fan-out threads).
    Matched by name so we don't import httpx/httpcore here.
    """
    exc_type = type(exc).__name__
    exc_msg = str(exc)
    return (
        "RemoteProtocolError" in exc_type
        or "LocalProtocolError" in exc_type
        or "Server disconnected" in exc_msg
        or "ConnectionState.CLOSED" in exc_msg
    )


def retry_on_disconnect(fn):
    """Decorator: retry a db helper once on a stale-connection error.

    Supabase's PostgREST client uses httpx with HTTP/2. After a long idle
    period (or a connection-pool race) the connection is dead and the next
    request fails with a protocol error — see `_is_stale_connection_error`.
    One retry is sufficient: we reset `_supabase_client` between attempts so
    the new call gets a fresh supabase-py Client with a clean httpx session.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if _is_stale_connection_error(exc):
                logger.warning(
                    "Supabase stale connection in %s (%s) — resetting client and retrying once",
                    fn.__qualname__,
                    type(exc).__name__,
                )
                reset_client()
                return fn(*args, **kwargs)
            raise
    return wrapper


def require_client() -> Any:
    """Like `supabase_client()` but raises a clear error if unavailable.

    Helper functions in `app.db.*` call this — at runtime on EC2,
    Supabase is always configured; if it ever isn't, callers get a fast
    obvious failure instead of mysterious None-attribute errors deeper in.
    """
    c = supabase_client()
    if c is None:
        raise RuntimeError(
            "Supabase client unavailable — "
            "set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY",
        )
    return c

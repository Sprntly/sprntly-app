"""Supabase client + a tiny timestamp helper.

This module previously held the SQLite connection context manager and a
shadow-write hook. Both are gone — the cutover is complete and the
backend reads + writes only Supabase now. The SQLite file on EC2 is
preserved as a frozen archive but is no longer touched by any code path
in this repo.

Tests substitute a `FakeSupabaseClient` (see tests/_fake_supabase.py)
by monkey-patching `supabase_client` to return it.
"""
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

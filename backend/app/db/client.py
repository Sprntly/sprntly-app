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


def _force_http1(client: Any) -> None:
    """Swap every sub-client session onto HTTP/1.1 httpx clients.

    supabase-py's sync sub-clients (postgrest, storage3, gotrue) all
    default to `httpx.Client(http2=True)`, and the h2 state machine is not
    thread-safe. Each session is shared by every request thread (FastAPI
    runs sync endpoints in a threadpool) plus background jobs, and
    concurrent use corrupts the connection state — HTTP/2 multiplexes all
    threads onto one connection whose stream bookkeeping then races
    (`KeyError` in `h2._open_streams`, `LocalProtocolError: Invalid input
    ... in state ...`). The worst shape (staging 2026-07-24) corrupts
    without raising: every later Supabase call hangs forever, so
    `retry_on_disconnect` never fires while /healthz stays green. With
    HTTP/1.1 the pool checks out one whole connection per request, so
    threads never share protocol state.

    `options.httpx_client` can't express this — supabase-py passes the
    same instance to every sub-client and each rewrites its `base_url` —
    so we replace the sessions after construction. Attribute layout is
    pinned by supabase==2.16.0 and locked in by tests.

    `client.functions` also defaults to HTTP/2 but is lazily built and
    unused by this backend — extend this swap if that ever changes.
    """
    import httpx

    def _clone_http1(old: Any) -> httpx.Client:
        return httpx.Client(
            base_url=old.base_url,
            headers=old.headers,
            timeout=old.timeout,
            follow_redirects=True,
            http2=False,
        )

    # postgrest: request builders read `.session` per call.
    pg = client.postgrest
    old_pg = pg.session
    pg.session = _clone_http1(old_pg)
    old_pg.close()

    # storage3: `.session` and the bucket API's `._client` are the same
    # object — swap both references.
    st = client.storage
    old_st = st.session
    st.session = st._client = _clone_http1(old_st)
    old_st.close()

    # gotrue: base URL and headers travel per-request, the session is a
    # bare client; `auth.admin` shares the parent's instance.
    au = client.auth
    old_au = au._http_client
    au._http_client = au.admin._http_client = httpx.Client(
        follow_redirects=True,
        http2=False,
    )
    old_au.close()


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
        client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,
        )
        _force_http1(client)
        _supabase_client = client
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

    Three shapes in production:
      * `httpx.RemoteProtocolError: Server disconnected` — Supabase closed the
        idle connection and the next request noticed.
      * `httpcore.LocalProtocolError: Invalid input ... in state
        ConnectionState.CLOSED` — the h2 state machine already knows the
        connection is closed but the pooled client tried to use it anyway
        (seen under concurrent use, e.g. the ticket fan-out threads).
      * `httpx.ReadError: [Errno 11] Resource temporarily unavailable` — the
        socket under the pooled connection is dead/contended and the read
        fails (seen on staging 2026-07-14, /v1/team/members).
    Matched by name so we don't import httpx/httpcore here.
    """
    exc_type = type(exc).__name__
    exc_msg = str(exc)
    return (
        "RemoteProtocolError" in exc_type
        or "LocalProtocolError" in exc_type
        or "ReadError" in exc_type
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

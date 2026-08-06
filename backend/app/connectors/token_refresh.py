"""Serialising OAuth refresh, so two callers cannot rotate one token at once.

WHY THIS EXISTS. Jira, Confluence and HubSpot all issue ROTATING refresh
tokens: a successful refresh mints a new refresh token and retires the one that
was presented. Each of their session-open paths does the same three steps —
read the connection row, notice the access token is near expiry, POST the
refresh token and persist what comes back.

Run two of those concurrently for one company and both read the SAME stale row,
so both present the SAME refresh token. Providers keep a short reuse grace
window precisely because clients race, so both calls can succeed and return
DIFFERENT new refresh tokens, of which only the later-issued one is valid.
Whichever `update_connection_tokens` lands LAST wins the row — and if that is
the earlier-issued payload, the credential we have stored is one the provider
has already retired. The tenant's connector is then dead until a human
reconnects, and it surfaces only as a 401 on some later, unrelated request.

Two chat turns in the same second will do it. So will one chat racing the
scheduler's `auto_sync`. It became materially likelier when the cross-connector
sweep started opening several sessions in parallel on an ordinary chat turn
(`connector_lookup/sweep.py`), which is what surfaced it — but the race is in
the refresh paths themselves and predates that, so the fix belongs here rather
than in the sweep.

THE FIX IS THE RE-READ, NOT JUST THE LOCK. Serialising alone would still have
the second caller POST a refresh token the first has just retired. Callers must
hold the lock, RE-READ the row, and re-check freshness — by then the winner has
persisted a fresh token and the loser has nothing to do. That is why this module
exposes a lock rather than a decorator: the re-read has to happen inside the
critical section, in the caller's own token format.

SCOPE, honestly stated. This is a PROCESS-wide lock. It closes concurrency
within one API process, which is where the realistic races live (chat turns and
the in-process scheduler share a process). It does NOT coordinate across the
staging and prod boxes that share one Supabase project. Cross-process
serialisation needs a DB advisory lock or a compare-and-set on the row, which is
a schema-owner change; this is deliberately the in-process half.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

#: (company_id, provider) -> lock. Guarded by `_REGISTRY_LOCK` so two threads
#: cannot mint two different locks for one key and both "hold" it.
#:
#: Never evicted, and that is fine: the key space is tenants × refreshing
#: providers, each entry is a bare `threading.Lock`, and a process that has
#: served every tenant holds a few hundred of them. Eviction would need its own
#: synchronisation to avoid dropping a lock somebody holds — a worse trade than
#: the memory.
_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_REGISTRY_LOCK = threading.Lock()

#: Cap on how long a caller waits for another caller's refresh. A refresh is one
#: HTTP POST, so this is generous; it exists so a thread that somehow dies
#: holding the lock cannot pin every later caller forever. On timeout the caller
#: proceeds unserialised — the original behaviour, no worse than before.
LOCK_TIMEOUT_S = 20.0


def refresh_lock(company_id: str, provider: str) -> threading.Lock:
    """The lock guarding token refresh for one company's one connector."""
    key = (company_id or "", provider or "")
    with _REGISTRY_LOCK:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.Lock()
        return lock


class _NullContext:
    def __enter__(self):
        return False

    def __exit__(self, *exc):
        return False


class serialised_refresh:  # noqa: N801 — used as a context manager, reads as one
    """Hold the refresh lock for `(company_id, provider)`.

    Yields True when the lock was acquired and False when the wait timed out.
    Callers should re-read and re-check freshness inside the block either way —
    the timeout path is strictly the pre-existing unserialised behaviour, which
    is worse than serialising but better than not refreshing at all.
    """

    def __init__(self, company_id: str, provider: str, timeout: float = LOCK_TIMEOUT_S):
        self._lock = refresh_lock(company_id, provider)
        self._timeout = timeout
        self._held = False
        self._company_id = company_id
        self._provider = provider

    def __enter__(self) -> bool:
        self._held = self._lock.acquire(timeout=self._timeout)
        if not self._held:
            logger.warning(
                "token refresh: timed out waiting %ss for the %s lock on %s — "
                "proceeding unserialised",
                self._timeout, self._provider, self._company_id,
            )
        return self._held

    def __exit__(self, *exc) -> bool:
        if self._held:
            self._lock.release()
            self._held = False
        return False

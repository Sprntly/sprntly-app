"""DB helpers for `llm_usage_events` — the unified per-call usage ledger.

Write path: **buffered**. `record_usage()` appends to an in-process queue and
returns immediately; a daemon thread flushes in batches. supabase-py is a
synchronous HTTP client, so a per-call INSERT would add ~50-100ms to every
single LLM response — unacceptable on a path a user is waiting on, and pure
overhead on a 40-call agent loop. Batching moves that cost off the request path
entirely.

The tradeoff is explicit and bounded: rows buffered but not yet flushed are lost
if the process dies. That is acceptable for ANALYTICS (a handful of missing rows
shifts a cost chart by cents) and would NOT be acceptable for billing or quota
enforcement — if this ever backs a hard spend limit, the writer must become
synchronous and lossless. `flush()` runs on interpreter exit so an orderly
shutdown loses nothing.

Read path: one `llm_usage_summary` RPC (see the migration) that rolls rows up in
Postgres. The API slices the small result set; it never pages raw rows.

Grain: `company_id` is the tenant (an `companies.id`), NOT a `workspaces` row —
usage is reported per company, so every workspace under one company shares a
single view. The column was briefly named `workspace_id`; see the rename in
migration 20260725130000.

Fail-open contract, same as `db/usage_events.py`: metering must never change
product behaviour. Every public function here swallows its own errors and logs —
a broken ledger degrades the dashboard, it does not break generation.
"""
from __future__ import annotations

import atexit
import logging
import queue
import threading
from typing import Any

from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)

_TABLE = "llm_usage_events"

# Flush when either bound trips: enough rows to amortise the round trip, or
# enough time that a low-traffic workspace's rows still land promptly.
_MAX_BATCH = 50
_FLUSH_INTERVAL_S = 5.0
# Hard cap on buffered rows. If the DB is down and the queue fills, we drop new
# rows rather than grow the buffer without bound — an OOM caused by the
# telemetry sidecar would be a far worse outcome than a gap in the chart.
_MAX_QUEUE = 10_000

_q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=_MAX_QUEUE)
_writer_thread: threading.Thread | None = None
_writer_lock = threading.Lock()
_background_enabled = True
_dropped_since_warn = 0


def record_usage(
    *,
    company_id: str,
    feature: str,
    operation: str | None = None,
    user_id: str | None = None,
    provider: str = "anthropic",
    model: str | None = None,
    key_mode: str = "unknown",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    est_cost_usd: float | None = None,
    latency_ms: int | None = None,
    status: str = "succeeded",
    error_class: str | None = None,
) -> None:
    """Buffer one usage row. Never raises, never blocks on the DB.

    `est_cost_usd` is None when the model has no pricing entry — the tokens are
    still recorded, so spend can be re-derived once the model is priced.
    """
    global _dropped_since_warn
    row = {
        "company_id": company_id,
        "feature": feature,
        "operation": operation,
        "user_id": user_id,
        "provider": provider,
        "model": model,
        "key_mode": key_mode,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cache_creation_input_tokens": int(cache_creation_input_tokens or 0),
        "cache_read_input_tokens": int(cache_read_input_tokens or 0),
        "est_cost_usd": est_cost_usd,
        "latency_ms": latency_ms,
        "status": status,
        "error_class": error_class,
    }
    try:
        _q.put_nowait(row)
    except queue.Full:
        # Backed up (DB down / sustained burst). Drop and count; warn at most
        # once per 100 drops so a sustained outage doesn't flood the log.
        _dropped_since_warn += 1
        if _dropped_since_warn % 100 == 1:
            logger.warning(
                "llm_usage buffer full — dropped %d usage rows so far",
                _dropped_since_warn,
            )
        return
    _ensure_writer()


def _ensure_writer() -> None:
    """Start the flusher thread on first use (daemon: never blocks shutdown)."""
    global _writer_thread
    if not _background_enabled or (_writer_thread and _writer_thread.is_alive()):
        return
    with _writer_lock:
        if _writer_thread and _writer_thread.is_alive():
            return
        t = threading.Thread(
            target=_writer_loop, name="llm-usage-writer", daemon=True
        )
        _writer_thread = t
        t.start()


def _writer_loop() -> None:
    while True:
        try:
            # Block for the first row so an idle process burns nothing, then
            # drain whatever else is already queued up to the batch bound.
            first = _q.get(timeout=_FLUSH_INTERVAL_S)
        except queue.Empty:
            continue
        batch = [first]
        while len(batch) < _MAX_BATCH:
            try:
                batch.append(_q.get_nowait())
            except queue.Empty:
                break
        _write_batch(batch)


@retry_on_disconnect
def _insert(rows: list[dict[str, Any]]) -> None:
    require_client().table(_TABLE).insert(rows).execute()


def _write_batch(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    try:
        _insert(rows)
    except Exception:  # noqa: BLE001 — metering must never surface to the product
        logger.exception("llm_usage batch insert failed (dropping %d rows)", len(rows))


def flush() -> int:
    """Drain and write everything currently buffered. Returns rows written.

    Called on interpreter exit, and by tests for determinism. Safe to call
    alongside the background thread — both pull from the same queue, so at worst
    they split the batch between them.
    """
    rows: list[dict[str, Any]] = []
    while True:
        try:
            rows.append(_q.get_nowait())
        except queue.Empty:
            break
    _write_batch(rows)
    return len(rows)


def disable_background_writer() -> None:
    """Test hook: keep rows buffered until the test calls `flush()` itself.

    Without this a test's assertions race the flusher thread. Production never
    calls it.
    """
    global _background_enabled
    _background_enabled = False


def reset_for_tests() -> None:
    """Drop any buffered rows and the drop counter between tests."""
    global _dropped_since_warn
    while True:
        try:
            _q.get_nowait()
        except queue.Empty:
            break
    _dropped_since_warn = 0


atexit.register(flush)


# --- read path ---------------------------------------------------------------


def fetch_usage_summary(
    *,
    company_id: str,
    start: str,
    end: str,
    tz: str = "UTC",
) -> list[dict[str, Any]]:
    """Rolled-up usage rows for one company (tenant) over `[start, end)`.

    Delegates the GROUP BY to the `llm_usage_summary` Postgres function so the
    backend receives at most a few thousand pre-aggregated rows instead of every
    raw call. `start` / `end` are ISO-8601 timestamps; `tz` is an IANA name used
    only to decide which calendar day a row falls in.

    Returns `[]` (never raises) when the ledger is unavailable — an empty
    dashboard is a better failure than a 500 on a settings page.
    """
    try:
        resp = require_client().rpc(
            "llm_usage_summary",
            {
                "p_company_id": company_id,
                "p_from": start,
                "p_to": end,
                "p_tz": tz,
            },
        ).execute()
        return list(resp.data or [])
    except Exception:  # noqa: BLE001
        logger.exception("llm_usage_summary rpc failed for company %s", company_id)
        return []

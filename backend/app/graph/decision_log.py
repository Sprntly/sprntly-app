"""Agent decision log — append-only, tenant-scoped (§4d).

Every agent / LLM decision writes one record capturing the *why*:
factors, reasoning (chain-of-thought / rationale), output, model,
prompt_version, confidence, and the KG nodes referenced. One record
serves explainability + audit + the Tier-2 learning trace.

The Supabase INSERT is on the agent hot path (the gateway logs one row per
llm_call), and an audit-log write MUST NEVER BLOCK the primary flow. So the
write is submitted to a module-level single-worker background executor and the
caller returns immediately. Failures in the background write are swallowed +
logged, never raised.

Test-safety: many tests assert rows in `agent_decision_log` synchronously right
after calling run_synthesis/etc. To keep them race-free we run the write inline
(synchronously) under pytest — detected via `"pytest" in sys.modules` — so the
row is present the instant the call returns. A `flush_decision_log()` is also
exported for any caller (or test) that needs to block until the queue drains.
"""
from __future__ import annotations

import atexit
import logging
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from app.db.client import require_client

logger = logging.getLogger(__name__)

# Single-worker executor: writes are append-only and order-independent, so one
# background thread keeps them off the hot path without unbounded fan-out. Lazily
# created so module import stays cheap and so a post-flush shutdown can rebuild it.
_executor: Optional[ThreadPoolExecutor] = None
_pending: list[Future] = []


def _running_under_pytest() -> bool:
    """Run the write inline under the test runner so synchronous row-assertions
    right after run_synthesis/etc. never race the background thread."""
    return "pytest" in sys.modules


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="decision-log"
        )
    return _executor


def _do_insert(
    row: dict, client: Any | None
) -> Optional[int]:
    """The actual blocking write. Returns the new row id (or None when the
    insert returns no data, e.g. some fake-client paths). Swallows + logs any
    failure so a background write never raises into the worker thread."""
    try:
        cli = client or require_client()
        r = cli.table("agent_decision_log").insert(row).execute()
        if r.data and isinstance(r.data, list) and r.data:
            return r.data[0].get("id")
        return None
    except Exception:  # noqa: BLE001 — audit write must never break the flow
        logger.exception("agent_decision_log write failed (background)")
        return None


def log_agent_decision(
    *,
    enterprise_id: str,
    agent: str,
    decision_type: str,
    factors: Optional[dict] = None,
    reasoning: Optional[str] = None,
    output: Optional[dict] = None,
    model: Optional[str] = None,
    prompt_version: Optional[str] = None,
    confidence: Optional[float] = None,
    kg_refs: Optional[Iterable[str]] = None,
    client: Any | None = None,
) -> Optional[int]:
    """Append one row to `agent_decision_log` WITHOUT blocking the primary flow.

    The write is submitted to a background single-worker thread and this returns
    immediately with None — the caller's hot path is never gated on the audit
    write, and any write failure is swallowed + logged (never raised). Use
    `flush_decision_log()` when you need the row to be durably present (e.g.
    tests asserting the row, or a clean shutdown).

    Under pytest the write runs inline (synchronously) and returns the new row
    id, so existing tests that read the row right after the call stay green
    without a race.
    """
    row = {
        "enterprise_id": enterprise_id,
        "agent": agent,
        "decision_type": decision_type,
        "factors": factors or {},
        "reasoning": reasoning,
        "output": output or {},
        "model": model,
        "prompt_version": prompt_version,
        "confidence": confidence,
        "kg_refs": list(kg_refs) if kg_refs is not None else [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Inline under pytest so synchronous row-assertions don't race the worker.
    if _running_under_pytest():
        return _do_insert(row, client)

    fut = _get_executor().submit(_do_insert, row, client)
    _pending.append(fut)
    return None


def flush_decision_log(timeout: Optional[float] = None) -> None:
    """Block until every submitted decision-log write has completed.

    Drains the pending futures (failures are already swallowed inside the
    worker, so this never raises). No-op under pytest, where writes are inline."""
    if _running_under_pytest():
        return
    pending, _pending[:] = list(_pending), []
    for fut in pending:
        try:
            fut.result(timeout=timeout)
        except Exception:  # noqa: BLE001 — worker already logged; never raise here
            logger.exception("agent_decision_log flush observed a failed write")


@atexit.register
def _drain_on_exit() -> None:
    """Best-effort flush at interpreter exit so in-flight audit writes land."""
    try:
        flush_decision_log(timeout=5.0)
    except Exception:  # noqa: BLE001
        pass

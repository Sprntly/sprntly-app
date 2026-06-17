"""Design Agent generation worker (Tier 2) — `python -m app.worker`.

A SEPARATE process from the API. It drains the `design_agent_jobs` queue: claim
the oldest queued job, run the IDENTICAL generation body the inline /generate
path runs (`routes.design_agent._run_generation_bg`), then mark the job
done/error. The prototype row is already 'generating' when the job is enqueued,
and `_run_generation_bg` owns the prototype-status write (ready/failed), so the
frontend's existing status-polling is transparent to whether the API or this
worker produced the bundle.

Why a separate process (not just Tier 1's semaphore): on the prod t3.micro the
heavy generation (LLM recreate loop + vite build + headless Chromium) pins both
cores and starves a concurrent /locate (the 504s). Running it here removes that
work from the API request process entirely.

Opt-in: this process REFUSES to drain unless DESIGN_AGENT_WORKER_ENABLED is set
(it idles with a clear log otherwise). It requires the operator to install a 2nd
systemd unit; until then /generate falls back to its in-process path and nothing
is enqueued, so an idle/absent worker is harmless.

Reuse: the worker imports and calls `_run_generation_bg`
UNCHANGED — same runner.py / tools / Anthropic-direct client / tool registry the
inline path uses. No generation logic is duplicated here.

Graceful stop (mirrors Tier 0 intent): on SIGTERM/SIGINT it stops claiming NEW
jobs and lets the current job finish before exiting, so a deploy never SIGKILLs
mid-build. The heavy vite step runs on an uncancellable thread, so — exactly like
the API drain — we do not cancel; we let it complete and then exit.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app.worker")

# Idle sleep between empty polls. Short enough to feel responsive, long enough to
# keep the queue scan + heartbeat write off the hot path. The heartbeat freshness
# window in /generate (30s) is comfortably wider than this.
_IDLE_SLEEP_SECONDS = 2.0


def _worker_enabled() -> bool:
    """Read DESIGN_AGENT_WORKER_ENABLED at runtime (mirrors the route gate)."""
    val = (os.environ.get("DESIGN_AGENT_WORKER_ENABLED") or "").strip().lower()
    return val in {"1", "true", "yes"}


def _worker_id() -> str:
    """Stable-per-process identity for claim ownership + heartbeat attribution."""
    return f"{socket.gethostname()}:{os.getpid()}"


async def _run_one(job: dict) -> None:
    """Run a single claimed job through the shared generation body, then mark it
    done/error. Never raises: a failed generation fails its job + prototype row;
    the loop continues to the next job.

    Imports the route module lazily so importing app.worker (e.g. in a test or a
    `--help`) does not drag in the full route module at import time, and so the
    worker reuses the SAME `_run_generation_bg` the API uses.
    """
    from app.db import design_agent_jobs as jobs_db
    from app.routes import design_agent as routes

    job_id = job["id"]
    payload = job.get("payload") or {}
    kwargs = routes._deserialize_generation_payload(payload)
    prototype_id = kwargs.get("prototype_id")
    try:
        # The IDENTICAL body the inline /generate path runs. It owns the
        # prototype-status write (ready/failed) and the structured cost log.
        await routes._run_generation_bg(**kwargs)
        jobs_db.complete_job(job_id=job_id)
        logger.info("worker_job_complete job_id=%s prototype_id=%s", job_id, prototype_id)
    except Exception as exc:  # noqa: BLE001 — one bad job must not stop the worker
        # _run_generation_bg already failed the prototype row in its own
        # try/except; mark the job row error for queue observability.
        jobs_db.fail_job(job_id=job_id, error=f"{type(exc).__name__}: {exc}")
        logger.warning(
            "worker_job_failed job_id=%s prototype_id=%s", job_id, prototype_id,
            exc_info=True,
        )


async def run_worker(stop: asyncio.Event | None = None) -> None:
    """The drain loop. Writes a heartbeat each tick, claims the next job, runs it,
    else idles. Stops claiming once `stop` is set (SIGTERM) and returns after the
    current job — never mid-job (graceful, mirroring the Tier 0 API drain).

    Refuses to drain when DESIGN_AGENT_WORKER_ENABLED is off: it idles with a
    clear log so a misconfigured unit is obvious instead of silently consuming
    the queue (or busy-looping).
    """
    from app.db import design_agent_jobs as jobs_db

    stop = stop or asyncio.Event()
    wid = _worker_id()
    logger.info("worker_starting worker_id=%s enabled=%s", wid, _worker_enabled())

    while not stop.is_set():
        if not _worker_enabled():
            # Opt-in guard: do not claim while disabled. Idle so the process can
            # be left running ahead of the flag flip without draining anything.
            logger.info("worker_disabled idling worker_id=%s", wid)
            await _sleep_or_stop(stop, _IDLE_SLEEP_SECONDS * 5)
            continue

        # Heartbeat first so /generate sees us as live before we claim work.
        jobs_db.write_heartbeat(worker_id=wid)

        job = jobs_db.claim_next_job(worker_id=wid)
        if job is None:
            await _sleep_or_stop(stop, _IDLE_SLEEP_SECONDS)
            continue

        # A claimed job runs to completion even if a stop arrives mid-run — the
        # graceful-drain contract.
        await _run_one(job)

    logger.info("worker_stopped worker_id=%s", wid)


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    """Sleep up to `seconds`, waking early if `stop` is set — so SIGTERM during
    an idle poll exits promptly instead of waiting out the full interval."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """Wire SIGTERM/SIGINT to set the stop event so the loop stops claiming and
    finishes the current job before exiting. Falls back to signal.signal where
    the event loop cannot add handlers (e.g. non-main thread in some envs)."""
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        if not stop.is_set():
            logger.info("worker_stop_requested — finishing current job then exiting")
            stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_a: _request_stop())


async def _main() -> None:
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    await run_worker(stop)


if __name__ == "__main__":  # pragma: no cover — process entrypoint
    asyncio.run(_main())

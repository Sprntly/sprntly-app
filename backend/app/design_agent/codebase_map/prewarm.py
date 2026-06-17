"""Best-effort, bounded, coalesced background pre-warm of the codebase map.

The cold map build (``service.build_map`` → ``read_repo`` → extractors) is one of
the heavy CPU operations that contends on the 2-vCPU prod box and contributes to
``/locate`` 504s. So the first locate on a freshly-connected repo — and the first
locate after a new push (a new commit_sha is a natural L1/L2 miss) — pays the full
cold build inline while a user waits.

This module warms that map *ahead* of the first locate, so the user's first locate
is an L1/L2 hit. It is deliberately conservative about load:

* **Best-effort.** Every failure is swallowed + logged. A pre-warm failure NEVER
  affects the connect/webhook response that scheduled it.
* **Bounded.** A single process-wide permit (``_PREWARM_SLOTS``) caps how many cold
  builds run *concurrently* at one. A rapid burst of pushes therefore cannot fan
  out into N simultaneous cold builds competing with live ``/locate``; they run at
  most one-at-a-time (extras coalesce away, below). This mirrors the Tier-1
  generation-concurrency guard philosophy: never let background warming stampede
  the box.
* **Coalesced.** An in-flight set keyed by ``(installation_id, repo)`` de-dupes:
  while a pre-warm for a repo is running OR queued, a second request for the same
  repo is dropped rather than starting a duplicate cold build. The key is ref-/sha-
  agnostic on purpose — a default-branch pre-warm for a repo is "the same work"
  regardless of which push triggered it, and ``build_map`` resolves the current
  default-branch SHA itself, so the queued run always warms the *latest* commit.

Thread-based (mirroring the connect path's existing ``kickoff_sync`` daemon
fire-and-forget) rather than asyncio: ``build_map`` is synchronous + CPU-heavy, and
the GitHub OAuth callback that schedules a connect pre-warm is itself a *sync*
FastAPI handler with no running event loop. A daemon worker schedules identically
from both the sync callback and the async webhook with no cross-thread loop
juggling. A half-built map abandoned at process shutdown is harmless: nothing is
written until the build completes, and commit_sha keying means correctness never
depends on the warm having finished — this is purely a first-locate latency cut.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# How many cold pre-warm builds may run concurrently. One: a background warm must
# never stampede the box with parallel cold builds while live /locate is serving.
# A burst of pushes coalesces (see _inflight) down to at most this many builds.
_PREWARM_SLOTS = 1
_slots = threading.Semaphore(_PREWARM_SLOTS)

# On connect we only know the installation, not which repo the user will locate
# against. We enumerate the installation's repos (App-JWT, server-side) and warm
# only the most-recently-updated few — the most likely first-locate targets —
# rather than every repo, so a many-repo install does not trigger a build storm.
_CONNECT_MAX_REPOS = 1

# Coalescing set: (installation_id, repo) currently queued-or-running. Guarded by
# _lock so two near-simultaneous schedules race-free de-dupe to a single build.
_inflight: set[tuple[int, str]] = set()
_lock = threading.Lock()


def _run(installation_id: int, repo: str, ref: str | None) -> None:
    """Worker body: acquire the single build permit, run the cold build, release.

    The permit is held for the *whole* build so concurrency is genuinely bounded
    (acquiring inside the coalesce key would let queued workers pile up waiting on
    the semaphore — they instead coalesce away before a thread is ever spawned).
    Best-effort throughout: any exception is logged, never raised.
    """
    key = (installation_id, repo)
    try:
        # Deferred import: keeps this module importable without dragging the full
        # service + extractor graph in at connectors-import time, and avoids any
        # import cycle through the route layer.
        from app.design_agent.codebase_map.service import build_map

        with _slots:
            logger.info(
                "codebase_map.prewarm start installation=%s repo=%s ref=%s",
                installation_id, repo, ref or "<default>",
            )
            # ref=None → build_map resolves the current default-branch SHA, so a
            # push-triggered warm always targets the latest commit even though the
            # coalesce key is sha-agnostic.
            result = build_map(installation_id, repo, ref)
            logger.info(
                "codebase_map.prewarm done installation=%s repo=%s warmed=%s",
                installation_id, repo, result is not None,
            )
    except Exception:  # noqa: BLE001 — pre-warm is best-effort; never propagate.
        logger.warning(
            "codebase_map.prewarm failed installation=%s repo=%s; first locate "
            "stays cold (no impact on connect/webhook response)",
            installation_id, repo, exc_info=True,
        )
    finally:
        with _lock:
            _inflight.discard(key)


def prewarm_map(installation_id: int, repo: str, ref: str | None = None) -> bool:
    """Schedule a best-effort, bounded, coalesced background pre-warm of ``repo``.

    Returns True if a pre-warm worker was started, False if one was coalesced away
    (already queued/running for this ``(installation_id, repo)``) or could not be
    scheduled. NEVER blocks and NEVER raises into the caller's connect/webhook flow.

    Coalescing is by ``(installation_id, repo)`` only — a burst of pushes to the
    same repo schedules a single warm that targets the latest default-branch SHA.
    """
    try:
        installation_id = int(installation_id)
    except (TypeError, ValueError):
        return False
    if not repo:
        return False

    key = (installation_id, repo)
    with _lock:
        if key in _inflight:
            logger.info(
                "codebase_map.prewarm coalesced installation=%s repo=%s "
                "(already queued/running)",
                installation_id, repo,
            )
            return False
        _inflight.add(key)

    try:
        t = threading.Thread(
            target=_run, args=(installation_id, repo, ref),
            name=f"da-prewarm-{installation_id}-{repo}", daemon=True,
        )
        t.start()
        return True
    except Exception:  # noqa: BLE001 — a thread-spawn failure must not break connect.
        with _lock:
            _inflight.discard(key)
        logger.warning(
            "codebase_map.prewarm could not start thread installation=%s repo=%s",
            installation_id, repo, exc_info=True,
        )
        return False


def _enumerate_and_warm(installation_id: int, max_repos: int) -> None:
    """Worker: enumerate an installation's repos (App-JWT), warm the top ``max_repos``.

    Runs in its own daemon thread so the GitHub enumeration call never blocks the
    connect response. Best-effort: an enumeration failure is logged and dropped.
    Each repo it picks is handed to ``prewarm_map``, which coalesces + bounds the
    actual builds, so even if this enumerates many repos no build storm results.
    """
    try:
        from app.connectors import github_app

        repos = github_app.fetch_installation_repos(installation_id) or []
    except Exception:  # noqa: BLE001 — enumeration is best-effort.
        logger.warning(
            "codebase_map.prewarm connect enumeration failed installation=%s",
            installation_id, exc_info=True,
        )
        return
    # Most-recently-updated first — the likeliest first-locate target. Repos with
    # no updated_at sort last (empty string).
    repos.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    warmed = 0
    for r in repos:
        if warmed >= max_repos:
            break
        full_name = r.get("full_name")
        if not full_name:
            continue
        if prewarm_map(installation_id, str(full_name)):
            warmed += 1
    logger.info(
        "codebase_map.prewarm connect installation=%s candidates=%d warmed=%d cap=%d",
        installation_id, len(repos), warmed, max_repos,
    )


def prewarm_installation(
    installation_id: int, max_repos: int | None = None
) -> bool:
    """Schedule a best-effort connect-time pre-warm for a just-bound installation.

    Enumerates the installation's repos server-side (off the response path) and
    warms the most-recently-updated ``max_repos`` (default ``_CONNECT_MAX_REPOS``).
    Returns True if the enumeration worker was started. NEVER blocks/raises into the
    connect flow. Coalescing + the single build permit still bound the resulting
    builds, so this is safe even on a many-repo installation.
    """
    try:
        installation_id = int(installation_id)
    except (TypeError, ValueError):
        return False
    cap = _CONNECT_MAX_REPOS if max_repos is None else max(int(max_repos), 0)
    if cap <= 0:
        return False
    try:
        t = threading.Thread(
            target=_enumerate_and_warm, args=(installation_id, cap),
            name=f"da-prewarm-enum-{installation_id}", daemon=True,
        )
        t.start()
        return True
    except Exception:  # noqa: BLE001 — never break connect on a thread-spawn failure.
        logger.warning(
            "codebase_map.prewarm could not start connect enumeration installation=%s",
            installation_id, exc_info=True,
        )
        return False

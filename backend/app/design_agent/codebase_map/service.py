"""Map service orchestrator + ephemeral in-process cache.

Ties the read + probe + extract steps into a single entry point,
``build_map``, that downstream callers (locate, the codebase-context phase
gate harness) use as their sole interface to the deterministic screen map.

Cache shape (no persistence):
    Bounded LRU + TTL, keyed on ``(installation_id, repo, commit_sha)``.
    Process-local — no Redis, no DB, no shared store. A cache miss simply
    re-runs the deterministic map (cheap, correct). Tenant isolation is
    structural: the key carries the upstream-tenant-scoped installation_id,
    and the value is derived only from that installation's repo bytes, so a
    cross-tenant collision is not reachable.

Sub-step degradation:
    A failed ``read_repo`` is the only hard ``None`` return. Probe / nodes /
    edges / shell are each wrapped so that one extractor exception leaves
    that part of the ``MapResult`` at its honest default rather than failing
    the whole build.
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from threading import Lock
from typing import Callable, TypeVar

from app.design_agent.codebase_map.edges import resolve_edges
from app.design_agent.codebase_map.nav_probe import ProbeResult, probe_nav_abstraction
from app.design_agent.codebase_map.nodes import extract_nodes
from app.design_agent.codebase_map.repo_reader import read_repo
from app.design_agent.codebase_map.shell import extract_shell
from app.design_agent.codebase_map.stack import (
    StackProfile,
    UnreadableStackError,
    detect_stack,
)
from app.design_agent.codebase_map.types import (
    MapResult,
    NavEdge,
    ScreenNode,
    ShellModel,
    UnresolvedEdge,
)

logger = logging.getLogger(__name__)


# ── cache tuning ──────────────────────────────────────────────────────────────

_CACHE_MAX_ENTRIES = 32
# Bounded LRU; never unbounded growth (would be a process-lifetime leak).

_CACHE_TTL_SECONDS = 900
# 15 min ephemeral. SHA-keying already busts on a new commit, so TTL is
# belt-and-suspenders for the rare same-SHA force-push edge case. A reviewer
# who preferred SHA-only keying could drop the TTL; the bounded-size eviction
# is not optional.

_CacheKey = tuple[int, str, str]  # (installation_id, repo, commit_sha)

_T = TypeVar("_T")


class _MapCache:
    """Bounded LRU + TTL in-process cache.

    Thread-safe so a single FastAPI worker handling concurrent requests does
    not race on the underlying OrderedDict.
    """

    def __init__(self, max_entries: int, ttl_seconds: int) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._entries: OrderedDict[_CacheKey, tuple[float, MapResult]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: _CacheKey) -> MapResult | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            inserted_at, value = entry
            if time.monotonic() - inserted_at > self._ttl_seconds:
                self._entries.pop(key, None)
                return None
            self._entries.move_to_end(key)
            return value

    def put(self, key: _CacheKey, value: MapResult) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self, installation_id: int | None = None) -> None:
        """SYSTEM operation: drop one installation's entries or all of them.

        Invoked on uninstall events and from tests; never on a normal
        request path. Iterates every entry, so callers should expect O(n)
        cost in the bounded cache size.
        """
        with self._lock:
            if installation_id is None:
                self._entries.clear()
                return
            for k in [k for k in self._entries if k[0] == installation_id]:
                self._entries.pop(k, None)


_CACHE = _MapCache(_CACHE_MAX_ENTRIES, _CACHE_TTL_SECONDS)


def clear_map_cache(installation_id: int | None = None) -> None:
    """Drop one installation's cached maps or every entry."""
    _CACHE.clear(installation_id)


def _safe(func: Callable[[], _T], default: _T, label: str) -> _T:
    """Run an extractor; on exception, log a warning and return the default.

    Lets one sub-step's failure degrade gracefully to that sub-step's honest
    default rather than failing the whole build.
    """
    try:
        return func()
    except Exception:
        logger.warning(
            "codebase_map.build sub_step=%s extractor failed; using default", label,
            exc_info=True,
        )
        return default


def build_map(
    installation_id: int, repo: str, ref: str | None = None,
) -> MapResult | None:
    """Deterministic just-in-time screen map for a connected repo.

    Reads the repo at ``ref`` (or default branch), probes the nav abstraction,
    extracts nodes / edges / shell, and assembles a single ``MapResult``.

    Returns ``None`` only when ``read_repo`` itself cannot produce a snapshot
    (no installation, no SHA, empty tree) — callers degrade to "no codebase
    map". Sub-step failures degrade per-field, not whole-build.

    Cached by ``(installation_id, repo, commit_sha)``; the snapshot supplies
    the SHA so a new commit naturally produces a fresh map.
    """
    snapshot = read_repo(installation_id, repo, ref)
    if snapshot is None:
        return None

    key: _CacheKey = (installation_id, repo, snapshot.commit_sha)
    cached = _CACHE.get(key)
    if cached is not None:
        logger.info(
            "codebase_map.build repo=%s sha=%s cache=hit",
            repo, snapshot.commit_sha,
        )
        return cached

    start = time.monotonic()
    probe: ProbeResult = _safe(
        lambda: probe_nav_abstraction(snapshot), ProbeResult(), "probe",
    )

    # Stack detection selects the enumerator adapter. An unreadable non-JS/TS
    # stack declines LOUDLY here rather than letting any enumerator emit a
    # confident-but-wrong screen set. detect_stack is deterministic and never
    # raises on its own; _safe guards an unexpected internal error.
    profile: StackProfile = _safe(
        lambda: detect_stack(snapshot), StackProfile(), "stack",
    )
    if profile.stack == "unreadable":
        logger.info(
            "codebase_map.build repo=%s sha=%s stack=unreadable decline reason=%s",
            snapshot.repo, snapshot.commit_sha, profile.reason,
        )
        raise UnreadableStackError(profile.reason)

    nodes: list[ScreenNode] = _safe(
        lambda: extract_nodes(snapshot, probe), [], "nodes",
    )
    edges_pair: tuple[list[NavEdge], list[UnresolvedEdge]] = _safe(
        lambda: resolve_edges(snapshot, probe, nodes), ([], []), "edges",
    )
    edges, unresolved = edges_pair
    shell: ShellModel = _safe(
        lambda: extract_shell(snapshot), ShellModel(), "shell",
    )

    # The unknown-JS/TS fallback loses the completeness gate, so the build is
    # PARTIAL regardless of what the nav probe inferred — the capability
    # downgrade is surfaced, never silent (StackProfile.reason carries the why).
    posture = "PARTIAL" if profile.stack == "unknown-js-ts" else probe.posture

    result = MapResult(
        repo=snapshot.repo,
        commit_sha=snapshot.commit_sha,
        posture=posture,
        nodes=nodes,
        edges=edges,
        shell=shell,
        unresolved=unresolved,
    )

    duration_ms = int((time.monotonic() - start) * 1000)
    _CACHE.put(key, result)
    n_resolved = sum(1 for e in edges if e.resolved)
    logger.info(
        "codebase_map.build repo=%s sha=%s posture=%s n_nodes=%d n_edges=%d "
        "n_resolved=%d n_unresolved=%d n_nav_items=%d cache=miss duration_ms=%d",
        snapshot.repo, snapshot.commit_sha, result.posture,
        len(nodes), len(edges), n_resolved, len(unresolved), len(shell.nav_items),
        duration_ms,
    )
    return result

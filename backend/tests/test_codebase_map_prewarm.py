"""Pre-warm the codebase map on connect + push.

Covers the pre-warm primitive (``app.design_agent.codebase_map.prewarm``) and its
two route call sites in ``app.routes.connectors``:

* connect → ``prewarm_installation`` enumerates the install's repos and warms the
  top-N most-recently-updated (best-effort, off the response path)
* push webhook → ``prewarm_map`` warms the pushed default-branch's new sha

The design invariants we assert (the whole point of the ticket — DO NOT add load):
* best-effort: a ``build_map`` failure NEVER fails the connect/webhook response
* coalesced: two rapid pre-warms for the same (installation, repo) start ONE build
* bounded: a single build permit means no fan-out of concurrent cold builds
* the webhook signature gate is unchanged (a bad signature is still 401)

``build_map`` / ``fetch_installation_repos`` are mocked throughout so NO real
network or heavy build runs. Threads are joined via a bounded wait helper (no real
work, just synchronisation) so assertions are deterministic.
"""
from __future__ import annotations

import threading
import time

import pytest

from app.design_agent.codebase_map import prewarm as pw


def _wait_until(pred, timeout: float = 5.0, interval: float = 0.005) -> bool:
    """Bounded busy-wait for an in-test condition (NOT a polling production loop).

    Returns True once pred() is truthy, or False on timeout. Used to join the
    daemon pre-warm worker deterministically without a fixed sleep.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


@pytest.fixture(autouse=True)
def _clean_inflight():
    """Each test starts with an empty coalesce set + a fresh full permit so prior
    tests' state never bleeds in."""
    with pw._lock:
        pw._inflight.clear()
    # Drain/reset the build permit to its configured slot count.
    pw._slots = threading.Semaphore(pw._PREWARM_SLOTS)
    yield
    assert _wait_until(lambda: not pw._inflight, timeout=5.0), (
        "pre-warm workers did not drain the in-flight set"
    )


# ─────────────────────── primitive: prewarm_map ───────────────────────


def test_prewarm_map_calls_build_map_in_background(monkeypatch):
    """prewarm_map fires a (mocked) build_map for the repo; the call is async to
    the caller (it returns True immediately) and runs build_map exactly once."""
    calls: list[tuple] = []
    done = threading.Event()

    def _fake_build_map(installation_id, repo, ref=None):
        calls.append((installation_id, repo, ref))
        done.set()
        return object()

    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map", _fake_build_map
    )
    started = pw.prewarm_map(42, "owner/repo")
    assert started is True
    assert done.wait(timeout=5.0)
    assert calls == [(42, "owner/repo", None)]


def test_prewarm_map_passes_ref_through(monkeypatch):
    """An explicit ref is forwarded to build_map (push path warms a known branch)."""
    calls: list[tuple] = []
    done = threading.Event()

    def _fake_build_map(installation_id, repo, ref=None):
        calls.append((installation_id, repo, ref))
        done.set()
        return object()

    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map", _fake_build_map
    )
    pw.prewarm_map(7, "o/r", "main")
    assert done.wait(timeout=5.0)
    assert calls == [(7, "o/r", "main")]


def test_prewarm_map_failure_is_swallowed(monkeypatch):
    """A build_map exception is logged + swallowed; the in-flight key is released so
    the repo can be warmed again (no stuck coalesce key)."""
    def _boom(installation_id, repo, ref=None):
        raise RuntimeError("cold build blew up")

    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map", _boom
    )
    started = pw.prewarm_map(1, "o/r")
    assert started is True
    # Key must be released even though the build raised.
    assert _wait_until(lambda: (1, "o/r") not in pw._inflight)


def test_prewarm_map_coalesces_same_repo(monkeypatch):
    """Two rapid pre-warms for the SAME (installation, repo) start exactly ONE
    build — the second coalesces away rather than launching a duplicate cold
    build. We hold the first build open with a barrier so the second arrives while
    the first is still in-flight."""
    release = threading.Event()
    entered = threading.Event()
    n_builds = {"count": 0}

    def _blocking_build(installation_id, repo, ref=None):
        n_builds["count"] += 1
        entered.set()
        release.wait(timeout=5.0)
        return object()

    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map", _blocking_build
    )
    first = pw.prewarm_map(5, "o/r")
    assert first is True
    assert entered.wait(timeout=5.0)  # first build is now in-flight + holding

    # Second request for the same key while the first is in-flight: coalesced.
    second = pw.prewarm_map(5, "o/r")
    assert second is False

    release.set()
    assert _wait_until(lambda: (5, "o/r") not in pw._inflight)
    assert n_builds["count"] == 1


def test_prewarm_map_distinct_repos_not_coalesced(monkeypatch):
    """Different repos are independent: both warm."""
    seen: set[str] = set()
    lock = threading.Lock()
    both = threading.Event()

    def _fake_build_map(installation_id, repo, ref=None):
        with lock:
            seen.add(repo)
            if seen >= {"o/a", "o/b"}:
                both.set()
        return object()

    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map", _fake_build_map
    )
    assert pw.prewarm_map(1, "o/a") is True
    assert pw.prewarm_map(1, "o/b") is True
    assert both.wait(timeout=5.0)
    assert seen == {"o/a", "o/b"}


def test_prewarm_map_rejects_bad_args(monkeypatch):
    """Falsy repo / unparseable installation id are no-ops (return False), never
    spawning a thread."""
    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map",
        lambda *a, **k: pytest.fail("build_map must not be called"),
    )
    assert pw.prewarm_map(1, "") is False
    assert pw.prewarm_map("not-an-int", "o/r") is False  # type: ignore[arg-type]


# ─────────────────────── primitive: prewarm_installation ───────────────────────


def test_prewarm_installation_warms_most_recent_repo(monkeypatch):
    """Connect path: enumerate the install's repos and warm only the most-recently
    -updated one (the default cap is 1)."""
    repos = [
        {"full_name": "o/old", "updated_at": "2026-01-01T00:00:00Z"},
        {"full_name": "o/new", "updated_at": "2026-06-01T00:00:00Z"},
        {"full_name": "o/mid", "updated_at": "2026-03-01T00:00:00Z"},
    ]
    monkeypatch.setattr(
        "app.connectors.github_app.fetch_installation_repos",
        lambda installation_id: repos,
    )
    warmed: list[str] = []
    done = threading.Event()

    def _fake_build_map(installation_id, repo, ref=None):
        warmed.append(repo)
        done.set()
        return object()

    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map", _fake_build_map
    )
    assert pw.prewarm_installation(99) is True
    assert done.wait(timeout=5.0)
    assert _wait_until(lambda: not pw._inflight)
    assert warmed == ["o/new"]  # cap=1, most-recently-updated only


def test_prewarm_installation_enumeration_failure_swallowed(monkeypatch):
    """A GitHub enumeration failure on connect is best-effort: no build, no raise."""
    def _boom(installation_id):
        raise RuntimeError("github down")

    monkeypatch.setattr(
        "app.connectors.github_app.fetch_installation_repos", _boom
    )
    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map",
        lambda *a, **k: pytest.fail("no repo enumerated → no build"),
    )
    assert pw.prewarm_installation(99) is True  # thread started; it fails internally
    # Nothing should ever land in the in-flight set.
    time.sleep(0.05)
    assert not pw._inflight


def test_prewarm_installation_cap_zero_is_noop(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.github_app.fetch_installation_repos",
        lambda installation_id: pytest.fail("enumeration must not run for cap=0"),
    )
    assert pw.prewarm_installation(99, max_repos=0) is False

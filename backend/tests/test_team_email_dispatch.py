"""Tests for the OFF-THE-REQUEST-THREAD invite-email dispatch (B5a).

`team_email.dispatch_invite_email` wraps `send_invite_email` (unchanged) so
neither the project-tag route nor the team-invite route waits on Supabase's
generate_link + Resend's httpx.post (observed ~1.5s in the request path).

Two behaviours by mode:
  - Under pytest: runs INLINE and returns the real SENT/SENT_EXISTING/FAILED
    status — every existing test that asserts on `send_invite_email`'s
    outcome (via this dispatcher) keeps working unchanged.
  - In production (`sys.modules` without "pytest" — simulated below by
    monkeypatching team_email's `sys`): submits to a dedicated, MODULE-LEVEL
    `ThreadPoolExecutor` and returns the optimistic QUEUED sentinel
    immediately. That path is otherwise invisible to a functional test (the
    pytest guard exists precisely so tests stay deterministic), so these
    tests bypass the guard directly, mirroring
    `test_routes_custom_artifacts.py`'s structural tests for the identical
    dilemma on `routes/custom_artifacts.py::generate`.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import app.auth  # noqa: F401 — ensure app.config/app.auth in sys.modules

_POLL_TIMEOUT_S = 5.0


def _force_production_path(monkeypatch, team_email):
    """Makes `"pytest" in sys.modules` false from `dispatch_invite_email`'s
    point of view, so the background-submit branch runs for real even though
    this test itself is running under pytest."""
    monkeypatch.setattr(team_email, "sys", SimpleNamespace(modules={}))


def _wait_for_inflight_drain(team_email, before: set, *, timeout: float = _POLL_TIMEOUT_S) -> None:
    """The background send runs on a REAL thread — poll briefly for its
    done-callback to fire rather than assuming synchronous completion."""
    deadline = time.monotonic() + timeout
    while team_email._inflight_email_futures != before and time.monotonic() < deadline:
        time.sleep(0.01)


# ── inline-under-pytest (the mode every other test in this codebase relies on) ──


def test_dispatch_runs_inline_under_pytest_and_returns_real_status(
    isolated_settings, monkeypatch
):
    from app import team_email

    monkeypatch.setattr(
        team_email, "send_invite_email", lambda email, **kw: team_email.SENT_EXISTING
    )
    result = team_email.dispatch_invite_email("person@co.com", inviter_first_name="Ada")
    # NOT the optimistic QUEUED sentinel — pytest gets the real, synchronous
    # outcome, exactly as calling send_invite_email directly would.
    assert result == team_email.SENT_EXISTING


def test_dispatch_inline_path_forwards_every_kwarg(isolated_settings, monkeypatch):
    from app import team_email

    seen: dict = {}

    def _fake(email, **kwargs):
        seen["email"] = email
        seen.update(kwargs)
        return team_email.SENT

    monkeypatch.setattr(team_email, "send_invite_email", _fake)
    team_email.dispatch_invite_email(
        "person@co.com",
        inviter_first_name="Ada",
        workspace_name="Acme",
        first_name="Bo",
        project_name="Launch",
    )
    assert seen == {
        "email": "person@co.com",
        "inviter_first_name": "Ada",
        "workspace_name": "Acme",
        "first_name": "Bo",
        "project_name": "Launch",
    }


# ── production path (guard bypassed) — the actual latency fix ──


def test_dispatch_returns_immediately_and_runs_off_thread(isolated_settings, monkeypatch):
    """The core AC: the caller's thread is never blocked on send_invite_email,
    and the send genuinely happens on a DIFFERENT thread."""
    from app import team_email

    _force_production_path(monkeypatch, team_email)

    seen: dict = {}
    ran = threading.Event()

    def _slow_send(email, **kwargs):
        seen["thread_id"] = threading.get_ident()
        seen["email"] = email
        ran.set()
        return team_email.SENT

    monkeypatch.setattr(team_email, "send_invite_email", _slow_send)

    caller_thread_id = threading.get_ident()
    before = set(team_email._inflight_email_futures)

    result = team_email.dispatch_invite_email("person@co.com")

    # Returns the optimistic sentinel — never waits for `_slow_send`.
    assert result == team_email.QUEUED
    assert ran.wait(timeout=_POLL_TIMEOUT_S), "backgrounded send never ran"
    assert seen["thread_id"] != caller_thread_id
    assert seen["email"] == "person@co.com"

    _wait_for_inflight_drain(team_email, before)
    assert team_email._inflight_email_futures == before  # no leaked future


def test_dispatch_preserves_contextvars_into_the_background_thread(
    isolated_settings, monkeypatch
):
    """`contextvars.copy_context().run(...)` — not a bare submit(...) — is
    what makes an ambient binding set in the request survive into the
    executor's worker thread (plain ThreadPoolExecutor.submit does not
    propagate contextvars on its own)."""
    import contextvars

    from app import team_email

    _force_production_path(monkeypatch, team_email)

    probe: contextvars.ContextVar[str] = contextvars.ContextVar("probe", default="unset")
    seen: dict = {}
    ran = threading.Event()

    def _fake(email, **kwargs):
        seen["value"] = probe.get()
        ran.set()
        return team_email.SENT

    monkeypatch.setattr(team_email, "send_invite_email", _fake)

    before = set(team_email._inflight_email_futures)
    token = probe.set("bound-in-request")
    try:
        team_email.dispatch_invite_email("person@co.com")
    finally:
        probe.reset(token)

    assert ran.wait(timeout=_POLL_TIMEOUT_S)
    assert seen["value"] == "bound-in-request"
    _wait_for_inflight_drain(team_email, before)


def test_a_failed_backgrounded_send_is_logged_not_silently_dropped(
    isolated_settings, monkeypatch, caplog
):
    """Degrade-not-error is preserved (the invite row already exists by the
    time this runs — see routes), but a FAILED outcome must still be
    DISCOVERABLE in the logs since the response can no longer report it —
    the whole point of retaining the future + a done-callback instead of a
    bare `.submit(...)`."""
    from app import team_email

    _force_production_path(monkeypatch, team_email)
    monkeypatch.setattr(team_email, "send_invite_email", lambda email, **kw: team_email.FAILED)

    before = set(team_email._inflight_email_futures)
    with caplog.at_level(logging.WARNING):
        result = team_email.dispatch_invite_email("person@co.com")
    assert result == team_email.QUEUED

    _wait_for_inflight_drain(team_email, before)
    assert team_email._inflight_email_futures == before
    assert any(
        "FAILED" in rec.getMessage() for rec in caplog.records
    ), "a backgrounded FAILED send must be logged"


def test_an_exception_from_the_backgrounded_send_is_logged_not_swallowed(
    isolated_settings, monkeypatch, caplog
):
    """send_invite_email is total by contract (catches everything, returns
    FAILED) — this proves the belt-and-braces case where something breaks
    that contract still surfaces in the logs rather than vanishing with a
    dropped Future."""
    from app import team_email

    _force_production_path(monkeypatch, team_email)

    def _boom(email, **kwargs):
        raise RuntimeError("unexpected boom")

    monkeypatch.setattr(team_email, "send_invite_email", _boom)

    before = set(team_email._inflight_email_futures)
    with caplog.at_level(logging.ERROR):
        result = team_email.dispatch_invite_email("person@co.com")
    assert result == team_email.QUEUED  # the caller never sees the exception

    _wait_for_inflight_drain(team_email, before)
    assert team_email._inflight_email_futures == before
    assert any(
        "unexpected boom" in rec.getMessage() for rec in caplog.records
    ), "an exception from the backgrounded send must be logged, not swallowed"


def test_a_burst_of_dispatches_all_complete_without_leaking_futures(
    isolated_settings, monkeypatch
):
    """The set-plus-discard discipline holds under concurrency, not just for
    a single call."""
    from app import team_email

    _force_production_path(monkeypatch, team_email)

    count = 12
    seen_emails: list[str] = []
    lock = threading.Lock()
    all_done = threading.Event()

    def _fake(email, **kwargs):
        with lock:
            seen_emails.append(email)
            if len(seen_emails) == count:
                all_done.set()
        return team_email.SENT

    monkeypatch.setattr(team_email, "send_invite_email", _fake)

    before = set(team_email._inflight_email_futures)
    for i in range(count):
        result = team_email.dispatch_invite_email(f"person{i}@co.com")
        assert result == team_email.QUEUED

    assert all_done.wait(timeout=_POLL_TIMEOUT_S)
    _wait_for_inflight_drain(team_email, before)
    assert team_email._inflight_email_futures == before
    assert sorted(seen_emails) == sorted(f"person{i}@co.com" for i in range(count))


# ── structural: the pool itself ──


def test_invite_email_pool_is_a_dedicated_module_level_executor(isolated_settings):
    """MODULE-LEVEL and bounded, not created per request — a per-request
    executor would be garbage-collected as soon as the handler returned,
    which can kill an in-flight send outright."""
    from app import team_email

    assert isinstance(team_email._INVITE_EMAIL_POOL, ThreadPoolExecutor)
    assert team_email._INVITE_EMAIL_POOL._max_workers >= 1
    assert "invite-email" in team_email._INVITE_EMAIL_POOL._thread_name_prefix


def test_dispatch_is_reused_by_both_invite_routes(isolated_settings):
    """The tag/invite-carrying-project path (routes/projects.py) and the
    direct team-invite path (routes/team.py) both call the SAME dispatcher —
    one dispatch mechanism, not two divergent copies."""
    import inspect

    import app.routes.projects as projects_mod
    import app.routes.team as team_mod

    assert "dispatch_invite_email" in inspect.getsource(projects_mod._invite_carrying_project)
    assert "dispatch_invite_email" in inspect.getsource(team_mod._send_invite_for_row)

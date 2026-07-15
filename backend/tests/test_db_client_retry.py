"""retry_on_disconnect — one reconnect-and-retry on stale-connection errors.

All production shapes of a dead HTTP/2 connection to Supabase must be retried:
  * httpx.RemoteProtocolError ("Server disconnected") — idle timeout
  * httpcore.LocalProtocolError ("... in state ConnectionState.CLOSED") — pooled
    connection already closed (seen under concurrent use, e.g. ticket fan-out)
  * httpx.ReadError ("[Errno 11] Resource temporarily unavailable") — dead
    socket under the pooled connection (seen on staging, /v1/team/members)
Anything else propagates untouched, and there is exactly ONE retry.
"""
from __future__ import annotations

import pytest

from app.db import client as db_client


class _RemoteProtocolError(Exception):
    pass


class _LocalProtocolError(Exception):
    pass


class _ReadError(Exception):
    pass


def _flaky(exc: Exception, results: list):
    """A helper that raises `exc` once, then returns 'ok'."""
    calls = {"n": 0}

    @db_client.retry_on_disconnect
    def fn():
        calls["n"] += 1
        results.append(calls["n"])
        if calls["n"] == 1:
            raise exc
        return "ok"

    return fn


@pytest.mark.parametrize(
    "exc",
    [
        _RemoteProtocolError("Server disconnected"),
        _LocalProtocolError(
            "Invalid input ConnectionInputs.RECV_DATA in state ConnectionState.CLOSED"
        ),
        _ReadError("[Errno 11] Resource temporarily unavailable"),
        # Message-only matches (exception type from a different layer):
        RuntimeError("Server disconnected"),
        RuntimeError("Invalid input ConnectionInputs.SEND_HEADERS in state ConnectionState.CLOSED"),
    ],
)
def test_retries_once_on_stale_connection(exc, monkeypatch):
    resets = []
    monkeypatch.setattr(db_client, "reset_client", lambda: resets.append(True))
    results: list = []
    assert _flaky(exc, results)() == "ok"
    assert results == [1, 2]
    assert resets == [True]


def test_other_errors_propagate_without_retry(monkeypatch):
    resets = []
    monkeypatch.setattr(db_client, "reset_client", lambda: resets.append(True))
    results: list = []
    with pytest.raises(ValueError):
        _flaky(ValueError("column does not exist"), results)()
    assert results == [1]
    assert resets == []


def test_team_read_helpers_are_decorated(monkeypatch):
    """app/db/team.py reads go through retry_on_disconnect (the staging
    /v1/team/members 500 was a ReadError in an undecorated helper)."""
    from app.db import team as db_team

    resets = []
    monkeypatch.setattr(db_client, "reset_client", lambda: resets.append(True))

    calls = {"n": 0}

    class _Result:
        data: list = []

    class _Query:
        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def execute(self):
            return _Result()

    class _Client:
        def table(self, name):
            return _Query()

    def fake_require_client():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _ReadError("[Errno 11] Resource temporarily unavailable")
        return _Client()

    monkeypatch.setattr(db_team, "require_client", fake_require_client)
    assert db_team.list_company_members("c-1") == []
    assert calls["n"] == 2
    assert resets == [True]


def test_retry_happens_at_most_once(monkeypatch):
    monkeypatch.setattr(db_client, "reset_client", lambda: None)
    calls = {"n": 0}

    @db_client.retry_on_disconnect
    def always_stale():
        calls["n"] += 1
        raise _RemoteProtocolError("Server disconnected")

    with pytest.raises(_RemoteProtocolError):
        always_stale()
    assert calls["n"] == 2

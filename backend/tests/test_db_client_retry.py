"""retry_on_disconnect — one reconnect-and-retry on stale-connection errors.

Both production shapes of a dead HTTP/2 connection to Supabase must be retried:
  * httpx.RemoteProtocolError ("Server disconnected") — idle timeout
  * httpcore.LocalProtocolError ("... in state ConnectionState.CLOSED") — pooled
    connection already closed (seen under concurrent use, e.g. ticket fan-out)
Anything else propagates untouched, and there is exactly ONE retry.
"""
from __future__ import annotations

import pytest

from app.db import client as db_client


class _RemoteProtocolError(Exception):
    pass


class _LocalProtocolError(Exception):
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

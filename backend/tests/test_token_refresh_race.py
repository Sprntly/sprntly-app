"""The rotating-refresh-token race, and the lock that closes it.

Jira, Confluence and HubSpot all issue ROTATING refresh tokens: a successful
refresh retires the token that was presented. Two callers reading one stale
connection row therefore present the SAME refresh token, both can succeed inside
the provider's reuse grace window, and the LAST write wins the row — which can
leave a credential the provider has already retired, killing that tenant's
connector until a human reconnects.

These tests drive real threads through the real refresh functions with the
network and DB faked, so what is asserted is the concurrency behaviour rather
than a description of it. Each provider gets the same two questions: does a race
produce exactly ONE refresh POST, and does the loser return the winner's token
rather than overwriting it.
"""
from __future__ import annotations

import json
import threading
import time

import pytest


class FakeConnectionRow:
    """One company's connection row, with the encrypt/decrypt seam stubbed to
    identity so the tests exercise refresh logic rather than crypto."""

    def __init__(self, token_json: dict):
        self.payload = json.dumps(token_json)
        self.writes: list[str] = []
        self.lock = threading.Lock()

    def read(self):
        with self.lock:
            return {"token_json_encrypted": self.payload, "config_json": "{}"}

    def write(self, encrypted: str):
        with self.lock:
            self.payload = encrypted
            self.writes.append(encrypted)


def _stale_token(refresh_token="R0"):
    # obtained_at far enough back that every provider's staleness check fires.
    return {
        "access_token": "A0",
        "refresh_token": refresh_token,
        "expires_in": 1800,
        "obtained_at": int(time.time()) - 100_000,
    }


class RotatingProvider:
    """Mimics an OAuth server with rotating refresh tokens and a reuse grace
    window: presenting a retired token still SUCCEEDS (that is what makes the
    race dangerous), but issues a payload that is already superseded."""

    def __init__(self):
        self.calls: list[str] = []
        self.lock = threading.Lock()
        self.issued = 0

    def refresh(self, presented: str) -> dict:
        # A real refresh is a network round trip; the sleep widens the window so
        # a second thread reliably arrives mid-flight when the lock is absent.
        time.sleep(0.05)
        with self.lock:
            self.calls.append(presented)
            self.issued += 1
            n = self.issued
        return {
            "access_token": f"A{n}",
            "refresh_token": f"R{n}",
            "expires_in": 1800,
        }


def _stamped(token_json: dict) -> str:
    """Stand-in for the real `token_payload_to_store`, which stamps `obtained_at`
    before storing.

    That stamp is load-bearing here and a plain `json.dumps` fake hid it: without
    it the persisted payload has no freshness marker, `_token_is_fresh` returns
    False on the re-read, and the losing caller refreshes a second time. The
    fake, not the code, was wrong — but it is exactly the shape of divergence
    that makes a double stubbed too narrowly worse than no test, so it is
    spelled out rather than inlined.
    """
    return json.dumps({**token_json, "obtained_at": int(time.time())})


def _race(target, n=2):
    """Run `target` on `n` threads at once, return their results in order."""
    results: list = [None] * n
    barrier = threading.Barrier(n)

    def run(i):
        barrier.wait()
        results[i] = target()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


@pytest.fixture(autouse=True)
def _fresh_locks(monkeypatch):
    """Each test gets its own lock registry, so ordering between tests cannot
    let one test's lock satisfy another's race."""
    from app.connectors import token_refresh

    monkeypatch.setattr(token_refresh, "_LOCKS", {})


# ── Jira ─────────────────────────────────────────────────────────────────────


def test_jira_concurrent_refresh_posts_once(monkeypatch):
    """Two chat turns in the same second must not both rotate the token."""
    from app import db
    from app.connectors import jira_fetch

    row = FakeConnectionRow(_stale_token())
    provider = RotatingProvider()

    monkeypatch.setattr(db, "get_connection", lambda cid, p: row.read())
    monkeypatch.setattr(db, "update_connection_tokens",
                        lambda cid, p, enc: row.write(enc))
    monkeypatch.setattr(jira_fetch, "refresh_access_token", provider.refresh)
    monkeypatch.setattr(jira_fetch, "decrypt_token_json", lambda s: s)
    monkeypatch.setattr(jira_fetch, "encrypt_token_json", lambda s: s)
    monkeypatch.setattr(jira_fetch, "token_payload_to_store", _stamped)

    out = _race(lambda: jira_fetch._refresh_if_stale("co-1", _stale_token()))

    assert len(provider.calls) == 1, (
        f"rotating token presented {len(provider.calls)} times: {provider.calls}"
    )
    # Both callers end up holding the SAME, winning token.
    assert {o["access_token"] for o in out} == {"A1"}
    # And the row holds it too — no later write clobbered the winner.
    assert json.loads(row.payload)["refresh_token"] == "R1"


def test_jira_refresh_is_skipped_when_another_caller_already_did_it(monkeypatch):
    """The loser must RE-READ and find the row fresh — the lock alone would
    still have it POST a token the winner just retired."""
    from app import db
    from app.connectors import jira_fetch

    fresh = {
        "access_token": "A-fresh", "refresh_token": "R-fresh",
        "expires_in": 1800, "obtained_at": int(time.time()),
    }
    row = FakeConnectionRow(fresh)
    provider = RotatingProvider()

    monkeypatch.setattr(db, "get_connection", lambda cid, p: row.read())
    monkeypatch.setattr(db, "update_connection_tokens",
                        lambda cid, p, enc: row.write(enc))
    monkeypatch.setattr(jira_fetch, "refresh_access_token", provider.refresh)
    monkeypatch.setattr(jira_fetch, "decrypt_token_json", lambda s: s)
    monkeypatch.setattr(jira_fetch, "encrypt_token_json", lambda s: s)
    monkeypatch.setattr(jira_fetch, "token_payload_to_store", _stamped)

    # Caller arrives holding a STALE copy, but the row has since been refreshed.
    out = jira_fetch._refresh_if_stale("co-1", _stale_token())

    assert provider.calls == [], "should not refresh a row that is already fresh"
    assert out["access_token"] == "A-fresh"
    assert row.writes == []


def test_jira_refresh_failure_still_degrades_to_the_input(monkeypatch):
    """Pre-existing contract: a failed refresh returns the token unchanged and
    lets the ensuing 401 surface as 'reconnect Jira'. The lock must not turn
    that into an exception."""
    from app import db
    from app.connectors import jira_fetch

    row = FakeConnectionRow(_stale_token())
    monkeypatch.setattr(db, "get_connection", lambda cid, p: row.read())
    monkeypatch.setattr(db, "update_connection_tokens",
                        lambda cid, p, enc: row.write(enc))
    monkeypatch.setattr(jira_fetch, "decrypt_token_json", lambda s: s)
    monkeypatch.setattr(jira_fetch, "encrypt_token_json", lambda s: s)

    def _boom(_token):
        raise RuntimeError("atlassian said no")

    monkeypatch.setattr(jira_fetch, "refresh_access_token", _boom)

    given = _stale_token()
    out = jira_fetch._refresh_if_stale("co-1", given)

    assert out == given
    assert row.writes == []


# ── HubSpot ──────────────────────────────────────────────────────────────────


def test_hubspot_concurrent_refresh_posts_once(monkeypatch):
    from app.connectors import hubspot_sync

    row = FakeConnectionRow(_stale_token())
    provider = RotatingProvider()

    def _refresh(token_json):
        new = provider.refresh(token_json["refresh_token"])
        return {**token_json, **new, "obtained_at": int(time.time())}

    monkeypatch.setattr(hubspot_sync.db, "get_connection", lambda cid, p: row.read())
    monkeypatch.setattr(hubspot_sync.db, "update_connection_tokens",
                        lambda cid, p, enc: row.write(enc))
    monkeypatch.setattr(hubspot_sync, "refresh_access_token", _refresh)
    monkeypatch.setattr(hubspot_sync, "decrypt_token_json", lambda s: s)
    monkeypatch.setattr(hubspot_sync, "encrypt_token_json", lambda s: s)

    out = _race(lambda: hubspot_sync._get_valid_access_token("co-1"))

    assert len(provider.calls) == 1, (
        f"rotating token presented {len(provider.calls)} times: {provider.calls}"
    )
    assert {token for token, _ in out} == {"A1"}


# ── the lock itself ──────────────────────────────────────────────────────────


def test_lock_is_per_company_and_per_provider():
    from app.connectors.token_refresh import refresh_lock

    assert refresh_lock("co-1", "jira") is refresh_lock("co-1", "jira")
    assert refresh_lock("co-1", "jira") is not refresh_lock("co-2", "jira")
    assert refresh_lock("co-1", "jira") is not refresh_lock("co-1", "hubspot")


def test_two_companies_refresh_in_parallel_not_serially():
    """The lock must not become a global bottleneck: one tenant's slow refresh
    cannot hold up another's."""
    from app.connectors.token_refresh import serialised_refresh

    started = threading.Event()

    def hold():
        with serialised_refresh("co-slow", "jira"):
            started.set()
            time.sleep(0.4)

    t = threading.Thread(target=hold)
    t.start()
    assert started.wait(timeout=5)

    began = time.monotonic()
    with serialised_refresh("co-other", "jira") as held:
        assert held
    assert time.monotonic() - began < 0.2, "a different tenant should not wait"
    t.join(timeout=5)


def test_waiting_caller_times_out_rather_than_hanging_forever():
    """A thread that dies holding the lock must not pin every later caller. On
    timeout we proceed unserialised — the pre-existing behaviour."""
    from app.connectors.token_refresh import refresh_lock, serialised_refresh

    refresh_lock("co-stuck", "jira").acquire()
    try:
        began = time.monotonic()
        with serialised_refresh("co-stuck", "jira", timeout=0.2) as held:
            assert held is False
        assert time.monotonic() - began < 2.0
    finally:
        refresh_lock("co-stuck", "jira").release()

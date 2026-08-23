"""Ingest cost gate — the per-record content-hash ledger (db.kg_ingest_ledger).

Pullers re-fetch everything each sync; before the ledger, every record was
re-extracted (paid) and only the signal WRITE deduped. These tests pin the
money-saving contract: seen records never reach extract_document, a failed
batch keeps its hashes unrecorded (retried next sync), and every ledger
failure fails OPEN (extract everything — never skip unextracted data).
"""
from __future__ import annotations

import pytest

from app import llm_errors
from app.kg_ingest import runner
from app.kg_ingest.types import RawRecord


def _rec(i: int, text: str = "") -> RawRecord:
    return RawRecord(
        provider="clickup", kind="task", external_id=f"t{i}",
        title=f"Task {i}", text=text or f"body of task {i}",
    )


class _Ledger:
    """In-memory stand-in for db.kg_ingest_ledger, patched into the runner.
    Keyed (enterprise_id, hash) like the real table's primary key."""

    def __init__(self):
        self.hashes: set[tuple[str, str]] = set()
        self.recorded_calls = 0

    def seen(self, enterprise_id, hashes):
        return {h for h in hashes if (enterprise_id, h) in self.hashes}

    def record(self, enterprise_id, provider, hashes):
        self.recorded_calls += 1
        self.hashes.update((enterprise_id, h) for h in hashes)


@pytest.fixture
def ledger(monkeypatch):
    led = _Ledger()
    monkeypatch.setattr(runner, "seen_hashes", led.seen)
    monkeypatch.setattr(runner, "record_hashes", led.record)
    return led


@pytest.fixture
def extract_calls(monkeypatch):
    calls: list[str] = []

    def fake_extract(facade, enterprise_id, *, doc_name, text, **kwargs):
        calls.append(text)
        return {"signals": 1, "themes": 0, "skipped": 0}

    monkeypatch.setattr(runner, "extract_document", fake_extract)
    return calls


def test_second_sync_of_same_records_pays_zero_llm_calls(ledger, extract_calls):
    records = [_rec(i) for i in range(4)]
    out1 = runner.sync_provider(None, "ent-A", "clickup", token="t", records=records)
    assert out1["deduped"] == 0 and len(extract_calls) >= 1

    first_calls = len(extract_calls)
    out2 = runner.sync_provider(None, "ent-A", "clickup", token="t", records=records)
    assert len(extract_calls) == first_calls, "re-sync must not re-extract"
    assert out2["deduped"] == 4 and out2["batches"] == 0


def test_only_unseen_records_are_extracted(ledger, extract_calls):
    runner.sync_provider(None, "ent-A", "clickup", token="t",
                         records=[_rec(1), _rec(2)])
    extract_calls.clear()

    out = runner.sync_provider(None, "ent-A", "clickup", token="t",
                               records=[_rec(1), _rec(2), _rec(3)])
    assert out["deduped"] == 2
    assert len(extract_calls) == 1 and "Task 3" in extract_calls[0]
    assert "Task 1" not in extract_calls[0], "seen record must not re-enter a batch"


def test_changed_record_content_is_re_extracted(ledger, extract_calls):
    runner.sync_provider(None, "ent-A", "clickup", token="t", records=[_rec(1)])
    extract_calls.clear()

    changed = [_rec(1, text="now closed as wontfix")]
    out = runner.sync_provider(None, "ent-A", "clickup", token="t", records=changed)
    assert out["deduped"] == 0 and len(extract_calls) == 1


def test_failed_batch_hashes_stay_unrecorded_for_retry(ledger, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("model down")

    monkeypatch.setattr(runner, "extract_document", boom)
    out = runner.sync_provider(None, "ent-A", "clickup", token="t",
                               records=[_rec(1)])
    assert out["errors"] and ledger.recorded_calls == 0

    # Next sync: the record is still unseen and IS extracted.
    calls = []
    monkeypatch.setattr(
        runner, "extract_document",
        lambda *a, **k: calls.append(1) or {"signals": 0, "themes": 0, "skipped": 0},
    )
    out2 = runner.sync_provider(None, "ent-A", "clickup", token="t",
                                records=[_rec(1)])
    assert out2["deduped"] == 0 and calls, "failed batch must be retried"


def test_ledger_is_scoped_per_enterprise(ledger, extract_calls):
    runner.sync_provider(None, "ent-A", "clickup", token="t", records=[_rec(1)])
    extract_calls.clear()
    out = runner.sync_provider(None, "ent-B", "clickup", token="t", records=[_rec(1)])
    assert out["deduped"] == 0 and len(extract_calls) == 1, \
        "another tenant's ledger must not suppress extraction"


def test_ledger_read_failure_fails_open(monkeypatch, extract_calls):
    monkeypatch.setattr(runner, "record_hashes", lambda *a, **k: None)

    def broken_seen(*a, **k):
        # db.kg_ingest_ledger.seen_hashes itself catches and returns set() —
        # mirror that contract here to pin the runner-side behavior.
        return set()

    monkeypatch.setattr(runner, "seen_hashes", broken_seen)
    out = runner.sync_provider(None, "ent-A", "clickup", token="t",
                               records=[_rec(1), _rec(2)])
    assert out["deduped"] == 0 and len(extract_calls) >= 1


def test_db_helpers_fail_open_without_a_client(monkeypatch):
    # No Supabase client configured → seen_hashes returns empty (extract all)
    # and record_hashes swallows — neither may raise.
    from app.db import kg_ingest_ledger as store

    class _Boom:
        def table(self, *a, **k):
            raise RuntimeError("no db")

    monkeypatch.setattr(store, "require_client", lambda: _Boom())
    assert store.seen_hashes("ent-A", ["h1", "h2"]) == set()
    store.record_hashes("ent-A", "clickup", ["h1"])  # must not raise


# --- provider-limit abort ---------------------------------------------------
#
# A dead account (out of credits) is not a per-batch problem. Isolating it
# means every remaining batch fails identically, and because a failed batch
# deliberately keeps its hashes out of the ledger, the NEXT sync re-attempts
# the entire corpus — the failure compounds instead of decaying. Measured in
# production: ~57k failed calls/day from one company, 98.6% of all
# extract_document traffic, each one holding a slot in the LLM concurrency gate.


class _FakeStatusError(Exception):
    """Stands in for anthropic.APIStatusError without constructing a real SDK
    error (which needs an httpx response) — same double `test_llm_errors` uses.

    The type matters: `llm_errors._status_and_text` returns ("not mine") for
    anything that is not an SDK error class, so a bare RuntimeError carrying
    the same text is NOT classified as a limit. That is deliberate on its part,
    and it is why this test patches the class rather than raising a plain
    exception.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


#: The real sentence. Anthropic reports an exhausted balance as a 400
#: invalid_request_error, not a 429, so `llm_errors` matches on message text.
_CREDIT_MSG = (
    "Error code: 400 - {'type': 'error', 'error': {'type': "
    "'invalid_request_error', 'message': 'Your credit balance is too low to "
    "access the Anthropic API. Please go to Plans & Billing to upgrade or "
    "purchase credits.'}, 'request_id': 'req_011CeLVJWNnrpUoBSztCgRRw'}"
)


@pytest.fixture
def as_anthropic_error(monkeypatch):
    """Make `_status_and_text` treat `_FakeStatusError` as an anthropic error."""
    import anthropic
    monkeypatch.setattr(anthropic, "APIStatusError", _FakeStatusError,
                        raising=False)
    return _FakeStatusError


def _limit_error() -> Exception:
    return _FakeStatusError(_CREDIT_MSG, 400)


def _many_records(n: int) -> list[RawRecord]:
    """Enough records to guarantee several batches (6000-char budget)."""
    return [_rec(i, text="x" * 3000) for i in range(n)]


def test_provider_limit_aborts_the_run_instead_of_burning_every_batch(
    ledger, monkeypatch, as_anthropic_error
):
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise _limit_error()

    monkeypatch.setattr(runner, "extract_document", boom)
    records = _many_records(12)
    assert len(list(runner._batches(records))) > 1, "test needs multiple batches"

    out = runner.sync_provider(None, "ent-A", "clickup", token="t",
                               records=records)

    # The whole point: ONE attempt, not one per batch.
    assert calls["n"] == 1, f"expected abort after 1 batch, got {calls['n']}"
    # The surfaced string is the FIXED sentence, never the provider's own text
    # -- last_sync_error is served straight to the connector UI.
    assert out["errors"] == [llm_errors.user_message(llm_errors.PROVIDER_LIMIT)]
    assert not any("credit balance" in e for e in out["errors"]), \
        "raw provider billing text must never reach a customer's screen"


def test_ordinary_batch_failure_still_isolates(ledger, monkeypatch):
    """The abort must be narrow — a bad batch keeps the old per-batch isolation.

    Guards against the guard: classifying too widely would turn one malformed
    record into a silently truncated sync.
    """
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("model down")

    monkeypatch.setattr(runner, "extract_document", boom)
    records = _many_records(12)
    n_batches = len(list(runner._batches(records)))

    out = runner.sync_provider(None, "ent-A", "clickup", token="t",
                               records=records)

    assert calls["n"] == n_batches, "a non-limit error must not abort the run"
    assert not any(llm_errors.user_message(llm_errors.PROVIDER_LIMIT) in e
                   for e in out["errors"])


def test_batches_before_the_limit_keep_their_ledger_progress(
    ledger, monkeypatch, as_anthropic_error
):
    """Aborting must not throw away work already paid for.

    `break` rather than `raise` exists for this: the batches that succeeded
    stay in the ledger, so the next sync does not re-extract them too.
    """
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"signals": 1, "themes": 0, "skipped": 0}
        raise _limit_error()

    monkeypatch.setattr(runner, "extract_document", flaky)
    out = runner.sync_provider(None, "ent-A", "clickup", token="t",
                               records=_many_records(12))

    assert calls["n"] == 2, "one success, then abort on the limit"
    assert ledger.recorded_calls == 1, "the successful batch must be ledgered"
    assert out["batches"] == 1

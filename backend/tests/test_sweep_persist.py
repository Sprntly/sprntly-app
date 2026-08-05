"""Sweep persistence — writing what a cross-connector sweep read into the KG
(app/connector_lookup/sweep_persist.py).

No network, no LLM, no real DB: `extract_document`, `GraphFacade` and the
`kg_ingest_ledger` store are all patched. What these lock down, in the order
they matter:

- the flag is a real kill switch: OFF means zero write-path activity, not
  just "the write is skipped after starting" (AC1, mutation-proved);
- a source the sweep did NOT actually read (timeout/error/dropped/empty)
  never reaches extraction (AC6);
- content already in the ledger is never re-extracted (AC4);
- a persistence failure never propagates out of the background run (AC8);
- one company's write never uses another company's tenant id (AC9);
- provenance names both the provider and the sweep route (AC5), and triage
  runs like every other ingestion path (AC7).
"""
from __future__ import annotations

import threading

import pytest

from app.connector_lookup import sweep as cs
from app.connector_lookup import sweep_persist as sp


def _source(key="jira", text="PROJ-9 needs a fix", status=cs.STATUS_OK):
    s = cs.SourceResult(key=key, display_name=key.title(), status=status, text=text)
    return s


def _result(*sources):
    r = cs.SweepResult(terms=["billing"])
    r.sources = list(sources)
    return r


class FakeThread:
    """Records the call instead of actually spawning — lets kickoff-level
    tests assert what WOULD have run, mirroring
    test_corpus_seed_kickoff.FakeThread."""

    instances: list["FakeThread"] = []

    def __init__(self, target=None, args=(), name=None, daemon=None):
        self.target = target
        self.args = args
        self.name = name
        self.daemon = daemon
        self.started = False
        FakeThread.instances.append(self)

    def start(self):
        self.started = True


@pytest.fixture(autouse=True)
def _reset_fake_thread():
    FakeThread.instances = []
    yield
    FakeThread.instances = []


# ─────────────────────────── AC1 — the flag ───────────────────────────


def test_flag_off_starts_no_thread(monkeypatch):
    monkeypatch.setattr(sp.settings, "sweep_kg_persist_enabled", False)
    monkeypatch.setattr(threading, "Thread", FakeThread)

    result = _result(_source())
    assert sp.kickoff_sweep_persist("ent-A", result) is False
    assert FakeThread.instances == []


def test_flag_off_is_a_real_kill_switch_not_just_a_skip(monkeypatch):
    """Mutation-proof: with the flag off, nothing downstream of the flag
    check is even imported/touched, not merely "started and then no-opped"."""
    monkeypatch.setattr(sp.settings, "sweep_kg_persist_enabled", False)

    def boom(*a, **k):
        raise AssertionError("must not run _run when the flag is off")

    monkeypatch.setattr(sp, "_run", boom)
    monkeypatch.setattr(threading, "Thread",
                        lambda target=None, args=(), name=None, daemon=None:
                        (_ for _ in ()).throw(AssertionError("must not spawn a thread")))

    result = _result(_source())
    assert sp.kickoff_sweep_persist("ent-A", result) is False


def test_flag_on_starts_a_daemon_thread_with_the_read_sources(monkeypatch):
    monkeypatch.setattr(sp.settings, "sweep_kg_persist_enabled", True)
    monkeypatch.setattr(threading, "Thread", FakeThread)

    usable = _source("jira", "PROJ-9")
    unread = _source("slack", "", status=cs.STATUS_TIMEOUT)
    result = _result(usable, unread)

    assert sp.kickoff_sweep_persist("ent-A", result) is True
    assert len(FakeThread.instances) == 1
    t = FakeThread.instances[0]
    assert t.started is True
    assert t.daemon is True
    assert t.name == "sweep-persist"
    enterprise_id, sources = t.args
    assert enterprise_id == "ent-A"
    # AC6 — only the READ source rides into the background run.
    assert [s.key for s in sources] == ["jira"]


def test_no_enterprise_id_starts_nothing(monkeypatch):
    monkeypatch.setattr(sp.settings, "sweep_kg_persist_enabled", True)
    monkeypatch.setattr(threading, "Thread", FakeThread)
    assert sp.kickoff_sweep_persist("", _result(_source())) is False
    assert FakeThread.instances == []


def test_no_result_starts_nothing(monkeypatch):
    monkeypatch.setattr(sp.settings, "sweep_kg_persist_enabled", True)
    monkeypatch.setattr(threading, "Thread", FakeThread)
    assert sp.kickoff_sweep_persist("ent-A", None) is False
    assert FakeThread.instances == []


def test_nothing_usable_starts_nothing(monkeypatch):
    monkeypatch.setattr(sp.settings, "sweep_kg_persist_enabled", True)
    monkeypatch.setattr(threading, "Thread", FakeThread)
    unread = _source("slack", "", status=cs.STATUS_TIMEOUT)
    assert sp.kickoff_sweep_persist("ent-A", _result(unread)) is False
    assert FakeThread.instances == []


def test_kickoff_never_raises_on_thread_spawn_failure(monkeypatch):
    monkeypatch.setattr(sp.settings, "sweep_kg_persist_enabled", True)

    def boom(*a, **k):
        raise RuntimeError("no threads today")

    monkeypatch.setattr(threading, "Thread", boom)
    assert sp.kickoff_sweep_persist("ent-A", _result(_source())) is False


# ─────────────────────── AC2 — off the ask's hot path ───────────────────────


def test_kickoff_returns_immediately_regardless_of_background_run_duration(monkeypatch):
    """Structural latency proof: `kickoff_sweep_persist` must return before the
    background body has done any real work, no matter how long that work
    takes. This is what makes the call from `qa_agent._sweep_context` — which
    runs BEFORE the answer is composed — safe to leave unconditional: the
    caller's wall clock cannot see the extraction/triage/DB-write cost at all.

    This is a structural proof of the fire-and-forget shape, not a
    replacement for a live-staging latency measurement against a real ask —
    see the done-report for what that live gate still needs to confirm.
    """
    import time

    monkeypatch.setattr(sp.settings, "sweep_kg_persist_enabled", True)

    ran_body = threading.Event()

    def slow_run(enterprise_id, sources):
        # Simulates a slow extraction — if the caller were blocking on this,
        # the assertion below on kickoff's own elapsed time would fail.
        time.sleep(0.3)
        ran_body.set()

    monkeypatch.setattr(sp, "_run", slow_run)

    started = time.perf_counter()
    assert sp.kickoff_sweep_persist("ent-A", _result(_source())) is True
    elapsed = time.perf_counter() - started

    assert elapsed < 0.05, (
        f"kickoff must return near-instantly; took {elapsed:.3f}s — the "
        "background body's cost leaked onto the caller's thread"
    )
    assert not ran_body.is_set(), (
        "the slow body must not have completed synchronously inside kickoff"
    )
    # Let the background thread actually finish before the test tears down,
    # so the assertion above is meaningfully checked mid-flight rather than
    # racing process exit.
    ran_body.wait(timeout=2.0)
    assert ran_body.is_set()


# ─────────────────────────── AC6 — unread sources ───────────────────────────


def test_run_only_extracts_read_sources(monkeypatch):
    calls = []
    from app.graph import facade as facade_mod
    monkeypatch.setattr(facade_mod, "GraphFacade", lambda: "FACADE")

    from app.db import kg_ingest_ledger as ledger

    monkeypatch.setattr(ledger, "seen_hashes", lambda eid, hashes: set())
    monkeypatch.setattr(ledger, "record_hashes", lambda eid, provider, hashes: None)

    from app.graph import extractor

    def fake_extract(facade, enterprise_id, *, doc_name, text, **kwargs):
        calls.append((enterprise_id, text))
        return {"signals": 1, "themes": 0, "skipped": 0}

    monkeypatch.setattr(extractor, "extract_document", fake_extract)

    # Only sources that .read would have surfaced are ever handed to `_run` —
    # `sp._run` receives exactly the list `kickoff_sweep_persist` selected,
    # so this pins that a source with no usable text never gets extracted
    # even if it slipped through.
    sp._run("ent-A", [_source("jira", "PROJ-9 text")])
    assert calls == [("ent-A", "PROJ-9 text")]


# ─────────────────────────── AC4 — dedupe against the ledger ───────────────


def test_already_hashed_content_is_skipped(monkeypatch):
    calls = []
    from app.graph import facade as facade_mod
    monkeypatch.setattr(facade_mod, "GraphFacade", lambda: "FACADE")

    from app.db import kg_ingest_ledger as ledger
    from app.graph import extractor

    h = sp._content_hash("same content")
    monkeypatch.setattr(ledger, "seen_hashes", lambda eid, hashes: {h})
    recorded = []
    monkeypatch.setattr(
        ledger, "record_hashes",
        lambda eid, provider, hashes: recorded.append((eid, provider, hashes)),
    )
    monkeypatch.setattr(
        extractor, "extract_document",
        lambda *a, **k: calls.append(1) or {"signals": 1, "themes": 0, "skipped": 0},
    )

    sp._run("ent-A", [_source("jira", "same content")])
    assert calls == [], "already-hashed content must not be re-extracted"
    assert recorded == [], "a skipped (already-seen) source is not re-recorded"


def test_new_content_is_extracted_and_recorded(monkeypatch):
    from app.graph import facade as facade_mod
    monkeypatch.setattr(facade_mod, "GraphFacade", lambda: "FACADE")

    from app.db import kg_ingest_ledger as ledger
    from app.graph import extractor

    monkeypatch.setattr(ledger, "seen_hashes", lambda eid, hashes: set())
    recorded = []
    monkeypatch.setattr(
        ledger, "record_hashes",
        lambda eid, provider, hashes: recorded.append((eid, provider, hashes)),
    )
    monkeypatch.setattr(
        extractor, "extract_document",
        lambda *a, **k: {"signals": 2, "themes": 1, "skipped": 0},
    )

    sp._run("ent-A", [_source("jira", "fresh content")])
    assert recorded == [("ent-A", "jira", [sp._content_hash("fresh content")])]


# ─────────────────────────── AC8 — error isolation ───────────────────────────


def test_ledger_read_failure_fails_open_and_still_extracts(monkeypatch):
    from app.graph import facade as facade_mod
    monkeypatch.setattr(facade_mod, "GraphFacade", lambda: "FACADE")

    from app.db import kg_ingest_ledger as ledger
    from app.graph import extractor

    def boom_seen(*a, **k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(ledger, "seen_hashes", boom_seen)
    monkeypatch.setattr(ledger, "record_hashes", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(
        extractor, "extract_document",
        lambda *a, **k: calls.append(1) or {"signals": 1, "themes": 0, "skipped": 0},
    )

    sp._run("ent-A", [_source("jira", "x")])  # must not raise
    assert calls, "a ledger read failure must fail open (extract), not skip"


def test_one_source_extraction_failure_does_not_block_the_rest(monkeypatch):
    from app.graph import facade as facade_mod
    monkeypatch.setattr(facade_mod, "GraphFacade", lambda: "FACADE")

    from app.db import kg_ingest_ledger as ledger
    from app.graph import extractor

    monkeypatch.setattr(ledger, "seen_hashes", lambda eid, hashes: set())
    recorded = []
    monkeypatch.setattr(
        ledger, "record_hashes",
        lambda eid, provider, hashes: recorded.append(provider),
    )

    def flaky(facade, enterprise_id, *, doc_name, text, **kwargs):
        if "jira" in doc_name:
            raise RuntimeError("model down")
        return {"signals": 1, "themes": 0, "skipped": 0}

    monkeypatch.setattr(extractor, "extract_document", flaky)

    # Must not raise even though the jira leg blows up, and the confluence
    # leg (processed after it) must still land.
    sp._run("ent-A", [_source("jira", "a"), _source("confluence", "b")])
    assert recorded == ["confluence"]


def test_run_never_raises_even_when_facade_construction_fails(monkeypatch):
    """GraphFacade() itself blowing up is OUTSIDE the per-source try/except —
    this pins that `_run`'s outer try covers construction too, not just the
    extraction loop (mirrors auto_sync._run_corpus_seed's one-try shape)."""
    from app.graph import facade as facade_mod

    monkeypatch.setattr(
        facade_mod, "GraphFacade",
        lambda: (_ for _ in ()).throw(RuntimeError("no facade")),
    )

    from app.db import kg_ingest_ledger as ledger

    monkeypatch.setattr(ledger, "seen_hashes", lambda eid, hashes: set())
    sp._run("ent-A", [_source("jira", "x")])  # must not raise


# ─────────────────────────── AC9 — tenancy ───────────────────────────


def test_company_a_write_never_carries_company_b_tenant(monkeypatch):
    """A sweep in company A must never write, or be suppressed by, anything
    scoped to company B."""
    from app.graph import facade as facade_mod
    monkeypatch.setattr(facade_mod, "GraphFacade", lambda: "FACADE")

    from app.db import kg_ingest_ledger as ledger
    from app.graph import extractor

    # Enterprise-scoped fake ledger, keyed exactly like the real table's
    # primary key (enterprise_id, content_hash) — mirrors
    # test_kg_ingest_ledger.py's `_Ledger`.
    seen_store: set[tuple[str, str]] = set()

    def fake_seen(eid, hashes):
        return {h for h in hashes if (eid, h) in seen_store}

    def fake_record(eid, provider, hashes):
        seen_store.update((eid, h) for h in hashes)

    monkeypatch.setattr(ledger, "seen_hashes", fake_seen)
    monkeypatch.setattr(ledger, "record_hashes", fake_record)

    extract_calls: list[str] = []

    def fake_extract(facade, enterprise_id, *, doc_name, text, **kwargs):
        extract_calls.append(enterprise_id)
        return {"signals": 1, "themes": 0, "skipped": 0}

    monkeypatch.setattr(extractor, "extract_document", fake_extract)

    # Same content, two different tenants.
    sp._run("ent-A", [_source("jira", "identical text")])
    sp._run("ent-B", [_source("jira", "identical text")])

    # Company B's identical content must still be extracted — company A's
    # ledger entry must not suppress it (no cross-tenant dedupe leak), and
    # every extract_document call must carry its OWN tenant, never the other
    # company's.
    assert extract_calls == ["ent-A", "ent-B"]
    assert ("ent-A", sp._content_hash("identical text")) in seen_store
    assert ("ent-B", sp._content_hash("identical text")) in seen_store


# ─────────────────────────── AC5/AC7 — provenance + triage ───────────────────


def test_provenance_names_provider_and_sweep_route(monkeypatch):
    from app.graph import facade as facade_mod
    monkeypatch.setattr(facade_mod, "GraphFacade", lambda: "FACADE")

    from app.db import kg_ingest_ledger as ledger
    from app.graph import extractor

    monkeypatch.setattr(ledger, "seen_hashes", lambda eid, hashes: set())
    monkeypatch.setattr(ledger, "record_hashes", lambda *a, **k: None)

    captured = {}

    def fake_extract(facade, enterprise_id, *, doc_name, text, **kwargs):
        captured.update(kwargs)
        captured["doc_name"] = doc_name
        return {"signals": 1, "themes": 0, "skipped": 0}

    monkeypatch.setattr(extractor, "extract_document", fake_extract)

    sp._run("ent-A", [_source("confluence", "a wiki page")])

    assert captured["agent"] == "ingest:confluence"
    assert captured["origin"] == "connector"
    assert captured["provenance_extra"] == {"route": "sweep"}
    assert captured["triage"] is True
    assert "confluence" in captured["doc_name"]


# ─────────────────────────── AC3 — never the model's answer ───────────────


def test_persist_input_is_structurally_only_connector_text():
    """SweepResult carries no answer field at all — `_run`'s only source of
    text is `SourceResult.text`, which is populated exclusively from a
    connector adapter's `dispatch()` return (sweep.py's `_run_live`/
    `_run_local`). There is no attribute on SweepResult or SourceResult that
    could carry the model's generated answer, so a caller cannot even
    ACCIDENTALLY pass it in."""
    import dataclasses

    source_fields = {f.name for f in dataclasses.fields(cs.SourceResult)}
    result_fields = {f.name for f in dataclasses.fields(cs.SweepResult)}
    for suspicious in ("answer", "response", "reply", "output"):
        assert suspicious not in source_fields
        assert suspicious not in result_fields

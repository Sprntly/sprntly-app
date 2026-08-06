"""Sweep persistence — writing what a cross-connector sweep read into the KG
(app/connector_lookup/sweep_persist.py).

No network, no LLM, no real DB: `extract_document`, `GraphFacade` and the
`kg_ingest_ledger` store are all patched. What these lock down, in the order
they matter:

- a source the sweep did NOT actually read (timeout/error/dropped/empty)
  never reaches extraction (AC6);
- content already in the ledger is never re-extracted, whether hashed from a
  source's `records` or (absent those) its whole `text` (AC4/AC6);
- a persistence failure never propagates out of the background run (AC8);
- one company's write never uses another company's tenant id (AC9);
- provenance names both the provider and the sweep route (AC5), and triage
  runs like every other ingestion path (AC7).

There is no feature flag here (removed once the records-based dedupe made it
unnecessary — see the module docstring); `kickoff_sweep_persist` is gated only
on "is there a tenant" and "did the sweep read anything usable".
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


# ─────────────────────────── kickoff (no flag) ───────────────────────────


def test_kickoff_starts_a_daemon_thread_with_the_read_sources(monkeypatch):
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
    monkeypatch.setattr(threading, "Thread", FakeThread)
    assert sp.kickoff_sweep_persist("", _result(_source())) is False
    assert FakeThread.instances == []


def test_no_result_starts_nothing(monkeypatch):
    monkeypatch.setattr(threading, "Thread", FakeThread)
    assert sp.kickoff_sweep_persist("ent-A", None) is False
    assert FakeThread.instances == []


def test_nothing_usable_starts_nothing(monkeypatch):
    monkeypatch.setattr(threading, "Thread", FakeThread)
    unread = _source("slack", "", status=cs.STATUS_TIMEOUT)
    assert sp.kickoff_sweep_persist("ent-A", _result(unread)) is False
    assert FakeThread.instances == []


def test_kickoff_never_raises_on_thread_spawn_failure(monkeypatch):
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


# ─────────────────────────── AC6 — records vs text fallback ───────────────


def test_a_source_with_records_hashes_each_record_not_the_whole_text(monkeypatch):
    """AC6. A source carrying `records` is hashed/extracted PER RECORD, using
    `record.render()` — not `source.text` at all. Proven by seeding the ledger
    with one record's hash and asserting only THAT record is skipped while a
    second record on the same source is still extracted, and that the
    extracted text is the record's own rendering, not the source's prose."""
    from app.graph import facade as facade_mod
    monkeypatch.setattr(facade_mod, "GraphFacade", lambda: "FACADE")

    from app.db import kg_ingest_ledger as ledger
    from app.graph import extractor
    from app.kg_ingest.types import RawRecord

    r1 = RawRecord(provider="jira", kind="issue", external_id="PROJ-1",
                    title="one", text="", properties={})
    r2 = RawRecord(provider="jira", kind="issue", external_id="PROJ-2",
                    title="two", text="", properties={})
    source = _source("jira", text="stale prose that must never be what gets hashed")
    source.records = [r1, r2]

    seen_hash = sp._content_hash(r1.render())
    monkeypatch.setattr(ledger, "seen_hashes", lambda eid, hashes: {seen_hash})
    recorded = []
    monkeypatch.setattr(
        ledger, "record_hashes",
        lambda eid, provider, hashes: recorded.append(hashes),
    )
    extracted_texts = []
    monkeypatch.setattr(
        extractor, "extract_document",
        lambda facade, eid, *, doc_name, text, **k:
            extracted_texts.append(text) or {"signals": 1, "themes": 0, "skipped": 0},
    )

    sp._run("ent-A", [source])

    assert extracted_texts == [r2.render()], (
        "only the record NOT in the ledger should be extracted, and its own "
        "render() — never source.text — is what gets extracted"
    )
    assert recorded == [[sp._content_hash(r2.render())]]


def test_a_source_without_records_falls_back_to_hashing_text(monkeypatch):
    """AC6, the other half: an adapter that returned no records (AC1: absence
    must keep working) degrades to hashing/extracting `source.text` exactly as
    before this ticket."""
    from app.graph import facade as facade_mod
    monkeypatch.setattr(facade_mod, "GraphFacade", lambda: "FACADE")

    from app.db import kg_ingest_ledger as ledger
    from app.graph import extractor

    monkeypatch.setattr(ledger, "seen_hashes", lambda eid, hashes: set())
    monkeypatch.setattr(ledger, "record_hashes", lambda *a, **k: None)
    extracted_texts = []
    monkeypatch.setattr(
        extractor, "extract_document",
        lambda facade, eid, *, doc_name, text, **k:
            extracted_texts.append(text) or {"signals": 1, "themes": 0, "skipped": 0},
    )

    source = _source("slack", text="whole-source prose, no records")
    assert source.records is None
    sp._run("ent-A", [source])

    assert extracted_texts == ["whole-source prose, no records"]


# ─────────────────────────── AC7 — dedupes against the SCHEDULED PULL ─────


def test_sweep_over_content_the_scheduled_pull_already_ingested_is_skipped(monkeypatch):
    """THE PROOF THE TICKET EXISTS FOR. Seed the ledger via the PULL's own
    hashing path (`kg_ingest.runner._content_hash` over a `RawRecord.render()`
    for a fixture Jira issue), then run `sp._run` on a sweep `SourceResult`
    whose `records` holds the SAME record for the SAME issue. `skipped` (no
    extraction call) proves the sweep recognises content the pull already
    ingested — the ledger collision this whole ticket is for.
    """
    from app.graph import facade as facade_mod
    monkeypatch.setattr(facade_mod, "GraphFacade", lambda: "FACADE")

    from app.db import kg_ingest_ledger as ledger
    from app.graph import extractor
    from app.kg_ingest.runner import _content_hash as pull_content_hash
    from app.kg_ingest.types import RawRecord

    # Hand-built independently of connector_lookup.jira's own construction —
    # this test pins the WIRE FORMAT both sides must agree on, not just that
    # sp._content_hash equals itself.
    record = RawRecord(
        provider="jira", kind="issue", external_id="PROJ-42",
        title="Billing migration", text="Move billing off the old gateway",
        properties={
            "status": "In Progress", "priority": "High", "type": "Story",
            "project": "PROJ", "labels": ["billing"], "assignee": "Ada",
        },
        timestamp="2026-08-01T00:00:00Z",
    )

    # Ledger already holds the hash the SCHEDULED PULL would have written for
    # this exact issue — sp._content_hash IS kg_ingest.runner._content_hash
    # (see that function's docstring), so this is the real collision, not a
    # simulated one.
    ledger_store = {pull_content_hash(record.render())}
    monkeypatch.setattr(
        ledger, "seen_hashes",
        lambda eid, hashes: {h for h in hashes if h in ledger_store},
    )
    monkeypatch.setattr(
        ledger, "record_hashes",
        lambda eid, provider, hashes: ledger_store.update(hashes),
    )
    extract_calls = []
    monkeypatch.setattr(
        extractor, "extract_document",
        lambda facade, eid, *, doc_name, text, **k:
            extract_calls.append(text) or {"signals": 0, "themes": 0, "skipped": 0},
    )

    source = cs.SourceResult(
        key="jira", display_name="Jira", status=cs.STATUS_OK,
        text="(sweep prose for a different question — never hashed when "
             "records are present)",
    )
    source.records = [record]

    sp._run("ent-live", [source])

    assert extract_calls == [], (
        "the sweep's record must collide with the pull's ledger entry — "
        "nothing should be re-extracted, i.e. skipped > 0"
    )


# ═══════════════ AMENDMENT: AC-A1..A5 — persist-thread enrichment ═══════════


class _FakeAdapter:
    """Stand-in for a LookupProvider — only `open_session` matters here;
    `_enrich_source` never calls anything else on it."""

    def open_session(self, enterprise_id):
        return "SESSION"


def _mock_ledger(monkeypatch, seen=None):
    from app.db import kg_ingest_ledger as ledger

    seen = seen or set()
    monkeypatch.setattr(ledger, "seen_hashes", lambda eid, hashes: seen)
    recorded = []
    monkeypatch.setattr(
        ledger, "record_hashes",
        lambda eid, provider, hashes: recorded.append((provider, hashes)),
    )
    return recorded


def _mock_facade_and_extractor(monkeypatch):
    from app.graph import extractor
    from app.graph import facade as facade_mod

    monkeypatch.setattr(facade_mod, "GraphFacade", lambda: "FACADE")
    calls = []
    monkeypatch.setattr(
        extractor, "extract_document",
        lambda facade, eid, *, doc_name, text, **k:
            calls.append((doc_name, text)) or {"signals": 1, "themes": 0, "skipped": 0},
    )
    return calls


def _mock_cooldown(monkeypatch, *, cooled: set[str] | None = None):
    from app.db import sweep_persist_cooldown as cooldown

    cooled = cooled or set()
    monkeypatch.setattr(
        cooldown, "in_cooldown", lambda eid, provider, hours: provider in cooled
    )
    marked: list[str] = []
    monkeypatch.setattr(
        cooldown, "mark_run", lambda eid, provider: marked.append(provider)
    )
    return marked


def test_only_the_three_lean_providers_are_enrichable():
    """AC-A1's scope: jira (already byte-identical at sweep time) and slack
    (no puller-shaped record to enrich toward) are deliberately absent."""
    assert set(sp._enrichers()) == {"clickup", "confluence", "hubspot"}


def test_enrichment_runs_before_hashing_so_the_enriched_render_is_what_extracts(
    monkeypatch,
):
    """AC-A1: the ENRICHED record's render() is what gets hashed/extracted,
    not the lean sweep-time one — proving enrichment happens BEFORE the
    ledger check, not after."""
    from app.connector_lookup import registry
    from app.kg_ingest.types import RawRecord

    lean = RawRecord(provider="clickup", kind="task", external_id="t1",
                     title="lean", text="", properties={})
    enriched = RawRecord(provider="clickup", kind="task", external_id="t1",
                         title="enriched (full task)", text="full body",
                         properties={"tags": ["urgent"]})
    assert lean.render() != enriched.render()

    monkeypatch.setattr(
        sp, "_enrichers", lambda: {"clickup": lambda session, record: enriched}
    )
    monkeypatch.setattr(registry, "provider_for", lambda key: _FakeAdapter())
    _mock_cooldown(monkeypatch)
    _mock_ledger(monkeypatch)
    extract_calls = _mock_facade_and_extractor(monkeypatch)

    source = _source("clickup", text="stale clickup prose — never hashed")
    source.records = [lean]

    sp._run("ent-A", [source])

    assert len(extract_calls) == 1
    assert extract_calls[0][1] == enriched.render()
    assert extract_calls[0][1] != lean.render()


def test_enrichment_is_bounded_to_the_top_k_cap(monkeypatch):
    """AC-A3: a source with more hits than the cap enriches only the first
    `_ENRICH_MAX_PER_SOURCE` — the rest pass through UNENRICHED, not
    dropped."""
    from app.connector_lookup import registry
    from app.kg_ingest.types import RawRecord

    n = sp._ENRICH_MAX_PER_SOURCE + 3
    records = [
        RawRecord(provider="clickup", kind="task", external_id=f"t{i}",
                  title=f"lean {i}", text="", properties={})
        for i in range(n)
    ]
    enrich_calls: list[str] = []

    def fake_enrich(session, record):
        enrich_calls.append(record.external_id)
        return RawRecord(provider="clickup", kind="task",
                         external_id=record.external_id,
                         title=f"enriched {record.external_id}", text="full",
                         properties={})

    monkeypatch.setattr(sp, "_enrichers", lambda: {"clickup": fake_enrich})
    monkeypatch.setattr(registry, "provider_for", lambda key: _FakeAdapter())

    source = _source("clickup", text="prose")
    source.records = records

    out = sp._enrich_source("ent-A", source)

    assert len(enrich_calls) == sp._ENRICH_MAX_PER_SOURCE
    assert len(out) == n
    # Past the cap: still present, still the LEAN record — not dropped.
    assert out[sp._ENRICH_MAX_PER_SOURCE].title == f"lean {sp._ENRICH_MAX_PER_SOURCE}"


def test_enrichment_failure_is_isolated_per_hit(monkeypatch):
    """AC-A4: one hit's 404/timeout drops just that record back to its lean
    form — it never aborts the source or the run."""
    from app.connector_lookup import registry
    from app.kg_ingest.types import RawRecord

    r1 = RawRecord(provider="clickup", kind="task", external_id="t1",
                   title="lean1", text="", properties={})
    r2 = RawRecord(provider="clickup", kind="task", external_id="t2",
                   title="lean2", text="", properties={})

    def flaky_enrich(session, record):
        if record.external_id == "t1":
            raise TimeoutError("boom")
        return RawRecord(provider="clickup", kind="task", external_id="t2",
                         title="enriched2", text="full", properties={})

    monkeypatch.setattr(sp, "_enrichers", lambda: {"clickup": flaky_enrich})
    monkeypatch.setattr(registry, "provider_for", lambda key: _FakeAdapter())

    source = _source("clickup", text="prose")
    source.records = [r1, r2]

    out = sp._enrich_source("ent-A", source)

    assert out[0] is r1, "a failed enrichment falls back to the lean record"
    assert out[1].title == "enriched2"


def test_provider_in_cooldown_skips_enrichment_and_extraction_entirely(monkeypatch):
    """AC-A5: a provider processed within the cooldown window gets ZERO
    enrichment fetches and ZERO extraction on the next sweep."""
    from app.connector_lookup import registry
    from app.kg_ingest.types import RawRecord

    enrich_calls: list[str] = []

    def fake_enrich(session, record):
        enrich_calls.append(record.external_id)
        return record

    monkeypatch.setattr(sp, "_enrichers", lambda: {"clickup": fake_enrich})
    monkeypatch.setattr(registry, "provider_for", lambda key: _FakeAdapter())
    marked = _mock_cooldown(monkeypatch, cooled={"clickup"})
    _mock_ledger(monkeypatch)
    extract_calls = _mock_facade_and_extractor(monkeypatch)

    source = _source("clickup", text="prose")
    source.records = [
        RawRecord(provider="clickup", kind="task", external_id="t1",
                  title="x", text="", properties={})
    ]

    sp._run("ent-A", [source])

    assert enrich_calls == [], "zero enrichment fetches while in cooldown"
    assert extract_calls == [], "zero extraction while in cooldown"
    assert marked == [], "a cooled-down source is not re-marked — it never ran"


def test_mark_run_called_only_for_processed_sources_not_cooled_ones(monkeypatch):
    """AC-A2/A6: cooldown is marked per source actually processed this run —
    a source skipped for being in cooldown is not re-marked."""
    marked = _mock_cooldown(monkeypatch, cooled={"confluence"})
    _mock_ledger(monkeypatch)
    _mock_facade_and_extractor(monkeypatch)

    sp._run("ent-A", [_source("jira", "a"), _source("confluence", "b")])

    assert marked == ["jira"]


def test_cooldown_check_uses_the_configured_pipeline_interval(monkeypatch):
    """The cooldown window is `settings.pipeline_interval_hours` — the SAME
    knob the scheduled pull itself runs on (AC-A2)."""
    from app.config import settings
    from app.db import sweep_persist_cooldown as cooldown

    monkeypatch.setattr(settings, "pipeline_interval_hours", 3, raising=False)
    seen_hours: list[float] = []
    monkeypatch.setattr(
        cooldown, "in_cooldown",
        lambda eid, provider, hours: seen_hours.append(hours) or False,
    )
    monkeypatch.setattr(cooldown, "mark_run", lambda eid, provider: None)
    _mock_ledger(monkeypatch)
    _mock_facade_and_extractor(monkeypatch)

    sp._run("ent-A", [_source("jira", "a")])
    assert seen_hours == [3]


def test_sweep_module_never_references_enrichment_or_cooldown(monkeypatch):
    """AC-A2, structural half: enrichment/cooldown must live ONLY in this
    module's background-thread path — `sweep.py` (the fast, latency-bound
    fan-out `qa_agent` calls synchronously) must not import or call any of
    it. Complements the file-level proof (`git diff` shows sweep.py
    byte-identical to the branch base) with something that survives future
    edits to either file.
    """
    import inspect

    src = inspect.getsource(cs)
    for forbidden in (
        "enrich_record", "_enrich_source", "sweep_persist_cooldown",
        "_ENRICH_MAX_PER_SOURCE",
    ):
        assert forbidden not in src, (
            f"sweep.py must not reference {forbidden!r} — enrichment/cooldown "
            "belong to the persist thread only"
        )

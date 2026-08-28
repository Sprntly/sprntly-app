"""Per-call traceability for connector ingestion (COMMIT-1).

Two halves:

  * `kg_ingest.runner` extracts CALL-shaped providers (fireflies/zoom/
    google_meet) one call per document, threading each call's
    (provider, external_id) so the signal can carry a `source_id`; every other
    provider keeps char-budget batching with no source_ref.
  * `call_index.resolve_call_id` is the keyed `(company, provider, external_id)
    -> call_index.id` lookup the extractor stamps onto `kg_signal.source_id`.
"""
from __future__ import annotations

import pytest

import app.call_index as ci
from app.kg_ingest import runner
from app.kg_ingest.types import RawRecord


# ── runner: un-batching call providers, batching everything else ─────────────


@pytest.fixture
def captured(monkeypatch):
    """Capture every runner -> extract_document call (doc_name/text/source_ref),
    with the ledger stubbed so every record is fresh and nothing persists."""
    calls: list[dict] = []

    def fake_extract(facade, enterprise_id, *, doc_name, text,
                     source_ref=None, valid_at=None, **kwargs):
        calls.append({"doc_name": doc_name, "text": text,
                      "source_ref": source_ref, "valid_at": valid_at})
        return {"signals": 1, "themes": 0, "skipped": 0}

    monkeypatch.setattr(runner, "extract_document", fake_extract)
    # These tests are about extract_document's source_ref/valid_at threading,
    # not the directed-checklist second pass — stub it to a no-op (still
    # accepting `valid_at` so the real call site's kwarg doesn't error) so a
    # call-provider sync here never reaches a real LLM call.
    monkeypatch.setattr(
        runner, "run_checklist_pass",
        lambda *a, **k: {"signals": 0, "themes": 0, "skipped": 0, "signal_ids": []},
    )
    # Zoom/Meet's Config-B main-pass condensation is a real (Haiku) LLM call
    # (`app.graph.extractor.summarize_call_transcript`) — stub it too so the
    # zoom/google_meet parametrization below never reaches a real LLM call
    # either. Fireflies never calls this (condenses for free at the puller).
    monkeypatch.setattr(
        runner, "summarize_call_transcript", lambda enterprise_id, text: "condensed"
    )
    monkeypatch.setattr(runner, "seen_hashes", lambda *a, **k: set())
    monkeypatch.setattr(runner, "record_hashes", lambda *a, **k: None)
    return calls


def _rec(provider: str, external_id: str, body: str, *, timestamp: str | None = None) -> RawRecord:
    return RawRecord(provider=provider, kind="meeting", external_id=external_id,
                     title=f"{provider} {external_id}", text=body,
                     timestamp=timestamp)


def test_call_provider_extracts_one_document_per_call(captured):
    """A Fireflies sync of 2 distinct calls produces 2 SEPARATE extractions,
    each carrying that call's (provider, external_id) as source_ref — instead
    of one batch that flattens both calls' provenance into a single label."""
    runner.sync_provider(None, "ent-A", "fireflies", token="t", records=[
        _rec("fireflies", "FF1", "body of call one"),
        _rec("fireflies", "FF2", "body of call two"),
    ])

    assert len(captured) == 2, "expected one extraction per call, not a batch"
    assert {c["source_ref"] for c in captured} == {
        ("fireflies", "FF1"), ("fireflies", "FF2")}

    # No cross-contamination: each extraction sees only its own call's text.
    by_ref = {c["source_ref"][1]: c["text"] for c in captured}
    assert "body of call one" in by_ref["FF1"]
    assert "body of call two" not in by_ref["FF1"]
    assert "body of call two" in by_ref["FF2"]
    assert "body of call one" not in by_ref["FF2"]


@pytest.mark.parametrize("provider", ["fireflies", "zoom", "google_meet"])
def test_every_call_provider_threads_a_source_ref(captured, provider):
    """All three call-shaped providers un-batch and thread their source_ref —
    the set kept in step with call_index.CALL_PROVIDERS."""
    runner.sync_provider(None, "ent-A", provider, token="t",
                         records=[_rec(provider, "X1", "some call body")])
    assert len(captured) == 1
    assert captured[0]["source_ref"] == (provider, "X1")


def test_non_call_provider_still_batches_with_no_source_ref(captured):
    """A non-call provider (github) keeps char-budget batching: two small
    records go into ONE extraction, with no source_ref and the unchanged
    `<provider>-sync-batch-<n>` doc_name."""
    runner.sync_provider(None, "ent-A", "github", token="t", records=[
        _rec("github", "PR1", "diff of pr one"),
        _rec("github", "PR2", "diff of pr two"),
    ])

    assert len(captured) == 1, "small non-call records must batch into one call"
    assert captured[0]["source_ref"] is None
    assert captured[0]["doc_name"] == "github-sync-batch-0"
    assert "diff of pr one" in captured[0]["text"]
    assert "diff of pr two" in captured[0]["text"]


def test_call_provider_doc_name_keeps_the_sync_batch_shape(captured):
    """The doc_name keeps `<provider>-sync-batch-<n>` even per-call, because
    call_digest's double-counting filter (call_digest._SYNC_BATCH_DOC) reads
    the provider off that shape. Changing it would silently break that filter;
    per-call linkage rides source_id/provenance instead."""
    import re

    runner.sync_provider(None, "ent-A", "fireflies", token="t", records=[
        _rec("fireflies", "FF1", "one"), _rec("fireflies", "FF2", "two"),
    ])
    for c in captured:
        assert re.match(r"^fireflies-sync-batch-\d+$", c["doc_name"]), c["doc_name"]


# ── valid_at: a call's own date, not ingest time ──────────────────────────────


@pytest.mark.parametrize("provider", ["fireflies", "zoom", "google_meet"])
def test_call_provider_threads_the_call_own_date_as_valid_at(captured, provider):
    """A call-shaped provider's `RawRecord.timestamp` (the call's own date)
    reaches `extract_document` as `valid_at` — not left None to fall back to
    ingest time. A historical call re-synced today must stale FROM WHEN IT
    HAPPENED, not from today (#Part 2)."""
    runner.sync_provider(None, "ent-A", provider, token="t", records=[
        _rec(provider, "X1", "some call body", timestamp="2026-01-15T10:00:00Z"),
    ])
    assert len(captured) == 1
    from datetime import datetime, timezone

    assert captured[0]["valid_at"] == datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)


def test_call_provider_missing_timestamp_leaves_valid_at_none(captured):
    """A call record with no timestamp degrades to `valid_at=None` (the
    caller's ingest-time default) rather than raising — best-effort, never a
    sync failure."""
    runner.sync_provider(None, "ent-A", "fireflies", token="t", records=[
        _rec("fireflies", "X1", "some call body", timestamp=None),
    ])
    assert len(captured) == 1
    assert captured[0]["valid_at"] is None


def test_non_call_provider_batch_leaves_valid_at_none(captured):
    """A batched (non-call) provider mixes many records with no single
    honest "as-of" date, so `valid_at` stays None — the pre-existing
    ingest-time default, unchanged for every batched connector."""
    runner.sync_provider(None, "ent-A", "github", token="t", records=[
        _rec("github", "PR1", "diff of pr one", timestamp="2026-01-01T00:00:00Z"),
        _rec("github", "PR2", "diff of pr two", timestamp="2026-01-02T00:00:00Z"),
    ])
    assert len(captured) == 1
    assert captured[0]["valid_at"] is None


# ── call_index.resolve_call_id ───────────────────────────────────────────────


class _Query:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self._eqs: dict = {}
        self._limit: int | None = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._eqs[col] = val
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        data = [r for r in self._rows
                if all(r.get(c) == v for c, v in self._eqs.items())]
        if self._limit is not None:
            data = data[: self._limit]
        return type("R", (), {"data": data})()


class _Client:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def table(self, name):
        return _Query(self._rows if name == "call_index" else [])


@pytest.fixture
def call_index_rows(monkeypatch):
    # call_index.id is a bigint identity, so ids are ints here (not uuids).
    rows = [
        {"id": 101, "company_id": "ent-A", "provider": "fireflies",
         "external_id": "FF1"},
        {"id": 202, "company_id": "ent-A", "provider": "zoom",
         "external_id": "Z9"},
        # A different tenant's row with a colliding external_id — must not leak.
        {"id": 303, "company_id": "ent-B", "provider": "fireflies",
         "external_id": "FF1"},
    ]
    monkeypatch.setattr("app.db.client.require_client", lambda: _Client(rows))
    return rows


def test_resolve_call_id_returns_id_for_known_call(call_index_rows):
    assert ci.resolve_call_id("ent-A", "fireflies", "FF1") == 101
    assert ci.resolve_call_id("ent-A", "zoom", "Z9") == 202


def test_resolve_call_id_is_tenant_and_provider_scoped(call_index_rows):
    """The natural key is (company, provider, external_id) — a colliding
    external_id in another tenant or from another provider must not resolve."""
    assert ci.resolve_call_id("ent-B", "fireflies", "FF1") == 303
    assert ci.resolve_call_id("ent-A", "zoom", "FF1") is None


def test_resolve_call_id_returns_none_for_unknown_call(call_index_rows):
    assert ci.resolve_call_id("ent-A", "fireflies", "NOPE") is None


def test_resolve_call_id_returns_none_on_missing_args():
    assert ci.resolve_call_id("", "fireflies", "FF1") is None
    assert ci.resolve_call_id("ent-A", "fireflies", "") is None


def test_resolve_call_id_fails_soft_on_lookup_error(monkeypatch):
    """A DB blip must degrade the signal to unlinked (None), never raise into
    the ingestion writing it."""
    class _Boom:
        def table(self, _name):
            raise RuntimeError("supabase down")

    monkeypatch.setattr("app.db.client.require_client", lambda: _Boom())
    assert ci.resolve_call_id("ent-A", "fireflies", "FF1") is None

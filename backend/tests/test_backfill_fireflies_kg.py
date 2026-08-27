"""Tests for scripts/backfill_fireflies_kg.py — the company-scoped Fireflies
KG re-extraction CLI (Message Batches API).

The script lives under `backend/scripts/`, not the `app` package (matching
every other one-off backfill there — see `scripts/backfill_roadmap_kg.py`),
so it is loaded here via `importlib.util.spec_from_file_location` rather than
a normal import.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "backfill_fireflies_kg.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "backfill_fireflies_kg", _SCRIPT_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod(isolated_settings):
    return _load_module()


def _rec(external_id: str, title: str = "t"):
    from app.kg_ingest.types import RawRecord
    return RawRecord(
        provider="fireflies", kind="meeting", external_id=external_id, title=title,
        text=f"summary: digest for {external_id}",
        checklist_text=f"summary: digest for {external_id}\ntranscript:\nfull text {external_id}",
    )


# ── --company is required ─────────────────────────────────────────────────


def test_company_is_required(mod):
    with pytest.raises(SystemExit) as exc:
        mod.main([])
    assert exc.value.code == 2


# ── --provider other than fireflies errors clearly ───────────────────────


def test_unsupported_provider_errors_without_touching_anything(mod, monkeypatch):
    called = []
    monkeypatch.setattr(mod, "_fetch_api_key",
                        lambda *_a, **_k: called.append("fetch") or "key")
    rc = mod.main(["--company", "ent-x", "--provider", "zoom"])
    assert rc == 2
    assert called == [], "must refuse before touching any credential/API"


# ── --dry-run (default) writes nothing and reports a count + cost ────────


def test_dry_run_is_the_default_and_writes_nothing(mod, monkeypatch, caplog):
    records = [_rec("FF1"), _rec("FF2")]
    monkeypatch.setattr(mod, "_fetch_api_key", lambda *_a, **_k: "api-key")
    monkeypatch.setattr(mod.fireflies, "pull", lambda *a, **k: iter(records))
    monkeypatch.setattr(mod, "seen_hashes", lambda *_a, **_k: set())

    run_batch_calls = []
    monkeypatch.setattr(mod, "run_batch", lambda *a, **k: run_batch_calls.append(1))
    record_hashes_calls = []
    monkeypatch.setattr(mod, "record_hashes",
                        lambda *a, **k: record_hashes_calls.append(1))
    facade_calls = []
    monkeypatch.setattr(mod, "GraphFacade",
                        lambda *a, **k: facade_calls.append(1))

    with caplog.at_level("INFO", logger="backfill_fireflies_kg"):
        rc = mod.main(["--company", "ent-x"])

    assert rc == 0
    assert run_batch_calls == [], "dry-run must never call the batch API"
    assert record_hashes_calls == [], "dry-run must write NOTHING to the ledger"
    assert facade_calls == [], "dry-run must never construct a write-capable facade"
    text = " ".join(r.message for r in caplog.records)
    assert "DRY RUN" in text
    assert "2 call" in text
    assert "estimated cost" in text.lower()


def test_no_fresh_calls_is_a_clean_no_op(mod, monkeypatch, caplog):
    monkeypatch.setattr(mod, "_fetch_api_key", lambda *_a, **_k: "api-key")
    monkeypatch.setattr(mod.fireflies, "pull", lambda *a, **k: iter([_rec("FF1")]))
    # Everything already in the ledger.
    monkeypatch.setattr(mod, "seen_hashes",
                        lambda _eid, hs: set(hs))
    with caplog.at_level("INFO", logger="backfill_fireflies_kg"):
        rc = mod.main(["--company", "ent-x", "--run"])
    assert rc == 0
    assert any("nothing to do" in r.message for r in caplog.records)


# ── bulk-batch request assembly ───────────────────────────────────────────


def test_batch_request_assembly_builds_one_request_per_pass_with_correct_custom_id(mod):
    records = [_rec("FF1"), _rec("FF2")]
    requests, unit_by_id = mod._build_batch_requests(records, "ent-x")

    custom_ids = {r.custom_id for r in requests}
    assert custom_ids == {"FF1-main", "FF1-checklist", "FF2-main", "FF2-checklist"}
    assert len(requests) == 4

    # Each request's params are what build_extract_request/build_checklist_request
    # produce — a real messages.create-shaped dict with the model + schema tool.
    by_id = {r.custom_id: r.params for r in requests}
    from app.graph import extractor as ex
    assert by_id["FF1-main"]["tools"][0]["input_schema"] == ex._EXTRACT_SCHEMA
    assert by_id["FF1-checklist"]["tools"][0]["input_schema"] == ex._CHECKLIST_SCHEMA
    assert "tool_choice" in by_id["FF1-main"]
    assert "tool_choice" in by_id["FF1-checklist"]

    # unit_by_id carries what parse_*_response needs to write the result back.
    assert unit_by_id["FF1-main"][0] == "main"
    assert unit_by_id["FF1-checklist"][0] == "checklist"
    assert unit_by_id["FF1-main"][1].external_id == "FF1"
    assert unit_by_id["FF1-main"][2] == "fireflies-backfill-FF1"


def test_custom_ids_are_pattern_valid_for_the_real_batches_api(mod):
    """The real Anthropic Batches API rejects a `custom_id` outside
    `^[a-zA-Z0-9_-]{1,64}$` (discovered live: the original `:` separator
    400s the WHOLE batch submission). Every generated id must match that
    pattern, and never exceed 64 characters."""
    import re
    pattern = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
    records = [_rec("FF1"), _rec("FF-with-a-dash_and_1234"),
               _rec("x" * 80)]  # pathologically long external_id
    requests, _unit_by_id = mod._build_batch_requests(records, "ent-x")
    for r in requests:
        assert pattern.match(r.custom_id), f"{r.custom_id!r} is not custom_id-safe"
        assert ":" not in r.custom_id


def test_custom_ids_round_trip_back_to_the_correct_call_and_pass(mod):
    """The batch result -> (call, pass) lookup is a dict lookup on the FULL
    custom_id (`unit_by_id[custom_id]`) — proves it holds for every request
    built, not just the two IDs asserted by name elsewhere."""
    records = [_rec("FF1"), _rec("FF2"), _rec("FF3")]
    requests, unit_by_id = mod._build_batch_requests(records, "ent-x")
    assert len(requests) == 6
    for r in requests:
        pass_name, rec, _doc_name, _text = unit_by_id[r.custom_id]
        assert pass_name in ("main", "checklist")
        assert r.custom_id == f"{rec.external_id}{'-main' if pass_name == 'main' else '-checklist'}"


def test_custom_id_prefix_sanitizes_an_unsafe_external_id(mod, caplog):
    """A stray disallowed character must never silently corrupt/collide a
    custom_id — it is sanitized AND logged loudly, since Fireflies ids are
    alphanumeric in practice and this should never fire for real data."""
    with caplog.at_level("WARNING", logger="backfill_fireflies_kg"):
        prefix = mod._custom_id_prefix("ff:weird/id with spaces")
    import re
    assert re.match(r"^[a-zA-Z0-9_-]+$", prefix)
    assert any("not custom_id-safe" in r.message for r in caplog.records)


def test_custom_id_prefix_is_unchanged_for_a_safe_external_id(mod, caplog):
    with caplog.at_level("WARNING", logger="backfill_fireflies_kg"):
        prefix = mod._custom_id_prefix("FF-1234_abc")
    assert prefix == "FF-1234_abc"
    assert not any("not custom_id-safe" in r.message for r in caplog.records)


def test_batch_main_pass_gets_the_digest_checklist_pass_gets_the_full_transcript(mod):
    """Config B split preserved: the main-pass request is built from the
    condensed digest, the checklist-pass request from the full transcript."""
    records = [_rec("FF1")]
    requests, _unit_by_id = mod._build_batch_requests(records, "ent-x")
    by_id = {r.custom_id: r.params for r in requests}
    main_user = by_id["FF1-main"]["messages"][0]["content"]
    checklist_user = by_id["FF1-checklist"]["messages"][0]["content"]
    assert "transcript:" not in main_user
    assert "transcript:" in checklist_user
    assert "full text FF1" in checklist_user
    assert "full text FF1" not in main_user


# ── _submit_batch binds company_llm_key + usage_scope around run_batch ────
#
# Live-verify (2026-08-27) found the CLI calling `run_batch` bare left every
# batched usage row unattributed: `record_external_usage` reads the acting
# company off `app.llm_keys.current_company_id()` and the feature/operation
# label off `app.usage_context.current_scope()`, both ContextVars that
# `gateway.llm_call` normally binds and this CLI, calling `run_batch`
# directly, never entered.


def test_submit_batch_binds_company_and_usage_scope_around_run_batch(mod, monkeypatch):
    from app.llm_keys import current_company_id
    from app.usage_context import current_scope

    seen = {}

    def spy_run_batch(requests, **kw):
        seen["company_id"] = current_company_id()
        seen["scope"] = current_scope()
        return {"r0": "ignored"}

    monkeypatch.setattr(mod, "run_batch", spy_run_batch)

    # Outside the call, nothing is bound — proves the binding is scoped to
    # the call, not a process-wide side effect leaking from an earlier test.
    assert current_company_id() is None

    mod._submit_batch("ent-x", [])

    assert seen["company_id"] == "ent-x"
    assert seen["scope"].feature not in (None, "unattributed")
    assert seen["scope"].operation == "kg_backfill"

    # And the binding is unwound again after the call.
    assert current_company_id() is None


def test_submit_batch_feature_matches_the_sync_fallback_agents_bucket(mod):
    """Batch and sync-fallback attribute to the SAME feature bucket (see
    `_AGENT`) — cost/feature reporting must not split by which transport a
    given run happened to take."""
    from app.usage_context import feature_for_agent
    assert feature_for_agent(mod._AGENT) not in (None, "unattributed")


# ── run_batch -> None fallback routes to the existing sync path ──────────


def test_run_batch_none_falls_back_to_sync_extraction(mod, monkeypatch):
    records = [_rec("FF1"), _rec("FF2")]
    monkeypatch.setattr(mod, "_fetch_api_key", lambda *_a, **_k: "api-key")
    monkeypatch.setattr(mod.fireflies, "pull", lambda *a, **k: iter(records))
    monkeypatch.setattr(mod, "seen_hashes", lambda *_a, **_k: set())
    monkeypatch.setattr(mod, "run_batch", lambda *a, **k: None)
    monkeypatch.setattr(mod, "GraphFacade", lambda *a, **k: object())

    sync_calls = []
    parse_calls = []

    def fake_extract_document(facade, enterprise_id, *, doc_name, text, **kw):
        sync_calls.append(("extract", doc_name))
        return {"signals": 1, "themes": 1, "skipped": 0, "signal_ids": []}

    def fake_run_checklist_pass(facade, enterprise_id, *, doc_name, text, **kw):
        sync_calls.append(("checklist", doc_name))
        return {"signals": 1, "themes": 0, "skipped": 0, "signal_ids": []}

    def fake_parse_extract_response(*a, **k):
        parse_calls.append("extract")
        return {"signals": 0, "themes": 0, "skipped": 0, "signal_ids": []}

    def fake_parse_checklist_response(*a, **k):
        parse_calls.append("checklist")
        return {"signals": 0, "themes": 0, "skipped": 0, "signal_ids": []}

    recorded = []
    monkeypatch.setattr(mod, "extract_document", fake_extract_document)
    monkeypatch.setattr(mod, "run_checklist_pass", fake_run_checklist_pass)
    monkeypatch.setattr(mod, "parse_extract_response", fake_parse_extract_response)
    monkeypatch.setattr(mod, "parse_checklist_response", fake_parse_checklist_response)
    monkeypatch.setattr(mod, "record_hashes", lambda *a, **k: recorded.append(a))

    rc = mod.main(["--company", "ent-x", "--run"])

    assert rc == 0
    assert parse_calls == [], "the batch-result parse path must not run on a None batch"
    assert [c for c in sync_calls if c[0] == "extract"] == \
           [("extract", "fireflies-backfill-FF1"), ("extract", "fireflies-backfill-FF2")]
    assert [c for c in sync_calls if c[0] == "checklist"] == \
           [("checklist", "fireflies-backfill-FF1"), ("checklist", "fireflies-backfill-FF2")]
    assert len(recorded) == 2, "the ledger advances once per successfully extracted call"


def test_batched_results_route_through_parse_not_sync(mod, monkeypatch):
    records = [_rec("FF1")]
    monkeypatch.setattr(mod, "_fetch_api_key", lambda *_a, **_k: "api-key")
    monkeypatch.setattr(mod.fireflies, "pull", lambda *a, **k: iter(records))
    monkeypatch.setattr(mod, "seen_hashes", lambda *_a, **_k: set())
    monkeypatch.setattr(mod, "GraphFacade", lambda *a, **k: object())

    fake_message = SimpleNamespace(content=[])

    def fake_run_batch(requests, **kw):
        return {r.custom_id: fake_message for r in requests}

    monkeypatch.setattr(mod, "run_batch", fake_run_batch)

    sync_calls = []
    parse_calls = []
    monkeypatch.setattr(mod, "extract_document",
                        lambda *a, **k: sync_calls.append("extract"))
    monkeypatch.setattr(mod, "run_checklist_pass",
                        lambda *a, **k: sync_calls.append("checklist"))

    def fake_parse_extract_response(*a, **k):
        parse_calls.append("extract")
        return {"signals": 1, "themes": 0, "skipped": 0, "signal_ids": []}

    def fake_parse_checklist_response(*a, **k):
        parse_calls.append("checklist")
        return {"signals": 1, "themes": 0, "skipped": 0, "signal_ids": []}

    monkeypatch.setattr(mod, "parse_extract_response", fake_parse_extract_response)
    monkeypatch.setattr(mod, "parse_checklist_response", fake_parse_checklist_response)
    recorded = []
    monkeypatch.setattr(mod, "record_hashes", lambda *a, **k: recorded.append(a))

    rc = mod.main(["--company", "ent-x", "--run"])

    assert rc == 0
    assert sync_calls == [], "the sync fallback must not run when run_batch succeeds"
    assert set(parse_calls) == {"extract", "checklist"}
    assert len(recorded) == 1

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


# ── batch deadline: generous, not the 900s single-request default ────────
#
# Live-verify (2026-08-27): the first real run submitted 798 requests as ONE
# batch using `run_batch`'s bare 900s/15min default (sized for a single
# request), missed it, and silently fell back to full-price sequential sync
# for all 399 calls. `_submit_batch` must always pass an explicit, generous
# deadline — never the bare default.


def test_submit_batch_passes_a_generous_deadline_by_default(mod, monkeypatch):
    seen = {}
    monkeypatch.setattr(mod, "run_batch",
                        lambda requests, **kw: seen.update(kw) or None)
    mod._submit_batch("ent-x", [])
    assert seen["deadline_s"] > 900, (
        "must be well above run_batch's bare single-request default")
    assert seen["deadline_s"] == mod.DEFAULT_BATCH_DEADLINE_HOURS * 3600


def test_main_threads_batch_deadline_hours_through_to_run_batch(mod, monkeypatch):
    """`--batch-deadline-hours` actually reaches `run_batch`'s `deadline_s`,
    not just the module-level default."""
    records = [_rec("FF1")]
    monkeypatch.setattr(mod, "_fetch_api_key", lambda *_a, **_k: "api-key")
    monkeypatch.setattr(mod.fireflies, "pull", lambda *a, **k: iter(records))
    monkeypatch.setattr(mod, "seen_hashes", lambda *_a, **_k: set())
    monkeypatch.setattr(mod, "GraphFacade", lambda *a, **k: object())
    monkeypatch.setattr(mod, "record_hashes", lambda *a, **k: None)

    seen_deadlines = []

    def fake_run_batch(requests, **kw):
        seen_deadlines.append(kw.get("deadline_s"))
        return None  # falls back to sync — irrelevant to what we're proving

    monkeypatch.setattr(mod, "run_batch", fake_run_batch)
    monkeypatch.setattr(mod, "extract_document",
                        lambda *a, **k: {"signals": 0, "themes": 0, "skipped": 0,
                                         "signal_ids": []})
    monkeypatch.setattr(mod, "run_checklist_pass",
                        lambda *a, **k: {"signals": 0, "themes": 0, "skipped": 0,
                                         "signal_ids": []})

    rc = mod.main(["--company", "ent-x", "--run", "--batch-deadline-hours", "8"])
    assert rc == 0
    assert seen_deadlines == [8 * 3600]


def test_default_batch_deadline_is_hours_not_the_bare_llm_batch_default(mod, monkeypatch):
    """The CLI's default must be measured in HOURS and comfortably clear
    `app.llm_batch.DEFAULT_DEADLINE_S` (900s) — the exact gap live-verify
    found."""
    from app import llm_batch as llm_batch_mod
    assert mod.DEFAULT_BATCH_DEADLINE_HOURS * 3600 > llm_batch_mod.DEFAULT_DEADLINE_S


# ── chunking: a large call set splits into multiple bulk submissions ─────
#
# CHUNK_SIZE bounds one `run_batch` submission's blast radius: if one chunk
# stalls or misses its deadline, only ITS calls fall back to sync, not the
# whole run. These pin that a call set bigger than one chunk actually DOES
# split into multiple `run_batch` calls, and that results reassemble
# correctly (custom_id -> call/pass mapping holds independently per chunk).


def test_large_call_set_splits_into_multiple_run_batch_submissions(mod, monkeypatch):
    monkeypatch.setattr(mod, "CHUNK_SIZE", 2)
    records = [_rec(f"FF{i}") for i in range(5)]  # 5 calls, chunk size 2 -> 3 chunks
    monkeypatch.setattr(mod, "_fetch_api_key", lambda *_a, **_k: "api-key")
    monkeypatch.setattr(mod.fireflies, "pull", lambda *a, **k: iter(records))
    monkeypatch.setattr(mod, "seen_hashes", lambda *_a, **_k: set())
    monkeypatch.setattr(mod, "GraphFacade", lambda *a, **k: object())
    recorded = []
    monkeypatch.setattr(mod, "record_hashes", lambda *a, **k: recorded.append(a))

    submissions = []

    def fake_run_batch(requests, **kw):
        submissions.append([r.custom_id for r in requests])
        # Every request in THIS submission "succeeds" — echo an empty-content
        # message per custom_id so parse_*_response has something to parse.
        return {r.custom_id: SimpleNamespace(content=[]) for r in requests}

    monkeypatch.setattr(mod, "run_batch", fake_run_batch)

    parsed = []

    def fake_parse_extract_response(facade, enterprise_id, message, *, doc_name, **kw):
        parsed.append(("extract", doc_name))
        return {"signals": 1, "themes": 0, "skipped": 0, "signal_ids": []}

    def fake_parse_checklist_response(facade, enterprise_id, message, *, doc_name, **kw):
        parsed.append(("checklist", doc_name))
        return {"signals": 1, "themes": 0, "skipped": 0, "signal_ids": []}

    monkeypatch.setattr(mod, "parse_extract_response", fake_parse_extract_response)
    monkeypatch.setattr(mod, "parse_checklist_response", fake_parse_checklist_response)

    rc = mod.main(["--company", "ent-x", "--run"])

    assert rc == 0
    # 5 calls / chunk size 2 -> 3 submissions (2, 2, 1 calls -> 4, 4, 2 requests).
    assert len(submissions) == 3
    assert [len(s) for s in submissions] == [4, 4, 2]
    # Every call's two requests round-tripped back to the RIGHT call/pass,
    # independently within its own chunk's submission.
    assert len(parsed) == 10  # 5 calls x 2 passes
    assert {p[1] for p in parsed} == {f"fireflies-backfill-FF{i}" for i in range(5)}
    assert len(recorded) == 5, "every call's ledger hash advanced exactly once"


def test_one_stalled_chunk_falls_back_without_affecting_other_chunks(mod, monkeypatch):
    """The whole point of chunking: chunk 2 misses its deadline (`run_batch`
    returns None for it) and falls back to sync, while chunk 1 (already
    batched) is completely unaffected."""
    monkeypatch.setattr(mod, "CHUNK_SIZE", 1)
    records = [_rec("FF1"), _rec("FF2")]
    monkeypatch.setattr(mod, "_fetch_api_key", lambda *_a, **_k: "api-key")
    monkeypatch.setattr(mod.fireflies, "pull", lambda *a, **k: iter(records))
    monkeypatch.setattr(mod, "seen_hashes", lambda *_a, **_k: set())
    monkeypatch.setattr(mod, "GraphFacade", lambda *a, **k: object())
    monkeypatch.setattr(mod, "record_hashes", lambda *a, **k: None)

    call_n = {"n": 0}

    def fake_run_batch(requests, **kw):
        call_n["n"] += 1
        if call_n["n"] == 1:
            return {r.custom_id: SimpleNamespace(content=[]) for r in requests}
        return None  # second chunk "stalls"

    monkeypatch.setattr(mod, "run_batch", fake_run_batch)
    monkeypatch.setattr(mod, "parse_extract_response",
                        lambda *a, **k: {"signals": 1, "themes": 0, "skipped": 0,
                                         "signal_ids": []})
    monkeypatch.setattr(mod, "parse_checklist_response",
                        lambda *a, **k: {"signals": 1, "themes": 0, "skipped": 0,
                                         "signal_ids": []})
    sync_calls = []
    monkeypatch.setattr(
        mod, "extract_document",
        lambda *a, doc_name, **k: sync_calls.append(("extract", doc_name)) or
        {"signals": 1, "themes": 0, "skipped": 0, "signal_ids": []},
    )
    monkeypatch.setattr(
        mod, "run_checklist_pass",
        lambda *a, doc_name, **k: sync_calls.append(("checklist", doc_name)) or
        {"signals": 1, "themes": 0, "skipped": 0, "signal_ids": []},
    )

    rc = mod.main(["--company", "ent-x", "--run"])

    assert rc == 0
    # Chunk 1 (FF1) went through the batch/parse path — no sync call for it.
    assert not any("FF1" in c[1] for c in sync_calls)
    # Chunk 2 (FF2) fell back to sync.
    assert sync_calls == [("extract", "fireflies-backfill-FF2"),
                          ("checklist", "fireflies-backfill-FF2")]


# ── malformed batch-result shape: skip gracefully, don't take the batch down ─
#
# Live-verify (2026-08-27): one call's batched MAIN-pass result had `signals`
# come back as a bare string instead of a list — an `AttributeError: 'str'
# object has no attribute 'get'` deep inside the write path, on the exact
# same bulk backfill run that also tripped the statement_timeout burst.
# `app.graph.extractor._finish_extract`/`_finish_checklist` now guard the
# shape explicitly (`MalformedLLMResultError`); this proves the CLI's own
# per-call isolation (`_run_batched`) still degrades that ONE call gracefully
# — logged, ledger not advanced, retried next run — while the OTHER call in
# the SAME batch/chunk writes and advances normally. Uses the REAL (not
# monkeypatched) `parse_extract_response`/`parse_checklist_response` so this
# exercises the actual guard, not a stand-in.


def _tool_use_message(input_dict: dict):
    """A minimal stand-in for a batched Anthropic `Message` whose forced
    `submit_response` tool was invoked with `input_dict` — the shape
    `app.llm.parse_tool_response` expects."""
    return SimpleNamespace(content=[
        SimpleNamespace(type="tool_use", name="submit_response", input=input_dict)
    ])


def test_malformed_batch_result_shape_is_skipped_other_call_in_batch_still_processes(
    mod, monkeypatch, caplog,
):
    records = [_rec("FF1"), _rec("FF2")]
    requests, unit_by_id = mod._build_batch_requests(records, "ent-x")

    results = {}
    for r in requests:
        pass_name, rec, _doc_name, _text = unit_by_id[r.custom_id]
        if rec.external_id == "FF1" and pass_name == "main":
            # The exact live-verify shape: 'signals' is a bare string.
            results[r.custom_id] = _tool_use_message(
                {"signals": "a bare string, not a list"})
        elif pass_name == "main":
            results[r.custom_id] = _tool_use_message({"signals": []})
        else:
            results[r.custom_id] = _tool_use_message({"checklist": []})

    hashes = {id(r): f"hash-{r.external_id}" for r in records}
    recorded = []
    monkeypatch.setattr(mod, "record_hashes", lambda *a, **k: recorded.append(a))

    with caplog.at_level("WARNING"):
        tally, extracted = mod._run_batched(
            object(), "ent-x", records, hashes, unit_by_id, results,
        )

    assert extracted == {"FF2"}, (
        "FF1's malformed batch result must not be counted as extracted")
    assert len(recorded) == 1, (
        "only FF2's ledger hash advances — FF1 is retried on the next run")
    assert any("FF1" in r.message for r in caplog.records), (
        "the skip must be logged, not silently swallowed")
    # No exception escaped _run_batched — the whole point of the guard.


# ── write throttle: pace the batched-write loop (burst-load 57014 guard) ────


def _empty_result_for(unit_by_id: dict, custom_id: str):
    pass_name, *_rest = unit_by_id[custom_id]
    return _tool_use_message({"signals": []} if pass_name == "main"
                             else {"checklist": []})


def test_run_batched_pauses_write_throttle_seconds_between_calls(mod, monkeypatch):
    """WRITE_THROTTLE_S paces the batched-write loop — a burst-write guard
    against the transient 57014 a tight-window insert burst tripped live
    (see the module-level comment). Proven via a spy on time.sleep, never a
    real wait."""
    records = [_rec("FF1"), _rec("FF2")]
    requests, unit_by_id = mod._build_batch_requests(records, "ent-x")
    results = {r.custom_id: _empty_result_for(unit_by_id, r.custom_id)
               for r in requests}
    hashes = {id(r): f"hash-{r.external_id}" for r in records}
    monkeypatch.setattr(mod, "record_hashes", lambda *a, **k: None)
    sleeps = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))

    mod._run_batched(object(), "ent-x", records, hashes, unit_by_id, results)

    assert sleeps == [mod.WRITE_THROTTLE_S] * len(records)


def test_write_throttle_disabled_when_set_to_zero(mod, monkeypatch):
    monkeypatch.setattr(mod, "WRITE_THROTTLE_S", 0)
    records = [_rec("FF1")]
    requests, unit_by_id = mod._build_batch_requests(records, "ent-x")
    results = {r.custom_id: _empty_result_for(unit_by_id, r.custom_id)
               for r in requests}
    hashes = {id(r): f"hash-{r.external_id}" for r in records}
    monkeypatch.setattr(mod, "record_hashes", lambda *a, **k: None)
    sleeps = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))

    mod._run_batched(object(), "ent-x", records, hashes, unit_by_id, results)

    assert sleeps == []

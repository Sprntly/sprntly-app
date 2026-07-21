"""Fan-out ticket generation: plan → parallel enrich.

The LLM gateway is mocked — a fake `llm_call` dispatches on prompt_version to
return stubs for the plan leg and full stories for each enrich batch, so these
tests never reach a real model or the DB.
"""
from __future__ import annotations

import threading

import pytest

import app.stories.generate as gen
from app.graph.gateway import LLMResult
from app.stories.generate import (
    ENRICH_PROMPT_VERSION,
    PLAN_PROMPT_VERSION,
    PROMPT_VERSION,
    _stories_from_output,
    generate_from_input,
    generate_user_stories,
)


def _result(output):
    return LLMResult(
        output=output, model="claude-sonnet-4-6", prompt_version="pv+skill@x",
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="tool_use",
    )


def _story(title: str, body: str = "b") -> dict:
    return {"title": title, "body": body,
            "acceptance_criteria": ["Given x, When y, Then z."]}


def _fake_llm(stub_titles, *, record=None, lock=None):
    """Build a fake llm_call. The plan leg returns one stub per title; each enrich
    leg returns one full story per stub title it was asked to expand."""
    def _call(**kw):
        pv = kw.get("prompt_version", "")
        if record is not None:
            with (lock or threading.Lock()):
                record.append(kw)
        if pv == PLAN_PROMPT_VERSION:
            return _result({"stubs": [{"title": t, "summary": f"do {t}"}
                                      for t in stub_titles]})
        if pv == ENRICH_PROMPT_VERSION:
            # Expand exactly the titles named in this batch's input.
            titles = [t for t in stub_titles
                      if f"- {t}" in kw["input"].split("Tickets to expand in THIS batch")[-1]]
            return _result({"stories": [_story(t) for t in titles]})
        # single path
        return _result({"stories": [_story(t) for t in stub_titles]})
    return _call


def test_fanout_plans_then_enriches_all_stubs(isolated_settings, monkeypatch):
    titles = [f"T{i}" for i in range(10)]
    calls: list[dict] = []
    monkeypatch.setattr(gen, "llm_call", _fake_llm(titles, record=calls))

    stories = generate_from_input(
        "ent-A", prd_input="PRD body", strategy="fanout",
        batch_size=4, max_parallel=4,
    )

    got = {s.title for s in stories}
    assert got == set(titles), "every planned stub is expanded into a ticket"
    plan_calls = [c for c in calls if c["prompt_version"] == PLAN_PROMPT_VERSION]
    enrich_calls = [c for c in calls if c["prompt_version"] == ENRICH_PROMPT_VERSION]
    assert len(plan_calls) == 1, "exactly one plan call"
    assert len(enrich_calls) == 3, "10 stubs / batch 4 = 3 enrich batches"


def test_fanout_empty_plan_falls_back_to_single(isolated_settings, monkeypatch):
    calls: list[dict] = []

    def _call(**kw):
        calls.append(kw)
        if kw["prompt_version"] == PLAN_PROMPT_VERSION:
            return _result({"stubs": []})
        return _result({"stories": [_story("Fallback ticket")]})

    monkeypatch.setattr(gen, "llm_call", _call)
    stories = generate_from_input("ent-A", prd_input="PRD", strategy="fanout")

    assert [s.title for s in stories] == ["Fallback ticket"]
    versions = [c["prompt_version"] for c in calls]
    assert PLAN_PROMPT_VERSION in versions
    assert PROMPT_VERSION in versions, "fell back to the single-call path"
    assert ENRICH_PROMPT_VERSION not in versions, "no enrich after an empty plan"


def test_fanout_gives_every_batch_the_full_roster(isolated_settings, monkeypatch):
    titles = [f"T{i}" for i in range(9)]
    calls: list[dict] = []
    lock = threading.Lock()
    monkeypatch.setattr(gen, "llm_call", _fake_llm(titles, record=calls, lock=lock))

    generate_from_input("ent-A", prd_input="PRD", strategy="fanout",
                        batch_size=3, max_parallel=3)

    enrich = [c for c in calls if c["prompt_version"] == ENRICH_PROMPT_VERSION]
    assert len(enrich) == 3
    for c in enrich:
        roster = c["input"].split("Full ticket roster")[-1].split("Tickets to expand")[0]
        for t in titles:
            assert f"- {t}" in roster, f"{t} missing from a batch's dependency roster"


def test_fanout_dedups_overlapping_stories(isolated_settings, monkeypatch):
    def _call(**kw):
        if kw["prompt_version"] == PLAN_PROMPT_VERSION:
            return _result({"stubs": [{"title": "A"}, {"title": "B"}]})
        # Every enrich batch returns the SAME two stories (worst-case overlap).
        return _result({"stories": [_story("A"), _story("B")]})

    monkeypatch.setattr(gen, "llm_call", _call)
    stories = generate_from_input("ent-A", prd_input="PRD", strategy="fanout",
                                  batch_size=1, max_parallel=2)

    assert sorted(s.title for s in stories) == ["A", "B"], "dedup by stable_id"


def test_fanout_stats_capture_per_phase(isolated_settings, monkeypatch):
    titles = [f"T{i}" for i in range(6)]
    monkeypatch.setattr(gen, "llm_call", _fake_llm(titles))
    stats: dict = {}

    generate_from_input("ent-A", prd_input="PRD", strategy="fanout",
                        batch_size=3, max_parallel=3, stats_out=stats)

    assert stats["strategy"] == "fanout"
    assert stats["n_stubs"] == 6
    assert stats["n_batches"] == 2
    assert stats["n_stories"] == 6
    labels = [c["label"] for c in stats["calls"]]
    assert labels[0] == "plan"
    assert sum(1 for label in labels if label.startswith("enrich")) == 2


def test_single_strategy_makes_one_call(isolated_settings, monkeypatch):
    calls: list[dict] = []

    def _call(**kw):
        calls.append(kw)
        return _result({"stories": [_story("Only")]})

    monkeypatch.setattr(gen, "llm_call", _call)
    stats: dict = {}
    stories = generate_from_input("ent-A", prd_input="PRD", strategy="single",
                                  stats_out=stats)

    assert [s.title for s in stories] == ["Only"]
    assert len(calls) == 1
    assert calls[0]["prompt_version"] == PROMPT_VERSION
    assert stats["strategy"] == "single"


def test_fanout_on_batch_streams_growing_partial_sets(isolated_settings, monkeypatch):
    titles = [f"T{i}" for i in range(9)]  # 3 batches of 3
    monkeypatch.setattr(gen, "llm_call", _fake_llm(titles))
    updates: list[tuple[int, int, int]] = []  # (n_stories_so_far, done, total)

    def _on_batch(stories, done, total):
        updates.append((len(stories), done, total))

    result = generate_from_input(
        "ent-A", prd_input="PRD", strategy="fanout",
        batch_size=3, max_parallel=3, on_batch=_on_batch,
    )

    assert len(result) == 9
    assert len(updates) == 3, "one callback per batch"
    # Progress counter is monotonic and terminates at total.
    assert [u[1] for u in updates] == [1, 2, 3]
    assert all(u[2] == 3 for u in updates)
    # Accumulated ticket count only grows and ends at the full set.
    counts = [u[0] for u in updates]
    assert counts == sorted(counts)
    assert counts[-1] == 9


def test_single_strategy_never_calls_on_batch(isolated_settings, monkeypatch):
    monkeypatch.setattr(gen, "llm_call",
                        lambda **kw: _result({"stories": [_story("Only")]}))
    fired = []
    generate_from_input("ent-A", prd_input="PRD", strategy="single",
                        on_batch=lambda *a: fired.append(a))
    assert fired == [], "the single path has no batches to stream"


def test_fanout_on_plan_fires_once_before_any_enrich(isolated_settings, monkeypatch):
    """on_plan delivers the full stub roster + batch count as soon as the plan
    leg completes — BEFORE any enrich call, so a UI can render skeletons ~20-35s
    in instead of waiting for the first full batch (on a single-wave run that's
    the very end)."""
    lock = threading.Lock()
    record: list[dict] = []
    monkeypatch.setattr(
        gen, "llm_call", _fake_llm(["A", "B", "C"], record=record, lock=lock)
    )
    planned: list[tuple[list[dict], int]] = []

    def _on_plan(stubs, total):
        # At callback time only the plan call has gone out.
        with lock:
            assert [kw["prompt_version"] for kw in record] == [PLAN_PROMPT_VERSION]
        planned.append((stubs, total))

    result = generate_from_input(
        "ent-A", prd_input="PRD", strategy="fanout",
        batch_size=2, max_parallel=2, on_plan=_on_plan,
    )

    assert len(result) == 3
    assert len(planned) == 1, "on_plan fires exactly once"
    stubs, total = planned[0]
    assert [s["title"] for s in stubs] == ["A", "B", "C"]
    assert stubs[0]["summary"] == "do A"
    assert total == 2, "3 stubs at batch_size=2 → 2 batches"


def test_fanout_on_plan_exception_never_breaks_generation(isolated_settings, monkeypatch):
    monkeypatch.setattr(gen, "llm_call", _fake_llm(["A", "B"]))

    def _boom(stubs, total):
        raise RuntimeError("display hiccup")

    result = generate_from_input(
        "ent-A", prd_input="PRD", strategy="fanout",
        batch_size=2, max_parallel=2, on_plan=_boom,
    )
    assert sorted(s.title for s in result) == ["A", "B"]


def test_single_strategy_never_calls_on_plan(isolated_settings, monkeypatch):
    monkeypatch.setattr(gen, "llm_call",
                        lambda **kw: _result({"stories": [_story("Only")]}))
    fired = []
    generate_from_input("ent-A", prd_input="PRD", strategy="single",
                        on_plan=lambda *a: fired.append(a))
    assert fired == [], "the single path has no plan leg"


def test_enrich_tolerates_string_items_in_stories(isolated_settings, monkeypatch):
    """Regression: a real PRD made the enrich model return a bare string inside
    `stories`; `s.get('title')` then raised 'str has no attribute get' and failed
    the whole run. Malformed items must be skipped, valid ones kept."""
    def _call(**kw):
        if kw["prompt_version"] == PLAN_PROMPT_VERSION:
            return _result({"stubs": [{"title": "A"}, {"title": "B"}]})
        # One good ticket + a stray string + a None the model slipped in.
        return _result({"stories": [_story("A"), "just a title string", None]})

    monkeypatch.setattr(gen, "llm_call", _call)
    stories = generate_from_input("ent-A", prd_input="PRD", strategy="fanout",
                                  batch_size=2, max_parallel=2)

    assert [s.title for s in stories] == ["A"], "valid ticket survives, junk dropped"


def test_fanout_all_batches_empty_falls_back_to_single(isolated_settings, monkeypatch):
    """If every enrich batch yields 0 usable tickets, fall back to the single
    call rather than returning an empty set for a PRD that did plan stubs."""
    calls: list[str] = []

    def _call(**kw):
        pv = kw["prompt_version"]
        calls.append(pv)
        if pv == PLAN_PROMPT_VERSION:
            return _result({"stubs": [{"title": "A"}, {"title": "B"}]})
        if pv == ENRICH_PROMPT_VERSION:
            return _result({"stories": ["garbage", None]})  # all malformed
        return _result({"stories": [_story("Recovered via single")]})  # PROMPT_VERSION

    monkeypatch.setattr(gen, "llm_call", _call)
    stories = generate_from_input("ent-A", prd_input="PRD", strategy="fanout",
                                  batch_size=2, max_parallel=2)

    assert [s.title for s in stories] == ["Recovered via single"]
    assert PROMPT_VERSION in calls, "fell back to the single-call path"


def test_stories_from_output_handles_non_list(isolated_settings, monkeypatch):
    """Guard the shape where `stories` itself isn't a list."""
    def _call(**kw):
        return _result({"stories": "not a list at all"})

    monkeypatch.setattr(gen, "llm_call", _call)
    stories = generate_from_input("ent-A", prd_input="PRD", strategy="single")
    assert stories == []


# ── per-batch enrich retry ───────────────────────────────────────────────────

def test_enrich_batch_retries_on_shortfall_and_keeps_better(isolated_settings, monkeypatch):
    """A batch whose first (temp-0) pass drops a malformed item is retried once
    at a non-zero temperature; the fuller retry result is kept."""
    enrich_calls: list[float] = []

    def _call(**kw):
        pv = kw["prompt_version"]
        if pv == PLAN_PROMPT_VERSION:
            return _result({"stubs": [{"title": "A"}, {"title": "B"}]})
        if pv == ENRICH_PROMPT_VERSION:
            temp = kw.get("temperature", 0)
            enrich_calls.append(temp)
            if temp == 0:
                return _result({"stories": [_story("A"), "malformed-string"]})  # 1 usable
            return _result({"stories": [_story("A"), _story("B")]})  # retry: both good
        return _result({"stories": []})

    monkeypatch.setattr(gen, "llm_call", _call)
    stories = generate_from_input("ent-A", prd_input="PRD", strategy="fanout",
                                  batch_size=2, max_parallel=1)

    assert sorted(s.title for s in stories) == ["A", "B"], "retry recovered the lost ticket"
    assert enrich_calls[0] == 0, "first pass is deterministic (temp 0)"
    assert enrich_calls[1] > 0, "retry samples at a non-zero temperature"
    assert len(enrich_calls) == 2, "exactly one retry"


def test_enrich_batch_no_retry_when_complete(isolated_settings, monkeypatch):
    """A batch that returns one ticket per stub on the first pass is not retried."""
    enrich_calls = []

    def _call(**kw):
        pv = kw["prompt_version"]
        if pv == PLAN_PROMPT_VERSION:
            return _result({"stubs": [{"title": "A"}, {"title": "B"}]})
        if pv == ENRICH_PROMPT_VERSION:
            enrich_calls.append(kw.get("temperature", 0))
            return _result({"stories": [_story("A"), _story("B")]})
        return _result({"stories": []})

    monkeypatch.setattr(gen, "llm_call", _call)
    generate_from_input("ent-A", prd_input="PRD", strategy="fanout",
                        batch_size=2, max_parallel=1)
    assert enrich_calls == [0], "no retry when the batch is already complete"


def test_enrich_batch_retry_keeps_best_when_both_short(isolated_settings, monkeypatch):
    """If the retry is also short, keep whichever attempt had more tickets."""
    def _call(**kw):
        pv = kw["prompt_version"]
        if pv == PLAN_PROMPT_VERSION:
            return _result({"stubs": [{"title": "A"}, {"title": "B"}, {"title": "C"}]})
        if pv == ENRICH_PROMPT_VERSION:
            if kw.get("temperature", 0) == 0:
                return _result({"stories": [_story("A"), _story("B"), None]})  # 2 usable
            return _result({"stories": [_story("A"), "junk", None]})  # retry: 1 usable
        return _result({"stories": []})

    monkeypatch.setattr(gen, "llm_call", _call)
    stories = generate_from_input("ent-A", prd_input="PRD", strategy="fanout",
                                  batch_size=3, max_parallel=1)
    assert sorted(s.title for s in stories) == ["A", "B"], "kept the fuller first pass"


# ── malformed model-output tolerance (regression net for the live failure) ────
# forced tool-use validates the schema only loosely, so the model can hand back
# stray strings/None/wrong types inside `stories`. This is the class of bug that
# failed a live ticket generation ('str' object has no attribute 'get'); every
# shape below must parse to the valid titles with NO exception.

@pytest.mark.parametrize("output,expected", [
    (None, []),
    ({}, []),
    ({"stories": None}, []),
    ({"stories": "a bare string, not a list"}, []),
    ({"stories": 42}, []),
    ({"stories": {}}, []),
    ({"not_stories": [{"title": "X"}]}, []),
    ("output is a string, not a dict", []),
    ([{"title": "X"}], []),                       # output is a list, not a dict
    ({"stories": []}, []),
    ({"stories": ["bare title", None, 123, {"no_title": 1}, {"title": ""}]}, []),
    ({"stories": [{"title": "Good"}]}, ["Good"]),
    ({"stories": [{"title": "Good"}, "junk", None, {"title": "Also"}]}, ["Good", "Also"]),
    ({"stories": [{"title": "  Trimmed  "}]}, ["Trimmed"]),
])
def test_stories_from_output_tolerates_malformed_shapes(output, expected):
    stories = _stories_from_output(output)
    assert [s.title for s in stories] == expected


def test_generate_user_stories_honors_strategy(isolated_settings, monkeypatch):
    """The public entry threads strategy through to the dispatch core."""
    seen: list[str] = []

    def _call(**kw):
        seen.append(kw["prompt_version"])
        if kw["prompt_version"] == PLAN_PROMPT_VERSION:
            return _result({"stubs": [{"title": "X"}]})
        return _result({"stories": [_story("X")]})

    monkeypatch.setattr(gen, "llm_call", _call)
    stories = generate_user_stories("ent-A", insight="need X", strategy="fanout")

    assert [s.title for s in stories] == ["X"]
    assert PLAN_PROMPT_VERSION in seen, "fanout strategy reached the plan leg"


def test_fanout_prd_rides_cacheable_prefix(isolated_settings, monkeypatch):
    """Plan and every enrich call send the PRD via `user_cacheable_prefix` (one
    prompt-cached copy, identical across enrich batches) — never inline in
    `input`, which carries only the roster + batch stubs."""
    titles = [f"T{i}" for i in range(6)]
    calls: list[dict] = []
    monkeypatch.setattr(gen, "llm_call", _fake_llm(titles, record=calls))

    generate_from_input(
        "ent-A", prd_input="PRD body", strategy="fanout",
        batch_size=2, max_parallel=3,
    )

    plan_calls = [c for c in calls if c["prompt_version"] == PLAN_PROMPT_VERSION]
    enrich_calls = [c for c in calls if c["prompt_version"] == ENRICH_PROMPT_VERSION]
    assert len(plan_calls) == 1 and len(enrich_calls) == 3
    for c in plan_calls + enrich_calls:
        assert c["user_cacheable_prefix"] == "PRD body"
        assert "PRD body" not in c["input"]
    # Cache sharing requires the enrich prefixes to be byte-identical.
    assert len({c["user_cacheable_prefix"] for c in enrich_calls}) == 1

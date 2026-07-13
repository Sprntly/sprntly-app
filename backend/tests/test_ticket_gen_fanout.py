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

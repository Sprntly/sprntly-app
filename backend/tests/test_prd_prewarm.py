"""Tests for PRD pre-warming (app.prd_runner.warm_prds_for_brief).

After a brief is generated, the top insights' PRDs are pre-generated in the
LLM gate's background lane so a user's first "Generate PRD" click renders
instantly. These tests prove:

  * the top-N selection is hero-first (is_headline), then confidence desc,
  * warming dedupes against existing PRD rows,
  * every warm generate_prd call runs with background=True,
  * prd_warm_count=0 disables warming entirely,
  * a failure warming one insight doesn't stop the next.

DB + generation are monkeypatched; nothing hits Supabase or Anthropic.
"""
from __future__ import annotations

import asyncio

import pytest

from app import prd_runner
from app.prd_runner import _top_insight_indices, warm_prds_for_brief


def _insight(title: str, confidence: float, headline: bool = False) -> dict:
    ins = {"title": title, "confidence": confidence}
    if headline:
        ins["is_headline"] = True
    return ins


def _brief(insights: list[dict], brief_id: int = 7) -> dict:
    return {"id": brief_id, "insights": insights}


def test_top_insight_indices_hero_first_then_confidence():
    insights = [
        _insight("low", 0.3),
        _insight("hero", 0.1, headline=True),  # lowest confidence but flagged
        _insight("high", 0.9),
        _insight("mid", 0.5),
    ]
    assert _top_insight_indices(insights, 2) == [1, 2]
    assert _top_insight_indices(insights, 3) == [1, 2, 3]
    # No headline flag → pure confidence order.
    no_hero = [_insight("a", 0.2), _insight("b", 0.8), _insight("c", 0.5)]
    assert _top_insight_indices(no_hero, 2) == [1, 2]


@pytest.fixture()
def warm_spy(monkeypatch):
    """Stub the DB + generation layer under warm_prds_for_brief and record
    every call. `existing` controls which (brief_id, insight_index) pairs the
    dedupe reports as already present."""
    calls = {"started": [], "generated": [], "existing": set()}

    monkeypatch.setattr(
        prd_runner, "find_existing_prd",
        lambda brief_id, idx, variant: (
            {"id": 99} if (brief_id, idx) in calls["existing"] else None
        ),
    )

    def _start_prd(*, brief_id, insight_index, title, template_version, variant):
        calls["started"].append((brief_id, insight_index, title, variant))
        return 1000 + insight_index

    monkeypatch.setattr(prd_runner, "start_prd", _start_prd)

    async def _generate(prd_id, brief_id, insight_index, background=False):
        calls["generated"].append((prd_id, insight_index, background))

    monkeypatch.setattr(prd_runner, "generate_prd", _generate)
    return calls


def test_warm_generates_top_n_in_background(warm_spy, monkeypatch):
    monkeypatch.setattr(prd_runner.settings, "prd_warm_count", 2)
    insights = [_insight("a", 0.4), _insight("hero", 0.2, headline=True), _insight("c", 0.9)]

    asyncio.run(warm_prds_for_brief(_brief(insights)))

    # Hero first, then highest confidence; both ran background=True.
    assert [g[1] for g in warm_spy["generated"]] == [1, 2]
    assert all(g[2] is True for g in warm_spy["generated"])
    # start_prd used the shared variant so route dedupe matches warm rows.
    assert all(s[3] == prd_runner.PRD_VARIANT for s in warm_spy["started"])


def test_default_warm_count_covers_all_three_brief_insights(warm_spy):
    """With the default prd_warm_count (3 = the brief's MAX_INSIGHTS), every one
    of the brief's 3 points gets its PRD auto-generated — no monkeypatch, so
    this also pins the default-on behavior."""
    assert prd_runner.settings.prd_warm_count >= 3
    insights = [_insight("a", 0.4), _insight("hero", 0.2, headline=True), _insight("c", 0.9)]

    asyncio.run(warm_prds_for_brief(_brief(insights)))

    # All 3 insight indices warmed (order: hero first, then by confidence).
    assert sorted(g[1] for g in warm_spy["generated"]) == [0, 1, 2]
    assert all(g[2] is True for g in warm_spy["generated"])


def test_warm_skips_existing_prds(warm_spy, monkeypatch):
    monkeypatch.setattr(prd_runner.settings, "prd_warm_count", 2)
    insights = [_insight("hero", 0.9, headline=True), _insight("b", 0.5)]
    warm_spy["existing"].add((7, 0))  # hero already has a PRD

    asyncio.run(warm_prds_for_brief(_brief(insights)))

    assert [g[1] for g in warm_spy["generated"]] == [1]
    assert len(warm_spy["started"]) == 1


def test_warm_count_zero_disables(warm_spy, monkeypatch):
    monkeypatch.setattr(prd_runner.settings, "prd_warm_count", 0)

    asyncio.run(warm_prds_for_brief(_brief([_insight("a", 0.9)])))

    assert warm_spy["generated"] == []
    assert warm_spy["started"] == []


def test_warm_failure_is_isolated_per_insight(warm_spy, monkeypatch):
    """One insight's warm failure must not stop the next insight's warm."""
    monkeypatch.setattr(prd_runner.settings, "prd_warm_count", 2)

    async def _generate(prd_id, brief_id, insight_index, background=False):
        if insight_index == 0:
            raise RuntimeError("boom")
        warm_spy["generated"].append((prd_id, insight_index, background))

    monkeypatch.setattr(prd_runner, "generate_prd", _generate)
    insights = [_insight("hero", 0.9, headline=True), _insight("b", 0.5)]

    asyncio.run(warm_prds_for_brief(_brief(insights)))

    assert [g[1] for g in warm_spy["generated"]] == [1]


def test_warm_handles_missing_brief_fields(warm_spy, monkeypatch):
    monkeypatch.setattr(prd_runner.settings, "prd_warm_count", 2)
    asyncio.run(warm_prds_for_brief({}))
    asyncio.run(warm_prds_for_brief({"id": None, "insights": []}))
    assert warm_spy["generated"] == []

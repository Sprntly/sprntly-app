"""Per-user insight FILTER pool: the brief composes the top POOL_SIZE findings,
persists the top 3 as the canonical brief plus the full ranked set as `_pool`
(each finding classified into user-facing insight_types), and the frontend
filters `_pool` by the reader's chosen types.

These cover the backend half: schema, prompt, and what run_synthesis persists.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.graph.gateway import LLMResult


def _llm_result(output, model="claude-sonnet-4-6"):
    return LLMResult(
        output=output, model=model, prompt_version="test",
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


def _seed_multi_source_theme(facade, ent, label):
    """A multi-source theme so the evidence gate passes and the judge runs."""
    from app.graph.types import Entity, Relationship, Signal
    theme = Entity(enterprise_id=ent, type="theme", canonical_label=label)
    facade.create_entity(ent, theme)
    now = datetime.now(timezone.utc)
    for st, kind, props, age in [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 1400000}, 1),
        ("customer_voice", "feature_request", {}, 2),
    ]:
        sig = Signal(enterprise_id=ent, source_type=st, kind=kind,
                     content=f"{label} {kind}", properties=props,
                     valid_at=now - timedelta(days=age))
        facade.write_signal(ent, sig)
        facade.write_relationship(ent, Relationship(
            enterprise_id=ent, type="REQUESTS", source_kind="signal",
            source_id=sig.id, target_kind="entity", target_id=theme.id))
    return theme


def _insight(i, *, insight_types, is_headline=False):
    return {
        "theme_id": f"t{i}",
        "tag": "something_broken",
        "insight_types": insight_types,
        "title": f"Finding {i}",
        "subtitle": f"Subtitle {i}.",
        "recommendation": f"Do thing {i}.",
        "metrics": [{"label": "ARR at risk", "value": "$1.4M"}],
        "chart_hints": [],
        "convergence": [{"source": "revenue", "signal": "s", "strength": "Strong"}],
        "confidence": 0.9 - i * 0.05,
        "is_headline": is_headline,
        "prototypeable": True,
        "reasoning": f"Reason {i}.",
    }


# ---------- schema ----------

def test_schema_declares_insight_types_enum_required():
    from app.synthesis import agent as synth
    from app.insight_types import INSIGHT_TYPE_SLUGS

    item = synth._BRIEF_SCHEMA["properties"]["insights"]["items"]
    props = item["properties"]
    assert "insight_types" in props
    assert props["insight_types"]["type"] == "array"
    assert props["insight_types"]["items"]["enum"] == list(INSIGHT_TYPE_SLUGS)
    # Required so every composed finding carries a routing category.
    assert "insight_types" in item["required"]


def test_prompt_lists_every_insight_type_and_the_pool_rule():
    from app.synthesis import agent as synth
    from app.insight_types import INSIGHT_TYPE_SLUGS, INSIGHT_TYPES

    sys = synth._SYSTEM
    for slug in INSIGHT_TYPE_SLUGS:
        assert slug in sys, f"prompt missing insight type slug {slug}"
    # A couple of human labels are present too (the block carries them).
    assert INSIGHT_TYPES["competitor_moves"][0] in sys
    # The pool number is injected (no stray unreplaced placeholder).
    assert "{pool_size}" not in sys
    assert str(synth.POOL_SIZE) in sys


# ---------- run_synthesis persistence ----------

def _run_with_insights(facade, insights):
    from app.synthesis import agent as synth
    _seed_multi_source_theme(facade, "ent-A", "SSO")
    ranked = {"summary_headline": "H", "insights": insights}
    with patch.object(synth, "llm_call", return_value=_llm_result(ranked)):
        return synth.run_synthesis(facade, "ent-A", dataset_slug="acme")


def test_top3_is_the_brief_full_set_is_the_pool(facade, isolated_settings):
    """Six composed findings ⇒ the brief carries the top 3; `_pool` carries all
    six, so a per-user filter has ranks 4–6 to draw on."""
    insights = [
        _insight(0, insight_types=["top_problems"], is_headline=True),
        _insight(1, insight_types=["reliability_signals"]),
        _insight(2, insight_types=["user_feedback"]),
        _insight(3, insight_types=["competitor_moves"]),
        _insight(4, insight_types=["build_priorities"]),
        _insight(5, insight_types=["wins"]),
    ]
    brief = _run_with_insights(facade, insights)

    assert [i["title"] for i in brief["insights"]] == ["Finding 0", "Finding 1", "Finding 2"]
    assert [i["title"] for i in brief["_pool"]] == [f"Finding {i}" for i in range(6)]
    # The reasoning field is stripped from both, like the legacy insights.
    assert all("reasoning" not in i for i in brief["_pool"])

    # Persisted, not just returned — the UI reads the saved payload via /current.
    rows = isolated_settings["supabase"].table("briefs").select("*") \
        .eq("dataset", "acme").execute().data
    payload = rows[0]["payload"]
    assert len(payload["insights"]) == 3
    assert len(payload["_pool"]) == 6
    assert payload["_pool"][3]["insight_types"] == ["competitor_moves"]


def test_pool_is_capped_at_pool_size(facade):
    from app.synthesis import agent as synth
    insights = [_insight(i, insight_types=["top_problems"]) for i in range(synth.POOL_SIZE + 3)]
    brief = _run_with_insights(facade, insights)
    assert len(brief["_pool"]) == synth.POOL_SIZE
    assert len(brief["insights"]) == synth.MAX_INSIGHTS


def test_unknown_insight_types_are_dropped(facade):
    """A category outside the canonical set never reaches the persisted pool —
    it would match no filter chip and only confuse the UI."""
    brief = _run_with_insights(facade, [
        _insight(0, insight_types=["competitor_moves", "bogus_type"], is_headline=True),
        _insight(1, insight_types=["not_a_real_type"]),
    ])
    assert brief["_pool"][0]["insight_types"] == ["competitor_moves"]
    assert brief["_pool"][1]["insight_types"] == []  # all-unknown ⇒ empty


def test_missing_insight_types_defaults_to_empty(facade):
    """A finding with no insight_types (legacy/hand-edited payload) degrades to
    an empty list rather than raising."""
    from app.synthesis import agent as synth
    _seed_multi_source_theme(facade, "ent-A", "SSO")
    bare = _insight(0, insight_types=["top_problems"], is_headline=True)
    del bare["insight_types"]
    ranked = {"summary_headline": "H", "insights": [bare]}
    with patch.object(synth, "llm_call", return_value=_llm_result(ranked)):
        brief = synth.run_synthesis(facade, "ent-A", dataset_slug="acme")
    assert brief["_pool"][0]["insight_types"] == []

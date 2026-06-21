"""Tests for the Synthesis Agent vertical slice: extractor → convergence → brief."""
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


# ---------- convergence (pure) ----------

def _seed_theme_with_signals(facade, ent, label, specs):
    """specs: list of (source_type, kind, props, age_days)."""
    from app.graph.types import Entity, Relationship, Signal
    theme = Entity(enterprise_id=ent, type="theme", canonical_label=label)
    facade.create_entity(ent, theme)
    now = datetime.now(timezone.utc)
    for st, kind, props, age in specs:
        sig = Signal(enterprise_id=ent, source_type=st, kind=kind,
                     content=f"{label} {kind} {age}", properties=props,
                     valid_at=now - timedelta(days=age))
        facade.write_signal(ent, sig)
        facade.write_relationship(ent, Relationship(
            enterprise_id=ent, type="REQUESTS", source_kind="signal",
            source_id=sig.id, target_kind="entity", target_id=theme.id))
    return theme


def test_convergence_ranks_multi_source_above_single(facade):
    from app.synthesis.convergence import compute_convergence

    _seed_theme_with_signals(facade, "ent-A", "multi", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 500000}, 1),
        ("customer_voice", "feature_request", {}, 2),
        ("project_mgmt", "bug", {}, 3),
    ])
    _seed_theme_with_signals(facade, "ent-A", "single", [
        ("communication", "feature_request", {}, 0),
        ("communication", "feature_request", {}, 1),
    ])
    out = compute_convergence(facade, "ent-A")
    assert [t.theme_label for t in out] == ["multi", "single"]
    assert out[0].breadth == 3
    assert out[0].revenue_at_stake_usd == 500000
    assert out[1].breadth == 1


def test_convergence_recency_decay_halves_at_window(facade):
    from app.synthesis.convergence import compute_convergence

    # communication window = 7d half-life: a 7-day-old signal weighs ~0.5
    _seed_theme_with_signals(facade, "ent-A", "aged", [
        ("communication", "feature_request", {}, 7),
    ])
    out = compute_convergence(facade, "ent-A")
    assert out and abs(out[0].effective_weight - 0.5) < 0.02


def test_convergence_skips_superseded(facade):
    from app.synthesis.convergence import compute_convergence

    theme = _seed_theme_with_signals(facade, "ent-A", "t", [
        ("revenue", "deal_blocker", {}, 0),
        ("revenue", "deal_reopened", {}, 0),
    ])
    sigs = facade.active_signals("ent-A")
    blocker = next(s for s in sigs if s.kind == "deal_blocker")
    reopened = next(s for s in sigs if s.kind == "deal_reopened")
    facade.supersede_signal("ent-A", blocker.id, reopened.id)
    out = compute_convergence(facade, "ent-A")
    assert out[0].signal_count == 1   # superseded excluded


# ---------- convergence dedup of multi-edge signals (BUG: double-count) ----------

def _seed_signal_with_edges(facade, ent, theme, source_type, kind, props, age_days,
                            edge_types):
    """Write ONE signal wired to `theme` via several relationship rows. Returns
    the signal. Models the real-world case where a signal AFFECTS and PRESSURES
    the same theme (two edges, one source signal)."""
    from app.graph.types import Relationship, Signal
    now = datetime.now(timezone.utc)
    sig = Signal(enterprise_id=ent, source_type=source_type, kind=kind,
                 content=f"{kind} via {'+'.join(edge_types)}", properties=props,
                 valid_at=now - timedelta(days=age_days))
    facade.write_signal(ent, sig)
    for et in edge_types:
        facade.write_relationship(ent, Relationship(
            enterprise_id=ent, type=et, source_kind="signal",
            source_id=sig.id, target_kind="entity", target_id=theme.id))
    return sig


def _single_theme(facade, ent, label):
    from app.graph.types import Entity
    theme = Entity(enterprise_id=ent, type="theme", canonical_label=label)
    facade.create_entity(ent, theme)
    return theme


def test_convergence_dedups_double_edged_signal_revenue_and_count(facade):
    """A signal wired to a theme via TWO edges (AFFECTS + PRESSURES) must count
    ONCE in revenue / signal_count / effective_weight — not twice."""
    from app.synthesis.convergence import compute_convergence

    theme = _single_theme(facade, "ent-A", "SSO")
    _seed_signal_with_edges(facade, "ent-A", theme, "revenue", "deal_blocker",
                            {"revenue_at_risk_usd": 500000}, 1,
                            ["AFFECTS", "PRESSURES"])
    out = compute_convergence(facade, "ent-A")
    assert len(out) == 1
    tc = out[0]
    assert tc.signal_count == 1                 # not 2
    assert tc.revenue_at_stake_usd == 500000    # not 1_000_000


def test_convergence_double_edged_matches_single_edge_totals(facade):
    """The doubled-edge scenario yields the SAME totals as the single-edge one:
    revenue, signal_count, and effective_weight are identical."""
    from app.synthesis.convergence import compute_convergence

    # Two themes, same signal data: one wired by 1 edge, one wired by 3 edges.
    single = _single_theme(facade, "ent-A", "single_edge")
    _seed_signal_with_edges(facade, "ent-A", single, "revenue", "deal_blocker",
                            {"revenue_at_risk_usd": 500000}, 1, ["AFFECTS"])
    triple = _single_theme(facade, "ent-A", "triple_edge")
    _seed_signal_with_edges(facade, "ent-A", triple, "revenue", "deal_blocker",
                            {"revenue_at_risk_usd": 500000}, 1,
                            ["AFFECTS", "PRESSURES", "REQUESTS"])

    by_label = {t.theme_label: t for t in compute_convergence(facade, "ent-A")}
    a, b = by_label["single_edge"], by_label["triple_edge"]
    assert a.signal_count == b.signal_count == 1
    assert a.revenue_at_stake_usd == b.revenue_at_stake_usd == 500000
    assert a.effective_weight == pytest.approx(b.effective_weight)
    assert a.base_score == pytest.approx(b.base_score)


def test_convergence_dedup_weight_over_distinct_signals(facade):
    """effective_weight (and the base_score severity term) is computed over
    DISTINCT signals: a single double-edged signal weighs the same as if it had
    one edge."""
    from app.synthesis.convergence import compute_convergence

    theme = _single_theme(facade, "ent-A", "t")
    sig = _seed_signal_with_edges(facade, "ent-A", theme, "revenue", "deal_blocker",
                                  {}, 0, ["AFFECTS", "PRESSURES"])
    out = compute_convergence(facade, "ent-A")
    tc = out[0]
    # fresh revenue signal: confidence*weight*recency, single instance
    assert tc.effective_weight == pytest.approx(sig.confidence * sig.weight)
    assert tc.signal_count == 1


def test_convergence_dedup_breadth_unchanged(facade):
    """Dedup must not collapse distinct signals: two different signals on a theme
    still give breadth/count of 2 even if one of them is double-edged."""
    from app.synthesis.convergence import compute_convergence

    theme = _single_theme(facade, "ent-A", "t")
    _seed_signal_with_edges(facade, "ent-A", theme, "revenue", "deal_blocker",
                            {"revenue_at_risk_usd": 100000}, 1, ["AFFECTS", "PRESSURES"])
    _seed_signal_with_edges(facade, "ent-A", theme, "customer_voice",
                            "feature_request", {}, 2, ["REQUESTS"])
    out = compute_convergence(facade, "ent-A")
    tc = out[0]
    assert tc.signal_count == 2
    assert tc.breadth == 2
    assert tc.revenue_at_stake_usd == 100000


def test_convergence_dedup_evidence_listed_once(facade):
    """The evidence list for a theme carries each distinct signal once, even when
    that signal reaches the theme via multiple edges."""
    from app.synthesis.convergence import compute_convergence

    theme = _single_theme(facade, "ent-A", "t")
    sig = _seed_signal_with_edges(facade, "ent-A", theme, "revenue", "deal_blocker",
                                  {}, 0, ["AFFECTS", "PRESSURES", "REQUESTS"])
    out = compute_convergence(facade, "ent-A")
    ev_ids = [e["signal_id"] for e in out[0].evidence]
    assert ev_ids.count(sig.id) == 1
    assert len(ev_ids) == 1


def test_convergence_dedup_competitor_pressure_counted_once(facade):
    """A competitor-pressure signal reaching a theme by two edges bumps
    competitor_pressure once, not twice."""
    from app.synthesis.convergence import compute_convergence

    theme = _single_theme(facade, "ent-A", "t")
    _seed_signal_with_edges(facade, "ent-A", theme, "customer_voice",
                            "competitor_move", {}, 0, ["PRESSURES", "AFFECTS"])
    out = compute_convergence(facade, "ent-A")
    assert out[0].competitor_pressure == 1


# ---------- extractor ----------

_EXTRACTED = {
    "signals": [
        {"kind": "deal_blocker", "content": "Acme deal $1.4M blocked on SSO",
         "source_type": "revenue", "theme": "SSO",
         "relationship": "BLOCKED_BY", "confidence": 0.9,
         "properties": {"revenue_at_risk_usd": 1400000}},
        {"kind": "feature_request", "content": "Customers ask for SSO in calls",
         "source_type": "customer_voice", "theme": "SSO",
         "relationship": "REQUESTS", "confidence": 0.85},
    ]
}


def test_extractor_writes_signals_themes_edges(facade):
    from app.graph import extractor

    with patch.object(extractor, "llm_call", return_value=_llm_result(_EXTRACTED)), \
         patch.object(extractor, "embed_texts",
                      side_effect=lambda texts, **k: [[0.1] * 4 for _ in texts]):
        r = extractor.extract_document(facade, "ent-A", doc_name="doc1", text="...")
    assert r == {"signals": 2, "themes": 1, "skipped": 0}
    themes = facade.query_entities("ent-A", type="theme")
    assert len(themes) == 1 and themes[0].canonical_label == "SSO"
    edges = facade.edges_to("ent-A", themes[0].id)
    assert {e.type for e in edges} == {"BLOCKED_BY", "REQUESTS"}


def test_extractor_rerun_is_idempotent(facade):
    from app.graph import extractor

    with patch.object(extractor, "llm_call", return_value=_llm_result(_EXTRACTED)), \
         patch.object(extractor, "embed_texts",
                      side_effect=lambda texts, **k: [[0.1] * 4 for _ in texts]):
        extractor.extract_document(facade, "ent-A", doc_name="doc1", text="...")
        r2 = extractor.extract_document(facade, "ent-A", doc_name="doc1", text="...")
    assert r2["signals"] == 0 and r2["skipped"] == 2   # same ids → skipped
    assert len(facade.active_signals("ent-A")) == 2    # not duplicated


# ---------- synthesis (KG → brief) ----------

_RANKED = {
    "summary_headline": "SSO is blocking revenue across three sources",
    "insights": [{
        "theme_id": "FILLED_IN_TEST",
        "tag": "something_broken",
        "title": "SSO gap blocks $1.4M in deals",
        "subtitle": "Three sources converge.",
        "recommendation": "Ship SSO this quarter.",
        "metrics": [{"label": "ARR at risk", "value": "$1.4M"}],
        "impact_math": ["Revenue at risk: $1.4M/yr"],
        "chart_hints": [
            {"kind": "bar", "title": "SSO blocks $1.4M across deals",
             "subtitle": "revenue signals",
             "data": [{"label": "Acme", "value": 800000},
                      {"label": "Globex", "value": 600000}]},
            {"kind": "stat", "title": "3 sources converge on SSO",
             "data": [{"label": "sources", "value": 3}]},
        ],
        "convergence": [
            {"source": "revenue", "signal": "Acme deal blocked", "strength": "Strong"},
            {"source": "customer_voice", "signal": "asked in calls", "strength": "Moderate"},
        ],
        "confidence": 0.85,
        "is_headline": True,
        "reasoning": "Highest revenue x breadth.",
    }],
}


def test_run_synthesis_saves_brief_and_ledger(facade, isolated_settings):
    from app.synthesis import agent as synth

    theme = _seed_theme_with_signals(facade, "ent-A", "SSO", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 1400000}, 1),
        ("customer_voice", "feature_request", {}, 2),
    ])
    ranked = {**_RANKED, "insights": [
        {**_RANKED["insights"][0], "theme_id": theme.id}]}

    with patch.object(synth, "llm_call", return_value=_llm_result(ranked)):
        brief = synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    # brief persisted in legacy schema → existing UI renders it
    rows = isolated_settings["supabase"].table("briefs").select("*") \
        .eq("dataset", "acme").execute().data
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["_generated_by"] == "synthesis_agent"
    assert payload["insights"][0]["tag"] == "something_broken"
    assert "reasoning" not in payload["insights"][0]   # audit-only field stripped
    assert brief["week_label"].startswith("Week of")

    # ledger: hypothesis entity + ADDRESSES/SUPPORTS edges
    hyps = facade.query_entities("ent-A", type="hypothesis")
    assert len(hyps) == 1
    assert hyps[0].properties["tag"] == "something_broken"
    edges_out = facade.edges_from("ent-A", hyps[0].id, type="ADDRESSES")
    assert edges_out and edges_out[0].target_id == theme.id
    supports = facade.edges_to("ent-A", hyps[0].id, type="SUPPORTS")
    assert len(supports) == 2

    # semantic decision log with reasoning
    logs = isolated_settings["supabase"].table("agent_decision_log").select("*") \
        .eq("enterprise_id", "ent-A").execute().data
    rank_rows = [r for r in logs if r["decision_type"] == "rank"]
    assert len(rank_rows) == 1
    assert "Highest revenue" in rank_rows[0]["reasoning"]
    assert rank_rows[0]["output"]["insight_titles"] == ["SSO gap blocks $1.4M in deals"]


def test_run_synthesis_empty_kg_raises(facade):
    from app.synthesis import agent as synth

    with pytest.raises(ValueError, match="no themes"):
        synth.run_synthesis(facade, "ent-empty", dataset_slug="empty")


# ---------- evidence gate: has_sufficient_evidence (pure) ----------

def test_sufficient_when_multi_source_connected_theme(facade):
    """A single theme with >=2 distinct CONNECTED source types clears the bar."""
    from app.synthesis.convergence import (
        compute_convergence, has_sufficient_evidence)

    _seed_theme_with_signals(facade, "ent-A", "multi", [
        ("revenue", "deal_blocker", {}, 1),
        ("customer_voice", "feature_request", {}, 2),
    ])
    conv = compute_convergence(facade, "ent-A")
    assert has_sufficient_evidence(conv, min_connected_signals=3) is True


def test_sufficient_when_connected_signal_count_meets_threshold(facade):
    """No multi-source theme, but >= MIN_CONNECTED_SIGNALS connected signals
    across themes ⇒ sufficient (each theme single-source here)."""
    from app.synthesis.convergence import (
        compute_convergence, has_sufficient_evidence)

    _seed_theme_with_signals(facade, "ent-A", "a", [
        ("communication", "feature_request", {}, 0),
        ("communication", "feature_request", {}, 1),
    ])
    _seed_theme_with_signals(facade, "ent-A", "b", [
        ("project_mgmt", "bug", {}, 0),
    ])
    conv = compute_convergence(facade, "ent-A")
    # 3 connected signals total, but no theme has connected_breadth>=2.
    assert all(tc.connected_breadth < 2 for tc in conv)
    assert has_sufficient_evidence(conv, min_connected_signals=3) is True
    # Raising the bar above the count flips it insufficient.
    assert has_sufficient_evidence(conv, min_connected_signals=4) is False


def test_insufficient_when_only_onboarding_metadata(facade):
    """The screenshot case: only pm_manual/agent_inferred onboarding signals and
    a single thin theme ⇒ INSUFFICIENT (those source types are not connected
    sources, so they don't count toward the bar)."""
    from app.synthesis.convergence import (
        compute_convergence, has_sufficient_evidence)

    _seed_theme_with_signals(facade, "ent-A", "north-star", [
        ("pm_manual", "constraint", {}, 0),
        ("agent_inferred", "good_outcome", {}, 0),
    ])
    _seed_theme_with_signals(facade, "ent-A", "stage", [
        ("verbal_claim", "claim", {}, 0),
    ])
    conv = compute_convergence(facade, "ent-A")
    assert sum(tc.connected_signal_count for tc in conv) == 0
    assert has_sufficient_evidence(conv, min_connected_signals=3) is False


def test_insufficient_single_connected_source_thin(facade):
    """One single-source theme with one connected signal ⇒ insufficient
    (breadth 1, count 1 < 3)."""
    from app.synthesis.convergence import (
        compute_convergence, has_sufficient_evidence)

    _seed_theme_with_signals(facade, "ent-A", "thin", [
        ("communication", "feature_request", {}, 0),
    ])
    conv = compute_convergence(facade, "ent-A")
    assert has_sufficient_evidence(conv, min_connected_signals=3) is False


def test_require_multi_source_false_ignores_breadth_path(facade):
    """With require_multi_source=False a multi-source theme no longer auto-passes;
    only the connected-signal count matters."""
    from app.synthesis.convergence import (
        compute_convergence, has_sufficient_evidence)

    _seed_theme_with_signals(facade, "ent-A", "multi", [
        ("revenue", "deal_blocker", {}, 1),
        ("customer_voice", "feature_request", {}, 2),
    ])
    conv = compute_convergence(facade, "ent-A")
    # 2 connected signals; breadth path would pass, count path needs >=3.
    assert has_sufficient_evidence(
        conv, min_connected_signals=3, require_multi_source=False) is False
    assert has_sufficient_evidence(
        conv, min_connected_signals=2, require_multi_source=False) is True


# ---------- evidence gate: run_synthesis wiring ----------

def test_run_synthesis_thin_kg_saves_empty_brief(facade, isolated_settings):
    """A thin KG (one single-source theme, one signal) → EMPTY brief saved, the
    LLM judge is NOT invoked, and the _insufficient_evidence flag is set."""
    from app.synthesis import agent as synth

    _seed_theme_with_signals(facade, "ent-A", "thin", [
        ("communication", "feature_request", {}, 0),
    ])

    judge_calls: list = []

    def _spy_llm(**kw):
        judge_calls.append(kw)
        return _llm_result(_RANKED)

    with patch.object(synth, "llm_call", side_effect=_spy_llm):
        brief = synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    # Judge never ran — the gate short-circuited before any LLM call.
    assert judge_calls == []
    assert brief["insights"] == []
    assert brief["_insufficient_evidence"] is True
    assert brief["_generated_by"] == "synthesis_agent"
    assert "_empty_reason" in brief

    # Empty brief persisted in the same `briefs` table the UI reads.
    rows = isolated_settings["supabase"].table("briefs").select("*") \
        .eq("dataset", "acme").execute().data
    assert len(rows) == 1
    assert rows[0]["payload"]["insights"] == []
    assert rows[0]["payload"]["_insufficient_evidence"] is True

    # No hypothesis ledger entities created for an empty brief.
    assert facade.query_entities("ent-A", type="hypothesis") == []


def test_run_synthesis_rich_kg_generates_normal_brief(facade, isolated_settings):
    """A rich (multi-source) KG → normal brief as before; judge invoked."""
    from app.synthesis import agent as synth

    theme = _seed_theme_with_signals(facade, "ent-A", "SSO", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 1400000}, 1),
        ("customer_voice", "feature_request", {}, 2),
    ])
    ranked = {**_RANKED, "insights": [
        {**_RANKED["insights"][0], "theme_id": theme.id}]}

    judge_calls: list = []

    def _spy_llm(**kw):
        judge_calls.append(kw)
        return _llm_result(ranked)

    with patch.object(synth, "llm_call", side_effect=_spy_llm):
        brief = synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    assert judge_calls, "judge should run for a sufficiently-rich KG"
    assert len(brief["insights"]) == 1
    assert "_insufficient_evidence" not in brief
    assert facade.query_entities("ent-A", type="hypothesis")


# ---------- chart_hints parity (BUG 1) ----------

def _run_with_ranked(facade, ranked):
    """Seed an SSO theme, run synthesis with `ranked` as the judge output,
    and return the persisted brief payload's first insight."""
    from app.synthesis import agent as synth

    theme = _seed_theme_with_signals(facade, "ent-A", "SSO", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 1400000}, 1),
        ("customer_voice", "feature_request", {}, 2),
    ])
    ranked = {**ranked, "insights": [
        {**ranked["insights"][0], "theme_id": theme.id}]}
    with patch.object(synth, "llm_call", return_value=_llm_result(ranked)):
        return synth.run_synthesis(facade, "ent-A", dataset_slug="acme")


def test_brief_schema_declares_chart_hints():
    """The judge schema must request chart_hints, matching the legacy shape
    (kind|title|subtitle + data:[{label,value}]) the frontend renders."""
    from app.synthesis import agent as synth

    insight_schema = synth._BRIEF_SCHEMA["properties"]["insights"]["items"]
    props = insight_schema["properties"]
    assert "chart_hints" in props
    # chart_hints is intentionally OPTIONAL (not required) so an insight with no
    # cleanly-chartable data emits [] instead of being forced to fabricate a
    # chart — the old forcing function behind unrealistic/mixed-unit charts.
    assert "chart_hints" not in insight_schema["required"]
    item = props["chart_hints"]["items"]
    # brief-adapter.ts reads h.kind, h.title, and h.data[].{label,value}
    assert {"kind", "title", "data"} <= set(item["properties"])
    assert item["required"] == ["kind", "title", "data"]
    data_item = item["properties"]["data"]["items"]
    assert set(data_item["properties"]) == {"label", "value"}
    assert data_item["properties"]["value"]["type"] == "number"


def test_system_prompt_forbids_mixed_unit_and_filler_charts():
    """The chart rule must steer the model away from the unrealistic charts we
    saw in prod: mixed-unit charts and trivial/filler ones."""
    from app.synthesis import agent as synth

    sys = synth._SYSTEM.lower()
    assert "one unit per chart" in sys
    assert "never mix units" in sys
    assert "not trivial" in sys
    # grounding requirement preserved
    assert "never invent" in sys


def test_sanitize_chart_hints_drops_junk_keeps_real():
    """Deterministic backstop: empty/single-point/all-equal bar-line-pie charts
    and non-numeric data are dropped; real multi-point charts and stat tiles
    survive — so only sensible graphs reach the brief."""
    from app.synthesis.agent import _sanitize_chart_hints

    insights = [{
        "chart_hints": [
            # KEEP: real 2-point comparison
            {"kind": "bar", "title": "ok", "data": [
                {"label": "Android", "value": 63.5}, {"label": "iOS", "value": 88.0}]},
            # KEEP: stat with a single standalone number
            {"kind": "stat", "title": "ok", "data": [{"label": "sources", "value": 3}]},
            # DROP: single-point bar (nothing to compare)
            {"kind": "bar", "title": "junk", "data": [{"label": "x", "value": 1}]},
            # DROP: all-equal flags
            {"kind": "bar", "title": "junk", "data": [
                {"label": "a", "value": 1}, {"label": "b", "value": 1}, {"label": "c", "value": 1}]},
            # DROP: empty data
            {"kind": "pie", "title": "junk", "data": []},
            # DROP: non-numeric value
            {"kind": "bar", "title": "junk", "data": [
                {"label": "a", "value": "lots"}, {"label": "b", "value": 2}]},
        ]
    }]
    _sanitize_chart_hints(insights)
    kept = insights[0]["chart_hints"]
    assert [h["kind"] for h in kept] == ["bar", "stat"]
    assert all(h["title"] == "ok" for h in kept)


def test_run_synthesis_persists_chart_hints(facade, isolated_settings):
    """chart_hints from the judge survive into the saved brief insight in the
    exact shape brief-adapter.ts iterates over."""
    brief = _run_with_ranked(facade, _RANKED)
    insight = brief["insights"][0]
    assert "chart_hints" in insight
    hints = insight["chart_hints"]
    assert len(hints) == 2
    # parity with web/app/lib/brief-adapter.ts: each hint is {kind,title,data}
    # where data is [{label, value:number}] — the adapter drops hints missing
    # any of these, so they MUST be present for charts to render.
    for h in hints:
        assert h["kind"] in ("bar", "line", "pie", "stat")
        assert isinstance(h["title"], str) and h["title"]
        assert isinstance(h["data"], list) and h["data"]
        for d in h["data"]:
            assert isinstance(d["label"], str)
            assert isinstance(d["value"], (int, float))


def test_run_synthesis_chart_hints_in_saved_payload(facade, isolated_settings):
    """The DB row (not just the returned dict) carries chart_hints, since the
    UI reads the saved payload via /current."""
    _run_with_ranked(facade, _RANKED)
    rows = isolated_settings["supabase"].table("briefs").select("*") \
        .eq("dataset", "acme").execute().data
    saved_insight = rows[0]["payload"]["insights"][0]
    assert saved_insight["chart_hints"][0]["kind"] == "bar"
    assert saved_insight["chart_hints"][0]["data"][0]["value"] == 800000


def test_synthesis_judge_prompt_requests_grounded_chart_hints():
    """The judge system prompt instructs chart_hints generation grounded in the
    insight's own evidence — never invented numbers."""
    from app.synthesis import agent as synth

    sys = synth._SYSTEM.lower()
    assert "chart_hints" in sys
    assert "never invent" in sys


# ---------- goal-alignment factor (classifier + caching) ----------

def _kpi_tree(version=1):
    from app.kpi_tree import KpiTree, NorthStar, PrimaryMetric
    return KpiTree(
        north_star=NorthStar(metric="Weekly Active Technicians",
                             description="Technicians active in a 7-day window."),
        primary_metrics=[PrimaryMetric(metric="Net revenue retention",
                                       description="Expansion minus churn on existing accounts.")],
        version=version,
    )


def _legacy_kpi_tree(version=1):
    """A legacy row shape (weights/current/target) parsed via model_validate —
    extra numeric keys are ignored; goal-fit must work with no KeyError."""
    from app.kpi_tree import KpiTree
    return KpiTree.model_validate({
        "north_star": {"metric": "Weekly Active Technicians",
                       "current_value": 5310, "target_value": 7500,
                       "target_window_days": 90},
        "primary_metrics": [{"metric": "Net revenue retention",
                             "current_value": 0.96, "target_value": 1.10, "weight": 1.0}],
        "secondary_signals": [{"metric": "Day-30 activation", "current_value": 0.61}],
        "version": version,
    })


def test_classify_theme_fit_works_with_legacy_tree(facade):
    """Goal-fit classification runs on a legacy tree (old numeric fields) with
    no KeyError on weight/current_value, and the prompt carries no weights."""
    from app.synthesis import scoring

    tc = _theme_conv(facade, "ent-A", "SSO", [("revenue", "deal_blocker", {}, 1)])
    captured = {}

    def fake_llm(**kw):
        captured["input"] = kw["input"]
        return _llm_result({"fit": "high", "reasoning": "moves NRR"})

    with patch.object(scoring, "llm_call", fake_llm):
        fit = scoring.classify_theme_fit(facade, "ent-A", tc, _legacy_kpi_tree())

    assert fit == "high"
    assert "Weekly Active Technicians" in captured["input"]
    assert "weight" not in captured["input"].lower()


def _theme_conv(facade, ent, label, specs):
    """Seed a theme then return its ThemeConvergence (with base_score)."""
    from app.synthesis.convergence import compute_convergence
    _seed_theme_with_signals(facade, ent, label, specs)
    convs = compute_convergence(facade, ent)
    return next(c for c in convs if c.theme_label == label)


def test_classify_theme_fit_caches_after_first_call(facade):
    from app.synthesis import scoring

    tc = _theme_conv(facade, "ent-A", "SSO", [
        ("revenue", "deal_blocker", {}, 1),
    ])
    tree = _kpi_tree(version=3)

    calls = []
    def fake_llm(**kw):
        calls.append(kw)
        return _llm_result({"fit": "high", "reasoning": "moves NRR"})

    with patch.object(scoring, "llm_call", fake_llm):
        first = scoring.classify_theme_fit(facade, "ent-A", tc, tree)
        second = scoring.classify_theme_fit(facade, "ent-A", tc, tree)

    assert first == "high" and second == "high"
    assert len(calls) == 1  # second run hit the cache, no llm_call
    assert calls[0]["purpose"] == "classify_goal_fit"
    assert calls[0]["prompt_version"] == scoring.FIT_PROMPT_VERSION
    # cache persisted on the theme entity
    ent = facade.get_entity("ent-A", tc.theme_id)
    gf = ent.properties["goal_fit"]
    assert gf["fit"] == "high" and gf["kpi_tree_version"] == 3
    assert "classified_at" in gf


def test_classify_reclassifies_on_tree_version_bump(facade):
    from app.synthesis import scoring

    tc = _theme_conv(facade, "ent-A", "SSO", [("revenue", "deal_blocker", {}, 1)])

    seq = iter(["high", "low"])
    calls = []
    def fake_llm(**kw):
        calls.append(kw)
        return _llm_result({"fit": next(seq), "reasoning": "r"})

    with patch.object(scoring, "llm_call", fake_llm):
        a = scoring.classify_theme_fit(facade, "ent-A", tc, _kpi_tree(version=1))
        b = scoring.classify_theme_fit(facade, "ent-A", tc, _kpi_tree(version=2))

    assert a == "high" and b == "low"
    assert len(calls) == 2  # version changed → reclassified


def test_classify_no_tree_skips_classification(facade):
    from app.synthesis import scoring

    tc = _theme_conv(facade, "ent-A", "SSO", [("revenue", "deal_blocker", {}, 1)])
    calls = []
    with patch.object(scoring, "llm_call", lambda **kw: calls.append(kw)):
        fit = scoring.classify_theme_fit(facade, "ent-A", tc, None)
    assert fit == "high"          # neutral → goal_factor 1.0
    assert calls == []            # no classification call
    ent = facade.get_entity("ent-A", tc.theme_id)
    assert "goal_fit" not in ent.properties


def test_goal_factor_math():
    from app.synthesis.scoring import goal_factor
    assert goal_factor("high") == 1.0
    assert goal_factor("med") == pytest.approx(0.6)
    assert goal_factor("low") == pytest.approx(0.25)
    # goal_weight blends toward 1.0
    assert goal_factor("low", goal_weight=0.0) == 1.0
    assert goal_factor("low", goal_weight=0.5) == pytest.approx(0.625)


# ---------- factor application in the rank path ----------

def _run_with_fits(facade, monkeypatch, ent, fits, *, tree=None, captured=None):
    """Run synthesis with classify_theme_fit stubbed by theme label → fit."""
    from app.synthesis import agent as synth

    monkeypatch.setattr(synth, "load_kpi_tree", lambda eid: tree)
    monkeypatch.setattr(synth, "classify_theme_fit",
                        lambda f, e, c, t, **k: fits[c.theme_label])

    def fake_llm(**kw):
        if captured is not None:
            captured["input"] = kw["input"]
        ranked = {**_RANKED, "insights": [{**_RANKED["insights"][0],
                                           "theme_id": "x"}]}
        return _llm_result(ranked)

    monkeypatch.setattr(synth, "llm_call", fake_llm)
    return synth


def test_factor_reorders_low_fit_below_high_fit(facade, isolated_settings, monkeypatch):
    # "broad" has higher breadth (higher base_score) but LOW fit; its signals are
    # aged (communication 7d half-life) so per-signal severity is ~0.5.
    # "narrow" has a single fresh signal (lower base_score) but HIGH fit.
    _seed_theme_with_signals(facade, "ent-A", "broad", [
        ("communication", "feature_request", {}, 7),
        ("customer_voice", "feature_request", {}, 30),
        ("project_mgmt", "bug", {}, 14),
        ("revenue", "deal_blocker", {}, 30),
    ])
    _seed_theme_with_signals(facade, "ent-A", "narrow", [
        ("revenue", "deal_blocker", {}, 0),
    ])
    from app.synthesis.convergence import compute_convergence
    convs = {c.theme_label: c for c in compute_convergence(facade, "ent-A")}
    assert convs["broad"].base_score > convs["narrow"].base_score  # pre-factor

    synth = _run_with_fits(facade, monkeypatch, "ent-A",
                           {"broad": "low", "narrow": "high"}, tree=_kpi_tree())
    synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    logs = isolated_settings["supabase"].table("agent_decision_log").select("*") \
        .eq("enterprise_id", "ent-A").execute().data
    rank = next(r for r in logs if r["decision_type"] == "rank")
    cands = rank["factors"]["candidates"]
    order = [c["label"] for c in cands]
    assert order == ["narrow", "broad"]  # high-fit thin theme now leads
    by_label = {c["label"]: c for c in cands}
    assert by_label["broad"]["goal_adjusted_score"] < by_label["narrow"]["goal_adjusted_score"]


def test_decision_log_factors_carry_four_score_fields(facade, isolated_settings, monkeypatch):
    # Multi-source (revenue + customer_voice) so it clears the evidence gate;
    # still a single theme, so per-candidate assertions below are unchanged.
    _seed_theme_with_signals(facade, "ent-A", "SSO", [
        ("revenue", "deal_blocker", {}, 0),
        ("customer_voice", "feature_request", {}, 0)])
    synth = _run_with_fits(facade, monkeypatch, "ent-A", {"SSO": "med"}, tree=_kpi_tree())
    synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    logs = isolated_settings["supabase"].table("agent_decision_log").select("*") \
        .eq("enterprise_id", "ent-A").execute().data
    rank = next(r for r in logs if r["decision_type"] == "rank")
    c0 = rank["factors"]["candidates"][0]
    assert set(c0) >= {"base_score", "fit", "goal_factor", "goal_adjusted_score"}
    assert c0["fit"] == "med"
    assert c0["goal_factor"] == pytest.approx(0.6)
    assert c0["goal_adjusted_score"] == pytest.approx(c0["base_score"] * 0.6)


def test_flag_off_factors_all_one_and_no_classification(facade, isolated_settings, monkeypatch):
    from app.synthesis import agent as synth

    # Multi-source (revenue + customer_voice) so it clears the evidence gate;
    # still a single theme, so per-candidate assertions below are unchanged.
    _seed_theme_with_signals(facade, "ent-A", "SSO", [
        ("revenue", "deal_blocker", {}, 0),
        ("customer_voice", "feature_request", {}, 0)])
    monkeypatch.setattr(synth, "load_kpi_tree", lambda eid: _kpi_tree())
    monkeypatch.setitem(
        __import__("app.graph.config_layers", fromlist=["PLATFORM_DEFAULTS"])
        .PLATFORM_DEFAULTS["scoring"], "goal_factor_enabled", False)

    classify_calls = []
    monkeypatch.setattr(synth, "classify_theme_fit",
                        lambda *a, **k: classify_calls.append(a) or "high")
    monkeypatch.setattr(synth, "llm_call",
                        lambda **kw: _llm_result({**_RANKED, "insights": [
                            {**_RANKED["insights"][0], "theme_id": "x"}]}))
    try:
        synth.run_synthesis(facade, "ent-A", dataset_slug="acme")
    finally:
        __import__("app.graph.config_layers", fromlist=["PLATFORM_DEFAULTS"]) \
            .PLATFORM_DEFAULTS["scoring"]["goal_factor_enabled"] = True

    assert classify_calls == []  # flag off → never classified
    logs = isolated_settings["supabase"].table("agent_decision_log").select("*") \
        .eq("enterprise_id", "ent-A").execute().data
    rank = next(r for r in logs if r["decision_type"] == "rank")
    assert rank["factors"]["goal_factor_enabled"] is False
    for c in rank["factors"]["candidates"]:
        assert c["goal_factor"] == 1.0
        assert c["goal_adjusted_score"] == c["base_score"]
        assert c["fit"] == "off"


def test_judge_prompt_has_no_rerank_instruction(facade, isolated_settings, monkeypatch):
    captured = {}
    synth = _run_with_fits(facade, monkeypatch, "ent-A", {"SSO": "high"},
                           tree=_kpi_tree(), captured=captured)
    # Multi-source (revenue + customer_voice) so it clears the evidence gate;
    # still a single theme, so per-candidate assertions below are unchanged.
    _seed_theme_with_signals(facade, "ent-A", "SSO", [
        ("revenue", "deal_blocker", {}, 0),
        ("customer_voice", "feature_request", {}, 0)])
    synth.run_synthesis(facade, "ent-A", dataset_slug="acme")
    text = captured["input"]
    assert "do NOT re-rank by strategic fit" in text
    assert "ALREADY priced into the candidate scores" in text


def test_no_tree_path_factors_neutral_no_classification(facade, isolated_settings, monkeypatch):
    from app.synthesis import agent as synth
    from app.synthesis import scoring

    # Multi-source (revenue + customer_voice) so it clears the evidence gate;
    # still a single theme, so per-candidate assertions below are unchanged.
    _seed_theme_with_signals(facade, "ent-A", "SSO", [
        ("revenue", "deal_blocker", {}, 0),
        ("customer_voice", "feature_request", {}, 0)])
    monkeypatch.setattr(synth, "load_kpi_tree", lambda eid: None)
    # Real classify_theme_fit runs (no-tree branch), but it must make no llm_call.
    llm_calls = []
    monkeypatch.setattr(scoring, "llm_call", lambda **kw: llm_calls.append(kw))
    monkeypatch.setattr(synth, "llm_call",
                        lambda **kw: _llm_result({**_RANKED, "insights": [
                            {**_RANKED["insights"][0], "theme_id": "x"}]}))
    brief = synth.run_synthesis(facade, "ent-A", dataset_slug="acme")
    assert brief["insights"]
    assert llm_calls == []  # no tree → no classification llm_call
    logs = isolated_settings["supabase"].table("agent_decision_log").select("*") \
        .eq("enterprise_id", "ent-A").execute().data
    rank = next(r for r in logs if r["decision_type"] == "rank")
    for c in rank["factors"]["candidates"]:
        assert c["goal_factor"] == 1.0
        assert c["goal_adjusted_score"] == c["base_score"]


# ---------- provenance pinning: rank decision logs the gateway's returned
# prompt_version (carrying the skill hash), not the bare module constant -------

# The gateway appends `+<skill_id>@<hash>` to prompt_version when a skill binds;
# the decision log MUST record that returned value so the §4d audit row pins the
# exact method/skill version behind the ranking call.
_SKILL_PINNED_VERSION = "synthesis-brief-v1+prioritize@deadbeef"


def _ranked_for(theme_id):
    return {**_RANKED, "insights": [{**_RANKED["insights"][0], "theme_id": theme_id}]}


def _run_synthesis_with_pinned_gateway(facade, isolated_settings,
                                       returned_version=_SKILL_PINNED_VERSION):
    """Run synthesis with the gateway mocked to return `returned_version` as the
    LLMResult.prompt_version (mirrors the real gateway's `+skill@hash` suffix).
    Returns the persisted rank decision-log row."""
    from app.synthesis import agent as synth

    theme = _seed_theme_with_signals(facade, "ent-A", "SSO", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 1400000}, 1),
        ("customer_voice", "feature_request", {}, 2),
    ])

    def fake_llm(**kw):
        # the OUTGOING call still passes the bare constant in; the gateway is
        # what appends the suffix and returns it on the result.
        assert kw["prompt_version"] == synth.PROMPT_VERSION
        return LLMResult(
            output=_ranked_for(theme.id), model="claude-sonnet-4-6",
            prompt_version=returned_version, input_tokens=10, output_tokens=5,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
            cost_usd=0.001, latency_ms=5, stop_reason="end_turn",
        )

    with patch.object(synth, "llm_call", fake_llm):
        synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    logs = isolated_settings["supabase"].table("agent_decision_log").select("*") \
        .eq("enterprise_id", "ent-A").execute().data
    return next(r for r in logs if r["decision_type"] == "rank")


def test_rank_decision_log_pins_gateway_returned_prompt_version(
        facade, isolated_settings):
    """The top-level prompt_version on the rank decision-log row is the gateway's
    RETURNED result.prompt_version (with the skill hash), not the bare constant."""
    from app.synthesis import agent as synth

    rank = _run_synthesis_with_pinned_gateway(facade, isolated_settings)
    assert rank["prompt_version"] == _SKILL_PINNED_VERSION
    assert rank["prompt_version"] != synth.PROMPT_VERSION
    assert "+prioritize@deadbeef" in rank["prompt_version"]


def test_rank_decision_log_factors_prompt_version_pins_skill_hash(
        facade, isolated_settings):
    """The factors['prompt_version'] mirror also carries the gateway's returned
    version, so the audit JSON itself records the bound skill."""
    rank = _run_synthesis_with_pinned_gateway(facade, isolated_settings)
    assert rank["factors"]["prompt_version"] == _SKILL_PINNED_VERSION
    assert "+prioritize@deadbeef" in rank["factors"]["prompt_version"]


def test_rank_decision_log_prompt_version_matches_result(facade, isolated_settings):
    """Top-level and factors prompt_version agree — both pin the same returned
    version (no split-brain between the row and its factors blob)."""
    custom = "synthesis-brief-v1+prioritize@cafef00d"
    rank = _run_synthesis_with_pinned_gateway(
        facade, isolated_settings, returned_version=custom)
    assert rank["prompt_version"] == custom
    assert rank["factors"]["prompt_version"] == custom


def test_rank_decision_log_carries_skill_hash_not_bare_constant(
        facade, isolated_settings):
    """Regression: with a skill bound, neither the row nor its factors may fall
    back to the bare module constant (that would lose the method/skill version)."""
    from app.synthesis import agent as synth

    rank = _run_synthesis_with_pinned_gateway(facade, isolated_settings)
    assert rank["prompt_version"] != synth.PROMPT_VERSION
    assert rank["factors"]["prompt_version"] != synth.PROMPT_VERSION
    # the bound-skill suffix is the differentiator
    assert rank["prompt_version"].endswith("@deadbeef")


# ---------- brief de-dup wiring (don't resurface unless changed) ----------

def _ranked_for_themes(themes):
    """Build a judge payload that surfaces one insight per given (theme, label)."""
    base = _RANKED["insights"][0]
    return {**_RANKED, "insights": [
        {**base, "theme_id": t.id, "title": f"{lbl} finding"}
        for t, lbl in themes
    ]}


def _seed_company(isolated_settings, ent_id="ent-A"):
    """brief_finding_state.enterprise_id FK-references companies(id); seed a bare
    company row so the fingerprint upsert isn't dropped on a FK violation."""
    isolated_settings["supabase"].table("companies").insert(
        {"id": ent_id, "slug": ent_id, "display_name": ent_id}
    ).execute()


def test_run_synthesis_records_finding_state_for_surfaced_themes(facade, isolated_settings):
    from app.synthesis import agent as synth

    _seed_company(isolated_settings)
    theme = _seed_theme_with_signals(facade, "ent-A", "SSO", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 1400000}, 1),
        ("customer_voice", "feature_request", {}, 2),
    ])
    with patch.object(synth, "llm_call", return_value=_llm_result(_ranked_for_themes([(theme, "SSO")]))):
        synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    rows = isolated_settings["supabase"].table("brief_finding_state").select("*") \
        .eq("enterprise_id", "ent-A").execute().data
    assert len(rows) == 1
    fp = rows[0]
    assert fp["theme_id"] == theme.id
    assert fp["fp_signal_count"] == 2
    assert fp["fp_breadth"] == 2
    assert fp["fp_revenue_at_stake"] == 1400000


def test_unchanged_prior_finding_is_suppressed_from_next_brief(facade, isolated_settings):
    """An already-surfaced theme that didn't change is dropped from the next
    brief's candidates; a sibling that DID change still appears."""
    from app.synthesis import agent as synth

    _seed_company(isolated_settings)
    a = _seed_theme_with_signals(facade, "ent-A", "Alpha", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 500000}, 1),
        ("customer_voice", "feature_request", {}, 2),
    ])
    b = _seed_theme_with_signals(facade, "ent-A", "Beta", [
        ("project_mgmt", "bug", {}, 1),
        ("communication", "feature_request", {}, 2),
    ])

    # Run 1 — surface both → fingerprints recorded for Alpha and Beta.
    with patch.object(synth, "llm_call",
                      return_value=_llm_result(_ranked_for_themes([(a, "Alpha"), (b, "Beta")]))):
        synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    # Change ONLY Alpha — add a fresh signal so its issue materially changed.
    from app.graph.types import Relationship, Signal
    from datetime import datetime, timezone
    sig = Signal(enterprise_id="ent-A", source_type="revenue", kind="deal_blocker",
                 content="Alpha new blocker", properties={"revenue_at_risk_usd": 900000},
                 valid_at=datetime.now(timezone.utc))
    facade.write_signal("ent-A", sig)
    facade.write_relationship("ent-A", Relationship(
        enterprise_id="ent-A", type="REQUESTS", source_kind="signal",
        source_id=sig.id, target_kind="entity", target_id=a.id))

    # Run 2 — capture the judge's candidate payload.
    captured = {}

    def _capture(**kw):
        captured["input"] = kw.get("input", "")
        return _llm_result(_ranked_for_themes([(a, "Alpha")]))

    with patch.object(synth, "llm_call", side_effect=_capture):
        synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    # Alpha changed → still a candidate; Beta unchanged → suppressed.
    assert "Alpha" in captured["input"]
    assert "Beta" not in captured["input"]

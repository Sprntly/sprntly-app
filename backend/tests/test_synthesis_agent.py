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


# ---------- goal-alignment factor (classifier + caching) ----------

def _kpi_tree(version=1):
    from app.kpi_tree import KpiTree, NorthStar, PrimaryMetric
    return KpiTree(
        north_star=NorthStar(metric="Weekly Active Technicians",
                             current_value=5310, target_value=7500),
        primary_metrics=[PrimaryMetric(metric="Net revenue retention",
                                       current_value=0.96, weight=1.0)],
        version=version,
    )


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
    _seed_theme_with_signals(facade, "ent-A", "SSO", [("revenue", "deal_blocker", {}, 0)])
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

    _seed_theme_with_signals(facade, "ent-A", "SSO", [("revenue", "deal_blocker", {}, 0)])
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
    _seed_theme_with_signals(facade, "ent-A", "SSO", [("revenue", "deal_blocker", {}, 0)])
    synth.run_synthesis(facade, "ent-A", dataset_slug="acme")
    text = captured["input"]
    assert "do NOT re-rank by strategic fit" in text
    assert "ALREADY priced into the candidate scores" in text


def test_no_tree_path_factors_neutral_no_classification(facade, isolated_settings, monkeypatch):
    from app.synthesis import agent as synth
    from app.synthesis import scoring

    _seed_theme_with_signals(facade, "ent-A", "SSO", [("revenue", "deal_blocker", {}, 0)])
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

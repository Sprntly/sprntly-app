"""Tests for the KPI tree config entity + Synthesis strategic anchoring."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.kpi_tree import KpiTree, NorthStar, PrimaryMetric, SecondarySignal


def _tree(**kw):
    base = dict(
        north_star=NorthStar(metric="Weekly Active Technicians",
                             current_value=5310, target_value=7500,
                             target_window_days=90),
        primary_metrics=[
            PrimaryMetric(metric="Net revenue retention", current_value=0.96,
                          target_value=1.10, weight=0.5),
            PrimaryMetric(metric="Enterprise new ARR", current_value=38000,
                          target_value=120000, weight=0.5),
        ],
        secondary_signals=[
            SecondarySignal(metric="Day-30 activation", current_value=0.61),
            SecondarySignal(metric="Support tickets/100 cos",
                            direction="lower_is_better"),
        ],
    )
    base.update(kw)
    return KpiTree(**base)


# ---------- model validation ----------

def test_valid_tree_roundtrips():
    t = _tree()
    assert KpiTree.model_validate(t.model_dump()).north_star.metric \
        == "Weekly Active Technicians"


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1.0"):
        _tree(primary_metrics=[
            PrimaryMetric(metric="A", weight=0.5),
            PrimaryMetric(metric="B", weight=0.2),
        ])


def test_render_for_prompt_compact():
    out = _tree().render_for_prompt()
    assert "North star: Weekly Active Technicians" in out
    assert "target 7500 within 90d" in out
    assert "Primary (weight 50%): Net revenue retention" in out
    assert "Secondary: Support tickets/100 cos (lower_is_better)" in out


# ---------- storage ----------

def _client_with(tree_raw):
    class FakeQ:
        def __init__(self): self.updated = None
        def select(self, *_): return self
        def eq(self, *_): return self
        def update(self, patch): self.updated = patch; return self
        def execute(self):
            return SimpleNamespace(data=[{"kpi_tree": tree_raw}])
    q = FakeQ()
    return type("C", (), {"table": lambda s, n: q})(), q


def test_load_returns_none_for_empty(monkeypatch):
    import app.kpi_tree as kt
    client, _ = _client_with({})
    monkeypatch.setattr(kt, "require_client", lambda: client)
    assert kt.load_kpi_tree("e") is None


def test_load_tolerates_invalid_shape(monkeypatch):
    import app.kpi_tree as kt
    client, _ = _client_with({"north_star": {"metric": "x"},
                              "primary_metrics": [{"metric": "a", "weight": 9}]})
    monkeypatch.setattr(kt, "require_client", lambda: client)
    assert kt.load_kpi_tree("e") is None   # weight>1 → invalid → None, no raise


def test_save_bumps_version_past_stored(monkeypatch):
    import app.kpi_tree as kt
    stored = _tree().model_dump(); stored["version"] = 4
    client, q = _client_with(stored)
    monkeypatch.setattr(kt, "require_client", lambda: client)
    saved = kt.save_kpi_tree("e", _tree())
    assert saved.version == 5
    assert q.updated["kpi_tree"]["version"] == 5


# ---------- synthesis anchoring ----------

def test_synthesis_includes_strategic_context(isolated_settings, monkeypatch):
    from app.graph import GraphFacade
    from app.graph.gateway import LLMResult
    from app.synthesis import agent as synth
    from tests.test_synthesis_agent import _seed_theme_with_signals, _RANKED

    facade = GraphFacade()
    theme = _seed_theme_with_signals(facade, "ent-A", "SSO", [
        ("revenue", "deal_blocker", {}, 1),
    ])
    monkeypatch.setattr(synth, "load_kpi_tree", lambda eid: _tree())

    captured = {}
    def fake_llm(**kw):
        captured["input"] = kw["input"]
        ranked = {**_RANKED, "insights": [
            {**_RANKED["insights"][0], "theme_id": theme.id}]}
        return LLMResult(output=ranked, model="m", prompt_version="t",
                         input_tokens=1, output_tokens=1,
                         cache_read_input_tokens=0, cache_creation_input_tokens=0,
                         cost_usd=0, latency_ms=1, stop_reason="end_turn")
    monkeypatch.setattr(synth, "llm_call", fake_llm)

    synth.run_synthesis(facade, "ent-A", dataset_slug="acme")
    assert "STRATEGIC CONTEXT" in captured["input"]
    assert "Weekly Active Technicians" in captured["input"]


def test_synthesis_works_without_tree(isolated_settings, monkeypatch):
    from app.graph import GraphFacade
    from app.graph.gateway import LLMResult
    from app.synthesis import agent as synth
    from tests.test_synthesis_agent import _seed_theme_with_signals, _RANKED

    facade = GraphFacade()
    theme = _seed_theme_with_signals(facade, "ent-B", "SSO", [
        ("revenue", "deal_blocker", {}, 1),
    ])
    monkeypatch.setattr(synth, "load_kpi_tree", lambda eid: None)
    monkeypatch.setattr(synth, "llm_call", lambda **kw: LLMResult(
        output={**_RANKED, "insights": [{**_RANKED["insights"][0], "theme_id": theme.id}]},
        model="m", prompt_version="t", input_tokens=1, output_tokens=1,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
        cost_usd=0, latency_ms=1, stop_reason="end_turn"))
    brief = synth.run_synthesis(facade, "ent-B", dataset_slug="b")
    assert brief["insights"]   # no tree → still generates

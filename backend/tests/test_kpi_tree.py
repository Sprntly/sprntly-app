"""Tests for the KPI tree config entity + Synthesis strategic anchoring."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.kpi_tree import KpiTree, NorthStar, PrimaryMetric, SecondarySignal


def _tree(**kw):
    base = dict(
        north_star=NorthStar(
            metric="Weekly Active Technicians",
            description="Technicians active in a 7-day window — our core engagement signal.",
        ),
        primary_metrics=[
            PrimaryMetric(metric="Net revenue retention",
                          description="Expansion minus churn across existing accounts."),
            PrimaryMetric(metric="Enterprise new ARR",
                          description="New annual recurring revenue from enterprise logos."),
        ],
        secondary_signals=[
            SecondarySignal(metric="Day-30 activation",
                            description="Share of new accounts reaching the aha moment by day 30."),
            SecondarySignal(metric="Support tickets/100 cos",
                            description="Inverse health signal — lower is better.",
                            direction="lower_is_better"),
        ],
    )
    base.update(kw)
    return KpiTree(**base)


# ---------- model validation ----------

def test_valid_tree_roundtrips():
    t = _tree()
    parsed = KpiTree.model_validate(t.model_dump())
    assert parsed.north_star.metric == "Weekly Active Technicians"
    assert parsed.north_star.description.startswith("Technicians active")
    assert parsed.primary_metrics[0].description.startswith("Expansion")


def test_new_shape_parses_metric_and_description():
    """The new {metric, description} shape parses for north star + metrics."""
    raw = {
        "north_star": {"metric": "Activation", "description": "Reach value fast."},
        "primary_metrics": [{"metric": "NRR", "description": "Net revenue retention."}],
        "secondary_signals": [{"metric": "NPS", "description": "Promoter score."}],
    }
    t = KpiTree.model_validate(raw)
    assert t.north_star.description == "Reach value fast."
    assert t.primary_metrics[0].metric == "NRR"
    assert t.secondary_signals[0].description == "Promoter score."


def test_legacy_numeric_shape_parses_and_ignores_old_fields():
    """Existing rows carry weight/current_value/target_value/target_window_days.

    They must still parse: the extra numeric keys are ignored and the new
    `description` defaults to "" rather than raising a ValidationError."""
    legacy = {
        "north_star": {
            "metric": "Weekly Active Technicians",
            "current_value": 5310,
            "target_value": 7500,
            "target_window_days": 90,
        },
        "primary_metrics": [
            {"metric": "NRR", "current_value": 0.96, "target_value": 1.10, "weight": 0.5},
            {"metric": "ARR", "current_value": 38000, "target_value": 120000, "weight": 0.5},
        ],
        "secondary_signals": [
            {"metric": "Day-30 activation", "current_value": 0.61},
        ],
        "version": 3,
    }
    t = KpiTree.model_validate(legacy)
    assert t.north_star.metric == "Weekly Active Technicians"
    assert t.north_star.description == ""           # defaulted
    assert t.primary_metrics[0].metric == "NRR"
    assert t.primary_metrics[0].description == ""    # defaulted
    # Old numeric fields are not retained on the model.
    dumped = t.primary_metrics[0].model_dump()
    assert "weight" not in dumped
    assert "current_value" not in dumped
    assert "target_value" not in dumped


def test_legacy_string_north_star_still_coerces():
    """The legacy bare-string north star coercion is preserved."""
    t = KpiTree.model_validate({"north_star": "Revenue"})
    assert t.north_star.metric == "Revenue"
    assert t.north_star.description == ""
    empty = KpiTree.model_validate({"north_star": ""})
    assert empty.north_star.metric == "North Star"


def test_render_for_prompt_uses_descriptions_not_weights():
    out = _tree().render_for_prompt()
    assert "North star: Weekly Active Technicians — Technicians active" in out
    assert "Primary: Net revenue retention — Expansion minus churn" in out
    assert "Secondary: Support tickets/100 cos — Inverse health signal" in out
    # No weights / targets leak into the prompt anymore.
    assert "weight" not in out.lower()
    assert "target" not in out.lower()
    assert "%" not in out


def test_render_for_prompt_metric_only_when_no_description():
    t = _tree(
        north_star=NorthStar(metric="Revenue"),
        primary_metrics=[PrimaryMetric(metric="NRR")],
        secondary_signals=[],
    )
    out = t.render_for_prompt()
    assert "North star: Revenue" in out
    assert "—" not in out  # no dangling em-dash when description is empty


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


def test_load_parses_legacy_tree(monkeypatch):
    """A legacy row with weights/values loads cleanly (extra keys ignored)."""
    import app.kpi_tree as kt
    client, _ = _client_with({"north_star": {"metric": "x"},
                              "primary_metrics": [{"metric": "a", "weight": 9}]})
    monkeypatch.setattr(kt, "require_client", lambda: client)
    tree = kt.load_kpi_tree("e")
    assert tree is not None
    assert tree.north_star.metric == "x"
    assert tree.primary_metrics[0].metric == "a"


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
        ("customer_voice", "feature_request", {}, 1),  # multi-source: clears gate
    ])
    monkeypatch.setattr(synth, "load_kpi_tree", lambda eid: _tree())
    monkeypatch.setattr(synth, "classify_theme_fit",
                        lambda *a, **k: "high")  # avoid a live classifier call

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
    # The metric description flows into the prompt as richer goal-fit context.
    assert "Technicians active" in captured["input"]


def test_synthesis_works_without_tree(isolated_settings, monkeypatch):
    from app.graph import GraphFacade
    from app.graph.gateway import LLMResult
    from app.synthesis import agent as synth
    from tests.test_synthesis_agent import _seed_theme_with_signals, _RANKED

    facade = GraphFacade()
    theme = _seed_theme_with_signals(facade, "ent-B", "SSO", [
        ("revenue", "deal_blocker", {}, 1),
        ("customer_voice", "feature_request", {}, 1),  # multi-source: clears gate
    ])
    monkeypatch.setattr(synth, "load_kpi_tree", lambda eid: None)
    monkeypatch.setattr(synth, "llm_call", lambda **kw: LLMResult(
        output={**_RANKED, "insights": [{**_RANKED["insights"][0], "theme_id": theme.id}]},
        model="m", prompt_version="t", input_tokens=1, output_tokens=1,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
        cost_usd=0, latency_ms=1, stop_reason="end_turn"))
    brief = synth.run_synthesis(facade, "ent-B", dataset_slug="b")
    assert brief["insights"]   # no tree → still generates

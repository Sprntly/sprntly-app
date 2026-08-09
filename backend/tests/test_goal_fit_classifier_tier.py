"""classify_goal_fit runs on the classifier tier, and that tier is PRICED.

Two things that only matter together. Moving the highest-volume call in the
system onto haiku is the saving; pricing the id the API actually returns is
what keeps that saving visible instead of silently reporting $0.
"""
from __future__ import annotations

import pytest

from app import llm_telemetry
from app.llm import FAST_MODEL
from app.llm_telemetry import MODEL_PRICING, RunUsage


def test_classify_goal_fit_requests_the_classifier_tier(monkeypatch):
    """Highest-volume call in the system (5,292 runs / 30d) for a 3-way label.

    Pinned at the call site rather than trusted to a default: a regression here
    is invisible in behaviour and only shows up as a bill and a slower gate.
    """
    from app.synthesis import scoring

    captured: dict = {}

    def fake_llm_call(**kwargs):
        captured.update(kwargs)
        class _R:
            output = {"fit": "high", "reasoning": "because"}
        return _R()

    class _Ent:
        properties: dict = {}

    class _Facade:
        def get_entity(self, *a, **k):
            return _Ent()

        def update_entity_properties(self, *a, **k):
            pass

    class _Tree:
        version = 3

        def render_for_prompt(self):
            return "North Star: activation"

    class _Theme:
        theme_id = "t1"
        theme_label = "onboarding"
        evidence: list = []

    monkeypatch.setattr(scoring, "llm_call", fake_llm_call)
    scoring.classify_theme_fit(_Facade(), "ent-1", _Theme(), _Tree())

    assert captured["model"] == FAST_MODEL
    assert captured["purpose"] == "classify_goal_fit"


def test_the_classifier_tier_is_priced():
    """A tier the pricing table does not know costs $0 everywhere it is read."""
    assert FAST_MODEL in MODEL_PRICING


@pytest.mark.parametrize("returned_model", [
    "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",   # what the API actually echoes back
])
def test_both_haiku_ids_price_identically(returned_model):
    """Regression guard for 1,769 `llm_usage unpriced` warnings in 14 days.

    We REQUEST the alias but metering and gateway._est_cost key on the model the
    response carries. If only the alias were priced, every haiku call would be
    recorded at zero cost — and gateway._est_cost fails OPEN (returns 0.0 on an
    unknown model) rather than raising, so nothing would ever have surfaced it.
    """
    usage = RunUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    cost = usage.est_cost_usd(returned_model)
    assert cost == pytest.approx(6.0), "1M in + 1M out on haiku is $1 + $5"


def test_unknown_model_still_fails_closed():
    """Adding an alias must not soften the fail-closed contract."""
    with pytest.raises(llm_telemetry.UnknownModelError):
        RunUsage(input_tokens=10).est_cost_usd("claude-not-a-model")

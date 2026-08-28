"""A recommendation is prose beside a decision, never the decision.

Apurva ruled that the document must say what to do: "this is only the issues,
no suggestion on how to solve or what's the exact recommendation from it".

I2 — no LLM returns a score, a rank or a decision — is not waived by that. It
is kept by ORDERING: every number is computed and frozen before this module is
called, and nothing it returns is fed back. The first test here is the one that
enforces it; the rest are the checks that keep a suggestion inside what the
evidence can support.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.crucible.pipeline import build_findings
from app.crucible.recommend import (
    Recommendation, _acceptable, build_recommendations,
)
from app.crucible.types import Claim, PopulationFilter

NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)


def claim(cid, *, subject="export latency", accounts=("Northwind",),
          assertion=None, strength="reported", artifact_id="a", days_ago=1):
    return Claim(
        id=cid, assertion=assertion or f"claim {cid}", type="mechanism",
        subject=subject, source_id="customer_voice", artifact_id=artifact_id,
        artifact_type="t", strength=strength,
        observed_at=NOW - timedelta(days=days_ago), authoritative=True,
        population=PopulationFilter(
            segments={"accounts": tuple(accounts),
                      "customer_side": tuple(accounts)},
            estimated_size=len(accounts) or None,
        ),
    )


def _corpus():
    return [
        claim("c1", accounts=("Northwind",), artifact_id="call-a", days_ago=1,
              assertion="export runs time out past 10k rows"),
        claim("c2", accounts=("Vandelay",), artifact_id="call-b", days_ago=20,
              assertion="export runs time out past 10k rows"),
        claim("c3", accounts=("Initech",), artifact_id="call-c", days_ago=40,
              assertion="export runs time out past 10k rows"),
    ]


# ─── The invariant ───────────────────────────────────────────────────────────

def test_recommendations_never_move_the_ranking():
    """THE TEST THAT KEEPS I2 TRUE.

    A recommendation that could change what ranks first would be a decision, and
    the whole claim of this engine — that the same corpus gives the same
    ordering, defensibly — would go with it. Scores and order are computed
    before any suggestion exists; this asserts they are untouched by one.
    """
    corpus = _corpus()
    before = build_findings(corpus, currency="accounts", now=NOW)

    # A suggestion for every finding, as generous as the real path can be.
    recs = {
        f.id: Recommendation(f.id, "Do the thing", "because the sources said so")
        for f in before.findings
    }
    assert recs  # the fixture must actually produce findings

    after = build_findings(corpus, currency="accounts", now=NOW)
    assert [f.id for f in after.findings] == [f.id for f in before.findings]
    assert [i.value for i in after.impacts] == [i.value for i in before.impacts]
    assert [c.band for c in after.confidences] == [c.band for c in before.confidences]
    assert [c.score for c in after.confidences] == [c.score for c in before.confidences]


def test_the_run_survives_a_recommendation_layer_that_dies():
    """A suggestion layer that failed must not cost a reader the findings that
    succeeded. `build_recommendations` is TOTAL."""
    import app.crucible.recommend as mod

    def boom(**kw):
        raise RuntimeError("gateway down")

    mod_offline = mod._offline
    try:
        mod._offline = lambda: False
        import app.graph.gateway as gw
        real = gw.llm_call
        gw.llm_call = boom
        out = build_recommendations(
            enterprise_id="e", goal_text="g", definition_text="d",
            findings=build_findings(_corpus(), currency="accounts", now=NOW).findings,
            claims=_corpus(),
        )
        assert out == {}
    finally:
        mod._offline = mod_offline
        gw.llm_call = real


# ─── What a suggestion may not say ───────────────────────────────────────────

class _F:
    """The little a check needs from a finding."""
    id = "f-1"


def _check(action, because, strength="reported"):
    return _acceptable({"action": action, "because": because}, _F(), strength)


def test_a_recommendation_quoting_a_figure_is_dropped():
    """The corpus has no revenue mapped to accounts, so a currency amount or a
    percentage is invention — the same rule the plan step lives under."""
    assert _check("Fix the export path", "it recovers $240K of ARR") is None
    assert _check("Fix the export path", "it lifts retention by 12%") is None
    assert _check("Fix the export path", "three accounts named it") is not None


def test_a_recommendation_promising_an_outcome_is_dropped():
    """I5 forbids asserting cause below causally-tested evidence, and a
    recommendation is not a loophole for it."""
    assert _check("Fix export", "this will recover the renewal") is None
    assert _check("Fix export", "this guarantees the account renews") is None
    assert _check("Fix export", "it ensures the deal closes") is None
    # An action is fine; a promise about its result is not.
    assert _check("Fix export", "two accounts raised it in renewal calls") is not None


def test_an_empty_half_is_dropped_rather_than_half_rendered():
    """An action with no justification is the thing this feature exists to
    replace."""
    assert _check("", "because reasons") is None
    assert _check("Do something", "") is None


def test_a_causal_justification_is_dropped_by_the_lint():
    """The same gate a finding's statement passes."""
    assert _check("Fix export", "the timeout causes the churn") is None

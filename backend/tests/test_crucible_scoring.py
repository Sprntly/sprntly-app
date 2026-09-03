"""Stage 9 scoring — and the moment PR1's harnesses stop being hypothetical.

Those harnesses shipped before the thing they guard. This is the file where the
REAL `score_impact` is handed to `assert_impact_ignores_corroboration`, which
sweeps every field of `ConfidenceInputs` and `Finding` and demands byte-identical
impact under all of them.

If that test ever fails, the engine has become a slower, more expensive way to
surface the obvious — a finding in one careful analysis nobody circulated sinks
below a loud one, which is the single failure the whole design exists to
prevent. It is not a style check.

No network, no DB, no LLM.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from app.crucible.invariants import (
    InvariantViolation,
    assert_impact_ignores_corroboration,
    assert_scores_frozen_across,
)
from app.crucible.scoring import (
    MAX_CORROBORATION_BONUS,
    MAX_SCORE_WITHOUT_LEVER_EVIDENCE,
    score_confidence,
    score_impact,
)
from app.crucible.types import ConfidenceInputs, Finding, ImpactInputs

NOW = datetime.now(timezone.utc)


def a_finding(*, impact=None, **conf) -> Finding:
    ci = dict(
        strengths=("measured", "reported"),
        claim_types=("magnitude", "preference"),
        observed_ats=(NOW - timedelta(days=10), NOW - timedelta(days=20)),
        authoritative_count=2, claim_count=6,
        independent_authoritative_source_types=2,
        surfaced_by=("structural", "corpus"),
    )
    ci.update(conf)
    return Finding(
        id="f-1", statement="A finding.", claim_ids=("c-1", "c-2"),
        impact_inputs=impact or ImpactInputs(
            currency="arr_dollars", affected_population=1000.0,
            movable_gap=0.2, value_per_unit=50.0,
        ),
        confidence_inputs=ConfidenceInputs(**ci),
        adjudication="corroborated",
    )


# ══ I1 — the flagship, now against the real scorer ═══════════════════════════

def test_the_real_score_impact_ignores_corroboration():
    """THE test. Sweeps every field of both objects, mutates each, and demands
    byte-identical impact."""
    assert_impact_ignores_corroboration(score_impact, a_finding())


def test_it_still_holds_for_a_reach_only_impact():
    """Corpus-only findings are sized in accounts with no value-per-unit, which
    is a different arithmetic path through `score_impact` — so it needs its own
    pass through the harness rather than an assumption that one covers both."""
    reach_only = ImpactInputs(
        currency="accounts", affected_population=12.0,
        movable_gap=1.0, value_per_unit=None,
    )
    assert_impact_ignores_corroboration(score_impact, a_finding(impact=reach_only))


def test_adding_a_corroboration_bonus_to_impact_would_be_caught():
    """Mutation proof against the real function: the exact change spec F2
    predicts under deadline."""
    def tempting(finding: Finding):
        base = score_impact(finding)
        n = finding.confidence_inputs.independent_authoritative_source_types
        return dataclasses.replace(
            base, value=None if base.value is None else base.value * (1 + 0.05 * n)
        )

    with pytest.raises(InvariantViolation) as exc:
        assert_impact_ignores_corroboration(tempting, a_finding())
    assert exc.value.invariant == "I1"


# ══ I3 — unsizeable is not zero ══════════════════════════════════════════════

@pytest.mark.parametrize("missing", ["affected_population", "movable_gap"])
def test_an_unmeasured_input_makes_the_impact_unsizeable_not_zero(missing):
    """"We could not size this" and "this is worth nothing" lead to opposite
    decisions, and only one of them is true."""
    inputs = dataclasses.replace(
        ImpactInputs(currency="accounts", affected_population=10.0,
                     movable_gap=0.5, value_per_unit=None),
        **{missing: None},
    )
    assert score_impact(a_finding(impact=inputs)).value is None


def test_reach_without_a_price_is_sized_in_reach():
    """Honest in a corpus-only run: we know how many accounts are touched and
    nothing about what one is worth."""
    inputs = ImpactInputs(currency="accounts", affected_population=12.0,
                          movable_gap=1.0, value_per_unit=None)
    assert score_impact(a_finding(impact=inputs)).value == 12.0


def test_a_measured_zero_survives_as_zero():
    inputs = ImpactInputs(currency="accounts", affected_population=0.0,
                          movable_gap=1.0, value_per_unit=None)
    assert score_impact(a_finding(impact=inputs)).value == 0.0


def test_grounded_commercial_native_units_pass_through_untouched():
    """`native_units` is where a finding's grounded commercial evidence
    rides (see `pipeline._grounded_commercial_native_units`) — `score_impact`
    must carry it straight through to `Impact` exactly as given, the same
    way `currency`/`assumed_params` already do, and must not let it touch
    `value` (never extrapolated into the reach-based sizing)."""
    inputs = ImpactInputs(
        currency="accounts", affected_population=2.0, movable_gap=1.0,
        value_per_unit=None,
        native_units={"commercial_grounded_usd": 150000.0,
                      "commercial_grounded_accounts": 2.0},
    )
    impact = score_impact(a_finding(impact=inputs))
    assert impact.native_units == {"commercial_grounded_usd": 150000.0,
                                    "commercial_grounded_accounts": 2.0}
    # Reach-based sizing (2 accounts x full gap) is untouched by the
    # presence of a grounded dollar figure alongside it.
    assert impact.value == 2.0


# ══ Confidence ═══════════════════════════════════════════════════════════════

def test_corroboration_is_capped_and_cannot_dominate():
    """It may nudge how sure we are. The moment it can dominate, it is doing
    impact's job one field over."""
    lonely = score_confidence(a_finding(independent_authoritative_source_types=1), now=NOW)
    crowded = score_confidence(a_finding(independent_authoritative_source_types=99), now=NOW)
    assert crowded.score - lonely.score <= MAX_CORROBORATION_BONUS + 1e-9


def test_a_single_authoritative_claim_scores_close_to_a_corroborated_one():
    """The quiet-finding guard, asserted RELATIVELY.

    An absolute threshold was the first attempt and it was the wrong shape: the
    combined score is dominated by the solution leg, so every finding sits low
    until a lever library exists (see MAX_SCORE_WITHOUT_LEVER_EVIDENCE). What
    actually matters is that being ALONE costs a finding almost nothing — at
    most the corroboration cap — because one careful analysis nobody circulated
    is exactly what this engine exists to surface.
    """
    lone = score_confidence(a_finding(
        strengths=("measured",), claim_types=("magnitude",),
        observed_ats=(NOW - timedelta(days=5),),
        authoritative_count=1, claim_count=6,
        independent_authoritative_source_types=1,
    ), now=NOW)
    crowd = score_confidence(a_finding(
        strengths=("measured",), claim_types=("magnitude",),
        observed_ats=(NOW - timedelta(days=5),),
        authoritative_count=6, claim_count=6,
        independent_authoritative_source_types=6,
    ), now=NOW)
    assert crowd.score - lone.score <= MAX_CORROBORATION_BONUS + 1e-9


def test_without_lever_evidence_the_combined_path_cannot_reach_medium():
    """Measured, not assumed. The spec's formula pins every finding below the
    medium threshold while the solution leg is a constant — which is the
    formula correctly reporting that we know nothing about whether any fix
    works, and precisely why corpus-only mode exists rather than rendering it.
    """
    best_possible = score_confidence(a_finding(
        strengths=("causally_tested",), claim_types=("magnitude",),
        observed_ats=(NOW,), authoritative_count=9, claim_count=9,
        independent_authoritative_source_types=9, coverage=1.0,
    ), now=NOW)
    assert best_possible.score <= MAX_SCORE_WITHOUT_LEVER_EVIDENCE + 1e-9
    assert best_possible.band == "low"


def test_no_authoritative_source_is_penalised():
    weak = score_confidence(a_finding(authoritative_count=0), now=NOW)
    strong = score_confidence(a_finding(authoritative_count=3), now=NOW)
    assert weak.score < strong.score


def test_stale_evidence_scores_lower_than_fresh():
    fresh = score_confidence(
        a_finding(observed_ats=(NOW,), claim_types=("direction",)), now=NOW)
    stale = score_confidence(
        a_finding(observed_ats=(NOW - timedelta(days=400),),
                  claim_types=("direction",)), now=NOW)
    assert stale.score < fresh.score


def test_an_unchecked_confound_halves_the_problem_leg():
    """Not a small doubt — it means the comparison population may differ from
    the frontier on something nobody looked at.

    Asserted on the PROBLEM LEG, which is what the code halves. The combined
    score compresses everything toward the constant solution leg, so measuring
    the halving there understates it and the first version of this test picked
    a threshold the arithmetic could never meet.
    """
    clean = score_confidence(a_finding(), now=NOW)
    confounded = score_confidence(
        a_finding(blockers=("confound_unchecked",)), now=NOW)

    def problem_leg(c):
        base = (c.components["strongest"] * 0.35 + c.components["authority"] * 0.20
                + c.components["sample"] * 0.15 + c.components["coverage"] * 0.20
                + c.components["recency"] * 0.10)
        return base * c.components["confound"]

    assert confounded.components["confound"] == 0.5
    assert problem_leg(confounded) == pytest.approx(problem_leg(clean) / 2)
    assert confounded.score < clean.score


def test_the_band_never_exposes_a_decimal():
    c = score_confidence(a_finding(), now=NOW)
    assert c.band in {"high", "medium", "low"}
    assert isinstance(c.score, float)      # internal only


# ══ Corpus-only mode ═════════════════════════════════════════════════════════

def test_with_no_outcome_evidence_confidence_caps_at_medium():
    """The common case on a real tenant, not an edge case."""
    c = score_confidence(a_finding(solution_evidence_absent=True), now=NOW)
    assert c.band in {"medium", "low"}
    assert c.cap_reason is not None
    assert "no outcome evidence" in c.cap_reason


def test_the_cap_is_a_statement_about_the_corpus_not_the_finding():
    """Rendered beside the band, so a reader does not read a corpus-wide limit
    as a judgement about this particular finding."""
    c = score_confidence(a_finding(solution_evidence_absent=True), now=NOW)
    assert "corpus" in c.cap_reason


def test_corpus_only_still_ORDERS_findings():
    """The bug this mode exists to fix: combining with a constant solution leg
    made every finding score identically, so the ranking carried no
    information."""
    strong = score_confidence(a_finding(
        solution_evidence_absent=True, strengths=("measured",),
        claim_types=("magnitude",), observed_ats=(NOW,),
        authoritative_count=3, claim_count=8), now=NOW)
    weak = score_confidence(a_finding(
        solution_evidence_absent=True, strengths=("reported",),
        claim_types=("preference",), observed_ats=(NOW - timedelta(days=300),),
        authoritative_count=0, claim_count=1), now=NOW)
    assert strong.score > weak.score


def test_the_weakest_leg_is_named_in_prose():
    """The only reason the decomposition exists: a reader who knows WHICH half
    is weak can probe the problem or pilot the fix. A single number only tells
    them to hesitate."""
    c = score_confidence(a_finding(solution_evidence_absent=True), now=NOW)
    assert c.weakest_leg == "solution"
    assert "unevidenced" in c.weakest_leg_reason


# ══ I10 — scoring is frozen once produced ════════════════════════════════════

def test_a_prioritiser_cannot_write_back_into_these_scores():
    scored = [(score_impact(a_finding()), score_confidence(a_finding(), now=NOW))]
    assert_scores_frozen_across(
        lambda items: sorted(items, key=lambda p: -(p[0].value or 0)), scored
    )


def test_scoring_is_deterministic():
    """Reproducibility is the differentiator against a general LLM: the same
    substrate must produce the same numbers every time."""
    f = a_finding()
    assert repr(score_impact(f)) == repr(score_impact(f))
    assert repr(score_confidence(f, now=NOW)) == repr(score_confidence(f, now=NOW))

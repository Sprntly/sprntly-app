"""Stage 10b and Stage 11 — ordering, and the decision it supports.

The two invariants these exist to keep:

  I10  prioritisation never mutates impact or confidence. It reads frozen
       scores and returns rankings BESIDE them.
  I7   an effort that cannot be derived is `unrankable` with its reason, never
       a fabricated number — because a laundered guess then decides an ordering.

No network, no LLM, no DB.
"""
from __future__ import annotations

import pytest

from app.crucible.decide import DECISIVE_MARGIN, decide
from app.crucible.invariants import InvariantViolation, assert_scores_frozen_across
from app.crucible.prioritise import (
    Framework,
    framework_for,
    prioritise,
    prioritise_one,
)
from app.crucible.types import Confidence, EffortEstimate, Impact


def imp(value=None, population=None, currency="accounts") -> Impact:
    return Impact(value=value, currency=currency,
                  affected_population=population, movable_gap=1.0,
                  value_per_unit=None)


def conf(score=0.6, band="medium") -> Confidence:
    return Confidence(
        band=band, score=score, weakest_leg="solution",
        weakest_leg_reason="no outcome evidence", cap_reason="",
    )


def eff(weeks=None, derivation="no comparables", comparables=()) -> EffortEstimate:
    return EffortEstimate(weeks=weeks, derivation=derivation, comparables=comparables)


DERIVED = eff(4.0, "median of 3 prior projects on billing: 3, 4, 6", (3.0, 4.0, 6.0))


# ── I7: an underivable effort is stated, never invented ──────────────────────

def test_an_underivable_effort_is_unrankable_and_says_why():
    p = prioritise_one("f-1", imp(1000.0, 40.0), conf(), eff())
    assert p.unrankable == "effort_underivable"
    assert p.score is None, "an unrankable item must not carry a score"
    assert "no comparables" in p.effort_derivation
    # The three terms that ARE computed still travel — they are what the reader
    # is left with when the ordering cannot be produced.
    assert p.reach == 40.0 and p.impact_value == 1000.0
    assert p.confidence_score == pytest.approx(0.6)


def test_an_unrankable_item_is_not_sorted_to_the_bottom(): 
    """"We could not derive an effort" and "this ranked last" lead to opposite
    decisions — the same distinction I3 draws between unsized and small."""
    out = prioritise([
        ("f-rank", imp(500.0, 10.0), conf(), DERIVED),
        ("f-none", imp(9999.0, 99.0), conf(), eff()),
    ])
    assert [p.finding_id for p in out.ranked] == ["f-rank"]
    assert [p.finding_id for p in out.unrankable] == ["f-none"]


def test_when_nothing_ranks_the_report_says_so_where_it_shows():
    out = prioritise([("f-1", imp(1.0, 1.0), conf(), eff()),
                      ("f-2", imp(2.0, 2.0), conf(), eff())])
    assert not out.ranked and len(out.unrankable) == 2
    assert "Nothing could be ranked" in out.note
    assert "guess wearing a decision" in out.note


def test_the_arithmetic_is_rendered_not_just_the_ordering():
    """§10b: "A rank the reader cannot interrogate is an oracle."""
    p = prioritise_one("f-1", imp(1000.0, 40.0), conf(0.5), DERIVED)
    assert p.score == pytest.approx(1000.0 * 0.5 / 4.0)
    assert "1,000" in p.arithmetic and "0.50" in p.arithmetic and "4" in p.arithmetic


# ── I10: ordering never touches sizing ───────────────────────────────────────

def test_prioritisation_does_not_mutate_the_scores_it_reads():
    scored = [(imp(100.0, 5.0), conf(0.4)), (imp(200.0, 9.0), conf(0.8))]

    def step(pairs):
        return prioritise([
            (f"f-{n}", i, c, DERIVED) for n, (i, c) in enumerate(pairs)
        ]).ranked

    assert_scores_frozen_across(step, scored)


def test_the_i10_guard_catches_a_prioritiser_that_rewrites_scores():
    """The guard has to be worth something. A step in the ordinary functional
    style — rebuild and return altered copies while claiming only to have
    ordered them — is the route that is easy to write by accident."""
    from dataclasses import replace

    scored = [(imp(100.0, 5.0), conf()), (imp(200.0, 9.0), conf())]

    def cheating(pairs):
        return [(replace(i, value=1.0), c) for i, c in pairs]

    with pytest.raises(InvariantViolation):
        assert_scores_frozen_across(cheating, scored)


# ── Framework selection ──────────────────────────────────────────────────────

def test_rice_is_the_default_when_the_company_states_no_rubric():
    fw = framework_for({})
    assert fw.source == "default_rice"
    assert {c.key for c in fw.criteria} == {"reach", "impact", "confidence", "effort"}


def test_a_company_rubric_is_carried_verbatim_and_never_paraphrased():
    """Adopted the way a metric definition is adopted: read it, state it, let
    them change it. Parsing prose into weights would be the paraphrase §10
    forbids one gate over."""
    words = "we ship the cheapest thing that unblocks a paying customer"
    fw = framework_for({"prioritization_framework": words})
    assert fw.source == "company_defined"
    assert fw.verbatim == words, "the company's own words must survive intact"
    assert fw.stated_at


def test_ranking_is_a_total_order():
    """Two candidates with identical scores must not flip between requests."""
    out = prioritise([
        ("f-b", imp(100.0, 1.0), conf(0.5), DERIVED),
        ("f-a", imp(100.0, 1.0), conf(0.5), DERIVED),
    ])
    assert [p.finding_id for p in out.ranked] == ["f-a", "f-b"]


# ── Stage 11: the decision ───────────────────────────────────────────────────

FINDINGS = [{"id": "f-big", "statement": "Exports time out for enterprise accounts"},
            {"id": "f-small", "statement": "Mobile parity gaps"},
            {"id": "f-none", "statement": "Agency permissions"}]


def test_it_names_a_first_move_and_the_reason_is_checkable():
    out = prioritise([
        ("f-big", imp(1000.0, 100.0), conf(0.8), DERIVED),
        ("f-small", imp(100.0, 10.0), conf(0.5), DERIVED),
    ])
    d = decide(out, FINDINGS)
    assert d.recommended_id == "f-big"
    assert "Exports time out" in d.recommended_statement
    # The why quotes the frozen numbers, so a reader can check it against the
    # table rather than taking it on authority.
    assert "1,000" in d.why and "0.80" in d.why


def test_what_it_did_not_pick_names_the_term_that_decided_it():
    out = prioritise([
        ("f-big", imp(1000.0, 100.0), conf(0.8), DERIVED),
        ("f-small", imp(100.0, 10.0), conf(0.8), DERIVED),
    ])
    d = decide(out, FINDINGS)
    assert d.not_picked
    reason = d.not_picked[0].reason
    assert "smaller size" in reason
    # BOTH sides quoted — a comparison with one number in it is not checkable.
    assert "100" in reason and "1,000" in reason
    # AND THE PERCENTAGE IS REAL. Leader 1000x0.8/4 = 200; runner 100x0.8/4 =
    # 20; the gap is 90%. Asserting only that both numbers appear let a
    # mutated denominator render a nonsensical ">100% behind" and still pass.
    assert "90% behind" in reason, reason


def test_an_unrankable_candidate_appears_in_what_was_not_picked():
    out = prioritise([
        ("f-big", imp(1000.0, 100.0), conf(0.8), DERIVED),
        ("f-none", imp(5000.0, 500.0), conf(0.9), eff()),
    ])
    d = decide(out, FINDINGS)
    ids = {n.finding_id for n in d.not_picked}
    assert "f-none" in ids, "an unrankable candidate is not silently dropped"
    reason = next(n.reason for n in d.not_picked if n.finding_id == "f-none")
    assert "could not be ranked" in reason


def test_it_withholds_a_recommendation_when_the_top_two_are_a_tie():
    """A coin flip announced as a decision is worse than the coin flip: the
    reader cannot tell which one they were handed."""
    out = prioritise([
        ("f-big", imp(1000.0, 100.0), conf(0.8), DERIVED),
        ("f-small", imp(980.0, 98.0), conf(0.8), DERIVED),
    ])
    d = decide(out, FINDINGS)
    assert d.recommended_id is None
    assert d.withheld and "tie" in d.withheld
    # It still shows the runners-up, so withholding is not withholding evidence.
    assert d.not_picked


def test_it_withholds_when_nothing_could_be_ranked_at_all():
    out = prioritise([("f-1", imp(1.0, 1.0), conf(), eff()),
                      ("f-2", imp(2.0, 2.0), conf(), eff())])
    d = decide(out, FINDINGS)
    assert d.recommended_id is None
    assert "not going to name a first move" in d.withheld
    assert "no effort could be derived" in d.withheld


def test_a_clear_winner_states_what_would_change_it():
    out = prioritise([
        ("f-big", imp(1000.0, 100.0), conf(0.8), DERIVED),
        ("f-small", imp(100.0, 10.0), conf(0.5), DERIVED),
    ])
    d = decide(out, FINDINGS).would_change_it
    assert d, "a recommendation with no falsifier is an assertion"
    # CHECKS THE NUMBER, not the words. The first version asserted only that
    # the string was non-empty and mentioned "effort" or "size", so mutating
    # the formula to a tautology still passed — the same "checks the words, not
    # the numbers" hole that produced the I3 bug two functions over.
    # Leader: 1000 x 0.8 / 4 = 200. Runner: 100 x 0.5 / 4 = 12.5.
    # The size at which the leader ties the runner is 12.5 * 4 / 0.8 = 62.5.
    assert "62.5" in d, f"the falsifier states no checkable number: {d}"


def test_the_margin_is_what_decides_a_tie_not_a_hardcoded_pair():
    """Pins the threshold's behaviour rather than one example either side."""
    close = prioritise([
        ("f-a", imp(100.0, 1.0), conf(0.8), DERIVED),
        ("f-b", imp(100.0 * (1 - DECISIVE_MARGIN / 2), 1.0), conf(0.8), DERIVED),
    ])
    assert decide(close, FINDINGS).recommended_id is None

    clear = prioritise([
        ("f-a", imp(100.0, 1.0), conf(0.8), DERIVED),
        ("f-b", imp(100.0 * (1 - DECISIVE_MARGIN * 2), 1.0), conf(0.8), DERIVED),
    ])
    assert decide(clear, FINDINGS).recommended_id == "f-a"



def test_the_reason_names_the_term_that_actually_decided_it():
    """The first version picked a branch on raw thresholds and then quoted the
    COMBINED gap in every branch — so a 0.06 confidence difference could be
    reported as "weaker evidence, 98% behind" when the 98% came entirely from a
    50x effort difference the sentence never mentioned."""
    slow = eff(50.0, "median of 3: 40, 50, 60", (40.0, 50.0, 60.0))
    out = prioritise([
        ("f-lead", imp(1000.0, 10.0), conf(0.86), DERIVED),   # 4 weeks
        ("f-slow", imp(1000.0, 10.0), conf(0.80), slow),      # 50 weeks
    ])
    d = decide(out, [{"id": "f-lead", "statement": "lead"},
                     {"id": "f-slow", "statement": "slow"}])
    reason = next(n.reason for n in d.not_picked if n.finding_id == "f-slow")
    assert "more work" in reason, f"named the wrong term: {reason}"
    assert "50 weeks" in reason and "4" in reason
    assert "weaker evidence" not in reason


def test_an_unsized_finding_is_never_ranked_or_recommended():
    """I3 at the ranking. Coercing `value=None` into the numerator gave it a
    real score of 0.0, let it rank, and let `decide()` recommend it — emitting
    "the largest thing this reading found that can also be sized: —"."""
    out = prioritise([("f-unsized", imp(None, 40.0), conf(0.9), DERIVED)])
    assert not out.ranked
    assert out.unrankable[0].unrankable == "unsized"
    d = decide(out, [{"id": "f-unsized", "statement": "unsized thing"}])
    assert d.recommended_id is None
    assert "could not be sized" in out.note


def test_a_zero_week_effort_is_unrankable_with_a_stated_reason():
    """`weeks == 0` is not free work, it is an unusable denominator. It
    previously produced score=None with `unrankable` unset, so the item landed
    in the unrankable bucket carrying no reason at all."""
    free = eff(0.0, "median of 3: 0, 0, 0", (0.0, 0.0, 0.0))
    out = prioritise([("f-free", imp(100.0, 5.0), conf(0.7), free)])
    assert not out.ranked
    assert out.unrankable[0].unrankable == "effort_not_positive"
    assert "zero weeks" in out.note

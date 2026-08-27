"""RICE, with every term either derived or declared missing.

Apurva: "We can use RICE by default. Let's see if we can make decision
assumptions in a way that could be grounded with the data we have in KG."

The tests that matter here are not the arithmetic. They are the ones that stop
a missing term being quietly filled.
"""
from __future__ import annotations

from app.crucible.rice import (
    CONFIDENCE_BY_BAND, EFFORT_ABSENT, effort_flags, impact_for, rice_for,
)


def _row(**kw):
    base = dict(label="export latency", reach=10.0, reach_unit="accounts",
                claim_types=["mechanism"], confidence_band="medium")
    base.update(kw)
    return rice_for(**base)


# ─── What is missing stays missing ──────────────────────────────────────────

def test_no_effort_means_the_score_says_so_rather_than_assuming_one():
    """EFFORT IS NOT IN THE CORPUS. Nothing carries a person-month —
    `KIND_TO_CLAIM_TYPE` maps `milestone → attempt` and no estimate is ingested
    anywhere. Inventing "2.0 PM" from claim types is the fabrication the rest of
    this engine refuses, and dividing by a default of 1 while calling the result
    RICE is the same invention wearing a neutral number."""
    r = _row()
    assert r.effort is None
    assert r.scored_without_effort is True
    # R x I x C, undivided.
    assert r.score == 10.0 * 1.0 * 0.5


def test_a_supplied_effort_actually_divides():
    """The reader can supply one at the gate, and then it is real RICE."""
    r = _row(effort=2.0)
    assert r.scored_without_effort is False
    assert r.score == (10.0 * 1.0 * 0.5) / 2.0


def test_a_zero_or_negative_effort_is_treated_as_absent():
    """A cleared field posts 0. Dividing by it raises; treating it as "no
    effort supplied" is the only reading that is both safe and true."""
    for bad in (0, 0.0, -3):
        assert _row(effort=bad).effort is None


def test_an_unsized_finding_has_no_rice_rather_than_a_zero():
    """I3, in the one place it would be easiest to lose. An unsized finding has
    no reach; printing 0 says "we sized this and it is nothing", which leads to
    the opposite decision from "we could not size it"."""
    assert _row(reach=None).score is None


# ─── The one real judgement, disclosed as one ───────────────────────────────

def test_impact_comes_from_the_claim_type_and_says_which():
    """The memo's scale is "3 = directly determines whether the ARR renews, 1 =
    influences indirectly". The corpus's own vocabulary for that distinction is
    the claim type."""
    blocked, why = impact_for(["constraint"])
    asked, _ = impact_for(["preference"])
    described, _ = impact_for(["mechanism"])
    assert blocked > asked > described
    assert "stopped" in why


def test_the_strongest_claim_decides_not_the_average():
    """A theme carrying one blocked deal among ten descriptions is still about
    a blocked deal. Averaging lets volume of commentary dilute the single claim
    that bears on revenue — the relevance-gate failure in a new costume."""
    many_descriptions = ["mechanism"] * 10 + ["constraint"]
    strongest, _ = impact_for(many_descriptions)
    assert strongest == 3.0


def test_an_unknown_claim_type_scores_the_floor_not_a_crash():
    assert impact_for(["something_new"])[0] == 1.0


# ─── Effort evidence is directional, and never enters the arithmetic ────────

def test_attempted_before_is_flagged_but_does_not_move_the_score():
    """"was tried / committed / abandoned / reverted" is evidence something is
    harder than it looks. It is not a person-month, so it is a flag a PM reads,
    not a term in the maths."""
    plain = _row(claim_types=["constraint"])
    tried = _row(claim_types=["constraint", "attempt"])
    assert any("attempted before" in f for f in tried.flags)
    assert not plain.flags
    assert tried.score == plain.score


def test_already_exists_is_flagged_as_the_cheaper_shape():
    r = _row(claim_types=["existence"])
    assert any("already exists" in f for f in r.flags)


# ─── The mapping is an assumption, and is one place ─────────────────────────

def test_the_band_mapping_is_derived_from_the_band_not_reinvented():
    """The BAND is what `scoring.py` computed; only the fraction is assumed."""
    assert CONFIDENCE_BY_BAND["high"] > CONFIDENCE_BY_BAND["medium"]
    assert CONFIDENCE_BY_BAND["medium"] > CONFIDENCE_BY_BAND["low"]
    assert _row(confidence_band="high").confidence == CONFIDENCE_BY_BAND["high"]
    # An unrecognised band is the most cautious reading, never the most
    # flattering one.
    assert _row(confidence_band="").confidence == CONFIDENCE_BY_BAND["low"]


def test_the_absent_effort_wording_is_the_memos_own():
    assert EFFORT_ABSENT == "Unquantified"


# ─── The prioritize skill's discipline for missing inputs ───────────────────
#
# The skill does NOT estimate effort — "Effort comes from engineering, not the
# PM's hope", and "the skill mitigates with sensitivity analysis but can't
# manufacture good inputs". What it prescribes is how to run WITHOUT it.


def test_every_assumed_input_is_labelled():
    """The skill: "Mark every input real vs. [ASSUMPTION]."

    `impact` is always assumed — it is our ordering of claim types, not the
    corpus's. `reach` never is: it is a count of named accounts.
    """
    r = _row()
    assert "impact" in r.assumed
    assert "effort" in r.assumed          # none supplied
    assert "reach" not in r.assumed
    assert "effort" not in _row(effort=2.0).assumed


def test_the_convergence_count_says_how_many_inputs_were_present():
    """The skill: "Report a signal-convergence count per item: how many of the
    expected inputs were present." A row scored on half its inputs must not
    look like one scored on all four."""
    assert _row().inputs_present == 3               # no effort
    assert _row(effort=2.0).inputs_present == 4
    assert _row(reach=None).inputs_present == 2     # no reach, no effort


def test_a_missing_input_lowers_confidence():
    """The skill: "label each missing input, and lower Confidence
    accordingly." Without the penalty a row scored on two inputs presents the
    same confidence as one scored on four — the false precision the skill's own
    limitations section warns about."""
    full = _row(effort=2.0)
    partial = _row()
    assert partial.confidence_after_gaps < full.confidence_after_gaps
    # And it never bottoms out at zero for a row that does have evidence.
    assert _row(reach=None).confidence_after_gaps > 0


def test_a_row_missing_reach_or_effort_is_flagged_low_confidence():
    """The skill: "If Reach and Effort are both guessed, the RICE score is
    flagged as low-confidence"."""
    assert _row().low_confidence is True             # no effort
    assert _row(reach=None).low_confidence is True   # no reach
    assert _row(effort=2.0).low_confidence is False


def test_effort_cannot_flip_a_ranking_where_nobody_supplied_it():
    """A COMMON DIVISOR CHANGES NO ORDER. With effort absent everywhere, RICE
    degenerates to reach x impact x confidence and the ranking is provably
    effort-insensitive — which is the derived form of the reference memo's own
    observation that "cheapness is not the constraint here"."""
    from app.crucible.rice import sensitivity

    rows = [_row(label="a", reach=11.0), _row(label="b", reach=20.0)]
    assert sensitivity(rows) == ()


def test_a_row_whose_rank_depends_on_an_unknown_effort_is_named():
    """The skill: "the ranking ships with a sensitivity note naming the 1-2
    items whose rank flips on a shaky input." Without this the table launders a
    guess into an order."""
    from app.crucible.rice import sensitivity

    # `cheap` wins outright on value; `unknown` has more raw value but no
    # effort, so where its effort actually lands decides the order.
    cheap = _row(label="cheap", reach=10.0, claim_types=["constraint"], effort=0.5)
    unknown = _row(label="unknown", reach=12.0, claim_types=["constraint"])
    flipped = sensitivity([cheap, unknown])
    assert flipped, "a rank that depends on an unsupplied effort must be named"
    assert len(flipped) <= 2


def test_sensitivity_says_nothing_when_there_is_nothing_to_compare():
    from app.crucible.rice import sensitivity

    assert sensitivity([]) == ()
    assert sensitivity([_row()]) == ()

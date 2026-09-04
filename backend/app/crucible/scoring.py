"""Stage 9 — scoring. Fully deterministic, and the highest-stakes file here.

Two functions, and the whole design is the wall between them.

`score_impact` answers HOW BIG. `score_confidence` answers HOW SURE. They read
different inputs, and impact never sees corroboration (I1) — not as a small
bonus, not as a tiebreak, not at all. That single rule is why this engine can
surface a finding that appears in one careful analysis nobody circulated, and
why every naive version of this system buries it.

The temptation is real and the spec names it (F2): when four sources agree, a
"small bonus" to impact makes demo output look more sensible. It also re-buries
every quiet finding, and turns the product into a slower, more expensive way to
surface the obvious. `assert_impact_ignores_corroboration` runs against these
functions in CI precisely so that change cannot land quietly.

CORPUS-ONLY MODE. On a tenant with no `outcome_measured` signals — the common
case, not an edge case, per the Phase 0 spike — nothing records whether any fix
ever worked. The solution leg is then a constant for every finding, which makes
a combined score useless for ordering and dishonest to render. So confidence
bands on the PROBLEM leg alone, caps at medium, and says why. That is the
enterprise-readiness §1 posture, and it is the behaviour real tenants will get.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from app.crucible.invariants import decay_factor
from app.crucible.types import (
    STRENGTH_SCORE,
    Confidence,
    ConfidenceInputs,
    Finding,
    Impact,
    ImpactInputs,
    band_for,
)

#: How much agreement can ever move confidence. SPEC §9.
#:
#: Small on purpose, and the critique argues it should be zero. It is kept
#: because independent authoritative sources agreeing IS weak evidence about
#: reliability — but capped hard, because the moment corroboration can dominate,
#: it starts doing the job impact is supposed to do, one field over.
MAX_CORROBORATION_BONUS = 0.15

#: What we know about whether a fix works, absent any outcome evidence.
#: `unknown_no_prior` from SPEC §9's table.
SOLUTION_NO_PRIOR = 0.25

# A CONSEQUENCE OF THE SPEC'S FORMULA, measured rather than assumed, and worth
# knowing before anyone reads a band:
#
#     score = min(problem, solution) * 0.7 + mean(problem, solution) * 0.3
#
# With `solution` pinned at 0.25 the maximum achievable score is
# 0.25*0.7 + 0.625*0.3 = 0.3625 — BELOW the `medium` threshold. So until a lever
# library exists (Phase 3), the combined path bands EVERY finding `low`,
# however strong its evidence, and the ranking carries no information.
#
# That is not a bug in the formula; it is the formula correctly reporting that
# we know nothing about whether any fix works. It IS a bug to render it, which
# is what `solution_evidence_absent` exists for. A caller with no lever
# evidence should set it — and until Phase 3, that is every caller.
MAX_SCORE_WITHOUT_LEVER_EVIDENCE = 0.3625


def score_impact(finding: Finding) -> Impact:
    """HOW BIG, in the goal's currency.

    Reads `finding.impact_inputs` and NOTHING else. Not `adjudication`, not
    `claim_ids`, not the confidence side — a scorer that touches any of them
    fails `assert_impact_ignores_corroboration`, which sweeps every field of
    both objects.

    Returns `value=None` when the corpus never measured what it would take to
    size this (I3). That is not zero, and the renderer must not print it as
    zero: an unsizeable finding goes in its own section, because "we could not
    size this" and "this is worth nothing" lead to opposite decisions.

    `size_rank` PASSES THROUGH AND NEVER ENTERS `value`. A quoted dollar
    figure is real evidence about size, and folding it into `value` would be
    the worst possible way to use it: `value` is denominated in ACCOUNTS on a
    corpus-only run, so a dollar amount added to it wins by six orders of
    magnitude and anyone who names a figure outranks everyone who did not,
    regardless of how big the figure is or how sure we are of it. People who
    quote numbers are self-selected, so that is the loudest-problem failure
    wearing a currency symbol.

    So the rank is ORDINAL and it is computed elsewhere. A finding sized in
    dollars is ranked against the other findings sized in dollars; one sized
    in accounts against the other findings sized in accounts. This function
    carries the answer onto the frozen `Impact` and computes nothing from it —
    which is what keeps a figure-only finding (no named accounts, so `value`
    is honestly `None`) able to rank at all, without ever pretending we
    measured a reach we did not.
    """
    i: ImpactInputs = finding.impact_inputs

    if i.affected_population is None or i.movable_gap is None:
        value: Optional[float] = None
    elif i.value_per_unit is None:
        # Reach without a price. Honest in a corpus-only run: we know how many
        # accounts are touched and nothing about what one is worth, so the
        # population IS the size, denominated in accounts. The caller carries an
        # AssumedParam for the missing value-per-unit (I8) — that disclosure is
        # what keeps this from reading as a currency figure.
        value = i.affected_population * i.movable_gap
    else:
        value = i.affected_population * i.movable_gap * i.value_per_unit

    return Impact(
        value=value,
        currency=i.currency,
        affected_population=i.affected_population,
        movable_gap=i.movable_gap,
        value_per_unit=i.value_per_unit,
        assumed_params=i.assumed_params,
        native_units=i.native_units,
        size_rank=i.size_rank,
        grounded_figures=i.grounded_figures,
    )


def _problem_leg(
    c: ConfidenceInputs, now: datetime
) -> tuple[float, dict[str, float]]:
    """Is this real, and is it correctly sized?"""
    n = max(c.claim_count, 1)

    strongest = max((STRENGTH_SCORE[s] for s in c.strengths), default=0.0)
    authority = 1.0 if c.authoritative_count else 0.4
    sample = 1.0 if n >= 5 else (0.6 if n >= 2 else 0.3)
    coverage = c.coverage if c.coverage is not None else (c.authoritative_count / n)

    if c.observed_ats and c.claim_types:
        pairs = list(zip(c.claim_types, c.observed_ats))
        recency = sum(decay_factor(t, at, now) for t, at in pairs) / len(pairs)
    else:
        recency = 0.0

    # THE ONLY PLACE CORROBORATION APPEARS, and it is capped.
    corroboration = min(
        MAX_CORROBORATION_BONUS,
        0.05 * max(0, c.independent_authoritative_source_types - 1),
    )

    # A confound the sweep could not rule out halves the leg. An unchecked
    # confound is not a small doubt: it means the comparison population may
    # differ from the frontier on something we never looked at.
    confound = 0.5 if "confound_unchecked" in c.blockers else 1.0

    components = {
        "strongest": strongest, "authority": authority, "sample": sample,
        "coverage": coverage, "recency": recency,
        "corroboration": corroboration, "confound": confound,
    }
    base = (
        strongest * 0.35 + authority * 0.20 + sample * 0.15
        + coverage * 0.20 + recency * 0.10
    )
    return max(0.0, min(1.0, base * confound + corroboration)), components


def score_confidence(finding: Finding, *, now: datetime) -> Confidence:
    """HOW SURE, as a band. Decimals exist for sorting and never reach output.

    `now` IS REQUIRED, and that is deliberate. Recency decay reads a clock, so
    an internal `datetime.now()` made this function non-deterministic — the same
    finding scored differently on every call, which its own determinism test
    caught. Reproducibility is the differentiator against a general LLM ("the
    same substrate produces the same ranking"), and a scorer that quietly reads
    the wall clock cannot offer it.

    Passing it also fixes a subtler wrong: every finding in one run now decays
    against the SAME instant, so two findings are never scored against clocks a
    minute apart. The run captures `now` once at its start.
    """
    c = finding.confidence_inputs
    problem, components = _problem_leg(c, now)
    solution = SOLUTION_NO_PRIOR
    components["solution"] = solution

    if c.solution_evidence_absent:
        # CORPUS-ONLY. With no outcome evidence anywhere in the tenant, every
        # finding's solution leg is the same constant, so combining would order
        # findings by a number that carries no information and render a
        # confident-looking score over an absent leg. Band on the problem leg
        # and cap at medium — the cap is a statement about the CORPUS, not about
        # this finding, which is why `cap_reason` is rendered beside it.
        score = problem
        band = "medium" if problem >= 0.50 else "low"
        return Confidence(
            band=band, score=score,
            weakest_leg="solution",
            weakest_leg_reason=(
                "nothing in this company's connected sources records whether a "
                "fix like this has ever worked, so how well it would work is "
                "unevidenced — the size and the diagnosis are not"
            ),
            components=components, blockers=c.blockers,
            cap_reason="capped at medium: no outcome evidence in the corpus",
        )

    score = min(problem, solution) * 0.7 + ((problem + solution) / 2) * 0.3
    weakest = "problem" if problem <= solution else "solution"
    return Confidence(
        band=band_for(score), score=score,
        weakest_leg=weakest,
        weakest_leg_reason=_explain(weakest, problem, solution, components),
        components=components, blockers=c.blockers,
    )


def _explain(leg: str, problem: float, solution: float, comp: dict) -> str:
    """One sentence naming the weaker leg.

    The whole reason confidence decomposes at all. A reader who knows WHICH half
    is weak can act — probe the problem, or pilot the fix — where a single
    number only tells them to hesitate.
    """
    if leg == "solution":
        return (
            "the problem is well evidenced; whether this particular fix moves "
            "it is not"
        )
    if comp["authority"] < 1.0:
        return (
            "no source that may speak to this claim type actually reported it, "
            "so the finding rests on sources outside their authority"
        )
    if comp["sample"] < 1.0:
        return "too few independent claims to be sure this is not noise"
    if comp["recency"] < 0.5:
        return "the evidence behind this has aged past its useful window"
    return "the evidence is thinner than the proposed fix's track record"

"""RICE, with every term either derived from the corpus or declared missing.

Apurva: "We can use RICE by default. Let's see if we can make decision
assumptions in a way that could be grounded with the data we have in KG."

WHAT IS DERIVED AND WHAT IS ASSUMED — the whole point of this module is that a
reader can tell which is which without being told twice.

  REACH        DERIVED. `impact.affected_population` — the accounts a theme
               touches. The reference memo scores reach in ARR; this corpus has
               no revenue mapped to accounts (the plan step says so up front),
               so reach is in ACCOUNTS and the table says that in its own
               definition row rather than quietly meaning something else.

  IMPACT       ASSUMED, from a derived signal. The memo's scale is "3 =
               directly determines whether the ARR renews or bills, 1 =
               influences it indirectly", and the corpus has a real proxy for
               that distinction: the CLAIM TYPE. A `constraint` is something
               blocked; a `preference` is something asked for; the rest
               describe. That ordering is a judgement — it assumes a blocker
               outranks a request — so it ships as an `AssumedParam` with its
               basis, disclosed on the finding, and can be argued with.

  CONFIDENCE   DERIVED. The band `scoring.py` already computed, mapped to a
               percentage. The MAPPING is the assumption, not the band.

  EFFORT       NOT IN THE DATA, and not invented. Nothing in the corpus carries
               a person-month: `KIND_TO_CLAIM_TYPE` maps `milestone → attempt`
               and no story point or estimate is ingested anywhere. Inventing
               "2.0 PM" from claim types would be exactly the fabrication the
               rest of this engine refuses.

               So effort is either SUPPLIED BY THE READER at the gate, or it is
               absent — and when it is absent the score is R x I x C and the
               table says so. The reference memo's own §04 makes this argument
               for a different missing term ("What RICE does not capture, and
               why it does not change the answer"), and its appendix already
               has the vocabulary: `Unquantified`.

TWO DIRECTIONAL FLAGS, where the corpus does carry something about effort:

  `attempt` claims — "was tried / committed / abandoned / reverted". Somebody
  already went at this, which is evidence it is harder than it looks. The memo
  reasons the same way: "The March pause happened for exactly this reason."

  `existence` claims — "exists in the product today". This is a fix or a
  communication problem rather than a build.

Neither is a number and neither enters the arithmetic. They are flags a PM can
act on, printed beside the row.

THE SKILL'S DISCIPLINE, ADOPTED WHOLESALE. `skills/prioritize` (vendored;
`synthesis/scoring.py` already ports its VoC formula) does NOT estimate effort —
"Effort comes from engineering, not the PM's hope", and "the skill mitigates
with sensitivity analysis but can't manufacture good inputs". What it prescribes
instead is how to run when an input is missing, and every rule is honoured here:

  - "Never refuse because an input is missing — run the framework with what's
    available, label each missing input, and lower Confidence accordingly."
  - "Mark every input real vs. [ASSUMPTION]."
  - "Report a signal-convergence count per item: how many of the expected
    inputs were present."
  - "The ranking ships with a sensitivity note naming the 1-2 items whose rank
    flips on a shaky input."
  - "If Reach and Effort are both guessed, the RICE score is flagged as
    low-confidence."

THE SCORE NEVER REORDERS ANYTHING. `_rank` owns the order and is frozen before
this module runs; RICE is arithmetic over scores that already exist, rendered as
a table. I10 holds: prioritisation never mutates impact or confidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

#: Claim type → the memo's 1..3 impact scale.
#:
#: THE ONE REAL JUDGEMENT IN THIS FILE. A blocked deal and a feature request are
#: not the same distance from revenue, and this is the corpus's own vocabulary
#: for that difference — but the ORDERING is ours, not the data's.
IMPACT_BY_CLAIM_TYPE: dict[str, float] = {
    # Something is blocked. Closest thing the corpus has to "directly
    # determines whether the revenue lands".
    "constraint": 3.0,
    # Somebody asked for something. Influences, does not determine.
    "preference": 2.0,
    # Describes the world: how it works, what exists, what was tried, which way
    # a number moved, how big it was. Real evidence, further from a decision.
    "mechanism": 1.0,
    "existence": 1.0,
    "attempt": 1.0,
    "direction": 1.0,
    "magnitude": 1.0,
}

#: Confidence band → the fraction RICE multiplies by. The BAND is derived; this
#: mapping is the assumption, and it is disclosed as one.
CONFIDENCE_BY_BAND: dict[str, float] = {"high": 0.8, "medium": 0.5, "low": 0.25}

#: What the reader sees when effort was never supplied. Borrowed from the
#: reference memo's appendix, which uses exactly this vocabulary for a column it
#: cannot fill.
EFFORT_ABSENT = "Unquantified"


#: What RICE expects. Used for the skill's signal-convergence count — "N of M
#: inputs present" — so a row scored on half its inputs cannot look like a row
#: scored on all of them.
RICE_INPUTS = ("reach", "impact", "confidence", "effort")

#: How much a missing input costs the row's confidence, per the skill's "label
#: each missing input, and lower Confidence accordingly". Multiplicative so two
#: gaps cost more than one, and floored so a row never reads as zero-confidence
#: when it does have evidence.
_MISSING_INPUT_PENALTY = 0.6
_MIN_CONFIDENCE = 0.1


@dataclass(frozen=True)
class RiceRow:
    """One finding's RICE, with the missing term visible rather than filled."""
    label: str
    reach: Optional[float]
    reach_unit: str
    impact: float
    impact_basis: str
    confidence: float
    confidence_band: str
    effort: Optional[float]
    #: Directional effort evidence from the corpus. Never enters the score.
    flags: tuple[str, ...]

    @property
    def assumed(self) -> tuple[str, ...]:
        """Which inputs are NOT traced to data — the skill's `[ASSUMPTION]`.

        `impact` always: it is our ordering of claim types, not the corpus's.
        `effort` whenever the reader did not supply one.
        `reach` never: it is a count of named accounts.
        """
        out = ["impact"]
        if not self.effort:
            out.append("effort")
        return tuple(out)

    @property
    def inputs_present(self) -> int:
        """The skill's convergence count: how many of RICE's four we have."""
        n = 2  # impact and confidence always resolve to something
        if self.reach is not None:
            n += 1
        if self.effort:
            n += 1
        return n

    @property
    def low_confidence(self) -> bool:
        """The skill: "If Reach and Effort are both guessed, the RICE score is
        flagged as low-confidence." Reach is never guessed here — it is counted
        — so this fires when reach is ABSENT and effort was not supplied, which
        is the same condition one step further along."""
        return self.reach is None or not self.effort

    @property
    def confidence_after_gaps(self) -> float:
        """Confidence, lowered once per missing input.

        The skill requires the penalty; without it a row scored on two of four
        inputs presents the same confidence as one scored on four, which is the
        false precision its own limitations section warns about.
        """
        c = self.confidence
        for _ in range(len(RICE_INPUTS) - self.inputs_present):
            c *= _MISSING_INPUT_PENALTY
        return max(c, _MIN_CONFIDENCE)

    @property
    def score(self) -> Optional[float]:
        """R x I x C / E, or R x I x C when no effort was supplied.

        None when there is no reach: an unsized finding has no RICE, and
        printing 0 for it is the I3 error this codebase exists to avoid.
        """
        if self.reach is None:
            return None
        value = self.reach * self.impact * self.confidence
        if self.effort:
            return value / self.effort
        return value

    @property
    def scored_without_effort(self) -> bool:
        return self.reach is not None and not self.effort


def impact_for(claim_types: Sequence[str]) -> tuple[float, str]:
    """The strongest claim type present decides, and says which.

    STRONGEST, NOT THE AVERAGE. A theme carrying one blocked deal among ten
    descriptions is still about a blocked deal; averaging would let volume of
    commentary dilute the one claim that bears on revenue — which is the exact
    failure the relevance gate was built for, in a different costume.
    """
    best, kind = 1.0, ""
    for t in claim_types:
        v = IMPACT_BY_CLAIM_TYPE.get(t, 1.0)
        if v > best:
            best, kind = v, t
    if best >= 3.0:
        return best, "a blocked deal or a stated constraint — something is stopped"
    if best >= 2.0:
        return best, "a customer asked for this — it influences rather than determines"
    return best, "describes what happens, what exists or what was tried"


def effort_flags(claim_types: Sequence[str]) -> tuple[str, ...]:
    """Directional evidence about effort. Never a number, never in the score."""
    kinds = set(claim_types)
    out: list[str] = []
    if "attempt" in kinds:
        out.append(
            "attempted before — the tracker records this being tried, "
            "committed or reverted, which is evidence it is harder than it looks"
        )
    if "existence" in kinds:
        out.append(
            "already exists in the product — likely a fix or a communication "
            "problem rather than a build"
        )
    return tuple(out)


def rice_for(
    *,
    label: str,
    reach: Optional[float],
    reach_unit: str,
    claim_types: Sequence[str],
    confidence_band: str,
    effort: Optional[float] = None,
) -> RiceRow:
    impact, basis = impact_for(claim_types)
    return RiceRow(
        label=label,
        reach=reach,
        reach_unit=reach_unit or "accounts",
        impact=impact,
        impact_basis=basis,
        confidence=CONFIDENCE_BY_BAND.get((confidence_band or "").strip(), 0.25),
        confidence_band=(confidence_band or "").strip() or "low",
        effort=effort if effort and effort > 0 else None,
        flags=effort_flags(claim_types),
    )


#: The effort range a sensitivity check sweeps when none was supplied. Wide on
#: purpose: the question is not "what is the effort" but "does the ranking
#: survive not knowing it".
PLAUSIBLE_EFFORT = (0.5, 3.0)


def sensitivity(rows: Sequence[RiceRow]) -> tuple[str, ...]:
    """Which rows' rank flips on an input we do not have.

    The skill: "the ranking ships with a sensitivity note naming the 1-2 items
    whose rank flips on a shaky input." Without this the table launders a
    guess into an order — which the skill names as the standing risk of every
    quantitative framework ("quant frameworks launder subjective inputs into
    false precision").

    Swept over `PLAUSIBLE_EFFORT` rather than over one guess, because the claim
    being tested is that the ORDER holds however the unknown lands.
    """
    scored = [r for r in rows if r.score is not None]
    if len(scored) < 2:
        return ()
    base = [r.label for r in sorted(scored, key=lambda r: -(r.score or 0))]
    flips: list[str] = []
    for low_high in (PLAUSIBLE_EFFORT[0], PLAUSIBLE_EFFORT[1]):
        swept = sorted(
            scored,
            key=lambda r: -((r.reach or 0) * r.impact * r.confidence
                            / (r.effort or low_high)),
        )
        for pos, r in enumerate(swept):
            if base.index(r.label) != pos and r.label not in flips:
                flips.append(r.label)
    return tuple(flips[:2])

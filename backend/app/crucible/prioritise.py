"""Stage 10b — what to do first, without touching how big anything is.

SPEC §10b. Scoring says how big and how sure; this says what to do first, and it
is a separate stage precisely so ordering can never contaminate sizing.

    I10. Prioritisation never mutates impact or confidence.

Everything here takes frozen `Impact`/`Confidence` and returns rankings beside
them. `assert_scores_frozen_across` (invariants.py) proves it, and it checks the
returned set as well as the input, because the ordinary functional style —
`return sorted(replace(i, value=...) for i in items)` — rewrites scores while
claiming only to have ordered them.

WHAT RICE BINDS TO, AND WHY NOTHING NEW IS ESTIMATED. §10b: "RICE binds to
fields that already exist, and this binding is the whole trick."

    Reach       accounts the finding touches      (Stage 9, already computed)
    Impact      the finding's own impact value    (Stage 9, already computed)
    Confidence  confidence.score                  (Stage 9; rendered as a band)
    Effort      derive_effort(comparables)        (may be None under I7)

EFFORT IS None ON EVERY TENANT TODAY, and that is not a bug in this module. I7
requires >= 3 comparable prior projects and `derive_effort` refuses to invent an
estimate without them; nothing in this codebase records project durations —
`projects` carries no dates beyond created/updated, `prd_tickets` none, and
`project_mgmt` signals carry assignee/status/priority and no duration. So every
candidate returns `unrankable='effort_underivable'` with its derivation, is
listed in its own section, and says why. §10b: "Fabricating an effort number to
complete a RICE score would launder a guess into a decision, which is precisely
the failure I7 exists to stop."

That makes the honest output today a ranking that cannot rank, and it still
carries the three terms that ARE computed — which is strictly more than the
report said before, and is the shape that lights up the moment effort has a
source.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

from app.crucible.types import Confidence, EffortEstimate, Impact

logger = logging.getLogger(__name__)

#: RICE, used when the company states no rubric of its own. Weights are equal
#: and explicit: a weighting nobody chose should not look like one somebody did.
DEFAULT_FRAMEWORK_ID = "rice_default"


@dataclass(frozen=True)
class Criterion:
    key: str
    weight: float
    direction: str            # 'higher_better' | 'lower_better'
    bound_to: Optional[str]   # which computed field supplies it


@dataclass(frozen=True)
class Framework:
    """§10b: the company's own criteria if they state any, else RICE.

    `source` and `stated_at` exist so the output can say WHERE the ordering
    rule came from. A rank whose criteria are invisible is an oracle, and §10d
    requires the framework to be approved with the plan rather than chosen after
    the results are in — a reader who sees the criteria only after the ranking
    cannot tell whether they were picked to fit it.
    """
    id: str
    source: str                       # 'company_defined' | 'default_rice'
    criteria: tuple[Criterion, ...]
    stated_at: Optional[str] = None
    verbatim: Optional[str] = None    # the company's own words, never tidied

    def to_json(self) -> dict:
        return {
            "id": self.id, "source": self.source, "stated_at": self.stated_at,
            "verbatim": self.verbatim,
            "criteria": [asdict(c) for c in self.criteria],
        }


_RICE = Framework(
    id=DEFAULT_FRAMEWORK_ID,
    source="default_rice",
    criteria=(
        Criterion("reach", 1.0, "higher_better", "impact.affected_population"),
        Criterion("impact", 1.0, "higher_better", "impact.value"),
        Criterion("confidence", 1.0, "higher_better", "confidence.score"),
        Criterion("effort", 1.0, "lower_better", "effort.weeks"),
    ),
)


@dataclass(frozen=True)
class Priority:
    """One candidate's place in the ordering, or its refusal to have one.

    `unrankable` and `score` are mutually exclusive by construction: an item
    without a derivable effort has no score at all rather than a score computed
    from a stand-in, because a stand-in is the laundered guess I7 exists to
    stop.
    """
    finding_id: str
    reach: Optional[float]
    impact_value: Optional[float]
    confidence_score: float
    effort_weeks: Optional[float]
    effort_derivation: str
    score: Optional[float] = None
    unrankable: Optional[str] = None
    #: The arithmetic, rendered. §10b: "The output shows the inputs, not just
    #: the ordering. A rank the reader cannot interrogate is an oracle."
    arithmetic: str = ""

    def to_json(self) -> dict:
        return asdict(self)


def framework_for(company_context: Optional[dict]) -> Framework:
    """§10b framework selection: the company's own criteria, else RICE.

    ADOPTED THE WAY A METRIC DEFINITION IS ADOPTED — read it, state it, let them
    change it, and never paraphrase it into something tidier. So the company's
    text is carried VERBATIM in `verbatim` and is not parsed into weights: a
    rubric written in prose ("we ship the cheapest thing that unblocks a
    customer") has no numeric reading, and inventing one would be exactly the
    paraphrase §10 forbids one gate over. It is stated, and RICE's arithmetic is
    still what runs — with the output saying both, so the reader can see where
    they disagree.
    """
    stated = ((company_context or {}).get("prioritization_framework") or "")
    stated = stated.strip() if isinstance(stated, str) else ""
    if not stated:
        return _RICE
    return Framework(
        id="company_defined",
        source="company_defined",
        criteria=_RICE.criteria,
        stated_at="your company context",
        verbatim=stated,
    )


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:,.4g}"


def prioritise_one(
    finding_id: str,
    impact: Impact,
    confidence: Confidence,
    effort: EffortEstimate,
) -> Priority:
    """One candidate's RICE, or its stated refusal.

    READS the frozen score objects and returns a NEW `Priority` beside them.
    Nothing here writes to `impact` or `confidence`; they are frozen dataclasses
    and this never calls `replace` on either.
    """
    reach = impact.affected_population
    value = impact.value

    if not effort.derivable:
        return Priority(
            finding_id=finding_id, reach=reach, impact_value=value,
            confidence_score=confidence.score, effort_weeks=None,
            effort_derivation=effort.derivation,
            unrankable="effort_underivable",
            arithmetic="",
        )

    weeks = effort.weeks or 0.0
    # `value` already carries reach: Stage 9 computes it as
    # population x movable_gap x value_per_unit, so multiplying by reach again
    # would count the population twice — the "reach" column is shown for the
    # reader, not multiplied back in.
    numerator = (value if value is not None else 0.0) * confidence.score
    score = numerator / weeks if weeks else None
    return Priority(
        finding_id=finding_id, reach=reach, impact_value=value,
        confidence_score=confidence.score, effort_weeks=weeks,
        effort_derivation=effort.derivation,
        score=score,
        arithmetic=(
            f"{_fmt(value)} x {confidence.score:.2f} confidence "
            f"/ {_fmt(weeks)} weeks = {_fmt(score)}"
        ),
    )


@dataclass(frozen=True)
class Prioritisation:
    framework: Framework
    ranked: tuple[Priority, ...] = ()
    unrankable: tuple[Priority, ...] = ()
    #: Stated whenever nothing could be ranked, so an empty table is explained
    #: where it appears rather than left to look like a bug.
    note: str = ""

    def to_json(self) -> dict:
        return {
            "framework": self.framework.to_json(),
            "ranked": [p.to_json() for p in self.ranked],
            "unrankable": [p.to_json() for p in self.unrankable],
            "note": self.note,
        }


def prioritise(
    scored: Sequence[tuple[str, Impact, Confidence, EffortEstimate]],
    *,
    framework: Optional[Framework] = None,
) -> Prioritisation:
    """Stage 10b over the deep set. Reads frozen scores; writes none.

    Returns ranked items and unrankable ones SEPARATELY rather than sorting the
    unrankable to the bottom, because "we could not derive an effort for this"
    and "this ranked last" lead to opposite decisions — the same distinction I3
    draws between an unsized finding and a small one.
    """
    fw = framework or _RICE
    priorities = [prioritise_one(fid, i, c, e) for fid, i, c, e in scored]

    ranked = [p for p in priorities if p.unrankable is None and p.score is not None]
    unrankable = [p for p in priorities if p not in ranked]
    # Highest score first; `finding_id` breaks ties so the order is total and
    # cannot flip between requests.
    ranked.sort(key=lambda p: (-(p.score or 0.0), p.finding_id))
    unrankable.sort(key=lambda p: p.finding_id)

    note = ""
    if not ranked and unrankable:
        note = (
            f"Nothing could be ranked. RICE needs an effort estimate and "
            f"{len(unrankable)} candidate"
            f"{'' if len(unrankable) == 1 else 's'} could not derive one — "
            f"there is no record of how long comparable work took, so any "
            f"number here would be a guess wearing a decision. Reach, size and "
            f"confidence are shown for each; the ordering is the part that is "
            f"missing."
        )
    return Prioritisation(framework=fw, ranked=tuple(ranked),
                          unrankable=tuple(unrankable), note=note)

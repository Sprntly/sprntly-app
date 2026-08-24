"""Stage 11 — the report lands a decision, or says why it cannot.

The report ranked themes and stopped. A PM's job ends at a decision they can
defend in a room, and a ranking is not one: it says what is biggest, not what to
do, and it leaves "why not the other thing" unanswered, which is the question
that actually gets asked.

THE RECOMMENDATION IS THE RANKING, STATED AS A POSITION. No model picks it and
no model scores it (I2: "no LLM call anywhere in this system returns a score, a
rank, a confidence value, or a decision about what to do next"). The pick is
rank 1 from frozen scores; the runners-up and the reasons they lost are computed
DELTAS between those same frozen numbers. That is what makes "I did not pick X
because…" true rather than narrated — the sentence is arithmetic in words.

WHAT IT REFUSES TO DO. Where the ranking cannot support a position — nothing
ranked, or the top two separated by less than `DECISIVE_MARGIN` — it says so
instead of picking. A recommendation is only worth having if the alternative was
a real possibility; a coin-flip announced as a decision is worse than the coin
flip, because the reader cannot tell which one they were handed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Sequence

from app.crucible.prioritise import Prioritisation, Priority

#: Below this relative gap the top two are a tie, not an ordering. Stated as a
#: share of the leader's score so it scales with the numbers rather than
#: assuming a unit.
DECISIVE_MARGIN = 0.15


@dataclass(frozen=True)
class NotPicked:
    """A runner-up and the computed reason it lost.

    `reason` is derived from the frozen scores, never composed: "smaller
    reach (41 accounts vs 118)" is checkable against the table above it, and a
    sentence a reader can check is the difference between an argument and an
    assertion.
    """
    finding_id: str
    statement: str
    reason: str

    def to_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Decision:
    recommended_id: Optional[str]
    recommended_statement: str
    why: str
    not_picked: tuple[NotPicked, ...] = ()
    would_change_it: str = ""
    #: Set when no position is defensible. The report renders THIS instead of a
    #: recommendation, rather than rendering a recommendation with a caveat.
    withheld: str = ""

    def to_json(self) -> dict:
        return {
            "recommended_id": self.recommended_id,
            "recommended_statement": self.recommended_statement,
            "why": self.why,
            "not_picked": [n.to_json() for n in self.not_picked],
            "would_change_it": self.would_change_it,
            "withheld": self.withheld,
        }


def _fmt(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:,.4g}"


def _statement_of(finding_id: str, findings: Sequence[dict]) -> str:
    for f in findings:
        if str(f.get("id") or f.get("finding_id") or "") == finding_id:
            return str(f.get("statement") or "")
    return finding_id


def _loss_reason(leader: Priority, other: Priority) -> str:
    """WHY this one lost, from the numbers themselves.

    Ordered by which term actually differs most, so the sentence names the term
    that decided it rather than the first one that happened to differ. Every
    branch quotes both sides, so the claim is checkable against the table.
    """
    if other.score is None:
        return (
            f"could not be ranked at all — {other.effort_derivation}"
        )
    lead, mine = leader.score or 0.0, other.score
    gap = (lead - mine) / lead if lead else 0.0

    # Which input explains the gap.
    if (leader.impact_value or 0) > (other.impact_value or 0) * 1.2:
        return (
            f"smaller size ({_fmt(other.impact_value)} against "
            f"{_fmt(leader.impact_value)}), {gap:.0%} behind on the combined score"
        )
    if leader.confidence_score > other.confidence_score + 0.05:
        return (
            f"weaker evidence (confidence {other.confidence_score:.2f} against "
            f"{leader.confidence_score:.2f}), {gap:.0%} behind"
        )
    if (other.effort_weeks or 0) > (leader.effort_weeks or 0) * 1.2:
        return (
            f"more work for the same return ({_fmt(other.effort_weeks)} weeks "
            f"against {_fmt(leader.effort_weeks)}), {gap:.0%} behind"
        )
    return f"{gap:.0%} behind on the combined score, with no single term deciding it"


def decide(
    prioritisation: Prioritisation,
    findings: Sequence[dict],
    *,
    margin: float = DECISIVE_MARGIN,
) -> Decision:
    """Turn a frozen ranking into a position, or decline to.

    Reads `prioritisation` and `findings`; writes to neither. No model call.
    """
    ranked = prioritisation.ranked

    if not ranked:
        n = len(prioritisation.unrankable)
        return Decision(
            recommended_id=None, recommended_statement="", why="",
            withheld=(
                f"I am not going to name a first move from this run. "
                f"{n} candidate{'' if n == 1 else 's'} could not be ranked "
                f"because no effort could be derived for any of them, and a "
                f"recommendation that skipped that would be an ordering I made "
                f"up. What each one touches, how big it is and how sure I am "
                f"are all above; the part that would turn those into a "
                f"sequence is the part that is missing."
            ),
        )

    leader = ranked[0]
    lead_score = leader.score or 0.0

    if len(ranked) > 1:
        runner = ranked[1]
        gap = (lead_score - (runner.score or 0.0)) / lead_score if lead_score else 0.0
        if gap < margin:
            return Decision(
                recommended_id=None, recommended_statement="", why="",
                not_picked=tuple(
                    NotPicked(p.finding_id, _statement_of(p.finding_id, findings),
                              _loss_reason(leader, p))
                    for p in ranked[1:4]
                ),
                withheld=(
                    f"The top two are {gap:.0%} apart, which is inside the "
                    f"margin this ranking can actually resolve — treat them as "
                    f"a tie and pick on something this run cannot see, like who "
                    f"is free. Calling one of them first would be a coin flip "
                    f"announced as a decision."
                ),
            )

    why_bits = [
        f"It is the largest thing this reading found that can also be sized: "
        f"{_fmt(leader.impact_value)} at confidence "
        f"{leader.confidence_score:.2f}, against "
        f"{_fmt(leader.effort_weeks)} weeks of comparable work"
    ]
    if leader.reach is not None:
        why_bits.append(f"touching {_fmt(leader.reach)} accounts")

    return Decision(
        recommended_id=leader.finding_id,
        recommended_statement=_statement_of(leader.finding_id, findings),
        why=". ".join(why_bits) + ".",
        not_picked=tuple(
            NotPicked(p.finding_id, _statement_of(p.finding_id, findings),
                      _loss_reason(leader, p))
            for p in list(ranked[1:4]) + list(prioritisation.unrankable[:2])
        ),
        would_change_it=(
            f"This stops being first if its size drops below "
            f"{_fmt((ranked[1].score or 0) * (leader.effort_weeks or 1) / max(leader.confidence_score, 0.01)) if len(ranked) > 1 else '—'}"
            f", or if the effort turns out materially worse than the "
            f"{_fmt(leader.effort_weeks)} weeks its comparables suggest."
            if len(ranked) > 1 else
            "Nothing else was rankable, so there is no second place for it to "
            "lose to — a new candidate with a derivable effort could displace it."
        ),
    )

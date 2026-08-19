"""Stages 4–8 — claims in, ranked findings out. Deterministic throughout.

    claims -> cluster -> adjudicate -> size -> score -> VERIFY -> rank

The verify step is not decoration and it is not last-minute polish. The Phase 0
spike proposed a finding, a human read it, and it was WRONG — the supporting
signals were all echoes of one meeting rather than a pattern over months.
Confident, well-sourced, and false. Only pulling the evidence in date order
killed it. So refutation runs inside the pipeline, before anything is rendered,
and a finding that cannot survive its own evidence is dropped with its reason
recorded rather than shipped with a caveat.

WHAT IS NOT HERE. No LLM call, anywhere. Clustering is by subject, sizing is by
population intersection, and every threshold is a named constant. That is what
lets `assert_impact_ignores_corroboration` mean something and what makes a run
reproducible — the differentiator against asking a general model the same
question.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional, Sequence

from app.crucible.lint import lint_claim
from app.crucible.scoring import score_confidence, score_impact
from app.crucible.types import (
    Adjudication,
    AssumedParam,
    Claim,
    Confidence,
    ConfidenceInputs,
    Finding,
    GoalCurrency,
    Impact,
    ImpactInputs,
)

logger = logging.getLogger(__name__)

#: A cluster below this many claims is not a finding, it is an anecdote.
MIN_CLAIMS_PER_FINDING = 2

#: Refutation: a "pattern over time" backed by evidence that all lands inside
#: this window is one conversation echoing, not a pattern. This is the exact
#: shape that fooled the Phase 0 spike.
ECHO_WINDOW = timedelta(days=10)

#: How many findings get full treatment. Twenty-five equally-weighted options
#: is not a decision aid.
DEFAULT_DEEP_CAP = 5


@dataclass(frozen=True)
class Rejection:
    """A candidate that did not survive, and why. Never silently dropped —
    the considered list is the credibility of the ranking."""
    label: str
    reason: str
    stopped_at: str
    claim_ids: tuple[str, ...]


@dataclass(frozen=True)
class PipelineResult:
    findings: tuple[Finding, ...]
    impacts: tuple[Impact, ...]
    confidences: tuple[Confidence, ...]
    rejected: tuple[Rejection, ...]
    stats: dict


def _cluster(claims: Sequence[Claim]) -> dict[str, list[Claim]]:
    """Group claims that are about the same thing.

    By `subject_cluster_id` when the graph gave us one, else by subject. NOT by
    wording: the spec's "deduplicate by mechanism, not by wording" exists
    because the Phase 0 corpus carried four labels for one theme, splitting its
    accounts four ways so each looked smaller than it was.
    """
    out: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        key = (c.subject_cluster_id or c.subject or c.type).strip().lower()
        out[key].append(c)
    return dict(out)


def _adjudicate(claims: Sequence[Claim]) -> Adjudication:
    """SPEC Stage 7, deterministic.

    An authoritative CONFLICT is a finding in its own right and outranks
    everything — two sources that may both speak to a question disagreeing
    means the model of the business is wrong somewhere, which is worth more
    than either claim. It is never averaged away.
    """
    authoritative = [c for c in claims if c.authoritative]
    if not authoritative:
        return "no_authoritative_source"
    directions = {c.direction for c in authoritative if c.direction != "neutral"}
    if len(directions) > 1:
        return "conflict"
    if len(authoritative) == 1:
        return "single_authoritative"      # full weight — the quiet-finding guard
    return "corroborated"


def _accounts(claims: Sequence[Claim]) -> tuple[str, ...]:
    seen: list[str] = []
    for c in claims:
        for name in c.population.segments.get("customer_side", ()):
            if name not in seen:
                seen.append(name)
    return tuple(seen)


def _refute(claims: Sequence[Claim], accounts: Sequence[str]) -> Optional[str]:
    """Try to kill the finding. Returns a reason if it dies.

    Modelled on what actually killed the spike's first framing: evidence that
    looks like a pattern because there is a lot of it, when all of it lands in
    one window and says one thing once.
    """
    dates = sorted(c.observed_at for c in claims)
    if len(claims) >= 4 and (dates[-1] - dates[0]) < ECHO_WINDOW:
        return (
            f"all {len(claims)} supporting claims land within "
            f"{(dates[-1] - dates[0]).days} days — this is one conversation "
            f"echoing through the corpus, not a pattern over time"
        )
    if len(accounts) <= 1 and len(claims) >= 4:
        return (
            "every supporting claim comes from a single account, so this is "
            "that account's situation rather than a pattern across the book"
        )
    if not any(c.authoritative for c in claims):
        return (
            "no source that may speak to this claim type reported it — every "
            "supporting claim is outside its source's authority"
        )
    return None


def build_findings(
    claims: Iterable[Claim],
    *,
    currency: GoalCurrency,
    now: datetime,
    goal_accounts: Optional[frozenset[str]] = None,
    solution_evidence_absent: bool = True,
    deep_cap: int = DEFAULT_DEEP_CAP,
) -> PipelineResult:
    """The whole deterministic middle of the engine.

    `solution_evidence_absent` DEFAULTS TRUE, and that is deliberate: until a
    lever library exists there is no outcome evidence for any tenant, and the
    combined confidence formula would band every finding `low` regardless of
    its evidence (see `MAX_SCORE_WITHOUT_LEVER_EVIDENCE`). Defaulting the other
    way would render a number that carries no information.
    """
    claims = list(claims)
    clusters = _cluster(claims)

    findings: list[Finding] = []
    impacts: list[Impact] = []
    confidences: list[Confidence] = []
    rejected: list[Rejection] = []

    for key, group in sorted(clusters.items()):
        ids = tuple(c.id for c in group)

        if len(group) < MIN_CLAIMS_PER_FINDING:
            rejected.append(Rejection(
                key, f"only {len(group)} supporting claim — an anecdote, not a "
                     f"finding", "clustering", ids))
            continue

        accounts = _accounts(group)
        if goal_accounts is not None:
            # THE POPULATION INTERSECTION DOES REAL WORK. Against a retention
            # goal, a finding about prospects scores zero however loud it is.
            accounts = tuple(a for a in accounts if a in goal_accounts)

        refutation = _refute(group, accounts)
        if refutation:
            rejected.append(Rejection(key, refutation, "verification", ids))
            continue

        statement = _statement(key, group, accounts)
        strongest = max(group, key=lambda c: c.strength_score)
        if not lint_claim(statement, strongest.strength).ok:
            # I5 is a hard error at the boundary; here it is a drop, because a
            # statement we cannot phrase honestly is not one to ship with a
            # caveat.
            rejected.append(Rejection(
                key, "could not be stated without asserting causation the "
                     "evidence does not support", "rendering", ids))
            continue

        assumed: tuple[AssumedParam, ...] = ()
        if accounts:
            assumed = (AssumedParam(
                name="value_per_account",
                value=None,
                basis="no revenue data connected; accounts weighted equally",
                plausible_range=(0.0, 1.0),
            ),)

        finding = Finding(
            id=f"f-{key[:60]}",
            statement=statement,
            claim_ids=ids,
            impact_inputs=ImpactInputs(
                currency=currency,
                # I3: no named account is NOT MEASURED, never zero.
                affected_population=float(len(accounts)) if accounts else None,
                movable_gap=1.0 if accounts else None,
                value_per_unit=None,
                assumed_params=assumed,
            ),
            confidence_inputs=ConfidenceInputs(
                strengths=tuple(c.strength for c in group),
                claim_types=tuple(c.type for c in group),
                observed_ats=tuple(c.observed_at for c in group),
                authoritative_count=sum(1 for c in group if c.authoritative),
                claim_count=len(group),
                independent_authoritative_source_types=len(
                    {c.source_id for c in group if c.authoritative}
                ),
                surfaced_by=("corpus",),
                solution_evidence_absent=solution_evidence_absent,
            ),
            adjudication=_adjudicate(group),
        )
        findings.append(finding)
        impacts.append(score_impact(finding))
        confidences.append(score_confidence(finding, now=now))

    order = _rank(findings, impacts, confidences, deep_cap=deep_cap)
    findings = [findings[i] for i in order]
    impacts = [impacts[i] for i in order]
    confidences = [confidences[i] for i in order]

    return PipelineResult(
        findings=tuple(findings), impacts=tuple(impacts),
        confidences=tuple(confidences), rejected=tuple(rejected),
        stats={
            "claims": len(claims), "clusters": len(clusters),
            "findings": len(findings), "rejected": len(rejected),
            "sizeable": sum(1 for i in impacts if i.value is not None),
        },
    )


def _rank(
    findings: Sequence[Finding],
    impacts: Sequence[Impact],
    confidences: Sequence[Confidence],
    *,
    deep_cap: int,
) -> list[int]:
    """Order by size, then by how sure — reading FROZEN scores (I10).

    An unsizeable finding sorts last but is never dropped and never treated as
    zero: "we could not size this" and "this is worth nothing" lead to opposite
    decisions. An authoritative conflict outranks everything, because two
    sources that may both speak disagreeing is worth more than either claim.
    """
    def key(i: int):
        conflict = findings[i].adjudication == "conflict"
        value = impacts[i].value
        return (
            0 if conflict else 1,
            -(value if value is not None else -1),
            -confidences[i].score,
        )

    return sorted(range(len(findings)), key=key)


def _statement(key: str, claims: Sequence[Claim], accounts: Sequence[str]) -> str:
    """Prose that survives the causal lint by construction.

    Says what was OBSERVED and in what population, and stops. No "because", no
    "drives" — those need causal evidence, and a corpus of tickets and calls
    does not have any.
    """
    n = len(claims)
    where = (
        f" across {len(accounts)} account{'s' if len(accounts) != 1 else ''}"
        if accounts else ""
    )
    kinds = sorted({c.type for c in claims})
    return (
        f"{n} claims{where} concern {key}"
        f" ({', '.join(kinds)})."
    )

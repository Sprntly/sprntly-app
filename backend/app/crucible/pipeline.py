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
from typing import Iterable, NamedTuple, Optional, Sequence

from app.crucible.cluster import UNGROUPABLE_PREFIX
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

#: Every reason a candidate can die, in the order the run applies them. The
#: running view renders this funnel, so the set is CLOSED and declared here
#: rather than inferred from whatever rejections a particular run happened to
#: produce — a funnel that silently omits a rule the run applied reads as "this
#: never happens" when the truth is "nothing counted it".
NARRATED_DROPS = (
    "ungroupable", "anecdote", "echo", "single_account", "no_authority",
    "uncausal",
)

#: A cluster below this many claims is not a finding, it is an anecdote.
MIN_CLAIMS_PER_FINDING = 2

#: Refutation: a "pattern over time" backed by evidence that all lands inside
#: this window is one conversation echoing, not a pattern. This is the exact
#: shape that fooled the Phase 0 spike.
ECHO_WINDOW = timedelta(days=10)

#: How many findings get full treatment. Twenty-five equally-weighted options
#: is not a decision aid.
DEFAULT_DEEP_CAP = 5

#: How many rejections are listed individually. A real tenant produced 1,577 —
#: too many to insert in one request and far too many to read. The remainder is
#: NOT dropped: it collapses into one row that says how many and why, because a
#: silently truncated considered list reads as "we looked at everything" when
#: it did not. The ones kept are the largest, since a rejection backed by nine
#: claims is the one a reader is most likely to want to reopen.
MAX_LISTED_REJECTIONS = 100


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
    #: How many leading findings get the full treatment. The rest are RETURNED
    #: — dropping them would be the silent truncation the ledger exists to
    #: prevent — but a reader given 168 equally-weighted options has been handed
    #: the corpus back, not a decision aid.
    deep_count: int = 0


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


class Refutation(NamedTuple):
    """Why a candidate died, in two registers.

    `code` is for COUNTING and `reason` is for READING, and they are separate
    fields because the funnel the running view renders has to say which rule
    killed how many — and deriving that by matching on the prose would break
    the first time someone improves a sentence. The codes are a closed set;
    `NARRATED_DROPS` names every one of them for the client.
    """
    code: str
    reason: str


def _refute(
    claims: Sequence[Claim],
    accounts: Sequence[str],
    *,
    dates_are_ingest_clock: bool = False,
) -> Optional[Refutation]:
    """Try to kill the finding. Returns a code and a reason if it dies.

    Modelled on what actually killed the spike's first framing: evidence that
    looks like a pattern because there is a lot of it, when all of it lands in
    one window and says one thing once.

    MONOTONIC IN THE EVIDENCE, which the first version was not. Both rules were
    gated on `len(claims) >= 4`, so three same-day claims from one account
    shipped as a finding and a fourth killed it — more evidence for the same
    thing made the verdict stricter, which is backwards, and it let through the
    exact shape the spike was fooled by at the sizes where it is most likely.
    A cluster is two or more claims by construction, so the gates are gone.

    `accounts` here is the RAW set, before the goal's population filter. The
    single-account rule is about how diverse the EVIDENCE is; narrowing to the
    accounts a goal cares about is a different question, asked later, and
    conflating them would refute every finding against a one-account goal.
    """
    dates = sorted(c.observed_at for c in claims)
    span = dates[-1] - dates[0]
    # WHEN THE DATES ARE THE INGEST CLOCK, THIS TEST MEANS NOTHING. A backfill
    # stamps thousands of signals within seconds of each other whatever the
    # underlying events' real dates were, so every cluster looks like one
    # conversation and the run returns nothing — with a reason that is stated
    # confidently and is false, which is worse than returning nothing plainly.
    # Measured on a real 2,777-signal tenant: 2,410 rows had valid_at equal to
    # created_at, across 16 distinct days. The rule is skipped and the caller
    # renders a coverage note saying so; disclosing the blind spot beats
    # exercising a check that cannot see.
    # ONE CONVERSATION MEANS ONE ARTIFACT. Keying the rule on the clock alone
    # refuted two claims from two different accounts, arriving through two
    # different connectors three days apart, as "one conversation echoing" —
    # which is simply false, and the count-based gate it replaced was
    # non-monotonic. Distinct source artifacts is the thing the rule was always
    # reaching for, and it is monotonic: adding evidence from a NEW artifact
    # can only ever make a finding safer.
    # `<= 1` would read "no artifact recorded at all" as "one conversation",
    # which is the difference between a test that fires and a test that is
    # simply always true. Unknown provenance means the rule CANNOT run, and a
    # check that cannot run must not return a verdict.
    # EVERY claim must name its document, not just one of them. Declining only
    # when ALL are unattributed still refuted "one known doc plus two unknowns"
    # as a single conversation — and mixed corpora are the normal case, since
    # `business_context_projection` and `ds/analyses` write provenance with no
    # `doc` key at all. If we cannot see where a claim came from, we cannot say
    # it came from the same place as the others.
    sources = {c.artifact_id for c in claims if c.artifact_id}
    fully_attributed = len(sources) > 0 and all(c.artifact_id for c in claims)
    one_conversation = fully_attributed and len(sources) == 1
    if span < ECHO_WINDOW and one_conversation and not dates_are_ingest_clock:
        return Refutation("echo", (
            f"all {len(claims)} supporting claims come from one source "
            f"document within {span.days} days — this is one conversation "
            f"echoing through the corpus, not a pattern over time"
        ))
    # Exactly one, not "at most one": ZERO named accounts is unsizeable, which
    # is a finding we keep and mark (I3), not one we drop.
    if len(accounts) == 1:
        return Refutation("single_account", (
            "every supporting claim comes from a single account, so this is "
            "that account's situation rather than a pattern across the book"
        ))
    if not any(c.authoritative for c in claims):
        return Refutation("no_authority", (
            "no source that may speak to this claim type reported it — every "
            "supporting claim is outside its source's authority"
        ))
    return None


def build_findings(
    claims: Iterable[Claim],
    *,
    currency: GoalCurrency,
    now: datetime,
    goal_accounts: Optional[frozenset[str]] = None,
    solution_evidence_absent: bool = True,
    dates_are_ingest_clock: bool = False,
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
    ungroupable: list[str] = []
    # COUNTED HERE, not derived from `rejected` afterwards. `rejected` is the
    # LISTED set: over `MAX_LISTED_REJECTIONS` it collapses into one summary
    # row, so counting it would report 100 anecdotes on a run that dropped
    # 1,576. The funnel has to be the truth, not the excerpt.
    # `defaultdict`, PRE-SEEDED. Pre-seeded so every rule is present at zero
    # (the panel distinguishes "dropped nothing" from "did not run"); a
    # defaultdict so a rule added to `_refute` without its constant cannot
    # raise KeyError here — that escapes into `execute_run`'s catch-all and
    # fails the run for every tenant. Narration must never outrank the answer.
    drops: defaultdict[str, int] = defaultdict(int)
    for _code in NARRATED_DROPS:
        drops[_code] = 0

    for key, group in sorted(clusters.items()):
        ids = tuple(c.id for c in group)

        if key.startswith(UNGROUPABLE_PREFIX):
            # OUR failure, not the evidence's. Calling these anecdotes would
            # blame the claims for a vector we could not compute.
            #
            # COLLECTED, not one row each. A tenant with no embeddings produces
            # one of these per signal — 2,777 on a real one — and writing that
            # many identical rows into the ledger buries every genuine
            # rejection under it and makes the considered list unreadable.
            ungroupable.extend(ids)
            drops["ungroupable"] += len(ids)
            continue

        if len(group) < MIN_CLAIMS_PER_FINDING:
            drops["anecdote"] += 1
            rejected.append(Rejection(
                _label(group, key),
                f"only {len(group)} supporting claim — an anecdote, not a "
                     f"finding", "clustering", ids))
            continue

        raw_accounts = _accounts(group)
        accounts = raw_accounts
        if goal_accounts is not None:
            # THE POPULATION INTERSECTION DOES REAL WORK. Against a retention
            # goal, a finding about prospects scores zero however loud it is.
            accounts = tuple(a for a in raw_accounts if a in goal_accounts)

        # Refute on the RAW set — see `_refute`. Size on the scoped one.
        refutation = _refute(group, raw_accounts,
                             dates_are_ingest_clock=dates_are_ingest_clock)
        if refutation:
            drops[refutation.code] += 1
            rejected.append(Rejection(
                _label(group, key), refutation.reason, "verification", ids))
            continue

        # The KEY groups; the LABEL reads. They are different strings on
        # purpose: grouping wants a stable opaque id (`c490`), and a reader
        # wants the theme in the corpus's own words. Rendering the key is how
        # the first version put "c490" in front of a user.
        label = _label(group, key)
        statement = _statement(label, group, accounts)
        strongest = max(group, key=lambda c: c.strength_score)
        if not lint_claim(statement, strongest.strength).ok:
            drops["uncausal"] += 1
            # I5 is a hard error at the boundary; here it is a drop, because a
            # statement we cannot phrase honestly is not one to ship with a
            # caveat.
            rejected.append(Rejection(
                _label(group, key),
                "could not be stated without asserting causation the "
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
            id=f"f-{key}",
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
                # THE DOCUMENTS IT ACTUALLY CAME FROM, not the word "corpus".
                # This field is the only provenance the panel renders, and it
                # rendered the literal string "corpus" on every finding — so
                # "8 claims concern X" was unfalsifiable from the screen. The
                # source documents are already on the claims.
                surfaced_by=_sources_of(group),
                solution_evidence_absent=solution_evidence_absent,
            ),
            adjudication=_adjudicate(group),
        )
        findings.append(finding)
        impacts.append(score_impact(finding))
        confidences.append(score_confidence(finding, now=now))

    if len(rejected) > MAX_LISTED_REJECTIONS:
        rejected.sort(key=lambda r: (-len(r.claim_ids), r.label))
        overflow = rejected[MAX_LISTED_REJECTIONS:]
        rejected = rejected[:MAX_LISTED_REJECTIONS]
        rejected.append(Rejection(
            f"{len(overflow)} further candidates",
            f"{len(overflow)} more groups were considered and dropped, each "
            f"backed by fewer claims than the {MAX_LISTED_REJECTIONS} listed "
            f"above — most of them single-claim anecdotes",
            "clustering",
            tuple(cid for r in overflow for cid in r.claim_ids)[:2000],
        ))

    if ungroupable:
        rejected.append(Rejection(
            f"{len(ungroupable)} ungroupable signals",
            f"{len(ungroupable)} signals have no usable embedding, so whether "
            f"they corroborate anything is unknown rather than false — they "
            f"were read but could not be grouped",
            "clustering", tuple(ungroupable)))

    order = _rank(findings, impacts, confidences, deep_cap=deep_cap)
    findings = [findings[i] for i in order]
    impacts = [impacts[i] for i in order]
    confidences = [confidences[i] for i in order]

    return PipelineResult(
        findings=tuple(findings), impacts=tuple(impacts),
        confidences=tuple(confidences), rejected=tuple(rejected),
        deep_count=min(deep_cap, len(findings)),
        stats={
            "claims": len(claims), "clusters": len(clusters),
            "findings": len(findings), "rejected": len(rejected),
            "sizeable": sum(1 for i in impacts if i.value is not None),
            "conflicts": sum(1 for f in findings if f.adjudication == "conflict"),
            # PER REASON, and every key present even at zero — the running view
            # distinguishes "this rule dropped nothing" from "this rule did not
            # run", and a missing key cannot carry that difference.
            "dropped": dict(drops),
            "echo_check_skipped": bool(dates_are_ingest_clock),
            "claims_without_artifact": sum(1 for c in claims if not c.artifact_id),
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


#: How many source documents to name before summarising. Enough to show the
#: evidence is spread, short enough to read in a list.
MAX_NAMED_SOURCES = 4


def _sources_of(claims: Sequence[Claim]) -> tuple[str, ...]:
    """Which documents this finding rests on, most-cited first.

    Deterministic: ties break on the document name, so a re-run names the same
    sources in the same order.
    """
    counts: dict[str, int] = {}
    for c in claims:
        doc = (c.artifact_id or "").strip()
        if doc:
            counts[doc] = counts.get(doc, 0) + 1
    if not counts:
        return ()
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    named = tuple(f"{doc} ({n})" for doc, n in ranked[:MAX_NAMED_SOURCES])
    if len(ranked) > MAX_NAMED_SOURCES:
        named += (f"+{len(ranked) - MAX_NAMED_SOURCES} more documents",)
    return named


def _label(claims: Sequence[Claim], key: str) -> str:
    """What a reader should see this group called.

    The MOST COMMON subject in the group, not the first claim's: the cluster
    leader is simply whichever claim happened to appear first in id order, and
    naming a theme after an arbitrary member is how a group of nine about
    billing ends up titled with the one sentence about a calendar invite. Ties
    break toward the subject seen earliest, so the label is stable across runs.
    """
    counts: dict[str, int] = {}
    order: dict[str, int] = {}
    for i, c in enumerate(claims):
        subject = (c.subject or "").strip()
        if not subject:
            continue
        counts[subject] = counts.get(subject, 0) + 1
        order.setdefault(subject, i)
    if not counts:
        return key
    return max(counts, key=lambda k: (counts[k], -order[k]))


def _statement(label: str, claims: Sequence[Claim], accounts: Sequence[str]) -> str:
    """Prose that survives the causal lint by construction.

    Says what was OBSERVED and in what population, and stops. No "because", no
    "drives" — those need causal evidence, and a corpus of tickets and calls
    does not have any.

    The topic is QUOTED. It comes from a signal's own words, so presenting it
    unquoted would read as our description of the business; quoted, it is
    plainly reported speech, which is what it is. `cluster.label_for` has
    already cut it at the first causal connective, so a source's own "because"
    cannot arrive here and be attributed to us.
    """
    n = len(claims)
    where = (
        f" across {len(accounts)} account{'s' if len(accounts) != 1 else ''}"
        if accounts else ""
    )
    topic = (label or "").strip() or "an unlabelled group"
    return f"{n} claims{where} concern \u201c{topic}\u201d."

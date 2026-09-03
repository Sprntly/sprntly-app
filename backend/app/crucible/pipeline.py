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
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, NamedTuple, Optional, Sequence

from app.crucible.cluster import UNGROUPABLE_PREFIX, example_for, label_for
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

#: EXCEPT FOR CLAIM TYPES WHERE ONE MENTION IS THE WHOLE POINT.
#:
#: The corroboration rule is right for THEMES — "customers keep asking for X"
#: means nothing on one mention. It is wrong for CONSTRAINTS. A deal blocker is
#: specific to one deal by definition, so it is mentioned once by definition,
#: and requiring a second independent mention deletes exactly the items a PM
#: most needs.
#:
#: Measured on a real staging corpus of 160 blocker signals: 85 of 101
#: rejections were `anecdote`, and among them
#:   "$217,988 in expansion revenue was described as gated for the week of…"
#:   "336K USD in renewals is at risk as of the Week of Aug 3 brief"
#:   "1 named account is gating a deal, and 3 missing roles were identified…"
#: A single-source, single-account, named-figure blocker is not an anecdote. It
#: is the most actionable line in the corpus.
#:
#: The claim keeps everything else that makes it honest: it is still capped at
#: `reported` strength, still sized only if an account is named (I3), and its
#: confidence still falls with the thinner evidence — a reader sees ONE claim
#: beside it and can weigh that. What changes is that they get to see it.
CORROBORATION_EXEMPT_TYPES = frozenset({"constraint"})

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

#: The two ledger rows that are BOOKKEEPING rather than candidates: the
#: "N further candidates" summary the list overflows into, and the one standing
#: for every signal that had no usable embedding. Both used to claim
#: `stopped_at_stage="clustering"`, so every renderer counted them as
#: rejections in their own right — a run that considered 1,576 candidates
#: reported "Considered and ruled out (102)" directly under a promise that
#: everything considered was listed. They also formed their own "reason"
#: groups, inflating a one-reason ledger to three.
OVERFLOW_STAGE = "overflow"
UNGROUPED_STAGE = "ungrouped"
AGGREGATE_STAGES = frozenset({OVERFLOW_STAGE, UNGROUPED_STAGE})


def _grounded_commercial_native_units(group: Sequence[Claim]) -> dict[str, float]:
    """Real, transcript-stated dollar figures among THIS finding's claims —
    additive evidence carried alongside Impact on `native_units`, never
    folded into `value` (never `affected_population`, `movable_gap` or
    `value_per_unit`, all left exactly as `_refute`/the caller already set
    them). Reporting the sum as if it applied to every account in the
    cluster would be exactly the extrapolation forbidden alongside this: an
    account that never stated a figure is not assumed to be worth the mean
    of the ones that did. So this only ever answers "customers named $X
    across N accounts" — the accounts that actually named one, nothing
    wider — for the report to render distinctly from any projection.

    Currency-conservative: only claims with no stated currency or an
    explicit "USD" are summed. A claim naming a different currency is
    counted (`commercial_grounded_claims`) but excluded from the dollar sum
    rather than risk silently mixing currencies into one number.
    """
    grounded = [c for c in group if c.magnitude is not None]
    if not grounded:
        return {}
    usd_amounts = [
        c.magnitude for c in grounded
        if (c.raw or {}).get("currency") in (None, "", "USD")
    ]
    accounts_named: set[str] = set()
    for c in grounded:
        accounts_named.update(c.population.segments.get("accounts", ()))
    units: dict[str, float] = {"commercial_grounded_claims": float(len(grounded))}
    if usd_amounts:
        units["commercial_grounded_usd"] = float(sum(usd_amounts))
    if accounts_named:
        units["commercial_grounded_accounts"] = float(len(accounts_named))
    return units


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
    A cluster USED TO BE two or more claims by construction, which is why the
    gates went. That is no longer true: `CORROBORATION_EXEMPT_TYPES` lets a
    single constraint through, so the echo rule now states its own precondition
    rather than borrowing one from the caller. A group of one cannot be "one
    conversation echoing" — there is nothing repeating. It fired anyway, and
    the reason it printed read "all 1 supporting claims come from one source
    document within 0 days", which is not a sentence about evidence.

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
    if (len(claims) >= MIN_CLAIMS_PER_FINDING
            and span < ECHO_WINDOW and one_conversation
            and not dates_are_ingest_clock):
        return Refutation("echo", (
            f"all {len(claims)} supporting claims come from one source "
            f"document within {span.days} days — this is one conversation "
            f"echoing through the corpus, not a pattern over time"
        ))
    # Exactly one, not "at most one": ZERO named accounts is unsizeable, which
    # is a finding we keep and mark (I3), not one we drop.
    # THE SAME CATEGORY ERROR, ONE RULE OVER. "This is that account's situation
    # rather than a pattern across the book" is right about a PREFERENCE — one
    # account wanting a feature is that account's opinion. It is wrong about a
    # CONSTRAINT, where being about one account is the entire content:
    # "Northwind has only a $5,000 budget approved for a POC" is not a failed
    # pattern, it is the finding. Exempting the anecdote rule without this one
    # left 34 named-account blockers still dropped on the measured corpus, so
    # the change would have looked landed and delivered nothing.
    exempt_single = all(c.type in CORROBORATION_EXEMPT_TYPES for c in claims)
    if len(accounts) == 1 and not exempt_single:
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
    #: Ungroupable CLUSTERS, as distinct from the ungroupable CLAIMS in
    #: `drops`. The theme count is `clusters - this`.
    ungroupable_groups = 0

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
            # COUNTED SEPARATELY FROM THE CLAIMS, because the theme count is
            # derived by subtracting it and the two are only equal by an
            # assumption: `_cluster` lowercases its key, so two claim ids
            # differing only in case would share one ungroupable cluster and
            # the subtraction would quietly under-report. Counting the groups
            # themselves removes the assumption instead of relying on it.
            ungroupable_groups += 1
            continue

        # ALL of them, not any: a mixed group still contains claim types that
        # do need corroboration, and one exempt member must not carry them.
        exempt = all(c.type in CORROBORATION_EXEMPT_TYPES for c in group)
        if len(group) < MIN_CLAIMS_PER_FINDING and not exempt:
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
        statement, example = _statement_parts(label, group, accounts)
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
            label=label,
            example=example,
            claim_ids=ids,
            impact_inputs=ImpactInputs(
                currency=currency,
                # I3: no named account is NOT MEASURED, never zero.
                affected_population=float(len(accounts)) if accounts else None,
                movable_gap=1.0 if accounts else None,
                value_per_unit=None,
                assumed_params=assumed,
                native_units=_grounded_commercial_native_units(group),
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
            # NOT "clustering". This row is BOOKKEEPING, not a candidate: it
            # stands for everything the list could not hold. Renderers counted
            # it as one more rejection, so a run that considered 1,576
            # candidates reported "102" while promising it had listed them all.
            # A distinct stage is how they can tell — no schema change, and the
            # column is already free text.
            OVERFLOW_STAGE,
            tuple(cid for r in overflow for cid in r.claim_ids)[:2000],
        ))

    if ungroupable:
        rejected.append(Rejection(
            f"{len(ungroupable)} ungroupable signals",
            f"{len(ungroupable)} signals have no usable embedding, so whether "
            f"they corroborate anything is unknown rather than false — they "
            f"were read but could not be grouped",
            UNGROUPED_STAGE, tuple(ungroupable)))

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
            # `clusters` counts one pseudo-group per ungroupable claim, so a
            # reader's "themes" is `clusters - ungroupable_groups`. Published
            # rather than derived at the call site, so the subtraction cannot
            # drift from how the clustering actually keyed.
            "ungroupable_groups": ungroupable_groups,
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


#: `kg_ingest.runner.sync_provider`'s doc_name shape: `<provider>-sync-batch-<n>`.
#:
#: THE NUMBER IS AN INGEST CHUNK, NOT A DOCUMENT. A connector sync slices its
#: pull into arbitrary batches and stamps each with its index, so a finding read
#: back as
#:
#:   fireflies-sync-batch-0 (9) · fireflies-sync-batch-4 (7) ·
#:   fireflies-sync-batch-10 (4) · fireflies-sync-batch-5 (3) · +1 more documents
#:
#: looks like evidence spread across five documents and is one source chopped
#: five ways. That is worse than unhelpful: breadth across documents is exactly
#: what a reader uses to judge whether a finding is well-supported, and this
#: inflates it.
#:
#: There is no call title to put here instead, and that is a property of the
#: data rather than of this function: `call_digest` records that KG extraction
#: is per-BATCH, so `extract_document` stamps only `{"doc": <batch name>}` and
#: an extracted signal carries no call id to resolve. The provider is the ONLY
#: real attribution in the string, so the provider is what gets rendered.
_SYNC_BATCH = re.compile(r"^([a-z0-9_]+)-sync-batch-\d+$")

#: What to call a provider's batches once they are collapsed. Anything not
#: listed falls back to its own name, title-cased — a new connector reads
#: acceptably without a code change.
_PROVIDER_LABELS = {
    "fireflies": "Fireflies call transcripts",
    "zoom": "Zoom call transcripts",
    "slack": "Slack",
    "gong": "Gong call transcripts",
}


def _document_label(doc: str) -> str:
    """The name to show for one source document.

    Real document names — `slack/#mvp-product (part 2/3)`, a Drive filename —
    pass through untouched. Only the sync-batch shape is rewritten, and only
    because its number identifies nothing a reader could look up.
    """
    m = _SYNC_BATCH.match(doc)
    if not m:
        return doc
    provider = m.group(1)
    return _PROVIDER_LABELS.get(provider, provider.replace("_", " ").title())


def _sources_of(claims: Sequence[Claim]) -> tuple[str, ...]:
    """Which documents this finding rests on, most-cited first.

    Deterministic: ties break on the document name, so a re-run names the same
    sources in the same order.
    """
    counts: dict[str, int] = {}
    for c in claims:
        doc = (c.artifact_id or "").strip()
        if doc:
            # COLLAPSED BEFORE COUNTING, so the count is per SOURCE rather than
            # per ingest chunk: five batches of one provider become one entry
            # carrying all five counts, which is the true number of claims that
            # source contributed.
            label = _document_label(doc)
            counts[label] = counts.get(label, 0) + 1
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
    """The sentence alone. See `_statement_parts` for why there are two."""
    return _statement_parts(label, claims, accounts)[0]


def _statement_parts(
    label: str, claims: Sequence[Claim], accounts: Sequence[str],
) -> tuple[str, str]:
    """The statement, AND the example quote it used — or "" when it used none.

    Two returns rather than one because the renderer needs the example on its
    own. A finding renders as a heading (the theme), a row of chips (the counts)
    and the quote; the sentence form puts all three in one clause, which reads
    well and scans terribly.

    THE EXAMPLE COMES BACK ONLY IF THE WHOLE CANDIDATE PASSED THE LINT. That is
    why this is one function and not two: the lint runs on the assembled
    sentence, so an example that is fine alone and causal in context must not
    escape. Recomputing it beside `_statement` would drift the moment either
    side changed.
    """
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
    # "1 claims concern" has been in every report this engine has ever
    # produced. A count that cannot get its own plural right is the first thing
    # a reader stops trusting, and everything after it is numbers.
    # The VERB agrees too. Fixing only the noun produced "1 claim concern",
    # which is the same defect one word to the right.
    claims_word = "claim" if n == 1 else "claims"
    concern = "concerns" if n == 1 else "concern"
    plain = f"{n} {claims_word}{where} {concern} \u201c{topic}\u201d."

    # AND ONE OF THEM, IN THE SOURCE'S OWN WORDS.
    #
    # "4 claims concern 'Mobile editor keystroke loss'" is a table-of-contents
    # entry, not a finding: it names a topic and says how many times it came
    # up. A reader cannot judge it, argue with it, or take it to anyone —
    # which is the whole job. Read against the same corpus, the chat surface
    # answers with account counts and quotes; the report answered with a label.
    #
    # SO: quote the strongest claim beside the count. Reported speech, exactly
    # like the topic — this asserts nothing we have not been told, and adds no
    # causation, because the example goes through `label_for`, the same cut at
    # the first causal connective the label already gets.
    #
    # AND IT CAN ONLY EVER ADD. The example is linted before it is used, and a
    # failure falls back to `plain` rather than dropping the finding — the
    # caller treats an unlintable statement as a DROP, so a clumsy example
    # would have silently deleted findings, which is far worse than a dull one.
    strongest = max(claims, key=lambda c: c.strength_score, default=None)
    said = (getattr(strongest, "assertion", "") or "").strip()
    # `label_for("")` returns the literal string "unlabelled", so an empty
    # assertion rendered `for example, "unlabelled"` — a quotation mark around
    # a word no source ever said, which is worse than no example at all.
    # `example_for`, not `label_for`: same causal cut, its own length budget,
    # and it ends where a reader can tell it ended.
    example = example_for(said) if said else ""
    if (
        strongest is not None
        and example
        and example.lower() != topic.lower()
        # A quote that only repeats the label teaches nothing and costs a line.
        and example.lower() not in topic.lower()
    ):
        candidate = (
            f"{n} {claims_word}{where} {concern} \u201c{topic}\u201d "
            f"\u2014 for example, \u201c{example}\u201d."
        )
        if lint_claim(candidate, strongest.strength).ok:
            return candidate, example
    return plain, ""

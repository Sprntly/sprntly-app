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

import hashlib
import logging
import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Iterable, NamedTuple, Optional, Sequence

from app.crucible.cluster import UNGROUPABLE_PREFIX, example_for, label_for
from app.crucible.lint import lint_claim
from app.crucible.scoring import score_confidence, score_impact
from app.crucible.types import (
    SIZE_BANDS,  # noqa: F401  (re-exported: callers read the band vocabulary here)
    Adjudication,
    AssumedParam,
    Claim,
    Confidence,
    ConfidenceInputs,
    Finding,
    GoalCurrency,
    GroundedFigure,
    Impact,
    ImpactInputs,
    _band_for_rank,
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


#: The `native_units` key carrying a finding's deduplicated, transcript-stated
#: dollar sum. Named once so the code that WRITES the sum and the code that
#: BANDS on it can never read different keys — a silent typo there would mean
#: no dollar finding ever bands, and nothing would fail.
#: The SUMMABLE money: issued quotes, contract values, named deals. This is
#: the only figure a money target may be answered from.
COMMITTED_USD_UNIT = "commercial_committed_usd"
#: How much of that came off a paraphrase rather than a verified quote.
COMMITTED_USD_DERIVED_UNIT = "commercial_committed_usd_derived"

#: LIST PRICING IS A RANGE, NEVER A SUM, and it is reported in parts that
#: cannot be added back into one: the two ends, how many distinct prices
#: there were, and how many accounts heard them. There is deliberately no
#: total here — a rate card quoted to sixteen accounts has no total.
LIST_PRICE_MIN_UNIT = "commercial_list_price_min"
LIST_PRICE_MAX_UNIT = "commercial_list_price_max"
LIST_PRICE_DISTINCT_UNIT = "commercial_list_price_distinct"
LIST_PRICE_ACCOUNTS_UNIT = "commercial_list_price_accounts"

#: How many DISTINCT accounts must be quoted the identical figure before it
#: is treated as a rate card rather than a coincidence.
#:
#: MEASURED, AND ITS LIMITS MEASURED TOO. On a 61-row stratified sample the
#: dominant list price appeared across 16 distinct accounts, so any
#: threshold from 2 to 4 catches it identically. Three is chosen because two
#: accounts agreeing on a round number is a plausible coincidence between
#: negotiated deals and three is not.
#:
#: WHAT THIS SIGNAL CANNOT DO. In that same sample it fires on ONE of ~13
#: distinct list prices — the other twelve were each quoted once, where
#: repetition says nothing at all. So modality is an OVERRIDE that catches a
#: rate card wearing the wrong kind; it is not the classifier. See
#: `_figure_is_committed`.
LIST_PRICE_MIN_ACCOUNTS = 3

#: Phrases that mark a figure as money someone has actually committed to.
#: Taken from the genuine rows in the sample: "A $9,000 quote was issued",
#: "contract value was updated to $2,000", "deals nearing closure with two
#: accounts, together valued at $165k".
_COMMITTED_PHRASE = re.compile(
    r"\bquote\b|\bquoted\b|\bcontract\s+value\b|\bvalued\s+at\b"
    r"|\bdeal\b|\bclosed\b|\bsigned\b|\brenewed\b",
    re.IGNORECASE,
)

#: Phrases that mark a figure as a rate card entry.
_LIST_PRICE_PHRASE = re.compile(
    r"\bstarts\s+at\b|\bstarting\s+at\b|\bannual\s+subscription\b"
    r"|\bper\s+hour\b|\bper\s+seat\b|\bper\s+user\b"
    r"|\bone[-\s]time\s+fee\b|\blist\s+price\b|\bprice\s+list\b",
    re.IGNORECASE,
)

#: The extractor kind that means "a price", as opposed to "a term of a
#: deal". This is the ROUTER, because it is the only signal with an opinion
#: about every row: the extraction pass already judged what the signal is.
_LIST_PRICE_KIND = "pricing"


def repeated_amounts(claims: Sequence[Claim]) -> frozenset[float]:
    """Amounts quoted to `LIST_PRICE_MIN_ACCOUNTS` or more DISTINCT accounts
    anywhere in the corpus — a rate card, not a set of coincidences.

    CORPUS-WIDE ON PURPOSE, not per finding. The sixteen mentions of one
    price were sixteen separate sales calls and land in whatever clusters
    their subjects put them; counting only within a finding would miss the
    pattern precisely when it is most pronounced.
    """
    accounts_by_amount: defaultdict[float, set[str]] = defaultdict(set)
    for c in claims:
        if c.magnitude is None:
            continue
        for account in c.population.segments.get("accounts", ()) or ("",):
            accounts_by_amount[float(c.magnitude)].add(account)
    return frozenset(
        amount for amount, accounts in accounts_by_amount.items()
        if len({a for a in accounts if a}) >= LIST_PRICE_MIN_ACCOUNTS
    )


def _figure_is_committed(
    claim: Claim, amount: float, list_price_amounts: frozenset[float],
) -> bool:
    """Is this money someone has agreed to, or a price on a rate card?

    THREE SIGNALS, IN THE ORDER THE EVIDENCE SUPPORTS.

    1. MODALITY OVERRIDES EVERYTHING, because a figure quoted to three or
       more distinct accounts is a rate card whatever it is labelled. This
       is the one signal that generalises to a tenant whose phrasing we have
       never seen — but it was measured firing on only one of ~13 distinct
       list prices in the sample, so it cannot be asked to do the job alone.
    2. PHRASES, where the text says outright which it is.
    3. KIND, WHICH MAY ONLY EXCLUDE. The extraction pass already judged
       whether the signal is a `pricing` fact or a `commercial_term`, and it
       is the only one of the three with an opinion about every row — but a
       `commercial_term` label is a weak signal, and it is attached to the
       lower-precision of the two populations.

    A POSITIVE SIGNAL IS REQUIRED, and this function used to end
    `return claim.artifact_type != _LIST_PRICE_KIND`, which did the exact
    opposite of the paragraph below it: a `commercial_term` row matching
    NEITHER phrase is a tie, and that line resolved the tie by admitting it.
    It routed the whole weaker population into the summed total on the
    strength of a label. A live spot-check found 2 of 11 rows in the
    committed head were real deals, and both of the real ones had matched a
    phrase anyway — so requiring the phrase removed nine wrong rows and cost
    nothing.

    Ties go to NOT committed. Every failure in this feature's history has
    been over-claiming, and a figure wrongly left out of the sum understates
    a total, where one wrongly added invents money.
    """
    if amount in list_price_amounts:
        return False
    if claim.artifact_type == _LIST_PRICE_KIND:
        return False
    text = claim.assertion or ""
    if _LIST_PRICE_PHRASE.search(text):
        return False
    return bool(_COMMITTED_PHRASE.search(text))

#: The `certainty` marker a figure recovered from a written summary carries
#: (`app.crucible.backfill.BACKFILL_CERTAINTY`). Declared here rather than
#: imported because `backfill` is an operator tool that imports the graph
#: extractor and a DB client; the read path must not pull that in to answer a
#: question about one string. The value is pinned by a test on both sides.
BACKFILL_CERTAINTY_MARKER = "derived-from-summary"


def _account_key(accounts: Sequence[str]) -> str:
    """A stable, opaque identity for the account(s) a figure belongs to.

    STABLE, so two runs over the same corpus deduplicate identically —
    Python's own `hash()` is salted per process and would make the output
    depend on which process produced it, which is the reproducibility claim
    this engine makes against asking a general model the same question.

    OPAQUE, because this value rides on scored objects that get logged and
    diffed. Deduplication needs to know two figures belong to the same
    customer; nothing downstream needs to know which customer, and a digest
    grants the first while refusing the second.
    """
    if not accounts:
        return ""
    joined = "\x1f".join(sorted(accounts))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def deduped_grounded_figures(
    group: Sequence[Claim],
    list_price_amounts: frozenset[float] = frozenset(),
) -> tuple[GroundedFigure, ...]:
    """The DISTINCT transcript-stated dollar figures among this finding's
    claims — the single source of truth for the grounded sum, which is simply
    these added up.

    DO NOT "SIMPLIFY" THIS INTO `sum(c.magnitude for c in group)`. One deal
    restated in five messages is five claims carrying one figure; adding them
    gives five times the money, which turns HOW OFTEN something was said into
    HOW BIG it is — corroboration deciding size, the single failure the
    separation of impact from confidence exists to prevent. It was merely a
    wrong display line while the sum was display-only. It is now also the
    number that orders findings and the number summed toward a reader's
    stated money target, so the same defect would inflate a target by
    whatever the repetition rate happened to be.

    The identity of a figure is `(the accounts it is attached to, the
    amount)`:

      * named account — the same amount on the same account is one figure,
        however many claims restate it. Two accounts naming the same amount
        stay two figures.
      * no named account — the amount alone is the identity, which collapses
        an anonymous restatement of one deal.
      * an anonymous amount already attributed to some account is dropped
        entirely. Most often that is the same deal in a message that did not
        name the customer, and between double-counting a real figure and
        under-counting a duplicate, only one of those errors inflates.

    Currency needs no place in the key: a claim in another currency is
    excluded outright, so everything reaching deduplication is one currency
    by construction.

    The cost is real and worth naming: one account that genuinely signs two
    separate contracts for the identical amount is counted once. That is an
    undercount of an unknowable, and this sum's job is to be a floor a reader
    can trust, not a maximum.
    """
    attributed: dict[tuple[str, float], GroundedFigure] = {}
    anonymous: dict[float, GroundedFigure] = {}
    for c in group:
        if c.magnitude is None:
            continue
        raw = c.raw or {}
        if raw.get("currency") not in (None, "", "USD"):
            continue
        accounts = tuple(sorted(c.population.segments.get("accounts", ())))
        key = _account_key(accounts)
        derived = raw.get("certainty") == BACKFILL_CERTAINTY_MARKER
        figure = GroundedFigure(
            account_key=key, amount=float(c.magnitude), derived=derived,
            committed=_figure_is_committed(
                c, float(c.magnitude), list_price_amounts,
            ),
        )
        if accounts:
            existing = attributed.get((key, figure.amount))
            # A figure seen both ways keeps the STRONGER provenance: if any
            # claim carried it against a verified quote, the money is quoted
            # money, and hedging it as derived would understate what we know.
            if existing is None or (existing.derived and not derived):
                attributed[(key, figure.amount)] = figure
            elif existing.committed and not figure.committed:
                # Same money seen both ways: the rate-card reading wins, for
                # the same reason ties do. Calling it committed would put it
                # in a sum on the strength of the weaker evidence.
                attributed[(key, figure.amount)] = existing._replace(
                    committed=False,
                )
        else:
            existing = anonymous.get(figure.amount)
            if existing is None or (existing.derived and not derived):
                anonymous[figure.amount] = figure
            elif existing.committed and not figure.committed:
                anonymous[figure.amount] = existing._replace(committed=False)

    attributed_amounts = {amount for _key, amount in attributed}
    out = list(attributed.values()) + [
        f for amount, f in anonymous.items() if amount not in attributed_amounts
    ]
    # Sorted so the tuple is stable regardless of claim iteration order —
    # these end up on a frozen, hashed, repr-compared object.
    return tuple(sorted(out, key=lambda f: (-f.amount, f.account_key)))


def _grounded_commercial_native_units(
    group: Sequence[Claim],
    list_price_amounts: frozenset[float] = frozenset(),
) -> dict[str, float]:
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

    DEDUPLICATED BEFORE SUMMING, AND THAT IS THE WHOLE POINT OF THE SUM —
    the rule and the reasoning live on `deduped_grounded_figures`, which is
    the single source of truth. This sum is literally those figures added up,
    so the two can never disagree about what the money is.
    """
    grounded = [c for c in group if c.magnitude is not None]
    if not grounded:
        return {}

    accounts_named: set[str] = set()
    for c in grounded:
        accounts_named.update(c.population.segments.get("accounts", ()))

    figures = deduped_grounded_figures(group, list_price_amounts)

    # STILL THE RAW CLAIM COUNT, deliberately. This is a statement about the
    # evidence ("N claims carried a figure"), not about the money, and it is
    # the one number here a reader should be able to reconcile against the
    # claim list. It is also why nothing downstream may size a finding from
    # it: it counts agreement, and agreement is confidence's business.
    units: dict[str, float] = {"commercial_grounded_claims": float(len(grounded))}
    list_prices = [f for f in figures if not f.committed]
    if list_prices:
        # A RANGE AND ITS SHAPE, WITH NO TOTAL ANYWHERE. The parts are chosen
        # so they cannot be recombined into a sum: two ends, a count of
        # distinct prices, and a count of accounts. Multiplying any of them
        # together would be meaningless and looks it, which is the point.
        amounts = sorted({f.amount for f in list_prices})
        units[LIST_PRICE_MIN_UNIT] = amounts[0]
        units[LIST_PRICE_MAX_UNIT] = amounts[-1]
        units[LIST_PRICE_DISTINCT_UNIT] = float(len(amounts))
        accounts = {f.account_key for f in list_prices if f.account_key}
        if accounts:
            units[LIST_PRICE_ACCOUNTS_UNIT] = float(len(accounts))

    committed = [f for f in figures if f.committed]
    if committed:
        units[COMMITTED_USD_UNIT] = float(sum(f.amount for f in committed))
        derived_total = sum(f.amount for f in committed if f.derived)
        if derived_total:
            # The portion of the sum that came off a written summary rather
            # than a verified quote. Carried as a NUMBER rather than a flag
            # so a renderer can hedge in proportion — "$X of that" reads very
            # differently from a blanket disclaimer over the whole figure.
            units[COMMITTED_USD_DERIVED_UNIT] = float(derived_total)
    if accounts_named:
        units["commercial_grounded_accounts"] = float(len(accounts_named))
    return units


def _rank_fractions(values: Sequence[float]) -> dict[float, float]:
    """value -> where it sits within `values`, from just above 0 to 1.0 for
    the largest.

    FULL RESOLUTION, NOT THE QUARTILE. The quartile is what gets reported
    (`types.SIZE_BANDS`); this is what gets sorted on. Quantising first and
    then breaking the ties would mean falling through to the raw `value`,
    which is denominated differently for different findings in the same run —
    the exact cross-currency comparison the whole ordinal design exists to
    avoid. Sorting on the fraction and reporting the quartile gets both: a
    total order that never compares dollars to accounts, and an output number
    that never implies more precision than the evidence carries.

    Ties always share a fraction — the rank used is the count of the
    population AT OR BELOW each value, so two findings of identical size can
    never be separated by an accident of iteration order. That matters more
    than it sounds: ordering has to be reproducible run over run, which is
    the claim this engine makes against asking a general model the same
    question.

    The largest member of any population sits at 1.0, which is also true of a
    population of one. That is deliberate rather than a degenerate case: a
    lone quoted figure IS the largest quoted figure in the run. It does mean
    a small figure ranks top when it is the only one, which is a property of
    the DATA rather than of this function — `_log_size_bands` publishes the
    population sizes and the figures behind them so that condition is visible
    rather than inferred.
    """
    if not values:
        return {}
    ordered = sorted(values)
    n = len(ordered)
    return {
        value: bisect_right(ordered, value) / n
        for value in set(ordered)
    }


def _size_ranks(
    findings: Sequence[Finding], provisional: Sequence[Impact],
) -> list[Optional[float]]:
    """The one cross-finding comparison in this pipeline, and the reason it
    lives HERE rather than inside `score_impact`.

    THE PROBLEM. A finding carrying a real quoted dollar figure but no named
    account scores `value=None` — we know what a customer said and nothing
    about how many accounts it touches — so it sorted below every finding we
    could size, however trivially. Findings holding actual money ranked last,
    which is the opposite of what a stated figure is worth. And the obvious
    repair, adding the dollars to `value`, is worse than the disease: `value`
    is denominated in accounts here, so dollars would win by six orders of
    magnitude and naming any figure at all would beat every reach-based
    finding in the corpus.

    THE ANSWER IS ORDINAL, AND EACH FINDING COMPETES IN ITS OWN CURRENCY.
    Dollar findings are ranked against the other dollar findings; reach
    findings against the other reach findings. The top of the quoted figures
    and the top of the reaches are both "the biggest of their kind", which IS
    comparable, where their raw numbers are not. A finding that has both
    takes the higher of its two ranks, so carrying more evidence can lift a
    finding and can never demote one.

    WHAT THIS DELIBERATELY DOES NOT DO. It does not guarantee a dollar
    finding reaches the top of the list. If the quoted figures in a corpus
    span a wide range, the smallest of them ranks low and stays there.
    Guaranteeing the dollar line renders would mean a $5,000 one-off
    outranking a forty-account finding, which is the loudest-problem failure
    rebuilt with a currency symbol on it. The figure earns its place or it
    does not.

    I10 IS SATISFIED, and the direction matters. I10 forbids Stage 10 writing
    BACKWARD into frozen scores; this reads forward, before the freeze:
    inputs are compared, the answer is written into `ImpactInputs`, and only
    then is anything scored. Ordering afterwards reads the frozen `Impact` and
    mutates nothing, exactly as before.
    """
    dollar_values: list[float] = []
    reach_values: list[float] = []
    per_finding: list[tuple[Optional[float], Optional[float]]] = []
    for finding, impact in zip(findings, provisional):
        usd = finding.impact_inputs.native_units.get(COMMITTED_USD_UNIT)
        usd = float(usd) if isinstance(usd, (int, float)) else None
        reach = impact.value
        if usd is not None:
            dollar_values.append(usd)
        if reach is not None:
            reach_values.append(reach)
        per_finding.append((usd, reach))

    dollar_ranks = _rank_fractions(dollar_values)
    reach_ranks = _rank_fractions(reach_values)

    ranks: list[Optional[float]] = []
    for usd, reach in per_finding:
        candidates = [
            rank for rank in (
                dollar_ranks.get(usd) if usd is not None else None,
                reach_ranks.get(reach) if reach is not None else None,
            )
            if rank is not None
        ]
        ranks.append(max(candidates) if candidates else None)
    return ranks


def _log_size_bands(
    findings: Sequence[Finding], ranks: Sequence[Optional[float]]
) -> None:
    """The figures behind each dollar band, and how many findings each
    population holds, so a reader can check whether the ranking is doing
    something sensible on a real corpus.

    Without this the band is an unfalsifiable integer. The specific thing it
    exists to expose: a position is only as meaningful as the population it
    was taken against, so in a corpus with two quoted figures BOTH sit near
    the top of "the quoted figures" and rank accordingly. That is correct by
    the ordinal design and may still be wrong for a reader, and the only way
    to tell is to see the amounts and the population sizes together — which
    is why `dollar_population` is on this line and not left to be inferred
    from the band histogram. Magnitudes and counts only; never a paraphrase,
    an account name, or anything a customer said.
    """
    dollars_by_band: defaultdict[int, list[float]] = defaultdict(list)
    reach_by_band: defaultdict[int, int] = defaultdict(int)
    for finding, rank in zip(findings, ranks):
        band = _band_for_rank(rank)
        if band is None:
            continue
        usd = finding.impact_inputs.native_units.get(COMMITTED_USD_UNIT)
        if isinstance(usd, (int, float)):
            dollars_by_band[band].append(float(usd))
        else:
            reach_by_band[band] += 1
    if not dollars_by_band and not reach_by_band:
        return
    dollar_population = sum(len(v) for v in dollars_by_band.values())
    reach_population = sum(reach_by_band.values())
    logger.info(
        "crucible_size_bands dollar_population=%s dollar_figures_by_band=%s "
        "reach_population=%s reach_findings_by_band=%s unbanded=%s",
        dollar_population,
        {b: sorted(v, reverse=True) for b, v in sorted(dollars_by_band.items())},
        reach_population,
        dict(sorted(reach_by_band.items())),
        sum(1 for r in ranks if r is None),
    )


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
    # CORPUS-WIDE, BEFORE CLUSTERING. A rate card quoted to sixteen accounts
    # scatters across whatever clusters those calls' subjects produce, so the
    # repetition is only visible from here.
    list_price_amounts = repeated_amounts(claims)
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
                native_units=_grounded_commercial_native_units(
                    group, list_price_amounts,
                ),
                # The identities behind that sum, so anything summing ACROSS
                # findings can deduplicate the same money one more time.
                grounded_figures=deduped_grounded_figures(
                    group, list_price_amounts,
                ),
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

    # ── The ordinal size band: the ONE cross-finding comparison ─────────────
    #
    # SCORED TWICE, ON PURPOSE, and only the second result ever escapes. The
    # reach half of the band is `score_impact`'s own arithmetic, so the
    # alternative was to re-derive `affected_population * movable_gap *
    # value_per_unit` here — a second copy of the sizing formula, free to
    # drift from the real one and wrong in a way no test would catch, because
    # both copies would agree until someone changed one. Calling the real
    # scorer for a throwaway pass costs nothing (it is pure arithmetic over a
    # frozen dataclass) and there is then exactly one definition of size.
    #
    # The provisional impacts are discarded here and never reach a caller.
    provisional = [score_impact(f) for f in findings]
    ranks = _size_ranks(findings, provisional)
    _log_size_bands(findings, ranks)
    findings = [
        f if rank is None else replace(
            f, impact_inputs=replace(f.impact_inputs, size_rank=rank)
        )
        for f, rank in zip(findings, ranks)
    ]
    impacts = [score_impact(f) for f in findings]
    confidences = [score_confidence(f, now=now) for f in findings]

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

    Size is read as `size_rank` — the finding's position among its OWN
    currency's peers — and never as the raw `value`. That is the whole
    change, and it is a change of unit rather than of policy. `value` is
    denominated differently for different findings on the same run: accounts
    for a reach-based finding, and nothing at all for one whose only evidence
    is a quoted dollar figure. Sorting the whole list on it compared
    incommensurable things, and put every finding we could not size in
    accounts last by construction — including the ones holding real money.
    `size_rank` is the same question asked in a unit every finding shares:
    how big is this, relative to the things it can be compared to.

    WHY NOT SORT ON THE QUARTILE AND BREAK TIES ON `value`. Because the tie
    would be broken by exactly the comparison the quartile exists to avoid.
    Measured on a probe corpus of 201 findings, a figure-only finding banded
    top-quartile and then landed 51st, behind every reach finding in its own
    band, because `value` was asked to rank "fifty accounts" against "no
    account measure" and `None` loses that every time. The quartile is what
    gets REPORTED (`types.SIZE_BANDS`); the underlying position is what
    sorts.

    This preserves reach-only ordering exactly, and provably: within one
    currency `size_rank` is monotone in `value`, so reach findings come out
    in precisely the order sorting on `value` alone produced. A finding with
    no figure is exactly where it always was, relative to the others with no
    figure.

    An unranked finding — no grounded figure and no measured reach — sorts
    last but is never dropped and never treated as zero: "we could not size
    this" and "this is worth nothing" lead to opposite decisions. An
    authoritative conflict still outranks everything, because two sources
    that may both speak disagreeing is worth more than either claim.
    """
    def key(i: int):
        conflict = findings[i].adjudication == "conflict"
        size_rank = impacts[i].size_rank
        return (
            0 if conflict else 1,
            # A real rank is always > 0, so an unranked finding sorting at 0
            # lands behind every ranked one without a sentinel.
            -(size_rank if size_rank is not None else 0.0),
            # Cross-currency ties (the top of the figures and the top of the
            # reaches both sit at 1.0) fall through to how SURE we are —
            # the only remaining discriminator that means the same thing for
            # both kinds of finding.
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

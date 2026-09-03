"""Deterministic backfill: recover a stated commercial figure a historical
`kg_signal.content` paraphrase already carries, with zero LLM calls.

WHY THIS EXISTS. The extraction pass now writes a grounded
`amount`/`currency`/`basis`/`certainty` shape onto `commercial_term`/`pricing`
signals (see `app.graph.extractor._grounded_amount_properties`), but only at
ingest time. Every signal written before that change predates it, so the
feature finds nothing on a historical corpus. This module re-reads the
paraphrase text those old signals already carry and, where it holds an
unambiguous dollar figure, fills the same two fields.

THE PROVENANCE PROBLEM, AND WHY THIS MODULE NEVER WRITES `certainty` VIA THE
SHARED VALIDATOR. `content` is the model's paraphrase, not the source text — a
figure read back out of it has no `verbatim_quote` behind it, so it is not
grounded the same way an ingest-time figure is (transcription error, not
fabrication: the paraphrase itself was written under a grounding gate, so the
number came from verified text, but the model could have copied it wrong).
A backfilled row must therefore be distinguishable from an ingest-time one.
`_grounded_amount_properties`'s `certainty` vocabulary
(`quoted`/`asked`/`estimated-by-speaker`) is deliberately CLOSED to states an
extraction call can actually observe — calling it with anything else silently
drops the key, which is exactly the point: this module reuses that function
for the numeric SHAPE (float coercion, currency normalisation), then stamps
`certainty=BACKFILL_CERTAINTY` itself, a sentinel value the real extractor's
own gate would never let through. A downstream reader can trust
`certainty in {"quoted", "asked", "estimated-by-speaker"}` as "this number
came off a verbatim-grounded utterance" and treat `BACKFILL_CERTAINTY` as
"this number came off a paraphrase, hedge accordingly" — never the same claim.

`basis` is left untouched either way: it is not recoverable from a paraphrase
and this module never guesses it (a missing field stays missing, per I3).

ELIGIBILITY MIRRORS INGEST EXACTLY. Only signals whose `kind` is in
`app.graph.extractor._AMOUNT_ELIGIBLE_KINDS` (`commercial_term`, `pricing`)
are considered — the same gate `extract_document`/the checklist pass apply —
so a backfilled row is eligible for `amount` in exactly the cases an ingest-
time row would have been, never a wider set.

RESUMABLE AND IDEMPOTENT WITHOUT A SEPARATE QUEUE. A signal that already
carries `amount` is skipped outright (never re-derived — see R4 in the
ticket this implements). That skip IS the resume checkpoint: a crashed or
re-invoked run simply re-scans the company's eligible signals from the start,
and everything already enriched is a no-op, so a second run over the same
population enriches exactly zero new rows. `app.db.crucible_backfill_runs`
records each invocation for audit, but carries no per-signal claim state —
there is nothing to claim, because the signal row itself already is the
completion marker.

AND THAT IS WHY THE UNDO IS PART OF THE TOOL. The same guard that makes a
re-run safe makes a re-run USELESS against rows an earlier, wronger pattern
already wrote: they carry `amount`, so a corrected sweep skips them forever.
`purge_backfilled_amounts` clears exactly the rows this module minted — no
wider — so a corrected pattern can be applied to them. A sweep that can only
add is a sweep whose first mistake is permanent.

WHAT THE PARSER MAY AND MAY NOT DECIDE. At ingest, the judgement "is this
figure a deal fact?" is made by the model under a prompt contract, and only
then is `amount` attached. A regex cannot ask that question, and the first
revision of this module did not try to — it kept the transcription and
dropped the classification, which is how market sizes and valuations were
read as customer-stated deal values. The three gates in
`scan_dollar_figures` are a deliberate, lossy approximation of the missing
half, and each one refuses with a recorded reason rather than adjusting a
number: this module may decline to write a figure, but it never writes a
figure other than the one the text states.
"""
from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from app.db import crucible_backfill_runs
from app.db.client import require_client
from app.graph.extractor import _AMOUNT_ELIGIBLE_KINDS, _grounded_amount_properties

logger = logging.getLogger(__name__)

#: Bump this whenever the parsing rules below change, so an old run's numbers
#: are never silently compared against a newer pattern (R5 auditability).
#:
#: `dollar-v2` added the SEMANTIC gate this sweep was missing: v1 kept the
#: model's transcription ("there is a `$` figure here") and dropped its
#: classification ("is this figure a deal fact?"), which a regex has no way
#: to ask on its own. See `_NON_DEAL_CONTEXT`, `_NON_DEAL_SCALE` and
#: `_MAX_PLAUSIBLE_DEAL_USD`. Runs on either side of this line are not
#: comparable and the version string is how a reader can tell.
PATTERN_VERSION = "dollar-v2"

#: The sentinel `certainty` value a backfilled row carries. Deliberately NOT
#: a member of `extractor._COMMERCIAL_CERTAINTY_VALUES` — see module docstring.
BACKFILL_CERTAINTY = "derived-from-summary"

_PAGE_SIZE = 500

#: A run-away safety valve, not a business rule: no single invocation reads
#: more than this many candidate rows, so a mis-typed `--company` against a
#: much larger tenant than expected fails loud (KeyError-free `None` company
#: never reaches this far) rather than paging for an unbounded time.
_MAX_ROWS_PER_RUN = 200_000

#: Scale words a stated dollar figure may carry ("$500k", "$2.4 million").
#:
#: `b`/`billion` are deliberately ABSENT — see `_NON_DEAL_SCALE`, which is
#: where they went and why.
_DOLLAR_SCALE: dict[str, float] = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "million": 1e6,
}

#: Scale words the pattern still RECOGNISES but never RESOLVES to a value.
#:
#: WHY NOT SIMPLY DELETE THEM FROM `_DOLLAR_SCALE`. Deleting alone would be
#: strictly worse than leaving them in: the scale group would stop matching
#: "billion", the pattern would fall back to a bare "$2", and the row would
#: be enriched with `2.0`. A wrong figure is more dangerous than a missing
#: one, because unlike a missing one it looks like data. So the scale group
#: keeps consuming the word and the RESOLUTION step rejects the whole
#: figure, with a reason, instead.
#:
#: WHY REJECT AT ALL. Measured on a real corpus: one market-size row
#: carrying a `$50B` figure minted a finding-level grounded sum of
#: 50,010,812,725 across 61 claims — the other 60 of which summed to about
#: $10.8M. A render cap was the only reason a reader was not shown that
#: number as a customer-stated figure. No quoted DEAL in this corpus is
#: billions; the only work this scale word does here is import market-size,
#: valuation and funding figures, which are not deal facts.
_NON_DEAL_SCALE: frozenset[str] = frozenset({"b", "billion"})

#: The largest figure this sweep will accept as a stated DEAL value.
#:
#: A CEILING THAT SKIPS, NEVER A CLAMP. A clamped figure is a wrong figure
#: wearing the shape of a real one; there is no honest way to render "we
#: reduced this number because it looked too big". Above the ceiling the
#: whole figure is refused and the reason recorded.
#:
#: WHERE THE NUMBER COMES FROM. The observed enrichable shapes on the real
#: corpus are $NNNK, $NNK, $N.NM, $NM and $NNM — every legitimate one below
#: $100M by a wide margin, with the known-bad rows two and four orders of
#: magnitude above it. $100M therefore sits above every deal shape the
#: corpus actually contains and below every non-deal figure it was caught
#: importing. It is a corpus-calibrated bound, not a universal one: a
#: tenant that genuinely writes nine-figure contracts would need it raised,
#: and raising it is a one-line change with this comment as the record of
#: what it was set from.
_MAX_PLAUSIBLE_DEAL_USD: float = 100_000_000.0

#: Words whose presence near a `$` figure says the figure is about the
#: MARKET, the COMPANY or its FUNDRAISING rather than about a deal.
#:
#: This is the classification half the sweep was missing. At ingest the
#: semantic gate lives in the prompt contract — the model attaches `amount`
#: only after judging the item a commercial term. A regex cannot make that
#: judgement, so this approximates its most common failure directions from
#: the shapes actually observed being imported: valuations, market sizes,
#: and rounds raised.
#:
#: EXPLICITLY LOSSY, AND COUNTED AS SUCH. A real deal figure stated in the
#: same breath as a market size ("$80k this year, in a $4B market") is
#: refused along with it. That is the intended trade: a missing figure is
#: recoverable by a later, better pass, and a wrong one is not recoverable
#: at all once it has been read as data. The run reports how many signals
#: each reason skipped so the size of the loss is visible rather than
#: inferred.
_NON_DEAL_CONTEXT = re.compile(
    r"\b(?:valuation|market|tam|raise|raised|raising|funding|"
    r"book\s+of\s+business)\b",
    re.IGNORECASE,
)

#: How far either side of a matched figure the stop-list looks. `content` is
#: a one-or-two-sentence paraphrase, so this is roughly "the same clause and
#: its neighbour" — wide enough to catch the qualifier that names what the
#: figure is, narrow enough that an unrelated later sentence does not veto a
#: good figure.
_CONTEXT_WINDOW_CHARS = 80

#: Why a matched `$` figure was refused rather than written. Closed
#: vocabulary — these strings are also the `skipped_counts` keys the audit
#: row persists, so a run's funnel stays comparable across invocations.
SKIP_IMPLAUSIBLE_MAGNITUDE = "implausible_magnitude"
SKIP_NON_DEAL_CONTEXT = "non_deal_context"

#: A single dollar figure, `$`-prefixed only.
#:
#: WHY `$`-ONLY. The costing pass measured the `$`-prefixed subset at 1,989
#: hits and called it "the high-precision set to trust"; the bare
#: digit-plus-k/m subset (819, no currency symbol) was measured as "looser"
#: and the source of the probe's false positives — a bare "10m" or "3k" in a
#: paraphrase is at least as often a headcount or a percentage as a dollar
#: figure. This pattern only ever matches text with an explicit `$`, which is
#: an unambiguous currency marker by construction — a "no currency marker"
#: ambiguity case therefore never reaches this regex at all; it is excluded
#: by the pattern, not by a runtime check. Extending to `£`/`€` would be a
#: small change but is unverified against any measured data and is left out
#: deliberately.
#:
#: WHY THE NUMBER GROUP IS SHAPED THIS WAY. The costing pass's own probe
#: sample showed "clipping mid-number" (`$NN,NNN,` — a truncated match that
#: silently kept only a prefix of the real figure). `\d{1,3}(?:,\d{3})+`
#: requires PROPERLY grouped thousands separators end-to-end (never stops
#: after one comma group the way a naive `[\d,]+` can), and `\d+` on its own
#: covers a plain ungrouped number ("$1500"). A malformed group (say "$12,34"
#: — a 2-digit second group) matches neither alternative in full, so this
#: pattern never returns a truncated prefix of a bad number; at worst it
#: fails to match at all, which this module treats as "no figure found",
#: never a wrong figure.
_DOLLAR_FIGURE = re.compile(
    r"\$\s?(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?!,?\d)(?:\.(?P<cents>\d+))?"
    r"\s?(?P<scale>k|m|b|thousand|million|billion)?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FigureScan:
    """What the parser saw in one paraphrase: the figures it will stand
    behind, and the reason it refused each one it will not.

    The refusals are the point. A sweep that silently returned an empty list
    for "$2 billion valuation" and for "the customer seemed happy" would
    report both as `no_figure_found`, and the operator reading the funnel
    could not tell "there was nothing here" from "there was something here
    and we judged it not a deal fact". Those are different facts about the
    corpus and only one of them is a reason to improve the parser.
    """

    figures: tuple[float, ...]
    skips: tuple[str, ...]


def scan_dollar_figures(text: str) -> FigureScan:
    """Read every `$` figure in `text`, resolve the ones that can be a stated
    deal value, and record why each of the others was refused.

    Three gates, applied in this order to each match:

    1. **Context** — a stop-word near the figure says it is about the market,
       the company's valuation or a funding round rather than a deal. Checked
       first because it is the truest reason: "$250M book of business" is
       refused for being a book of business, not for being large, and the
       recorded reason should say so.
    2. **Scale word** — a billions figure is never a deal here.
    3. **Magnitude** — a plainly-written figure above the plausible ceiling
       (no scale word to catch it, e.g. "$4,000,000,000") is refused too.

    Surviving figures are order-preserving and de-duplicated by resolved
    value: the same figure mentioned twice ("the deal is $50,000... so
    $50,000 total") is one figure, not two, and is not treated as ambiguous.
    """
    figures: list[float] = []
    skips: list[str] = []
    seen: set[float] = set()
    text = text or ""
    for m in _DOLLAR_FIGURE.finditer(text):
        num_str = m.group("num")
        cents = m.group("cents")
        scale = (m.group("scale") or "").lower()
        try:
            num = float(num_str.replace(",", ""))
        except ValueError:  # pragma: no cover - defensive, regex guarantees digits
            continue
        if cents:
            num += float(f"0.{cents}")

        window = text[
            max(0, m.start() - _CONTEXT_WINDOW_CHARS):
            m.end() + _CONTEXT_WINDOW_CHARS
        ]
        if _NON_DEAL_CONTEXT.search(window):
            skips.append(SKIP_NON_DEAL_CONTEXT)
            continue
        if scale in _NON_DEAL_SCALE:
            skips.append(SKIP_IMPLAUSIBLE_MAGNITUDE)
            continue

        mult = _DOLLAR_SCALE.get(scale, 1.0) if scale else 1.0
        amount = round(num * mult, 2)
        if amount > _MAX_PLAUSIBLE_DEAL_USD:
            skips.append(SKIP_IMPLAUSIBLE_MAGNITUDE)
            continue
        if amount not in seen:
            seen.add(amount)
            figures.append(amount)
    return FigureScan(figures=tuple(figures), skips=tuple(skips))


def find_dollar_figures(text: str) -> list[float]:
    """Every DISTINCT dollar amount `text` states that this module will stand
    behind as a deal value. The refusals and their reasons are on
    `scan_dollar_figures`; this is the value-only view of the same scan, kept
    because "what figures are in this text" is its own question and the
    parse boundary reads better without the reason plumbing."""
    return list(scan_dollar_figures(text).figures)


def amount_distribution(amounts: Sequence[float]) -> Optional[dict[str, Any]]:
    """min / median / max / top-10 of a set of minted amounts, or `None` if
    nothing was minted.

    A STANDING REPORTING BAR, NOT A DIAGNOSTIC. The previous revision of this
    sweep reported only counts — examined, enriched, skipped by reason — and
    every one of those numbers was correct on the run that wrote a
    fifty-billion-dollar figure into a customer-stated field. Counts answer
    "did the tool do what it was told"; they cannot answer "was what it wrote
    true". A single `max()` on that run would have shown 50,000,000,000
    before anyone called it clean, which is why the distribution is emitted
    on every run including a dry one — the dry run is where it is supposed
    to be read.
    """
    if not amounts:
        return None
    ordered = sorted(amounts, reverse=True)
    return {
        "count": len(ordered),
        "min": ordered[-1],
        "median": float(statistics.median(ordered)),
        "max": ordered[0],
        "top_10": ordered[:10],
    }


@dataclass
class BackfillCounts:
    examined: int = 0
    enriched: int = 0
    skipped_already_has_amount: int = 0
    skipped_no_figure_found: int = 0
    skipped_ambiguous_multiple_figures: int = 0
    skipped_non_deal_context: int = 0
    skipped_implausible_magnitude: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "already_has_amount": self.skipped_already_has_amount,
            "no_figure_found": self.skipped_no_figure_found,
            "ambiguous_multiple_figures": self.skipped_ambiguous_multiple_figures,
            SKIP_NON_DEAL_CONTEXT: self.skipped_non_deal_context,
            SKIP_IMPLAUSIBLE_MAGNITUDE: self.skipped_implausible_magnitude,
        }

    @property
    def total_skipped(self) -> int:
        return (
            self.skipped_already_has_amount
            + self.skipped_no_figure_found
            + self.skipped_ambiguous_multiple_figures
            + self.skipped_non_deal_context
            + self.skipped_implausible_magnitude
        )


@dataclass
class SignalDecision:
    """What the sweep decided about one signal — used by both the live write
    path and the mutation-proof/unit-test path so the two never drift apart."""

    signal_id: str
    #: "enriched" | "already_has_amount" | "no_figure_found"
    #: | "ambiguous_multiple_figures" | SKIP_NON_DEAL_CONTEXT
    #: | SKIP_IMPLAUSIBLE_MAGNITUDE
    outcome: str
    new_properties: Optional[dict[str, Any]] = None


def decide_for_signal(properties: dict[str, Any] | None, content: str) -> SignalDecision:
    """Pure decision function: given one signal's existing `properties` and
    `content`, decide whether it is already enriched, unresolvable, ambiguous,
    refused on semantics, or ready to be enriched — and if the latter, return
    the exact new `properties` dict to write (R4/R6: touches
    `amount`/`currency`/`certainty` only, everything else in `properties`
    passes through unchanged).

    ONE OUTCOME PER SIGNAL, and refusals only decide the outcome when nothing
    survived. A paraphrase carrying one good deal figure and one refused
    market figure is enriched from the survivor: that figure passed every
    gate on its own, including the context window around itself. The refusal
    is then not counted anywhere, which is a deliberate limit of a per-signal
    funnel — the counts answer "why was this SIGNAL skipped", never "why was
    each MATCH skipped".
    """
    props = dict(properties or {})
    existing_amount = props.get("amount")
    if isinstance(existing_amount, (int, float)) and not isinstance(existing_amount, bool):
        return SignalDecision(signal_id="", outcome="already_has_amount")

    scan = scan_dollar_figures(content or "")
    figures = list(scan.figures)
    if not figures:
        # The most specific reason wins. `non_deal_context` says what the
        # figure WAS; `implausible_magnitude` only says it was too big, which
        # is the weaker statement of the two when both apply.
        if SKIP_NON_DEAL_CONTEXT in scan.skips:
            return SignalDecision(signal_id="", outcome=SKIP_NON_DEAL_CONTEXT)
        if SKIP_IMPLAUSIBLE_MAGNITUDE in scan.skips:
            return SignalDecision(signal_id="", outcome=SKIP_IMPLAUSIBLE_MAGNITUDE)
        return SignalDecision(signal_id="", outcome="no_figure_found")
    if len(figures) > 1:
        return SignalDecision(signal_id="", outcome="ambiguous_multiple_figures")

    validated = _grounded_amount_properties({"amount": figures[0], "currency": "USD"})
    if "amount" not in validated:
        # Reachable: the shared validator's `_is_number` excludes a literal
        # `0` (a stated figure of zero is not a real quoted amount either —
        # same exclusion the extractor applies at ingest), so a parsed "$0"
        # lands here rather than being written as a real amount.
        return SignalDecision(signal_id="", outcome="no_figure_found")

    new_props = dict(props)
    new_props["amount"] = validated["amount"]
    if "currency" in validated:
        new_props["currency"] = validated["currency"]
    new_props["certainty"] = BACKFILL_CERTAINTY
    return SignalDecision(signal_id="", outcome="enriched", new_properties=new_props)


def _page_eligible_signals(client: Any, company_id: str, page: int) -> list[dict[str, Any]]:
    offset = page * _PAGE_SIZE
    resp = (
        client.table("kg_signal")
        .select("id, kind, content, properties")
        .eq("enterprise_id", company_id)
        .in_("kind", sorted(_AMOUNT_ELIGIBLE_KINDS))
        .order("id")
        .range(offset, offset + _PAGE_SIZE - 1)
        .execute()
    )
    return resp.data or []


def run_backfill(*, company_id: str, apply: bool, limit: Optional[int] = None) -> dict[str, Any]:
    """Sweep every `commercial_term`/`pricing` signal for `company_id`,
    filling `amount`/`currency` (+ the backfill `certainty` marker) where the
    signal's `content` states exactly one unambiguous dollar figure and the
    signal does not already carry an ingest-time `amount`.

    `apply=False` (the default a caller must opt out of explicitly) performs
    every read and every decision but writes nothing — R2's dry-run-first
    contract. Returns a summary dict shaped for both the CLI printer and
    tests; also persists a `crucible_backfill_runs` audit row either way.

    The summary carries `amounts`: the min/median/max/top-10 of what the run
    minted (or would mint, in dry-run). See `amount_distribution` for why
    that is a permanent part of the report rather than a diagnostic.
    """
    if not company_id:
        raise ValueError("company_id is required — there is no global mode")

    client = require_client()
    counts = BackfillCounts()
    minted: list[float] = []
    mode = "apply" if apply else "dry_run"
    run_row = crucible_backfill_runs.start(
        company_id=company_id, mode=mode, pattern_version=PATTERN_VERSION,
    )
    run_id = run_row.get("id")

    try:
        page = 0
        while True:
            rows = _page_eligible_signals(client, company_id, page)
            if not rows:
                break
            for row in rows:
                if limit is not None and counts.examined >= limit:
                    break
                if counts.examined >= _MAX_ROWS_PER_RUN:
                    break
                counts.examined += 1
                decision = decide_for_signal(row.get("properties"), row.get("content") or "")
                if decision.outcome == "already_has_amount":
                    counts.skipped_already_has_amount += 1
                elif decision.outcome == "no_figure_found":
                    counts.skipped_no_figure_found += 1
                elif decision.outcome == "ambiguous_multiple_figures":
                    counts.skipped_ambiguous_multiple_figures += 1
                elif decision.outcome == SKIP_NON_DEAL_CONTEXT:
                    counts.skipped_non_deal_context += 1
                elif decision.outcome == SKIP_IMPLAUSIBLE_MAGNITUDE:
                    counts.skipped_implausible_magnitude += 1
                elif decision.outcome == "enriched":
                    counts.enriched += 1
                    minted.append(float((decision.new_properties or {})["amount"]))
                    if apply:
                        (
                            client.table("kg_signal")
                            .update({"properties": decision.new_properties})
                            .eq("enterprise_id", company_id)
                            .eq("id", row["id"])
                            .execute()
                        )
            if (
                (limit is not None and counts.examined >= limit)
                or counts.examined >= _MAX_ROWS_PER_RUN
                or len(rows) < _PAGE_SIZE
            ):
                break
            page += 1

        crucible_backfill_runs.finish(
            run_id=run_id,
            company_id=company_id,
            status="completed",
            examined_count=counts.examined,
            enriched_count=counts.enriched,
            skipped_counts=counts.as_dict(),
        )
    except Exception as exc:  # noqa: BLE001 - record the failure, then re-raise
        crucible_backfill_runs.finish(
            run_id=run_id,
            company_id=company_id,
            status="failed",
            examined_count=counts.examined,
            enriched_count=counts.enriched,
            skipped_counts=counts.as_dict(),
            error=str(exc),
        )
        logger.warning(
            "crucible_backfill_run_error company_id=%s run_id=%s", company_id, run_id,
            exc_info=True,
        )
        raise

    distribution = amount_distribution(minted)
    if distribution:
        # Identifiers and magnitudes only — never the paraphrase the figure
        # came out of, which is customer speech.
        logger.info(
            "crucible_backfill_amounts run_id=%s company_id=%s mode=%s count=%s "
            "min=%s median=%s max=%s",
            run_id, company_id, mode, distribution["count"],
            distribution["min"], distribution["median"], distribution["max"],
        )

    return {
        "run_id": run_id,
        "company_id": company_id,
        "mode": mode,
        "pattern_version": PATTERN_VERSION,
        "examined": counts.examined,
        "enriched": counts.enriched,
        "skipped": counts.as_dict(),
        "total_skipped": counts.total_skipped,
        "amounts": distribution,
    }


# ── Undoing a bad sweep ──────────────────────────────────────────────────────

#: The only keys a backfilled row gained, and therefore the only keys a purge
#: may take away. Mirrors `decide_for_signal`'s write set exactly.
PURGEABLE_KEYS = ("amount", "currency", "certainty")


def decide_purge_for_signal(
    properties: dict[str, Any] | None,
) -> Optional[dict[str, Any]]:
    """The new `properties` for one signal if this row was minted by THIS
    sweep, or `None` to leave it alone.

    `BACKFILL_CERTAINTY` is what makes the undo surgical, and it is the one
    piece of the original design that pays for itself here: a backfilled row
    is the only kind of row that can carry that sentinel, because the shared
    extractor validator drops any `certainty` outside its own closed
    vocabulary (see the module docstring). So this can identify exactly the
    rows the sweep wrote — never an ingest-time figure a customer's own words
    put there, which no undo may ever touch.
    """
    props = dict(properties or {})
    if props.get("certainty") != BACKFILL_CERTAINTY:
        return None
    return {k: v for k, v in props.items() if k not in PURGEABLE_KEYS}


def purge_backfilled_amounts(
    *, company_id: str, apply: bool = False, limit: Optional[int] = None,
) -> dict[str, Any]:
    """Strip `amount`/`currency`/`certainty` from exactly the signals a
    previous run of this sweep enriched, for ONE company.

    WHY THIS TOOL HAS TO EXIST. The sweep's idempotency guard skips any
    signal already carrying `amount`. That guard is right — it is what makes
    a crashed run resumable and a re-run a no-op — but it also means a
    CORRECTED sweep can never repair a row an incorrect sweep already wrote.
    Rows minted under an old `PATTERN_VERSION` have to be cleared before the
    new pattern can be applied to them at all.

    TENANT SCOPING IS THE FIRST FILTER, NOT THE LAST. Every read and every
    write names `company_id` explicitly. A bare `certainty` filter would be
    correct-looking and wrong: the sentinel is global, the database is
    shared, and a predicate that matches on it alone reaches other tenants'
    rows. It is not offered as an option here — there is no all-companies
    mode, the same as the sweep itself.

    Membership is decided in Python rather than as a JSON predicate in the
    query, so the eligible page is exactly the population the sweep itself
    reads (`_page_eligible_signals`) and the two can never select different
    sets. `apply=False` is the default: a dry run reports the count and the
    distribution of what it WOULD clear, and writes nothing.
    """
    if not company_id:
        raise ValueError("company_id is required — there is no global mode")

    client = require_client()
    examined = 0
    cleared = 0
    cleared_amounts: list[float] = []

    page = 0
    while True:
        rows = _page_eligible_signals(client, company_id, page)
        if not rows:
            break
        for row in rows:
            if limit is not None and examined >= limit:
                break
            if examined >= _MAX_ROWS_PER_RUN:
                break
            examined += 1
            props = row.get("properties") or {}
            new_props = decide_purge_for_signal(props)
            if new_props is None:
                continue
            cleared += 1
            amount = props.get("amount")
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                cleared_amounts.append(float(amount))
            if apply:
                (
                    client.table("kg_signal")
                    .update({"properties": new_props})
                    .eq("enterprise_id", company_id)
                    .eq("id", row["id"])
                    .execute()
                )
        if (
            (limit is not None and examined >= limit)
            or examined >= _MAX_ROWS_PER_RUN
            or len(rows) < _PAGE_SIZE
        ):
            break
        page += 1

    distribution = amount_distribution(cleared_amounts)
    logger.info(
        "crucible_backfill_purge company_id=%s mode=%s examined=%s cleared=%s max=%s",
        company_id, "apply" if apply else "dry_run", examined, cleared,
        (distribution or {}).get("max"),
    )
    return {
        "company_id": company_id,
        "mode": "apply" if apply else "dry_run",
        "examined": examined,
        "cleared": cleared,
        "amounts": distribution,
    }

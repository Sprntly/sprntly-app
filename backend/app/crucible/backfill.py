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
read as customer-stated deal values. The gates in `scan_dollar_figures` are
a deliberate, lossy approximation of the missing half, and each one refuses
with a recorded reason rather than adjusting a number: this module may
decline to write a figure, but it never writes a figure other than the one
the text states.

THAT APPROXIMATION HAS NOW BEEN PATCHED FIVE TIMES. Read the header comment
above `_NO_DEAL_VALUE_STATED` before adding a sixth pattern — the durable
fix is a classifier, and every pattern added here is permanent surface for
some future real quote to collide with. (The range gate below is the one
exception worth separating out: it fixes the PARSER producing a number the
text never stated, which is a different and more serious fault than the
category patterns, and no classifier removes the need for it.)
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
#:
#: `dollar-v3` added the floor (`_MIN_PLAUSIBLE_DEAL_USD`). The first live
#: run under v2 recovered a headline sum whose bottom five addends were
#: $500, $400, $200, $100 and $25 — noise being laundered into a figure a
#: reader would quote.
#:
#: `dollar-v4` added the three refusal families. The same live run's HEAD
#: was worse than its tail: $4.5M of a $5.17M total was a competitor's
#: pricing, a revenue target and the company's own ARR bound.
#:
#: `dollar-v6` adds a permanent refusal for a personal sales record
#: ("$3.7M TCV with a 43% close rate"), as a backstop under the classifier
#: rather than as a replacement for it.
#:
#: `dollar-v5` refuses ranges. "~$100-150k/year" was being stored as $100 —
#: a 1,000x DOWNWARD error, and the only one of these defects that
#: manufactures a wrong number rather than importing one.
PATTERN_VERSION = "dollar-v6"

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

#: The smallest figure this sweep will accept as a stated DEAL value.
#:
#: THE CEILING'S SIBLING, AND DERIVED THE SAME WAY — from the distribution
#: the sweep actually produced rather than from a round number that felt
#: right. One finding's twenty-one recovered figures, in full:
#:
#:     3,000,000  1,000,000  500,000  160,000  150,000  100,000  75,000
#:        50,000     47,500   30,000   25,000   10,000    9,000   5,000
#:         3,500      3,000      500      400      200      100      25
#:
#: The largest multiplicative gap anywhere in that distribution is between
#: 3,000 and 500 — a 6.0x step, wider than any gap in the head (the biggest
#: there is 3.1x, from 500,000 to 160,000). That break is the evidence: the
#: bottom five figures are a different population from the rest, not the
#: thin end of one.
#:
#: They also carry no weight. Those five are 24% of the figures and 0.024%
#: of the money. A quarter of the addends contributing a four-hundredth of
#: one percent is the whole argument: each one is another chance to be
#: wrong, and none of them can change the answer.
#:
#: WHY THE BOTTOM OF THE GAP RATHER THAN THE TOP. Any floor from 501 to
#: 3,000 removes exactly the same five figures here, so this corpus cannot
#: choose between them — and where the evidence is silent, the smaller
#: filter is the honest one. $1,000 is the SMALLEST floor that captures the
#: entire observed noise cluster; anything higher discards no additional
#: noise here while starting to eat real four-figure deals on some other
#: tenant. A three-figure amount reads as a line item, a credit or a
#: per-unit price; a four-figure one is a plausible small deal or pilot.
#:
#: Corpus-calibrated, not universal, and one line to move — same posture as
#: the ceiling above.
_MIN_PLAUSIBLE_DEAL_USD: float = 1_000.0

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

# ─────────────────────────────────────────────────────────────────────────────
# READ THIS BEFORE ADDING A SIXTH PATTERN.
#
# The families below are the FOURTH patch on one wound. In order: a $50B
# market size; a tail of $25/$100/$200 line items; then a $3M figure that a
# customer pays to a COMPETITOR, a $1M "target", and a "less than $500K ARR"
# stored as a precise point value. Every one of them was arithmetically
# valid and semantically wrong, and each time the answer was another phrase.
#
# (`_RANGE_AFTER`/`_RANGE_BEFORE` further down are NOT part of this count.
# Those fix the parser inventing a value the text never stated — a
# correctness bug, not a category judgement — and a classifier would not
# make them unnecessary.)
#
# A phrase list catches phrasings we have already seen. It cannot catch the
# next one, and there is always a next one, because the question these
# patterns are approximating — "is this figure a deal value?" — is a
# judgement about meaning, not a property of the characters. At ingest that
# judgement is made by a model under a prompt contract, which is why the
# ingest path does not have this problem.
#
# So: the durable fix is CLASSIFICATION, not accumulation. If you have found
# a fifth variant that slips through, that is the signal to build the
# classifier — not to add pattern six. Each pattern added here buys one
# corpus's worth of correctness and adds permanent surface that some future
# real quote will collide with (see `_BOUNDED_QUANTITY_BEFORE`, which is
# adjacency-anchored for exactly that reason).
# ─────────────────────────────────────────────────────────────────────────────

#: THE PARAGRAPH ARGUES AGAINST ITSELF — the highest-precision signal here,
#: because the text states outright that no deal value is present and the
#: sweep then reads a number out of the same sentence anyway.
#:
#: Observed: "No specific contract value or pricing for [subject] is
#: stated." alongside a $3,000,000 figure, and "clients above $250K … rather
#: than a direct fee" alongside a $250,000 one.
_NO_DEAL_VALUE_STATED = re.compile(
    r"\bno\s+(?:specific\s+)?contract\s+value\b"
    r"|\bno\s+(?:specific\s+)?(?:contract\s+value|pricing)"
    r"[^.]{0,80}?\b(?:is\s+)?(?:stated|discussed|mentioned|specified)\b"
    r"|\brather\s+than\s+a\s+direct\s+fee\b",
    re.IGNORECASE,
)

#: THE FIGURE IS ABOUT THE COMPANY, NOT ABOUT A DEAL. Recurring-revenue
#: totals and revenue goals are the company's own scale; a deal is what one
#: customer agreed to pay.
#:
#: Observed: "with a target of $1M+ by end of year" (an aspiration, not an
#: agreement) and "less than $500K ARR" (the company's own book).
#:
#: NO BARE `ARR`, AND THAT IS A MEASURED DECISION RATHER THAN AN OVERSIGHT.
#: The obvious pattern here is `\bARR\b`, and it was written, and the
#: existing suite immediately refused "closed at $1.5 million ARR" — which
#: is a real DEAL, stated the way SaaS contracts are normally stated. ARR is
#: the unit both a company's own book and a single contract are quoted in,
#: so the token cannot distinguish them.
#:
#: It also turned out to be unnecessary. The observed ARR case ("less than
#: $500K ARR") is already refused by `_BOUNDED_QUANTITY_BEFORE` on "less
#: than", so the bare token caught nothing the other families missed while
#: eating a legitimate quote. A pattern that adds no coverage and removes
#: real data is strictly worse than no pattern.
#:
#: NO COMPETITOR-PRICING PATTERN, for the same reason. The observed
#: competitor case ("customers currently pay around $3,000,000 to [vendor]")
#: is already refused twice over — by the disclaimer in its own sentence and
#: by "around" — so a "pay $X to Y" pattern would buy nothing while
#: misfiring on the most ordinary real deal sentence there is ("they pay
#: $50,000 to us annually").
#:
#: What is left is the pair that cannot mean an agreement: a TARGET is by
#: definition a number nobody has agreed to yet.
_COMPANY_SCALE = re.compile(
    r"\btarget\s+of\b|\brevenue\s+target\b",
    re.IGNORECASE,
)

#: A BOUND OR AN ESTIMATE IS NOT A STATED AMOUNT, and this one is a
#: correctness bug in its own right, independent of any category: the sweep
#: was silently converting inequalities into precise numbers. "less than
#: $500K" is not $500,000. It is not any number.
#:
#: ANCHORED TO THE FIGURE, NOT SEARCHED IN A WINDOW — the one place these
#: families deliberately differ, because this is a claim about GRAMMAR
#: rather than about topic. "less than" only bounds the figure it sits
#: immediately in front of. Searched in the ±80-character window used by the
#: topic families, "revenue is under pressure, but the deal closed at
#: $50,000" would refuse a perfectly good quote on the word "under", and a
#: stop-list that eats real quotes is worse than the problem it solves.
#:
#: `estimated` IS ABSENT AND MUST STAY ABSENT. "Estimated solution cost for
#: [subject] is $300,000" is a real, usable figure; the word modifies "cost"
#: several words earlier and does not bound the number. Adjacency is what
#: keeps that quote alive — see the control test.
#: A PERSON'S SALES RECORD AT A FORMER EMPLOYER. Not a deal, not a price,
#: and not this company's money at all.
#:
#: THIS IS DEFENCE-IN-DEPTH, NOT THE SIXTH PATTERN THE HEADER WARNS ABOUT,
#: and the distinction is worth stating because the two look identical from
#: a diff. The header above is about using phrase-matching AS THE
#: CLASSIFIER — chasing each new genre with another regex instead of
#: building the thing that generalises. That argument was won: the
#: classifier exists and it is what decides categories now.
#:
#: What this adds is a permanent floor UNDER a probabilistic component, for
#: one figure that has already done damage. "$3.7M TCV with a 43% close
#: rate" is a job candidate's personal track record; it is currently kept
#: out of both the sum and the range by a per-run model call, which means a
#: single unlucky draw puts a $3.7M personal sales record back into a
#: client-facing number. A deterministic refusal cannot have an unlucky
#: draw.
#:
#: The rule for whether the NEXT one of these belongs here is not "did the
#: classifier miss it" — that is the argument for improving the classifier.
#: It is "would a wrong answer here be unrecoverable", which is true of very
#: few figures and was true of this one.
_PERSONAL_TRACK_RECORD = re.compile(
    r"\bTCV\b|\bclose\s+rate\b|\binfluenced\b",
    re.IGNORECASE,
)

_BOUNDED_QUANTITY_BEFORE = re.compile(
    r"\b(?:less\s+than|under|below|above|over|approximately|around|roughly)"
    r"\s*$",
    re.IGNORECASE,
)

#: A RANGE IS A BOUND, AND THE PARSER WAS TURNING IT INTO A POINT VALUE.
#:
#: THE SAME DEFECT CLASS AS THE `billion` ONE, and worse. In
#: "~$100-150k/year" the pattern matches `$100`, the dash then blocks the
#: scale group, `150k` is discarded, and the `k` never applies — so a
#: hundred-thousand-a-year engagement was stored as ONE HUNDRED DOLLARS.
#: Wrong by 1,000x, downward, and invisible: it looks like clean data.
#:
#: NOT CAUGHT BY ANYTHING ELSE HERE, which is why it needs its own gate. The
#: stop-list families see no category signal in a range. The ceiling is far
#: above it. The floor cannot save it in general — it MANUFACTURES sub-floor
#: values, so the floor refuses some of them for the wrong reason and
#: reports a misleading funnel, while ranges whose endpoints clear the floor
#: ("$100 to $150k" stored 150,000; "$20,000-30,000" stored 20,000) sail
#: straight through.
#:
#: BOTH ENDS, because refusing only the left one promotes the right one. In
#: "$50,000-$75,000" the first figure is followed by a separator and the
#: second is preceded by one; catching only the first would have turned an
#: ambiguous row into a confidently-stored $75,000.
#:
#: NEITHER ENDPOINT IS TAKEN AND THE RANGE IS NEVER AVERAGED. "$100-150k"
#: is not $100, not $150k, and not $125k — it is a range, and this module's
#: contract is that it may decline to write a figure but never writes a
#: figure other than the one the text states.
#:
#: A DIGIT IS REQUIRED ON THE FAR SIDE, which is what separates a range from
#: ordinary punctuation: "$30,000 - the annual fee" keeps its figure, and so
#: does "a two-year contract for $30,000", where the hyphen belongs to a
#: word and never touches the number.
_RANGE_AFTER = re.compile(r"^\s{0,2}(?:[-–—]|to\b)\s{0,2}\$?\s?\d")
_RANGE_BEFORE = re.compile(
    r"(?:\d|\dk|\dm)\s{0,2}(?:[-–—]|to)\s{0,2}$",
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
#: Its OWN reason rather than sharing the ceiling's. A figure refused for
#: being too small and one refused for being a market size are different
#: facts about the corpus, and collapsing them would hide how much the floor
#: is actually removing behind a count that already means something else.
SKIP_BELOW_DEAL_FLOOR = "below_deal_floor"
#: One reason PER FAMILY, so the funnel shows which pattern is doing the
#: work. Collapsing them would hide a family that has stopped matching
#: anything behind the counts of the ones that still do — which is how you
#: end up maintaining dead patterns and trusting live ones you cannot see.
SKIP_NO_DEAL_VALUE_STATED = "no_deal_value_stated"
SKIP_COMPANY_SCALE = "company_scale"
SKIP_BOUNDED_QUANTITY = "bounded_quantity"
#: Its own reason so the funnel shows how many rows this was silently
#: corrupting — the shape that was storing $100 for a $100-150k/year deal.
SKIP_STATED_AS_A_RANGE = "stated_as_a_range"
#: Its own reason, so a refusal that exists as a backstop under the
#: classifier is legible as one rather than hidden in a category count.
SKIP_PERSONAL_TRACK_RECORD = "personal_track_record"

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

    Eight gates, applied in this order to each match:

    1. **Disclaimer** — the text says outright that no deal value is stated.
       Checked first: a paragraph that argues against itself is the clearest
       evidence there is.
    2. **Context** — a stop-word near the figure says it is about the market,
       the company's valuation or a funding round rather than a deal. Before
       the magnitude checks because it is the truer reason: "$250M book of
       business" is refused for being a book of business, not for being
       large, and the recorded reason should say so.
    3. **Company scale** — recurring-revenue totals and revenue goals are
       the company's own size, not what a customer agreed to pay.
    4. **Bounded quantity** — a bound or an estimate immediately in front of
       the figure ("less than $500K") means it is not a stated amount.
    5. **Range** — a separator touching either side of the figure
       ("$100-150k") means it is one end of a range, not a point value.
    6. **Scale word** — a billions figure is never a deal here.
    7. **Ceiling** — a plainly-written figure above the plausible maximum
       (no scale word to catch it, e.g. "$4,000,000,000") is refused too.
    8. **Floor** — a figure below the plausible minimum is a line item, a
       credit or a per-unit price rather than a deal value. Refused here at
       SWEEP time, so junk is never written and then filtered on read.

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
        # MOST SPECIFIC REASON FIRST. Several families can be true of one
        # sentence ("less than $500K ARR" is both a bound and a company
        # total); the recorded reason should be the one that says the most
        # about why the figure is not a deal value.
        if _NO_DEAL_VALUE_STATED.search(window):
            skips.append(SKIP_NO_DEAL_VALUE_STATED)
            continue
        if _NON_DEAL_CONTEXT.search(window):
            skips.append(SKIP_NON_DEAL_CONTEXT)
            continue
        if _COMPANY_SCALE.search(window):
            skips.append(SKIP_COMPANY_SCALE)
            continue
        if _PERSONAL_TRACK_RECORD.search(window):
            skips.append(SKIP_PERSONAL_TRACK_RECORD)
            continue
        # ADJACENCY, NOT THE WINDOW — see `_BOUNDED_QUANTITY_BEFORE`. Only
        # the text immediately in front of the figure can bound it.
        if _BOUNDED_QUANTITY_BEFORE.search(text[:m.start()]):
            skips.append(SKIP_BOUNDED_QUANTITY)
            continue
        # BEFORE the magnitude gates, so the recorded reason is the true one.
        # A range's left endpoint is often sub-floor precisely BECAUSE the
        # scale word was stripped, so letting the floor answer first would
        # report "too small" for a figure whose real problem is that it was
        # never a point value.
        if (_RANGE_AFTER.search(text[m.end():])
                or _RANGE_BEFORE.search(text[:m.start()])):
            skips.append(SKIP_STATED_AS_A_RANGE)
            continue
        if scale in _NON_DEAL_SCALE:
            skips.append(SKIP_IMPLAUSIBLE_MAGNITUDE)
            continue

        mult = _DOLLAR_SCALE.get(scale, 1.0) if scale else 1.0
        amount = round(num * mult, 2)
        if amount > _MAX_PLAUSIBLE_DEAL_USD:
            skips.append(SKIP_IMPLAUSIBLE_MAGNITUDE)
            continue
        if amount < _MIN_PLAUSIBLE_DEAL_USD:
            # AT SWEEP TIME, so the junk is never stored rather than stored
            # and filtered later. A figure this small is a line item, a
            # credit or a per-unit price, and laundering it into a headline
            # sum is exactly what the floor exists to stop.
            skips.append(SKIP_BELOW_DEAL_FLOOR)
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
    skipped_below_deal_floor: int = 0
    skipped_no_deal_value_stated: int = 0
    skipped_company_scale: int = 0
    skipped_bounded_quantity: int = 0
    skipped_stated_as_a_range: int = 0
    skipped_personal_track_record: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "already_has_amount": self.skipped_already_has_amount,
            "no_figure_found": self.skipped_no_figure_found,
            "ambiguous_multiple_figures": self.skipped_ambiguous_multiple_figures,
            SKIP_NON_DEAL_CONTEXT: self.skipped_non_deal_context,
            SKIP_IMPLAUSIBLE_MAGNITUDE: self.skipped_implausible_magnitude,
            SKIP_BELOW_DEAL_FLOOR: self.skipped_below_deal_floor,
            SKIP_NO_DEAL_VALUE_STATED: self.skipped_no_deal_value_stated,
            SKIP_COMPANY_SCALE: self.skipped_company_scale,
            SKIP_BOUNDED_QUANTITY: self.skipped_bounded_quantity,
            SKIP_STATED_AS_A_RANGE: self.skipped_stated_as_a_range,
            SKIP_PERSONAL_TRACK_RECORD: self.skipped_personal_track_record,
        }

    @property
    def total_skipped(self) -> int:
        return (
            self.skipped_already_has_amount
            + self.skipped_no_figure_found
            + self.skipped_ambiguous_multiple_figures
            + self.skipped_non_deal_context
            + self.skipped_implausible_magnitude
            + self.skipped_below_deal_floor
            + self.skipped_no_deal_value_stated
            + self.skipped_company_scale
            + self.skipped_bounded_quantity
            + self.skipped_stated_as_a_range
            + self.skipped_personal_track_record
        )


@dataclass
class SignalDecision:
    """What the sweep decided about one signal — used by both the live write
    path and the mutation-proof/unit-test path so the two never drift apart."""

    signal_id: str
    #: "enriched" | "already_has_amount" | "no_figure_found"
    #: | "ambiguous_multiple_figures" | SKIP_NON_DEAL_CONTEXT
    #: | SKIP_IMPLAUSIBLE_MAGNITUDE | SKIP_BELOW_DEAL_FLOOR
    #: | SKIP_NO_DEAL_VALUE_STATED | SKIP_COMPANY_SCALE
    #: | SKIP_BOUNDED_QUANTITY | SKIP_STATED_AS_A_RANGE
    #: | SKIP_PERSONAL_TRACK_RECORD
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
        if SKIP_NO_DEAL_VALUE_STATED in scan.skips:
            return SignalDecision(signal_id="", outcome=SKIP_NO_DEAL_VALUE_STATED)
        if SKIP_NON_DEAL_CONTEXT in scan.skips:
            return SignalDecision(signal_id="", outcome=SKIP_NON_DEAL_CONTEXT)
        if SKIP_COMPANY_SCALE in scan.skips:
            return SignalDecision(signal_id="", outcome=SKIP_COMPANY_SCALE)
        if SKIP_PERSONAL_TRACK_RECORD in scan.skips:
            return SignalDecision(signal_id="", outcome=SKIP_PERSONAL_TRACK_RECORD)
        if SKIP_STATED_AS_A_RANGE in scan.skips:
            return SignalDecision(signal_id="", outcome=SKIP_STATED_AS_A_RANGE)
        if SKIP_BOUNDED_QUANTITY in scan.skips:
            return SignalDecision(signal_id="", outcome=SKIP_BOUNDED_QUANTITY)
        if SKIP_IMPLAUSIBLE_MAGNITUDE in scan.skips:
            return SignalDecision(signal_id="", outcome=SKIP_IMPLAUSIBLE_MAGNITUDE)
        if SKIP_BELOW_DEAL_FLOOR in scan.skips:
            return SignalDecision(signal_id="", outcome=SKIP_BELOW_DEAL_FLOOR)
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
                elif decision.outcome == SKIP_BELOW_DEAL_FLOOR:
                    counts.skipped_below_deal_floor += 1
                elif decision.outcome == SKIP_NO_DEAL_VALUE_STATED:
                    counts.skipped_no_deal_value_stated += 1
                elif decision.outcome == SKIP_COMPANY_SCALE:
                    counts.skipped_company_scale += 1
                elif decision.outcome == SKIP_PERSONAL_TRACK_RECORD:
                    counts.skipped_personal_track_record += 1
                elif decision.outcome == SKIP_BOUNDED_QUANTITY:
                    counts.skipped_bounded_quantity += 1
                elif decision.outcome == SKIP_STATED_AS_A_RANGE:
                    counts.skipped_stated_as_a_range += 1
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

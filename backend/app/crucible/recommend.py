"""What to DO about a finding — the one thing the engine never said.

Apurva, on a real report: "this is only the issues, no suggestion on how to
solve or what's the exact recommendation from it".

WHAT THIS IS AND IS NOT, because the distinction is the whole design.

I2 says no LLM returns a score, a rank or a decision. That invariant is intact
and this module is why it can stay intact while the document still recommends
something: every NUMBER a reader sees — impact, confidence band, ordering — is
computed by `scoring.py` and frozen by `_rank` BEFORE this module is called, and
nothing here is ever fed back into any of them. `build_recommendations` takes
findings that are already ranked and returns prose to hang beside them.

The test that keeps this honest is `test_recommendations_never_move_the_ranking`:
it runs the pipeline twice, with and without, and asserts the order and every
score are identical. A recommendation that could change what ranks first would
be a decision, and then I2 really would be broken.

THREE THINGS THE PROSE IS NOT ALLOWED TO DO, enforced after generation rather
than asked for in the prompt, because a prompt is a request and a check is a
guarantee:

  1. Assert an outcome. "Fixing this will recover 12 accounts" is a causal claim
     the corpus cannot support, and I5 already forbids it in a finding — a
     recommendation is not a loophole. Linted with the same `lint_claim`.
  2. Quote a figure. The corpus has no revenue mapped to accounts, so any
     currency amount or percentage is invention. Same rule the plan step lives
     under, for the same reason.
  3. Outrun its evidence. The justification may only rest on what the finding's
     own claims say, which is why the input carries those claims and nothing
     else — no company profile, no other findings, no outside knowledge.

A recommendation that fails a check is DROPPED, not repaired. The finding still
renders exactly as it did before; the reader loses a suggestion and keeps a
document that never says anything it cannot stand behind.
"""
from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass, replace
from typing import Optional, Sequence

from app.crucible.lint import lint_claim
from app.crucible.moscow import (
    TYPE_BUCKET_BLOCKER, TYPE_BUCKET_NEITHER, TYPE_BUCKET_PREFERENCE,
    type_bucket,
)
from app.crucible.types import (
    Claim,
    Confidence,
    Finding,
    GroundedFigure,
    Impact,
)

logger = logging.getLogger(__name__)

#: How many findings get a recommendation.
#:
#: NOT a cost guess — a reading limit. A document that recommends 296 things
#: recommends nothing, and the reader has already been told the list is ordered.
#: The cap applies to the TOP of the frozen order, so the recommendations land
#: on the findings the ranking already put first.
MAX_RECOMMENDED = 8

#: How long the suggestion layer may take, in seconds.
#:
#: Same reason as the gate's: a call that never returns raises nothing, and the
#: report is withheld behind it. Past this the findings render without
#: recommendations, which is exactly what they did before this feature existed.
DEADLINE_SECONDS = 60.0

#: Claims sent per finding. Enough to ground a suggestion, few enough that eight
#: findings fit one call.
MAX_CLAIMS_PER_FINDING = 6

#: Currency figures and percentages, which the corpus cannot support.
_FIGURE = re.compile(r"[$£€]\s?\d|\b\d+(?:\.\d+)?\s?%")

#: Verbs that promise an outcome rather than propose an action.
_PROMISE = re.compile(
    r"\b(will (?:recover|increase|reduce|unlock|drive|deliver|save|win)"
    r"|guarantee\w*|ensures?|results? in|leads? to)\b",
    re.IGNORECASE,
)

RECOMMENDATION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["recommendations"],
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["finding_id", "action", "because"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "action": {
                        "type": "string", "minLength": 8, "maxLength": 240,
                        "description": "One thing to DO, in the imperative.",
                    },
                    "because": {
                        "type": "string", "minLength": 8, "maxLength": 400,
                        "description": (
                            "Why this action follows FROM THE CLAIMS SHOWN. "
                            "Cite what the sources said, not what you know."
                        ),
                    },
                },
            },
        },
    },
}

_SYSTEM = """You read findings from an evidence engine and propose what to do \
about each one.

The findings are already ranked and sized. You are not scoring, ordering or \
selecting anything — every finding you are shown gets exactly one \
recommendation, in the order given.

Rules, all of them hard:
- Propose an ACTION, in the imperative. "Route export tickets to the rendering \
on-call team", not "there is a routing problem".
- Justify it ONLY from the claims shown for that finding. You have no other \
information about this company and must not imply that you do.
- Never assert an outcome. You may not say a change will recover, increase, \
reduce or unlock anything. The evidence does not establish cause, and a \
recommendation that promises a result is worse than none.
- Never quote a figure. No currency amounts, no percentages. The corpus carries \
no revenue mapped to accounts.
- If the claims do not support any action, say so in `action` as "No action \
this evidence supports" and explain what is missing in `because`.

Write for a product manager who will have to defend the suggestion to their \
leadership from the same evidence."""


@dataclass(frozen=True)
class Recommendation:
    """One suggestion, already checked."""
    finding_id: str
    action: str
    because: str


def _claims_for(finding: Finding, claims_by_id: dict[str, Claim]) -> list[str]:
    out = []
    for cid in finding.claim_ids[:MAX_CLAIMS_PER_FINDING]:
        c = claims_by_id.get(cid)
        if c is None:
            continue
        said = (c.assertion or "").strip()
        if said:
            out.append(said)
    return out


def _input(
    goal_text: str, definition_text: str,
    findings: Sequence[Finding], claims_by_id: dict[str, Claim],
) -> str:
    lines = [
        f"GOAL: {goal_text}",
        f"THE READER'S OWN DEFINITION OF THE METRIC: {definition_text}",
        "",
        "FINDINGS. Each is followed by the claims it rests on. Recommend one "
        "action per finding, grounded only in its own claims.",
        "",
    ]
    for f in findings:
        lines.append(f"--- finding_id: {f.id}")
        lines.append(f"theme: {f.label or f.statement}")
        for said in _claims_for(f, claims_by_id):
            lines.append(f"  - {said}")
        lines.append("")
    return "\n".join(lines)


def _acceptable(rec: dict, finding: Finding, strength: str) -> Optional[Recommendation]:
    """Every check that keeps a suggestion inside what the evidence supports."""
    action = (rec.get("action") or "").strip()
    because = (rec.get("because") or "").strip()
    if not action or not because:
        return None
    both = f"{action} {because}"
    if _FIGURE.search(both):
        logger.info("crucible: dropped a recommendation quoting a figure")
        return None
    if _PROMISE.search(both):
        logger.info("crucible: dropped a recommendation promising an outcome")
        return None
    # I5, through the same gate a finding passes. Checked on BOTH halves —
    # `action` used to go unlinted (a real hole: "Fix the export path that is
    # driving churn" passed clean because only `because` was ever checked),
    # and a causal claim in the imperative sentence is exactly as false as one
    # in the justification underneath it.
    if not lint_claim(action, strength).ok or not lint_claim(because, strength).ok:
        logger.info("crucible: dropped a recommendation that failed the lint")
        return None
    return Recommendation(finding_id=finding.id, action=action, because=because)


def _offline() -> bool:
    """True when no model should be called.

    A FUNCTION, not an inline `"pytest" in sys.modules`, so a test that WANTS to
    exercise the real path can monkeypatch this one seam — the convention
    `project_memory` and `graph.decision_log` already use here.

    Without it, adding the first model call to the crucible run path made every
    route test in the suite attempt a network connection and wait for it to
    fail. The call is wrapped in a try, so those tests still passed — slowly,
    and with a stack trace in the log that means nothing.
    """
    return "pytest" in sys.modules


def build_recommendations(
    *,
    enterprise_id: str,
    goal_text: str,
    definition_text: str,
    findings: Sequence[Finding],
    claims: Sequence[Claim],
) -> dict[str, Recommendation]:
    """One suggestion per top finding, or {} if the call fails.

    TOTAL — never raises. A run that produced good findings must not die because
    the suggestion layer did; the document renders without recommendations and
    says nothing false.
    """
    top = list(findings)[:MAX_RECOMMENDED]
    if not top or _offline():
        return {}
    claims_by_id = {c.id: c for c in claims}
    # The strength a recommendation is linted at is the WEAKEST of the claims it
    # rests on, not the strongest: a suggestion inherits the least of what
    # supports it, and linting at the strongest would let a causal phrasing
    # through on the back of one good claim.
    strength_of: dict[str, str] = {}
    for f in top:
        strengths = [claims_by_id[c].strength for c in f.claim_ids
                     if c in claims_by_id]
        strength_of[f.id] = min(strengths, key=_STRENGTH_ORDER.index) if strengths else "reported"

    started = time.monotonic()
    try:
        from app.graph.gateway import llm_call

        result = llm_call(
            enterprise_id=enterprise_id,
            agent="crucible",
            purpose="recommend_actions",
            prompt_version="crucible-recommend-v1",
            system=_SYSTEM,
            input=_input(goal_text, definition_text, top, claims_by_id),
            json_schema=RECOMMENDATION_SCHEMA,
            max_tokens=4000,
        )
        out = result.output
    except Exception:  # noqa: BLE001 — the suggestion layer never kills a run
        logger.exception("crucible: recommendation call failed")
        return {}

    if not isinstance(out, dict):
        return {}
    if time.monotonic() - started > DEADLINE_SECONDS:
        # It answered, but too late to be worth the reader's wait. Recorded so
        # a slow provider shows up in the logs rather than only in the latency.
        logger.warning("crucible: recommendations exceeded their deadline")
    by_id = {f.id: f for f in top}
    kept: dict[str, Recommendation] = {}
    for rec in out.get("recommendations") or []:
        if not isinstance(rec, dict):
            continue
        f = by_id.get((rec.get("finding_id") or "").strip())
        if f is None:
            continue
        ok = _acceptable(rec, f, strength_of.get(f.id, "reported"))
        if ok is not None:
            kept[f.id] = ok
    return kept


#: Weakest first, so `min` picks the least-supported claim.
_STRENGTH_ORDER = ["reported", "inferred", "correlated", "measured", "causally_tested"]


# ── AC-1/AC-2: a deep pass for the top of the ranking, sized by the goal ──────
#
# Apurva: "we don't need to clean up the recommendations for everything. We
# just need to clean it up for like two … Once we pick the top two, then we
# could just compare them." David: "the number of projects really has to be
# in context of the question and what the goal is."
#
# EVERYTHING BELOW THAT DECIDES A COUNT IS ARITHMETIC OVER FROZEN SCORES (I2).
# No model is asked how many findings deserve a deep recommendation, and no
# model is asked which one out-ranks which — both are read off `Impact` and
# `Confidence` objects that `scoring.py` already froze and `_rank` already
# ordered before this module runs, exactly like the flat pass above.

#: Safety cap on the deep pass, independent of what the goal asks for. A
#: user naming "the top 40 things I can do" still gets a real recommendation
#: for a bounded number of them — this is a cost and reading-attention limit,
#: not a claim about how many the goal implies. `resolve_recommendation_count`
#: says so in its own sentence when it bites.
MAX_DEEP_RECOMMENDED = 5

#: `changes[]` entries rendered per deep recommendation. Generous — the model
#: is asked for at most this many — but still a stated bound, same reasoning
#: as `MAX_RECOMMENDED` above: a card that lists forty edits recommends
#: nothing.
MAX_CHANGES_PER_DEEP = 5
MAX_OPEN_QUESTIONS_PER_DEEP = 5

#: Number words a goal ask spells out rather than typing as a digit —
#: "what are two things I can do", not "what are 2 things I can do".
_COUNT_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

#: The vocabulary a count phrase is counting. Deliberately narrow: "two
#: accounts churned" is a fact about the corpus, not an ask for two
#: recommendations, and this list exists so the regex below cannot confuse
#: the two.
_COUNTABLE_NOUN = (
    r"(?:things?|recommendations?|suggestions?|initiatives?|options?|ways?|"
    r"ideas?|projects?|priorities?|actions?)"
)

#: A BOUNDED run of adjective-like words between a named count and its
#: countable noun — "the ten MOST IMPORTANT things", "the three BIGGEST
#: initiatives" — never unlimited distance. Without this, `_NAMED_COUNT_WORD`/
#: `_NAMED_COUNT_DIGIT` required the noun immediately after the number, so any
#: modifier broke the match and the report then claimed "no count or target
#: was named" over a goal that plainly named one — worse than a missed cap,
#: because it is a false claim about what the user asked. Capped at three
#: words (plain letters only, so it cannot cross a comma or reach into an
#: unrelated clause — `\s+` cannot match through a comma that sits directly
#: after the previous word) so a sentence like "two accounts churned in three
#: months" still does not read as a count: "months" is not in
#: `_COUNTABLE_NOUN` regardless of window size, and this cap is what keeps a
#: FUTURE reader from "fixing" a miss by widening the gap without limit, which
#: is the one change that would start reading corpus facts as counts.
_COUNT_ADJECTIVE_GAP = r"(?:[a-zA-Z]+\s+){0,3}"

_NAMED_COUNT_DIGIT = re.compile(
    r"\b(\d{1,3})\s+" + _COUNT_ADJECTIVE_GAP + _COUNTABLE_NOUN + r"\b",
    re.IGNORECASE,
)
_NAMED_COUNT_WORD = re.compile(
    r"\b(" + "|".join(_COUNT_WORDS) + r")\s+" + _COUNT_ADJECTIVE_GAP
    + _COUNTABLE_NOUN + r"\b",
    re.IGNORECASE,
)

#: A goal naming a dollar figure — "$1M in revenue", "1 million dollars",
#: "$500k ARR" — and, separately, a goal naming a headcount-shaped figure —
#: "1,000 accounts", "200 customers". Each capture group is `(number, scale)`;
#: `scale` is empty for the account form.
_NAMED_TARGET_DOLLARS = re.compile(
    r"\$\s?([\d,]+(?:\.\d+)?)\s*(k|m|b|thousand|million|billion)?\b"
    r"|\b(a|an|one|[\d,]+(?:\.\d+)?)\s+(thousand|million|billion)\s+dollars?\b",
    re.IGNORECASE,
)
_NAMED_TARGET_ACCOUNTS = re.compile(
    r"\b([\d,]+(?:\.\d+)?)\s*(accounts?|customers?|clients?|users?)\b",
    re.IGNORECASE,
)
_NAMED_TARGET_PERCENT = re.compile(r"\b([\d,]+(?:\.\d+)?)\s?%")

_DOLLAR_SCALE = {
    "k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9,
    "billion": 1e9,
}

#: `GoalDefinition.currency` values that mean "this is a dollar figure" and
#: "this is a headcount figure", for matching against what the ASK named.
#: A goal that asks in dollars over a corpus sized in accounts is the NORMAL
#: case per the spike — this corpus has no revenue mapped to accounts — and
#: the two sets below are how `resolve_recommendation_count` tells the two
#: units apart rather than silently treating them as interchangeable.
_DOLLAR_CURRENCIES = frozenset({"arr_dollars", "cost_dollars"})
_ACCOUNT_CURRENCIES = frozenset({
    "accounts", "activated_accounts", "new_users", "retained_users",
    "contacts",
})


def _named_count(goal_text: str) -> Optional[int]:
    """"give me 3 recommendations" / "what are two things I can do" -> 3 / 2.

    Regex, not a model call — I2 forbids an LLM deciding how many
    recommendations there will be, and a count is exactly that kind of
    decision.
    """
    text = goal_text or ""
    m = _NAMED_COUNT_DIGIT.search(text)
    if m:
        n = int(m.group(1))
        if n > 0:
            return n
    m = _NAMED_COUNT_WORD.search(text)
    if m:
        return _COUNT_WORDS[m.group(1).lower()]
    return None


def _named_target(goal_text: str) -> Optional[tuple[float, str]]:
    """"I want to get a million dollars" / "reach 1,000 accounts" -> (1e6,
    "dollars") / (1000.0, "accounts"). Dollars are checked first: "$1M
    across 40 accounts" names a dollar target, not an account count, even
    though the sentence also contains a number-plus-noun the account pattern
    would otherwise match.
    """
    text = goal_text or ""
    m = _NAMED_TARGET_DOLLARS.search(text)
    if m:
        if m.group(1) is not None:
            digits, scale_word = m.group(1), (m.group(2) or "").lower()
        else:
            word = (m.group(3) or "").lower()
            digits = "1" if word in ("a", "an", "one") else word
            scale_word = (m.group(4) or "").lower()
        if digits:
            value = float(digits.replace(",", "")) * _DOLLAR_SCALE.get(scale_word, 1.0)
            return value, "dollars"
    m = _NAMED_TARGET_ACCOUNTS.search(text)
    if m:
        return float(m.group(1).replace(",", "")), "accounts"
    m = _NAMED_TARGET_PERCENT.search(text)
    if m:
        return float(m.group(1).replace(",", "")), "percent"
    return None


def _unit_matches(unit: str, currency: str) -> bool:
    """Can THIS corpus size a finding in the unit the goal named?

    Almost always no — the spike's own finding: this corpus sizes findings in
    accounts, and a goal named in dollars is the normal case, not an edge one.
    `percent` never matches anything a `rice.py`/`moscow.py` Impact carries
    today, so a percent target always falls through honestly rather than
    pretending an account count answers a percentage question.
    """
    if unit == "dollars":
        return currency in _DOLLAR_CURRENCIES
    if unit == "accounts":
        return currency in _ACCOUNT_CURRENCIES
    return False


def _money_phrase(amount: float) -> str:
    return f"${amount:,.0f}"


#: How many individual figures the basis sentence names before summarising
#: the rest.
#:
#: The first live run put TWENTY-ONE addends in a single sentence
#: ("$3,000,000 + $1,000,000 + $500,000 + …"), which is not a sentence
#: anybody reads. Five because on that run the top five carried 93% of the
#: total and the top three carried 87% — enough that a reader sees the
#: figures actually driving the number, few enough to scan in one pass.
#:
#: A RENDERING CAP, NOT A DATA CHANGE. Every figure still counts toward the
#: sum and the full set stays on `Impact.grounded_figures`, structured, for
#: anything that wants to enumerate them.
MAX_INLINE_FIGURES = 5


def _figures_phrase(figures: Sequence[GroundedFigure]) -> str:
    """`$3,000,000 + $1,000,000 + $500,000 + $160,000 + $150,000, and 16
    smaller figures` — the contributors that matter, then an honest count of
    the tail rather than a truncation a reader cannot detect."""
    ordered = sorted(figures, key=lambda f: -f.amount)
    shown = ordered[:MAX_INLINE_FIGURES]
    remainder = len(ordered) - len(shown)
    text = " + ".join(_money_phrase(f.amount) for f in shown)
    if remainder > 0:
        text += (
            f", and {remainder} smaller figure"
            f"{'' if remainder == 1 else 's'}"
        )
    return text


def aggregate_price_range(
    pairs: Sequence[tuple[float, float]],
) -> Optional[tuple[float, float, int]]:
    """The one aggregation rule for a corpus-wide list-pricing range: a min
    of mins, a max of maxes, and how many pairs there were. `None` for an
    empty sequence.

    SHARED BY BOTH RENDERERS OF LIST PRICING, so the arithmetic can never
    independently drift between them: `quoted_list_pricing_basis` below (the
    live panel) and `report.py`'s `_list_pricing` (the exported document)
    both call this rather than each computing their own version of the same
    two extremes.

    ONLY WHAT CAN BE AGGREGATED WITHOUT DOUBLE COUNTING — see
    `quoted_list_pricing_basis`'s docstring for why a sum is never the right
    operation here.
    """
    if not pairs:
        return None
    mins = [lo for lo, _ in pairs]
    maxes = [hi for _, hi in pairs]
    return min(mins), max(maxes), len(pairs)


def quoted_list_pricing_basis(impacts: Sequence[Impact]) -> Optional[str]:
    """Corpus-wide list pricing, in the live panel's own words — the exact
    prose `report.py`'s `_list_pricing`/`_findings_section` already produce
    for the exported document, so the two surfaces never disagree.

    UNCONDITIONAL, UNLIKE THE COMMITTED-MONEY BASIS ABOVE. Money toward a
    target only exists to answer a target somebody named, so it is computed
    inside `resolve_recommendation_count`'s money-target branch and is silent
    on every other run. List pricing is not a property of one theme or one
    goal-dollar-target — `report.py`'s own words: "it is what the product
    costs, and it turns up wherever pricing was discussed" — so this is
    called every run, gated on nothing but whether any finding carries
    pricing units at all.

    READS `Impact.native_units`, not the dict shape `report.py`'s
    `_list_pricing` reads off a stored finding row. Same data, two different
    shapes at two different pipeline stages: this runs on the frozen
    `Impact` objects `resolve_recommendation_count` already takes, before
    anything is serialised for storage.

    ONLY THE TWO EXTREMES, NEVER A SUM — the same non-additivity rule
    `_list_pricing` documents: a $30,000 tier quoted to sixteen accounts is
    sixteen genuine mentions of one rate-card entry, not $480,000 of
    anything. `aggregate_price_range` is the one place that rule is coded.

    `None` when no impact in the sequence carries list-pricing units,
    mirroring `_list_pricing`'s own `None` return so a caller can skip
    cleanly.
    """
    pairs: list[tuple[float, float]] = []
    for imp in impacts:
        lo = imp.native_units.get("commercial_list_price_min")
        hi = imp.native_units.get("commercial_list_price_max")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            pairs.append((float(lo), float(hi)))
    aggregated = aggregate_price_range(pairs)
    if aggregated is None:
        return None
    low, high, carrying = aggregated
    span = f"${low:,.0f}" if low == high else f"${low:,.0f}–${high:,.0f}"
    # SAYS HOW MANY IT SPEAKS FOR, same rule as the committed-money basis
    # above and `_list_pricing`'s own paragraph: a hoisted sentence that
    # overstates its own scope is the exact failure this whole feature
    # exists to correct.
    where = (
        "one finding below" if carrying == 1 else f"{carrying} of the findings below"
    )
    return (
        f"List pricing was quoted in {where}. {span}. "
        f"This is what was quoted, not what was agreed — the same price "
        f"offered to several accounts is one rate card, so these are never "
        f"added together or added to any figure above."
    )


def _quoted_money_toward_target(
    ranked_impacts: Sequence[Impact], target: float, *, max_count: int,
) -> Optional["RecommendationCount"]:
    """Sum the figures people ACTUALLY STATED toward a money target, in rank
    order. Returns `None` when the corpus holds no quoted figures at all, so
    the caller's existing refusal stays exactly as it was.

    THE LANGUAGE IS THE DELIVERABLE HERE, NOT THE ARITHMETIC. Summing real
    quoted values is legitimate; extrapolating from them is forbidden. So
    every sentence this produces says what it is — figures people stated,
    totalled — and none of them says "we expect", "projected", or anything
    that would let a reader take a number nobody said out of this report.
    The gap to a target is never closed by inference: if the quoted figures
    fall short, that is what it says.

    DEDUPLICATED ACROSS FINDINGS, NOT JUST WITHIN THEM. Each finding's own
    sum is already deduplicated, but clustering keys on subject, so the same
    deal described by two different signals can land in two different
    findings — a renewal figure legitimately appears under both a pricing
    theme and a churn theme. Adding the findings' totals would then count
    that money twice, against a target where double-counting is the
    difference between "covered" and "half covered". So the identities are
    replayed here and the same rule applied one more time. This is only
    possible because the figures travel as identities rather than as a
    single number, and only correct because they were deduplicated within a
    finding first.
    """
    # COMMITTED MONEY ONLY, and this is the load-bearing line of the whole
    # function. A list price quoted to sixteen accounts is sixteen genuine
    # mentions of one rate-card entry — not duplicates, so deduplication
    # never touched them, and not $480,000 of anything either. Summing was
    # the wrong OPERATION for that population, so it is excluded here rather
    # than deduplicated harder. See `types.GroundedFigure.committed`.
    #
    # The consequence is intended: on a corpus that is mostly rate card, the
    # committed total is small and a named target will often not be reached.
    # That is the honest answer, and the shortfall wording already carries
    # it.
    # EVERY COMMITTED FIGURE, WITH NO EARLY EXIT — and that is the fix for a
    # real over-claim, not an optimisation removed.
    #
    # This loop used to `break` as soon as the running total crossed the
    # target, and the sentence built from it said the money was "stated in
    # this corpus". Those are two different quantities. On a live run the
    # first finding carried $150,000 against a $100,000 target, the loop
    # stopped there, and the report claimed corpus scope for one finding's
    # subtotal — while three further findings carried another $48,000 that
    # the sentence implicitly denied existed.
    #
    # The wording and the value came from different places, which is exactly
    # how it drifted. Now there is ONE number: the corpus total. How many
    # findings it took to reach the target is tracked separately, and is a
    # count, never a sum.
    # DEDUPLICATED ACROSS FINDINGS THE SAME WAY IT IS WITHIN ONE.
    #
    # Exact-identity matching was already here, and it was half the rule. The
    # other half — an anonymous amount already attributed to some account is
    # the same money, seen once with its customer named and once without —
    # existed only inside a finding. Across findings it did not, so twelve
    # rows describing eight distinct commercial events summed as twelve: one
    # $10,000 payment counted twice, one $9,000 quote twice, one $5,000 PoC
    # three times. A 7% inflation of a client-facing total.
    #
    # Two passes, because the rule needs to know every attributed amount
    # before it can judge an anonymous one — a single pass would keep or drop
    # depending on which finding happened to rank first.
    attributed_amounts = {
        figure.amount
        for imp in ranked_impacts
        for figure in imp.grounded_figures
        if figure.committed and figure.account_key
    }
    seen: set[tuple[str, float]] = set()
    counted: list[GroundedFigure] = []
    findings_with_money = 0
    findings_needed = 0
    running = 0.0
    for imp in ranked_impacts:
        contributed = False
        for figure in imp.grounded_figures:
            if not figure.committed:
                continue
            if not figure.account_key and figure.amount in attributed_amounts:
                # The same money, in a row that did not name the customer.
                continue
            identity = (figure.account_key, figure.amount)
            if identity in seen:
                continue
            seen.add(identity)
            counted.append(figure)
            running += figure.amount
            contributed = True
        if contributed:
            findings_with_money += 1
            if not findings_needed and running >= target:
                findings_needed = findings_with_money

    if not counted:
        return None

    # THE COUNT IS "how many findings it took", never "how many there are".
    n = max(1, min(findings_needed or findings_with_money or 1, max_count))
    named_accounts = len({f.account_key for f in counted if f.account_key})
    reached = running >= target
    target_text = _money_phrase(target)
    total_text = _money_phrase(running)
    across = (
        f" across {named_accounts} named account"
        f"{'' if named_accounts == 1 else 's'}"
        if named_accounts else ""
    )

    derived_total = sum(f.amount for f in counted if f.derived)
    verified_total = running - derived_total
    figures_text = _figures_phrase(counted)

    # ONE NUMBER, ONE SCOPE WORD, USED BY EVERY BRANCH.
    #
    # `total_text` is the corpus total and nothing else, and every sentence
    # below says "in this corpus" about it. The previous version had one
    # branch saying "on the top finding alone" over a value that had just
    # become corpus-wide — the wording and the number came from different
    # places, which is how the over-claim appeared in the first place. The
    # reach of the number is now a property of the number, not a choice each
    # sentence makes.
    #
    # Statements about how FEW findings were needed are made about the
    # COUNT, which is a count, never a sum.
    scope = "in this corpus"

    if reached and not verified_total:
        # NOTHING HERE IS VERIFIED, SO NOTHING HERE SAYS "MEETS".
        #
        # An earlier revision let derived figures declare a target met and
        # then retracted it one sentence later ("…which meets it on its own.
        # … Without those, the quoted figures total $0"). Real output showed
        # why that fails: "which meets it" is the sentence a reader
        # remembers, and the retraction is the one they skim. Asserting and
        # withdrawing a claim in the same paragraph is worse than either
        # half alone, so when NOT ONE figure is matched to a verified quote
        # the strong verb is not available at all.
        #
        # The arithmetic is unchanged and every figure still counts — this
        # corpus is entirely derived today, so excluding them would switch
        # the feature off. What changes is the strength of the claim, and
        # the sentence names what would actually settle it rather than
        # leaving the reader with a number to discount by an unknown amount.
        basis = (
            f"you named a target of {target_text}; committed figures {scope} "
            f"total {total_text}{across} ({figures_text}), enough to reach "
            f"it — but not one of them is matched to a verified quote. Every "
            f"figure here was read back from a written summary, so your "
            f"target is reachable on unverified figures rather than met. "
            f"Checking them against the source text they were summarised "
            f"from is what would settle it."
        )
    elif reached:
        # SAID PLAINLY RATHER THAN PADDED, and said about the COUNT. One
        # finding covering the whole target is a strong claim resting
        # entirely on the accuracy of the figures behind it, so the figures
        # are shown — a reader who can see "$60,000 + $50,000" can judge it
        # in a way that "$110,000" does not allow. What must not happen is
        # the earlier mistake of describing the corpus total as belonging to
        # that one finding.
        enough = (
            "the top finding carries enough on its own"
            if findings_needed == 1 else
            f"the top {n} findings carry enough between them"
        )
        basis = (
            f"you named a target of {target_text}; committed figures {scope} "
            f"total {total_text}{across} ({figures_text}), which meets it — "
            f"{enough}. These are figures people actually stated, added up — "
            f"not a projection."
        )
    else:
        # Not reached. "Quoted" would be the same overstatement in miniature
        # when nothing is verified, so the noun follows the evidence.
        noun = "quoted figure" if verified_total else "stated figure"
        basis = (
            f"you named a target of {target_text}; every {noun} {scope} "
            f"totals {total_text}{across} — short of it. Nothing here is "
            f"projected to close the gap; these are only the figures people "
            f"actually stated."
        )

    if derived_total and verified_total:
        # PROPORTIONATE, AND SPECIFIC ABOUT WHICH RISK. A figure recovered
        # from a written summary came from text that was itself written under
        # a grounding gate, so the exposure is transcription error, not
        # invention — a blanket "this may be unreliable" would overstate it,
        # and saying nothing would understate a materially weaker claim.
        #
        # Only when SOMETHING is verified. With a verified total of zero the
        # branch above has already said so in its own sentence, and repeating
        # it here would read as a footnote to a claim that was never made.
        basis += (
            f" {_money_phrase(derived_total)} of that was read back from "
            f"written summaries rather than matched to a verified quote."
        )
        if reached and verified_total < target:
            # The target is met only because of the hedged figures. Stated
            # outright: "your target is covered" is a much stronger claim
            # than "a figure was named", and a reader must not have to do
            # this subtraction themselves to discover it.
            basis += (
                f" Without those, the quoted figures total "
                f"{_money_phrase(verified_total)}, which would not meet your "
                f"target."
            )

    return RecommendationCount(n, basis, target_unsizeable=not reached)


@dataclass(frozen=True)
class RecommendationCount:
    """How many findings get a deep recommendation, and why — rendered
    verbatim in the report so the count is arguable rather than a bare
    number (AC-2)."""
    count: int
    basis: str
    #: True when the goal named a target the corpus's unit cannot answer —
    #: the normal case per the spike. Carried so a caller can decide whether
    #: to say anything extra, without re-parsing `basis`.
    target_unsizeable: bool = False


#: Neither named nor honoured — the ordinary case, and Apurva's own baseline:
#: "once we pick the top two, then we could just compare them."
DEFAULT_RECOMMENDATION_COUNT = 2


def resolve_recommendation_count(
    goal_text: str,
    ranked_impacts: Sequence[Impact],
    *,
    default: int = DEFAULT_RECOMMENDATION_COUNT,
    max_count: int = MAX_DEEP_RECOMMENDED,
    asked_text: Optional[str] = None,
) -> RecommendationCount:
    """David: "somebody might ask 'I want to get a million dollars' … the
    number of projects really has to be in context of the question and what
    the goal is."

    Three cases, checked in this order because a user who names an exact
    count has said more than one who names a target:

      1. A count is named ("two things I can do") -> honour it, capped at
         `max_count` for reading attention and cost, never at `default`.
      2. A target is named ("get to a million dollars") -> sum
         `ranked_impacts` IN RANK ORDER — the frozen order, never
         re-sorted here — until the running total meets it, or say the
         corpus cannot answer in that unit and fall back to `default`.
      3. Neither -> `default`.

    `ranked_impacts` must already be in the run's own rank order; this
    function only sums and counts, exactly like `rice.py`'s arithmetic, and
    never re-derives an order (I10).

    `asked_text` IS THE FIX FOR A REAL BUG, not a nicety. Chat dispatches the
    planner's EXTRACTED goal as `goal_text` — "reduce churn" out of "What are
    three things I can do to reduce churn?" — which is the right string for
    `goal.resolve` and the KPI-tree match to read (they want the normalised
    metric, and this function must never become the reason that changes). But
    it silently drops a count or target the reader phrased in their own
    words, and nothing downstream could ever see the loss: the report just
    said "no count or target was named" over a goal that plainly named one.
    So the count/target regex reads `asked_text` when the caller has one
    non-blank, and `goal_text` otherwise — a run with no literal text (the
    direct API, or one started before this field existed) is byte-for-byte
    unaffected. Still regex over a sentence, never a model (I2): this is
    WHICH TEXT the same arithmetic reads, not a new decision.
    """
    count_text = asked_text if asked_text and asked_text.strip() else goal_text
    named = _named_count(count_text)
    if named is not None:
        n = max(1, min(named, max_count))
        basis = (
            # Third site with the same singular defect: "the top 1 get a full
            # recommendation" — swept for rather than waiting to be reported.
            f"you asked for {named}, so the top finding gets a full "
            f"recommendation."
            if n == named == 1 else
            f"you asked for {named}, so the top {n} get a full recommendation."
            if n == named else
            f"you asked for {named}, capped at {n} so the recommendation "
            f"stays readable."
        )
        return RecommendationCount(n, basis)

    target = _named_target(count_text)
    if target is not None:
        value, unit = target
        if unit == "dollars":
            # THE MONEY PATH, TRIED FIRST AND ONLY FOR A MONEY TARGET.
            #
            # A finding whose only evidence is a quoted dollar figure has no
            # named account, so it is honestly unsizeable in the corpus's own
            # currency and carries `value = None` (I3). It is therefore absent
            # from `sizeable` below, and `corpus_currency` — read off the
            # first SIZED finding — says "accounts". The result was that a
            # reader asking "how do we drive $100,000 in revenue?" was told
            # this corpus cannot size findings in dollars, while genuinely
            # quoted dollars sat ranked at the top of the same report.
            #
            # Returning `None` here when the corpus holds no quoted figures is
            # what keeps the refusal below fully intact. This does not weaken
            # it; it stops it firing when we actually do have dollars.
            money = _quoted_money_toward_target(
                ranked_impacts, value, max_count=max_count,
            )
            if money is not None:
                return money
        sizeable = [imp for imp in ranked_impacts if imp.value is not None]
        corpus_currency = sizeable[0].currency if sizeable else None
        if not sizeable or not _unit_matches(unit, corpus_currency or ""):
            named_as = (
                f"{value:,.0f} {unit}" if unit != "percent" else f"{value:g}%"
            )
            corpus_desc = (
                f"in {corpus_currency}" if corpus_currency else
                "at all — nothing here could be sized"
            )
            return RecommendationCount(
                default,
                f"you named a target of {named_as}, but this corpus sizes "
                f"findings {corpus_desc}, not in {unit} — summing toward "
                f"your target would say more than the evidence supports, so "
                f"this defaults to the top {default}.",
                target_unsizeable=True,
            )
        running = 0.0
        n = 0
        for imp in sizeable:
            if running >= value:
                break
            running += float(imp.value)  # type: ignore[arg-type]
            n += 1
        n = max(1, min(n or 1, max_count))
        reached = running >= value
        unit_word = unit
        if reached:
            basis = (
                f"you named a target of {value:,.0f} {unit_word}; the top "
                f"{n} finding{'' if n == 1 else 's'} by reach sum"
                f"{'s' if n == 1 else ''} to {running:,.0f} {unit_word}, "
                f"which meets it."
            )
        else:
            basis = (
                f"you named a target of {value:,.0f} {unit_word}; even the "
                f"best-sized finding here only sums to "
                if n == 1 else
                f"you named a target of {value:,.0f} {unit_word}; even the "
                f"{n} best-sized findings here only sum to "
            ) + (
                f"{running:,.0f} {unit_word} — short of your target. There "
                f"is not enough sized evidence in this corpus to reach it."
            )
        return RecommendationCount(n, basis, target_unsizeable=not reached)

    return RecommendationCount(
        default,
        f"no count or target was named in the goal, so the top {default} "
        f"get a full recommendation.",
    )


# ── AC-3: a citation, or the assertion is dropped ─────────────────────────────
#
# The spike measured `changes` — the field that makes a recommendation
# useful — at 7.7-13.6% grounded in the evidence shown to the model, against
# 38-71% for `because`, the only field `lint_claim` inspected. The gap is not
# a lint gap: `_FIGURE`/`_PROMISE`/`lint_claim` check the SHAPE of a
# sentence, never whether its content is in the evidence. This closes that
# specific hole by requiring each `changes` item to name the `claim_id` it
# rests on and restate what that claim says, in words a simple check can
# verify actually overlap with it — the same "grounded or dropped" contract
# `graph/extractor.py`'s `_quote_is_grounded` already applies to a transcript
# quote, re-derived here rather than lifted from anyone's open PR.

#: Below this length a claim's own vocabulary is too thin to require two
#: shared words without making the check impossible to pass honestly.
_GROUNDING_MIN_SHARED_WORDS = 2
_GROUNDING_SHORT_CLAIM_WORDS = 4

_GROUNDING_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "by",
    "with", "this", "that", "these", "those", "is", "are", "was", "were",
    "it", "its", "their", "they", "we", "you", "your", "as", "at", "from",
    "not", "be", "which", "who", "what", "how", "so", "than", "then",
    "into", "over", "across", "about", "will", "can", "may", "our", "one",
})


def _content_words(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(w) > 3 and w not in _GROUNDING_STOPWORDS
    }


def _grounded_in(text: str, claim_assertion: str) -> bool:
    """Does `text` share enough vocabulary with the claim it cites to be a
    restatement of it rather than an invention wearing its `claim_id`?

    A LEXICAL check, not a semantic one — cheap, deterministic, and exactly
    the shape of check this module's other gates already are. It asks less
    of short claims: a three-word claim has no room to share two words with
    anything and still say something, so `_GROUNDING_SHORT_CLAIM_WORDS`
    drops the bar to one shared word rather than failing every short claim
    by construction.
    """
    claim_words = _content_words(claim_assertion)
    if not claim_words:
        return False
    text_words = _content_words(text)
    shared = text_words & claim_words
    threshold = (
        1 if len(claim_words) <= _GROUNDING_SHORT_CLAIM_WORDS
        else _GROUNDING_MIN_SHARED_WORDS
    )
    return len(shared) >= threshold


@dataclass(frozen=True)
class DeepChange:
    """One thing to build, change or fix — and the single claim it rests on.

    `cited_claim` carries the CLAIM'S OWN assertion text, not the model's
    restatement of it: the renderer shows the reader the actual evidence
    rather than a paraphrase that may have drifted from it, which is more
    trustworthy than re-displaying whatever the model wrote back.
    """
    text: str
    claim_id: str
    cited_claim: str


@dataclass(frozen=True)
class DeepRecommendation:
    """The deep pass's output for one finding — AC-1's "what to build,
    change, or fix", not an execution schedule. `comparison` is empty on
    every finding except the one the frozen ranking put first among the deep
    set; it is never generated by the model (I2) — see `_compare`."""
    finding_id: str
    action: str
    because: str
    changes: tuple[DeepChange, ...]
    open_questions: tuple[str, ...] = ()
    what_would_falsify: str = ""
    comparison: str = ""


@dataclass(frozen=True)
class DeepRecommendationResult:
    by_id: dict[str, DeepRecommendation]
    count: RecommendationCount
    #: The finding ids that were CANDIDATES for a full write-up this run —
    #: `top`, before the citation gate (or a failed/malformed call) removed
    #: any of them. Lets a renderer connect a candidate's plain
    #: `recommendation` to the shortfall disclosed in `count.basis`, rather
    #: than leaving it as an unexplained absence next to findings that were
    #: never candidates at all (simply ranked past N). Empty exactly when
    #: nothing was attempted — no findings, or the offline short-circuit.
    attempted_ids: frozenset[str] = frozenset()


DEEP_RECOMMENDATION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["deep_recommendations"],
    "properties": {
        "deep_recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "finding_id", "action", "because", "changes",
                    "open_questions", "what_would_falsify",
                ],
                "properties": {
                    "finding_id": {"type": "string"},
                    "action": {
                        "type": "string", "minLength": 8, "maxLength": 240,
                        "description": "One thing to DO, in the imperative.",
                    },
                    "because": {
                        "type": "string", "minLength": 8, "maxLength": 400,
                        "description": (
                            "Why this action follows FROM THE CLAIMS SHOWN."
                        ),
                    },
                    "changes": {
                        "type": "array", "minItems": 1,
                        "maxItems": MAX_CHANGES_PER_DEEP,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["claim_id", "evidence", "change"],
                            "properties": {
                                "claim_id": {
                                    "type": "string",
                                    "description": (
                                        "The exact claim id, copied from the "
                                        "brackets in front of the claim this "
                                        "change rests on. Never invent one."
                                    ),
                                },
                                "evidence": {
                                    "type": "string", "minLength": 8,
                                    "maxLength": 250,
                                    "description": (
                                        "Restate, in your own words, ONLY "
                                        "what that one claim says. Add "
                                        "nothing beyond it."
                                    ),
                                },
                                "change": {
                                    "type": "string", "minLength": 8,
                                    "maxLength": 300,
                                    "description": (
                                        "What to build, change or fix "
                                        "because of that evidence — a "
                                        "system, a feature, a workflow. Not "
                                        "an outcome and not a schedule."
                                    ),
                                },
                            },
                        },
                    },
                    "open_questions": {
                        "type": "array",
                        "maxItems": MAX_OPEN_QUESTIONS_PER_DEEP,
                        "items": {"type": "string", "maxLength": 300},
                    },
                    "what_would_falsify": {
                        "type": "string", "maxLength": 400,
                        "description": (
                            "What, if you saw it, would make you doubt this "
                            "recommendation. A question, not a promise."
                        ),
                    },
                },
            },
        },
    },
}

_DEEP_SYSTEM = """You write a DEEP recommendation for a small number of \
already-ranked findings — what a reader would need to actually act on one, \
not a one-line suggestion.

You are not scoring, ordering or selecting anything — every finding you are \
shown gets exactly one deep recommendation, and the order they are shown in \
is already final.

Rules, all of them hard:
- `action` and `because` follow the same rules as a short recommendation: an \
imperative action, justified ONLY from the claims shown, no figure, no \
promised outcome.
- Every item in `changes` names the exact `claim_id` (copied from the \
brackets before the claim) it rests on. Never invent a claim id and never \
cite one that was not shown for THIS finding.
- `evidence` in each change is a restatement of what that ONE claim says — \
nothing more. `change` is what to build, change or fix because of it: a \
system, a feature, a workflow — never a dollar figure, a percentage, or a \
promised result.
- Do not describe execution steps, owners or a timeline. That is out of \
scope; a reader wants to know WHAT to build and WHY, not a rollout plan.
- `open_questions` names what you genuinely do not know from the evidence \
shown. `what_would_falsify` is one sentence: what, if the reader saw it, \
would make this recommendation wrong.
- If the claims do not support any real change, return `changes` with one \
item whose `change` says "No specific change this evidence supports" and \
whose `evidence` explains what is missing.

Write for a product manager who has to defend every line to their \
leadership from the same evidence, and nothing else."""


def _deep_claims_for(
    finding: Finding, claims_by_id: dict[str, Claim],
) -> list[tuple[str, str]]:
    """`(claim_id, assertion)` pairs, IDs included — the flat pass's
    `_claims_for` strips them because that prompt never asks for a citation;
    this one does, so the model needs something to copy."""
    out: list[tuple[str, str]] = []
    for cid in finding.claim_ids[:MAX_CLAIMS_PER_FINDING]:
        c = claims_by_id.get(cid)
        if c is None:
            continue
        said = (c.assertion or "").strip()
        if said:
            out.append((cid, said))
    return out


def _deep_input(
    goal_text: str, definition_text: str,
    findings: Sequence[Finding], claims_by_id: dict[str, Claim],
) -> str:
    lines = [
        f"GOAL: {goal_text}",
        f"THE READER'S OWN DEFINITION OF THE METRIC: {definition_text}",
        "",
        "FINDINGS. Each is followed by the claims it rests on, with the "
        "claim's id in brackets. Write one deep recommendation per finding, "
        "grounded only in its own claims, and cite the exact id in brackets "
        "for every change.",
        "",
    ]
    for f in findings:
        lines.append(f"--- finding_id: {f.id}")
        lines.append(f"theme: {f.label or f.statement}")
        for cid, said in _deep_claims_for(f, claims_by_id):
            lines.append(f"  - [{cid}] {said}")
        lines.append("")
    return "\n".join(lines)


def _deep_acceptable(
    rec: dict, finding: Finding, strength: str,
    shown_claim_ids: set[str], claims_by_id: dict[str, Claim],
) -> Optional[DeepRecommendation]:
    """Every check `_acceptable` runs, plus the citation gate AC-3 adds.

    A finding whose `changes` all fail — no valid citation, or cited but not
    grounded in what that claim says — loses the WHOLE deep recommendation,
    not just the empty array: a deep pass with nothing to add is not more
    useful than the flat one it would otherwise supersede, and the flat
    recommendation (computed separately) still renders for that finding.
    """
    action = (rec.get("action") or "").strip()
    because = (rec.get("because") or "").strip()
    if not action or not because:
        return None
    both = f"{action} {because}"
    if _FIGURE.search(both):
        logger.info("crucible: dropped a deep recommendation quoting a figure")
        return None
    if _PROMISE.search(both):
        logger.info("crucible: dropped a deep recommendation promising an outcome")
        return None
    if not lint_claim(action, strength).ok or not lint_claim(because, strength).ok:
        logger.info("crucible: dropped a deep recommendation that failed the lint")
        return None

    changes: list[DeepChange] = []
    for item in rec.get("changes") or []:
        if not isinstance(item, dict):
            continue
        text = (item.get("change") or "").strip()
        evidence = (item.get("evidence") or "").strip()
        claim_id = (item.get("claim_id") or "").strip()
        if not text or not evidence or not claim_id:
            continue
        # UNCITED, OR CITING SOMETHING NEVER SHOWN FOR THIS FINDING. Either
        # way this is not traceable to evidence this finding actually rests
        # on, and AC-3 drops it exactly as `_FIGURE`/`_PROMISE` drop today.
        if claim_id not in shown_claim_ids:
            logger.info(
                "crucible: dropped a change citing a claim not shown for "
                "this finding"
            )
            continue
        claim = claims_by_id.get(claim_id)
        if claim is None:
            continue
        both_change = f"{text} {evidence}"
        if _FIGURE.search(both_change) or _PROMISE.search(both_change):
            continue
        if not lint_claim(text, strength).ok or not lint_claim(evidence, strength).ok:
            continue
        # THE GROUNDEDNESS GATE ITSELF. `evidence` claims to restate what
        # `claim_id` says; verify it actually shares that claim's vocabulary
        # rather than trusting the citation at face value.
        if not _grounded_in(evidence, claim.assertion):
            logger.info(
                "crucible: dropped a change whose evidence did not overlap "
                "the claim it cited"
            )
            continue
        changes.append(DeepChange(
            text=text, claim_id=claim_id, cited_claim=claim.assertion,
        ))
    if not changes:
        return None

    open_qs: list[str] = []
    for q in rec.get("open_questions") or []:
        if not isinstance(q, str):
            continue
        q = q.strip()
        # Never asked to be grounded — a genuine open question, by
        # definition, cannot cite a claim that answers it. Still held to the
        # same figure/promise/lint discipline as every other sentence
        # leaving the engine.
        if q and not _FIGURE.search(q) and not _PROMISE.search(q) and lint_claim(q, strength).ok:
            open_qs.append(q)

    falsify = (rec.get("what_would_falsify") or "").strip()
    if falsify and (_FIGURE.search(falsify) or _PROMISE.search(falsify)
                    or not lint_claim(falsify, strength).ok):
        falsify = ""

    return DeepRecommendation(
        finding_id=finding.id, action=action, because=because,
        changes=tuple(changes), open_questions=tuple(open_qs),
        what_would_falsify=falsify,
    )


#: How a band compares to another, for `_compare` — never rendered as a
#: number (`Confidence.score` is "internal only, NEVER rendered"), only used
#: to pick which of two bands is higher.
_BAND_ORDER = {"low": 0, "medium": 1, "high": 2}

#: What each claim-type bucket is CALLED in the comparison sentence, phrased
#: to follow "this one" / "that one". Keyed on `moscow.type_bucket`'s own
#: return values, so a bucket cannot be described as one thing here and
#: sorted as another there.
_BUCKET_PHRASE = {
    TYPE_BUCKET_BLOCKER:
        "states a blocker — something is stopping an account today",
    TYPE_BUCKET_PREFERENCE:
        "states a preference — something an account asked for",
    TYPE_BUCKET_NEITHER:
        "states neither — it describes the world rather than asking for "
        "or blocking anything",
}


def _compare(
    top: Finding, second: Finding,
    impact_top: Impact, impact_second: Impact,
    confidence_top: Confidence, confidence_second: Confidence,
) -> str:
    """Why the ranking put `top` before `second` — COMPUTED, never narrated
    by a model (I2). Reads only what `_rank` itself reads: adjudication,
    the claim-type bucket, `Impact.value`, and `Confidence.band` — never
    `Confidence.score`, which is internal and never rendered anywhere in
    this codebase.

    Mirrors `pipeline._rank`'s own key order (conflict, then CLAIM-TYPE
    BUCKET, then reach, then confidence) so this sentence can never disagree
    with the ranking it is explaining.

    THE BUCKET TERM IS NOT DECORATION — it is the first discriminator most
    runs actually reach. Before it existed, the reach and band branches both
    fell through on a document-only corpus (nothing there carries a number,
    so `value` is `None` on both sides and the bands tie), and every such run
    printed the same "read it as a position in a list, not as a verdict"
    disclaimer. Which is to say this sentence had never yet printed a real
    reason for its own ordering. "A stated blocker outranks a stated
    preference" is a real reason, and it is the one the ranking used.

    IF `pipeline._rank`'S KEY CHANGES, THIS CHANGES IN THE SAME COMMIT. The
    failure is silent: the ordering stays correct and the sentence explaining
    it stops being true, which is worse than printing no sentence at all.
    """
    top_label = (top.label or top.statement).strip()
    second_label = (second.label or second.statement).strip()

    if top.adjudication == "conflict" and second.adjudication != "conflict":
        return (
            f"Ranked above “{second_label}” because sources "
            f"disagree here — an authoritative conflict is treated as worth "
            f"more than any single-sided claim, regardless of size."
        )

    # THE CLAIM-TYPE BUCKET — second in the key, exactly as in `_rank`. Read
    # through `moscow.type_bucket` rather than re-tested here, so the sentence
    # and the sort cannot disagree about what counts as a blocker.
    bucket_top = type_bucket(top.confidence_inputs.claim_types)
    bucket_second = type_bucket(second.confidence_inputs.claim_types)
    if bucket_top < bucket_second:
        tail = (
            " Something stopping an account is treated as worth more than "
            "something an account would prefer, however many accounts "
            "prefer it."
            if bucket_top == TYPE_BUCKET_BLOCKER
            and bucket_second == TYPE_BUCKET_PREFERENCE
            else ""
        )
        return (
            f"Ranked above “{second_label}” on what each one IS, before "
            f"any question of size: this one {_BUCKET_PHRASE[bucket_top]}, "
            f"and that one {_BUCKET_PHRASE[bucket_second]}.{tail}"
        )

    v1, v2 = impact_top.value, impact_second.value
    if v1 is not None and v2 is None:
        return (
            f"Ranked above “{second_label}” because this one "
            f"could be sized — {v1:g} {impact_top.currency} — and the other "
            f"could not."
        )
    if v1 is not None and v2 is not None and v1 > v2:
        return (
            f"Ranked above “{second_label}” because it touches "
            f"more {impact_top.currency}: {v1:g} against {v2:g}."
        )

    band_top = _BAND_ORDER.get(confidence_top.band, -1)
    band_second = _BAND_ORDER.get(confidence_second.band, -1)
    if band_top > band_second:
        return (
            f"Ranked above “{second_label}” on confidence — "
            f"{confidence_top.band} against {confidence_second.band} — "
            f"since neither could be sized apart."
        )
    return (
        f"Ranked above “{second_label}” by the run's own scoring. "
        f"The two are close on what this report shows — reach and "
        f"confidence band are tied or both unmeasured — so the order here "
        f"rests on a finer signal this report does not print; read it as a "
        f"position in a list, not as a verdict on which matters more."
    )


def build_deep_recommendations(
    *,
    enterprise_id: str,
    goal_text: str,
    definition_text: str,
    findings: Sequence[Finding],
    impacts: Sequence[Impact],
    confidences: Sequence[Confidence],
    claims: Sequence[Claim],
    asked_text: Optional[str] = None,
) -> DeepRecommendationResult:
    """The deep pass: AC-1's "what to build, change, or fix" for the top of
    the ranking, sized by AC-2's count arithmetic — for `findings` that
    already bear on the goal, already in rank order (I10: nothing here
    re-sorts them).

    `findings`, `impacts` and `confidences` MUST be the same length and in
    the same positional order, exactly like `pipeline.build_findings`'s own
    three return sequences — the caller is expected to have already zipped
    and filtered them together.

    `asked_text` is passed straight through to `resolve_recommendation_count`
    — see its own docstring. `goal_text` still goes to the model verbatim
    below (`_deep_input`'s `GOAL:` line): the LLM call reasons over the
    normalised goal, exactly as before this field existed.

    TOTAL, like `build_recommendations`: a suggestion layer that failed must
    not cost a reader the findings that succeeded.
    """
    count_info = resolve_recommendation_count(
        goal_text, impacts, asked_text=asked_text,
    )
    n = min(count_info.count, MAX_DEEP_RECOMMENDED, len(findings))
    top = list(findings)[:n]
    if not top or _offline():
        return DeepRecommendationResult(by_id={}, count=count_info)

    # ATTEMPTED, regardless of what happens below — `top` IS the candidate
    # set the moment it is sliced, whether the call that follows succeeds,
    # returns something unusable, or is dropped item-by-item at the citation
    # gate. A renderer uses this to connect a dropped candidate's plain
    # recommendation to the shortfall note below, rather than leaving it as
    # an unexplained absence next to findings that were never candidates at
    # all (ranked past `n`).
    attempted_ids = frozenset(f.id for f in top)

    claims_by_id = {c.id: c for c in claims}
    shown_ids_by_finding: dict[str, set[str]] = {}
    strength_of: dict[str, str] = {}
    for f in top:
        pairs = _deep_claims_for(f, claims_by_id)
        shown_ids_by_finding[f.id] = {cid for cid, _ in pairs}
        strengths = [claims_by_id[c].strength for c in f.claim_ids
                     if c in claims_by_id]
        strength_of[f.id] = min(strengths, key=_STRENGTH_ORDER.index) if strengths else "reported"

    started = time.monotonic()
    kept: dict[str, DeepRecommendation] = {}
    # WHY A SHARED REASON RATHER THAN A PER-ITEM ONE. A call that raises, or
    # returns a shape this module cannot use, fails every item in `top`
    # identically — there is no per-item verdict to report, unlike the
    # citation gate below, which judges each recommendation on its own
    # evidence. Kept separate from "the citation bar" language for exactly
    # that reason: saying a technical failure "did not meet the citation bar"
    # would be a false claim about why nothing survived.
    fail_reason: Optional[str] = None
    try:
        from app.graph.gateway import llm_call

        result = llm_call(
            enterprise_id=enterprise_id,
            agent="crucible",
            purpose="recommend_deep",
            prompt_version="crucible-recommend-deep-v1",
            system=_DEEP_SYSTEM,
            input=_deep_input(goal_text, definition_text, top, claims_by_id),
            json_schema=DEEP_RECOMMENDATION_SCHEMA,
            max_tokens=4000,
        )
        out = result.output
    except Exception:  # noqa: BLE001 — the suggestion layer never kills a run
        logger.exception("crucible: deep recommendation call failed")
        out = None
        fail_reason = "the suggestion pass did not return a result for this run"

    if fail_reason is None and not isinstance(out, dict):
        fail_reason = "the suggestion pass did not return a result for this run"

    if fail_reason is None:
        if time.monotonic() - started > DEADLINE_SECONDS:
            logger.warning("crucible: deep recommendations exceeded their deadline")

        by_finding_id = {f.id: f for f in top}
        for rec in out.get("deep_recommendations") or []:
            if not isinstance(rec, dict):
                continue
            f = by_finding_id.get((rec.get("finding_id") or "").strip())
            if f is None:
                continue
            ok = _deep_acceptable(
                rec, f, strength_of.get(f.id, "reported"),
                shown_ids_by_finding.get(f.id, set()), claims_by_id,
            )
            if ok is not None:
                kept[f.id] = ok

        # THE COMPARISON. Only between the two HIGHEST-RANKED findings that
        # actually kept a deep recommendation — a comparison against a
        # finding whose own deep pass was entirely dropped would be
        # comparing against nothing the reader can see. Attached to the
        # higher-ranked of the pair, which is the one Apurva described
        # comparing ("once we pick the top two, we could just compare
        # them").
        ranked_kept = [f for f in top if f.id in kept]
        if len(ranked_kept) >= 2:
            first, second = ranked_kept[0], ranked_kept[1]
            i1, i2 = findings.index(first), findings.index(second)
            comparison = _compare(
                first, second, impacts[i1], impacts[i2],
                confidences[i1], confidences[i2],
            )
            kept[first.id] = replace(kept[first.id], comparison=comparison)

    # THE BASIS SENTENCE MUST SAY WHAT SURVIVED, NOT ONLY WHAT WAS PROMISED —
    # AND THIS RUNS FOR EVERY REASON THE COUNT CAN FALL SHORT, NOT ONLY THE
    # CITATION GATE. `count_info.basis` was written by
    # `resolve_recommendation_count` BEFORE this call, from the goal's own
    # ask alone — it has no way to know that `_deep_acceptable`'s citation
    # gate (a documented, deliberate check, not a bug) is about to drop some
    # of `top`, OR that the call itself is about to fail outright. Earlier,
    # only the citation-gate path reached this far — a raised exception or a
    # malformed response returned straight from inside the `try` above,
    # `by_id={}`, with `count_info.basis` NEVER corrected, so a total
    # failure (0 of N) was the ONE case with LESS disclosure than a partial
    # citation-gate shortfall, not more. Left as-is, a report can promise
    # "the top 2 get a full recommendation" over one write-up or zero, which
    # reads as an error when it is really two true, unconnected sentences:
    # how many the goal asked for, and how many the evidence supported.
    # Corrected HERE, once, so this module's own `resolve_recommendation_
    # count`, the citation gate, and a total failure never disagree about
    # which count they printed.
    if len(kept) < n:
        # SINGULAR IS NOT A COSMETIC CASE HERE. With one candidate these
        # sentences read "None of the 1 met the citation bar … still stands
        # for each", which is the shape that makes a reader distrust every
        # other number on the page. A count of one is the COMMON case on a
        # corpus with few sizeable findings, not a rare edge.
        one = n == 1
        if fail_reason is not None:
            tail = (
                "the one finding is not shown below — the flat "
                "recommendation above still stands for it."
                if one else
                f"none of the {n} are shown below — the flat recommendation "
                f"above still stands for each."
            )
            gate_note = f" {fail_reason[0].upper()}{fail_reason[1:]}, so {tail}"
        elif not kept:
            gate_note = (
                " The one finding did not meet the citation bar for a full "
                "recommendation, so it is not shown below — the flat "
                "recommendation above still stands for it."
                if one else
                f" None of the {n} met the citation bar for a full "
                f"recommendation, so none are shown below — the flat "
                f"recommendation above still stands for each."
            )
        elif len(kept) == 1:
            gate_note = (
                f" Only 1 of the {n} met the citation bar for a full "
                f"recommendation and is shown below."
            )
        else:
            gate_note = (
                f" Only {len(kept)} of the {n} met the citation bar for a "
                f"full recommendation and are shown below."
            )
        count_info = replace(count_info, basis=count_info.basis + gate_note)

    return DeepRecommendationResult(
        by_id=kept, count=count_info, attempted_ids=attempted_ids,
    )


# ── ONE RECOMMENDATION FOR THE WHOLE DOCUMENT ─────────────────────────────────
#
# David's reference memo names ONE recommendation for the whole memo
# ("Option 1"). `build_deep_recommendations` above still writes one full
# write-up PER finding — that detail stays, it is not replaced — but nothing
# combined them into the single top-line answer the memo actually opens with.
#
# THIS IS A NARRATION PASS, NOT A SECOND RANKING (I2). `_DEEP_SYSTEM` already
# says it once, for the per-finding pass: "You are not scoring, ordering or
# selecting anything — every finding you are shown gets exactly one deep
# recommendation, and the order they are shown in is already final." The same
# boundary is restated in `_SYNTHESIS_SYSTEM` below, because this call reads
# the per-finding recommendations that pass already produced and frozen-ranked
# — never the findings, never the scores — and it narrates them into one
# recommendation. It cannot re-rank what it is never shown a rank to change.
#
# THE SAME ANTI-FABRICATION GATE, REUSED RATHER THAN REBUILT. Every claim this
# pass makes must trace to a `claim_id` one of the per-finding deep
# recommendations it is synthesizing ALREADY cited — `_synthesis_acceptable`
# below reuses `_grounded_in` (the exact lexical-overlap check
# `_deep_acceptable` uses) and `lint_claim`, rather than inventing a second
# citation mechanism for the same job.

#: `citations[]` entries the synthesis may make. Bounded for the same
#: reading-limit reason as `MAX_CHANGES_PER_DEEP`: a "single recommendation"
#: that cites ten things reads as a list wearing a singular's clothes.
MAX_SYNTHESIS_CITATIONS = 6


@dataclass(frozen=True)
class SynthesizedCitation:
    """One claim the synthesis rests on — always one a per-finding deep
    recommendation already cited (see `build_synthesized_recommendation`'s
    `allowed_claim_ids` gate). `cited_claim` carries the CLAIM'S OWN assertion
    text, exactly like `DeepChange.cited_claim`, so the renderer shows the
    reader the actual evidence rather than a paraphrase that may have drifted
    from it."""
    claim_id: str
    evidence: str
    cited_claim: str


@dataclass(frozen=True)
class SynthesizedRecommendation:
    """The single, top-line recommendation for the whole report — narrates
    and combines the deep, per-finding recommendations `build_deep_
    recommendations` already produced. Additive, never a replacement: the
    per-finding write-ups still render exactly as they did before this
    existed.

    `action` is NOT authored here — it is the rank-1 kept deep
    recommendation's own action, copied verbatim by
    `build_synthesized_recommendation`. Choosing what the single
    recommendation IS is a decision, and I2 leaves decisions to the frozen
    ranking, never to a model."""
    action: str
    because: str
    citations: tuple[SynthesizedCitation, ...]


# NO `action` FIELD, DELIBERATELY. The action is fixed by the frozen ranking
# (it is the rank-1 kept recommendation's own action, copied verbatim) and is
# never authored by a model — picking what the single recommendation is would
# be a decision, and I2 does not let a model return one. Leaving `action` out
# of the schema entirely, rather than instructing against it in the prompt,
# makes emitting one structurally impossible instead of merely discouraged.
SYNTHESIS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["because", "citations"],
    "properties": {
        "because": {
            "type": "string", "minLength": 8, "maxLength": 600,
            "description": (
                "Why the FIXED action shown to you is the single "
                "recommendation, argued ONLY from what the recommendations "
                "shown already said. Cite what they said, not new reasoning "
                "of your own."
            ),
        },
        "citations": {
            "type": "array", "minItems": 1, "maxItems": MAX_SYNTHESIS_CITATIONS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["claim_id", "evidence"],
                "properties": {
                    "claim_id": {
                        "type": "string",
                        "description": (
                            "The exact claim id, copied from the brackets in "
                            "front of a claim already cited below. Never "
                            "invent one and never cite one not shown."
                        ),
                    },
                    "evidence": {
                        "type": "string", "minLength": 8, "maxLength": 250,
                        "description": (
                            "Restate, in your own words, ONLY what that one "
                            "claim says. Add nothing beyond it."
                        ),
                    },
                },
            },
        },
    },
}

_SYNTHESIS_SYSTEM = """You read a small set of already-written, already-\
ranked recommendations — one per finding, shown in their final rank order — \
and write the supporting prose for ONE recommendation that stands for the \
whole report.

THE ACTION IS ALREADY FIXED. You are shown it verbatim: it is the rank-1 \
recommendation's own action, set by frozen, deterministic ranking. You do \
not write it, restate it as your own, replace it, soften it, or propose a \
different one — you write only the prose that supports it. Deciding what the \
single recommendation is would be a decision, and decisions are not yours to \
return.

You are not scoring, ordering or selecting anything — every recommendation \
you are shown has already been decided by that same ranking, and the order \
they are shown in is already final. Your job is to narrate why the fixed \
action is the one that carries the report, drawing on what the lower-ranked \
recommendations already said, never to re-rank, re-score, or choose which \
one matters most.

Rules, all of them hard:
- `because` follows the same rules as the recommendations shown: justified \
ONLY from what those recommendations already say, no figure, no promised \
outcome.
- Every item in `citations` names the exact claim id (copied from the \
brackets before a claim already cited below) it rests on. Never invent a \
claim id and never cite one that was not already cited by one of the \
recommendations shown.
- `evidence` in each citation is a restatement of what that ONE claim says — \
nothing more.
- Do not introduce a new priority, a new action, or a new justification that \
none of the recommendations shown already made. You are combining what is \
already there into the case for the fixed action, not adding to it.

Write for a product manager who has to defend this single recommendation to \
their leadership from the same evidence, and nothing else."""


def _synthesis_input(
    goal_text: str, definition_text: str,
    ranked: Sequence[tuple[Finding, DeepRecommendation]],
    bound_action: str,
) -> str:
    lines = [
        f"GOAL: {goal_text}",
        f"THE READER'S OWN DEFINITION OF THE METRIC: {definition_text}",
        "",
        # The fixed action goes in first and is named as fixed, so the only
        # thing left to write is the case for it.
        f"THE ACTION — ALREADY FIXED BY RANK, NOT YOURS TO WRITE: {bound_action}",
        "",
        "RECOMMENDATIONS, ALREADY RANKED AND WRITTEN — do not reorder or "
        "re-select them. Write the `because` that makes the case for the "
        "fixed action above, drawing only on these and citing only the claim "
        "ids already shown in brackets below.",
        "",
    ]
    for rank, (f, rec) in enumerate(ranked, start=1):
        label = (f.label or f.statement).strip()
        lines.append(f"--- rank {rank}: finding_id: {f.id} — {label}")
        lines.append(f"action: {rec.action}")
        lines.append(f"because: {rec.because}")
        for c in rec.changes:
            lines.append(f"  - [{c.claim_id}] {c.cited_claim}")
        lines.append("")
    return "\n".join(lines)


def _synthesis_acceptable(
    rec: dict, strength: str, allowed_claim_ids: set[str],
    claims_by_id: dict[str, Claim], *, bound_action: str,
) -> Optional[SynthesizedRecommendation]:
    """Every check `_deep_acceptable` runs on `action`/`because`, plus the
    same citation gate, restricted to the claim ids the per-finding
    recommendations being synthesized ALREADY cited — never the finding's
    full claim set, and never a claim id this call invents.

    `bound_action` is the action, full stop: the rank-1 kept recommendation's
    own wording, passed in by the caller. Anything the model may have put in
    `rec["action"]` is ignored outright rather than merged or preferred —
    the schema does not ask for one, and if one arrives anyway it must not be
    able to reach a reader, because that would be the model deciding what the
    recommendation is (I2).

    A synthesis whose citations all fail — no valid claim id, or cited but
    not grounded in what that claim says — is dropped entirely, the same
    "all or nothing" rule `_deep_acceptable` applies to a deep
    recommendation: a synthesis with nothing it can actually stand behind is
    not more useful than no synthesis at all, and the per-finding
    recommendations (computed separately) still render regardless.
    """
    action = bound_action.strip()
    because = (rec.get("because") or "").strip()
    if not action or not because:
        return None
    both = f"{action} {because}"
    if _FIGURE.search(both):
        logger.info("crucible: dropped a synthesized recommendation quoting a figure")
        return None
    if _PROMISE.search(both):
        logger.info(
            "crucible: dropped a synthesized recommendation promising an outcome"
        )
        return None
    if not lint_claim(action, strength).ok or not lint_claim(because, strength).ok:
        logger.info(
            "crucible: dropped a synthesized recommendation that failed the lint"
        )
        return None

    citations: list[SynthesizedCitation] = []
    for item in rec.get("citations") or []:
        if not isinstance(item, dict):
            continue
        evidence = (item.get("evidence") or "").strip()
        claim_id = (item.get("claim_id") or "").strip()
        if not evidence or not claim_id:
            continue
        # THE SAME GATE `_deep_acceptable` APPLIES TO `shown_claim_ids`, ONE
        # LEVEL UP: a claim id this call cites must be one a per-finding deep
        # recommendation already cited, not merely one that was shown to the
        # model for some finding. Citing something never cited by any of the
        # recommendations being synthesized would be a claim this synthesis
        # is making on its own, which is exactly what it is not allowed to do.
        if claim_id not in allowed_claim_ids:
            logger.info(
                "crucible: dropped a synthesis citation not already cited by "
                "a per-finding recommendation"
            )
            continue
        claim = claims_by_id.get(claim_id)
        if claim is None:
            continue
        if _FIGURE.search(evidence) or _PROMISE.search(evidence):
            continue
        if not lint_claim(evidence, strength).ok:
            continue
        if not _grounded_in(evidence, claim.assertion):
            logger.info(
                "crucible: dropped a synthesis citation whose evidence did "
                "not overlap the claim it cited"
            )
            continue
        citations.append(SynthesizedCitation(
            claim_id=claim_id, evidence=evidence, cited_claim=claim.assertion,
        ))
    if not citations:
        return None

    return SynthesizedRecommendation(
        action=action, because=because, citations=tuple(citations),
    )


def build_synthesized_recommendation(
    *,
    enterprise_id: str,
    goal_text: str,
    definition_text: str,
    findings: Sequence[Finding],
    deep_by_id: dict[str, DeepRecommendation],
    claims: Sequence[Claim],
) -> Optional[SynthesizedRecommendation]:
    """One top-line recommendation, synthesized across the deep per-finding
    recommendations that survived `build_deep_recommendations` — or `None`
    when there is nothing to synthesize, or the call/citation gate produced
    nothing usable.

    `findings` MUST be in the run's own rank order (I10) — the same contract
    `build_deep_recommendations` takes; this reads only the subset whose id
    is a key in `deep_by_id`, in that order, and never re-sorts. That order
    is load-bearing twice over now: it fixes which recommendation is rank 1,
    and rank 1's action IS the action returned here, copied verbatim. The
    model writes the prose and the citations only; it is never asked what the
    recommendation should be, because that is a decision and I2 keeps
    decisions in the deterministic ranking (see `SYNTHESIS_SCHEMA`, which has
    no `action` field at all).

    ONLY WHEN THERE IS MORE THAN ONE KEPT DEEP RECOMMENDATION. With exactly
    one, that recommendation already IS "the" recommendation for the report —
    synthesizing a single item with itself would spend a model call to
    restate what already exists rather than combine anything. With zero,
    there is nothing to synthesize.

    TOTAL, like `build_deep_recommendations`: a synthesis that failed must
    not cost a reader the per-finding recommendations that already
    succeeded — this is additive, never a replacement, and the caller wraps
    this the same way it wraps the flat and deep passes (see
    `routes/crucible.py`).
    """
    ranked = [(f, deep_by_id[f.id]) for f in findings if f.id in deep_by_id]
    if len(ranked) <= 1 or _offline():
        return None

    # THE ACTION, DECIDED BEFORE THE CALL. `findings` arrives in the run's
    # own frozen rank order (I10), so `ranked[0]` is rank 1 — its action,
    # verbatim, IS this recommendation's action. The model is asked only for
    # the prose around it. Letting it author the action would make it the
    # thing choosing what the single recommendation is, and I2 does not let a
    # model return a score, a rank, or a decision.
    bound_action = ranked[0][1].action.strip()
    if not bound_action:
        return None

    claims_by_id = {c.id: c for c in claims}
    # THE POOL A SYNTHESIS CITATION MAY DRAW FROM: every claim id one of the
    # per-finding deep recommendations being combined already cited. Nothing
    # wider — not the finding's full claim set, not the corpus — because a
    # citation this call did not inherit from an already-accepted
    # recommendation is a claim it is making on its own (I2's boundary,
    # applied to citations rather than to rank).
    allowed_claim_ids: set[str] = {
        change.claim_id for _, rec in ranked for change in rec.changes
    }
    if not allowed_claim_ids:
        return None
    strengths = [
        claims_by_id[cid].strength for cid in allowed_claim_ids
        if cid in claims_by_id
    ]
    strength = min(strengths, key=_STRENGTH_ORDER.index) if strengths else "reported"

    started = time.monotonic()
    try:
        from app.graph.gateway import llm_call

        result = llm_call(
            enterprise_id=enterprise_id,
            agent="crucible",
            purpose="recommend_synthesis",
            prompt_version="crucible-recommend-synthesis-v1",
            system=_SYNTHESIS_SYSTEM,
            input=_synthesis_input(
                goal_text, definition_text, ranked, bound_action,
            ),
            json_schema=SYNTHESIS_SCHEMA,
            max_tokens=2000,
        )
        out = result.output
    except Exception:  # noqa: BLE001 — the suggestion layer never kills a run
        logger.exception("crucible: synthesized recommendation call failed")
        return None

    if not isinstance(out, dict):
        return None
    if time.monotonic() - started > DEADLINE_SECONDS:
        logger.warning("crucible: synthesized recommendation exceeded its deadline")

    return _synthesis_acceptable(
        out, strength, allowed_claim_ids, claims_by_id,
        bound_action=bound_action,
    )

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
from app.crucible.types import Claim, Confidence, Finding, Impact

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

_NAMED_COUNT_DIGIT = re.compile(
    r"\b(\d{1,3})\s+" + _COUNTABLE_NOUN + r"\b", re.IGNORECASE,
)
_NAMED_COUNT_WORD = re.compile(
    r"\b(" + "|".join(_COUNT_WORDS) + r")\s+" + _COUNTABLE_NOUN + r"\b",
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
    """
    named = _named_count(goal_text)
    if named is not None:
        n = max(1, min(named, max_count))
        basis = (
            f"you asked for {named}, so the top {n} get a full recommendation."
            if n == named else
            f"you asked for {named}, capped at {n} so the recommendation "
            f"stays readable."
        )
        return RecommendationCount(n, basis)

    target = _named_target(goal_text)
    if target is not None:
        value, unit = target
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
                f"{n} findings by reach sum to {running:,.0f} {unit_word}, "
                f"which meets it."
            )
        else:
            basis = (
                f"you named a target of {value:,.0f} {unit_word}; even the "
                f"{n} best-sized findings here only sum to "
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


def _compare(
    top: Finding, second: Finding,
    impact_top: Impact, impact_second: Impact,
    confidence_top: Confidence, confidence_second: Confidence,
) -> str:
    """Why the ranking put `top` before `second` — COMPUTED, never narrated
    by a model (I2). Reads only what `_rank` itself reads: adjudication,
    `Impact.value`, and `Confidence.band` — never `Confidence.score`, which
    is internal and never rendered anywhere in this codebase.

    Mirrors `pipeline._rank`'s own key order (conflict, then reach, then
    confidence) so this sentence can never disagree with the ranking it is
    explaining.
    """
    top_label = (top.label or top.statement).strip()
    second_label = (second.label or second.statement).strip()

    if top.adjudication == "conflict" and second.adjudication != "conflict":
        return (
            f"Ranked above “{second_label}” because sources "
            f"disagree here — an authoritative conflict is treated as worth "
            f"more than any single-sided claim, regardless of size."
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
) -> DeepRecommendationResult:
    """The deep pass: AC-1's "what to build, change, or fix" for the top of
    the ranking, sized by AC-2's count arithmetic — for `findings` that
    already bear on the goal, already in rank order (I10: nothing here
    re-sorts them).

    `findings`, `impacts` and `confidences` MUST be the same length and in
    the same positional order, exactly like `pipeline.build_findings`'s own
    three return sequences — the caller is expected to have already zipped
    and filtered them together.

    TOTAL, like `build_recommendations`: a suggestion layer that failed must
    not cost a reader the findings that succeeded.
    """
    count_info = resolve_recommendation_count(goal_text, impacts)
    n = min(count_info.count, MAX_DEEP_RECOMMENDED, len(findings))
    top = list(findings)[:n]
    if not top or _offline():
        return DeepRecommendationResult(by_id={}, count=count_info)

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
        return DeepRecommendationResult(by_id={}, count=count_info)

    if not isinstance(out, dict):
        return DeepRecommendationResult(by_id={}, count=count_info)
    if time.monotonic() - started > DEADLINE_SECONDS:
        logger.warning("crucible: deep recommendations exceeded their deadline")

    by_finding_id = {f.id: f for f in top}
    kept: dict[str, DeepRecommendation] = {}
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
    # actually kept a deep recommendation — a comparison against a finding
    # whose own deep pass was entirely dropped would be comparing against
    # nothing the reader can see. Attached to the higher-ranked of the pair,
    # which is the one Apurva described comparing ("once we pick the top
    # two, we could just compare them").
    ranked_kept = [f for f in top if f.id in kept]
    if len(ranked_kept) >= 2:
        first, second = ranked_kept[0], ranked_kept[1]
        i1, i2 = findings.index(first), findings.index(second)
        comparison = _compare(
            first, second, impacts[i1], impacts[i2],
            confidences[i1], confidences[i2],
        )
        kept[first.id] = replace(kept[first.id], comparison=comparison)

    return DeepRecommendationResult(by_id=kept, count=count_info)

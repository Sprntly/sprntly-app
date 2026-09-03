"""What KIND of money a recovered figure is — the classifier the phrase
families were an approximation of.

WHY THIS EXISTS, AND WHY NOW. The deterministic sweep recovers a dollar
amount from a paraphrase; the question that decides what may be done with
it — is this a deal, a price, a salary, a target? — is a judgement about
meaning, which a regex cannot make. Five rounds of patterns have been added
to `app.crucible.backfill` chasing that judgement: a market size, a tail of
line items, a competitor's pricing, a revenue target, an ARR bound. Each one
caught the phrasings we had already seen and nothing else.

The corpus then turned out not to be what any of those patterns modelled. It
is sales calls PLUS recruiting calls PLUS internal operations chatter, and
only the first genre existed in the vocabulary. A recruiting conversation is
dense with six-figure amounts — "previous OTE was $260K", "a $150K base
salary plus equity" — that are neither deals nor prices, and one of them was
refused last round only by accident, on the word "approximately". That is
the fifth variant `backfill`'s own header comment names as the signal to
stop adding patterns and build this instead.

WHAT IT DOES NOT DECIDE (I2). The model returns a CATEGORY from a closed
vocabulary and nothing else — never a score, never a rank, never an amount,
never whether a figure is big or small or whether one finding outranks
another. The amount stays the parser's; it is read off the text
deterministically and this module never sees a chance to alter it. What
happens to each category afterwards — which one may be summed, which one
becomes a range, which one is refused — is decided by deterministic code in
`app.crucible.pipeline`, from a fixed table. The model proposes the class;
the code decides the consequence.

WHERE IT RUNS, AND WHY NOT IN THE PIPELINE. `pipeline.py` states that it
contains no LLM call anywhere, and that property is what makes a run
reproducible and `assert_impact_ignores_corroboration` meaningful. So this
runs BEFORE it, as its own stage: claims go in, the same claims come back
carrying `figure_class`, and the pipeline reads that field the way it reads
any other deterministic input.

THE DETERMINISTIC REFUSALS STAY. Ranges and bounds are parser correctness,
not classification — "$100-150k" is not a category error, it is the parser
producing a number the text never stated — so no classifier removes the need
for them, and they run first and independently. The phrase families remain
as a cheap pre-filter: where they already agree with a category they save a
call, but they no longer make the decision.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import replace
from typing import Optional, Sequence

from app.crucible.types import Claim

logger = logging.getLogger(__name__)

#: The closed vocabulary. Every candidate lands in exactly one of these, and
#: `pipeline` maps each to a consequence from a fixed table.
#:
#: CLOSED, AND SHORT, for the same reason the relevance gate's is: a model
#: choosing between eight known words is a very different task from one
#: writing prose, and it is the shape a fast model is reliable at. Anything
#: the model cannot place confidently is `other`, which is refused — an
#: honest "I do not know" that costs a figure, never a guess that invents
#: money.
FIGURE_CLASSES: tuple[str, ...] = (
    # Money a customer has agreed to, or been formally offered: a signed
    # contract, an issued quote, a named deal value. THE ONLY CLASS THAT MAY
    # BE SUMMED.
    "deal_value",
    # A rate card: what the product costs, quoted to whoever asks. Real, and
    # never additive — the same tier quoted to sixteen accounts is one price.
    "list_price",
    # The company's own size or ambition: ARR, revenue targets, valuations.
    "company_scale",
    # Money that belongs to someone else: a competitor's pricing, another
    # vendor's fees, a figure from a third party's business.
    "third_party",
    # Salaries, base pay, OTE, commission, equity. An entire conversation
    # genre in this corpus and the largest single source of wrong figures.
    "compensation",
    # Money deliberately NOT spent — "avoided spending $75K on a booth".
    # Arithmetically a real number and the opposite of revenue.
    "cost_avoidance",
    # Conditional or imagined: "a potential deal size of $200k IF a prospect
    # were to buy". Never happened, may never happen.
    "hypothetical",
    # Anything the model cannot place. Refused.
    "other",
)

#: The one class that may enter the summed committed total.
SUMMABLE_CLASS = "deal_value"
#: The one class that may enter the non-additive pricing range.
RANGE_CLASS = "list_price"

CLASSIFY_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["classifications"],
    "properties": {
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["idx", "figure_class"],
                "properties": {
                    "idx": {
                        "type": "integer",
                        "description": (
                            "The number of the item being classified, as "
                            "shown in the list."
                        ),
                    },
                    "figure_class": {
                        "type": "string",
                        "enum": list(FIGURE_CLASSES),
                        "description": (
                            "Which kind of money this figure is. Exactly one "
                            "of the listed values."
                        ),
                    },
                },
            },
        },
    },
}

_SYSTEM = """You are told what a dollar figure is, in a short paraphrase of \
something someone said on a call, in a message, or in a document. For each one, \
answer ONE question: what KIND of money is this?

You are NOT judging whether the figure is large, important, or worth acting on. \
You are not ranking anything and you are not changing any number. Choose one \
label per item.

deal_value — money a customer has agreed to pay, or been formally offered: a \
signed contract, an issued quote, a stated contract value, a named deal. \
"A $9,000 quote was issued." "Contract value was updated to $2,000." "Two \
accounts nearing closure, together valued at $165k."

list_price — what the product costs, as quoted to whoever asks. A rate card, a \
tier, a subscription rate, a per-seat or per-hour price. "$30,000 for 50 users." \
"The annual subscription starts at $12,000."

company_scale — the company's own size or ambition rather than any single \
transaction: ARR, total revenue, a revenue target, a valuation, money raised. \
"Less than $500K ARR." "A target of $1M by end of year."

third_party — money belonging to someone else: a competitor's pricing, another \
vendor's fees, a figure from a different company's business, or a person's \
track record at a previous employer. "Customers currently pay around $3,000,000 \
to a competing vendor." "Directly influenced $3.7M TCV at their last role."

compensation — pay for people: salary, base, OTE, commission, bonus, equity, \
rates for a role. "Previous OTE was $260K." "A $150K base salary plus equity." \
"A BDR at a $50k base plus $100 per meeting." Recruiting and hiring \
conversations are full of these and none of them is a deal.

cost_avoidance — money deliberately NOT spent, or a saving. "Avoided spending \
$75K on a booth this year."

hypothetical — conditional or imagined money that has not happened: "a potential \
deal size of $200k IF a prospect were to buy", "that would be worth about $80k \
to us".

other — anything you cannot confidently place in one of the above.

Two rules that matter more than getting a close call right:

Prefer `other` when genuinely unsure. A figure labelled `other` is left out of \
the report; a figure wrongly labelled `deal_value` is added into a total a \
reader will quote. Leaving one out understates a number. Putting a wrong one in \
invents money.

Judge what the figure IS, not what it is near. A salary mentioned during a \
sales call is still compensation. A deal value mentioned in a recruiting call \
is still a deal value.

Reply with one classification per item shown, using the item's own number as \
`idx`."""

#: How many candidates are classified in one model call.
#:
#: Same sizing logic as the relevance gate: large enough that the per-call
#: overhead is amortised, small enough that one slow call cannot approach
#: `app.llm._REQUEST_TIMEOUT_S` and be retried into minutes. The output here
#: is far smaller than the relevance gate's — one enum per item, no prose —
#: so the binding constraint is input length, not generation time.
CHUNK = 40

#: A paraphrase longer than this is truncated for the prompt. Classification
#: needs the clause the figure sits in, not the whole document, and an
#: unbounded field is the shape a very long row uses to blow a chunk's input
#: budget on its own.
MAX_TEXT_CHARS = 400


def _offline() -> bool:
    """True when no model should be called.

    A FUNCTION, not an inline check, so a test that WANTS to exercise the
    real path can monkeypatch it — the same convention the relevance gate
    and the deep-recommendation pass use.
    """
    return "pytest" in sys.modules


def _candidates(claims: Sequence[Claim]) -> list[Claim]:
    """The claims worth spending a call on: those carrying a figure that has
    not already been classified.

    ALREADY-CLASSIFIED ROWS ARE NEVER RE-SENT, and that is a correctness
    rule before it is a cost one. See `classify_figures`.

    Everything else in the corpus has no amount to classify, so it is not
    sent either — which is what keeps the cost proportional to figures rather
    than to corpus size.
    """
    return [
        c for c in claims
        if c.magnitude is not None and c.figure_class is None
    ]


def _input(candidates: Sequence[Claim]) -> str:
    """Numbered 1..N — the number IS `idx`, the only handle the model gets
    back to a claim. The claim id never leaves this function."""
    lines = ["FIGURES:"]
    for i, c in enumerate(candidates, start=1):
        text = (c.assertion or "").strip()[:MAX_TEXT_CHARS]
        lines.append(f"{i}. amount: {c.magnitude:,.0f} | said: {text}")
    return "\n".join(lines)


def _classify_chunk(
    *, enterprise_id: str, candidates: Sequence[Claim],
) -> dict[str, str]:
    from app.graph.gateway import llm_call
    from app.llm import FAST_MODEL

    result = llm_call(
        enterprise_id=enterprise_id,
        agent="crucible",
        purpose="classify_commercial_figure",
        prompt_version="crucible-figure-class-v1",
        # HIGH-VOLUME, closed-set, one-enum-per-item output — the shape
        # `FAST_MODEL`'s own charter names. Choosing between eight known
        # words is not the reasoning-depth job `DEFAULT_MODEL` is for.
        model=FAST_MODEL,
        system=_SYSTEM,
        input=_input(candidates),
        json_schema=CLASSIFY_SCHEMA,
        max_tokens=4000,
    )
    out = result.output
    if not isinstance(out, dict):
        return {}
    by_idx = {i: c for i, c in enumerate(candidates, start=1)}
    classified: dict[str, str] = {}
    for item in out.get("classifications") or []:
        if not isinstance(item, dict):
            continue
        claim = by_idx.get(item.get("idx"))
        if claim is None:
            continue
        figure_class = item.get("figure_class")
        # THE CLOSED VOCABULARY IS ENFORCED HERE, not trusted from the
        # schema. A value outside it is dropped rather than passed through,
        # so an unrecognised label can never reach the consequence table and
        # be treated as some default.
        if figure_class in FIGURE_CLASSES:
            classified[claim.id] = figure_class
    return classified


def classify_figures(
    claims: Sequence[Claim], *, enterprise_id: str,
) -> dict[str, str]:
    """`claim id -> figure class` for every UNCLASSIFIED claim carrying a
    figure.

    CLASSIFY ONCE. TAKE THE FIRST SAMPLE AND KEEP IT.
    ------------------------------------------------
    A model call is a draw, not a lookup. Two draws over an identical corpus
    returned `deal_value` counts of 14 and 12, with one $24,000 row flipping
    between them — so the same evidence produced two different committed
    totals, and a reader running the same analysis twice would be shown two
    different numbers. For a figure a client repeats in a meeting that is
    disqualifying, however accurate either draw was.

    So a class is drawn ONCE per row and then stored beside the amount
    (`persist_classes`). Later runs read it back and never re-draw. That
    buys reproducibility, no repeat spend, and per-row auditability — before
    this, nothing recorded what class a row had received, so the only way to
    inspect a past decision was to make a new one and hope it matched.

    NEVER RE-ROLL A STORED CLASS TO CHECK IT. Re-classifying and comparing
    is not verification; it is a second sample, and taking the better of two
    samples is how a number becomes a function of how many times you looked.
    Re-classification is a deliberate operation with its own trigger, never
    a side effect of running an analysis.

    A claim the model did not answer for, or one in a chunk whose call
    failed, is simply absent from the result. That is deliberate and is what
    makes this safe to add: `pipeline` falls back to the deterministic
    phrase families for anything unclassified, so a model outage degrades
    the answer rather than emptying it.
    """
    candidates = _candidates(claims)
    if not candidates or _offline():
        return {}

    classified: dict[str, str] = {}
    for start in range(0, len(candidates), CHUNK):
        chunk = candidates[start:start + CHUNK]
        try:
            classified.update(
                _classify_chunk(enterprise_id=enterprise_id, candidates=chunk)
            )
        except Exception:  # noqa: BLE001 — one bad chunk is not a failed run
            logger.exception(
                "crucible: figure classification chunk failed (offset=%s)", start,
            )

    if classified:
        counts: dict[str, int] = {}
        for value in classified.values():
            counts[value] = counts.get(value, 0) + 1
        # Identifiers and counts only — never the paraphrase, never an
        # account name, never the amounts themselves.
        logger.info(
            "crucible_figure_classes candidates=%s classified=%s by_class=%s",
            len(candidates), len(classified), dict(sorted(counts.items())),
        )
    return classified


def apply_classes(
    claims: Sequence[Claim], classified: dict[str, str],
) -> list[Claim]:
    """Attach each class to its claim, leaving everything else untouched.

    Returns new `Claim` objects rather than mutating: they are frozen, and
    the pipeline downstream depends on that.
    """
    return [
        replace(c, figure_class=classified[c.id]) if c.id in classified else c
        for c in claims
    ]


#: The `kg_signal.properties` key a stored class lives under, beside
#: `amount`/`currency`/`certainty`.
PROPERTY_KEY = "figure_class"


def persist_classes(
    classified: dict[str, str], *, company_id: str,
) -> int:
    """Write each class beside its own signal's amount. Returns how many rows
    were written.

    WHY IT IS STORED AT ALL: so the next run reads a fact instead of taking
    a second sample. See `classify_figures`.

    TENANT-SCOPED ON EVERY WRITE, and the id filter alone is not treated as
    sufficient: the update names `enterprise_id` as well, the same posture
    the backfill sweep uses, because this runs against a shared database and
    a predicate that is merely correct today is not a boundary.

    ONLY THE ONE KEY MOVES. The existing `properties` are read and rewritten
    with `figure_class` set; nothing else in the dict is touched, so an
    ingest-time amount, currency or certainty passes through unchanged.
    """
    if not classified or not company_id:
        return 0
    from app.db.client import require_client

    client = require_client()
    written = 0
    for signal_id, figure_class in classified.items():
        if figure_class not in FIGURE_CLASSES:
            continue
        rows = (
            client.table("kg_signal").select("id, properties")
            .eq("enterprise_id", company_id).eq("id", signal_id)
            .execute().data or []
        )
        if not rows:
            continue
        props = dict(rows[0].get("properties") or {})
        props[PROPERTY_KEY] = figure_class
        (
            client.table("kg_signal").update({"properties": props})
            .eq("enterprise_id", company_id).eq("id", signal_id)
            .execute()
        )
        written += 1
    logger.info(
        "crucible_figure_classes_persisted company_id=%s written=%s",
        company_id, written,
    )
    return written


def estimate_cost(
    claims: Sequence[Claim],
) -> dict[str, int]:
    """Token volumes for classifying this corpus, so a run can be costed
    BEFORE it is paid for rather than explained afterwards.

    Returns counts only. It deliberately does not multiply by a price: rates
    change, a stale constant in here would be quoted as fact, and the
    arithmetic is trivial for whoever holds the current numbers.
    """
    candidates = _candidates(claims)
    calls = (len(candidates) + CHUNK - 1) // CHUNK if candidates else 0
    # ~4 characters per token is the standard rough conversion; this is an
    # ESTIMATE and is named as one everywhere it surfaces.
    system_tokens = len(_SYSTEM) // 4
    body_chars = sum(
        len((c.assertion or "")[:MAX_TEXT_CHARS]) + 32 for c in candidates
    )
    return {
        "candidates": len(candidates),
        "calls": calls,
        "estimated_input_tokens": calls * system_tokens + body_chars // 4,
        # One `{"idx": N, "figure_class": "..."}` per item, ~15 tokens.
        "estimated_output_tokens": len(candidates) * 15,
    }

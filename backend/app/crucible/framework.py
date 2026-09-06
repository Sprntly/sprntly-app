"""Which ranking method fits this tenant's evidence — chosen by CODE, never by
a model. I2: no LLM returns a score, a rank, or a decision about what to do
next, and framework selection is exactly that kind of decision.

WHY THIS EXISTS. `crucible/plan.py` used to pin `framework: str = "RICE"`
unconditionally. Driven over a real 1,275-signal corpus with no analytics or
revenue connected, RICE does not rank badly — every one of 26 kept findings
scored reach=None, effort=None, confidence pinned at 0.50 (measured
independently against the real pipeline). RICE's terms are simply not
derivable from a corpus that carries no number, and a quantitative-looking
table with no quantity in it is worse than an honest ordinal one.

WHAT DECIDES IT. RICE needs a NUMERIC source connected — analytics, revenue or
a measured outcome — because only those source types can make a `magnitude`
claim authoritative (`claims.AUTHORITATIVE_FOR`); without one, Reach and
Impact both render unmeasured on every row. MoSCoW needs only the two claim
types every corpus that names a want or a blocker already carries:
`constraint` ("this is stopping us" → MUST) and `preference` ("we asked for
this" → SHOULD/COULD). Both checks run over the SOURCE INVENTORY at plan
time — before any finding exists — which is the only thing this stage has to
reason over; it is a proxy for what the pipeline will later compute, not the
pipeline itself, and is disclosed as such.

ONLY TWO OF THE DB'S SIX VALUES ARE REACHABLE TODAY. `companies.
prioritization_framework` is CHECK-constrained to `goal-based, rice, wsjf,
moscow, kano, volume-severity` — but `wsjf` needs a cost-of-delay input,
`kano` needs a satisfaction curve, `volume-severity` needs ticket volume and a
severity field, and `goal-based` needs a `sizeable` finding, none of which
this pipeline computes yet. Declaring one of those is honoured as INTENT and
answered honestly — this run falls back to the data-driven choice and says so
as a gap — rather than silently ignored or silently mapped onto RICE.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from app.crucible.plan import NUMERIC_SOURCES, PlanQuestion, SourceInventory

#: The only two rankings this build can actually score. Kept narrow rather
#: than claiming the DB's full six-value vocabulary — see module docstring.
SUPPORTED_FRAMEWORKS: tuple[str, ...] = ("rice", "moscow")

#: Declared values this build recognises but cannot yet score. Declaring one
#: of these falls back to the data-driven choice rather than to RICE by
#: default — RICE is not a safer unknown than MoSCoW, it is just as unable to
#: honour an unsupported framework.
_UNSUPPORTED_DECLARED: tuple[str, ...] = ("goal-based", "wsjf", "kano", "volume-severity")

#: How each framework's code is said in a sentence.
FRAMEWORK_DISPLAY: dict[str, str] = {
    "rice": "RICE",
    "moscow": "MoSCoW",
    "wsjf": "WSJF",
    "kano": "Kano",
    "volume-severity": "volume/severity",
    "goal-based": "a goal-based ranking",
}


def display_name(framework: str) -> str:
    key = (framework or "").strip().lower()
    return FRAMEWORK_DISPLAY.get(key, framework or "")


@dataclass(frozen=True)
class FrameworkChoice:
    """The framework this run will use, and why — shown in the plan and in
    the finished report. `reason` is written as a full sentence so it
    can be rendered directly, without a caller reconstructing prose from
    flags."""
    framework: str                          # "rice" | "moscow"
    reason: str
    #: What the company set at onboarding, verbatim, or None if nothing was
    #: set. Distinct from `framework`: this run may not have been able to
    #: honour it.
    declared: Optional[str] = None
    #: Whether `declared` is the framework actually used.
    honoured_declared: bool = False
    #: How to make the declared framework usable, when it was NOT honoured.
    #: Empty when there was nothing to decline (no declared framework, or the
    #: declared one WAS honoured).
    remedy: str = ""


def select_framework(
    kept: Sequence[SourceInventory],
    declared: Optional[str] = None,
) -> FrameworkChoice:
    """Reason over the inventory of what THIS run will actually read — never
    over an LLM's opinion (I2). Deterministic: same inventory and same
    declared value always choose the same framework.
    """
    present = {s.source_type for s in kept}
    has_numeric = bool(present & set(NUMERIC_SOURCES))
    numeric_named = "/".join(sorted(present & set(NUMERIC_SOURCES)))
    declared_norm = (declared or "").strip().lower() or None

    if declared_norm == "rice":
        if has_numeric:
            return FrameworkChoice(
                "rice",
                "your team set RICE as the prioritisation framework at "
                f"onboarding, and a numeric source is connected — "
                f"{numeric_named} — so reach is countable and RICE has "
                "something to score. It still sizes a theme by how many "
                "accounts it touches, never in money: nothing here carries a "
                "per-account value.",
                declared=declared_norm, honoured_declared=True,
            )
        return FrameworkChoice(
            "moscow",
            "your team set RICE at onboarding, but nothing connected here "
            "carries a number — no analytics, revenue or measured-outcome "
            "source — so RICE's reach and impact would both come back "
            "unmeasured on every row. Ranking by MoSCoW instead: what blocks "
            "an account outranks what it only asks for.",
            declared=declared_norm, honoured_declared=False,
            remedy="connect Amplitude (or your product analytics), Stripe "
                   "or your billing export, or your experiment tool, so RICE "
                   "has a number to size against",
        )
    if declared_norm == "moscow":
        return FrameworkChoice(
            "moscow",
            "your team set MoSCoW as the prioritisation framework at "
            "onboarding: what blocks an account is a MUST, what it asks for "
            "is a SHOULD or COULD.",
            declared=declared_norm, honoured_declared=True,
        )
    if declared_norm in _UNSUPPORTED_DECLARED:
        base = _choose_from_data(has_numeric, numeric_named)
        return FrameworkChoice(
            base.framework,
            f"your team set {display_name(declared_norm)} at onboarding, "
            f"but this run cannot score {display_name(declared_norm)} yet — "
            f"{base.reason}",
            declared=declared_norm, honoured_declared=False,
            remedy=f"{display_name(declared_norm)} needs an input this "
                   f"pipeline does not compute yet — flag it if you need it "
                   f"sooner",
        )
    return _choose_from_data(has_numeric, numeric_named)


def _choose_from_data(has_numeric: bool, numeric_named: str) -> FrameworkChoice:
    if has_numeric:
        return FrameworkChoice(
            "rice",
            f"a numeric source is connected — {numeric_named} — so reach is "
            "countable and RICE has something to score. It still sizes a "
            "theme by how many accounts it touches, never in money: nothing "
            "here carries a per-account value.",
        )
    return FrameworkChoice(
        "moscow",
        "nothing connected here carries a number — no analytics, revenue or "
        "measured-outcome source — so RICE's reach and impact would both "
        "come back unmeasured on every row. Ranking by MoSCoW instead: what "
        "blocks an account outranks what it only asks for.",
    )


#: The cap on plan questions, stated once rather than discovered by counting a
#: list.
#:
#: RAISED FROM THREE, AND THE HARD ASSERT THAT ENFORCED IT IS GONE.
#: The old three were fixed, so three was both the cap and the count and an
#: assertion could not fire. Questions are now DERIVED from what the
#: reconnaissance pass saw (`app.crucible.recon`), and a book with a
#: reconciliation problem, an uncoded field and a weighting choice legitimately
#: has four things worth asking — at which point an `assert` in a request path
#: is a 500 on the plan gate, i.e. the reader loses the whole plan because the
#: run found too much worth clarifying. That is a strictly worse outcome than
#: asking one question fewer.
#:
#: So the cap is now enforced by RANKED TRUNCATION, and the ranking is the
#: point: the questions kept are the ones where a wrong guess silently
#: corrupts a NUMBER. What is dropped is never lost — it is still on the plan
#: as the observation it was derived from, where a reader can see it and a
#: later run can ask it.
MAX_PLAN_QUESTIONS = 5

#: The two that are never derived and never dropped. Neither is framework
#: arithmetic — every ranking still ends at someone deciding, by some date —
#: and neither is in any corpus, so no amount of reconnaissance can infer
#: them. They are also the two the plan gate has always rendered, so cutting
#: one to make room for a derived question would be a visible regression in
#: exchange for a question nothing renders yet.
_DECISION_BOX_IDS = ("decision_owner", "needed_by")


def _decision_box() -> list[PlanQuestion]:
    return [
        PlanQuestion(
            id="decision_owner",
            prompt="Who decides this?",
            why="Named on the decision box so the ranking has an owner, "
                "whichever framework produced it.",
            affects="who the recommendation is addressed to",
            default_if_skipped="the decision box is rendered without an owner",
        ),
        PlanQuestion(
            id="needed_by",
            prompt="When do you need the decision?",
            why="Named on the decision box alongside the owner.",
            affects="the date on the decision box",
            default_if_skipped="the decision box is rendered without a date",
        ),
    ]


def _derived(observations: Sequence[object]) -> list[PlanQuestion]:
    """Questions the evidence itself raised, in the order they matter.

    THE BAR, AND IT IS DELIBERATELY HIGH: ask only where a wrong guess would
    silently corrupt a number AND the answer cannot be inferred from what is
    connected. Everything that fails either half is not a question — it is
    either a default the run should just take and disclose, or a fact the run
    should derive and say it derived.

    That bar is what keeps this from becoming a form. A gate that asks six
    questions gets six blanks; a gate that asks the two whose answers change
    the arithmetic gets answers.

    Typed loosely (`object`) for the same reason `plan.derive_gaps_and_
    promises` types its framework choice loosely: `recon` is imported lazily
    to keep the cheap paths cheap, and duck-typing on `.kind` / `.fields` /
    `.figures` is enough.
    """
    # ONE QUESTION PER ID, KEEPING THE FIRST. `recon` emits up to eight
    # observations of a kind — ticket volume, session volume and NPS volume
    # all diverge from revenue on the same book — and each would raise the
    # identical question with different percentages behind it. Asking a reader
    # the same thing three times is how a gate stops being answered. The
    # observations are severity-ordered with a stable tie-break, so "the
    # first" is the strongest instance and is the same one on every read.
    out: list[PlanQuestion] = []
    seen: set[str] = set()
    for o in observations:
        kind = getattr(o, "kind", "")
        fields = list(getattr(o, "fields", ()) or [])
        figures = dict(getattr(o, "figures", {}) or {})

        if kind == "value_columns_disagree" and len(fields) >= 3:
            base, total, explained = fields[0], fields[1], fields[2]
            out.append(PlanQuestion(
                id="value_column_choice",
                prompt=f"Which figure is your book — {total}, or {base}?",
                why="Every size in the finished document is denominated in "
                    "this. Picking the wrong one is not a rounding error, it "
                    "rescales the whole answer.",
                what_i_saw=(
                    f"`{base}` and `{total}` both read as the account's value "
                    f"and differ on {figures.get('rows_differing', 0):,.0f} of "
                    f"{figures.get('rows_compared', 0):,.0f} rows; the gap is "
                    f"exactly `{explained}`, worth "
                    f"{figures.get('gap_total', 0):,.0f}."
                ),
                affects="every size, and the ranking that follows from them",
                default_if_skipped=(
                    f"`{total}` is used, because it is the complete column, "
                    f"and the document says so"
                ),
                options=(total, base),
            ))
        # `concentration_divergence` DELIBERATELY RAISES NO QUESTION. It used
        # to ask "rank by accounts touched, or by the revenue those accounts
        # carry?" — offering an answer nothing in the engine read. The string
        # `weighting_choice` appeared exactly once in the whole backend, at its
        # own definition, so a reader who chose "Revenue carried" had that
        # answer collected and silently discarded while the question told them
        # it decided "the order of the findings, and which one is recommended".
        #
        # That is the same overclaim as a plan step promising work the run does
        # not do, in the one place a reader is actively asked to participate,
        # and it is worse than a wrong default: a default is a decision the
        # engine owns, while this made the reader believe they owned it.
        #
        # The divergence itself is real and STAYS: the observation is still
        # emitted and `compare_measures_across_groups` still puts it on the
        # plan as evidence. It also now motivates the question that IS wired —
        # see `_weighting_available_note`, which folds these figures in.
        elif kind == "coding_gap" and len(fields) >= 2:
            coded, text = fields[0], fields[1]
            out.append(PlanQuestion(
                id="uncoded_rows_policy",
                prompt=f"Should the rows with no `{coded}` be read from "
                       f"`{text}`, or left out of the count?",
                why="Counting only the coded rows reports a share of your "
                    "feedback as though it were all of it.",
                what_i_saw=(
                    f"`{coded}` is empty on {figures.get('missing', 0):,.0f} "
                    f"of {figures.get('rows', 0):,.0f} rows, and on "
                    f"{figures.get('missing_with_text', 0):,.0f} of those the "
                    f"free text is filled in."
                ),
                affects="how much of your feedback is counted at all",
                default_if_skipped=(
                    "the uncoded rows are left out and the document states how "
                    "many were dropped"
                ),
                options=(f"Read `{text}`", f"Count only coded `{coded}`"),
            ))
    deduped: list[PlanQuestion] = []
    for q in out:
        if q.id in seen:
            continue
        seen.add(q.id)
        deduped.append(q)
    return deduped


def derived_account_value(observations: Sequence[object]) -> tuple[Optional[float], str]:
    """What one account is worth, read off the evidence, and how.

    Returns `(None, "")` when nothing connected carries a per-account annual
    value — in which case the question is still worth asking.
    """
    for o in observations:
        if getattr(o, "kind", "") != "unit_value_derivable":
            continue
        figures = dict(getattr(o, "figures", {}) or {})
        median = figures.get("median")
        if not isinstance(median, (int, float)):
            continue
        fields = list(getattr(o, "fields", ()) or ["", ""])
        column = fields[1] if len(fields) > 1 else ""
        # THE NOTE MUST DISCLAIM AS WELL AS EXPLAIN, BECAUSE IT IS STORED.
        # It is carried onto the plan as `account_value_derived_note` and
        # SERIALISED (`plan.build_plan` -> `RunPlan.to_json`), so it outlives
        # the screen that produced it and is what a stored run and the
        # rendered report read back. It used to end "so a handful of very
        # large accounts do not set the price of a typical one" — a sentence
        # about how the figure is USED, in an engine that does not use it:
        # `weight_by_account_value` is `declared`, every `ImpactInputs`
        # carries `value_per_unit=None`, and `score_impact` returns a count of
        # accounts. Fixing the plan step alone left this copy of the claim
        # standing in the stored JSON.
        return float(median), (
            f"Taken from `{column}` in {getattr(o, 'source', 'your contracts')}, "
            f"which carries a value for each of {figures.get('accounts', 0):.0f} "
            f"accounts. The median is recorded rather than the mean because "
            f"a handful of very large accounts should not stand in for a "
            f"typical one. It is why you are not asked for this number; it "
            f"does not change how anything is sized here, which stays a count "
            f"of the accounts a theme touches."
        )
    return None, ""


#: The two answers to the business-model question, in the order they are
#: offered. Declared once so the prompt the reader sees and the mapping that
#: reads their answer back cannot drift apart — a mismatch there would record
#: an answer nobody gave.
BUSINESS_MODEL_OPTIONS: tuple[str, ...] = (
    "Sales-assisted or enterprise (B2B)",
    "Self-serve or consumer",
)


def _weighting_is_available(observations: Sequence[object]) -> bool:
    """Could this run weight by revenue if it were told to?

    Reads the reconnaissance pass's own verdict figures rather than re-deriving
    them: `recon._observe_priceable_coverage` measured the share and the
    threshold together, and a second comparison here would be a second place
    the bar could be set.
    """
    for o in observations:
        if getattr(o, "kind", "") != "priceable_coverage":
            continue
        figures = dict(getattr(o, "figures", {}) or {})
        share = figures.get("priceable_share")
        threshold = figures.get("threshold")
        if isinstance(share, (int, float)) and isinstance(threshold, (int, float)):
            return share >= threshold
    return False


def _divergence_clause(observations: Sequence[object]) -> str:
    """The strongest motivation for the unit question, in the reader's own
    numbers: their loudest accounts are not their most valuable ones.

    Carried here rather than raised as its own question. `concentration_
    divergence` used to ask the reader to choose a weighting directly and then
    read nothing back; the honest use of the same finding is to explain why the
    question that IS wired matters.
    """
    for o in observations:
        if getattr(o, "kind", "") != "concentration_divergence":
            continue
        f = dict(getattr(o, "figures", {}) or {})
        return (
            f" It matters here: the top {f.get('top_n', 0):.0f} of "
            f"{f.get('groups', 0):.0f} accounts generate "
            f"{f.get('volume_share', 0) * 100:.1f}% of the activity and hold "
            f"{f.get('value_share', 0) * 100:.1f}% of the money, so counting "
            f"and weighting do not put the same themes on top."
        )
    return ""


def _weighting_available_note(observations: Sequence[object]) -> str:
    for o in observations:
        if getattr(o, "kind", "") != "priceable_coverage":
            continue
        figures = dict(getattr(o, "figures", {}) or {})
        return (
            f"Your contracts price "
            f"{figures.get('book_accounts', 0):,.0f} accounts and cover "
            f"{figures.get('priceable_share', 0) * 100:.1f}% of the accounts "
            f"named in your evidence, so weighting is available here — but "
            f"your business model is not recorded, so nothing has decided "
            f"whether to use it."
        ) + _divergence_clause(observations)
    return ""


def questions_for(
    framework: str, observations: Sequence[object] = (),
    #: `value`, `count` or `""` — `plan.business_model_unit`'s output. Empty
    #: means nobody has said, which is the one case worth asking about.
    business_model: str = "",
) -> tuple[PlanQuestion, ...]:
    """What THIS run genuinely needs and cannot derive — batched, asked once,
    never invented if skipped (the gap is carried into the output instead; see
    `plan.derive_gaps_and_promises`).

    WHAT CHANGED: the set used to be a function of the FRAMEWORK alone, which
    meant every RICE run asked the same three things whatever was connected.
    Two of those three (`decision_owner`, `needed_by`) are genuinely
    un-derivable and are still asked unconditionally. The third —
    `account_value` — is frequently sitting in the customer's own contracts,
    and asking for it there is the engine requesting an estimate it can
    already measure, then labelling the reader's guess as an assumption in a
    document whose own data contradicts it. So it is asked only when nothing
    connected can answer it.

    `account_value` REMAINS RICE-ONLY. Asking it under MoSCoW would collect a
    number nothing downstream multiplies, which is the dishonest-ask this
    function exists to avoid.

    `observations` empty reproduces the previous behaviour exactly, which is
    what every caller that has not run a reconnaissance pass gets.
    """
    derived_value, _ = derived_account_value(observations)
    questions: list[PlanQuestion] = []

    # ── THE UNIT, WHEN THE EVIDENCE COULD SUPPORT EITHER ANSWER. ───────────
    #
    # ASKED, NEVER INFERRED, and asked FIRST because it decides what every
    # other number in the document is denominated in. Reading "B2B" off the
    # presence of a contracts spreadsheet is a guess dressed as a fact — a
    # self-serve business with an enterprise tier has exactly the same file —
    # and this is the most consequential thing on the gate to get wrong.
    #
    # ONLY WHEN THE ANSWER WOULD CHANGE SOMETHING. If the book cannot price
    # enough of the corpus to weight anyway, or the business model is already
    # recorded, this is a form field rather than a question, and a gate that
    # asks questions whose answers change nothing stops being answered.
    if not business_model and _weighting_is_available(observations):
        questions.append(PlanQuestion(
            id="business_model",
            prompt="How do you sell — sales-assisted, or self-serve?",
            why="It decides the unit every size in this document is stated "
                "in: the revenue behind a theme, or the number of accounts "
                "that raised it. Those two orderings disagree, and I would "
                "rather ask than assume from the fact that you have a "
                "contracts file.",
            what_i_saw=_weighting_available_note(observations),
            affects="whether themes are ranked by revenue or by accounts "
                    "touched",
            default_if_skipped=(
                "themes are counted, not weighted, and the document says that "
                "is what happened and why"
            ),
            options=BUSINESS_MODEL_OPTIONS,
        ))

    # FIRST, NOT LAST, WHEN IT IS ASKED AT ALL. It was appended after the
    # derived questions and a book with three derived questions truncated it
    # away — losing the only question that feeds a framework's ARITHMETIC to
    # make room for three that adjust it. A derived question changes which
    # column or which weighting; this one decides whether any size can be
    # stated in money at all.
    if (framework or "").strip().lower() == "rice" and derived_value is None:
        questions.append(PlanQuestion(
            id="account_value",
            prompt="What is one account worth to you, per year?",
            why="Turns reach-in-accounts into money — RICE's Reach term, "
                "read in the currency you actually think in.",
            affects="whether sizes can be stated in money at all",
            default_if_skipped="sizes stay in accounts touched",
        ))
    questions.extend(_derived(observations))

    # RANKED TRUNCATION, NOT AN ASSERT. The derived questions are already in
    # the order `_derived` produced them, which is the order the observations
    # were ranked in — severity first. The decision-box pair is appended after
    # the cut so it can never be crowded out by a derived question.
    room = max(0, MAX_PLAN_QUESTIONS - len(_DECISION_BOX_IDS))
    return tuple(questions[:room] + _decision_box())

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
#: list. RICE asks the one thing its own arithmetic needs (`account_value`)
#: plus the two decision-process questions every framework's decision box
#: uses regardless of ranking method (`decision_owner`, `needed_by`). MoSCoW
#: has no dollar arithmetic to feed, so it asks only the two.
MAX_PLAN_QUESTIONS = 3


def questions_for(framework: str) -> tuple[PlanQuestion, ...]:
    """What THIS framework genuinely needs and cannot derive — batched, asked
    once, never invented if skipped (the gap is carried into the output
    instead; see `derive_gaps_and_promises`).

    `account_value` is RICE-specific: it follows the product owner's stated
    reading of the RICE dimensions — reach is how many companies are
    impacted, and impact is read against what a company said a thing was
    worth — and it is the only question here that feeds a framework's ARITHMETIC
    rather than its decision box. Asking it under MoSCoW would collect a
    number nothing downstream multiplies, which is the dishonest-ask this
    function exists to avoid: a run must not solicit an input it will not
    use. `decision_owner`/`needed_by` are not framework math at all — every
    ranking still ends at someone deciding, by some date — so both are asked
    regardless of which framework ranked the findings.
    """
    questions: list[PlanQuestion] = []
    if (framework or "").strip().lower() == "rice":
        questions.append(PlanQuestion(
            id="account_value",
            prompt="What is one account worth to you, per year?",
            why="Turns reach-in-accounts into money — RICE's Reach term, "
                "read in the currency you actually think in.",
        ))
    questions.append(PlanQuestion(
        id="decision_owner",
        prompt="Who decides this?",
        why="Named on the decision box so the ranking has an owner, "
            "whichever framework produced it.",
    ))
    questions.append(PlanQuestion(
        id="needed_by",
        prompt="When do you need the decision?",
        why="Named on the decision box alongside the owner.",
    ))
    assert len(questions) <= MAX_PLAN_QUESTIONS, (
        "the plan-question cap: a set grew past what a reader should be "
        "asked in one batch."
    )
    return tuple(questions)

"""What this goal makes of the evidence — decided in code, narrated by a model.

THE DEFECT THIS EXISTS TO REMOVE. Two runs on the same tenant with the same
twelve attachments, one asking how to drive revenue and one asking how to
reduce churn, produced correctly different DEFINITIONS and then twenty-five
byte-identical steps — including a funnel-shape check on an activation table
under a question about whether accounts stay. That is not a wording problem. It
is structural: the steps are composed from the reconnaissance OBSERVATIONS,
which are computed from the SHAPE of the evidence and never see the goal. A
plan built that way is a plan about the corpus, handed to whoever happened to
ask.

WHAT THIS IS NOT, AND THE DISTINCTION IS THE WHOLE DESIGN. It is not a filter
that matches the goal's words against the step list and drops what does not
hit. That would look fixed and be worse: the plan would present goal-aware
routing the engine is not performing, and the reader would approve a method on
the strength of a keyword match. Every decision here is made over what the
evidence STRUCTURALLY IS — the observation kinds `recon` authored, the roles
`plan.role_for` reads off `claims.AUTHORITATIVE_FOR` — and every one of them is
disclosed with its reason, including the ones that set something aside.

THE SPLIT: DECISIONS IN CODE, PROSE FROM THE MODEL. Nothing in this file calls
a model, and the structure it returns is frozen before the composition prompt
is built. The model is given the decisions and may only narrate them — the same
guardrail `goal_report_chat_edit` puts on an edit that must not quietly remove a
document's own statement of its limits. It returns no disposition, no ranking
and no selection, which is I2 unchanged.

READ FROM THE GOAL, NOT INFERRED ABOUT IT. `classify_goal` is lexical and
deterministic, and it is the same move `goal._direction_of` already makes for
increase-versus-decrease and for the same reason: the reading is recorded, is
shown to the reader before they approve, and defaults to the answer that
changes nothing. A goal it cannot place is `unclassified`, which sets nothing
aside and produces exactly the plan this engine produced before any of this
existed. Being wrong in that direction costs a slightly long plan; being wrong
in the other direction silently drops evidence.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)


# ── THE CLOSED SET OF READINGS ──────────────────────────────────────────────
#
# NAMED FOR A POPULATION, NOT FOR A METRIC. "Churn" and "logo retention" and
# "renewals" are three names for one question about one population — the
# accounts you already have — and it is the POPULATION that decides whether a
# structural finding bears on the answer. A taxonomy of metric names would need
# a new entry per tenant vocabulary; this one has five entries and has not
# needed a sixth.

RETENTION = "retention"
EXPANSION = "expansion"
ACQUISITION = "acquisition"
ACTIVATION = "activation"
EFFICIENCY = "efficiency"
#: A goal about the whole book at once. Not a failure to classify — a real and
#: common reading ("grow revenue"), and one that sets nothing aside because
#: every population is in scope.
BOOK_WIDE = "book_wide"
#: Nothing recognised. Behaves exactly like `BOOK_WIDE` and is a DIFFERENT
#: STATEMENT: "this is about your whole book" and "I could not tell which part
#: of your book this is about" are both honest and only one of them is true.
UNCLASSIFIED = "unclassified"

GOAL_CLASSES: tuple[str, ...] = (
    RETENTION, EXPANSION, ACQUISITION, ACTIVATION, EFFICIENCY,
    BOOK_WIDE, UNCLASSIFIED,
)

#: What each reading means, said as the population it is about. Shown to the
#: reader at the gate, because a classification they cannot see is a decision
#: they cannot disagree with.
GOAL_CLASS_NOTE: Mapping[str, str] = {
    RETENTION: "keeping the accounts you already have",
    EXPANSION: "growing the accounts you already have",
    ACQUISITION: "winning accounts you do not have yet",
    ACTIVATION: "getting a new account to the point where it gets value",
    EFFICIENCY: "getting the same outcome for less effort or cost",
    BOOK_WIDE: "your whole book at once, rather than one part of it",
    UNCLASSIFIED: "something I could not place against one part of your book",
}

#: The words that place a goal. Matched on WORD BOUNDARIES against the
#: definition first and the goal text second — see `classify_goal`.
#:
#: DELIBERATELY SMALL. Every term here is a word a product manager uses for the
#: population itself, not for a symptom of it: "support tickets" is not in the
#: efficiency list, because a support-ticket question is as often a retention
#: question. A term that is ambiguous between two populations is left OUT, and
#: the goal then falls to `unclassified`, which sets nothing aside. The list
#: earns entries by being unable to be wrong, not by covering more goals.
_CLASS_TERMS: Mapping[str, tuple[str, ...]] = {
    RETENTION: (
        "churn", "churned", "churning", "retention", "retain", "retaining",
        "renewal", "renewals", "renew", "attrition", "contraction",
        "downgrade", "downgrades", "cancellation", "cancellations", "lapsed",
    ),
    EXPANSION: (
        "expansion", "upsell", "upsells", "upselling", "cross-sell",
        "crosssell", "cross-sells", "nrr", "net revenue retention",
        "account growth", "seat growth", "upgrade", "upgrades",
    ),
    ACQUISITION: (
        "acquisition", "acquire", "new business", "new logos", "new logo",
        "win rate", "win-rate", "won deals", "lost deals", "win/loss",
        "win-loss", "pipeline", "prospect", "prospects", "leads",
    ),
    ACTIVATION: (
        "activation", "activate", "activated", "onboarding", "onboard",
        "time to value", "time-to-value", "first value", "aha moment",
        "adoption", "ramp",
    ),
    EFFICIENCY: (
        "cost per", "unit cost", "margin", "margins", "efficiency",
        "deflection", "deflect", "handle time", "cost to serve",
        "cost-to-serve", "headcount",
    ),
    BOOK_WIDE: (
        "revenue", "arr", "mrr", "top line", "top-line", "grow the business",
        "bookings", "growth",
    ),
}


def _terms_present(text: str) -> set[str]:
    """Which classes' terms appear in `text`, matched on word boundaries.

    BOUNDARIES RATHER THAN SUBSTRINGS. `"churn" in text` is true of "churned"
    and also of a column called `churn_risk_bucket`, and a classification that
    fires on a column name is reading the evidence rather than the question.
    """
    lowered = f" {' '.join((text or '').lower().split())} "
    if not lowered.strip():
        return set()
    hit: set[str] = set()
    for cls, terms in _CLASS_TERMS.items():
        for term in terms:
            if re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", lowered):
                hit.add(cls)
                break
    return hit


def classify_goal(goal_text: str, definition_text: str = "") -> str:
    """Which part of the book this goal is about. Deterministic, closed set.

    THE DEFINITION FIRST, AND THAT IS NOT AN OPTIMISATION. `definition_text` is
    the sentence the reader is being asked to approve as the meaning of their
    own metric — "accounts lost in the period over accounts held at its start"
    — and it says what the goal is about far more precisely than the goal
    string, which is frequently three words typed into a chat box. Falling back
    to the goal text covers a run whose definition has not been proposed yet.

    AMBIGUITY RESOLVES TOWARDS DOING MORE, NEVER LESS. A text naming two
    populations, or naming the book as a whole, is `BOOK_WIDE`, which sets
    nothing aside. Only an unambiguous single-population reading narrows
    anything, because the cost of the two errors is not symmetric: an
    over-long plan is a plan, and a plan that quietly stopped looking at half
    the evidence is the failure this whole gate exists to prevent.
    """
    for text in (definition_text, goal_text):
        hit = _terms_present(text)
        if not hit:
            continue
        populations = hit - {BOOK_WIDE}
        if BOOK_WIDE in hit or len(populations) != 1:
            return BOOK_WIDE
        return populations.pop()
    return UNCLASSIFIED


# ── WHICH STRUCTURAL FINDINGS BEAR ON WHICH QUESTION ────────────────────────
#
# TWO KINDS ARE ABOUT A POPULATION; THE OTHER EIGHT ARE ABOUT THE CORPUS.
#
# That asymmetry is the reason this table is short enough to defend. Whether
# two columns both claim to be the account's value, whether a coded field was
# left empty, whether the dates are the import clock, how many distinct
# documents a claim rests on — none of those change because the question
# changed. They are facts about whether the evidence can be trusted at all, and
# a run that stopped checking them because the goal was about churn would be
# unsafe in a way no reader could see.
#
# The two that ARE population-shaped are the two `recon` derives from a shape
# rather than from a value: a funnel (adjacent stages holding one number twice)
# and a period grid (cohorts too young to have the window they are being
# divided by). Each is about the dynamics of one population and is genuinely
# silent about the others.
_BEARS_ON: Mapping[str, frozenset[str]] = {
    #: Two adjacent funnel stages that are the same number written twice. It
    #: says a conversion rate across the pair is 100% by construction — which
    #: is about moving through a funnel, and says nothing about whether the
    #: accounts already through it stay.
    "stage_collapse": frozenset({ACQUISITION, ACTIVATION}),
    #: Cohorts whose later periods are zero because those months have not
    #: happened yet. It is a correction to a rate measured over time on a
    #: population you already have.
    "censored_periods": frozenset({RETENTION, EXPANSION}),
}

#: Classes that set nothing aside, whatever the kind. Kept explicit rather than
#: expressed as an absence, because "everything bears on this" is a decision
#: with a reason and not a gap in a table.
_SETS_NOTHING_ASIDE: frozenset[str] = frozenset({BOOK_WIDE, UNCLASSIFIED})


def bears_on(observation_kind: str, goal_class: str) -> bool:
    """Does a finding of this kind bear on a goal of this class?

    True for every kind not in `_BEARS_ON`, which is the safe direction and
    also the honest one: a corpus-integrity finding bears on any question asked
    of that corpus.
    """
    if goal_class in _SETS_NOTHING_ASIDE:
        return True
    allowed = _BEARS_ON.get(observation_kind)
    return True if allowed is None else goal_class in allowed


#: Why a kind was set aside, said as what the check IS and what this question
#: is about — never as "this did not match your goal", which tells the reader
#: only that something matched something.
_SET_ASIDE_BECAUSE: Mapping[str, str] = {
    "stage_collapse": (
        "this is a funnel check: it found two adjacent stages holding the "
        "same number, so any conversion rate across that pair is 100% before "
        "anything is measured. That changes an answer about moving through a "
        "funnel"
    ),
    "censored_periods": (
        "this is a cohort-maturity correction: some cohorts have zeros in "
        "their later periods because those months have not happened yet, so a "
        "rate measured across all of them is wrong. That changes an answer "
        "about a population followed over time"
    ),
}


#: The action phrase the set-aside step would have carried. Kept beside the
#: reasons rather than reconstructed from `planner`, because a set-aside is
#: rendered on a plan whose step for that kind does not exist to copy.
_SET_ASIDE_WHAT: Mapping[str, str] = {
    "stage_collapse": "Check whether two funnel stages are really two steps",
    "censored_periods": "Count a rate only over cohorts old enough to have one",
}


@dataclass(frozen=True)
class SetAside:
    """A step the plan did NOT write, named with the reason it did not.

    NOT A DELETION, AND THE DIFFERENCE IS THE POINT. Hiding an irrelevant step
    makes a shorter plan and a less checkable one — the reader cannot tell a
    considered omission from a check that silently failed, which is exactly the
    confusion `list_cut_candidates` exists to remove at the other end of the
    run. So a set-aside carries what would have been done, why it was not, and
    the observation it would have rested on, and the finding itself stays on
    the plan's `observations` where the reader and the executor can both still
    see it.
    """
    what: str
    why: str
    #: The observation this would have been drawn from. The reader can find it
    #: in the plan's own observation list, which is how "set aside" stays a
    #: statement about the METHOD rather than about the evidence.
    observation: str
    source: str = ""


def set_asides_for(
    report: Any, goal_class: str, *, per_kind: int = 1,
) -> tuple[SetAside, ...]:
    """The steps this goal will not write, and why.

    `per_kind` mirrors `planner.MAX_DETERMINISTIC_PER_KIND`: the plan writes at
    most one step per observation kind, so at most one step per kind can have
    been set aside, and listing eight would tell the reader eight steps were
    dropped when only one was ever going to be written.
    """
    if goal_class in _SETS_NOTHING_ASIDE:
        return ()
    out: list[SetAside] = []
    for kind, becomes in _SET_ASIDE_BECAUSE.items():
        if bears_on(kind, goal_class):
            continue
        try:
            found = report.of_kind(kind)[:per_kind]
        except Exception:  # noqa: BLE001 — a report shape this cannot read is
            # a report with nothing to set aside, never a failed plan.
            continue
        for o in found:
            out.append(SetAside(
                what=_SET_ASIDE_WHAT.get(kind, "Check a structural finding"),
                why=(f"{becomes}. This run is about "
                     f"{GOAL_CLASS_NOTE.get(goal_class, 'this goal')}, which "
                     f"it does not bear on — so it is recorded here rather "
                     f"than made a step. The finding itself is still on this "
                     f"plan; say so and I will use it."),
                observation=o.id,
                source=getattr(o, "source_label", "") or o.source,
            ))
    return tuple(out)


# ── WHAT EACH SOURCE IS FOR, UNDER THIS PARTICULAR GOAL ─────────────────────

USE = "use"
WEIGHT = "weight"
DISCOUNT = "discount"
IGNORE = "ignore"

#: One line each, in the reader's language. The disposition is the decision;
#: this is what the decision MEANS for a number in the finished document.
DISPOSITION_NOTE: Mapping[str, str] = {
    USE: "Read, and counted towards a finding.",
    WEIGHT: "Read and counted, but it supports a finding rather than sizing one.",
    DISCOUNT: "Read and shown, never counted towards a finding.",
    IGNORE: "Not read at all.",
}


@dataclass(frozen=True)
class SourceRouting:
    """One source, and what this goal does with it."""
    source_type: str
    label: str
    disposition: str
    why: str


@dataclass(frozen=True)
class GoalRouting:
    """The frozen decision structure. Composed in code, narrated by a model.

    Everything here is settled before any prompt is built. `planner` hands this
    to the composition as material and forbids the model from changing it —
    the model may say these things in better sentences and may not say
    different things.
    """
    goal_class: str
    goal_class_note: str
    #: The business MODEL as the company recorded it at onboarding, verbatim.
    #: Empty when the column is unset or unreadable, which is a statement this
    #: module makes out loud rather than an absence the reader has to notice.
    business_type: str
    sources: tuple[SourceRouting, ...] = ()
    #: What this run can and cannot do given the above, each a full sentence.
    #: Separate from the per-source lines because they are facts about the RUN
    #: — the unit it can size in, whether its dates mean anything — and
    #: attaching them to one arbitrary source would misattribute them.
    notes: tuple[str, ...] = ()

    def to_json(self) -> dict:
        d = asdict(self)
        d["sources"] = [asdict(s) for s in self.sources]
        d["notes"] = list(self.notes)
        return d


def _disposition_for(role: str) -> tuple[str, str]:
    """What a source's ROLE means for whether it may be counted.

    READ OFF THE ROLE, WHICH IS READ OFF `AUTHORITATIVE_FOR`. Not a second
    table of "source X is for sizing": the engine already decides what each
    source may witness, the plan's role column is a reading of that decision,
    and a routing table that restated it would drift from the thing that
    actually gates a claim later — silently, and in the direction of promising
    more than the run will accept.
    """
    from app.crucible.plan import (
        ROLE_BACKGROUND, ROLE_CAUSE, ROLE_CONSTRAINT, ROLE_CORROBORATING,
        ROLE_SIZING, ROLE_UNUSED,
    )

    if role == ROLE_SIZING:
        return USE, "it can witness how much something moved, so it sizes this"
    if role == ROLE_CAUSE:
        return USE, ("it can witness why, from the party in a position to "
                     "know, so it explains this")
    if role == ROLE_CONSTRAINT:
        return USE, "it can witness what is stopping something today"
    if role == ROLE_CORROBORATING:
        return WEIGHT, ("it can witness what was asked for or attempted, "
                        "which corroborates a finding but cannot size one")
    if role == ROLE_UNUSED:
        return IGNORE, "you dropped it from this run"
    if role == ROLE_BACKGROUND:
        return DISCOUNT, ("nothing it carries may be counted towards a "
                          "finding, so it is context rather than evidence")
    return DISCOUNT, "what it may witness is not recorded, so it is not counted"


def resolve(
    *,
    goal_text: str,
    definition_text: str = "",
    sources: Sequence[Any] = (),
    business_type: str = "",
    unit_value_available: bool = False,
    coverage: Optional[Mapping[str, Any]] = None,
    dating_unreliable: bool = False,
    #: The run's unit, already decided (`plan.weighting_verdict`) — narrated
    #: here, never re-decided here. Empty means a caller built before the
    #: verdict existed, which renders exactly as it did then.
    weighting_unit: str = "",
    weighting_because: str = "",
) -> GoalRouting:
    """The whole decision, in one deterministic call.

    Every input is a fact the caller already holds — the source inventory it
    is about to render, the company row it already reads for the declared
    framework, and the reconnaissance summary it already puts on the plan. No
    query is issued here and no model is called, so this is reproducible for a
    given run and can be re-derived from a stored plan.
    """
    goal_class = classify_goal(goal_text, definition_text)
    routed: list[SourceRouting] = []
    for src in sources:
        role = getattr(src, "role", "") or ""
        disposition, because = _disposition_for(role)
        label = (getattr(src, "label", "")
                 or str(getattr(src, "source_type", "")).replace("_", " "))
        routed.append(SourceRouting(
            source_type=str(getattr(src, "source_type", "")),
            label=label,
            disposition=disposition,
            why=f"For {GOAL_CLASS_NOTE.get(goal_class, 'this goal')}, "
                f"{because}.",
        ))

    notes: list[str] = []

    # ── THE UNIT. The single most consequential thing a reader can learn
    # before approving, and the one the engine used to degrade to silently.
    if unit_value_available:
        # READ AND REPORTED, NOT APPLIED — AND THE NOTE MUST SAY BOTH.
        # This branch used to read "sizes here are stated in money rather than
        # in a count of accounts", which the engine does not do and has never
        # done: `weight_by_account_value` is `declared` in the primitive
        # registry, nothing constructs an `ImpactInputs` with a non-None
        # `value_per_unit`, and `score_impact` therefore returns
        # `affected_population * movable_gap` — a COUNT OF ACCOUNTS. Every
        # other statement of the unit in this engine already said so
        # (`planner`'s monetary-coverage step, `framework`'s reason lines, the
        # plan header), so that sentence was the single place the document
        # promised more than the run performs, on the one screen that sells
        # the run as reproducible.
        #
        # It is not deleted, because the derived value IS used: it is measured
        # off the customer's own contracts and reported with its median, and
        # it is why the plan does not ask them for a number their data already
        # answers. A reader who connected contract data should still learn it
        # was recognised — and should learn, in the same breath, that it does
        # not move the sizing.
        notes.append(
            "Your own evidence carries what an account is worth, so that "
            "figure is read from your own data rather than asked of you. It "
            "does not change how anything is sized here: a theme's size is "
            "still a count of the accounts it touches, never money.")
    else:
        notes.append(
            "Nothing read here carries a per-account value, so every size is "
            "stated in accounts touched and never in money. That is a "
            "statement about the evidence, not about how big anything is.")

    # ── THE UNIT THE RUN ACTUALLY SETTLED ON, IN ITS OWN WORDS.
    #
    # NARRATED, NOT DECIDED. The verdict is `plan.weighting_verdict`'s, made
    # from the reconnaissance pass and the recorded business model before this
    # is called; restating the rule here would be a second implementation of
    # the most consequential decision on the gate, free to disagree with the
    # one the run actually carries out.
    #
    # It says so even when the answer is "counted", and especially then: B100
    # requires a deviation from the default weighting to be stated with its
    # reason, and a self-serve business whose contracts COULD have priced its
    # themes is exactly the case where silence would read as the engine having
    # simply failed to try.
    if weighting_because:
        notes.append(weighting_because)

    # ── THE BUSINESS MODEL, READ RATHER THAN ASSUMED.
    if business_type:
        notes.append(
            f"You recorded this business as {business_type}, and I have "
            f"assumed nothing beyond that about how your accounts are "
            f"structured or renewed.")
    else:
        notes.append(
            "Your business type is not recorded, so nothing here assumes how "
            "your accounts are structured or renewed — if that matters to this "
            "question, say so and I will use it.")

    # ── THE WINDOW. `coverage` is the reconnaissance summary the plan already
    # renders; saying what it means for the method costs nothing extra.
    window = ""
    if coverage:
        earliest = str(coverage.get("earliest") or "")
        latest = str(coverage.get("latest") or "")
        if earliest and latest:
            window = f"{earliest} to {latest}"
    if dating_unreliable:
        notes.append(
            "The dates on this evidence are when it was imported rather than "
            "when anything happened, so nothing here is weighted by recency.")
    elif window:
        notes.append(
            f"What I read spans {window}, and every rate is measured inside "
            f"that window rather than extrapolated past it.")
    else:
        notes.append(
            "Nothing read here is reliably dated, so nothing is weighted by "
            "recency and no trend is claimed.")

    return GoalRouting(
        goal_class=goal_class,
        goal_class_note=GOAL_CLASS_NOTE.get(goal_class, ""),
        business_type=business_type,
        sources=tuple(routed),
        notes=tuple(notes),
    )


def prompt_block(routing: Optional[GoalRouting],
                 set_aside: Sequence[SetAside] = ()) -> str:
    """The frozen decisions, as material for the composition prompt.

    HANDED OVER AS SETTLED FACT, NOT AS A QUESTION. The model's job is to say
    these in the reader's language; it is told so here and told so again in the
    system prompt, which is where the prohibition lives. Same shape as the
    OBSERVATIONS block above it: an id or a label, then the decision, then the
    reason — so a model that copies the vocabulary it is given copies the
    right words.
    """
    if routing is None:
        return ""
    lines = [
        "HOW THIS GOAL ROUTES THE EVIDENCE — decided already, not by you:",
        f"  reading of the goal: {routing.goal_class} — "
        f"{routing.goal_class_note}",
    ]
    for s in routing.sources:
        lines.append(f"  - {s.label}: {s.disposition} — {s.why}")
    for n in routing.notes:
        lines.append(f"  · {n}")
    if set_aside:
        lines += ["", "SET ASIDE FOR THIS GOAL — write NO step for these:"]
        for sa in set_aside:
            lines.append(f"  [{sa.observation}] {sa.what} — {sa.why}")
    return "\n".join(lines)

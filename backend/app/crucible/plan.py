"""Stage 1 — say what will be done, BEFORE doing it.

WHY A PLAN STEP EXISTS AT ALL. A run reads a tenant's whole corpus and takes
minutes. Until now the first thing a user learned about its limits was the
coverage notes at the bottom of the finished output — after the wait, and
phrased as an apology. The same facts are worth far more BEFOREHAND, where they
are a decision: connect the missing source, narrow the window, or accept a
qualitative answer and press on.

THE PLAN NOW READS BEFORE IT SPEAKS, AND THAT REVERSES AN EARLIER DECISION.
This module still only counts — `source_inventory` reads no content — but the
stage no longer does. `routes.crucible._recon_report` reads a bounded slice of
the corpus (`_RECON_PAGES` pages) and hands the observations in, so a step can
say "these two value columns disagree on 13 of 60 rows" rather than "you have a
revenue source connected".

The decision this replaces was deliberate and its reasoning was sound: a plan
that had to read the corpus to describe it would be the expensive thing it
exists to gate. What changed is the evidence about what a plan is FOR. A plan
built from counts can state coverage and nothing else, so it cannot say how a
decision will be made — which is the whole of what a reader needs before
approving one, and the reason the old gate read as a receipt.

The cost is contained rather than dismissed: the read is bounded, fails open to
the count-only plan, and reuses a column both signal reads already select. It is
NOT free, and on a large tenant it has not been measured. If that measurement
comes back badly, the honest fix is a smaller sample — not a return to a plan
that cannot describe its own method.

WHAT MAKES IT ACTIONABLE. Sprntly can ingest numbers — connectors exist, and a
user can upload a document. So a gap is never reported as a dead end: every
`cannot_answer` entry carries the thing that would close it. "No analytics
source is connected, so this run cannot state a point estimate" is a shrug;
"…connect Amplitude, or upload the cohort export" is a next step.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Optional

from app.crucible.claims import AUTHORITATIVE_FOR
from app.graph.types import source_type_label

if TYPE_CHECKING:  # imported for annotations only — `planner` imports
    # `primitives`, which imports nothing from here, so a runtime import
    # would not actually be circular; it is deferred anyway because building
    # a plan without steps (every existing caller, every stored plan) must
    # not pay for loading the planner.
    from app.crucible.planner import PlanStep
    from app.crucible.recon import Observation

logger = logging.getLogger(__name__)

#: What each source can and cannot witness, in the user's language rather than
#: the type system's. Mirrors AUTHORITATIVE_FOR — the plan must not promise more
#: than the engine will accept later.
#: What each source can WITNESS. The other half — what each source is called —
#: is `graph.types.SOURCE_TYPE_LABELS`, because a source type is graph
#: vocabulary and this module is one consumer of it. They were both here, and
#: the consequence was that every other path in the product showed the reader
#: the storage key instead: a real session cited `[Source: pm_manual/finding]`.
_SOURCE_WITNESSES: dict[str, str] = {
    "customer_voice":   "what customers asked for and reported",
    "communication":    "what was discussed, hit and attempted",
    "project_mgmt":     "what was built, broken, blocked or attempted",
    "pm_manual":        "the company's stated constraints and goals",
    "analytics":        "how much something moved, and in which direction",
    "revenue":          "how much something moved, and in which direction",
    "outcome_measured": "whether a change actually worked",
    "verbal_claim":     "nothing — recorded, never counted",
    "agent_inferred":   "nothing — recorded, never counted",
}

#: Kept in this shape, and in this ORDER, because `source_inventory` iterates
#: it to decide which source types to count.
_SOURCE_PROSE: dict[str, tuple[str, str]] = {
    source_type: (source_type_label(source_type), witnesses)
    for source_type, witnesses in _SOURCE_WITNESSES.items()
}

#: Sources that carry NUMBERS. Without at least one, no finding can be stated as
#: a point estimate in the goal's own unit — only as reach. This is the single
#: most consequential thing a user can learn before a run rather than after.
NUMERIC_SOURCES = ("analytics", "revenue", "outcome_measured")

#: How to close each gap. Sprntly can ingest all of these, so a gap is a next
#: step rather than a limitation.
_REMEDY = {
    "analytics": "connect Amplitude (or your product analytics), "
                 "or upload a cohort export",
    "revenue": "connect Stripe or your billing export",
    "outcome_measured": "connect your experiment tool, or upload the "
                        "experiment history",
    "customer_voice": "connect Gong, Fireflies or Zendesk",
    "project_mgmt": "connect Jira or Linear",
}


#: WHAT EACH SOURCE IS BEING USED FOR, not merely what it contains.
#:
#: The plan gate listed sources as a flat inventory with counts, which is an
#: answer to "what have you got" and not to "what are you doing with it". A
#: reader cannot tell from a count whether a transcript is being used to size
#: an opportunity or to explain one — and those are different claims with
#: different reliability, which is the whole reason `AUTHORITATIVE_FOR` exists.
#:
#: DERIVED FROM `AUTHORITATIVE_FOR`, NEVER RESTATED. The role is a reading of
#: the claim types a source may witness, so it cannot disagree with what the
#: engine will actually accept later. A second table of "source X is for
#: sizing" would drift from the first the moment a source's authority changed,
#: and it would drift silently.
#:
#: ORDERED BY WHAT A CLAIM CAN CARRY. Magnitude outranks mechanism outranks
#: constraint: a source that may witness how much something moved is a sizing
#: source even if it can also say why, because sizing is the scarcer capability
#: and the one a reader most needs to locate.
ROLE_SIZING = "Sizing"
ROLE_CAUSE = "Cause"
ROLE_CONSTRAINT = "Constraint"
ROLE_CORROBORATING = "Corroborating"
ROLE_BACKGROUND = "Background"
ROLE_UNUSED = "Not using"

#: The order they are shown in, strongest claim first. `Not using` is last
#: because it is the only one that is not part of the method.
ROLE_ORDER: tuple[str, ...] = (
    ROLE_SIZING, ROLE_CAUSE, ROLE_CONSTRAINT, ROLE_CORROBORATING,
    ROLE_BACKGROUND, ROLE_UNUSED,
)

#: One line each, in the reader's language, saying what the role may and may
#: not do. The "never" half is the part that earns its place: a reader who
#: knows analytics can say how many but never why will not ask it why.
ROLE_NOTES: dict[str, str] = {
    ROLE_SIZING: "Behaviour: how many, how much. Never why.",
    ROLE_CAUSE: "Why it happened, from the party in a position to know.",
    ROLE_CONSTRAINT: "What is stopping something today.",
    ROLE_CORROBORATING: "What was asked for or attempted. Supports, never sizes.",
    ROLE_BACKGROUND: "Recorded and shown, never counted towards a finding.",
    ROLE_UNUSED: "Dropped by you, and the report says so.",
}


def role_for(source_type: str) -> str:
    """What this source is for, read off what it may witness."""
    witnesses = AUTHORITATIVE_FOR.get(source_type, frozenset())
    if not witnesses:
        return ROLE_BACKGROUND
    if {"magnitude", "direction"} & witnesses:
        return ROLE_SIZING
    if "mechanism" in witnesses:
        return ROLE_CAUSE
    if "constraint" in witnesses:
        return ROLE_CONSTRAINT
    return ROLE_CORROBORATING


@dataclass(frozen=True)
class SourceInventory:
    source_type: str
    signal_count: int
    label: str
    witnesses: str
    #: What this source is being used FOR — see `role_for`. Empty on an
    #: inventory built before roles existed, which renders ungrouped exactly
    #: as it did then.
    role: str = ""
    role_note: str = ""


@dataclass(frozen=True)
class Gap:
    """Something this run will NOT be able to answer, and how to change that."""
    question: str
    because: str
    remedy: str


@dataclass(frozen=True)
class PlanQuestion:
    """Something the CHOSEN framework needs and cannot derive — asked once,
    batched, before generation begins (AC-5). Replaces the old fixed set of
    three questions asked unconditionally regardless of what would actually
    use the answer; see `app.crucible.framework.questions_for`.

    Skipping one is not the same as answering it with nothing: the gate
    never invents a value for a blank field, and the gap it would have
    closed is carried into the output instead (I3/I8's discipline, applied
    to an input rather than a finding).
    """
    id: str
    prompt: str
    why: str
    #: ── WHAT A DERIVED QUESTION CARRIES THAT A FIXED ONE DID NOT. ───────
    #:
    #: The old three were asked unconditionally, so there was nothing to say
    #: about WHY THIS RUN in particular is asking. A question derived from
    #: something the reconnaissance pass actually saw can say it, and has to:
    #: a reader who is told "your contracts carry two different account
    #: values and they differ on 13 rows" can answer in seconds, where the
    #: same question asked cold reads as the engine being unsure of itself.
    #:
    #: All default to empty so every existing construction, and every plan
    #: stored before this shipped, is unchanged.
    #:
    #: What the run saw that prompted the question, with the numbers.
    what_i_saw: str = ""
    #: What the answer changes, said as the thing at stake rather than the
    #: field name it lands in.
    affects: str = ""
    #: What happens if it is left blank. NEVER "we will guess": every default
    #: here is a stated, conservative behaviour the run will carry out and
    #: disclose, which is the difference between a default and an invention.
    default_if_skipped: str = ""
    #: A closed set of answers where one exists. Empty means free text — right
    #: for a person's name or a date, wrong for "which of these two columns is
    #: your book", where the options ARE the answer and typing invites a third.
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunPlan:
    goal_text: str
    definition_text: str
    currency: str
    #: THE READER'S OWN SENTENCE, when the caller has one distinct from
    #: `goal_text` — chat dispatches the planner's EXTRACTED goal as
    #: `goal_text` (right for this class's own fields below: the metric and
    #: the definition genuinely want the normalised words) and this alongside
    #: it, so the gate can show what was actually typed rather than only its
    #: normalisation. Empty when there is no literal text to carry — the
    #: direct API, the `+` menu, or a plan built before this field existed.
    asked_text: str = ""
    sources: tuple[SourceInventory, ...] = ()
    cannot_answer: tuple[Gap, ...] = ()
    will_produce: tuple[str, ...] = ()
    total_signals: int = 0
    #: Source types the user has excluded. Empty means everything available.
    excluded_sources: tuple[str, ...] = ()
    #: The user's own hypotheses, to be killed or carried EXPLICITLY. Without
    #: these a run can only report what it found; with them it can also say
    #: "the thing you believed is not supported, and here is what killed it",
    #: which is usually the more valuable half.
    hypotheses: tuple[str, ...] = ()
    #: WHERE THE DEFINITION CAME FROM, and whether a person has said yes to it.
    #:
    #: The goal definition used to be settled at its own gate, one screen
    #: earlier, before the plan existed. Product feedback collapsed the two: a
    #: separate clarification step made the reader answer a question with no
    #: context for it, and the answer it collected showed exactly why — a run
    #: went out with its definition recorded as the literal words "that is
    #: accurate", because that is what the reader typed at a question that was
    #: not asking for a definition.
    #:
    #: So the definition now arrives here as a PROPOSAL and is adopted by the
    #: same click that approves the plan. I9 is unchanged and these three
    #: fields are how it stays unchanged: the proposal must be shown, be
    #: attributed to whatever produced it, and be editable at the moment of
    #: approval. `definition_adopted` is False for exactly as long as no person
    #: has said yes.
    definition_source: str = ""
    definition_note: str = ""
    definition_adopted: bool = False
    #: HOW THE SURVIVORS GET ORDERED, said before the run rather than
    #: discovered in the output.
    #:
    #: RICE by default, on Apurva's call. The `prioritize` skill's own checklist
    #: says a framework should be "chosen with a reason, not defaulted to RICE"
    #: — the reason here is that it is the one the reader asked for, and naming
    #: it in the plan is what makes it a choice they can override rather than a
    #: convention they discover afterwards.
    framework: str = "RICE"
    #: WHY THIS FRAMEWORK, said as a sentence rather than left for the reader
    #: to infer from the table underneath it. Chosen by code over the source
    #: inventory (`app.crucible.framework.select_framework`), never by a
    #: model (I2) — reasoning over what is connected, not a choice an LLM
    #: made. Empty only on a plan built before this field existed.
    framework_reason: str = ""
    #: WHAT THE CHOSEN FRAMEWORK NEEDS AND CANNOT DERIVE, batched (AC-5).
    #: Replaces the old fixed three (account value / decision owner /
    #: needed-by) asked unconditionally regardless of which framework would
    #: use the answer — see `app.crucible.framework.questions_for`.
    questions: tuple[PlanQuestion, ...] = ()
    #: ── THINGS THIS RUN CANNOT KNOW, AND NOW ASKS FOR. ──────────────────
    #:
    #: Apurva: "the plan gate can start asking questions it doesn't know
    #: answers to." Until now the gate asked exactly one thing — what the
    #: metric means — and everything else it lacked was reported as a limit.
    #: Four of the reference memo's sections are unreachable for want of three
    #: numbers, none of which are in any corpus and all of which a PM knows.
    #:
    #: EACH IS OPTIONAL AND EACH IS AN ASSUMPTION WHEN GIVEN. A value typed
    #: into a box is not evidence: it ships as an `AssumedParam` with the range
    #: it plausibly spans, so the document can say what the headline becomes at
    #: the pessimistic end rather than presenting an estimate as a measurement.
    #: That is I8, and it is the difference between asking for input and
    #: laundering a guess into a number.
    #:
    #: WHAT IS DELIBERATELY NOT ASKED HERE: effort. It is per-finding, the
    #: findings do not exist at plan time, and one value applied to every row
    #: is a common divisor that cannot change a ranking.
    #:
    #: What one account is worth per year, in the reader's own currency. Turns
    #: reach-in-accounts into money, which is the spine of the reference memo.
    account_value: Optional[float] = None
    #: Who signs off. The memo's decision box names one.
    decision_owner: str = ""
    #: When the decision is needed. Free text on purpose — "before the Q3 QBR"
    #: is a real answer and a date picker would refuse it.
    needed_by: str = ""
    #: ── THE METHOD, AND WHAT IT WAS DERIVED FROM. ───────────────────────
    #:
    #: `steps` is the numbered sequence this run will carry out, each naming a
    #: registered primitive (`app.crucible.planner`). `observations` is what
    #: the reconnaissance pass saw in the evidence (`app.crucible.recon`) —
    #: carried on the plan because it is BOTH the input the steps were
    #: composed from and the check every figure in them was verified against,
    #: so a stored plan can be re-verified without re-reading the corpus.
    #:
    #: ADDITIVE WITH DEFAULTS, AND THAT IS LOAD-BEARING. `crucible_runs.
    #: prioritisation` is a single JSONB blob with no version field and every
    #: reader guards with `.get()`; a rename or a required field would strand
    #: every plan already stored. Empty tuples are exactly what a plan built
    #: before this existed reads back as, which renders as the old document.
    steps: tuple["PlanStep", ...] = ()
    observations: tuple["Observation", ...] = ()
    #: HOW MUCH WAS READ, OVER WHAT WINDOW, FROM HOW MANY PLACES — the
    #: aggregate of `ReconReport.summary`. Empty dict on every plan built
    #: without a reconnaissance pass, which renders as no stat strip rather
    #: than as zeroes: "nothing was read" and "we did not look" are different
    #: statements and only one of them is true here.
    coverage: dict = field(default_factory=dict)
    #: TRUE WHILE THE METHOD IS STILL BEING COMPOSED.
    #:
    #: Everything above the steps on the gate — the verdict, the coverage, the
    #: definition, the counting unit, the sources and their roles — is
    #: deterministic and lands in a few hundred milliseconds. Only the wording
    #: of the steps needs a model call. Making the reader wait for the whole
    #: thing meant staring at nothing while the fast, true part sat ready.
    #:
    #: So the plan is written twice: once with the deterministic method, marked
    #: pending, which is what the gate renders immediately; then once more when
    #: the composition lands. THE SECOND WRITE IS A COMPLETION, NOT A RE-ROLL —
    #: the draw-once rule is unchanged, and `planner.load_steps` treats a
    #: pending plan as not yet drawn precisely so this one upgrade can happen
    #: and no other.
    steps_pending: bool = False
    #: WHAT ONE ACCOUNT IS WORTH, TAKEN FROM THE EVIDENCE RATHER THAN ASKED.
    #:
    #: DELIBERATELY NOT `account_value`. That field is the reader's own
    #: estimate and `report.py` renders it with the words "which is an
    #: estimate you gave rather than something measured" — true of a typed
    #: number and false of one read off the contracts, so writing a derived
    #: figure there would make the finished document misattribute measured
    #: data to the reader. A separate field keeps that attribution correct;
    #: rendering this one is a change to the report, not to the plan.
    account_value_derived: Optional[float] = None
    #: How it was derived, in one sentence, so the suppression of the question
    #: is visible rather than silent — a question that stops being asked with
    #: no explanation reads as a feature that broke.
    account_value_derived_note: str = ""

    def to_json(self) -> dict:
        return {
            "goal_text": self.goal_text,
            "asked_text": self.asked_text,
            "definition_text": self.definition_text,
            "currency": self.currency,
            "total_signals": self.total_signals,
            "sources": [asdict(s) for s in self.sources],
            "cannot_answer": [asdict(g) for g in self.cannot_answer],
            "will_produce": list(self.will_produce),
            "excluded_sources": list(self.excluded_sources),
            "hypotheses": list(self.hypotheses),
            "definition_source": self.definition_source,
            "definition_note": self.definition_note,
            "definition_adopted": self.definition_adopted,
            "framework": self.framework,
            "framework_reason": self.framework_reason,
            "questions": [asdict(q) for q in self.questions],
            "account_value": self.account_value,
            "decision_owner": self.decision_owner,
            "needed_by": self.needed_by,
            "coverage": dict(self.coverage),
            "steps_pending": self.steps_pending,
            "steps": [st.to_json() for st in self.steps],
            "observations": [o.to_json() for o in self.observations],
            "account_value_derived": self.account_value_derived,
            "account_value_derived_note": self.account_value_derived_note,
        }


def source_inventory(company_id: str) -> tuple[list[SourceInventory], int]:
    """Count signals per source. No content read, so this is cheap."""
    from app.db.client import require_client

    client = require_client()
    counts: dict[str, int] = {}
    total = 0
    for source_type in _SOURCE_PROSE:
        try:
            res = (
                client.table("kg_signal")
                .select("id", count="exact")
                .eq("enterprise_id", company_id)
                .eq("source_type", source_type)
                .limit(1)
                .execute()
            )
        except Exception:  # noqa: BLE001 — an uncountable source is reported as
            # absent rather than silently assumed present.
            logger.warning("crucible plan: could not count %s", source_type)
            continue
        n = res.count or 0
        if n:
            counts[source_type] = n
            total += n

    out = []
    for source_type, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        label, witnesses = _SOURCE_PROSE[source_type]
        role = role_for(source_type)
        out.append(SourceInventory(source_type, n, label, witnesses,
                                   role=role, role_note=ROLE_NOTES[role]))
    return out, total


def derive_gaps_and_promises(
    kept: "tuple[SourceInventory, ...] | list[SourceInventory]",
    hypotheses: tuple[str, ...] = (),
    *,
    framework_choice: "Optional[object]" = None,
) -> tuple[tuple["Gap", ...], tuple[str, ...]]:
    """What this run will NOT be able to answer, and what it WILL produce,
    derived from the sources it will actually read.

    EXTRACTED SO THE APPROVE PATH CAN RE-DERIVE THEM. `build_plan` computed
    these from the kept set correctly, but approval narrowed only `sources` and
    `total_signals` in place — so a reader who unticked analytics and revenue
    still got a plan promising "your analytics/revenue data is connected and
    will be read", in the same document that said those sources were excluded.
    Worse, they lost the gap that had just become TRUE ("nothing connected here
    carries numbers") along with its actionable remedy, and were handed "no
    action needed from you" instead.

    Pure: it reads only the kept inventory (plus the framework choice already
    derived from it), so the plan gate and the approve path cannot drift.

    `framework_choice` is an `app.crucible.framework.FrameworkChoice` — typed
    loosely (`object`) here rather than imported at module level, because
    `framework.py` imports FROM this module (`NUMERIC_SOURCES`,
    `SourceInventory`, `PlanQuestion`) and a top-level import back would be
    circular. Duck-typed on `.declared` / `.honoured_declared` / `.reason` /
    `.remedy`.
    """

    present = {s.source_type for s in kept}
    gaps: list[Gap] = []

    # THE FRAMEWORK THE COMPANY ASKED FOR, SAID PLAINLY WHEN IT COULD NOT BE
    # HONOURED (AC-2/AC-3). A gap the reader did not expect — "why isn't this
    # ranked by the thing I set at onboarding?" — is exactly the kind this
    # step exists to surface rather than let a reader discover in the output.
    declared = getattr(framework_choice, "declared", None)
    honoured = getattr(framework_choice, "honoured_declared", True)
    if framework_choice is not None and declared and not honoured:
        from app.crucible.framework import display_name

        gaps.append(Gap(
            question=f"Why isn't this ranked by {display_name(declared)}?",
            because=getattr(framework_choice, "reason", "") or
                    f"{display_name(declared)} needs data this run does not "
                    f"have connected",
            remedy=getattr(framework_choice, "remedy", "") or
                   "connect the source this framework needs",
        ))

    if not present & set(NUMERIC_SOURCES):
        gaps.append(Gap(
            question="How many points will this move the metric?",
            because="nothing connected here carries numbers — every source is "
                    "prose, and a magnitude needs instrumentation",
            remedy=_REMEDY["analytics"],
        ))
    else:
        # Connected but not yet usable for sizing. Said plainly, because a user
        # who has connected analytics will reasonably expect a number and
        # should learn otherwise here rather than at the bottom of the output.
        gaps.append(Gap(
            question="How many points will this move the metric?",
            because="your numeric sources are connected and will be read, but "
                    "the engine cannot yet size a finding in the goal's own "
                    "unit — it reports reach instead",
            remedy="no action needed from you; this is the next capability "
                   "being built",
        ))
    if "outcome_measured" not in present:
        gaps.append(Gap(
            question="Did a change like this work last time?",
            because="no measured outcomes are connected, so nothing can close "
                    "the loop between a change and its effect",
            remedy=_REMEDY["outcome_measured"],
        ))
    if "customer_voice" not in present:
        gaps.append(Gap(
            question="What did customers actually ask for?",
            because="the tracker records a PM's paraphrase of a request; only "
                    "the customer is authoritative about their own motive",
            remedy=_REMEDY["customer_voice"],
        ))

    produce = [
        "Themes ranked by how much of your book they touch, each with the "
        "source documents it rests on",
        "A considered-and-dropped list, with the reason each candidate died",
        "Every degradation disclosed beside the findings it affects",
    ]
    if hypotheses:
        # NOT "a verdict on each". Nothing adjudicates hypotheses yet — they are
        # recorded on the run and shown beside the findings so a reader can
        # compare by eye. Promising a verdict the engine cannot deliver is the
        # same overpromise this whole step exists to remove, and it is the
        # second time I have written one into it.
        produce.append(
            f"The {len(hypotheses)} things you already believe, recorded and "
            f"shown beside the findings — this run does NOT yet test them "
            f"against the evidence, so comparing them is still your job"
        )
    # ALWAYS reach, for now. The engine has no numeric sizing path yet: impact
    # is computed from how many accounts a theme touches, whatever sources are
    # connected. Promising "sizes in the goal's own unit" because an analytics
    # source happens to EXIST would be the plan overpromising what the run will
    # deliver — which is the exact dishonesty this step was added to remove.
    # When numeric sizing ships, this branches on the capability, not on the
    # presence of a source.
    produce.append("Sizes stated in reach — how many accounts a theme touches, "
                   "not how many points it will move the metric")
    if present & set(NUMERIC_SOURCES):
        produce.append(
            f"Your {'/'.join(sorted(present & set(NUMERIC_SOURCES)))} data is "
            f"connected and will be read as evidence, but it cannot yet be "
            f"turned into a point estimate — that is the next thing being built"
        )

    return tuple(gaps), tuple(produce)


def build_plan(
    *,
    company_id: str,
    goal_text: str,
    definition_text: str,
    #: The reader's own sentence, carried alongside `goal_text` — see
    #: `RunPlan.asked_text`. Empty by default so every existing caller (and
    #: every stored plan built before this shipped) is unaffected.
    asked_text: str = "",
    currency: str = "accounts",
    excluded_sources: tuple[str, ...] = (),
    hypotheses: tuple[str, ...] = (),
    definition_source: str = "",
    definition_note: str = "",
    definition_adopted: bool = False,
    #: `None` means "choose it" — the normal path. A caller that passes a
    #: string is asking for that framework explicitly (used by tests and by
    #: the approve path's re-derivation, which already has a `FrameworkChoice`
    #: from `select_framework` and does not need this function to pick again).
    framework: Optional[str] = None,
    account_value: Optional[float] = None,
    decision_owner: str = "",
    needed_by: str = "",
    #: WHAT THE RECONNAISSANCE PASS SAW, when one has been run. `None` — the
    #: default, and every existing caller — produces exactly the plan this
    #: function has always produced, with no steps and no observations. That
    #: matters beyond backwards compatibility: the pass reads content, and
    #: whether a given entry point can afford to is the CALLER's decision,
    #: not this function's.
    recon_report: "Optional[object]" = None,
    #: The run's own `prioritisation` blob, for the draw-once read-back. A
    #: plan whose steps were already composed is READ, never re-composed —
    #: see `app.crucible.planner.build_steps`.
    run_meta: "Optional[dict]" = None,
    enterprise_id: str = "",
    #: False writes the DETERMINISTIC method and marks the plan pending,
    #: skipping the model call entirely. The caller composes and completes it
    #: in a second write — see `RunPlan.steps_pending`.
    compose: bool = True,
) -> RunPlan:
    """What this run will try to establish, HOW, where it will look, and what
    it will not be able to tell you."""
    sources, total = source_inventory(company_id)
    kept = tuple(s for s in sources if s.source_type not in excluded_sources)

    # ── WHICH FRAMEWORK, CHOSEN BY CODE OVER THE INVENTORY (AC-2). ──────────
    # Local imports: `app.crucible.framework` imports `NUMERIC_SOURCES` /
    # `SourceInventory` / `PlanQuestion` FROM this module, so a top-level
    # import back here would be circular.
    from app.crucible.framework import FrameworkChoice, questions_for, select_framework

    if framework:
        choice = FrameworkChoice(framework=framework, reason="", declared=None,
                                 honoured_declared=True)
    else:
        declared = None
        try:
            from app.db.companies import declared_prioritization_framework

            declared = declared_prioritization_framework(company_id)
        except Exception:  # noqa: BLE001 — an unreadable company row must
            # never block a plan; fall back to choosing from data alone.
            logger.warning(
                "crucible plan: could not read declared framework for %s",
                company_id,
            )
        choice = select_framework(kept, declared)

    gaps, produce = derive_gaps_and_promises(kept, hypotheses, framework_choice=choice)

    # ── THE METHOD, WHEN THERE IS EVIDENCE TO DERIVE IT FROM. ──────────────
    # Local imports, and unconditionally cheap when `recon_report` is None:
    # `planner` pulls in the primitive registry, which nothing else on this
    # path needs.
    observations: tuple = ()
    steps: tuple = ()
    coverage: dict = {}
    derived_value: Optional[float] = None
    derived_note = ""
    if recon_report is not None:
        from app.crucible.framework import derived_account_value
        from app.crucible.planner import build_steps

        observations = tuple(getattr(recon_report, "observations", ()) or ())
        summary = getattr(recon_report, "summary", None)
        coverage = summary() if callable(summary) else {}
        derived_value, derived_note = derived_account_value(observations)
        if compose:
            steps = build_steps(
                enterprise_id=enterprise_id or company_id,
                goal_text=goal_text,
                definition_text=definition_text,
                currency=currency,
                report=recon_report,
                source_types=tuple(sv.source_type for sv in kept),
                # THE WORDS, NOT THE KEYS. The inventory carries the label the
                # card shows and the role the source plays, so the prompt can
                # name a source the way the reader will see it named.
                sources=kept,
                run_meta=run_meta,
            )
        else:
            from app.crucible.planner import minimal_plan

            steps = tuple(minimal_plan(
                goal_text=goal_text, currency=currency, report=recon_report,
                source_types=tuple(sv.source_type for sv in kept),
            ))

    return RunPlan(
        goal_text=goal_text,
        asked_text=asked_text,
        definition_text=definition_text,
        currency=currency,
        sources=tuple(kept),
        cannot_answer=tuple(gaps),
        will_produce=tuple(produce),
        total_signals=sum(s.signal_count for s in kept),
        excluded_sources=tuple(excluded_sources),
        hypotheses=tuple(h.strip() for h in hypotheses if h.strip()),
        definition_source=definition_source,
        definition_note=definition_note,
        definition_adopted=definition_adopted,
        framework=choice.framework,
        framework_reason=choice.reason,
        questions=questions_for(choice.framework, observations),
        account_value=account_value,
        decision_owner=decision_owner,
        needed_by=needed_by,
        steps=steps,
        observations=observations,
        coverage=coverage,
        steps_pending=bool(recon_report is not None and not compose),
        account_value_derived=derived_value,
        account_value_derived_note=derived_note,
    )

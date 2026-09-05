"""The closed vocabulary of analysis operations a plan step may name.

WHY A REGISTRY RATHER THAN FREE TEXT. The plan gate used to describe a run by
COUNTING it — so many rows of this source type, so many of that — and said
nothing about method. Replacing a count with a paragraph would have been worse:
prose about what a run "will examine" is unfalsifiable, and the one thing this
product sells is that its output is reproducible from a stated procedure.

So a step does not describe an operation, it NAMES one. The planner may only
emit steps naming an entry in this registry, which makes every plan
implementable by construction: there is no way to write down a step the engine
has no code for, because the vocabulary IS the code's surface area.

THE HONESTY INVARIANT, AND WHY `status` IS ON EVERY ENTRY. Half of what a good
analyst would do here is not built yet — cohort decomposition, trend fitting,
applying a company's stated constraints as a filter. Those are real operations
and they belong in the vocabulary, because a vocabulary that only contained
what shipped would give us nowhere to record the gap and no way to describe the
target. They are `declared`: registered, describable, and NOT runnable. The
planner refuses to emit them (`implemented_only`), so a plan states only what
the engine actually does. A plan that promised a cohort decomposition and then
did not do one would be the coverage-note apology this whole stage exists to
remove, moved to the front of the document where it does more damage.

COMPOSITION, NOT A DATAFRAME API. These are scoped to this product's domain —
accounts, evidence, themes, revenue, goals. `concentration_share` is "how much
of this sits in the top few accounts", not `groupby().agg()`. That is the
difference between a vocabulary a product manager can read in a plan and one
that leaks the implementation into the customer's document. It is also what
keeps the set finite: a general dataframe API has no closed vocabulary, so it
could never be validated against, and validation is the whole point.

ADDING ONE MUST NOT TOUCH THE PLANNER. The planner reads `REGISTRY` and the
catalogue text is generated from it (`catalogue`), so a new primitive becomes
available to the model by being registered here and nowhere else.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

#: The groups a primitive can belong to, in the order a run applies them.
#: ORDERED, because the parts of a plan are ordered and the renderer walks
#: this rather than sorting names alphabetically — a plan that measured before
#: it scoped would read as nonsense even if every individual step were valid.
GROUPS: tuple[str, ...] = (
    "scoping",
    "measurement",
    "evidence",
    "refutation",
    "constraint",
    "ranking",
)

#: `implemented` — there is code behind it and `implemented_by` names it.
#: `declared`    — the operation is real and describable, nothing runs it yet.
STATUSES: tuple[str, ...] = ("implemented", "declared")

#: The parameter types a primitive can declare. Deliberately tiny: a type here
#: exists so the validator can check it, and a type nothing validates is
#: decoration.
#:
#: `field` / `fields` are COLUMN NAMES within a source; `source` is the name of
#: an evidence table or a source type. They are separated because a step naming
#: a field of a source that is not in this run is a different failure from one
#: naming a source that is not there at all, and the reader deserves to be told
#: which.
PARAM_TYPES: tuple[str, ...] = (
    "field", "fields", "source", "sources", "int", "number", "string", "enum",
)


@dataclass(frozen=True)
class Param:
    """One argument a step must supply to name an operation unambiguously."""
    name: str
    type: str
    required: bool = True
    #: For `enum` only. Empty on every other type.
    choices: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Primitive:
    """One operation a plan step may name.

    `description` is written in PRODUCT language, not pipeline language,
    because it is what the model is shown when it composes a plan and the
    vocabulary a model is given is the vocabulary it writes back. Describing
    `count_reach` as "compute affected_population over the cluster" produced
    steps that said exactly that; describing it as "count how many of your
    accounts a theme actually touches" produced steps a customer can read.
    """
    id: str
    group: str
    description: str
    params: tuple[Param, ...] = ()
    #: Source types that must be present in the run for this step to be
    #: meaningful. Empty means it works on whatever is connected.
    requires_sources: tuple[str, ...] = ()
    status: str = "declared"
    #: The dotted path to the code that does it. REQUIRED when `status` is
    #: `implemented`, and checked at import (`_audit`) rather than trusted:
    #: an `implemented` entry with nothing behind it is precisely the lie this
    #: field exists to make impossible.
    implemented_by: str = ""

    @property
    def is_implemented(self) -> bool:
        return self.status == "implemented"


def _p(name: str, type_: str, required: bool = True, choices: Sequence[str] = (),
       note: str = "") -> Param:
    return Param(name=name, type=type_, required=required,
                 choices=tuple(choices), note=note)


# ── THE VOCABULARY ──────────────────────────────────────────────────────────
#
# Every `implemented` entry points at code that already existed before this
# module did, EXCEPT the four measurement checks, which `recon.py` implements
# alongside it. Nothing here reimplements an existing rule: `refute_echo` is
# `pipeline._refute`'s echo branch under a name a plan can cite, not a second
# copy of it. That matters more than it looks — two implementations of the
# echo rule would drift, and the plan would then describe a run that no longer
# happens.

_PRIMITIVES: tuple[Primitive, ...] = (

    # ── SCOPING. What is being counted, and over what. ──────────────────────
    Primitive(
        id="select_evidence",
        group="scoping",
        description="Decide which of your connected sources this answer is "
                    "allowed to rest on.",
        params=(
            _p("sources", "sources",
               note="the source types this step reads"),
            _p("since", "string", required=False,
               note="ISO date; omit to read everything connected"),
        ),
        status="implemented",
        implemented_by="app.crucible.claims.project_signals",
    ),
    Primitive(
        id="set_counting_unit",
        group="scoping",
        description="Fix the unit every size in this analysis is stated in, "
                    "so two numbers are never added that do not share one.",
        params=(
            _p("unit", "string", note="e.g. accounts, dollars per year"),
        ),
        status="implemented",
        implemented_by="app.crucible.plan.build_plan",
    ),
    Primitive(
        id="characterise_evidence_mix",
        group="scoping",
        description="Say what KIND of evidence this rests on — how much is a "
                    "customer speaking, and how much is the company "
                    "describing itself.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.recon.evidence_mix",
    ),
    Primitive(
        id="audit_signal_field_coverage",
        group="scoping",
        description="Count how much of the evidence names an account, or "
                    "carries a figure, before anything is sized by it.",
        params=(
            _p("field", "field", note="e.g. properties.account"),
        ),
        status="implemented",
        implemented_by="app.crucible.recon.signal_field_presence",
    ),
    Primitive(
        id="characterise_claim_mix",
        group="scoping",
        description="Say what kind of thing the evidence mostly is, before "
                    "ranking it.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.recon.claim_mix",
    ),
    Primitive(
        id="partition_population",
        group="scoping",
        description="Split the book into groups that should be looked at "
                    "separately before anything is averaged across them.",
        params=(
            _p("source", "source"),
            _p("field", "field", note="the column to split on"),
        ),
        # DECLARED. Nothing splits a population today; every finding is
        # computed over the whole book and the report says so.
        status="declared",
    ),

    # ── MEASUREMENT. Getting the numbers out. ───────────────────────────────
    Primitive(
        id="aggregate_by_group",
        group="measurement",
        description="Total a figure up by account, segment or month so it can "
                    "be compared like for like.",
        params=(
            _p("source", "source"),
            _p("group_field", "field"),
            _p("measure_field", "field"),
        ),
        status="implemented",
        implemented_by="app.crucible.recon.aggregate_by_group",
    ),
    Primitive(
        id="reconcile_value_columns",
        group="measurement",
        description="Check two columns that both claim to be the account's "
                    "value against each other, and find what explains the gap.",
        params=(
            _p("source", "source"),
            _p("base_field", "field"),
            _p("total_field", "field"),
        ),
        status="implemented",
        implemented_by="app.crucible.recon.reconcile_value_columns",
    ),
    Primitive(
        id="concentration_share",
        group="measurement",
        description="Work out how much of a total sits in the biggest few "
                    "accounts, rather than reading the average.",
        params=(
            _p("source", "source"),
            _p("group_field", "field"),
            _p("measure_field", "field"),
            _p("top_n", "int", required=False, note="defaults to 3"),
        ),
        status="implemented",
        implemented_by="app.crucible.recon.concentration_share",
    ),
    Primitive(
        id="compare_measures_across_groups",
        group="measurement",
        description="Ask whether the accounts that generate the most activity "
                    "are the same ones that carry the most money.",
        params=(
            _p("group_field", "field"),
            _p("volume_field", "field"),
            _p("value_field", "field"),
            _p("top_n", "int", required=False, note="defaults to 3"),
        ),
        status="implemented",
        implemented_by="app.crucible.recon.concentration_divergence",
    ),
    Primitive(
        id="audit_field_coverage",
        group="measurement",
        description="Count how often a field is actually filled in before any "
                    "conclusion is drawn from it.",
        params=(
            _p("source", "source"),
            _p("field", "field"),
        ),
        status="implemented",
        implemented_by="app.crucible.recon.field_coverage",
    ),
    Primitive(
        id="check_stage_collapse",
        group="measurement",
        description="Check whether two steps of a funnel are really two steps, "
                    "or the same number written twice.",
        params=(
            _p("source", "source"),
            _p("fields", "fields"),
        ),
        status="implemented",
        implemented_by="app.crucible.recon.identical_columns",
    ),
    Primitive(
        id="check_dating_reliability",
        group="measurement",
        description="Check whether the dates record when things happened or "
                    "just when we imported them.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.recon.dating_reliability",
    ),
    Primitive(
        id="check_source_concentration",
        group="measurement",
        description="Count how many separate documents the evidence actually "
                    "rests on, rather than how many rows it has.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.recon.source_concentration",
    ),
    Primitive(
        id="check_period_censoring",
        group="measurement",
        description="Check whether the empty later periods are a decline or "
                    "simply months that have not happened yet.",
        params=(
            _p("source", "source"),
            _p("period_field", "field"),
        ),
        status="implemented",
        implemented_by="app.crucible.recon.period_grid",
    ),
    Primitive(
        id="trend_over_window",
        group="measurement",
        description="Fit the direction of a figure over time and say whether "
                    "the movement is bigger than the noise.",
        params=(
            _p("source", "source"),
            _p("measure_field", "field"),
            _p("period_field", "field"),
        ),
        # DECLARED. `ds/analyses.py` computes a weekly anomaly z-score for the
        # data-science path, but nothing exposes a trend to this pipeline.
        status="declared",
    ),
    Primitive(
        id="decompose_by_cohort",
        group="measurement",
        description="Break a headline number apart by when the account "
                    "signed, to see whether it is a new problem or an old one.",
        params=(
            _p("source", "source"),
            _p("cohort_field", "field"),
            _p("measure_field", "field"),
        ),
        status="declared",
    ),
    Primitive(
        id="check_distribution_skew",
        group="measurement",
        description="Check whether an average is describing the typical "
                    "account or being dragged by a handful of large ones.",
        params=(
            _p("source", "source"),
            _p("measure_field", "field"),
        ),
        status="declared",
    ),

    # ── EVIDENCE-SHAPING. Which reading of the evidence counts. ─────────────
    Primitive(
        id="prefer_field",
        group="evidence",
        description="Where two columns describe the same thing, use the "
                    "complete one and say which was set aside.",
        params=(
            _p("source", "source"),
            _p("prefer", "field"),
            _p("over", "field"),
        ),
        status="implemented",
        implemented_by="app.crucible.recon.prefer_field",
    ),
    Primitive(
        id="weight_by_account_value",
        group="evidence",
        description="Weight a theme by the revenue of the accounts it touches, "
                    "instead of counting how many raised it.",
        params=(),
        # DECLARED, AND THIS ONE IS THE POINT OF THE STATUS FIELD. It is what a
        # reader assumes is happening when a plan says a theme is "big", and
        # nothing performs it: `score_impact` counts accounts. On a corpus
        # where almost nothing names an account it could not run even if it
        # existed, which is what `account_attribution_gap` says out loud —
        # instead of the run degrading to a count in silence.
        status="declared",
    ),
    Primitive(
        id="weight_by_speaker_authority",
        group="evidence",
        description="Only count a claim from a source entitled to witness it "
                    "— a customer about their own blocker, instrumentation "
                    "about a number.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.claims.AUTHORITATIVE_FOR",
    ),
    Primitive(
        id="weight_by_evidence_strength",
        group="evidence",
        description="Rate measured evidence above reported evidence above "
                    "anything we inferred ourselves.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.claims.DEFAULT_STRENGTH",
    ),
    Primitive(
        id="separate_revealed_from_stated",
        group="evidence",
        description="Keep what actually blocked an account apart from what an "
                    "account said it would like, and rank the first higher.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.moscow.type_bucket",
    ),

    # ── REFUTATION. Trying to kill each candidate before believing it. ──────
    #
    # All four are `pipeline`'s existing kill rules, named so a plan can cite
    # them. The funnel the running view renders counts exactly these codes
    # (`pipeline.NARRATED_DROPS`), which is why the ids match the codes.
    Primitive(
        id="refute_anecdote",
        group="refutation",
        description="Throw out anything resting on a single mention, unless "
                    "it is a blocker, where one mention is the whole point.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.pipeline.MIN_CLAIMS_PER_FINDING",
    ),
    Primitive(
        id="refute_echo",
        group="refutation",
        description="Throw out a pattern whose evidence all comes from one "
                    "conversation echoing through the corpus.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.pipeline._refute",
    ),
    Primitive(
        id="refute_single_account",
        group="refutation",
        description="Throw out anything only one account ever said, unless it "
                    "is that account blocking a deal.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.pipeline._refute",
    ),
    Primitive(
        id="refute_no_authority",
        group="refutation",
        description="Throw out anything no source entitled to witness it "
                    "actually reported.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.pipeline._refute",
    ),

    # ── CONSTRAINT. What the company has already ruled out. ─────────────────
    Primitive(
        id="apply_stated_constraint",
        group="constraint",
        description="Drop any recommendation the company has already said it "
                    "will not do, and say which constraint killed it.",
        params=(
            _p("constraint", "string"),
        ),
        # DECLARED. Stated constraints are PROJECTED as claims today
        # (`pm_manual` is authoritative for `constraint`) and shown, but
        # nothing filters candidates against them.
        status="declared",
    ),

    # ── RANKING AND SELECTION. Deciding what to recommend. ──────────────────
    Primitive(
        id="gate_relevance_to_goal",
        group="ranking",
        description="Set aside anything that surfaced but does not bear on "
                    "the goal you asked about, carrying the reason with it.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.relevance.judge_relevance",
    ),
    Primitive(
        id="score_impact",
        group="ranking",
        description="Size each surviving theme by how much of your book it "
                    "touches — never by how many sources mentioned it.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.scoring.score_impact",
    ),
    Primitive(
        id="score_confidence",
        group="ranking",
        description="Rate how sure we are, separately from how big it is, and "
                    "name which half is the weak one.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.scoring.score_confidence",
    ),
    Primitive(
        id="cap_confidence_without_outcome_evidence",
        group="ranking",
        description="Cap confidence at medium while nothing in your sources "
                    "records whether a fix like this has ever worked.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.scoring.score_confidence",
    ),
    Primitive(
        id="rank_findings",
        group="ranking",
        description="Order what survived: a disagreement between two sources "
                    "first, then blockers, then size, then how sure we are.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.pipeline._rank",
    ),
    Primitive(
        id="select_top_n",
        group="ranking",
        description="Take only the few that get a full write-up, because "
                    "twenty-five equally weighted options is not a decision.",
        params=(
            _p("top_n", "int", required=False, note="defaults to 5"),
        ),
        status="implemented",
        implemented_by="app.crucible.pipeline.DEFAULT_DEEP_CAP",
    ),
    Primitive(
        id="list_cut_candidates",
        group="ranking",
        description="List what was considered and ruled out, with the rule "
                    "that killed each one.",
        params=(),
        status="implemented",
        implemented_by="app.crucible.pipeline.MAX_LISTED_REJECTIONS",
    ),
)

REGISTRY: Mapping[str, Primitive] = {p.id: p for p in _PRIMITIVES}


def _audit() -> None:
    """Fail at IMPORT if the registry contradicts itself.

    An `implemented` entry with no `implemented_by`, an unknown group, an
    unknown status or an unknown param type are all the same bug — the
    registry telling the planner something is safe to emit when nothing
    checked that it is. Catching it at import means a bad entry cannot reach a
    customer's plan; catching it in a test would only mean it cannot reach
    main, and the failure mode here is a document that promises work the
    engine will not do.
    """
    for p in _PRIMITIVES:
        if p.group not in GROUPS:
            raise ValueError(f"primitive {p.id}: unknown group {p.group!r}")
        if p.status not in STATUSES:
            raise ValueError(f"primitive {p.id}: unknown status {p.status!r}")
        if p.is_implemented and not p.implemented_by:
            raise ValueError(
                f"primitive {p.id}: status is 'implemented' but nothing is "
                f"named in implemented_by"
            )
        if not p.is_implemented and p.implemented_by:
            raise ValueError(
                f"primitive {p.id}: 'declared' but names an implementation"
            )
        for param in p.params:
            if param.type not in PARAM_TYPES:
                raise ValueError(
                    f"primitive {p.id}: param {param.name!r} has unknown "
                    f"type {param.type!r}"
                )
            if param.choices and param.type != "enum":
                raise ValueError(
                    f"primitive {p.id}: param {param.name!r} lists choices "
                    f"but is not an enum"
                )
    if len(REGISTRY) != len(_PRIMITIVES):
        raise ValueError("primitive registry has a duplicate id")


_audit()


def implemented() -> tuple[Primitive, ...]:
    """Everything a plan is actually allowed to name today."""
    return tuple(p for p in _PRIMITIVES if p.is_implemented)


def declared_only() -> tuple[Primitive, ...]:
    """Registered, describable, not runnable. Never reaches a plan."""
    return tuple(p for p in _PRIMITIVES if not p.is_implemented)


def by_group(group: str, *, implemented_only: bool = True) -> tuple[Primitive, ...]:
    return tuple(
        p for p in _PRIMITIVES
        if p.group == group and (p.is_implemented or not implemented_only)
    )


def catalogue(*, implemented_only: bool = True) -> str:
    """The vocabulary, as the text the planner shows a model.

    GENERATED, NEVER HAND-MAINTAINED. A prompt listing the primitives by hand
    is a second registry that drifts from the first, and the direction it
    drifts is always the same: the prompt keeps offering an operation that was
    downgraded to `declared`, and the model keeps emitting it.
    """
    lines: list[str] = []
    for group in GROUPS:
        entries = by_group(group, implemented_only=implemented_only)
        if not entries:
            continue
        lines.append(f"[{group}]")
        for p in entries:
            args = ", ".join(
                f"{a.name}: {a.type}" + ("" if a.required else " (optional)")
                for a in p.params
            ) or "no parameters"
            lines.append(f"  {p.id}({args}) — {p.description}")
        lines.append("")
    return "\n".join(lines).strip()


# ── VALIDATION ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StepProblem:
    """One reason a proposed step cannot be run.

    Carries the step's position rather than the step itself so a caller can
    report several problems against one step without repeating it, and so the
    message is stable enough to assert on.
    """
    index: int
    primitive: str
    problem: str


def validate_steps(
    steps: Sequence[Mapping[str, Any]],
    *,
    available_sources: Iterable[str] = (),
    implemented_only: bool = True,
) -> tuple[StepProblem, ...]:
    """Can every one of these steps actually be run against THIS run?

    Three separate questions, and they are separate because they fail for
    different reasons and a caller does different things about each:

      1. Does the primitive exist, and is it runnable? A miss here is the
         model inventing an operation, which is what the closed vocabulary
         exists to catch.
      2. Are the parameters present and of the declared type? A miss here is
         usually the model supplying a field name where a list was wanted.
      3. Is every source the step names actually present in this run? A miss
         here is the model planning over evidence the customer has not
         connected — the single most expensive kind of wrong plan, because it
         reads as a promise.

    `available_sources` empty means "do not check sources" rather than "no
    sources exist". A caller that genuinely knows the run has nothing to read
    would not be building a plan at all, and treating an unset argument as an
    empty world would fail every step for a caller that simply did not have
    the inventory to hand.
    """
    known = {str(s) for s in available_sources if str(s).strip()}
    problems: list[StepProblem] = []

    for i, step in enumerate(steps):
        pid = str(step.get("primitive") or "").strip()
        prim = REGISTRY.get(pid)
        if prim is None:
            problems.append(StepProblem(i, pid, "no such primitive"))
            continue
        if implemented_only and not prim.is_implemented:
            problems.append(StepProblem(
                i, pid,
                "is declared but not implemented, so a plan must not name it",
            ))
            continue

        params = step.get("params")
        params = params if isinstance(params, Mapping) else {}
        declared_names = {a.name for a in prim.params}
        for extra in sorted(set(params) - declared_names):
            problems.append(StepProblem(
                i, pid, f"parameter {extra!r} is not declared by this primitive"))
        for arg in prim.params:
            if arg.name not in params:
                if arg.required:
                    problems.append(StepProblem(
                        i, pid, f"required parameter {arg.name!r} is missing"))
                continue
            bad = _type_problem(arg, params[arg.name])
            if bad:
                problems.append(StepProblem(i, pid, f"parameter {arg.name!r} {bad}"))

        if known:
            for name in _sources_named(step, params):
                if name not in known:
                    problems.append(StepProblem(
                        i, pid,
                        f"names source {name!r}, which is not present in this run",
                    ))

    return tuple(problems)


def _type_problem(arg: Param, value: Any) -> Optional[str]:
    """`None` when the value is acceptable, else why it is not."""
    if arg.type in ("field", "source", "string"):
        if not isinstance(value, str) or not value.strip():
            return "must be a non-empty string"
        return None
    if arg.type in ("fields", "sources"):
        if not isinstance(value, (list, tuple)) or not value:
            return "must be a non-empty list of strings"
        if not all(isinstance(v, str) and v.strip() for v in value):
            return "must contain only non-empty strings"
        return None
    if arg.type == "int":
        # `bool` is an `int` in Python and is never what a step meant by one.
        if isinstance(value, bool) or not isinstance(value, int):
            return "must be a whole number"
        return None
    if arg.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "must be a number"
        return None
    if arg.type == "enum":
        if value not in arg.choices:
            return f"must be one of {', '.join(arg.choices)}"
        return None
    return f"has unknown declared type {arg.type!r}"


def _sources_named(step: Mapping[str, Any], params: Mapping[str, Any]) -> tuple[str, ...]:
    """Every source this step claims to read.

    Reads BOTH the step's own `sources` list and any parameter declared as a
    source type. The two are different things a step can get wrong — the
    top-level list is what the plan renders beside the step, the parameters
    are what the operation would actually open — and a step whose rendered
    sources and actual sources disagree is a plan that misdescribes itself.
    """
    out: list[str] = []
    top = step.get("sources")
    if isinstance(top, (list, tuple)):
        out.extend(str(s) for s in top if isinstance(s, str) and s.strip())
    for key, value in params.items():
        prim_param = None
        pid = str(step.get("primitive") or "")
        prim = REGISTRY.get(pid)
        if prim:
            prim_param = next((a for a in prim.params if a.name == key), None)
        if prim_param is None:
            continue
        if prim_param.type == "source" and isinstance(value, str):
            out.append(value)
        elif prim_param.type == "sources" and isinstance(value, (list, tuple)):
            out.extend(str(v) for v in value if isinstance(v, str))
    return tuple(dict.fromkeys(out))

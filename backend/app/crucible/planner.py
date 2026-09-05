"""Write the plan: what this run will do, in order, and why each step matters.

WHAT CHANGED AND WHY. The plan gate used to be a coverage report. It counted
rows per source type, listed what could not be answered, and said nothing at
all about METHOD — a reader approving it was approving a promise to "read your
revenue data", which is not a procedure and cannot be wrong. This turns it into
a plan: a numbered sequence of operations, each naming a registered primitive,
each with a sentence saying what it gets us for THIS decision.

THE DIVISION OF LABOUR, WHICH IS THE WHOLE DESIGN.

  · `recon` reads the evidence and produces NUMBERS. Deterministic, no model.
  · `primitives` declares the closed vocabulary of operations. No model.
  · The model here does exactly two things: it chooses which operations to
    compose, and it writes the prose. It returns no score, no rank, no
    threshold, and no numeric — I2 holds, unchanged.

Every figure that appears in a generated sentence is checked back against the
observations before the plan is stored (`untraceable_figures`). A step whose
`why` contains a number that came from the model rather than from the evidence
is DROPPED, not corrected — the same "grounded or dropped" contract
`recommend._grounded_in` applies to a cited claim, applied here to a cited
figure. That check is what makes it safe to let a model write the reasoning at
all: it can be persuasive, and it cannot be quantitative.

DRAWN ONCE, THEN READ. A model call is a draw, not a lookup. `relevance`
already paid for learning that the hard way — two draws over an identical
corpus kept 38 and then 49 of the same 60 findings — and a plan re-sampled on
every render would be worse, because the reader has APPROVED one of the
samples. So the steps are generated once, stored on the run's plan, and read
back on every subsequent open, approve and render. Re-generating to check
would not be verification; it would be a second sample.

THE HONESTY INVARIANT. Only `implemented` primitives may be emitted. The
vocabulary contains operations the engine cannot yet run — cohort
decomposition, trend fitting, applying a stated constraint as a filter — and
they are registered precisely so the gap is recorded somewhere. They must
never reach a plan. A plan is a promise the run keeps, and the one failure this
whole stage exists to remove is a document that describes work that does not
happen.
"""
from __future__ import annotations

import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Sequence

from app.crucible import primitives as prim
from app.crucible.recon import Observation, ReconReport

logger = logging.getLogger(__name__)

#: The named parts a plan groups its steps under, in order.
#:
#: NAMED IN THE READER'S REGISTER, NOT THE PIPELINE'S. "Scoping / measurement /
#: refutation / ranking" is the code's decomposition and it is a fine one; it
#: is also four words a product manager has no reason to care about. These are
#: the same five phases said as what a person is trying to find out.
PARTS: tuple[str, ...] = (
    "Get the unit right",
    "Work out where the money is",
    "Understand why",
    "Throw out what will not hold",
    "Decide what to recommend",
)

#: Which part each primitive group belongs under. Derived from the registry so
#: a new primitive lands in a part automatically rather than needing a second
#: table maintained alongside the first.
_PART_FOR_GROUP: Mapping[str, str] = {
    "scoping": PARTS[0],
    "measurement": PARTS[1],
    "evidence": PARTS[2],
    "refutation": PARTS[3],
    "constraint": PARTS[4],
    "ranking": PARTS[4],
}

#: Where the steps live on the run.
#:
#: ON THE PLAN ITSELF, not in a sibling key beside it. `relevance` stores its
#: verdicts under their own `prioritisation` key because a verdict is a
#: per-finding fact with no natural home in the plan; steps ARE the plan, the
#: report renders them from `plan`, and a second copy in a sibling key is two
#: things that can disagree about what was approved. The discipline is
#: identical — written once, read back, never re-drawn — only the address
#: differs.
STEPS_KEY = "steps"
#: Bumped only if the stored shape changes incompatibly. Unrecognised reads as
#: absent, which regenerates — the direction everything here fails in.
STEPS_VERSION = 1

#: How many times a rejected draw is retried before the deterministic plan is
#: used instead. ONE. A second retry is a third sample, and choosing among
#: samples by which one passed validation is a slower way of drawing until you
#: like the answer; the fallback is a real plan, not a failure state.
MAX_REGENERATIONS = 1

#: Numbers a plan may state that come from the ENGINE rather than the
#: evidence. These are facts about how this pipeline is configured, they are
#: as checkable as an observation (they are constants in this repo), and
#: without them a step saying "only the five that get a full write-up" would
#: be dropped for citing an untraceable figure. Read from the modules that own
#: them so the plan cannot quote a cap the engine no longer applies.
def _engine_figures() -> dict[str, float]:
    from app.crucible.pipeline import (
        DEFAULT_DEEP_CAP, MAX_LISTED_REJECTIONS, MIN_CLAIMS_PER_FINDING,
    )
    from app.crucible.recon import DEFAULT_TOP_N

    return {
        "deep_cap": float(DEFAULT_DEEP_CAP),
        "min_claims": float(MIN_CLAIMS_PER_FINDING),
        "listed_rejections": float(MAX_LISTED_REJECTIONS),
        "top_n": float(DEFAULT_TOP_N),
    }


@dataclass(frozen=True)
class PlanStep:
    """One numbered thing the run will do.

    `what` is an ACTION PHRASE and `why` is what it gets us. They are separate
    fields rather than one paragraph because they are read at different
    speeds: a reader scans the `what` column to see the shape of the method
    and stops at the one `why` they want to argue with.
    """
    n: int
    part: str
    primitive: str
    params: Mapping[str, Any] = field(default_factory=dict)
    what: str = ""
    why: str = ""
    sources: tuple[str, ...] = ()
    #: The observation ids whose figures this step's `why` rests on. Empty is
    #: legitimate and common — most steps state a rule, not a number.
    observations: tuple[str, ...] = ()

    def to_json(self) -> dict:
        d = asdict(self)
        d["params"] = dict(self.params)
        d["sources"] = list(self.sources)
        d["observations"] = list(self.observations)
        return d


def steps_from_json(blob: Any) -> tuple[PlanStep, ...]:
    """Rebuild stored steps. Malformed entries are skipped, never raised on: a
    plan stored before this shipped has no steps, and that must read as
    'none', not as a broken run."""
    if not isinstance(blob, list):
        return ()
    out: list[PlanStep] = []
    for item in blob:
        if not isinstance(item, Mapping):
            continue
        params = item.get("params")
        out.append(PlanStep(
            n=int(item.get("n") or 0),
            part=str(item.get("part") or ""),
            primitive=str(item.get("primitive") or ""),
            params=dict(params) if isinstance(params, Mapping) else {},
            what=str(item.get("what") or ""),
            why=str(item.get("why") or ""),
            sources=tuple(str(s) for s in (item.get("sources") or [])),
            observations=tuple(str(s) for s in (item.get("observations") or [])),
        ))
    return tuple(out)


def load_steps(run_meta: Mapping[str, Any]) -> Optional[tuple[PlanStep, ...]]:
    """The steps already drawn for this run, or None if none ever were.

    NONE AND `()` ARE DIFFERENT ANSWERS, for the reason `relevance.
    load_verdicts` spells out: an empty result is a real outcome that must not
    trigger a second draw, and only the absence of the key means the planner
    has never run here.
    """
    if not isinstance(run_meta, Mapping):
        return None
    plan = run_meta.get("plan")
    if not isinstance(plan, Mapping) or STEPS_KEY not in plan:
        return None
    return steps_from_json(plan.get(STEPS_KEY))


# ── FIGURE VERIFICATION ─────────────────────────────────────────────────────

#: Anything that reads as a quantity: 12, 12.5, 1,234, $1,234, 37.4%.
_FIGURE_RE = re.compile(r"[-+]?\$?\d[\d,]*(?:\.\d+)?%?")


def _numbers_in(text: str) -> list[float]:
    out: list[float] = []
    for raw in _FIGURE_RE.findall(text or ""):
        cleaned = raw.replace("$", "").replace(",", "").rstrip("%")
        try:
            out.append(float(cleaned))
        except ValueError:
            continue
    return out


def _matches(x: float, allowed: Sequence[float]) -> bool:
    """Is `x` one of the allowed figures, allowing for how a figure is WRITTEN?

    Three forms, because one number legitimately appears three ways in prose:
    as itself (519574), rounded (8.9 for 8.88), and as a percentage of a share
    (37.4 for 0.3743). Anything else is the model having produced a quantity,
    which is exactly what it is not allowed to do.
    """
    for a in allowed:
        if abs(x - a) <= _tolerance(a):
            return True
        scaled = a * 100.0
        if abs(x - scaled) <= _tolerance(scaled):
            return True
    return False


#: How far a written figure may sit from a measured one and still be that
#: figure.
#:
#: TIGHTENED FROM AN ABSOLUTE 0.5, AND A TEST CAUGHT WHY. The gate's
#: false-accept rate scales with how many figures are in play: at 0.5, "41%"
#: matched a measured 40.6% and an assertion that an invented number gets
#: caught started passing for the wrong reason the moment a new check added
#: eight more figures to the allowed set. The floor now admits a one-decimal
#: rendering of a measured value (8.88 written as "8.9") and nothing looser.
#:
#: An approximation is therefore DROPPED, not accepted. That is the right
#: direction: the model is handed the exact figures and told to use them, and
#: a step that rounds to "about 41%" loses nothing a reader needed.
_ABSOLUTE_TOLERANCE = 0.05
_RELATIVE_TOLERANCE = 0.005


def _tolerance(a: float) -> float:
    return max(_ABSOLUTE_TOLERANCE, abs(a) * _RELATIVE_TOLERANCE)


def untraceable_figures(
    text: str,
    observations: Sequence[Observation],
    *,
    extra: Sequence[float] = (),
) -> tuple[str, ...]:
    """Every quantity in `text` that is not in the evidence.

    THE GATE THAT MAKES A MODEL-WRITTEN PLAN SAFE. A generated sentence is
    persuasive by construction, and the failure mode of persuasive prose about
    data is a plausible number nobody measured. Mirrors the claim-id gate in
    `recommend`: there, a proposed change must name the claim it rests on and
    share that claim's vocabulary; here, a stated figure must be findable in
    an observation's `figures` — the numbers as `recon` computed them, not as
    the sentence formatted them.

    Empty return means every number in the sentence came from the evidence or
    from the engine's own declared configuration.
    """
    allowed: list[float] = list(extra)
    for o in observations:
        allowed.extend(float(v) for v in o.figures.values())
    if not allowed:
        allowed = [float("nan")]  # nothing is traceable against nothing
    bad: list[str] = []
    for raw in _FIGURE_RE.findall(text or ""):
        cleaned = raw.replace("$", "").replace(",", "").rstrip("%")
        try:
            x = float(cleaned)
        except ValueError:
            continue
        if not _matches(x, allowed):
            bad.append(raw)
    return tuple(bad)


def cites_a_figure(text: str, observations: Sequence[Observation]) -> bool:
    """Does `text` quote at least one figure from these observations?

    THE OTHER HALF OF THE GATE. `untraceable_figures` punishes a step for
    citing a number it cannot support, and for a long time nothing punished a
    step for omitting a number it was handed — so a step derived from "4 of
    1,275 signals name an account" could say "very few" and pass. Measured
    against the real model, every one of eleven observation-backed steps did
    exactly that.

    The prompt is what stopped it happening; this is what stops it shipping if
    the prompt ever drifts, and it is deliberately the same comparison run the
    other way: a figure counts as cited when it MATCHES one the observation
    actually carries, so a step cannot satisfy this by inventing a number that
    the traceability half would then reject.
    """
    allowed = [float(v) for o in observations for v in o.figures.values()]
    if not allowed:
        return True
    return any(_matches(x, allowed) for x in _numbers_in(text))


def verify(
    steps: Sequence[PlanStep],
    observations: Sequence[Observation],
    *,
    available_sources: Sequence[str] = (),
    #: The run's own countable facts — table count, source count, records.
    #: See `ReconReport.inventory_figures`: a step may say how much it is
    #: about to read without that being a claim about what is in it.
    inventory: Sequence[float] = (),
) -> tuple[list[PlanStep], list[str]]:
    """Keep the steps that survive both gates; say why the others did not.

    Two gates, applied in this order because they fail for different reasons:
    the registry says whether the step could be RUN, and the figure check says
    whether its sentence is TRUE. A step that fails either is dropped rather
    than repaired — repairing a generated sentence means writing the sentence
    ourselves, at which point the model added nothing and we have lost the
    audit trail of what it actually produced.
    """
    kept: list[PlanStep] = []
    dropped: list[str] = []
    engine = list(_engine_figures().values()) + [float(v) for v in inventory]
    obs_by_id = {o.id: o for o in observations}

    problems = prim.validate_steps(
        [s.to_json() for s in steps], available_sources=available_sources,
    )
    bad_indexes = {p.index for p in problems}
    for p in problems:
        dropped.append(f"step {p.index + 1} ({p.primitive}): {p.problem}")

    for i, step in enumerate(steps):
        if i in bad_indexes:
            continue
        unknown = [oid for oid in step.observations if oid not in obs_by_id]
        if unknown:
            dropped.append(
                f"step {i + 1} ({step.primitive}): names observation(s) "
                f"{', '.join(unknown)}, which this run did not make"
            )
            continue
        # Numbers in the step's own parameters are part of the STEP, not a
        # claim about the evidence — `top_n=3` may be said out loud.
        extra = list(engine) + [
            float(v) for v in step.params.values()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        bad = untraceable_figures(
            f"{step.what} {step.why}", observations, extra=extra,
        )
        if bad:
            dropped.append(
                f"step {i + 1} ({step.primitive}): states "
                f"{', '.join(bad)}, which is not in any observation"
            )
            continue
        # A STEP DRAWN FROM A MEASUREMENT HAS TO REPORT IT. Both directions of
        # the same gate: a step may not state a figure it cannot support, and
        # may not withhold one it was given. Spine steps that rest on no
        # observation state a rule rather than a quantity and are untouched.
        cited = [obs_by_id[oid] for oid in step.observations]
        if cited and not cites_a_figure(f"{step.what} {step.why}", cited):
            dropped.append(
                f"step {i + 1} ({step.primitive}): rests on "
                f"{', '.join(step.observations)} and quotes none of its "
                f"figures, so it hedges where it has the answer"
            )
            continue
        kept.append(step)

    return _renumber(kept), dropped


def _renumber(steps: Sequence[PlanStep]) -> list[PlanStep]:
    """Order by part, then keep the order within a part, then number 1..N.

    Numbering is assigned HERE and never taken from the model. A model that
    numbers its own steps produces gaps the moment one is dropped, and a plan
    that jumps from 4 to 6 makes a reader hunt for the step that is not there.
    """
    order = {p: i for i, p in enumerate(PARTS)}
    ranked = sorted(
        enumerate(steps), key=lambda kv: (order.get(kv[1].part, len(PARTS)), kv[0]),
    )
    out: list[PlanStep] = []
    for n, (_, s) in enumerate(ranked, start=1):
        out.append(PlanStep(
            n=n, part=s.part or _part_for(s.primitive),
            primitive=s.primitive, params=dict(s.params), what=s.what,
            why=s.why, sources=tuple(s.sources), observations=tuple(s.observations),
        ))
    return out


def _part_for(primitive: str) -> str:
    """The part a step belongs under when the caller did not name one.

    Derived from the registry rather than defaulted to the last part, so a
    model that returns a valid primitive with a blank `part` still lands
    somewhere coherent instead of piling every unlabelled step under
    "Decide what to recommend".
    """
    p = prim.REGISTRY.get(primitive)
    return _PART_FOR_GROUP.get(p.group, PARTS[-1]) if p else PARTS[-1]


# ── THE DETERMINISTIC PLAN ──────────────────────────────────────────────────


#: How many sources are NAMED in the opening sentence before it stops listing
#: and starts counting. Three, because a sentence naming four things is a list
#: and a reader skims a list.
MAX_NAMED_SOURCES = 3


def _evidence_sentence(report: ReconReport, source_types: Sequence[str]) -> str:
    """What this run will read, said as a sentence rather than an inventory.

    Counts TABLES and SOURCES separately, because six sheets of one workbook
    are one source to a reader and calling them six would overstate what is
    connected. Falls back to the source types when nothing was read
    structurally, which is every prose-only tenant.
    """
    origins: list[str] = []
    for s in report.sources:
        if s.origin and s.origin not in origins:
            origins.append(s.origin)
    if not origins:
        origins = [str(t).replace("_", " ") for t in source_types]

    if not origins:
        return ("Everything below is computed over the sources you connected "
                "and nothing else, so a figure in the finished document can "
                "always be traced back to one of them.")

    shown = origins[:MAX_NAMED_SOURCES]
    named = ", ".join(shown[:-1]) + (" and " + shown[-1] if len(shown) > 1 else shown[0])
    if len(origins) > MAX_NAMED_SOURCES:
        named += f", and {len(origins) - MAX_NAMED_SOURCES} more"

    tables = len(report.sources)
    scope = (f"{tables} tables across {len(origins)} sources"
             if tables > len(origins) else
             f"{len(origins)} source{'s' if len(origins) != 1 else ''}")

    return (f"Everything below is computed over {scope} — {named} — and "
            f"nothing else, so a figure in the finished document can always "
            f"be traced back to something you connected.")


def _obs_step(
    primitive: str, o: Observation, what: str, why: str,
    params: Mapping[str, Any], part: str = "",
) -> PlanStep:
    return PlanStep(
        n=0, part=part or _part_for(primitive),
        primitive=primitive, params=dict(params), what=what, why=why,
        # THE LABEL, NOT THE KEY. `sources` is what a renderer shows beside
        # the step; the key it addresses lives on `params`, which is machine
        # -facing. `verify` accepts either vocabulary — see `build_steps`.
        sources=(o.source_label,), observations=(o.id,),
    )


def _plain(primitive: str, what: str, why: str,
           params: Optional[Mapping[str, Any]] = None,
           sources: Sequence[str] = (), part: str = "") -> PlanStep:
    """`part` overrides the registry's group→part mapping for one authored
    step. Used sparingly and only where the mapping reads wrong in sequence:
    choosing which of two value columns wins is evidence-shaping by group, but
    it settles the reconciliation directly above it and belongs beside it."""
    return PlanStep(
        n=0, part=part or _part_for(primitive),
        primitive=primitive, params=dict(params or {}), what=what, why=why,
        sources=tuple(sources),
    )


def minimal_plan(
    *,
    goal_text: str,
    currency: str,
    report: Optional[ReconReport] = None,
    source_types: Sequence[str] = (),
) -> list[PlanStep]:
    """The plan the engine can write without a model at all.

    NOT A FAILURE STATE. This is a real, complete, runnable plan: every step
    names an implemented primitive, the observation-driven steps carry the
    actual figures `recon` measured, and the whole thing is deterministic. The
    model's contribution on the normal path is COMPOSITION and PROSE — which
    of these matter for this particular goal, in what order, said in the
    reader's own terms. Losing that is a worse plan, not a broken one, which
    is the property that makes it safe to drop a bad draw rather than patch it.

    It is also what runs under test and offline, so it is exercised far more
    than the generated path and cannot quietly rot.
    """
    report = report or ReconReport()
    obs = report.observations
    steps: list[PlanStep] = []

    # ── Get the unit right. ────────────────────────────────────────────────
    #
    # THE FIRST THING A READER SEES IS A SENTENCE, NOT AN INVENTORY. This step
    # used to join every source it had into one line, which on a real upload
    # rendered sixteen storage keys — `03_product_analytics:activation_funnel`
    # and fifteen more — as the opening of a document whose entire purpose is
    # to be read. Enumerating is not describing. The count and the named
    # sources go in the prose; the full list stays on the parameters, where an
    # operation is being addressed rather than a person.
    steps.append(_plain(
        "select_evidence",
        "Fix which sources this answer may rest on",
        _evidence_sentence(report, source_types),
        params={"sources": list(source_types) or ["all"]},
        sources=source_types,
    ))
    # ── WHAT THE EVIDENCE CANNOT SUPPORT, SAID BEFORE IT IS RELIED ON. ────
    #
    # THE HONEST VERSION OF GRACEFUL DEGRADATION. The engine already counts
    # accounts rather than weighting by revenue, on every corpus, silently —
    # and a reader has no way to tell a considered count from a weighting that
    # quietly failed. Stated here it is the opposite: the run says what it
    # would have taken, what it actually has, and which of the two it is
    # therefore doing.
    for o in report.of_kind("account_attribution_gap")[:MAX_DETERMINISTIC_PER_KIND]:
        steps.append(_obs_step(
            "audit_signal_field_coverage", o,
            "Check how much of the evidence names an account",
            f"Only {o.figures['present']:,.0f} of "
            f"{o.figures['signals']:,.0f} signals name one, so weighting a "
            f"theme by the revenue behind it would rest on "
            f"{o.figures['share'] * 100:.1f}% of what I read. I will count "
            f"accounts instead of weighting them, and the report will say that "
            f"is what happened rather than presenting the count as a "
            f"valuation.",
            params={"field": (list(o.fields) or ["properties.account"])[0]},
        ))
    for o in report.of_kind("monetary_coverage_gap")[:MAX_DETERMINISTIC_PER_KIND]:
        steps.append(_obs_step(
            "audit_signal_field_coverage", o,
            "Check whether anything here carries a figure",
            f"{o.figures['present']:,.0f} of {o.figures['signals']:,.0f} "
            f"signals do. Every size in the finished document is therefore "
            f"stated in accounts touched, never in money — not because money "
            f"is unimportant, but because nothing connected here measures it.",
            params={"field": (list(o.fields) or ["properties.amount"])[0]},
        ))
    for o in report.of_kind("dating_unreliable")[:MAX_DETERMINISTIC_PER_KIND]:
        steps.append(_obs_step(
            "check_dating_reliability", o,
            "Check whether the dates mean anything",
            f"{o.figures['ingest_clock_share'] * 100:.1f}% of "
            f"{o.figures['signals']:,.0f} signals are dated within "
            f"{o.figures['tolerance_seconds']:.0f} seconds of when we imported "
            f"them, so the dates are the import and not the events. Nothing "
            f"is weighted by recency, and the rule that throws out one "
            f"conversation echoing is switched off — over these dates it would "
            f"throw out everything.",
            params={},
            part=PARTS[1],
        ))
    for o in report.of_kind("source_concentration")[:MAX_DETERMINISTIC_PER_KIND]:
        steps.append(_obs_step(
            "check_source_concentration", o,
            "Count how many documents this actually rests on",
            f"{o.figures['signals']:,.0f} signals come from "
            f"{o.figures['documents']:.0f} documents, and the largest single "
            f"one is {o.figures['top_share'] * 100:.1f}% of everything. Two "
            f"claims agreeing may be one document read twice, so corroboration "
            f"is judged on distinct documents rather than on how many rows say "
            f"the same thing.",
            params={},
            part=PARTS[1],
        ))
    for o in report.of_kind("claim_mix")[:MAX_DETERMINISTIC_PER_KIND]:
        steps.append(_obs_step(
            "characterise_claim_mix", o,
            "Say what kind of thing this evidence mostly is",
            f"{o.figures['top_share'] * 100:.1f}% of it is a single kind. A "
            f"ranking over evidence that is mostly one shape will reflect that "
            f"shape, and you should know which one before you read the order.",
            params={},
        ))
    for o in report.of_kind("evidence_mix")[:MAX_DETERMINISTIC_PER_KIND]:
        steps.append(_obs_step(
            "characterise_evidence_mix", o,
            "Say what kind of evidence this actually is",
            f"Only {o.figures['firsthand_share'] * 100:.1f}% of what is "
            f"classified is a customer speaking firsthand; the rest is the "
            f"company describing itself. A revenue answer built mostly from "
            f"internal documents can still be right, but you should know that "
            f"is what it is before you act on it.",
            params={},
        ))
    unit_obs = report.of_kind("unit_value_derivable")
    if unit_obs:
        o = unit_obs[0]
        steps.append(_obs_step(
            "set_counting_unit", o,
            "Count in accounts, and price an account from your own contracts",
            f"Your contracts already say what each account is worth, so this "
            f"does not have to be guessed at: the median account is "
            f"{o.figures['median']:,.0f} and the mean is "
            f"{o.figures['mean']:,.0f}. Sizing against the median rather than "
            f"the mean stops a handful of very large accounts from making "
            f"every theme look bigger than it is.",
            params={"unit": currency},
        ))
    else:
        steps.append(_plain(
            "set_counting_unit",
            "Fix the unit every size is stated in",
            f"Sizes are stated in {currency} throughout, so two numbers are "
            f"never added that do not share a unit.",
            params={"unit": currency},
        ))

    # ── Work out where the money is. ───────────────────────────────────────
    for o in report.of_kind("value_columns_disagree")[:MAX_DETERMINISTIC_PER_KIND]:
        base, total, explained = (list(o.fields) + ["", "", ""])[:3]
        steps.append(_obs_step(
            "reconcile_value_columns", o,
            f"Reconcile {base} against {total} before either is used",
            f"Both columns read as the account's value and they disagree on "
            f"{o.figures['rows_differing']:,.0f} of "
            f"{o.figures['rows_compared']:,.0f} rows. The whole gap is "
            f"{explained}, so taking the smaller one understates the book by "
            f"{o.figures['gap_total']:,.0f} — an error that would then be "
            f"carried into every theme sized against it.",
            params={"source": o.source, "base_field": base, "total_field": total},
        ))
        steps.append(_obs_step(
            "prefer_field", o,
            f"Use {total} and record that {base} was set aside",
            f"One of the two has to win, and it should be the complete one — "
            f"the {o.figures['gap_total']:,.0f} difference is real money that "
            f"the smaller column simply does not carry. Saying which was "
            f"dropped is what lets you check this against your own reporting "
            f"rather than wonder why the totals differ.",
            params={"source": o.source, "prefer": total, "over": base},
            part=PARTS[1],
        ))
    for o in report.of_kind("concentration_divergence")[:MAX_DETERMINISTIC_PER_KIND]:
        group, value = (list(o.fields) + ["", ""])[:2]
        steps.append(_obs_step(
            "compare_measures_across_groups", o,
            "Check whether the loudest accounts are the valuable ones",
            f"The top {o.figures['top_n']:.0f} of "
            f"{o.figures['groups']:.0f} accounts generate "
            f"{o.figures['volume_share'] * 100:.1f}% of the activity and hold "
            f"{o.figures['value_share'] * 100:.1f}% of the money. Anything "
            f"ranked by how often it comes up will put those accounts first, "
            f"and that is not where the revenue is — this is the one check "
            f"that stops the whole answer from being about the noisiest "
            f"customers.",
            params={"group_field": group, "volume_field": o.source,
                    "value_field": value},
        ))

    # ── Understand why. ────────────────────────────────────────────────────
    for o in report.of_kind("censored_periods")[:MAX_DETERMINISTIC_PER_KIND]:
        key, size, last = (list(o.fields) + ["", "", ""])[:3]
        steps.append(_obs_step(
            "check_period_censoring", o,
            "Count retention only over cohorts old enough to have one",
            f"{o.figures['immature_cohorts']:.0f} of "
            f"{o.figures['cohorts']:.0f} cohorts have zeros in the later "
            f"periods because those months have not happened yet. Dividing "
            f"the last period by every cohort gives "
            f"{o.figures['naive_rate'] * 100:.1f}%; over the "
            f"{o.figures['mature_cohorts']:.0f} that have a full window it is "
            f"{o.figures['mature_rate'] * 100:.1f}%. That is a "
            f"{o.figures['error_points']:.1f} point error in the direction "
            f"that invents a crisis, so the run reports the second figure and "
            f"says which cohorts it counted.",
            params={"source": o.source, "period_field": key},
            part=PARTS[1],
        ))
    for o in report.of_kind("coding_gap")[:MAX_DETERMINISTIC_PER_KIND]:
        coded, text = (list(o.fields) + ["", ""])[:2]
        steps.append(_obs_step(
            "audit_field_coverage", o,
            f"Count how often {coded} is actually filled in",
            f"It is empty on {o.figures['missing']:,.0f} of "
            f"{o.figures['rows']:,.0f} rows, and on "
            f"{o.figures['missing_with_text']:,.0f} of those someone wrote "
            f"the answer in {text} instead. Counting the coded field alone "
            f"would quietly throw those away and report the remainder as if "
            f"it were everything.",
            params={"source": o.source, "field": coded},
        ))
    for o in report.of_kind("stage_collapse")[:MAX_DETERMINISTIC_PER_KIND]:
        a, b = (list(o.fields) + ["", ""])[:2]
        steps.append(_obs_step(
            "check_stage_collapse", o,
            f"Check whether {a} and {b} are really two steps",
            f"They hold the same value on every one of "
            f"{o.figures['rows']:.0f} rows, so there is no drop-off to read "
            f"between them and any conversion rate across the pair is 100% "
            f"by construction. Worth knowing before a funnel chart says the "
            f"stage is healthy.",
            params={"source": o.source, "fields": [a, b]},
        ))
    steps.append(_plain(
        "weight_by_speaker_authority",
        "Only count a claim from a source entitled to make it",
        "A customer is authoritative about their own blocker; instrumentation "
        "is authoritative about a number. A claim made outside its source's "
        "authority is recorded and not counted, so nothing rests on a "
        "vendor's own description of the problem.",
    ))
    steps.append(_plain(
        "weight_by_evidence_strength",
        "Rate measured evidence above reported evidence",
        "What a system recorded outranks what someone said happened, which "
        "outranks anything we inferred ourselves — and the weakest link is "
        "named on the finding rather than averaged away.",
    ))
    steps.append(_plain(
        "separate_revealed_from_stated",
        "Keep what blocked a deal apart from what someone asked for",
        "A blocker and a feature request read the same in a transcript and "
        "mean opposite things for revenue. What actually stopped money moving "
        "ranks above what a customer said would be nice.",
    ))

    # ── Throw out what will not hold. ──────────────────────────────────────
    steps.append(_plain(
        "refute_anecdote",
        "Drop anything resting on a single mention",
        "One person saying something once is not a pattern. The exception is "
        "a blocker, where one mention is the whole point — a blocked deal is "
        "specific to that deal by definition.",
    ))
    # THE ECHO RULE IS NOT PROMISED WHEN THE RUN WILL NOT APPLY IT.
    #
    # `pipeline._refute` skips it on a corpus dated by the ingest clock,
    # because over those dates every cluster looks like one conversation and
    # the run would return nothing. The plan listed it anyway, so a prose
    # tenant got a document that said the rule was switched off in one step and
    # promised it four steps later — describing work that does not happen,
    # which is the single failure this whole stage exists to remove. The
    # observation that reports the dating is the same one the pipeline's own
    # detector produces, so the two cannot disagree.
    if not report.of_kind("dating_unreliable"):
        steps.append(_plain(
            "refute_echo",
            "Drop patterns that are one conversation echoing",
            "Nine quotes from one call is one data point, not nine, and it is "
            "the shape that most reliably fools this kind of analysis.",
        ))
    steps.append(_plain(
        "refute_single_account",
        "Drop anything only one account ever said",
        "That is that account's situation rather than something true across "
        "your book — again unless it is that account blocking a deal.",
    ))
    steps.append(_plain(
        "refute_no_authority",
        "Drop anything no entitled source actually reported",
        "If every supporting claim came from a source that may not speak to "
        "it, there is nothing underneath the finding.",
    ))

    # ── Decide what to recommend. ──────────────────────────────────────────
    # NOTHING THE READER TYPED IS INTERPOLATED INTO A `why`. The first version
    # of this step named the goal in its sentence, and a goal of "grow revenue
    # 15% this year" put "15%" into a plan sentence — a figure from nowhere,
    # which `untraceable_figures` correctly deleted the whole step for. The
    # gate was right and the step was wrong: a goal string is user text, it
    # frequently carries a number, and a number in a `why` is a claim about
    # the evidence. The goal is already rendered at the top of the plan.
    steps.append(_plain(
        "gate_relevance_to_goal",
        "Set aside what surfaced but does not bear on the goal",
        "Plenty of real themes have nothing to do with the question you "
        "asked. Those move to an appendix carrying the reason they were set "
        "aside, so you see the whole funnel rather than a shorter list.",
    ))
    steps.append(_plain(
        "score_impact",
        "Size each surviving theme by how much of the book it touches",
        "Size is how many accounts it affects — never how many sources "
        "mentioned it. Those are different questions and conflating them is "
        "how the obvious thing wins every time.",
    ))
    steps.append(_plain(
        "score_confidence",
        "Rate how sure we are, separately from how big it is",
        "A big finding we are unsure of and a small one we are certain of "
        "need different responses, and one blended number hides which is "
        "which. The weaker half is named.",
    ))
    if "outcome_measured" not in set(source_types):
        steps.append(_plain(
            "cap_confidence_without_outcome_evidence",
            "Cap confidence at medium and say why",
            "Nothing connected records whether a fix like this has ever "
            "worked, so no finding here can be more than moderately "
            "confident. That is a statement about the evidence, not about "
            "any one finding.",
        ))
    steps.append(_plain(
        "rank_findings",
        "Order what survived",
        "Two sources that may both speak disagreeing goes first, because it "
        "is worth more than either claim. Then blockers, then size, then how "
        "sure we are.",
    ))
    steps.append(_plain(
        "select_top_n",
        "Write up only the few that get a full treatment",
        "Twenty-five equally weighted options is not a decision aid.",
    ))
    steps.append(_plain(
        "list_cut_candidates",
        "List what was considered and ruled out, with the rule that killed it",
        "The cut list is how you check the method rather than take it on "
        "trust — and it is where you look first if something you expected to "
        "see is missing.",
    ))
    return _renumber(steps)


# ── THE GENERATED PLAN ──────────────────────────────────────────────────────


def _offline() -> bool:
    """True when no model should be called. One seam, monkeypatchable — the
    convention `relevance`, `figure_class` and `recommend` already use."""
    return "pytest" in sys.modules


PLAN_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["steps"],
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["part", "primitive", "what", "why"],
                "properties": {
                    "part": {"type": "string", "enum": list(PARTS)},
                    "primitive": {"type": "string"},
                    "what": {"type": "string"},
                    "why": {"type": "string"},
                    # PARAMETERS AS A LIST OF NAME/VALUE STRINGS, not a free
                    # object. A schema with open `additionalProperties` gives
                    # the SDK's tool validation nothing to check, and the
                    # values arrive needing coercion against the registry's
                    # declared types either way — so they are collected in the
                    # shape the coercion actually wants.
                    "params": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "value"],
                            "properties": {
                                "name": {"type": "string"},
                                "value": {"type": "string"},
                            },
                        },
                    },
                    "sources": {"type": "array", "items": {"type": "string"}},
                    "observations": {
                        "type": "array", "items": {"type": "string"},
                    },
                },
            },
        },
    },
}

#: THE PROMPT IS THE FIX, AND THE GATE IS THE GUARANTEE — in that order, and
#: it took a measurement to see why.
#:
#: The first version of this named figures ONLY under "what you must not do",
#: with a discard threat attached. Driven against the real model over a real
#: run's six observations, it produced ELEVEN observation-backed steps and not
#: one figure among them: "very few signals naming an account" where the run
#: had computed 4 of 1,275; "a very short window of import time" where it had
#: 100.0% within 120 seconds; "a large share… from a small number of documents"
#: where it had 18.4% and eleven. A plan that hedges where it holds the number
#: reads as generated, which is the exact impression this stage exists to
#: remove — and it is strictly worse than saying nothing, because the reader
#: cannot tell the model was not guessing.
#:
#: THE GATE CANNOT HAVE CAUSED THAT, and this is the part worth remembering:
#: `verify` runs after generation and the model never sees it. The only channel
#: from the gate to the output is the SENTENCE describing it, and that sentence
#: was a prohibition. Requiring citation in the gate without changing the
#: prompt would have rejected every draw, regenerated once, rejected again and
#: shipped the deterministic plan on every run — the composition silently lost
#: while everything appeared to work.
#:
#: Same observations, same model, citation required instead of merely
#: permitted: fifteen backed steps, fifteen carrying a figure, and the existing
#: traceability gate dropped none of them.
_SYSTEM = """You compose an analysis plan for a product decision, and you write \
it in the reader's language.

WHAT YOU DO: choose which of the listed operations this particular goal needs, \
put them in a sensible order under the given parts, and write two sentences for \
each — one saying what is being done, one saying what it gets the reader and why \
it matters to THIS decision.

CITE THE NUMBER. THIS IS THE MOST IMPORTANT RULE HERE.
Every OBSERVATION below carries measured figures. When you draw a step from an \
observation, its `why` MUST quote at least one of that observation's figures, \
written out exactly as given. Do not paraphrase a measurement into a quantity \
word. "very few", "a small number", "a large share", "a short window" are \
FAILURES when the figure is sitting in front of you: write "4 of 1275", \
"0.3%", "100.0%", "18.4%". A reader who is told "very few" when the run knows \
the answer is 4 stops trusting the document, and they are right to.

KEEP `what` SHORT. It is one action phrase, under about 120 characters, and it \
is what the reader scans down the page. Lists, examples and enumerations of \
sources or record types belong in `why`, never in `what`.

WHAT YOU MUST NOT DO:
- Do not invent an operation. You may only name a primitive from the catalogue.
- Do not state a number that is NOT in an observation below. No scores, no \
rankings, no thresholds, no estimates you worked out yourself, no dates. The \
observations are the source of every figure you write; nothing else is.
- Do not promise a result. A step says what will be done, never what will be \
found.

VOICE: a colleague explaining the approach to the person who has to act on it. \
"See whether the accounts you won last year are growing or shrinking", not \
"compute net revenue retention by cohort". Short. No jargon, no hedging, no \
enthusiasm."""


def _prompt(
    *, goal_text: str, definition_text: str, currency: str,
    report: ReconReport, source_types: Sequence[str],
) -> str:
    lines = [
        f"GOAL: {goal_text}",
        f"WHAT THE READER SAYS THE METRIC MEANS: {definition_text or '(not given)'}",
        f"EVERY SIZE IS STATED IN: {currency}",
        "",
        "SOURCES CONNECTED TO THIS RUN:",
    ]
    for s in report.sources:
        span = f", {s.earliest} to {s.latest}" if s.earliest else ""
        lines.append(f"  - {s.name} ({s.records} records{span})")
    if not report.sources:
        lines.append("  - (none read structurally)")
    if report.missing:
        lines.append("")
        lines.append("NOT CONNECTED AT ALL: " + ", ".join(report.missing))
    lines += ["", "OBSERVATIONS — the ONLY numbers you may use, each with an id:"]
    for o in report.observations:
        figures = ", ".join(f"{k}={v:g}" for k, v in o.figures.items())
        lines.append(f"  [{o.id}] ({o.kind}, {o.severity}) {o.what}")
        lines.append(f"      figures: {figures}")
    if not report.observations:
        lines.append("  (none — so write no numbers at all)")
    lines += [
        "", "PARTS, in order: " + " | ".join(PARTS),
        "", "OPERATIONS YOU MAY NAME:", prim.catalogue(),
        "",
        "Return one step per operation you choose. Put the observation ids "
        "you drew a number from in that step's `observations`. Steps whose "
        "`why` states no number need no observation ids.",
    ]
    return "\n".join(lines)


def _coerce_params(primitive: str, raw: Any) -> dict:
    """Turn the model's name/value strings into the registry's declared types.

    Coerced HERE rather than trusted, because the schema can only say the
    value is a string and `validate_steps` will reject `top_n="3"` for not
    being a whole number — correctly. Anything that will not coerce is left
    as the string it arrived as, so validation reports it rather than this
    function silently dropping it.
    """
    p = prim.REGISTRY.get(primitive)
    if p is None or not isinstance(raw, list):
        return {}
    types = {a.name: a.type for a in p.params}
    out: dict[str, Any] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        value = item.get("value")
        if not name or not isinstance(value, str):
            continue
        kind = types.get(name)
        if kind in ("fields", "sources"):
            out[name] = [v.strip() for v in value.split(",") if v.strip()]
        elif kind == "int":
            try:
                out[name] = int(value.strip())
            except ValueError:
                out[name] = value
        elif kind == "number":
            try:
                out[name] = float(value.strip())
            except ValueError:
                out[name] = value
        else:
            out[name] = value.strip()
    return out


def _parse(payload: Any) -> list[PlanStep]:
    if not isinstance(payload, Mapping):
        return []
    out: list[PlanStep] = []
    for item in payload.get("steps") or []:
        if not isinstance(item, Mapping):
            continue
        primitive = str(item.get("primitive") or "").strip()
        out.append(PlanStep(
            n=0,
            part=str(item.get("part") or ""),
            primitive=primitive,
            params=_coerce_params(primitive, item.get("params")),
            what=str(item.get("what") or "").strip(),
            why=str(item.get("why") or "").strip(),
            sources=tuple(
                str(s) for s in (item.get("sources") or []) if isinstance(s, str)
            ),
            observations=tuple(
                str(s) for s in (item.get("observations") or [])
                if isinstance(s, str)
            ),
        ))
    return out


#: A draw keeping fewer than this share of its own steps is not a plan with a
#: bad step in it, it is a bad draw. Retried once, then the deterministic plan
#: is used — which is a better document than two thirds of a generated one.
MIN_SURVIVING_SHARE = 0.6

#: Observation-driven steps the DETERMINISTIC plan writes per observation kind.
#:
#: ONE. `recon` is capped at eight observations per kind and every one of them
#: is real, but three consecutive steps reading "check whether the loudest
#: accounts are the valuable ones" against three different activity sources is
#: the same instruction three times — a reader skims past all three and the
#: plan gets longer without getting more decidable. The strongest is written
#: as a step; the rest travel on the plan as observations, where a reader who
#: wants the detail can see them and the executor can still act on them.
#: `report.of_kind` returns severity-ordered with a stable tie-break, so which
#: one is "strongest" is the same on every read of the same evidence.
MAX_DETERMINISTIC_PER_KIND = 1


def build_steps(
    *,
    enterprise_id: str,
    goal_text: str,
    definition_text: str = "",
    currency: str = "accounts",
    report: Optional[ReconReport] = None,
    source_types: Sequence[str] = (),
    run_meta: Optional[Mapping[str, Any]] = None,
) -> tuple[PlanStep, ...]:
    """The plan for this run: drawn once, validated, and never re-sampled.

    TOTAL. A model failure, a malformed draw, a draw that fails validation
    twice, or no model at all all resolve the same way — the deterministic
    plan. There is no path where this raises and no path where it returns
    something the engine cannot run.
    """
    stored = load_steps(run_meta or {})
    if stored is not None:
        logger.info("crucible_plan_steps_reused steps=%s", len(stored))
        return stored

    report = report or ReconReport()
    fallback = tuple(minimal_plan(
        goal_text=goal_text, currency=currency, report=report,
        source_types=source_types,
    ))
    if _offline():
        return fallback

    # BOTH VOCABULARIES. A step addresses a source two ways — the storage key
    # on its parameters, the reader's label on `sources` — so the validator
    # has to know both or it would reject every correctly-labelled step. It
    # keeps its teeth: a step naming a source this run does not have under
    # EITHER name still fails, which is the check that matters.
    available = (
        set(source_types)
        | {s.name for s in report.sources}
        | {s.label for s in report.sources}
        | {o.source for o in report.observations}
        | {o.source_label for o in report.observations}
    )
    prompt = _prompt(
        goal_text=goal_text, definition_text=definition_text,
        currency=currency, report=report, source_types=source_types,
    )
    for attempt in range(MAX_REGENERATIONS + 1):
        try:
            from app.graph.gateway import llm_call

            result = llm_call(
                enterprise_id=enterprise_id,
                agent="crucible",
                purpose="compose_run_plan",
                prompt_version="crucible-planner-v2",
                system=_SYSTEM,
                input=prompt,
                json_schema=PLAN_SCHEMA,
                max_tokens=8000,
            )
            drawn = _parse(getattr(result, "output", None))
        except Exception:  # noqa: BLE001 — a plan is never worth failing a run
            logger.exception("crucible: plan composition failed; using the "
                             "deterministic plan")
            return fallback

        kept, dropped = verify(drawn, report.observations,
                               available_sources=sorted(available),
                               inventory=report.inventory_figures())
        if dropped:
            logger.warning("crucible_plan_steps_dropped attempt=%s dropped=%s",
                           attempt, "; ".join(dropped))
        if kept and len(kept) >= MIN_SURVIVING_SHARE * max(1, len(drawn)):
            logger.info("crucible_plan_steps_drawn steps=%s dropped=%s "
                        "attempt=%s", len(kept), len(dropped), attempt)
            return tuple(kept)

    logger.warning("crucible: plan composition did not validate; using the "
                   "deterministic plan")
    return fallback

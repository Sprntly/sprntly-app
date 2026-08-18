"""The ten invariants, as executable checks.

SPEC §1. These are correctness properties, not preferences: where an
implementation choice conflicts with one, the invariant wins. They are built
FIRST, before any pipeline stage, because they are cross-cutting — stages built
against each other before the contract exists violate them at the seams, and
they do it invisibly, since each stage's own tests pass. You find out in week
five, in the form of a scoring function reading a field it was never allowed to
see.

Two kinds of thing live here and they are used differently:

  * **Runtime guards** (`assert_goal_locked`, `require_authority`,
    `validate_source_authority`, `derive_effort`) — called by pipeline code, on
    every run, in production.
  * **Property harnesses** (`assert_impact_ignores_corroboration`,
    `assert_scores_frozen_across`) — called by tests against a real
    implementation. They take the function under test as an argument, so the
    harness ships now and PR7's scorer plugs into it unchanged.

Every harness is itself tested against a deliberately non-compliant fake, so we
know it can actually fail. A property test that has never seen red is a
decoration; see `tests/test_crucible_invariants.py`.
"""
from __future__ import annotations

import dataclasses
import math
import re
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from app.crucible.lint import assert_lint_clean  # noqa: F401  (I5 lives there)
from app.crucible.types import (
    CLAIM_TYPES,
    CORROBORATION_FIELDS,
    DECAY_HALFLIFE_DAYS,
    MIN_EFFORT_COMPARABLES,
    AssumedParam,
    Claim,
    ClaimType,
    Confidence,
    EffortEstimate,
    Finding,
    GoalDefinition,
    GoalNotLockedError,
    Impact,
    SelectionBias,
)

#: The contract, in one place, for anything that needs to name an invariant in
#: an error message or a UI. Keep the wording in step with SPEC §1.
INVARIANTS: Mapping[str, str] = MappingProxyType({
    "I1": "Impact never reads corroboration.",
    "I2": "The LLM proposes, deterministic code decides.",
    "I3": "Unmeasured is not zero.",
    "I4": "A source never votes outside its authority.",
    "I5": "Causal verbs require causal evidence.",
    "I6": "Empty sources are closed silently.",
    "I7": "Effort estimates show their derivation or do not exist.",
    "I8": "Assumed parameters are visibly distinguished from measured ones.",
    "I9": "The goal definition is adopted or elicited, never inferred.",
    "I10": "Prioritisation never mutates impact or confidence.",
})


class InvariantViolation(AssertionError):
    """A named invariant was broken. Always carries which one."""

    def __init__(self, invariant: str, detail: str) -> None:
        self.invariant = invariant
        super().__init__(f"{invariant} violated — {INVARIANTS[invariant]} {detail}")


# ── I1. Impact never reads corroboration ─────────────────────────────────────

def assert_no_corroboration_fields(cls: type) -> None:
    """Structural half of I1: the impact-side type cannot carry corroboration.

    Cheap, static, and it catches the realistic regression — someone adding
    `source_count` to `ImpactInputs` for a "small bonus when four sources
    agree", which is exactly the change spec F2 predicts and which quietly
    re-buries every quiet finding.
    """
    names = {f.name for f in dataclasses.fields(cls)}
    leaked = names & CORROBORATION_FIELDS
    if leaked:
        raise InvariantViolation(
            "I1",
            f"{cls.__name__} carries corroboration field(s) {sorted(leaked)}. "
            f"Corroboration belongs to ConfidenceInputs only.",
        )


def _mutation_values(value):
    """Two wildly different substitutes for a field, chosen by runtime type.

    Wildly different on purpose: a scorer that scales by a corroboration term
    must not be able to land on the same number by coincidence.
    """
    if isinstance(value, bool):                 # before int — bool IS an int
        return [not value]
    if isinstance(value, tuple):
        probe = ("__probe_a__", "__probe_b__", "__probe_c__")
        return [(), value * 3 if value else probe]
    if isinstance(value, int):
        return [0, value + 97]
    if isinstance(value, float):
        return [0.0, value + 0.97]
    if value is None:
        return [0.0, 1.0]
    return []


#: Corroboration-bearing fields on `Finding` ITSELF, which the confidence-side
#: sweep below cannot reach. `adjudication` is the sharp one: it literally holds
#: the corroboration verdict, and `single_authoritative` is the quiet-finding
#: guard, so a scorer keyed on it re-buries exactly what I1 protects.
_FINDING_MUTATIONS: tuple[tuple[str, object], ...] = (
    ("claim_ids", ()),
    ("claim_ids", tuple(f"c-{i}" for i in range(37))),
    ("cell_refs", ()),
    ("cell_refs", tuple(f"cell-{i}" for i in range(29))),
    ("adjudication", "conflict"),
    ("adjudication", "single_authoritative"),
    ("adjudication", "corroborated"),
    ("adjudication", "no_authoritative_source"),
)


def assert_impact_ignores_corroboration(
    score_impact: Callable[[Finding], Impact],
    finding: Finding,
) -> None:
    """THE FLAGSHIP CHECK. Impact must be byte-identical under every mutation
    of how many sources agree.

    Without this, a future refactor helpfully adds a corroboration bonus to
    impact and silently reintroduces the one failure this engine exists to
    prevent. The comparison is on `repr`, not `==`, because two `Impact` values
    can compare equal while differing in a field a dataclass `__eq__` ignores.

    Three things this harness learned the hard way, each of which made an
    earlier version of it pass a scorer that read corroboration:

    **The mutation set is DERIVED from the dataclass, never hand-written.** A
    hand-written list covered 4 of `ConfidenceInputs`' 12 fields, so a scorer
    keyed on `claim_types`, `coverage`, or on `len(strengths)` — an exact proxy
    for the `claim_count` the list did mutate — sailed through. Every field is
    swept, so adding a field to `ConfidenceInputs` extends this automatically.

    **`Finding` itself carries corroboration.** The confidence-side sweep cannot
    see `adjudication`, `claim_ids` or `cell_refs`, and a scorer is handed the
    whole `Finding`. See `_FINDING_MUTATIONS`.

    **The probe must be SIZEABLE.** If the probe's impact is `None`, every
    mutation returns `None`, the reprs match, and the harness reports compliance
    having tested nothing. Corpus-only runs — the common case on real tenants —
    produce exactly that finding, so this refuses a degenerate probe loudly
    rather than passing vacuously.

    Each probe also gets a fresh `id`, which defeats a `score_impact` memoised
    on finding identity: `dataclasses.replace` preserves `id`, so a cache keyed
    on it would return the baseline for every mutation and hide a violation.
    The function under test must be pure and deterministic; if it is not, this
    check cannot mean anything.
    """
    baseline = score_impact(finding)
    if baseline.value is None:
        raise ValueError(
            "assert_impact_ignores_corroboration needs a probe finding whose "
            "impact is sizeable; this one scores None, so every mutation would "
            "also score None and the check would pass without testing anything. "
            "Supply a finding with affected_population, movable_gap and "
            "value_per_unit all measured."
        )
    baseline_repr = repr(baseline)

    def check(mutated: Finding, what: str) -> None:
        got = score_impact(mutated)
        if repr(got) != baseline_repr:
            raise InvariantViolation(
                "I1",
                f"score_impact changed when only corroboration moved ({what}). "
                f"Baseline {baseline_repr}, got {repr(got)}.",
            )

    counter = 0
    conf = finding.confidence_inputs
    for f in dataclasses.fields(conf):
        for substitute in _mutation_values(getattr(conf, f.name)):
            counter += 1
            mutated_conf = dataclasses.replace(conf, **{f.name: substitute})
            check(
                dataclasses.replace(
                    finding, confidence_inputs=mutated_conf,
                    id=f"{finding.id}-probe{counter}",
                ),
                f"confidence_inputs.{f.name}={substitute!r}",
            )

    for name, substitute in _FINDING_MUTATIONS:
        counter += 1
        check(
            dataclasses.replace(
                finding, **{name: substitute}, id=f"{finding.id}-probe{counter}"
            ),
            f"{name}={substitute!r}",
        )


# ── I2. The LLM proposes, deterministic code decides ─────────────────────────

#: Keys that represent a DECISION. No LLM call site may return one: not a score,
#: not a rank, not a confidence value, not a next action. An LLM returns
#: candidates; deterministic code orders them.
DECISION_FIELD_NAMES: frozenset[str] = frozenset({
    "score", "rank", "ranking", "priority", "priority_score", "confidence",
    "confidence_score", "band", "impact_score", "weight", "weighting",
    "next_action", "decision", "verdict", "should", "recommendation_rank",
    "severity_score", "importance", "order",
})


def assert_llm_schema_returns_no_decision(schema: Mapping[str, Any], site: str) -> None:
    """I2, checked against a call site's declared JSON schema.

    Walks the whole schema, not just top-level properties: a score nested inside
    an array item is the same violation and is the likelier way one appears.
    """
    offenders: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            props = node.get("properties")
            if isinstance(props, Mapping):
                for key, sub in props.items():
                    here = f"{path}.{key}" if path else key
                    if key.lower() in DECISION_FIELD_NAMES:
                        offenders.append(here)
                    walk(sub, here)
            items = node.get("items")
            if items is not None:
                walk(items, f"{path}[]")
            for combiner in ("anyOf", "oneOf", "allOf"):
                for i, sub in enumerate(node.get(combiner) or []):
                    walk(sub, f"{path}.{combiner}[{i}]")

    walk(schema, "")
    if offenders:
        raise InvariantViolation(
            "I2",
            f"LLM call site {site!r} declares decision field(s) {sorted(offenders)}. "
            f"Return structured candidates; let scoring functions decide.",
        )


# ── I3. Unmeasured is not zero ───────────────────────────────────────────────

def assert_aggregate_propagates_unmeasured(
    aggregate: Callable[[Sequence[Optional[float]]], Optional[float]],
) -> None:
    """I3 for ANY aggregation: unmeasured in, `None` out; measured in, number out.

    Deliberately does not pin the returned value, so it is honest about `nmean`
    and any future `nmax` as well as `nsum`. A single harness that asserted the
    SUM total against every aggregate accused `nmean` of violating I3 for
    correctly returning 6.0 — a false accusation against a correct function is
    how a guard gets deleted.
    """
    if aggregate([None, None, None]) is not None:
        raise InvariantViolation(
            "I3", "an all-unmeasured aggregate returned a number instead of None."
        )
    if aggregate([None, 5.0, None, 7.0]) is None:
        raise InvariantViolation(
            "I3", "an aggregate with measured values present returned None."
        )


def assert_sum_skips_unmeasured(
    aggregate: Callable[[Sequence[Optional[float]]], Optional[float]],
) -> None:
    """I3 for a SUM specifically — the property plus the arithmetic.

    Two cases, and the SECOND is the one implementations get wrong. Everybody
    handles all-null. Mixed null is where `sum(v or 0 for v in values)` hides,
    producing a number that is arithmetically fine and quietly asserts that the
    unmeasured cells were empty.
    """
    assert_aggregate_propagates_unmeasured(aggregate)
    mixed = aggregate([None, 5.0, None, 7.0])
    if mixed is None:
        raise InvariantViolation(
            "I3", "an aggregate with measured values present returned None."
        )
    if not math.isclose(mixed, 12.0):
        raise InvariantViolation(
            "I3",
            f"mixed-null aggregate returned {mixed!r}, expected 12.0 over the two "
            f"measured values — unmeasured cells must be skipped, not zeroed.",
        )


# ── I4. A source never votes outside its authority ───────────────────────────

@dataclasses.dataclass(frozen=True)
class SourceAuthority:
    """What one connector may vote on. Declarative — SPEC §4.1's manifest slice
    that the authority rules actually read."""
    source_id: str
    authoritative_for: frozenset[str]
    never_authoritative_for: frozenset[str] = frozenset()
    selection_bias: SelectionBias = "none"
    #: How prose refers to this source. The connector id is a slug
    #: (`google_drive`, `ms_teams`) and narrative never uses it, so I6's leak
    #: check matched nothing for any source whose id is not also its brand —
    #: "We found nothing in Google Drive" sailed past a check watching for
    #: `google_drive`. Every name a renderer might write goes here.
    display_names: tuple[str, ...] = ()


def validate_source_authority(authority: SourceAuthority) -> None:
    """Validated at connector onboarding, once, before anything is persisted.

    The self-selection rule is the load-bearing one and it is one line: reviews,
    tickets, social listening and sales calls all describe populations that
    chose to appear in them. Letting any of them vote on `magnitude` is how a
    system ends up telling a company its loudest problem is its biggest one.
    """
    unknown = (authority.authoritative_for | authority.never_authoritative_for) - CLAIM_TYPES
    if unknown:
        raise InvariantViolation(
            "I4", f"{authority.source_id!r} names unknown claim type(s) {sorted(unknown)}."
        )
    both = authority.authoritative_for & authority.never_authoritative_for
    if both:
        raise InvariantViolation(
            "I4",
            f"{authority.source_id!r} is declared both authoritative and never "
            f"authoritative for {sorted(both)}.",
        )
    if authority.selection_bias == "self_selected" and "magnitude" in authority.authoritative_for:
        raise InvariantViolation(
            "I4",
            f"{authority.source_id!r} has selection_bias='self_selected' and claims "
            f"magnitude authority. A self-selected population cannot size anything.",
        )


def is_authoritative(authority: SourceAuthority, claim_type: ClaimType) -> bool:
    return claim_type in authority.authoritative_for


def require_authority(
    claims: Iterable[Claim],
    registry: Mapping[str, SourceAuthority],
) -> tuple[Claim, ...]:
    """Stamp `authoritative` from the registry — never from the claim itself.

    NON-AUTHORITATIVE CLAIMS ARE RETAINED, NOT DROPPED (SPEC Stage 4). They
    contribute zero confidence and they supply the mechanism detail that makes a
    finding actionable; discarding them is how output becomes correct and
    useless at the same time.
    """
    out: list[Claim] = []
    for claim in claims:
        authority = registry.get(claim.source_id)
        allowed = bool(authority) and is_authoritative(authority, claim.type)
        out.append(dataclasses.replace(claim, authoritative=allowed))
    return tuple(out)


# ── I6. Empty sources are closed silently ────────────────────────────────────

def assert_only_read_sources_mentioned(
    text: str,
    read_source_ids: Iterable[str],
    known_sources: Iterable[str | SourceAuthority],
) -> None:
    """A store with no data is not read, not mentioned, and imposes no penalty.

    "We found nothing in Zendesk" is worse than saying nothing: it implies a
    search that never happened, and it invites a reader to discount a finding
    for a gap that does not exist.

    `known_sources` accepts ids or `SourceAuthority` objects. Prefer the latter:
    matching on the id alone misses every source whose slug is not its brand,
    which is most of them, and prose never writes the slug.
    """
    read = {s.lower() for s in read_source_ids}
    leaked: list[str] = []
    for source in known_sources:
        if isinstance(source, SourceAuthority):
            source_id, names = source.source_id, source.display_names
        else:
            source_id, names = source, ()
        if source_id.lower() in read:
            continue
        for candidate in (source_id, *names):
            # `\s+` between words so a line-wrapped "Google\nDrive" still matches.
            pattern = r"\s+".join(re.escape(w) for w in candidate.split())
            if re.search(rf"\b{pattern}\b", text, re.IGNORECASE):
                leaked.append(source_id)
                break
    if leaked:
        raise InvariantViolation(
            "I6", f"output mentions unread source(s) {sorted(set(leaked))}."
        )


# ── I7. Effort shows its derivation or does not exist ────────────────────────

def derive_effort(comparables: Sequence[float], surface: str) -> EffortEstimate:
    """Median of comparable prior projects, or `None` with a reason.

    Never guesses. An item with no history is `unrankable` and says so, because
    inventing a number to complete a RICE score launders a guess into a
    decision — precisely what I7 exists to stop.
    """
    if len(comparables) < MIN_EFFORT_COMPARABLES:
        return EffortEstimate(
            weeks=None,
            derivation=(
                f"insufficient comparable history on {surface}: "
                f"{len(comparables)} prior project(s), need {MIN_EFFORT_COMPARABLES}"
            ),
        )
    ordered = sorted(comparables)
    mid = len(ordered) // 2
    median = (
        ordered[mid] if len(ordered) % 2
        else (ordered[mid - 1] + ordered[mid]) / 2
    )
    return EffortEstimate(
        weeks=median,
        derivation=(
            f"median of {len(ordered)} prior projects on {surface}: "
            + ", ".join(f"{c:g}" for c in ordered)
        ),
        comparables=tuple(ordered),
    )


# ── I8. Assumed parameters are visibly distinguished ─────────────────────────

def assert_assumed_params_disclosed(
    impact: Impact,
    disclosed: Iterable[AssumedParam],
) -> None:
    """Every judgement-derived number in an impact appears in the assumptions
    section. A flagged assumption the reader never sees is an unflagged one."""
    missing = sorted(
        {p.name for p in impact.assumed_params} - {p.name for p in disclosed}
    )
    if missing:
        raise InvariantViolation(
            "I8", f"assumed parameter(s) {missing} never reached the assumptions section."
        )


# ── I9. The goal definition is adopted or elicited, never inferred ───────────

def assert_goal_locked(definition: GoalDefinition) -> None:
    """Hard error entering Stage 1 (SPEC Stage 0). Not a warning.

    This invariant sits above the others: they protect the quality of the
    answer, this one protects the identity of the question. A wrong definition
    does not produce a slightly wrong answer — it produces a fully coherent,
    well-argued answer to a different question, and nothing downstream can
    detect that.
    """
    if definition.status != "locked":
        raise GoalNotLockedError(
            f"I9: cannot enter Stage 1 with goal definition {definition.id!r} at "
            f"status={definition.status!r}. A definition is adopted from the "
            f"company's own systems or elicited from the user, then confirmed."
        )
    if definition.confirmed_by_user_at is None:
        raise GoalNotLockedError(
            f"I9: goal definition {definition.id!r} is locked without user confirmation."
        )


# ── I10. Prioritisation never mutates impact or confidence ───────────────────

def assert_scores_frozen_across(
    step: Callable[[Sequence[tuple[Impact, Confidence]]], Any],
    scored: Sequence[tuple[Impact, Confidence]],
) -> None:
    """Run a prioritisation step and prove it changed nothing upstream.

    I1's logic one layer up: the reason to do something first must not change
    how big we said it was. `Impact` and `Confidence` are frozen, so an in-place
    write raises on its own — this catches the other route, a step that rebuilds
    and returns altered copies while claiming to have only ordered them.

    THAT SECOND ROUTE IS THE WHOLE POINT, and an earlier version of this
    function missed it completely: it called `step(scored)`, threw the result
    away, and diffed the input against itself. A prioritiser written in the
    ordinary functional style —

        return sorted((replace(i, value=1.0), c) for i, c in items)

    — rewrote every impact and passed. Worse, when `scored` is a tuple (this
    parameter is a `Sequence`, and everything else in this module is tuples)
    in-place assignment is impossible, so the check was fully vacuous. Both the
    input and the RETURNED set are compared now, the latter as a multiset so
    that re-ordering — the one thing prioritisation is allowed to do — passes.
    """
    before = [(repr(i), repr(c)) for i, c in scored]
    returned = step(scored)
    after = [(repr(i), repr(c)) for i, c in scored]

    if before != after:
        for idx, (b, a) in enumerate(zip(before, after)):
            if b != a:
                raise InvariantViolation(
                    "I10",
                    f"prioritisation altered scored item {idx} in place: {b} -> {a}.",
                )
        raise InvariantViolation("I10", "prioritisation altered the scored set.")

    if returned is None:
        return
    try:
        returned_pairs = [(repr(i), repr(c)) for i, c in returned]
    except (TypeError, ValueError):
        # A step returning something that is not a sequence of pairs (a plain
        # ordering of ids, say) has nothing to compare, and that is fine.
        return
    if sorted(returned_pairs) != sorted(before):
        added = sorted(set(returned_pairs) - set(before))
        raise InvariantViolation(
            "I10",
            f"prioritisation returned scores that differ from its input by more "
            f"than order — {len(added)} altered item(s), first: {added[:1]}.",
        )


# ── Shared helper: per-claim-type decay (used by confidence, never by impact) ─

def decay_factor(claim_type: ClaimType, observed_at: datetime, now: Optional[datetime] = None) -> float:
    """Half-life decay keyed on CLAIM TYPE, not source type.

    A mechanism stays true far longer than a competitor fact, and where a claim
    came from cannot tell you which it is. Execution facts never decay: they are
    re-readable, so re-read rather than discount.
    """
    now = now or datetime.now(timezone.utc)
    try:
        half_life = DECAY_HALFLIFE_DAYS[claim_type]
    except KeyError:
        # `Claim.__post_init__` refuses an unknown type, so reaching here means
        # a string that skipped it — a typo, or a stage building the dict
        # directly. Falling back to the `magnitude` rate returned a plausible
        # 0.4958, which is worse than a stack trace: it is wrong and it looks
        # right.
        raise ValueError(
            f"Unknown claim type {claim_type!r}; no decay half-life is defined "
            f"for it. Expected one of {sorted(DECAY_HALFLIFE_DAYS)}."
        ) from None
    if half_life == math.inf:
        return 1.0
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - observed_at).total_seconds() / 86400.0)
    return math.pow(0.5, age_days / half_life)

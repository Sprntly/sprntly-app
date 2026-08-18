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
INVARIANTS: Mapping[str, str] = {
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
}


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


#: Mutations applied to a finding's confidence side. If impact moves under any
#: of them, impact is reading corroboration. Values are chosen to be wildly
#: different from any plausible original so a scorer that scales by them cannot
#: coincidentally land on the same number.
_CORROBORATION_MUTATIONS: tuple[dict[str, Any], ...] = (
    {"surfaced_by": ()},
    {"surfaced_by": ("structural", "corpus", "dispatch", "extra", "more")},
    {"independent_authoritative_source_types": 0},
    {"independent_authoritative_source_types": 97},
    {"authoritative_count": 0},
    {"authoritative_count": 41},
    {"claim_count": 1},
    {"claim_count": 953},
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
    """
    baseline = score_impact(finding)
    baseline_repr = repr(baseline)

    for mutation in _CORROBORATION_MUTATIONS:
        mutated_conf = dataclasses.replace(finding.confidence_inputs, **mutation)
        mutated = dataclasses.replace(finding, confidence_inputs=mutated_conf)
        got = score_impact(mutated)
        if repr(got) != baseline_repr:
            changed = ", ".join(f"{k}={v!r}" for k, v in mutation.items())
            raise InvariantViolation(
                "I1",
                f"score_impact changed when only corroboration moved ({changed}). "
                f"Baseline {baseline_repr}, got {repr(got)}.",
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

def assert_never_zero_for_missing(
    aggregate: Callable[[Sequence[Optional[float]]], Optional[float]],
) -> None:
    """I3, checked against an aggregation function.

    Two cases, and the SECOND is the one implementations get wrong. Everybody
    handles all-null. Mixed null is where `sum(v or 0 for v in values)` hides,
    producing a number that is arithmetically fine and quietly asserts that the
    unmeasured cells were empty.
    """
    if aggregate([None, None, None]) is not None:
        raise InvariantViolation(
            "I3", "an all-unmeasured aggregate returned a number instead of None."
        )
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
    known_source_ids: Iterable[str],
) -> None:
    """A store with no data is not read, not mentioned, and imposes no penalty.

    "We found nothing in Zendesk" is worse than saying nothing: it implies a
    search that never happened, and it invites a reader to discount a finding
    for a gap that does not exist.
    """
    read = {s.lower() for s in read_source_ids}
    leaked = sorted(
        sid for sid in known_source_ids
        if sid.lower() not in read
        and re.search(rf"\b{re.escape(sid)}\b", text, re.IGNORECASE)
    )
    if leaked:
        raise InvariantViolation(
            "I6", f"output mentions unread source(s) {leaked}."
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
    """
    before = [(repr(i), repr(c)) for i, c in scored]
    step(scored)
    after = [(repr(i), repr(c)) for i, c in scored]
    if before != after:
        for idx, (b, a) in enumerate(zip(before, after)):
            if b != a:
                raise InvariantViolation(
                    "I10",
                    f"prioritisation altered scored item {idx}: {b} -> {a}.",
                )
        raise InvariantViolation("I10", "prioritisation altered the scored set.")


# ── Shared helper: per-claim-type decay (used by confidence, never by impact) ─

def decay_factor(claim_type: ClaimType, observed_at: datetime, now: Optional[datetime] = None) -> float:
    """Half-life decay keyed on CLAIM TYPE, not source type.

    A mechanism stays true far longer than a competitor fact, and where a claim
    came from cannot tell you which it is. Execution facts never decay: they are
    re-readable, so re-read rather than discount.
    """
    now = now or datetime.now(timezone.utc)
    half_life = DECAY_HALFLIFE_DAYS.get(claim_type, 180.0)
    if half_life == math.inf:
        return 1.0
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - observed_at).total_seconds() / 86400.0)
    return math.pow(0.5, age_days / half_life)

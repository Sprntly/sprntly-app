"""Crucible data model — the contract every stage is built against.

Ported from `backend/docs/crucible/CRUCIBLE-SPEC.md` §3. Do not add fields here
without updating that spec.

Three properties of this module do real work and are worth reading for before
using anything in it:

**Everything is frozen.** `Impact` and `Confidence` are immutable because I10
says prioritisation reads frozen scores and never writes back — "the reason to
do something first must not change how big we said it was". A frozen dataclass
turns that from a rule people remember into an `AttributeError`. Mapping fields
are wrapped in `MappingProxyType` on construction for the same reason; a frozen
dataclass otherwise leaves a dict field wide open.

**`None` is not zero.** Every "not measured" value is `None` and stays `None`
(I3). Coercing these is the most common way an analytics system misleads, and
it is silent: a segment worth millions scores as worth nothing and never appears
in the output, with no error to notice. `nsum`/`nmean` below are the only
sanctioned aggregations.

**Impact and confidence read different inputs, by construction.** `ImpactInputs`
and `ConfidenceInputs` exist as separate types so that I1 — impact never reads
corroboration — is enforced by what a function can *see*, not by a comment
asking it not to look. `CORROBORATION_FIELDS` names the fields that may never
appear on the impact side, and `app.crucible.invariants` asserts it.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal, Mapping, Optional, Sequence


class _FrozenMapping:
    """Pickle, deepcopy and hashing for a frozen dataclass holding a proxy.

    `MappingProxyType` is what makes these types genuinely immutable — a frozen
    dataclass with a plain `dict` field is not frozen where it matters. It costs
    three capabilities people reasonably assume a frozen dataclass has, and each
    one fails at a call site far from the cause with a `TypeError` naming
    `dict`:

      * `hash()` — the generated `__hash__` hashes the field tuple, and a proxy
        is unhashable. Each class below defines its own `__hash__` over a sorted
        projection instead; a `__hash__` defined in the class BODY survives
        `@dataclass`, one inherited from a mixin does not.
      * `deepcopy` / `pickle` — both route through `__reduce_ex__`, and a proxy
        cannot be pickled. `__getstate__` unwraps to plain dicts on the way out
        and `__setstate__` re-freezes on the way back in.

    Without this, PR6 deduping candidates with `set(findings)` or PR9 taking a
    `deepcopy` of scored state before a prioritisation pass would each blow up
    at runtime, and the message would point nowhere near this file.
    """

    def __getstate__(self) -> dict:
        state = {}
        for f in dataclasses.fields(self):          # type: ignore[arg-type]
            value = getattr(self, f.name)
            state[f.name] = dict(value) if isinstance(value, MappingProxyType) else value
        return state

    def __setstate__(self, state: dict) -> None:
        for key, value in state.items():
            object.__setattr__(self, key, value)
        # Re-apply the freeze the constructor would have applied.
        post_init = getattr(self, "__post_init__", None)
        if post_init is not None:
            post_init()


def _hashable(mapping: Mapping) -> tuple:
    """A stable, hashable projection of a mapping field."""
    return tuple(sorted(mapping.items()))

# ── Vocabularies ─────────────────────────────────────────────────────────────

GoalCurrency = Literal[
    "arr_dollars", "retained_users", "retention_points", "sessions",
    "activated_accounts", "new_users", "contacts", "cost_dollars", "cycle_days",
    # NOT in the spec's list. Added for corpus-only tenants (see
    # docs/GOAL_ANALYSIS.md §2): where no revenue or telemetry exists, named
    # accounts are the only currency available, and the alternative to sizing in
    # accounts is not sizing at all. Any impact denominated in accounts carries
    # an AssumedParam for the missing value-per-account (I8) — it is a reach
    # measure standing in for a value measure, and must never be rendered as if
    # it were the latter.
    "accounts",
]
GOAL_CURRENCIES: frozenset[str] = frozenset(GoalCurrency.__args__)

ClaimType = Literal[
    "magnitude", "mechanism", "preference", "constraint", "direction",
    # Execution evidence — what this organisation DID, as opposed to what
    # anyone believes about users. See SPEC §4.5.
    "existence",   # exists in the product today
    "attempt",     # was tried / committed / abandoned / reverted
]
CLAIM_TYPES: frozenset[str] = frozenset(ClaimType.__args__)

EvidenceStrength = Literal[
    "causally_tested", "measured", "correlated", "inferred", "reported",
]
EVIDENCE_STRENGTHS: frozenset[str] = frozenset(EvidenceStrength.__args__)

STRENGTH_SCORE: Mapping[str, float] = MappingProxyType({
    "causally_tested": 1.00, "measured": 0.90, "correlated": 0.60,
    "inferred": 0.40, "reported": 0.25,
})

#: Confidence decay half-life in days, BY CLAIM TYPE — not by source type.
#: Competitive facts rot fast, structural facts do not, and where a claim came
#: from cannot tell you which it is. Execution facts are re-readable at any time
#: (the repo and the tracker either still say it or they do not), so they are
#: never discounted for age; re-read instead.
DECAY_HALFLIFE_DAYS: Mapping[str, float] = MappingProxyType({
    "magnitude": 180.0, "mechanism": 540.0, "preference": 270.0,
    "constraint": 120.0, "direction": 90.0,
    "existence": math.inf, "attempt": math.inf,
})

SelectionBias = Literal["none", "self_selected", "sampled", "census"]

ConfidenceBand = Literal["high", "medium", "low"]

#: Field names that carry "how many sources agree". Impact may never read one.
#: Enforced by `invariants.assert_no_corroboration_fields`, which is why this is
#: a data structure rather than a paragraph.
CORROBORATION_FIELDS: frozenset[str] = frozenset({
    "surfaced_by", "source_types", "breadth", "connected_breadth",
    "corroboration", "corroboration_bonus", "agreeing_sources", "source_count",
    "independent_authoritative_source_types", "convergence", "sweeps",
    "signal_count", "claim_count", "supporting_sources",
})


# ── Null-safe aggregation (I3) ───────────────────────────────────────────────

def nsum(values: Sequence[Optional[float]]) -> Optional[float]:
    """Sum, where "nothing was measured" is `None` and never `0`.

    Returns `None` when NO value is measured; otherwise sums the measured ones.
    The mixed case is the one that matters and the one usually got wrong: the
    all-`None` case is obvious enough that most implementations handle it, and
    then `[None, 5.0]` quietly becomes `5.0` as though the first cell had been
    measured and found empty. It had not been measured at all, and the caller
    needs `measured_count` to know that. Use `nsum_with_coverage` where the
    distinction affects the output.
    """
    measured = [v for v in values if v is not None]
    return sum(measured) if measured else None


def nsum_with_coverage(
    values: Sequence[Optional[float]],
) -> tuple[Optional[float], int, int]:
    """`nsum`, plus how much of the input was actually measured.

    Returns `(total, measured_count, total_count)`. A total computed over 3 of
    47 cells is a different claim from one computed over 47 of 47, and nothing
    downstream can tell them apart from the number alone.
    """
    measured = [v for v in values if v is not None]
    total = sum(measured) if measured else None
    return total, len(measured), len(values)


def nmean(values: Sequence[Optional[float]]) -> Optional[float]:
    """Mean over measured values only; `None` when nothing is measured."""
    measured = [v for v in values if v is not None]
    return (sum(measured) / len(measured)) if measured else None


#: What an unmeasured value renders as. Never "0", never "—", never blank: each
#: of those reads as a measurement to somebody.
NOT_MEASURED = "not measured"


def render_measure(value: Optional[float], suffix: str = "") -> str:
    """Render a possibly-unmeasured number for output (I3).

    I3 runs in ONE direction — unmeasured must never print as a number — and the
    first version of this function broke it in the other: `f"{v:,.0f}"` renders a
    measured `0.16` as `"0"`. `Impact.movable_gap` is exactly such a fraction, so
    the sanctioned formatter was printing a real opportunity as no opportunity,
    with no error. Whole numbers keep thousands separators and no decimal point;
    anything below 1 keeps three significant figures.
    """
    if value is None:
        return NOT_MEASURED
    if value == int(value):
        return f"{int(value):,d}{suffix}"
    if abs(value) < 1:
        return f"{value:.3g}{suffix}"
    return f"{value:,.2f}{suffix}"


# ── Goal ─────────────────────────────────────────────────────────────────────

GoalStatus = Literal["unresolved", "candidate", "locked"]

#: I9. There is no `inferred`, and that absence is the invariant: a definition is
#: adopted from something the company already wrote, or elicited from a human.
GoalOrigin = Literal["adopted", "elicited"]


@dataclass(frozen=True)
class PopulationFilter(_FrozenMapping):
    segments: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    estimated_size: Optional[int] = None      # None = not measured (I3)

    def __post_init__(self) -> None:
        object.__setattr__(self, "segments", MappingProxyType(dict(self.segments)))

    def __hash__(self) -> int:
        return hash((_hashable(self.segments), self.estimated_size))


@dataclass(frozen=True)
class DefinitionConflict:
    """Two systems defining the same metric differently.

    Retained rather than resolved. I9 forbids breaking the tie silently: a
    conflict is surfaced to the user, because picking the more recently updated
    one produces a fully coherent answer to the wrong question and nothing
    downstream can detect that.
    """
    metric_name: str
    source_a: str
    definition_a: str
    source_b: str
    definition_b: str


class GoalNotLockedError(RuntimeError):
    """Raised when analysis is attempted against an unconfirmed definition."""


@dataclass(frozen=True)
class GoalDefinition:
    """I9: adopted or elicited, never inferred; locked only by a human.

    `__post_init__` refuses to construct a `locked` definition that no user
    confirmed, so the invariant cannot be violated by a code path that forgets
    to check — including an LLM-populated one, which is the case the spec calls
    out explicitly ("No LLM output may set `status` to `locked`").
    """
    id: str
    raw_goal_text: str
    metric_name: str
    definition_text: str
    currency: GoalCurrency
    direction: Literal["increase", "decrease"]
    status: GoalStatus = "unresolved"
    origin: Optional[GoalOrigin] = None
    source_ref: Optional[str] = None
    definition_source_ref: Optional[str] = None
    target_value: Optional[float] = None
    horizon_weeks: Optional[int] = None
    population: PopulationFilter = field(default_factory=PopulationFilter)
    confirmed_by_user_at: Optional[datetime] = None
    confirmed_by_user_id: Optional[str] = None
    definition_hash: str = ""
    supersedes: Optional[str] = None
    conflicts_found: tuple[DefinitionConflict, ...] = ()

    def __post_init__(self) -> None:
        if self.status == "locked":
            if self.confirmed_by_user_at is None or not self.confirmed_by_user_id:
                raise GoalNotLockedError(
                    "I9: a GoalDefinition may only reach status='locked' through "
                    "explicit user confirmation; confirmed_by_user_at and "
                    "confirmed_by_user_id are both required."
                )
            if self.origin not in ("adopted", "elicited"):
                raise GoalNotLockedError(
                    f"I9: locked definition needs origin 'adopted' or 'elicited', "
                    f"got {self.origin!r}. A definition is never inferred."
                )


# ── Claim ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Claim:
    """The atom. Every piece of evidence, from any source, normalises to this."""
    id: str
    assertion: str
    type: ClaimType
    subject: str
    source_id: str            # connector id — NOT an enum, see SPEC §4
    artifact_id: str
    artifact_type: str
    strength: EvidenceStrength
    observed_at: datetime     # REQUIRED — drives decay
    authoritative: bool       # computed from the registry, never self-declared
    population: PopulationFilter = field(default_factory=PopulationFilter)
    population_value: Optional[float] = None   # in goal currency; None = unknown
    magnitude: Optional[float] = None
    direction: Literal["positive", "negative", "neutral"] = "neutral"
    subject_cluster_id: Optional[str] = None
    raw: Any = None           # original payload, always retained

    def __post_init__(self) -> None:
        if self.type not in CLAIM_TYPES:
            raise ValueError(f"Unknown claim type {self.type!r}")
        if self.strength not in EVIDENCE_STRENGTHS:
            raise ValueError(f"Unknown evidence strength {self.strength!r}")

    @property
    def strength_score(self) -> float:
        return STRENGTH_SCORE[self.strength]


# ── Assumed parameters (I8) ──────────────────────────────────────────────────

@dataclass(frozen=True)
class AssumedParam:
    """A number that came from judgement rather than data.

    `impact_at_low` is the part that stops this being decoration: it says what
    the headline becomes at the pessimistic end of the range, so a reader can
    see how much of the recommendation rests on the assumption.
    """
    name: str
    value: Optional[float]
    basis: str
    plausible_range: tuple[float, float]
    impact_at_low: Optional[float] = None


# ── The I1 split: what each scorer is allowed to see ─────────────────────────

@dataclass(frozen=True)
class ImpactInputs(_FrozenMapping):
    """Everything `score_impact` may read.

    Nothing here reveals how many sources agree, and that omission IS invariant
    I1. Size is a claim about the world; corroboration is a claim about our
    evidence. Letting the second inform the first is what buries a quiet,
    high-value finding under a loud, low-value one — the single failure mode
    this engine exists to prevent.

    `affected_population` and `movable_gap` are `Optional` because a corpus that
    never measured them yields `None`, which must survive to the output as
    "not measured" rather than collapsing to a confident zero (I3).
    """
    currency: GoalCurrency
    affected_population: Optional[float]
    movable_gap: Optional[float]
    value_per_unit: Optional[float]
    assumed_params: tuple[AssumedParam, ...] = ()
    native_units: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "native_units", MappingProxyType(dict(self.native_units))
        )

    def __hash__(self) -> int:
        return hash((self.currency, self.affected_population, self.movable_gap,
                     self.value_per_unit, self.assumed_params,
                     _hashable(self.native_units)))


@dataclass(frozen=True)
class ConfidenceInputs:
    """Everything `score_confidence` may read.

    This is the ONLY place corroboration is legible, and even here the spec caps
    its contribution at +0.15 so that agreement can raise how sure we are
    without ever changing how big we said it was.
    """
    strengths: tuple[EvidenceStrength, ...]
    claim_types: tuple[ClaimType, ...]
    observed_ats: tuple[datetime, ...]
    authoritative_count: int
    claim_count: int
    independent_authoritative_source_types: int
    surfaced_by: tuple[str, ...] = ()
    sample_adequate: tuple[bool, ...] = ()
    coverage: Optional[float] = None
    blockers: tuple[str, ...] = ()
    #: True when no source in the corpus can speak to whether a fix works —
    #: no experiments, no measured outcomes. Corpus-only tenants band on the
    #: problem leg alone and cap at medium rather than rendering a combined
    #: score whose solution half is a constant. See docs/GOAL_ANALYSIS.md §2.
    solution_evidence_absent: bool = False


# ── Scored outputs — frozen, because Stage 10 must not write back (I10) ──────

@dataclass(frozen=True)
class Impact(_FrozenMapping):
    value: Optional[float]                    # None = not sizeable (I3)
    currency: GoalCurrency
    affected_population: Optional[float]
    movable_gap: Optional[float]
    value_per_unit: Optional[float]
    assumed_params: tuple[AssumedParam, ...] = ()
    native_units: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "native_units", MappingProxyType(dict(self.native_units))
        )

    def __hash__(self) -> int:
        return hash((self.value, self.currency, self.affected_population,
                     self.movable_gap, self.value_per_unit, self.assumed_params,
                     _hashable(self.native_units)))


@dataclass(frozen=True)
class Confidence(_FrozenMapping):
    band: ConfidenceBand
    score: float                              # internal only, NEVER rendered
    weakest_leg: Literal["problem", "solution"]
    weakest_leg_reason: str
    components: Mapping[str, float] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()
    #: Why the band could not go higher, when something structural capped it
    #: (e.g. no outcome evidence anywhere in the corpus). Rendered; the band
    #: alone would look like a judgement about this finding rather than a
    #: statement about the whole corpus.
    cap_reason: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "components", MappingProxyType(dict(self.components))
        )

    def __hash__(self) -> int:
        return hash((self.band, self.score, self.weakest_leg,
                     self.weakest_leg_reason, _hashable(self.components),
                     self.blockers, self.cap_reason))


def band_for(score: float) -> ConfidenceBand:
    """SPEC §9. Bands, not decimals — decimals never reach the output."""
    return "high" if score >= 0.75 else "medium" if score >= 0.50 else "low"


# ── Effort (I7) ──────────────────────────────────────────────────────────────

#: Fewer comparables than this and an estimate is a guess wearing a number.
MIN_EFFORT_COMPARABLES = 3


@dataclass(frozen=True)
class EffortEstimate:
    """I7: shows its derivation or does not exist.

    Construction refuses an estimate backed by fewer than three comparables, so
    "just put something so the arithmetic completes" fails loudly. An item whose
    effort cannot be derived is `unrankable` with its reason — never assigned a
    fabricated number, because that laundered guess then decides an ordering.
    """
    weeks: Optional[float]
    derivation: str
    comparables: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if self.weeks is not None and len(self.comparables) < MIN_EFFORT_COMPARABLES:
            raise ValueError(
                f"I7: an effort estimate needs >= {MIN_EFFORT_COMPARABLES} "
                f"comparable prior projects, got {len(self.comparables)}. "
                f"Return EffortEstimate(weeks=None, derivation=<why>) instead."
            )
        if not self.derivation:
            raise ValueError("I7: an effort estimate must state its derivation.")

    @property
    def derivable(self) -> bool:
        return self.weeks is not None


# ── Finding ──────────────────────────────────────────────────────────────────

Adjudication = Literal[
    "conflict",                 # authoritative sources disagree — a FINDING
    "single_authoritative",     # stands at full weight — the quiet-finding guard
    "corroborated",
    "no_authoritative_source",
]


@dataclass(frozen=True)
class Finding:
    """A candidate, carrying both scorers' inputs but keeping them apart.

    A scorer is handed the whole `Finding` in practice, which is exactly why the
    I1 test exists: the type system stops `score_impact` reading corroboration
    only if it takes `ImpactInputs`, and the test catches it if it takes this.
    """
    id: str
    #: NOT linted here, deliberately. I5 needs the evidence STRENGTH the
    #: statement rests on, which is a property of the claim set rather than of
    #: this object, and a comment saying "must pass the lint" would be the exact
    #: thing this module argues against. Linting happens where a statement
    #: leaves the engine: call `lint.assert_lint_clean(statement, strength)` at
    #: the render boundary.
    statement: str
    claim_ids: tuple[str, ...]
    impact_inputs: ImpactInputs
    confidence_inputs: ConfidenceInputs
    adjudication: Adjudication = "no_authoritative_source"
    cell_refs: tuple[str, ...] = ()

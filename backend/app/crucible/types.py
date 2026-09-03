"""Crucible data model — the contract every stage is built against.

Ported from `backend/docs/crucible/CRUCIBLE-SPEC.md` §3. Do not add fields here
without updating that spec.

Three properties of this module do real work and are worth reading for before
using anything in it:

**Everything is frozen.** `Impact` and `Confidence` are immutable because I10
says prioritisation reads frozen scores and never writes back — "the reason to
do something first must not change how big we said it was". A frozen dataclass
turns that from a rule people remember into an `AttributeError`. Mapping fields
become a `FrozenDict` on construction for the same reason — a frozen dataclass
otherwise leaves a dict field wide open — and `FrozenDict` is a `dict` subclass
rather than a `MappingProxyType` so that `hash`, `deepcopy`, `pickle`, `json`
and `dataclasses.asdict` all keep working. See its docstring for why the proxy
could not.

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
from typing import Any, Literal, Mapping, NamedTuple, Optional, Sequence


class FrozenDict(dict):
    """An immutable mapping that is still a `dict`.

    Replaces `MappingProxyType` for every mapping field below. The proxy made
    the objects genuinely immutable and cost three capabilities in exchange —
    `hash`, `deepcopy`/`pickle`, and `dataclasses.asdict` — and only the first
    two can be bought back on the owning class. `asdict` cannot: it walks the
    fields itself and deep-copies each VALUE directly, so it hits the bare
    proxy and raises `TypeError: cannot pickle 'mappingproxy' object` even when
    the mapping is empty, no matter what `__deepcopy__` the dataclass defines.
    That is the traversal PR2's persistence (`asdict(finding)` into jsonb) and
    PR9's response serialiser reach for first.

    Being a real `dict` subclass fixes all three at once: `asdict` rebuilds it
    as its own type, `deepcopy` and `pickle` work because dicts do, `json`
    serialises it, and the mutators below still refuse. Immutability is
    enforced by overriding every mutating method rather than by wrapping.
    """

    __slots__ = ()

    def _immutable(self, *_a, **_k):
        raise TypeError(
            "FrozenDict is immutable — Crucible score objects are frozen so "
            "that prioritisation cannot write back to them (I10)."
        )

    __setitem__ = __delitem__ = _immutable          # type: ignore[assignment]
    pop = popitem = clear = update = setdefault = _immutable  # type: ignore[assignment]
    # `|=` IS A MUTATOR, and missing it traded away a property the proxy had:
    # `MappingProxyType` refuses `|=` outright, while a dict subclass inherits
    # `dict.__ior__` and updates in place. `impact.native_units |= {...}` is
    # safe by accident (the frozen dataclass's `__setattr__` catches the
    # rebind), but `d = impact.native_units; d |= {...}` writes straight
    # through into the frozen score — I10's exact harm, from the class built to
    # prevent it. `|` is fine and deliberately left alone: it returns a new
    # plain dict and mutates nothing.
    __ior__ = _immutable                            # type: ignore[assignment]

    def __hash__(self) -> int:                       # type: ignore[override]
        return hash(tuple(sorted(self.items())))

    def __copy__(self) -> "FrozenDict":
        return self                                  # immutable: sharing is safe

    def __reduce__(self):
        return (type(self), (dict(self),))


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

class GroundedFigure(NamedTuple):
    """One deduplicated, transcript-stated commercial figure.

    THE IDENTITY, NOT JUST THE NUMBER, and that is the whole reason this type
    exists rather than a bare `float`. A finding's grounded sum is already
    deduplicated within itself, but the same deal can be described by two
    different signals that cluster into two different findings — clustering
    keys on subject, so a renewal figure can legitimately appear under both a
    pricing theme and a churn theme. Summing findings' totals would then count
    that money twice. Carrying the identities lets any consumer that sums
    ACROSS findings apply the same rule one more time.

    `account_key` IS OPAQUE ON PURPOSE. Deduplication needs to know that two
    figures belong to the same customer; nothing downstream needs to know
    WHICH customer, and this value travels into scored objects that are
    logged and diffed. A stable digest gives the first and refuses the
    second. Empty string means no account was named, which is its own
    identity class (see `pipeline._grounded_commercial_native_units`).

    `derived` marks a figure read back out of a written summary rather than
    captured against a verified verbatim quote. The failure mode there is
    transcription error, not invention — so it is a reason to hedge the
    wording proportionately, never a reason to hide the figure.

    `committed` IS THE FIELD THAT DECIDES WHETHER A FIGURE MAY BE ADDED UP,
    and it exists because deduplication was solving the wrong problem. A
    sample of the real corpus found one list price — "$30,000 for 50 users" —
    quoted to SIXTEEN different accounts across sixteen different sales
    calls. Those are not duplicates: they are sixteen genuine prospects and
    sixteen genuine mentions, so nothing about deduplication touches them.
    They are also not $480,000 of anything. Summing was the wrong OPERATION,
    not merely the wrong rows.

    So a committed figure (an issued quote, a contract value, a named deal)
    is summable and answers a money target. A list price is a RATE CARD:
    reported as a range across the accounts it was quoted to, never totalled,
    because the total means nothing.

    DEFAULTS TO FALSE — the non-additive side. Every failure in this feature's
    history has been over-claiming, so a figure nothing has positively
    identified as committed stays out of the sum.
    """

    account_key: str
    amount: float
    derived: bool
    committed: bool = False
    #: May this figure appear in the non-additive pricing range? A THIRD
    #: STATE: a figure can be neither summed nor ranged — a salary, a
    #: competitor's fee, a hypothetical — and before the classifier existed
    #: everything that failed the committed test silently became a price,
    #: which is how a candidate's career track record became a pricing
    #: maximum.
    list_price: bool = False


#: How many ordinal bands a size is REPORTED in. Quartiles: enough to
#: separate a big finding from a small one, few enough that the output never
#: implies a precision the underlying evidence cannot support. A percentile
#: would read as measurement; a quartile reads as what it is, a rough
#: position among peers.
#:
#: A DISPLAY GRANULARITY, NOT AN ORDERING ONE. Ordering reads `size_rank`,
#: the underlying position at full resolution, because quantising first and
#: then trying to break the ties would mean comparing findings measured in
#: different currencies — the exact comparison the band exists to avoid. So
#: the coarse number is what a reader sees and the fine one is what sorts.
SIZE_BANDS = 4


def _band_for_rank(size_rank: Optional[float]) -> Optional[int]:
    """A full-resolution position among peers -> the quartile it is reported
    in. `None` in, `None` out: nothing to rank is not band 1.

    Clamped at both ends so a rank of exactly 0.0 cannot produce a band 0 and
    floating-point drift just above 1.0 cannot produce a band 5.
    """
    if size_rank is None:
        return None
    return max(1, min(SIZE_BANDS, math.ceil(SIZE_BANDS * size_rank)))


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
    if not math.isfinite(value):
        # A non-finite value is a computation that went wrong, not a
        # measurement. `int(value)` below would raise ValueError/OverflowError
        # at a render boundary; degrade instead of crashing the run.
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
class PopulationFilter:
    segments: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    estimated_size: Optional[int] = None      # None = not measured (I3)

    def __post_init__(self) -> None:
        object.__setattr__(self, "segments", FrozenDict(self.segments))

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
    """The atom. Every piece of evidence, from any source, normalises to this.

    Hashable on IDENTITY, not on contents: `raw` holds the original payload —
    an arbitrary dict on every real claim — so hashing the field tuple raises
    `TypeError: unhashable type: 'dict'`. Every test fixture leaves `raw` at
    None, so a contents-hash looks fine in the suite and fails on the first
    real run, which is exactly the shape of bug worth closing before PR6 dedups
    claims with `set(...)`.
    """
    id: str
    assertion: str
    type: ClaimType
    subject: str
    # THE SOURCE TYPE, despite the name. `project_signal` assigns
    # `source_id=source_type` (`claims.py`), so this carries `customer_voice` /
    # `project_mgmt` / … — the same vocabulary `AUTHORITATIVE_FOR` is keyed on,
    # which is why the authority check works at all. The comment here used to
    # read "connector id — NOT an enum, see SPEC §4", which described the SPEC's
    # intent rather than the code, and the run's published `sources` count is
    # correct only because the code disagrees with it. If this is ever made a
    # real connector id, `routes/crucible.py`'s `sources` must stop counting it.
    source_id: str
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
    #: WHAT KIND OF MONEY `magnitude` IS — one of
    #: `app.crucible.figure_class.FIGURE_CLASSES`, or `None` when nothing
    #: classified it (the model was not run, or did not answer for this
    #: claim). `None` is not a category and never means "assume the good
    #: one": the pipeline falls back to its deterministic phrase families,
    #: which admit money to a sum only on a positive signal.
    #:
    #: A CATEGORY, NEVER A DECISION (I2). The consequence of each class —
    #: summed, ranged, or refused — is a fixed table in `pipeline`, not
    #: anything the classifier returns.
    figure_class: Optional[str] = None
    raw: Any = None           # original payload, always retained

    def __post_init__(self) -> None:
        if self.type not in CLAIM_TYPES:
            raise ValueError(f"Unknown claim type {self.type!r}")
        if self.strength not in EVIDENCE_STRENGTHS:
            raise ValueError(f"Unknown evidence strength {self.strength!r}")

    def __hash__(self) -> int:
        # `id` is the claim's identity; two claims with the same id ARE the same
        # claim. Consistent with the generated `__eq__`, which compares every
        # field: equal claims share an id, so they hash equal.
        return hash(self.id)

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
class ImpactInputs:
    """Everything `score_impact` may read.

    Nothing here reveals how many sources agree, and that omission IS invariant
    I1. Size is a claim about the world; corroboration is a claim about our
    evidence. Letting the second inform the first is what buries a quiet,
    high-value finding under a loud, low-value one — the single failure mode
    this engine exists to prevent.

    `affected_population` and `movable_gap` are `Optional` because a corpus that
    never measured them yields `None`, which must survive to the output as
    "not measured" rather than collapsing to a confident zero (I3).

    `size_rank` IS A CROSS-FINDING COMPARISON, AND IT IS STILL NOT
    CORROBORATION. Every other field here describes this finding alone; the
    rank describes where its size falls among the other findings in the same
    run. That is a legitimate thing for size to depend on and an illegitimate
    thing for size to be computed from at scoring time, so the comparison
    happens in Stage 8 — BEFORE any scoring — and arrives here as an ordinary
    input. `score_impact` still reads this object and nothing else.

    The distinction that keeps I1 intact: the rank orders findings by how much
    money or how many accounts they carry, never by how many claims say so.
    An amount restated ten times and an amount stated once rank identically,
    because the sum they rank on is deduplicated before it is taken (see
    `pipeline._grounded_commercial_native_units`). Corroboration cannot reach
    this field, which is why the dedup had to land first.
    """
    currency: GoalCurrency
    affected_population: Optional[float]
    movable_gap: Optional[float]
    value_per_unit: Optional[float]
    assumed_params: tuple[AssumedParam, ...] = ()
    native_units: Mapping[str, float] = field(default_factory=dict)
    #: Where this finding's size falls among its OWN currency's peers in the
    #: same run, from just above 0 to 1.0 for the largest — dollar findings
    #: against dollar findings, reach findings against reach findings.
    #: `None` means there was nothing to rank: no grounded figure and no
    #: measured reach.
    #:
    #: STORED AT FULL RESOLUTION, RENDERED AS A QUARTILE. `size_band` below
    #: derives from this rather than sitting beside it as a second field,
    #: so the number a reader sees and the number that sorts can never
    #: disagree.
    size_rank: Optional[float] = None
    #: The deduplicated figures behind `native_units`' grounded dollar sum,
    #: carried as identities so a consumer summing ACROSS findings can apply
    #: the same deduplication one more time. See `GroundedFigure`.
    grounded_figures: tuple[GroundedFigure, ...] = ()

    @property
    def size_band(self) -> Optional[int]:
        """The reportable quartile, 1..4 with 4 largest. Derived, never
        stored — see `size_rank`."""
        return _band_for_rank(self.size_rank)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "native_units", FrozenDict(self.native_units)
        )

    def __hash__(self) -> int:
        return hash((self.currency, self.affected_population, self.movable_gap,
                     self.value_per_unit, self.assumed_params,
                     _hashable(self.native_units), self.size_rank,
                     self.grounded_figures))


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
class Impact:
    value: Optional[float]                    # None = not sizeable (I3)
    currency: GoalCurrency
    affected_population: Optional[float]
    movable_gap: Optional[float]
    value_per_unit: Optional[float]
    assumed_params: tuple[AssumedParam, ...] = ()
    native_units: Mapping[str, float] = field(default_factory=dict)
    #: Carried through from `ImpactInputs` unchanged, so ordering can read it
    #: off the FROZEN score without ever recomputing a comparison (I10). A
    #: finding can be unsizeable in the goal's currency (`value is None`) and
    #: still carry a rank, which is the case a quoted figure with no named
    #: account produces: we did not measure its reach, and saying so is not
    #: the same as saying it is worth nothing.
    size_rank: Optional[float] = None
    #: Carried through from `ImpactInputs` so a consumer summing across
    #: findings can deduplicate the same money one more time.
    grounded_figures: tuple[GroundedFigure, ...] = ()

    @property
    def size_band(self) -> Optional[int]:
        """The reportable quartile, 1..4 with 4 largest. Derived from
        `size_rank`, never stored alongside it."""
        return _band_for_rank(self.size_rank)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "native_units", FrozenDict(self.native_units)
        )

    def __hash__(self) -> int:
        return hash((self.value, self.currency, self.affected_population,
                     self.movable_gap, self.value_per_unit, self.assumed_params,
                     _hashable(self.native_units), self.size_rank,
                     self.grounded_figures))


@dataclass(frozen=True)
class Confidence:
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
            self, "components", FrozenDict(self.components)
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
    #: THE THEME, ON ITS OWN. `statement` embeds it in a sentence — "30 claims
    #: across 11 accounts concern “Sales Pipeline” — for example, …" — which is
    #: a fine thing to read and a terrible thing to SCAN: the one word the
    #: reader is looking for sits mid-clause, in quotes, behind two numbers they
    #: are about to be shown again as chips. Carried separately so a renderer
    #: can LEAD with it rather than reverse-engineer it back out of prose.
    #:
    #: Defaulted because the pipeline is not the only thing that builds a
    #: Finding — fixtures and older stored rows have none, and a renderer that
    #: falls back to the statement is correct for them.
    label: str = ""
    #: One claim in its source's own words, already linted as part of the
    #: statement it came from. Empty when the statement fell back to its plain
    #: form — never re-derived, because an example that is innocent alone can be
    #: causal in a sentence and the lint runs on the sentence.
    example: str = ""

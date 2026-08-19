"""The ten Crucible invariants, as tests. SPEC §1.

These are correctness properties, not style. A version of this engine with
beautiful stages and violated invariants is worth nothing, because it produces
confident, well-formatted, wrong answers — which is worse than producing none.

**Every harness here is proved to be able to FAIL.** For each property there are
two fakes: one that honours it and one that breaks it, and the test asserts the
harness passes the first and raises on the second. A property test that has only
ever been green is indistinguishable from one that cannot go red, and the
harnesses ship before the implementations they will guard (PR1 wires nothing),
so this is the only evidence available that they work at all.

No network, no DB, no LLM.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from app.crucible.invariants import (
    DECISION_FIELD_NAMES,
    INVARIANTS,
    InvariantViolation,
    SourceAuthority,
    assert_assumed_params_disclosed,
    assert_goal_locked,
    assert_impact_ignores_corroboration,
    assert_llm_schema_returns_no_decision,
    assert_aggregate_propagates_unmeasured,
    assert_sum_skips_unmeasured,
    assert_no_corroboration_fields,
    assert_only_read_sources_mentioned,
    assert_scores_frozen_across,
    decay_factor,
    derive_effort,
    is_authoritative,
    require_authority,
    validate_source_authority,
)
from app.crucible.types import (
    CORROBORATION_FIELDS,
    AssumedParam,
    Claim,
    Confidence,
    ConfidenceInputs,
    EffortEstimate,
    Finding,
    GoalDefinition,
    GoalNotLockedError,
    Impact,
    ImpactInputs,
    PopulationFilter,
    band_for,
    nmean,
    nsum,
    nsum_with_coverage,
    render_measure,
)

NOW = datetime(2026, 8, 17, tzinfo=timezone.utc)


def a_finding(**overrides) -> Finding:
    """A finding whose impact side and confidence side are both populated, so a
    scorer that peeks across the line has something to peek at."""
    impact_inputs = ImpactInputs(
        currency="arr_dollars",
        affected_population=3_288_200,
        movable_gap=0.16,
        value_per_unit=156.0,
    )
    confidence_inputs = ConfidenceInputs(
        strengths=("measured", "reported"),
        claim_types=("magnitude", "preference"),
        observed_ats=(NOW - timedelta(days=30), NOW - timedelta(days=200)),
        authoritative_count=2,
        claim_count=5,
        independent_authoritative_source_types=3,
        surfaced_by=("structural", "corpus"),
    )
    base = dict(
        id="f-1", statement="Default budget sits below revealed preference.",
        claim_ids=("c-1", "c-2"), impact_inputs=impact_inputs,
        confidence_inputs=confidence_inputs, adjudication="corroborated",
    )
    base.update(overrides)
    return Finding(**base)


# ══ I1. Impact never reads corroboration ═════════════════════════════════════
# The single failure mode this engine exists to prevent. Everything else in this
# file protects the quality of an answer; this protects whether the quiet,
# high-value finding is in the answer at all.

def compliant_score_impact(finding: Finding) -> Impact:
    """Reads `impact_inputs` and nothing else — the shape PR7 must keep."""
    i = finding.impact_inputs
    if i.affected_population is None or i.movable_gap is None or i.value_per_unit is None:
        value = None                       # I3: unsizeable is None, not 0
    else:
        value = i.affected_population * i.movable_gap * i.value_per_unit
    return Impact(
        value=value, currency=i.currency,
        affected_population=i.affected_population, movable_gap=i.movable_gap,
        value_per_unit=i.value_per_unit, assumed_params=i.assumed_params,
    )


def violating_score_impact(finding: Finding) -> Impact:
    """The "small bonus when four sources agree" change spec F2 predicts.

    It is genuinely tempting: it makes demo output look more sensible. It also
    re-buries every quiet finding, which turns the product into a slower, more
    expensive way to surface the obvious.
    """
    base = compliant_score_impact(finding)
    bonus = 1.0 + 0.05 * finding.confidence_inputs.independent_authoritative_source_types
    value = None if base.value is None else base.value * bonus
    return dataclasses.replace(base, value=value)


def test_i1_harness_passes_a_compliant_scorer():
    assert_impact_ignores_corroboration(compliant_score_impact, a_finding())


def test_i1_harness_catches_a_corroboration_bonus():
    """Mutation proof: without this the harness could be vacuous."""
    with pytest.raises(InvariantViolation) as exc:
        assert_impact_ignores_corroboration(violating_score_impact, a_finding())
    assert exc.value.invariant == "I1"


def test_i1_harness_catches_a_scorer_keyed_on_surfaced_by():
    """The other realistic shape: ranking by which sweeps found it."""
    def by_sweep_count(finding: Finding) -> Impact:
        base = compliant_score_impact(finding)
        n = len(finding.confidence_inputs.surfaced_by)
        return dataclasses.replace(
            base, value=None if base.value is None else base.value * (1 + n)
        )

    with pytest.raises(InvariantViolation):
        assert_impact_ignores_corroboration(by_sweep_count, a_finding())


def test_i1_impact_inputs_cannot_carry_corroboration():
    """Structural half — the regression is someone ADDING such a field."""
    assert_no_corroboration_fields(ImpactInputs)


def test_i1_structural_check_catches_a_leaked_field():
    @dataclasses.dataclass(frozen=True)
    class LeakyImpactInputs:
        affected_population: int
        source_count: int          # <- corroboration on the impact side

    with pytest.raises(InvariantViolation) as exc:
        assert_no_corroboration_fields(LeakyImpactInputs)
    assert exc.value.invariant == "I1"
    assert "source_count" in str(exc.value)


def test_i1_confidence_inputs_is_where_corroboration_belongs():
    """The split is only meaningful if the confidence side genuinely has it."""
    names = {f.name for f in dataclasses.fields(ConfidenceInputs)}
    assert names & CORROBORATION_FIELDS


# ══ I2. The LLM proposes, deterministic code decides ═════════════════════════

def test_i2_accepts_a_candidate_returning_schema():
    schema = {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "assertion": {"type": "string"},
                    "population": {"type": "string"},
                }},
            }
        },
    }
    assert_llm_schema_returns_no_decision(schema, "claim_extraction")


def test_i2_rejects_a_schema_returning_a_score():
    schema = {"type": "object", "properties": {
        "assertion": {"type": "string"}, "score": {"type": "number"}}}
    with pytest.raises(InvariantViolation) as exc:
        assert_llm_schema_returns_no_decision(schema, "claim_extraction")
    assert exc.value.invariant == "I2"


def test_i2_finds_a_decision_field_nested_in_an_array():
    """The likelier way one appears: not at the top level, but per item."""
    schema = {"type": "object", "properties": {"levers": {
        "type": "array",
        "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "priority": {"type": "integer"}}},
    }}}
    with pytest.raises(InvariantViolation) as exc:
        assert_llm_schema_returns_no_decision(schema, "lever_generation")
    assert "levers[].priority" in str(exc.value)


def test_i2_confidence_is_a_decision_field():
    """Extraction may report evidence STRENGTH; it may not report confidence.
    Strength is an observation about a document, confidence is a judgement the
    scoring functions own."""
    assert "confidence" in DECISION_FIELD_NAMES
    with pytest.raises(InvariantViolation):
        assert_llm_schema_returns_no_decision(
            {"properties": {"confidence": {"type": "number"}}}, "site")


# ══ I3. Unmeasured is not zero ═══════════════════════════════════════════════

def test_i3_nsum_honours_the_property():
    assert_sum_skips_unmeasured(nsum)


def test_i3_nmean_honours_the_generic_property():
    """The generic harness must not accuse a correct `nmean` of violating I3
    for returning 6.0 rather than the sum — a false accusation against a
    correct function is how a guard gets deleted."""
    assert_aggregate_propagates_unmeasured(nmean)


def test_i3_harness_catches_the_or_zero_idiom():
    """`sum(v or 0 for v in values)` is THE way this bug ships. It is
    arithmetically fine and quietly asserts the unmeasured cells were empty."""
    def coercing_sum(values):
        return sum(v or 0 for v in values)

    with pytest.raises(InvariantViolation) as exc:
        assert_sum_skips_unmeasured(coercing_sum)
    assert exc.value.invariant == "I3"


def test_i3_all_unmeasured_is_none_not_zero():
    assert nsum([None, None]) is None
    assert nmean([None, None]) is None


def test_i3_mixed_null_skips_rather_than_zeroes():
    """The all-null case usually gets handled; the mixed case usually does not."""
    assert nsum([None, 5.0, None, 7.0]) == 12.0
    assert nmean([None, 4.0, 8.0]) == 6.0


def test_i3_coverage_is_recoverable_from_the_aggregate():
    """A total over 2 of 4 cells is a different claim from one over 4 of 4, and
    the number alone cannot tell them apart."""
    total, measured, seen = nsum_with_coverage([None, 5.0, None, 7.0])
    assert (total, measured, seen) == (12.0, 2, 4)


def test_i3_unmeasured_renders_as_words_not_a_number():
    assert render_measure(None) == "not measured"
    assert render_measure(0.0) == "0"
    assert render_measure(1234.0, " accounts") == "1,234 accounts"


def test_i3_zero_survives_as_zero():
    """A measured zero is a real measurement and must not become "not measured"
    — the invariant runs in one direction only."""
    assert nsum([0.0, 0.0]) == 0.0


# ══ I4. A source never votes outside its authority ═══════════════════════════

CUSTOMER_VOICE = SourceAuthority(
    source_id="zendesk",
    authoritative_for=frozenset({"preference", "mechanism"}),
    never_authoritative_for=frozenset({"magnitude"}),
    selection_bias="self_selected",
)
TRACKER = SourceAuthority(
    source_id="jira",
    authoritative_for=frozenset({"attempt", "existence", "constraint"}),
    never_authoritative_for=frozenset({"preference", "magnitude"}),
    selection_bias="census",
)


def test_i4_valid_manifests_pass():
    validate_source_authority(CUSTOMER_VOICE)
    validate_source_authority(TRACKER)


def test_i4_self_selected_source_may_never_size_a_population():
    """The one-line rule that prevents the most common failure in evidence
    synthesis: letting review volume decide what the biggest problem is.

    Shaped as onboarding actually produces it — an LLM proposing a manifest
    lists what the source IS good for and leaves `never_authoritative_for`
    empty, so the self-selection rule is the only thing standing between a
    review feed and a population estimate. (With `magnitude` in both sets the
    intersection check fires first, which is a different bug.)
    """
    bad = SourceAuthority(
        source_id="g2_reviews",
        authoritative_for=frozenset({"preference", "magnitude"}),
        selection_bias="self_selected",
    )
    with pytest.raises(InvariantViolation) as exc:
        validate_source_authority(bad)
    assert exc.value.invariant == "I4"
    assert "self_selected" in str(exc.value)


def test_i4_a_census_source_may_size_a_population():
    """The rule is about selection bias, not about being qualitative — a census
    source with magnitude authority is fine."""
    validate_source_authority(SourceAuthority(
        source_id="billing",
        authoritative_for=frozenset({"magnitude", "direction"}),
        selection_bias="census",
    ))


def test_i4_intersecting_authority_sets_are_rejected():
    bad = dataclasses.replace(
        TRACKER, never_authoritative_for=frozenset({"attempt"})
    )
    with pytest.raises(InvariantViolation):
        validate_source_authority(bad)


def test_i4_unknown_claim_types_are_rejected():
    bad = dataclasses.replace(TRACKER, authoritative_for=frozenset({"vibes"}))
    with pytest.raises(InvariantViolation):
        validate_source_authority(bad)


def a_claim(source_id: str, claim_type: str, **kw) -> Claim:
    return Claim(
        id=f"c-{source_id}-{claim_type}", assertion="…", type=claim_type,
        subject="export", source_id=source_id, artifact_id="a-1",
        artifact_type="ticket", strength="reported", observed_at=NOW,
        authoritative=True,        # deliberately wrong; the registry decides
        **kw,
    )


def test_i4_authority_comes_from_the_registry_not_the_claim():
    """A claim arriving with `authoritative=True` must not be believed — that
    field is computed, and a self-declared one is how a source votes outside
    its lane."""
    registry = {"zendesk": CUSTOMER_VOICE, "jira": TRACKER}
    claims = require_authority(
        [a_claim("zendesk", "preference"), a_claim("zendesk", "magnitude")],
        registry,
    )
    assert [c.authoritative for c in claims] == [True, False]


def test_i4_non_authoritative_claims_are_retained_not_dropped():
    """SPEC Stage 4. They contribute zero confidence and supply the mechanism
    detail that makes a finding actionable; dropping them makes output correct
    and useless at once."""
    registry = {"jira": TRACKER}
    claims = require_authority([a_claim("jira", "preference")], registry)
    assert len(claims) == 1
    assert claims[0].authoritative is False


def test_i4_an_unregistered_source_votes_on_nothing():
    claims = require_authority([a_claim("mystery", "magnitude")], {})
    assert claims[0].authoritative is False


def test_i4_is_authoritative_is_a_pure_lookup():
    assert is_authoritative(TRACKER, "existence") is True
    assert is_authoritative(TRACKER, "preference") is False


# ══ I6. Empty sources are closed silently ════════════════════════════════════

DRIVE = SourceAuthority(
    source_id="google_drive",
    authoritative_for=frozenset({"existence"}),
    display_names=("Google Drive", "Drive"),
)
TEAMS = SourceAuthority(
    source_id="ms_teams",
    authoritative_for=frozenset({"attempt"}),
    display_names=("Microsoft Teams", "Teams"),
)


def test_i6_output_may_name_sources_that_were_read():
    assert_only_read_sources_mentioned(
        "Slack and Jira both record the escalation.",
        read_source_ids=["slack", "jira"],
        known_sources=["slack", "jira", "zendesk"],
    )


@pytest.mark.parametrize("text", [
    "We found nothing in Google Drive for this.",
    "Microsoft Teams has no record of the escalation.",
])
def test_i6_catches_the_prose_name_not_just_the_slug(text):
    """Connector ids are slugs (`google_drive`, `ms_teams`) and narrative never
    writes the slug, so an id-only check protected nothing for most sources."""
    with pytest.raises(InvariantViolation) as exc:
        assert_only_read_sources_mentioned(
            text, read_source_ids=["slack"], known_sources=[DRIVE, TEAMS],
        )
    assert exc.value.invariant == "I6"


def test_i6_a_read_source_may_be_named_in_prose():
    assert_only_read_sources_mentioned(
        "Google Drive holds the readout.",
        read_source_ids=["google_drive"], known_sources=[DRIVE, TEAMS],
    )


def test_i6_output_may_not_name_a_source_that_was_never_read():
    """"We found nothing in Zendesk" implies a search that never happened, and
    invites the reader to discount a finding for a gap that does not exist."""
    with pytest.raises(InvariantViolation) as exc:
        assert_only_read_sources_mentioned(
            "Nothing in Zendesk supports this.",
            read_source_ids=["slack"],
            known_sources=["slack", "zendesk"],
        )
    assert exc.value.invariant == "I6"


# ══ I7. Effort shows its derivation or does not exist ════════════════════════

def test_i7_three_comparables_yield_a_median_and_a_derivation():
    est = derive_effort([4.0, 6.0, 9.0], surface="billing")
    assert est.weeks == 6.0
    assert est.derivable is True
    assert "median of 3 prior projects on billing" in est.derivation


def test_i7_two_comparables_yield_none_with_a_reason():
    est = derive_effort([4.0, 6.0], surface="billing")
    assert est.weeks is None
    assert est.derivable is False
    assert "insufficient comparable history" in est.derivation


def test_i7_an_estimate_cannot_be_constructed_without_its_comparables():
    """Fabricating a number to complete a RICE score launders a guess into a
    decision. The type refuses."""
    with pytest.raises(ValueError) as exc:
        EffortEstimate(weeks=6.0, derivation="felt about right", comparables=(4.0,))
    assert "I7" in str(exc.value)


def test_i7_a_null_estimate_still_has_to_say_why():
    with pytest.raises(ValueError):
        EffortEstimate(weeks=None, derivation="")


# ══ I8. Assumed parameters are visibly distinguished ═════════════════════════

RECOVERY = AssumedParam(
    name="value_per_account", value=None,
    basis="no revenue data connected; accounts weighted equally",
    plausible_range=(0.0, 1.0),
)


def test_i8_disclosed_assumptions_pass():
    impact = Impact(value=4.0, currency="accounts", affected_population=4,
                    movable_gap=None, value_per_unit=None,
                    assumed_params=(RECOVERY,))
    assert_assumed_params_disclosed(impact, [RECOVERY])


def test_i8_an_undisclosed_assumption_is_a_violation():
    """A flagged assumption the reader never sees is an unflagged one."""
    impact = Impact(value=4.0, currency="accounts", affected_population=4,
                    movable_gap=None, value_per_unit=None,
                    assumed_params=(RECOVERY,))
    with pytest.raises(InvariantViolation) as exc:
        assert_assumed_params_disclosed(impact, [])
    assert exc.value.invariant == "I8"


# ══ I9. The goal definition is adopted or elicited, never inferred ═══════════

def a_definition(**overrides) -> GoalDefinition:
    base = dict(
        id="g-1", raw_goal_text="drive revenue", metric_name="Gross Advertiser Spend",
        definition_text="billed spend net of credits and refunds",
        currency="arr_dollars", direction="increase",
    )
    base.update(overrides)
    return GoalDefinition(**base)


def test_i9_an_unlocked_definition_cannot_enter_stage_one():
    with pytest.raises(GoalNotLockedError):
        assert_goal_locked(a_definition(status="candidate"))


def test_i9_a_locked_confirmed_definition_passes():
    assert_goal_locked(a_definition(
        status="locked", origin="adopted",
        confirmed_by_user_at=NOW, confirmed_by_user_id="u-1",
    ))


def test_i9_locked_without_confirmation_cannot_even_be_constructed():
    """The invariant is enforced at construction, so a code path that forgets
    to check — including an LLM-populated one — cannot produce the state."""
    with pytest.raises(GoalNotLockedError) as exc:
        a_definition(status="locked", origin="adopted")
    assert "I9" in str(exc.value)


def test_i9_locked_requires_adopted_or_elicited_origin():
    with pytest.raises(GoalNotLockedError):
        a_definition(status="locked", confirmed_by_user_at=NOW,
                     confirmed_by_user_id="u-1", origin=None)


def test_i9_there_is_no_inferred_origin():
    """The absence IS the invariant. If a future edit adds 'inferred' to the
    Literal, this fails and the reviewer has to argue for it explicitly."""
    from app.crucible.types import GoalOrigin
    assert set(GoalOrigin.__args__) == {"adopted", "elicited"}


def test_i9_conflicts_are_carried_not_resolved():
    """Never pick between conflicting definitions; surface the conflict."""
    definition = a_definition(status="candidate")
    assert definition.conflicts_found == ()
    assert definition.status != "locked"


# ══ I10. Prioritisation never mutates impact or confidence ═══════════════════

def an_impact() -> Impact:
    return Impact(value=492_000_000.0, currency="arr_dollars",
                  affected_population=3_288_200, movable_gap=0.16,
                  value_per_unit=156.0)


def a_confidence() -> Confidence:
    return Confidence(band="medium", score=0.62, weakest_leg="problem",
                      weakest_leg_reason="one segment only",
                      components={"strongest": 0.9})


def test_i10_scores_are_frozen_against_in_place_writes():
    impact = an_impact()
    with pytest.raises(dataclasses.FrozenInstanceError):
        impact.value = 1.0                                    # type: ignore[misc]


def test_i10_component_maps_are_frozen_too():
    """A frozen dataclass with a plain dict field is not frozen where it
    matters — the dict is still writable."""
    conf = a_confidence()
    with pytest.raises(TypeError):
        conf.components["strongest"] = 0.1                    # type: ignore[index]


def test_i10_a_read_only_prioritiser_passes():
    scored = [(an_impact(), a_confidence())]

    def order_only(items):
        return sorted(items, key=lambda pair: -(pair[0].value or 0))

    assert_scores_frozen_across(order_only, scored)


def test_i10_harness_catches_a_prioritiser_that_rewrites_scores():
    """The route freezing does not close: a step that rebuilds altered copies
    while claiming to have only ordered them."""
    scored = [(an_impact(), a_confidence())]

    def rewrites(items):
        # Effort in the denominator makes low-effort items float; someone then
        # "compensates" by tuning impact. Now sizing depends on how busy the
        # team is, which is nonsense.
        items[0] = (dataclasses.replace(items[0][0], value=1.0), items[0][1])
        return items

    with pytest.raises(InvariantViolation) as exc:
        assert_scores_frozen_across(rewrites, scored)
    assert exc.value.invariant == "I10"


# ══ Shared machinery ═════════════════════════════════════════════════════════

def test_decay_is_keyed_on_claim_type_not_source_type():
    """A mechanism outlives a competitor fact, and where the claim came from
    cannot tell you which it is."""
    old = NOW - timedelta(days=90)
    assert decay_factor("direction", old, NOW) == pytest.approx(0.5)
    assert decay_factor("mechanism", old, NOW) > 0.85


def test_execution_facts_do_not_decay():
    """They are re-readable: the repo either still says it or it does not."""
    ancient = NOW - timedelta(days=4000)
    assert decay_factor("existence", ancient, NOW) == 1.0
    assert decay_factor("attempt", ancient, NOW) == 1.0


def test_naive_timestamps_are_treated_as_utc_not_crashed_on():
    naive = datetime(2026, 5, 19)
    assert 0.0 < decay_factor("magnitude", naive, NOW) <= 1.0


def test_bands_never_expose_decimals():
    assert band_for(0.80) == "high"
    assert band_for(0.75) == "high"
    assert band_for(0.74) == "medium"
    assert band_for(0.50) == "medium"
    assert band_for(0.49) == "low"


def test_all_ten_invariants_are_named():
    """The registry is what error messages and the UI quote; a missing entry
    means an invariant nobody can cite."""
    assert sorted(INVARIANTS) == [
        "I1", "I10", "I2", "I3", "I4", "I5", "I6", "I7", "I8", "I9",
    ]


def test_population_filter_segments_are_frozen():
    pop = PopulationFilter(segments={"tier": ("smb",)})
    with pytest.raises(TypeError):
        pop.segments["tier"] = ()                             # type: ignore[index]


# ══ Regression: the eight evasions the PR-1226 review demonstrated ═══════════
# Each of these passed an earlier version of a harness while reading
# corroboration or rewriting a frozen score. They are the reason the harnesses
# derive their mutation sets from the dataclass instead of a hand-written list.

def _scaled(finding: Finding, factor: float) -> Impact:
    base = compliant_score_impact(finding)
    return dataclasses.replace(
        base, value=None if base.value is None else base.value * factor
    )


def test_i1_catches_a_scorer_keyed_on_the_adjudication_verdict():
    """The sharpest evasion: `adjudication` IS the corroboration verdict, and
    `single_authoritative` is the quiet-finding guard. A scorer keyed on it
    re-buries precisely what I1 protects — and it lives on `Finding`, which the
    old confidence-side-only mutation list never touched."""
    def by_verdict(finding: Finding) -> Impact:
        return _scaled(finding, 1.5 if finding.adjudication == "corroborated" else 1.0)

    with pytest.raises(InvariantViolation) as exc:
        assert_impact_ignores_corroboration(by_verdict, a_finding())
    assert exc.value.invariant == "I1"


@pytest.mark.parametrize("attr,reader", [
    ("claim_ids", lambda f: len(f.claim_ids)),
    ("cell_refs", lambda f: len(f.cell_refs)),
])
def test_i1_catches_scorers_keyed_on_finding_level_counts(attr, reader):
    """Supporting-claim count is corroboration wearing a different name, and it
    sits on `Finding` rather than in `CORROBORATION_FIELDS`."""
    def by_count(finding: Finding) -> Impact:
        return _scaled(finding, 1.0 + 0.1 * reader(finding))

    with pytest.raises(InvariantViolation):
        assert_impact_ignores_corroboration(by_count, a_finding())


@pytest.mark.parametrize("reader", [
    lambda f: len(f.confidence_inputs.strengths),
    lambda f: len(f.confidence_inputs.observed_ats),
    lambda f: len(f.confidence_inputs.claim_types),
    lambda f: (f.confidence_inputs.coverage or 0.0),
    lambda f: len(f.confidence_inputs.sample_adequate),
])
def test_i1_catches_proxies_for_the_fields_the_old_list_mutated(reader):
    """`len(strengths)` is an exact proxy for `claim_count`. Mutating the
    counter but not the lists that co-vary with it was the gap."""
    def by_proxy(finding: Finding) -> Impact:
        return _scaled(finding, 1.0 + 0.1 * reader(finding))

    with pytest.raises(InvariantViolation):
        assert_impact_ignores_corroboration(by_proxy, a_finding())


def test_i1_a_memoised_scorer_cannot_hide_behind_the_cache():
    """`dataclasses.replace` preserves `id`, so a scorer cached on finding
    identity returned the baseline for every mutation and the harness passed.
    Each probe now carries a fresh id, which forces recomputation."""
    cache: dict[str, Impact] = {}

    def memoised(finding: Finding) -> Impact:
        if finding.id not in cache:
            cache[finding.id] = violating_score_impact(finding)
        return cache[finding.id]

    with pytest.raises(InvariantViolation):
        assert_impact_ignores_corroboration(memoised, a_finding())


def test_i1_refuses_a_degenerate_unsizeable_probe():
    """An unsizeable probe makes the harness report compliance having tested
    nothing — every mutation returns None and the reprs match. Corpus-only runs
    produce exactly that finding, so this has to fail loudly."""
    unsizeable = a_finding(impact_inputs=ImpactInputs(
        currency="accounts", affected_population=None,
        movable_gap=None, value_per_unit=None,
    ))
    # The PR's own violating scorer would otherwise sail through.
    with pytest.raises(ValueError, match="sizeable"):
        assert_impact_ignores_corroboration(violating_score_impact, unsizeable)


def test_i10_catches_a_functional_prioritiser_that_rewrites_scores():
    """The ordinary functional style, and the exact route the harness docstring
    claimed to close while discarding `step`'s return value."""
    scored = [(an_impact(), a_confidence())]

    def rewrites_functionally(items):
        return sorted(
            ((dataclasses.replace(i, value=1.0), c) for i, c in items),
            key=lambda pair: -(pair[0].value or 0),
        )

    with pytest.raises(InvariantViolation) as exc:
        assert_scores_frozen_across(rewrites_functionally, scored)
    assert exc.value.invariant == "I10"


def test_i10_is_not_vacuous_when_the_scored_set_is_a_tuple():
    """`Sequence`, and everything else in this package is tuples. In-place
    assignment is impossible against a tuple, so the in-place check alone made
    the harness fully vacuous."""
    scored = ((an_impact(), a_confidence()),)

    def rewrites(items):
        return tuple((dataclasses.replace(i, value=1.0), c) for i, c in items)

    with pytest.raises(InvariantViolation):
        assert_scores_frozen_across(rewrites, scored)


def test_i10_permits_reordering_which_is_the_whole_job():
    """Prioritisation is allowed to reorder. Comparing as a multiset is what
    keeps the guard from banning the one thing the stage exists to do."""
    scored = [
        (dataclasses.replace(an_impact(), value=10.0), a_confidence()),
        (dataclasses.replace(an_impact(), value=90.0), a_confidence()),
    ]

    def reorder(items):
        return sorted(items, key=lambda pair: -(pair[0].value or 0))

    assert_scores_frozen_across(reorder, scored)


def test_i10_tolerates_a_step_that_returns_something_else():
    """A step returning an ordering of ids has no scores to compare."""
    scored = [(an_impact(), a_confidence())]
    assert_scores_frozen_across(lambda items: ["a", "b"], scored)


# ══ Regression: frozen types stay usable ═════════════════════════════════════

def test_scored_types_are_hashable_deepcopyable_and_picklable():
    """`MappingProxyType` is what makes these genuinely immutable, and it cost
    three capabilities people assume a frozen dataclass has. PR6 dedups with
    `set(...)`; PR9 may deepcopy scored state. Both would have raised a
    `TypeError` naming `dict`, pointing nowhere near the cause."""
    import copy
    import pickle

    impact = Impact(value=1.0, currency="accounts", affected_population=4,
                    movable_gap=0.16, value_per_unit=None,
                    native_units={"tickets": 2.0})
    conf = a_confidence()

    assert len({impact, dataclasses.replace(impact)}) == 1
    assert len({conf, dataclasses.replace(conf)}) == 1
    assert copy.deepcopy(impact) == impact
    assert pickle.loads(pickle.dumps(impact)) == impact
    # And the freeze survives the round trip — otherwise this "fix" would have
    # traded immutability for convenience.
    revived = pickle.loads(pickle.dumps(impact))
    with pytest.raises(TypeError):
        revived.native_units["tickets"] = 0.0        # type: ignore[index]


def test_render_measure_does_not_print_a_measured_fraction_as_zero():
    """I3 runs in one direction, and the first version broke it in the other:
    `movable_gap=0.16` rendered as "0", which reads as no opportunity."""
    assert render_measure(0.16) == "0.16"
    assert render_measure(0.4) == "0.4"
    assert render_measure(1234.0) == "1,234"
    assert render_measure(0.0) == "0"
    assert render_measure(None) == "not measured"


def test_decay_raises_on_an_unknown_claim_type():
    """A typo previously aged at the `magnitude` rate and returned a plausible
    0.4958 instead of an error."""
    with pytest.raises(ValueError, match="Unknown claim type"):
        decay_factor("magnitud", NOW - timedelta(days=90), NOW)   # type: ignore[arg-type]


def test_invariants_registry_is_read_only():
    """It is the text quoted in error messages and the UI."""
    with pytest.raises(TypeError):
        INVARIANTS["I3"] = "anything at all"          # type: ignore[index]


# ══ Round 2: the evasions the PR-1229 re-review demonstrated ═════════════════

@dataclasses.dataclass(frozen=True)
class _Ranked:
    """The shape a prioritisation stage naturally returns: the pair plus a rank."""
    impact: Impact
    confidence: Confidence
    rank: int


@pytest.mark.parametrize("shape", ["triples", "dataclass", "dict"])
def test_i10_catches_a_rewrite_in_every_natural_return_shape(shape):
    """The old harness unpacked `for i, c in returned` inside a try/except and
    returned quietly on TypeError/ValueError. That escape hatch made I10
    vacuous for the three shapes a ranking stage is MOST likely to return —
    each verified to slip a rewritten score straight through."""
    scored = [(an_impact(), a_confidence())]

    def rewrites(items):
        pairs = [(dataclasses.replace(i, value=1.0), c) for i, c in items]
        if shape == "triples":
            return [(i, c, n) for n, (i, c) in enumerate(pairs)]
        if shape == "dataclass":
            return [_Ranked(i, c, n) for n, (i, c) in enumerate(pairs)]
        return {i: c for i, c in pairs}

    with pytest.raises(InvariantViolation) as exc:
        assert_scores_frozen_across(rewrites, scored)
    assert exc.value.invariant == "I10"


@pytest.mark.parametrize("shape", ["triples", "dataclass", "dict"])
def test_i10_permits_those_same_shapes_when_scores_are_untouched(shape):
    """The fix must not ban the shapes, only the rewriting."""
    scored = [(an_impact(), a_confidence())]

    def orders(items):
        if shape == "triples":
            return [(i, c, n) for n, (i, c) in enumerate(items)]
        if shape == "dataclass":
            return [_Ranked(i, c, n) for n, (i, c) in enumerate(items)]
        return {i: c for i, c in items}

    assert_scores_frozen_across(orders, scored)


def test_i10_says_shortlist_rather_than_accusing_a_mutation():
    """A top-N step altered nothing; blaming it for score mutation sends the
    reader hunting for a bug that does not exist."""
    scored = [(an_impact(), a_confidence()),
              (dataclasses.replace(an_impact(), value=5.0), a_confidence())]

    with pytest.raises(InvariantViolation) as exc:
        assert_scores_frozen_across(lambda items: list(items)[:1], scored)
    assert "does not add or remove" in str(exc.value)


def test_i1_catches_a_scorer_keyed_on_the_statement_text():
    """An out-of-band index keyed on statement text reads corroboration without
    touching a corroboration field."""
    index = {"Default budget sits below revealed preference.": 4}

    def by_index(finding: Finding) -> Impact:
        return _scaled(finding, 1.0 + 0.1 * index.get(finding.statement, 0))

    with pytest.raises(InvariantViolation):
        assert_impact_ignores_corroboration(by_index, a_finding())


def test_i1_degenerate_probe_has_its_own_exception_type():
    """A scorer that crashes on an empty tuple also raises ValueError. Without
    a distinct type a PR7 author cannot tell "your probe is unsizeable" from
    "your scorer broke"."""
    from app.crucible.invariants import DegenerateProbe

    unsizeable = a_finding(impact_inputs=ImpactInputs(
        currency="accounts", affected_population=None,
        movable_gap=None, value_per_unit=None))
    with pytest.raises(DegenerateProbe):
        assert_impact_ignores_corroboration(compliant_score_impact, unsizeable)


def test_i1_refuses_a_confidence_field_it_cannot_vary():
    """`_mutation_values` returned [] for set/str/list/dict, so a corroboration
    field of one of those types was silently never mutated while the docstring
    claimed every field is swept. CORROBORATION_FIELDS already names
    set-shaped members, so this is the likely next addition."""
    from app.crucible.invariants import DegenerateProbe, _mutation_values

    # The types that used to be skipped now produce mutations...
    for value in ("text", {"a"}, frozenset({"a"}), ["a"], {"a": 1}):
        assert _mutation_values(value), f"no mutation for {type(value).__name__}"

    # ...and anything still unvariable fails loudly rather than silently.
    @dataclasses.dataclass(frozen=True)
    class Exotic(ConfidenceInputs):
        agreeing: object = object()

    probe = a_finding()
    exotic = Exotic(**dataclasses.asdict(probe.confidence_inputs))
    with pytest.raises(DegenerateProbe, match="cannot vary"):
        assert_impact_ignores_corroboration(
            compliant_score_impact,
            dataclasses.replace(probe, confidence_inputs=exotic),
        )


def test_frozen_types_survive_dataclasses_asdict():
    """`asdict` walks fields and deep-copies each VALUE directly, so it hit the
    bare mappingproxy even though __reduce_ex__ handled the object. It is the
    traversal PR2's persistence and PR9's serialiser reach for first."""
    impact = Impact(value=1.0, currency="accounts", affected_population=4,
                    movable_gap=0.16, value_per_unit=None,
                    native_units={"tickets": 2.0})
    assert dataclasses.asdict(impact)["native_units"] == {"tickets": 2.0}
    assert dataclasses.asdict(a_confidence())["components"] == {"strongest": 0.9}
    assert dataclasses.asdict(a_finding())["impact_inputs"]["currency"] == "arr_dollars"


def test_claim_is_hashable_with_a_real_raw_payload():
    """`raw` holds the original payload on every real claim, and every fixture
    leaves it None — so a contents-hash looks fine in the suite and raises on
    the first real run, right where PR6 dedups with set()."""
    claim = a_claim("zendesk", "preference")
    real = dataclasses.replace(claim, raw={"ticket": 42, "tags": ["a", "b"]})

    # Hashable at all — this is what used to raise.
    assert isinstance(hash(real), int)
    # Equal objects hash equal, which is the contract that actually matters.
    assert len({real, dataclasses.replace(real)}) == 1
    # Same id, different contents: hashes collide (hash is on id) but `__eq__`
    # compares every field, so they are correctly two distinct objects. A set
    # dedups identical claims, not merely same-id ones — worth pinning, because
    # PR6 will use `set(...)` and needs to know which of the two it gets.
    assert claim != real
    assert len({claim, real}) == 2


def test_render_measure_degrades_on_non_finite_rather_than_crashing():
    """int(nan) raises ValueError and int(inf) OverflowError, at a render
    boundary, from the module's own sanctioned formatter."""
    assert render_measure(float("nan")) == "not measured"
    assert render_measure(float("inf")) == "not measured"
    assert render_measure(float("-inf")) == "not measured"


def test_i9_a_definition_with_unresolved_conflicts_cannot_lock():
    """Replaces a vacuous test whose fixture contained no conflicts at all, so
    it asserted the default and proved nothing. `DefinitionConflict` was
    constructed nowhere in the suite."""
    from app.crucible.types import DefinitionConflict

    conflicts = (
        DefinitionConflict(metric_name="revenue", source_a="finance",
                           definition_a="recognised", source_b="crm",
                           definition_b="booked"),
        DefinitionConflict(metric_name="revenue", source_a="finance",
                           definition_a="net", source_b="board deck",
                           definition_b="gross"),
    )
    candidate = a_definition(status="candidate", conflicts_found=conflicts)
    assert len(candidate.conflicts_found) == 2

    # Surfaced, never silently resolved: it cannot enter Stage 1.
    with pytest.raises(GoalNotLockedError):
        assert_goal_locked(candidate)

    # And a human confirming one of them is what locks it — not the engine
    # picking the more recently updated source.
    resolved = a_definition(status="locked", origin="adopted",
                            confirmed_by_user_at=NOW, confirmed_by_user_id="u-1",
                            conflicts_found=conflicts)
    assert_goal_locked(resolved)


def test_frozendict_refuses_the_in_place_merge_operator():
    """`|=` is a mutator, and missing it TRADED AWAY a property the code this
    replaced had: `MappingProxyType` refuses `|=` outright, while a dict
    subclass inherits `dict.__ior__` and updates in place.

    `impact.native_units |= {...}` is safe by accident — the frozen dataclass's
    `__setattr__` catches the rebind — which is exactly what makes the aliased
    form easy to miss:

        d = impact.native_units
        d |= {"normalized": v}      # wrote through into a frozen score

    That is I10's harm arriving through the class built to prevent it.
    """
    impact = Impact(value=1.0, currency="accounts", affected_population=4,
                    movable_gap=0.16, value_per_unit=None,
                    native_units={"tickets": 2.0})
    aliased = impact.native_units
    with pytest.raises(TypeError):
        aliased |= {"HACKED": 1.0}                    # type: ignore[misc]
    assert dict(impact.native_units) == {"tickets": 2.0}

    # `|` is deliberately still allowed: it returns a new plain dict and
    # mutates nothing.
    merged = impact.native_units | {"extra": 1.0}
    assert merged == {"tickets": 2.0, "extra": 1.0}
    assert dict(impact.native_units) == {"tickets": 2.0}


@pytest.mark.parametrize("mutate", [
    lambda d: d.update({"x": 1.0}),
    lambda d: d.setdefault("x", 1.0),
    lambda d: d.pop("tickets"),
    lambda d: d.popitem(),
    lambda d: d.clear(),
    lambda d: d.__setitem__("x", 1.0),
    lambda d: d.__delitem__("tickets"),
    lambda d: d.__ior__({"x": 1.0}),
])
def test_every_dict_mutator_is_refused(mutate):
    """Enumerated rather than sampled — `__ior__` was missed exactly because
    the first version checked the ones that came to mind."""
    impact = Impact(value=1.0, currency="accounts", affected_population=4,
                    movable_gap=0.16, value_per_unit=None,
                    native_units={"tickets": 2.0})
    with pytest.raises(TypeError):
        mutate(impact.native_units)
    assert dict(impact.native_units) == {"tickets": 2.0}

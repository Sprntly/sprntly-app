"""Crucible data model — the guarantees the types themselves make.

The invariant tests (`test_crucible_invariants.py`) prove the properties; this
file pins the vocabulary and the constructor-level refusals those properties
stand on. Split out because a vocabulary change (a new claim type, a reordered
strength ladder) should fail HERE, next to the table it broke, rather than
somewhere downstream that happens to read it.

No network, no DB, no LLM.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.crucible.types import (
    CLAIM_TYPES,
    DECAY_HALFLIFE_DAYS,
    EVIDENCE_STRENGTHS,
    GOAL_CURRENCIES,
    STRENGTH_SCORE,
    Claim,
    ConfidenceInputs,
    Impact,
    ImpactInputs,
)

NOW = datetime(2026, 8, 17, tzinfo=timezone.utc)


def a_claim(**overrides) -> Claim:
    base = dict(
        id="c-1", assertion="41% accept the pre-filled default.", type="magnitude",
        subject="budget_field", source_id="amplitude", artifact_id="q-88",
        artifact_type="query", strength="measured", observed_at=NOW,
        authoritative=True,
    )
    base.update(overrides)
    return Claim(**base)


# ── Vocabulary ───────────────────────────────────────────────────────────────

def test_execution_claim_types_exist():
    """SPEC §4.5. `existence` and `attempt` are what let the engine tell "we
    never built it" from "we built it and nobody found it" — opposite
    recommendations that every other claim type conflates."""
    assert {"existence", "attempt"} <= CLAIM_TYPES


def test_strength_ladder_is_ordered():
    """The ladder is the whole point: an experiment outranks a measurement,
    which outranks a correlation, which outranks somebody saying so."""
    ladder = ["causally_tested", "measured", "correlated", "inferred", "reported"]
    assert set(ladder) == EVIDENCE_STRENGTHS
    scores = [STRENGTH_SCORE[s] for s in ladder]
    assert scores == sorted(scores, reverse=True)


def test_reported_is_the_floor_and_is_low():
    """Most of a corpus is `reported`. If that scored anywhere near `measured`,
    the strength term would stop discriminating at all."""
    assert STRENGTH_SCORE["reported"] == min(STRENGTH_SCORE.values())
    assert STRENGTH_SCORE["reported"] < STRENGTH_SCORE["measured"] / 3


def test_accounts_is_a_currency_and_is_documented_as_reach():
    """Corpus-only tenants have no revenue to size in. `accounts` is the
    fallback and it is a REACH measure standing in for a value measure — the
    docstring in types.py is the contract, this test is the reminder that it
    exists."""
    assert "accounts" in GOAL_CURRENCIES
    assert "arr_dollars" in GOAL_CURRENCIES


def test_every_claim_type_has_a_half_life():
    """A claim type with no decay entry would silently fall back to a default
    and age at the wrong rate."""
    assert set(DECAY_HALFLIFE_DAYS) == CLAIM_TYPES


def test_volatile_facts_decay_faster_than_structural_ones():
    assert DECAY_HALFLIFE_DAYS["direction"] < DECAY_HALFLIFE_DAYS["magnitude"]
    assert DECAY_HALFLIFE_DAYS["magnitude"] < DECAY_HALFLIFE_DAYS["mechanism"]


# ── Constructor refusals ─────────────────────────────────────────────────────

def test_claim_rejects_an_unknown_type():
    with pytest.raises(ValueError):
        a_claim(type="vibe")


def test_claim_rejects_an_unknown_strength():
    """An unrecognised strength must not slip through and be scored by a
    `.get(..., default)` somewhere downstream."""
    with pytest.raises(ValueError):
        a_claim(strength="pretty_sure")


def test_claim_exposes_its_strength_score():
    assert a_claim(strength="correlated").strength_score == 0.60


def test_claim_retains_its_raw_payload():
    """SPEC acceptance criterion 6: every claim retains `raw`. Without it a
    disputed finding cannot be traced back to what the document actually said."""
    assert a_claim(raw={"row": 12}).raw == {"row": 12}


# ── Unmeasured values survive construction ───────────────────────────────────

def test_impact_inputs_accept_unmeasured_values():
    """A corpus that never measured the gap yields None, and it has to survive
    to the output rather than being rejected or defaulted at the door."""
    inputs = ImpactInputs(currency="accounts", affected_population=4,
                          movable_gap=None, value_per_unit=None)
    assert inputs.movable_gap is None


def test_impact_carries_an_unsizeable_value_as_none():
    impact = Impact(value=None, currency="accounts", affected_population=None,
                    movable_gap=None, value_per_unit=None)
    assert impact.value is None


def test_native_units_map_is_frozen():
    inputs = ImpactInputs(currency="accounts", affected_population=1,
                          movable_gap=None, value_per_unit=None,
                          native_units={"tickets": 40.0})
    with pytest.raises(TypeError):
        inputs.native_units["tickets"] = 0.0            # type: ignore[index]


def test_corpus_only_flag_defaults_off():
    """It must be set deliberately by the stage that discovered there is no
    outcome evidence — never assumed."""
    inputs = ConfidenceInputs(
        strengths=(), claim_types=(), observed_ats=(),
        authoritative_count=0, claim_count=0,
        independent_authoritative_source_types=0,
    )
    assert inputs.solution_evidence_absent is False

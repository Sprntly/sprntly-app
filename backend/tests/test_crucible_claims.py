"""Claim projection — the mapping tables, and the two rules that protect them.

The spec calls extraction the single point of failure and budgets 40% of the
build for it: everything downstream is computed on these claims, so a wrong
strength or a wrong population is precise nonsense with clean provenance, and
nothing later can detect it.

The Phase 0 spike changed the shape of that risk here. A `kg_signal` row is
already an extracted assertion, so this module is four deterministic table
lookups rather than an LLM call — no prompt to drift. What still needs testing
is whether the TABLES are right, and specifically the two rules that stop a
claim reaching the substrate stronger than its source can support:

  * a source never votes outside its authority (I4), and the self-selection
    rule that keeps a ticket from sizing a population;
  * a non-authoritative claim is capped at `reported` even when its source's
    ceiling is higher — otherwise a Jira ticket speculating about users emits a
    MEASURED preference claim, wearing the strength of a structured field.

No network, no DB, no LLM.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.crucible.claims import (
    AUTHORITATIVE_FOR,
    DEFAULT_CLAIM_TYPE,
    KIND_TO_CLAIM_TYPE,
    infer_account_sides,
    normalise_account,
    project_signal,
    project_signals,
)
from app.crucible.types import CLAIM_TYPES, STRENGTH_SCORE

NOW = "2026-08-01T12:00:00+00:00"


def sig(**kw):
    base = {
        "id": "s-1", "kind": "finding", "source_type": "communication",
        "content": "an assertion", "properties": {}, "valid_at": NOW,
        "source_id": "src-1",
    }
    base.update(kw)
    return base


# ── The vocabulary tables ────────────────────────────────────────────────────

def test_every_mapped_claim_type_is_a_real_one():
    assert set(KIND_TO_CLAIM_TYPE.values()) <= CLAIM_TYPES
    assert DEFAULT_CLAIM_TYPE in CLAIM_TYPES


def test_an_unknown_kind_falls_to_the_weakest_consequence():
    """`mechanism`, not `magnitude`. A mechanism claim can never vote on size,
    so a mis-mapped signal cannot inflate a finding; defaulting the other way
    would let an unrecognised kind size one."""
    claim = project_signal(sig(kind="something_new"), {})
    assert claim is not None
    assert claim.type == "mechanism"
    assert "magnitude" != DEFAULT_CLAIM_TYPE


@pytest.mark.parametrize("source_type", list(AUTHORITATIVE_FOR))
def test_no_source_claims_authority_over_an_unknown_claim_type(source_type):
    assert AUTHORITATIVE_FOR[source_type] <= CLAIM_TYPES


# ── I4: the self-selection rule ──────────────────────────────────────────────

@pytest.mark.parametrize("source_type", ["customer_voice", "communication"])
def test_a_self_selected_source_can_never_size_a_population(source_type):
    """Tickets, reviews and sales calls describe people who CHOSE to speak.
    Letting one vote on magnitude is how a system ends up telling a company its
    loudest problem is its biggest one."""
    assert "magnitude" not in AUTHORITATIVE_FOR[source_type]
    claim = project_signal(sig(source_type=source_type, kind="metric_anomaly"), {})
    assert claim is not None
    assert claim.type == "magnitude"
    assert claim.authoritative is False


def test_an_execution_source_is_authoritative_about_us_not_about_users():
    """SPEC §4.5. A Jira ticket saying "users churn because export is slow" is
    one engineer's framing typed into a text field."""
    assert "preference" not in AUTHORITATIVE_FOR["project_mgmt"]
    assert "magnitude" not in AUTHORITATIVE_FOR["project_mgmt"]
    assert {"attempt", "existence", "constraint"} <= AUTHORITATIVE_FOR["project_mgmt"]


@pytest.mark.parametrize("source_type", ["verbal_claim", "agent_inferred"])
def test_unverified_sources_vote_on_nothing(source_type):
    assert AUTHORITATIVE_FOR[source_type] == frozenset()


def test_analytics_may_size_things_because_it_is_not_self_selected():
    claim = project_signal(sig(source_type="analytics", kind="metric_anomaly"), {})
    assert claim is not None
    assert claim.authoritative is True
    assert claim.strength == "measured"


# ── The strength ceiling ─────────────────────────────────────────────────────

def test_a_non_authoritative_claim_is_capped_at_reported():
    """THE RULE THAT MATTERS. `project_mgmt`'s ceiling is `measured`, because
    ticket status and transitions are structured fields. But a ticket BODY
    speculating about why users churn is a preference claim it may not vote on
    — and without this cap it would enter the substrate as MEASURED, carrying
    the authority of a structured field into a guess about users."""
    claim = project_signal(sig(source_type="project_mgmt", kind="feature_request"), {})
    assert claim is not None
    assert claim.type == "preference"
    assert claim.authoritative is False
    assert claim.strength == "reported"


def test_the_ceiling_does_not_downgrade_an_authoritative_claim():
    claim = project_signal(sig(source_type="project_mgmt", kind="bug"), {})
    assert claim is not None
    assert claim.type == "existence"
    assert claim.authoritative is True
    assert claim.strength == "measured"


def test_nothing_projected_ever_reaches_causally_tested():
    """Only an experiment earns that, and no signal source is one. If a
    projection ever emits it, the causal lint stops protecting anything."""
    for kind in list(KIND_TO_CLAIM_TYPE) + ["unmapped"]:
        for source_type in AUTHORITATIVE_FOR:
            claim = project_signal(sig(kind=kind, source_type=source_type), {})
            assert claim is not None
            assert claim.strength != "causally_tested"
            assert STRENGTH_SCORE[claim.strength] <= STRENGTH_SCORE["measured"]


# ── Population ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "", "n/a", "unknown", "TBD", "customers", "all", "various", "  ", None, 42,
    "ab", "x" * 81,
])
def test_placeholders_are_not_account_names(value):
    assert normalise_account(value) is None


def test_an_account_side_is_decided_across_the_CORPUS_not_per_row():
    """The same account appears under `customer` on one signal and `prospect`
    on another as a deal progresses, so no single row can decide."""
    rows = [
        sig(id="a", properties={"prospect": "Northwind"}),
        sig(id="b", properties={"customer": "Northwind"}),
        sig(id="c", properties={"prospect": "Contoso"}),
    ]
    sides = infer_account_sides(rows)
    assert sides["Northwind"] == "customer"   # any customer sighting wins
    assert sides["Contoso"] == "prospect"


def test_a_signal_with_no_named_account_is_unsized_not_zero():
    """I3. "We did not record who this is about" is not "this affects nobody",
    and a finding built from these must render unsizeable rather than
    worthless."""
    claim = project_signal(sig(properties={"meeting_id": "m-1"}), {})
    assert claim is not None
    assert claim.population.estimated_size is None
    assert claim.population.segments == {}


def test_the_population_separates_the_customer_side():
    rows = [sig(properties={"customer": "Northwind", "prospect": "Contoso"})]
    sides = infer_account_sides(rows)
    claim = project_signal(rows[0], sides)
    assert claim is not None
    assert set(claim.population.segments["accounts"]) == {"Northwind", "Contoso"}
    assert claim.population.segments["customer_side"] == ("Northwind",)


# ── Corpus-level behaviour ───────────────────────────────────────────────────

def test_a_signal_with_no_timestamp_is_dropped_and_counted():
    """`observed_at` drives decay. Defaulting it to now() would make stale
    evidence look fresh — a silent, permanent overstatement — so the row is
    dropped and the drop is reported."""
    claims, stats = project_signals([sig(id="ok"), sig(id="bad", valid_at=None)])
    assert [c.id for c in claims] == ["ok"]
    assert stats["no_timestamp"] == 1
    assert stats["seen"] == 2


def test_retired_signals_are_skipped_and_counted():
    claims, stats = project_signals([
        sig(id="live"),
        sig(id="dead", properties={"retired": True}),
    ])
    assert [c.id for c in claims] == ["live"]
    assert stats["retired"] == 1


def test_the_drop_counts_are_what_a_coverage_note_is_built_from():
    """A run that silently discarded a third of its evidence looks exactly like
    one that read everything — the degradation the spec calls worse than an
    outright failure."""
    claims, stats = project_signals([sig(id=str(i)) for i in range(5)])
    assert stats == {"seen": 5, "projected": 5, "no_timestamp": 0, "retired": 0}
    assert len(claims) == stats["projected"]


def test_raw_is_retained_on_every_claim():
    """Spec acceptance criterion 6 — a disputed finding must trace back to what
    the source actually said."""
    claim = project_signal(sig(properties={"customer": "Northwind", "x": 1}), {})
    assert claim is not None
    assert claim.raw == {"customer": "Northwind", "x": 1}


def test_projection_is_deterministic():
    """No LLM, so the same corpus must project identically every time —
    otherwise the run is not reproducible and the whole differentiator goes."""
    rows = [sig(id=str(i), properties={"customer": f"Co {i}"}) for i in range(20)]
    first, _ = project_signals(rows)
    second, _ = project_signals(rows)
    assert [repr(c) for c in first] == [repr(c) for c in second]


def test_observed_at_is_always_timezone_aware():
    """A naive datetime silently compares wrong against an aware `now`, and
    decay would be off by the local offset."""
    claim = project_signal(sig(valid_at="2026-08-01T12:00:00"), {})
    assert claim is not None
    assert claim.observed_at.tzinfo is not None
    assert claim.observed_at == datetime(2026, 8, 1, 12, tzinfo=timezone.utc)

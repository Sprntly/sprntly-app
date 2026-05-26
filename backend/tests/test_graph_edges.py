"""Tests for app.graph.edges helpers + app.graph.provenance walks.

Covers:
  1. Each make_*_edge() helper produces a valid Edge with the correct
     (source_type, target_type) pair.
  2. Mis-routed entity IDs (e.g. Decision in the source slot of a
     SUPPORTS edge) trigger the EDGE_DIRECTION_TABLE guard.
  3. Round-trip: write each edge via facade.write_edge, read back via
     edges_from / edges_to, metadata preserved.
  4. trace_provenance walks Signal → Hypothesis → Decision correctly.
  5. trace_outcome_provenance extends through Artifact + Outcome.
  6. Tenant isolation: trace_provenance on a decision in workspace X
     cannot pull signals from workspace Y.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.graph import (
    EDGE_DIRECTION_TABLE,
    EDGE_METADATA_SCHEMA,
    Edge,
    EdgeType,
    ProvenanceChain,
    make_contradicts_edge,
    make_expressed_as_edge,
    make_informs_edge,
    make_motivated_edge,
    make_promoted_to_edge,
    make_resulted_in_edge,
    make_scoped_to_edge,
    make_supports_edge,
    make_updates_weight_edge,
    make_validates_edge,
    make_visualizes_edge,
)
from app.graph.backends.sqlite_backend import SqliteBackend
from app.graph.facade import GraphFacade


@pytest.fixture
def facade(tmp_path) -> GraphFacade:
    db_path = tmp_path / "graph.db"
    backend = SqliteBackend(db_path=str(db_path))
    backend.initialize_schema()
    return GraphFacade(backend)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────── 1. helpers return valid Edges ───────────────────────


def test_make_supports_edge_basic_shape():
    e = make_supports_edge(
        workspace_id="ws-1",
        signal_id="sig-1",
        hypothesis_id="hyp-1",
        source="synthesis_agent_run",
        confidence=0.8,
        relevance_score=0.9,
    )
    assert e.edge_type == EdgeType.SUPPORTS
    assert e.source_entity_id == "sig-1"
    assert e.source_entity_type == "Signal"
    assert e.target_entity_id == "hyp-1"
    assert e.target_entity_type == "Hypothesis"
    assert e.confidence == 0.8
    assert e.metadata["relevance_score"] == 0.9
    # Bitemporal-distinct invariant must hold by construction.
    assert e.valid_at != e.transaction_at
    assert e.transaction_at > e.valid_at


def test_make_contradicts_edge_basic_shape():
    e = make_contradicts_edge(
        workspace_id="ws-1",
        signal_id="sig-2",
        hypothesis_id="hyp-1",
        severity=0.6,
    )
    assert e.edge_type == EdgeType.CONTRADICTS
    assert e.source_entity_type == "Signal"
    assert e.target_entity_type == "Hypothesis"
    assert e.metadata["severity"] == 0.6


def test_make_promoted_to_edge_captures_approval_metadata():
    approved_at = _now() - timedelta(minutes=5)
    e = make_promoted_to_edge(
        workspace_id="ws-1",
        hypothesis_id="hyp-1",
        decision_id="dec-1",
        approved_by_user_id="user-99",
        approved_at=approved_at,
    )
    assert e.edge_type == EdgeType.PROMOTED_TO
    assert e.source_entity_type == "Hypothesis"
    assert e.target_entity_type == "Decision"
    # Approval metadata is canonical for this edge type.
    assert e.metadata["approved_by_user_id"] == "user-99"
    assert "approved_at" in e.metadata


def test_make_motivated_edge_records_artifact_kind():
    e = make_motivated_edge(
        workspace_id="ws-1",
        decision_id="dec-1",
        artifact_id="art-prd-1",
        artifact_kind="prd",
    )
    assert e.edge_type == EdgeType.MOTIVATED
    assert e.source_entity_type == "Decision"
    assert e.target_entity_type == "Artifact"
    assert e.metadata["artifact_kind"] == "prd"


def test_make_resulted_in_edge_captures_shipped_at():
    shipped = _now() - timedelta(days=2)
    e = make_resulted_in_edge(
        workspace_id="ws-1",
        artifact_id="art-prd-1",
        outcome_id="out-1",
        shipped_at=shipped,
        release_tag="v2.3.0",
    )
    assert e.edge_type == EdgeType.RESULTED_IN
    assert e.source_entity_type == "Artifact"
    assert e.target_entity_type == "Outcome"
    assert e.metadata["release_tag"] == "v2.3.0"
    assert "shipped_at" in e.metadata


def test_make_validates_edge_captures_prediction_fields():
    e = make_validates_edge(
        workspace_id="ws-1",
        outcome_id="out-1",
        hypothesis_id="hyp-1",
        actual_impact=3.2,
        prediction_hit=True,
        prediction_delta=0.7,
    )
    assert e.edge_type == EdgeType.VALIDATES
    assert e.source_entity_type == "Outcome"
    assert e.target_entity_type == "Hypothesis"
    assert e.metadata["actual_impact"] == 3.2
    assert e.metadata["prediction_hit"] is True
    assert e.metadata["prediction_delta"] == 0.7


def test_make_updates_weight_edge_captures_delta():
    e = make_updates_weight_edge(
        workspace_id="ws-1",
        outcome_id="out-1",
        signal_id="sig-1",
        delta_weight=0.05,
        new_signal_confidence=0.85,
        reason="prediction_hit",
    )
    assert e.edge_type == EdgeType.UPDATES_WEIGHT
    assert e.source_entity_type == "Outcome"
    assert e.target_entity_type == "Signal"
    assert e.metadata["delta_weight"] == 0.05
    assert e.metadata["reason"] == "prediction_hit"


def test_make_scoped_to_edge_wildcards_source_type():
    """SCOPED_TO allows any entity type in the source slot — verify the
    wildcard branch of validate_direction is exercised."""
    e = make_scoped_to_edge(
        workspace_id="ws-1",
        entity_id="sig-1",
        entity_kind="Signal",
    )
    assert e.edge_type == EdgeType.SCOPED_TO
    assert e.source_entity_type == "Signal"
    assert e.target_entity_type == "Workspace"
    assert e.target_entity_id == "ws-1"

    # Also works with Decision as source.
    e2 = make_scoped_to_edge(
        workspace_id="ws-1",
        entity_id="dec-1",
        entity_kind="Decision",
    )
    assert e2.source_entity_type == "Decision"


def test_make_informs_edge_captures_ingest_metadata():
    e = make_informs_edge(
        workspace_id="ws-1",
        signal_id="sig-1",
        ingest_run_id="run-42",
        source_tool="amplitude",
    )
    assert e.edge_type == EdgeType.INFORMS
    assert e.source_entity_type == "Signal"
    assert e.target_entity_type == "Workspace"
    assert e.metadata["ingest_run_id"] == "run-42"
    assert e.metadata["source_tool"] == "amplitude"


def test_make_expressed_as_edge_distinct_from_motivated():
    e = make_expressed_as_edge(
        workspace_id="ws-1",
        decision_id="dec-1",
        artifact_id="art-prd-1",
        artifact_kind="prd",
    )
    assert e.edge_type == EdgeType.EXPRESSED_AS
    assert e.source_entity_type == "Decision"
    assert e.target_entity_type == "Artifact"


def test_make_visualizes_edge_artifact_to_artifact():
    e = make_visualizes_edge(
        workspace_id="ws-1",
        artifact_id="art-proto-1",
        parent_artifact_id="art-prd-1",
        relationship="prototype_of",
        parent_version=1,
    )
    assert e.edge_type == EdgeType.VISUALIZES
    assert e.source_entity_type == "Artifact"
    assert e.target_entity_type == "Artifact"
    assert e.metadata["relationship"] == "prototype_of"
    assert e.metadata["parent_version"] == 1


def test_helpers_default_confidence_is_in_range():
    """Default confidence values across helpers must be within [0, 1]."""
    edges = [
        make_supports_edge(workspace_id="w", signal_id="s", hypothesis_id="h"),
        make_contradicts_edge(workspace_id="w", signal_id="s", hypothesis_id="h"),
        make_promoted_to_edge(
            workspace_id="w",
            hypothesis_id="h",
            decision_id="d",
            approved_by_user_id="u",
            approved_at=_now(),
        ),
        make_motivated_edge(
            workspace_id="w", decision_id="d", artifact_id="a", artifact_kind="prd"
        ),
        make_resulted_in_edge(
            workspace_id="w", artifact_id="a", outcome_id="o", shipped_at=_now()
        ),
        make_validates_edge(
            workspace_id="w",
            outcome_id="o",
            hypothesis_id="h",
            actual_impact=1.0,
            prediction_hit=True,
            prediction_delta=0.1,
        ),
        make_updates_weight_edge(
            workspace_id="w",
            outcome_id="o",
            signal_id="s",
            delta_weight=0.0,
            new_signal_confidence=0.5,
            reason="r",
        ),
        make_scoped_to_edge(workspace_id="w", entity_id="s", entity_kind="Signal"),
        make_informs_edge(
            workspace_id="w", signal_id="s", ingest_run_id="r", source_tool="t"
        ),
        make_expressed_as_edge(
            workspace_id="w", decision_id="d", artifact_id="a", artifact_kind="prd"
        ),
        make_visualizes_edge(
            workspace_id="w",
            artifact_id="a",
            parent_artifact_id="b",
            relationship="prototype_of",
        ),
    ]
    for e in edges:
        assert 0.0 <= e.confidence <= 1.0


def test_metadata_registry_covers_every_edge_type():
    """EDGE_METADATA_SCHEMA registry must have an entry for every EdgeType."""
    for edge_type in EdgeType:
        assert edge_type in EDGE_METADATA_SCHEMA, f"no schema for {edge_type}"


def test_direction_table_covers_every_edge_type():
    """EDGE_DIRECTION_TABLE must have an entry for every EdgeType."""
    for edge_type in EdgeType:
        assert edge_type in EDGE_DIRECTION_TABLE, f"no direction for {edge_type}"


# ─────────────────────── 2. direction-table guard ───────────────────────


def test_supports_rejects_wrong_source_type():
    """Constructing a SUPPORTS edge directly with the wrong source type
    must raise via validate_direction()."""
    bad = Edge(
        workspace_id="ws-1",
        valid_at=_now() - timedelta(seconds=1),
        transaction_at=_now(),
        edge_type=EdgeType.SUPPORTS,
        source_entity_id="dec-1",
        source_entity_type="Decision",
        target_entity_id="hyp-1",
        target_entity_type="Hypothesis",
        source="t",
        confidence=0.5,
    )
    with pytest.raises(ValueError, match="SUPPORTS"):
        bad.validate_direction()


def test_promoted_to_rejects_wrong_target_type():
    bad = Edge(
        workspace_id="ws-1",
        valid_at=_now() - timedelta(seconds=1),
        transaction_at=_now(),
        edge_type=EdgeType.PROMOTED_TO,
        source_entity_id="hyp-1",
        source_entity_type="Hypothesis",
        target_entity_id="art-1",
        target_entity_type="Artifact",  # should be Decision
        source="t",
        confidence=1.0,
    )
    with pytest.raises(ValueError, match="PROMOTED_TO"):
        bad.validate_direction()


def test_resulted_in_rejects_wrong_source_type():
    """RESULTED_IN must originate from Artifact, not Decision."""
    bad = Edge(
        workspace_id="ws-1",
        valid_at=_now() - timedelta(seconds=1),
        transaction_at=_now(),
        edge_type=EdgeType.RESULTED_IN,
        source_entity_id="dec-1",
        source_entity_type="Decision",  # should be Artifact
        target_entity_id="out-1",
        target_entity_type="Outcome",
        source="t",
        confidence=1.0,
    )
    with pytest.raises(ValueError, match="RESULTED_IN"):
        bad.validate_direction()


def test_validates_rejects_wrong_direction():
    """VALIDATES must go Outcome→Hypothesis, not the other way."""
    bad = Edge(
        workspace_id="ws-1",
        valid_at=_now() - timedelta(seconds=1),
        transaction_at=_now(),
        edge_type=EdgeType.VALIDATES,
        source_entity_id="hyp-1",
        source_entity_type="Hypothesis",
        target_entity_id="out-1",
        target_entity_type="Outcome",
        source="t",
        confidence=0.9,
    )
    with pytest.raises(ValueError, match="VALIDATES"):
        bad.validate_direction()


def test_updates_weight_rejects_wrong_direction():
    bad = Edge(
        workspace_id="ws-1",
        valid_at=_now() - timedelta(seconds=1),
        transaction_at=_now(),
        edge_type=EdgeType.UPDATES_WEIGHT,
        source_entity_id="sig-1",
        source_entity_type="Signal",  # source must be Outcome
        target_entity_id="out-1",
        target_entity_type="Outcome",
        source="t",
        confidence=0.9,
    )
    with pytest.raises(ValueError, match="UPDATES_WEIGHT"):
        bad.validate_direction()


def test_visualizes_rejects_non_artifact_source():
    bad = Edge(
        workspace_id="ws-1",
        valid_at=_now() - timedelta(seconds=1),
        transaction_at=_now(),
        edge_type=EdgeType.VISUALIZES,
        source_entity_id="dec-1",
        source_entity_type="Decision",  # must be Artifact
        target_entity_id="art-1",
        target_entity_type="Artifact",
        source="t",
        confidence=1.0,
    )
    with pytest.raises(ValueError, match="VISUALIZES"):
        bad.validate_direction()


def test_informs_target_must_be_workspace():
    bad = Edge(
        workspace_id="ws-1",
        valid_at=_now() - timedelta(seconds=1),
        transaction_at=_now(),
        edge_type=EdgeType.INFORMS,
        source_entity_id="sig-1",
        source_entity_type="Signal",
        target_entity_id="hyp-1",
        target_entity_type="Hypothesis",  # must be Workspace
        source="t",
        confidence=1.0,
    )
    with pytest.raises(ValueError, match="INFORMS"):
        bad.validate_direction()


# ─────────────────────── 3. round-trip through the facade ───────────────────────


def test_supports_edge_round_trip(facade):
    e = make_supports_edge(
        workspace_id="ws-1",
        signal_id="sig-1",
        hypothesis_id="hyp-1",
        relevance_score=0.9,
        contribution_weight=0.3,
    )
    facade.write_edge("ws-1", e)
    fetched = facade.edges_from("ws-1", "sig-1", edge_type=EdgeType.SUPPORTS)
    assert len(fetched) == 1
    assert fetched[0].target_entity_id == "hyp-1"
    # Metadata preserved through JSON round-trip.
    assert fetched[0].metadata["relevance_score"] == 0.9
    assert fetched[0].metadata["contribution_weight"] == 0.3


def test_validates_edge_round_trip(facade):
    e = make_validates_edge(
        workspace_id="ws-1",
        outcome_id="out-1",
        hypothesis_id="hyp-1",
        actual_impact=2.5,
        prediction_hit=False,
        prediction_delta=-1.2,
    )
    facade.write_edge("ws-1", e)
    fetched = facade.edges_to("ws-1", "hyp-1", edge_type=EdgeType.VALIDATES)
    assert len(fetched) == 1
    assert fetched[0].metadata["actual_impact"] == 2.5
    assert fetched[0].metadata["prediction_hit"] is False
    assert fetched[0].metadata["prediction_delta"] == -1.2


def test_updates_weight_edge_round_trip(facade):
    e = make_updates_weight_edge(
        workspace_id="ws-1",
        outcome_id="out-1",
        signal_id="sig-1",
        delta_weight=-0.1,
        new_signal_confidence=0.6,
        reason="prediction_miss",
    )
    facade.write_edge("ws-1", e)
    fetched = facade.edges_from("ws-1", "out-1", edge_type=EdgeType.UPDATES_WEIGHT)
    assert len(fetched) == 1
    assert fetched[0].metadata["delta_weight"] == -0.1
    assert fetched[0].metadata["reason"] == "prediction_miss"


def test_resulted_in_edge_round_trip(facade):
    shipped = _now() - timedelta(days=3)
    e = make_resulted_in_edge(
        workspace_id="ws-1",
        artifact_id="art-1",
        outcome_id="out-1",
        shipped_at=shipped,
    )
    facade.write_edge("ws-1", e)
    fetched = facade.edges_from("ws-1", "art-1", edge_type=EdgeType.RESULTED_IN)
    assert len(fetched) == 1
    assert "shipped_at" in fetched[0].metadata


def test_promoted_to_edge_round_trip(facade):
    e = make_promoted_to_edge(
        workspace_id="ws-1",
        hypothesis_id="hyp-1",
        decision_id="dec-1",
        approved_by_user_id="user-99",
        approved_at=_now() - timedelta(minutes=2),
    )
    facade.write_edge("ws-1", e)
    fetched = facade.edges_to("ws-1", "dec-1", edge_type=EdgeType.PROMOTED_TO)
    assert len(fetched) == 1
    assert fetched[0].metadata["approved_by_user_id"] == "user-99"


# ─────────────────────── 4. trace_provenance walk ───────────────────────


def _wire_decision_chain(
    facade: GraphFacade,
    *,
    workspace_id: str,
    signal_ids: list[str],
    hypothesis_id: str,
    decision_id: str,
) -> None:
    """Wire SUPPORTS (each signal → hypothesis) + PROMOTED_TO
    (hypothesis → decision) edges into the facade. We deliberately
    do NOT write the underlying entities here — trace_provenance only
    needs the edges to walk."""
    for sid in signal_ids:
        facade.write_edge(
            workspace_id,
            make_supports_edge(
                workspace_id=workspace_id,
                signal_id=sid,
                hypothesis_id=hypothesis_id,
            ),
        )
    facade.write_edge(
        workspace_id,
        make_promoted_to_edge(
            workspace_id=workspace_id,
            hypothesis_id=hypothesis_id,
            decision_id=decision_id,
            approved_by_user_id="user-99",
            approved_at=_now(),
        ),
    )


def test_trace_provenance_walks_signal_to_decision(facade):
    _wire_decision_chain(
        facade,
        workspace_id="ws-1",
        signal_ids=["sig-1", "sig-2", "sig-3"],
        hypothesis_id="hyp-1",
        decision_id="dec-1",
    )
    chain = facade.trace_provenance("ws-1", "dec-1")
    assert isinstance(chain, ProvenanceChain)
    assert chain.decision_id == "dec-1"
    assert chain.hypothesis_id == "hyp-1"
    assert set(chain.signal_ids) == {"sig-1", "sig-2", "sig-3"}
    # walk_steps should contain 1 PROMOTED_TO + 3 SUPPORTS = 4 steps.
    assert len(chain.walk_steps) == 4
    edge_types = [s.edge_type for s in chain.walk_steps]
    assert edge_types.count("PROMOTED_TO") == 1
    assert edge_types.count("SUPPORTS") == 3


def test_trace_provenance_empty_when_decision_has_no_promoted_edge(facade):
    """If no PROMOTED_TO edge points at the decision, the chain is empty
    (no hypothesis_id, no signal_ids)."""
    chain = facade.trace_provenance("ws-1", "dec-orphan")
    assert chain.decision_id == "dec-orphan"
    assert chain.hypothesis_id is None
    assert chain.signal_ids == []
    assert chain.walk_steps == []


def test_trace_provenance_handles_hypothesis_with_no_signals(facade):
    """A PROMOTED_TO exists but no SUPPORTS — we still return the
    hypothesis_id but signal_ids is empty."""
    facade.write_edge(
        "ws-1",
        make_promoted_to_edge(
            workspace_id="ws-1",
            hypothesis_id="hyp-1",
            decision_id="dec-1",
            approved_by_user_id="user-99",
            approved_at=_now(),
        ),
    )
    chain = facade.trace_provenance("ws-1", "dec-1")
    assert chain.hypothesis_id == "hyp-1"
    assert chain.signal_ids == []


def test_trace_provenance_dedupes_repeated_signals(facade):
    """If the same Signal supports a Hypothesis twice (e.g. ingested
    again after a sweep), trace_provenance returns it once."""
    facade.write_edge(
        "ws-1",
        make_supports_edge(
            workspace_id="ws-1",
            signal_id="sig-1",
            hypothesis_id="hyp-1",
        ),
    )
    facade.write_edge(
        "ws-1",
        make_supports_edge(
            workspace_id="ws-1",
            signal_id="sig-1",
            hypothesis_id="hyp-1",
            relevance_score=0.5,
        ),
    )
    facade.write_edge(
        "ws-1",
        make_promoted_to_edge(
            workspace_id="ws-1",
            hypothesis_id="hyp-1",
            decision_id="dec-1",
            approved_by_user_id="u",
            approved_at=_now(),
        ),
    )
    chain = facade.trace_provenance("ws-1", "dec-1")
    assert chain.signal_ids == ["sig-1"]


# ─────────────────────── 5. trace_outcome_provenance walk ───────────────────────


def test_trace_outcome_provenance_full_chain(facade):
    """End-to-end: Signal → Hypothesis → Decision → Artifact → Outcome.
    Walking back from the Outcome must surface every link."""
    workspace_id = "ws-1"

    # 1. SUPPORTS (3 signals support the hypothesis)
    for sid in ("sig-1", "sig-2", "sig-3"):
        facade.write_edge(
            workspace_id,
            make_supports_edge(
                workspace_id=workspace_id,
                signal_id=sid,
                hypothesis_id="hyp-1",
            ),
        )
    # 2. PROMOTED_TO
    facade.write_edge(
        workspace_id,
        make_promoted_to_edge(
            workspace_id=workspace_id,
            hypothesis_id="hyp-1",
            decision_id="dec-1",
            approved_by_user_id="user-99",
            approved_at=_now(),
        ),
    )
    # 3. MOTIVATED (decision → PRD artifact)
    facade.write_edge(
        workspace_id,
        make_motivated_edge(
            workspace_id=workspace_id,
            decision_id="dec-1",
            artifact_id="art-prd-1",
            artifact_kind="prd",
        ),
    )
    # 4. RESULTED_IN (PRD → outcome)
    facade.write_edge(
        workspace_id,
        make_resulted_in_edge(
            workspace_id=workspace_id,
            artifact_id="art-prd-1",
            outcome_id="out-1",
            shipped_at=_now() - timedelta(days=2),
        ),
    )

    chain = facade.trace_outcome_provenance(workspace_id, "out-1")
    assert chain.outcome_id == "out-1"
    assert chain.artifact_ids == ["art-prd-1"]
    assert chain.decision_id == "dec-1"
    assert chain.hypothesis_id == "hyp-1"
    assert set(chain.signal_ids) == {"sig-1", "sig-2", "sig-3"}
    # 1 RESULTED_IN + 1 MOTIVATED + 1 PROMOTED_TO + 3 SUPPORTS = 6 steps.
    assert len(chain.walk_steps) == 6


def test_trace_outcome_provenance_empty_when_no_resulted_in(facade):
    chain = facade.trace_outcome_provenance("ws-1", "out-orphan")
    assert chain.outcome_id == "out-orphan"
    assert chain.artifact_ids == []
    assert chain.decision_id is None
    assert chain.signal_ids == []


def test_trace_outcome_provenance_stops_at_artifact_without_decision(facade):
    """Outcome ← Artifact exists but no MOTIVATED edge → walk stops at
    the artifact level."""
    facade.write_edge(
        "ws-1",
        make_resulted_in_edge(
            workspace_id="ws-1",
            artifact_id="art-1",
            outcome_id="out-1",
            shipped_at=_now(),
        ),
    )
    chain = facade.trace_outcome_provenance("ws-1", "out-1")
    assert chain.artifact_ids == ["art-1"]
    assert chain.decision_id is None
    assert chain.signal_ids == []


# ─────────────────────── 6. tenant isolation on the walk ───────────────────────


def test_trace_provenance_cannot_cross_workspaces(facade):
    """Same decision_id + hypothesis_id exist in two workspaces. Walking
    from ws-1 must NOT pull signals that live in ws-2."""
    # ws-1: full chain with sig-1, sig-2
    _wire_decision_chain(
        facade,
        workspace_id="ws-1",
        signal_ids=["sig-ws1-a", "sig-ws1-b"],
        hypothesis_id="hyp-shared",
        decision_id="dec-shared",
    )
    # ws-2: same IDs but different signals.
    _wire_decision_chain(
        facade,
        workspace_id="ws-2",
        signal_ids=["sig-ws2-a", "sig-ws2-b", "sig-ws2-c"],
        hypothesis_id="hyp-shared",
        decision_id="dec-shared",
    )

    chain1 = facade.trace_provenance("ws-1", "dec-shared")
    chain2 = facade.trace_provenance("ws-2", "dec-shared")

    assert set(chain1.signal_ids) == {"sig-ws1-a", "sig-ws1-b"}
    assert set(chain2.signal_ids) == {"sig-ws2-a", "sig-ws2-b", "sig-ws2-c"}
    # No leakage in either direction.
    assert not set(chain1.signal_ids) & set(chain2.signal_ids)


def test_trace_outcome_provenance_cannot_cross_workspaces(facade):
    """Same outcome_id exists in two workspaces. Walking from ws-1 must
    NOT touch ws-2's artifacts, decisions, or signals."""
    # ws-1: full chain
    for sid in ("sig-x", "sig-y"):
        facade.write_edge(
            "ws-1",
            make_supports_edge(
                workspace_id="ws-1", signal_id=sid, hypothesis_id="hyp-shared"
            ),
        )
    facade.write_edge(
        "ws-1",
        make_promoted_to_edge(
            workspace_id="ws-1",
            hypothesis_id="hyp-shared",
            decision_id="dec-shared",
            approved_by_user_id="u",
            approved_at=_now(),
        ),
    )
    facade.write_edge(
        "ws-1",
        make_motivated_edge(
            workspace_id="ws-1",
            decision_id="dec-shared",
            artifact_id="art-shared",
            artifact_kind="prd",
        ),
    )
    facade.write_edge(
        "ws-1",
        make_resulted_in_edge(
            workspace_id="ws-1",
            artifact_id="art-shared",
            outcome_id="out-shared",
            shipped_at=_now(),
        ),
    )

    # ws-2: same IDs, different supports.
    for sid in ("sig-leak-1", "sig-leak-2"):
        facade.write_edge(
            "ws-2",
            make_supports_edge(
                workspace_id="ws-2", signal_id=sid, hypothesis_id="hyp-shared"
            ),
        )
    facade.write_edge(
        "ws-2",
        make_promoted_to_edge(
            workspace_id="ws-2",
            hypothesis_id="hyp-shared",
            decision_id="dec-shared",
            approved_by_user_id="u",
            approved_at=_now(),
        ),
    )
    facade.write_edge(
        "ws-2",
        make_motivated_edge(
            workspace_id="ws-2",
            decision_id="dec-shared",
            artifact_id="art-shared",
            artifact_kind="prd",
        ),
    )
    facade.write_edge(
        "ws-2",
        make_resulted_in_edge(
            workspace_id="ws-2",
            artifact_id="art-shared",
            outcome_id="out-shared",
            shipped_at=_now(),
        ),
    )

    chain1 = facade.trace_outcome_provenance("ws-1", "out-shared")
    chain2 = facade.trace_outcome_provenance("ws-2", "out-shared")
    assert set(chain1.signal_ids) == {"sig-x", "sig-y"}
    assert set(chain2.signal_ids) == {"sig-leak-1", "sig-leak-2"}
    assert not set(chain1.signal_ids) & set(chain2.signal_ids)

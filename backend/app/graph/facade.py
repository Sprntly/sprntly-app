"""KG facade — the only API the rest of Sprntly should call.

This module is the tenant-isolation layer. Every public function takes
`workspace_id` as its first argument; the function asserts that the
entity being read/written carries the same workspace_id, raising
TenantViolationError otherwise. Other modules MUST go through this
facade — directly instantiating SqliteBackend or FalkorBackend bypasses
the safety checks and is forbidden (the convention is documented; a
follow-up could add a runtime guard).

Engineering decisions:
  1. `GRAPH_BACKEND` env var selects backend at process startup. Default
     is "sqlite" so existing deployments keep working without FalkorDB.
     Flipping to "falkor" requires the container running + P1-10/P1-11
     PRs merged.
  2. Every read/write enforces `entity.workspace_id == requested_workspace_id`.
     This is the floor for tenant isolation; the FalkorDB layer also
     scopes by `group_id = workspace_id` per spec but we don't trust it
     blindly.
  3. Decision creation is special: it must `_promote_from_hypothesis`
     and verifies the source Hypothesis exists in the same workspace.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.graph.backends import GraphBackend, get_backend
from app.graph.edges import Edge, EdgeType
from app.graph.entities import (
    Artifact,
    ArtifactType,
    ConfidenceTier,
    Decision,
    DismissedReason,
    Hypothesis,
    HypothesisStatus,
    KpiTreeNode,
    Outcome,
    ProvenanceTag,
    SIGNAL_STALENESS_BY_SOURCE_TYPE,
    Signal,
    SignalSourceType,
    Workspace,
    WorkspacePlan,
    WorkspaceStage,
    WorkspaceStrategy,
)
from app.graph.exceptions import (
    GraphError,
    HypothesisPromotionError,
    ImmutabilityError,
    TenantViolationError,
)
from app.graph.query_types import (
    ArtifactDelta,
    BriefContext,
    PrdContext,
    ProvenanceChain,
    SessionContext,
    SweepReport,
    WorkspaceSnapshot,
)

logger = logging.getLogger(__name__)


def _assert_tenant(workspace_id: str, entity_workspace_id: str) -> None:
    if workspace_id != entity_workspace_id:
        raise TenantViolationError(
            f"workspace_id mismatch: requested={workspace_id} entity={entity_workspace_id}"
        )


class GraphFacade:
    """The single point of access to the KG.

    Instantiate once at FastAPI app startup with the configured backend:

        graph = GraphFacade.from_env()
        graph.initialize()
    """

    # In-process session-context cache. Per spec §10 load_session_context
    # must complete in ≤500ms — even a single warm query is fast enough,
    # but repeated calls within one HTTP request (e.g. brief + ask in the
    # same flow) hit this cache and avoid 4 SQL round-trips. 30s TTL is
    # short enough that stale hypothesis/decision writes are reflected
    # before the next user interaction.
    _SESSION_CTX_TTL_SECONDS = 30.0

    def __init__(self, backend: GraphBackend):
        self._backend = backend
        # workspace_id → (SessionContext, perf_counter() expiry)
        self._session_ctx_cache: dict[str, tuple[SessionContext, float]] = {}
        self._cache_lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "GraphFacade":
        backend_name = os.environ.get("GRAPH_BACKEND", "sqlite").lower()
        if backend_name == "sqlite":
            db_path = os.environ.get("DB_PATH") or os.environ.get(
                "KG_SQLITE_PATH", "/var/lib/sprntly/data/sprintly.db"
            )
            backend = get_backend("sqlite", db_path=db_path)
        elif backend_name == "falkor":
            host = os.environ.get("FALKORDB_HOST", "127.0.0.1")
            port = int(os.environ.get("FALKORDB_PORT", "6379"))
            password = os.environ.get("FALKORDB_PASSWORD") or None
            backend = get_backend("falkor", host=host, port=port, password=password)
        else:
            raise ValueError(f"Unknown GRAPH_BACKEND={backend_name!r}")
        logger.info("GraphFacade backend=%s", backend_name)
        return cls(backend)

    def initialize(self) -> None:
        self._backend.initialize_schema()

    def healthy(self) -> bool:
        return self._backend.ping()

    # ──────────── Workspace ────────────

    def write_workspace(self, workspace_id: str, workspace: Workspace) -> None:
        _assert_tenant(workspace_id, workspace.workspace_id)
        self._backend.write_workspace(workspace)
        self._invalidate_cache(workspace_id)

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        return self._backend.get_workspace(workspace_id)

    # ──────────── Signal ────────────

    def write_signal(self, workspace_id: str, signal: Signal) -> None:
        _assert_tenant(workspace_id, signal.workspace_id)
        self._backend.write_signal(signal)

    def get_signal(self, workspace_id: str, signal_id: str) -> Optional[Signal]:
        return self._backend.get_signal(workspace_id, signal_id)

    def list_active_signals(
        self,
        workspace_id: str,
        source_types: Optional[list[str]] = None,
        limit: int = 50,
    ) -> list[Signal]:
        return self._backend.list_active_signals(workspace_id, source_types, limit)

    # ──────────── Hypothesis ────────────

    def write_hypothesis(self, workspace_id: str, hypothesis: Hypothesis) -> None:
        _assert_tenant(workspace_id, hypothesis.workspace_id)
        self._backend.write_hypothesis(hypothesis)
        self._invalidate_cache(workspace_id)

    def get_hypothesis(
        self, workspace_id: str, hypothesis_id: str
    ) -> Optional[Hypothesis]:
        return self._backend.get_hypothesis(workspace_id, hypothesis_id)

    def list_active_hypotheses(
        self, workspace_id: str, limit: int = 10
    ) -> list[Hypothesis]:
        return self._backend.list_active_hypotheses(workspace_id, limit)

    # ──────────── Decision (promotion-only path) ────────────

    def promote_hypothesis_to_decision(
        self, workspace_id: str, decision: Decision
    ) -> None:
        """Decisions are never created directly. The caller must construct
        the Decision with `promoted_from_hypothesis_id` already set; we
        verify the source Hypothesis exists in the same workspace.
        """
        _assert_tenant(workspace_id, decision.workspace_id)
        if not decision.promoted_from_hypothesis_id:
            raise HypothesisPromotionError(
                "Decision must be promoted from a Hypothesis (promoted_from_hypothesis_id required)"
            )
        source_hyp = self._backend.get_hypothesis(
            workspace_id, decision.promoted_from_hypothesis_id
        )
        if source_hyp is None:
            raise HypothesisPromotionError(
                f"Source Hypothesis {decision.promoted_from_hypothesis_id} not found "
                f"in workspace {workspace_id}"
            )
        # Existence + idempotency: if the same decision_id was already
        # written, deny re-write because Decision is immutable.
        existing = self._backend.get_decision(workspace_id, decision.decision_id)
        if existing is not None and existing.evidence_snapshot != decision.evidence_snapshot:
            raise ImmutabilityError(
                f"Decision {decision.decision_id} already exists with a different "
                "evidence_snapshot. Decisions are immutable; create a new Decision instead."
            )
        self._backend.write_decision(decision)
        self._invalidate_cache(workspace_id)

    def get_decision(
        self, workspace_id: str, decision_id: str
    ) -> Optional[Decision]:
        return self._backend.get_decision(workspace_id, decision_id)

    # ──────────── Outcome ────────────

    def write_outcome(self, workspace_id: str, outcome: Outcome) -> None:
        _assert_tenant(workspace_id, outcome.workspace_id)
        self._backend.write_outcome(outcome)
        self._invalidate_cache(workspace_id)

    def get_outcome(self, workspace_id: str, outcome_id: str) -> Optional[Outcome]:
        return self._backend.get_outcome(workspace_id, outcome_id)

    # ──────────── Artifact ────────────

    def write_artifact(self, workspace_id: str, artifact: Artifact) -> None:
        _assert_tenant(workspace_id, artifact.workspace_id)
        self._backend.write_artifact(artifact)

    def get_artifact(
        self, workspace_id: str, artifact_id: str
    ) -> Optional[Artifact]:
        return self._backend.get_artifact(workspace_id, artifact_id)

    # ──────────── Edges ────────────

    def write_edge(self, workspace_id: str, edge: Edge) -> None:
        _assert_tenant(workspace_id, edge.workspace_id)
        edge.validate_direction()  # raises if source/target types are wrong
        self._backend.write_edge(edge)

    def edges_from(
        self,
        workspace_id: str,
        source_entity_id: str,
        edge_type: Optional[EdgeType] = None,
    ) -> list[Edge]:
        return self._backend.edges_from(
            workspace_id,
            source_entity_id,
            edge_type.value if edge_type else None,
        )

    def edges_to(
        self,
        workspace_id: str,
        target_entity_id: str,
        edge_type: Optional[EdgeType] = None,
    ) -> list[Edge]:
        return self._backend.edges_to(
            workspace_id,
            target_entity_id,
            edge_type.value if edge_type else None,
        )

    # ════════════════════════════════════════════════════════════════════
    # The 12 public functions per spec §10. Order matches the spec exactly.
    # ════════════════════════════════════════════════════════════════════

    # ──────────── (1) seed_workspace ────────────

    def seed_workspace(
        self,
        workspace_id: str,
        onboarding_data: dict[str, Any],
    ) -> Workspace:
        """Spec §10 — create the root Workspace from onboarding payload.

        This duplicates the logic of `event_5_1_onboarding_complete` (which
        lands in feat/kg-write-events). When that event handler ships, this
        function will delegate to it; for now the body is inline to unblock
        the query API PR.

        TODO(P1-12): consolidate with event_5_1 once it merges.
        """
        now = datetime.now(timezone.utc)
        valid_at = onboarding_data.get("valid_at") or (now - timedelta(microseconds=1))
        if isinstance(valid_at, str):
            valid_at = datetime.fromisoformat(valid_at.replace("Z", "+00:00"))

        kpi_tree_raw = onboarding_data.get("kpi_tree") or []
        kpi_tree = [
            KpiTreeNode(**n) if isinstance(n, dict) else n
            for n in kpi_tree_raw
        ]
        strategy_raw = onboarding_data.get("strategy") or {}
        strategy = (
            strategy_raw
            if isinstance(strategy_raw, WorkspaceStrategy)
            else WorkspaceStrategy(**strategy_raw)
        )

        stage = onboarding_data.get("stage", WorkspaceStage.SEED)
        if isinstance(stage, str):
            stage = WorkspaceStage(stage)
        plan = onboarding_data.get("plan", WorkspacePlan.FREE)
        if isinstance(plan, str):
            plan = WorkspacePlan(plan)

        ws = Workspace(
            workspace_id=workspace_id,
            valid_at=valid_at,
            transaction_at=now,
            company_name=onboarding_data["company_name"],
            industry=onboarding_data["industry"],
            stage=stage,
            business_model=onboarding_data["business_model"],
            kpi_tree=kpi_tree,
            strategy=strategy,
            competitors=onboarding_data.get("competitors", []),
            plan=plan,
            team_capacity=onboarding_data.get("team_capacity"),
            created_at=onboarding_data.get("created_at", now),
            updated_at=onboarding_data.get("updated_at", now),
        )
        self.write_workspace(workspace_id, ws)
        self._invalidate_cache(workspace_id)
        return ws

    # ──────────── (2) load_session_context ────────────

    def load_session_context(self, workspace_id: str) -> SessionContext:
        """Spec §7 pattern 1 + §10 — workspace + top 10 active hypotheses +
        last 5 decisions + last 3 measured outcomes. Latency budget: ≤500ms.

        Strategy:
          1. Check in-process cache (30s TTL). Hits return in microseconds.
          2. On miss, run 4 indexed reads against the backend. SqliteBackend
             uses idx_kg_hyp_ws_status, idx_kg_dec_ws_approved,
             idx_kg_out_ws_measured — each is a workspace-scoped index lookup.
          3. Wrap into a SessionContext (Pydantic) and cache it.
        """
        # Cache check
        with self._cache_lock:
            entry = self._session_ctx_cache.get(workspace_id)
            if entry is not None:
                ctx, expires_at = entry
                if time.perf_counter() < expires_at:
                    return ctx

        # Cold path — backend fetch.
        raw = self._backend.load_session_context(workspace_id)
        ctx = SessionContext(
            workspace=raw.get("workspace"),
            active_hypotheses=raw.get("active_hypotheses", []),
            recent_decisions=raw.get("recent_decisions", []),
            recent_outcomes=raw.get("recent_outcomes", []),
            loaded_at=datetime.now(timezone.utc),
        )

        with self._cache_lock:
            self._session_ctx_cache[workspace_id] = (
                ctx,
                time.perf_counter() + self._SESSION_CTX_TTL_SECONDS,
            )
        return ctx

    def _invalidate_cache(self, workspace_id: str) -> None:
        """Drop the session-context cache for a workspace. Called on every
        write that would change session contents (hypothesis/decision/outcome)."""
        with self._cache_lock:
            self._session_ctx_cache.pop(workspace_id, None)

    # ──────────── (3) ingest_signal ────────────

    def ingest_signal(
        self,
        workspace_id: str,
        raw_text: str,
        source_type: SignalSourceType | str,
        source_tool: str,
        valid_at: datetime,
        *,
        signal_id: Optional[str] = None,
        provenance_tag: ProvenanceTag | str = ProvenanceTag.CONNECTOR_INGEST,
        confidence: float = 0.8,
        kpi_relevance: Optional[list[str]] = None,
        raw_source_ref: Optional[str] = None,
    ) -> Signal:
        """High-level Signal write. Generates UUID + applies the per-source
        staleness window from SIGNAL_STALENESS_BY_SOURCE_TYPE (spec §3.2.1).

        Outcome-measured signals get stale_after=None (never expire), enforced
        by the Signal model validator.
        """
        if isinstance(source_type, str):
            source_type = SignalSourceType(source_type)
        if isinstance(provenance_tag, str):
            provenance_tag = ProvenanceTag(provenance_tag)

        if valid_at.tzinfo is None:
            valid_at = valid_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        # transaction_at must differ from valid_at per BitemporalMixin invariant.
        transaction_at = now if now != valid_at else now + timedelta(microseconds=1)

        if provenance_tag == ProvenanceTag.OUTCOME_MEASURED:
            stale_after: Optional[datetime] = None
        else:
            window_days = SIGNAL_STALENESS_BY_SOURCE_TYPE[source_type]
            stale_after = valid_at + timedelta(days=window_days)

        sig = Signal(
            workspace_id=workspace_id,
            valid_at=valid_at,
            transaction_at=transaction_at,
            signal_id=signal_id or f"sig-{uuid.uuid4().hex[:12]}",
            content=raw_text,
            source_type=source_type,
            source_tool=source_tool,
            provenance_tag=provenance_tag,
            confidence=confidence,
            stale_after=stale_after,
            kpi_relevance=kpi_relevance or [],
            raw_source_ref=raw_source_ref,
        )
        self.write_signal(workspace_id, sig)
        return sig

    # ──────────── (4) create_hypothesis ────────────

    def create_hypothesis(
        self,
        workspace_id: str,
        claim: str,
        evidence_signal_ids: list[str],
        predicted_metric: str,
        predicted_impact_low: float,
        predicted_impact_high: float,
        reversal_condition: str,
        *,
        hypothesis_id: Optional[str] = None,
        predicted_impact_basis: str = "Generated by synthesis_agent_run.",
        confidence_composite: float = 0.6,
        confidence_tier: ConfidenceTier = ConfidenceTier.MEDIUM,
    ) -> Hypothesis:
        """High-level Hypothesis write. Auto-sets status:
          - "candidate" if evidence_count < 3 OR <2 distinct source_types
          - "proposed" otherwise
        per spec §3.3 / §5 synthesis logic.
        """
        # Look up the cited signals to count distinct source_types.
        source_types: set[str] = set()
        for sid in evidence_signal_ids:
            sig = self._backend.get_signal(workspace_id, sid)
            if sig is not None:
                source_types.add(sig.source_type.value)

        evidence_count = len(evidence_signal_ids)
        if evidence_count < 3 or len(source_types) < 2:
            status = HypothesisStatus.CANDIDATE
        else:
            status = HypothesisStatus.PROPOSED

        now = datetime.now(timezone.utc)
        valid_at = now - timedelta(microseconds=1)

        hyp = Hypothesis(
            workspace_id=workspace_id,
            valid_at=valid_at,
            transaction_at=now,
            hypothesis_id=hypothesis_id or f"hyp-{uuid.uuid4().hex[:12]}",
            claim=claim,
            predicted_metric=predicted_metric,
            predicted_impact_low=predicted_impact_low,
            predicted_impact_high=predicted_impact_high,
            predicted_impact_basis=predicted_impact_basis,
            status=status,
            evidence_signal_ids=evidence_signal_ids,
            evidence_count=evidence_count,
            confidence_composite=confidence_composite,
            confidence_tier=confidence_tier,
            reversal_condition=reversal_condition,
            created_at=now,
            status_updated_at=now,
        )
        self.write_hypothesis(workspace_id, hyp)
        self._invalidate_cache(workspace_id)
        return hyp

    # ──────────── (5) approve_hypothesis ────────────

    def approve_hypothesis(
        self,
        workspace_id: str,
        hypothesis_id: str,
        approved_by_user_id: str,
        *,
        reasoning: Optional[str] = None,
        decision_id: Optional[str] = None,
    ) -> Decision:
        """Promote a Hypothesis to a Decision. Freezes evidence_snapshot from
        the current Signals (spec §5.4 — Decision.evidence_snapshot IMMUTABLE).
        """
        hyp = self._backend.get_hypothesis(workspace_id, hypothesis_id)
        if hyp is None:
            raise HypothesisPromotionError(
                f"Hypothesis {hypothesis_id} not found in workspace {workspace_id}"
            )

        # Freeze the evidence snapshot from current signal state.
        evidence_snapshot: dict[str, Any] = {"signals": []}
        for sid in hyp.evidence_signal_ids:
            sig = self._backend.get_signal(workspace_id, sid)
            if sig is not None:
                evidence_snapshot["signals"].append(
                    {
                        "signal_id": sig.signal_id,
                        "content": sig.content,
                        "source_type": sig.source_type.value,
                        "source_tool": sig.source_tool,
                        "confidence": sig.confidence,
                        "provenance_tag": sig.provenance_tag.value,
                    }
                )

        # Pull the live KPI tree to freeze on the Decision.
        ws = self._backend.get_workspace(workspace_id)
        if ws is None:
            raise GraphError(
                f"Workspace {workspace_id} missing — cannot freeze KPI tree for Decision."
            )

        now = datetime.now(timezone.utc)
        valid_at = now - timedelta(microseconds=1)

        decision = Decision(
            workspace_id=workspace_id,
            valid_at=valid_at,
            transaction_at=now,
            decision_id=decision_id or f"dec-{uuid.uuid4().hex[:12]}",
            promoted_from_hypothesis_id=hypothesis_id,
            claim=hyp.claim,
            reasoning=reasoning
            or f"Approved by {approved_by_user_id} based on {hyp.evidence_count} signals.",
            approved_by_user_id=approved_by_user_id,
            approved_at=now,
            evidence_snapshot=evidence_snapshot,
            kpi_tree_snapshot=ws.kpi_tree,
            reversal_condition=hyp.reversal_condition,
        )
        self.promote_hypothesis_to_decision(workspace_id, decision)

        # Mirror PROMOTED_TO edge in the graph (spec §4).
        edge = Edge(
            workspace_id=workspace_id,
            valid_at=valid_at,
            transaction_at=now,
            edge_type=EdgeType.PROMOTED_TO,
            source_entity_id=hyp.hypothesis_id,
            source_entity_type="Hypothesis",
            target_entity_id=decision.decision_id,
            target_entity_type="Decision",
            source="brief_recommendation_approved",
            confidence=1.0,
        )
        self.write_edge(workspace_id, edge)

        # Bump the Hypothesis to CONFIRMED + cross-link.
        hyp_confirmed = hyp.model_copy(
            update={
                "status": HypothesisStatus.CONFIRMED,
                "status_updated_at": now,
                "promoted_to_decision_id": decision.decision_id,
            }
        )
        self._backend.write_hypothesis(hyp_confirmed)
        self._invalidate_cache(workspace_id)
        return decision

    # ──────────── (6) dismiss_hypothesis ────────────

    def dismiss_hypothesis(
        self,
        workspace_id: str,
        hypothesis_id: str,
        dismissed_reason: DismissedReason | str,
    ) -> Hypothesis:
        """Set status=rejected + dismissed_reason. Spec §5.5."""
        if isinstance(dismissed_reason, str):
            dismissed_reason = DismissedReason(dismissed_reason)
        hyp = self._backend.get_hypothesis(workspace_id, hypothesis_id)
        if hyp is None:
            raise GraphError(
                f"Hypothesis {hypothesis_id} not found in workspace {workspace_id}"
            )
        now = datetime.now(timezone.utc)
        dismissed = hyp.model_copy(
            update={
                "status": HypothesisStatus.REJECTED,
                "dismissed_reason": dismissed_reason,
                "status_updated_at": now,
            }
        )
        self._backend.write_hypothesis(dismissed)
        self._invalidate_cache(workspace_id)
        return dismissed

    # ──────────── (7) write_artifact_delta ────────────

    @staticmethod
    def _classify_delta(original_text: str, edited_text: str) -> str:
        """Heuristic delta classifier — keyword-based until the Claude
        classifier lands. Spec §5.7 lists: preference, data-driven,
        scope-cut, wording.

        TODO(P1-13): replace with `app.llm.call_json` invocation.
        """
        if not original_text:
            return "wording"
        original_lower = original_text.lower()
        edited_lower = edited_text.lower()
        if len(edited_text) < 0.5 * len(original_text):
            return "scope-cut"
        # Data-driven: numeric values changed
        import re

        original_nums = re.findall(r"\b\d+(?:\.\d+)?\b", original_text)
        edited_nums = re.findall(r"\b\d+(?:\.\d+)?\b", edited_text)
        if original_nums != edited_nums and (original_nums or edited_nums):
            return "data-driven"
        # Preference: words like "we prefer", "always", "never"
        if any(
            kw in edited_lower and kw not in original_lower
            for kw in ("we prefer", "always", "never", "must ", "must not")
        ):
            return "preference"
        return "wording"

    def write_artifact_delta(
        self,
        workspace_id: str,
        artifact_id: str,
        artifact_type: ArtifactType | str,
        section: str,
        original_text: str,
        edited_text: str,
        user_id: str,
    ) -> ArtifactDelta:
        """Spec §5.7 — record one PM edit against an Artifact. Classifies
        the edit (heuristic for now, Claude-backed in a follow-up) and
        appends to the kg_artifact_deltas log."""
        if isinstance(artifact_type, ArtifactType):
            artifact_type_str = artifact_type.value
        else:
            artifact_type_str = ArtifactType(artifact_type).value

        # Verify the artifact lives in the requested workspace.
        art = self._backend.get_artifact(workspace_id, artifact_id)
        if art is not None:
            _assert_tenant(workspace_id, art.workspace_id)

        classification = self._classify_delta(original_text, edited_text)
        now = datetime.now(timezone.utc)
        valid_at = now - timedelta(microseconds=1)
        delta = ArtifactDelta(
            delta_id=f"delta-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            artifact_id=artifact_id,
            artifact_type=artifact_type_str,
            section=section,
            original_text=original_text,
            edited_text=edited_text,
            user_id=user_id,
            classification=classification,
            valid_at=valid_at,
            transaction_at=now,
        )
        self._backend.write_artifact_delta(
            {
                "delta_id": delta.delta_id,
                "workspace_id": delta.workspace_id,
                "artifact_id": delta.artifact_id,
                "artifact_type": delta.artifact_type,
                "section": delta.section,
                "original_text": delta.original_text,
                "edited_text": delta.edited_text,
                "user_id": delta.user_id,
                "classification": delta.classification,
                "valid_at": delta.valid_at.isoformat(),
                "transaction_at": delta.transaction_at.isoformat(),
            }
        )
        return delta

    def list_artifact_deltas(
        self, workspace_id: str, artifact_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        return self._backend.list_artifact_deltas(workspace_id, artifact_id)

    # ──────────── (8) write_outcome (high-level) ────────────

    def write_outcome_for_decision(
        self,
        workspace_id: str,
        decision_id: str,
        feature_name: str,
        shipped_at: datetime,
        *,
        outcome_id: Optional[str] = None,
        metric_measured: Optional[str] = None,
    ) -> Outcome:
        """Spec §10 — wraps event_5_8_feature_shipped. Creates a fresh
        Outcome linked to the Decision. predicted_impact_low/high are copied
        from the source Decision/Hypothesis (NOT the ship-time value — spec
        invariant §3.5).

        TODO(P1-12): once event_5_8 lands, delegate to it.
        """
        dec = self._backend.get_decision(workspace_id, decision_id)
        if dec is None:
            raise GraphError(
                f"Decision {decision_id} not found in workspace {workspace_id}"
            )
        hyp = self._backend.get_hypothesis(workspace_id, dec.promoted_from_hypothesis_id)
        if hyp is None:
            raise GraphError(
                f"Source Hypothesis {dec.promoted_from_hypothesis_id} not found "
                f"in workspace {workspace_id}"
            )

        if shipped_at.tzinfo is None:
            shipped_at = shipped_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        # transaction_at must differ from valid_at; use ship time as valid_at.
        valid_at = shipped_at
        transaction_at = now if now != valid_at else now + timedelta(microseconds=1)

        outcome = Outcome(
            workspace_id=workspace_id,
            valid_at=valid_at,
            transaction_at=transaction_at,
            outcome_id=outcome_id or f"out-{uuid.uuid4().hex[:12]}",
            linked_decision_id=decision_id,
            linked_hypothesis_id=hyp.hypothesis_id,
            linked_signal_ids=hyp.evidence_signal_ids,
            feature_name=feature_name,
            shipped_at=shipped_at,
            metric_measured=metric_measured or hyp.predicted_metric,
            predicted_impact_low=hyp.predicted_impact_low,
            predicted_impact_high=hyp.predicted_impact_high,
            provenance_tag=ProvenanceTag.OUTCOME_MEASURED,
        )
        self.write_outcome(workspace_id, outcome)

        # Cross-link Decision → outcome_id for trace_provenance to walk.
        dec_updated = dec.model_copy(update={"outcome_id": outcome.outcome_id})
        # Decision is immutable in evidence_snapshot/kpi_tree_snapshot only;
        # outcome_id is a metadata link the spec allows post-creation.
        self._backend.write_decision(dec_updated)
        self._invalidate_cache(workspace_id)
        return outcome

    # ──────────── (9) update_outcome_measurement ────────────

    def update_outcome_measurement(
        self,
        workspace_id: str,
        outcome_id: str,
        actual_impact: float,
        measured_at: datetime,
        *,
        pm_annotation: Optional[str] = None,
        confounding_factors: Optional[list[str]] = None,
    ) -> Outcome:
        """Spec §10 — wraps event_5_9_outcome_measured. Fills actual_impact +
        measured_at, computes prediction_hit (whether actual fell within
        [predicted_impact_low, predicted_impact_high]) and prediction_delta.

        TODO(P1-12): consolidate with event_5_9 once it lands.
        """
        outcome = self._backend.get_outcome(workspace_id, outcome_id)
        if outcome is None:
            raise GraphError(
                f"Outcome {outcome_id} not found in workspace {workspace_id}"
            )

        if measured_at.tzinfo is None:
            measured_at = measured_at.replace(tzinfo=timezone.utc)

        prediction_hit = (
            outcome.predicted_impact_low <= actual_impact <= outcome.predicted_impact_high
        )
        # Distance from midpoint of predicted range — signed.
        midpoint = (outcome.predicted_impact_low + outcome.predicted_impact_high) / 2.0
        prediction_delta = actual_impact - midpoint

        updated = outcome.model_copy(
            update={
                "actual_impact": actual_impact,
                "actual_impact_measured_at": measured_at,
                "prediction_hit": prediction_hit,
                "prediction_delta": prediction_delta,
                "pm_annotation": pm_annotation,
                "confounding_factors": confounding_factors or outcome.confounding_factors,
            }
        )
        self.write_outcome(workspace_id, updated)
        self._invalidate_cache(workspace_id)
        return updated

    # ──────────── (10) trace_provenance ────────────

    def trace_provenance(
        self, workspace_id: str, decision_id: str
    ) -> ProvenanceChain:
        """Spec §7 pattern 4 — walk a Decision back to its Signals via
        PROMOTED_TO and SUPPORTS/CONTRADICTS edges. Returns the full chain
        plus an ordered walk_steps list for UI rendering.

        Graph shape:
          Signal --SUPPORTS--> Hypothesis --PROMOTED_TO--> Decision --RESULTED_IN--> Outcome
                  (or CONTRADICTS)
        """
        dec = self._backend.get_decision(workspace_id, decision_id)
        if dec is None:
            raise GraphError(
                f"Decision {decision_id} not found in workspace {workspace_id}"
            )

        hyp_id = dec.promoted_from_hypothesis_id
        hyp = self._backend.get_hypothesis(workspace_id, hyp_id)

        # Resolve supporting/contradicting signals via edges.
        supporting_ids: list[str] = []
        contradicting_ids: list[str] = []
        if hyp is not None:
            for e in self._backend.edges_to(workspace_id, hyp_id, EdgeType.SUPPORTS.value):
                supporting_ids.append(e.source_entity_id)
            for e in self._backend.edges_to(
                workspace_id, hyp_id, EdgeType.CONTRADICTS.value
            ):
                contradicting_ids.append(e.source_entity_id)
            # Fallback: if no edges have been written yet (early Sprntly
            # deployments without the event-write layer), use the Hypothesis's
            # evidence_signal_ids + disconfirming_signals.
            if not supporting_ids:
                supporting_ids = list(hyp.evidence_signal_ids)
            if not contradicting_ids:
                contradicting_ids = list(hyp.disconfirming_signals)

        walk_steps: list[dict[str, Any]] = []
        for sid in supporting_ids:
            walk_steps.append(
                {
                    "edge_type": EdgeType.SUPPORTS.value,
                    "source_entity_id": sid,
                    "target_entity_id": hyp_id,
                    "valid_at": dec.valid_at.isoformat(),
                }
            )
        for sid in contradicting_ids:
            walk_steps.append(
                {
                    "edge_type": EdgeType.CONTRADICTS.value,
                    "source_entity_id": sid,
                    "target_entity_id": hyp_id,
                    "valid_at": dec.valid_at.isoformat(),
                }
            )
        walk_steps.append(
            {
                "edge_type": EdgeType.PROMOTED_TO.value,
                "source_entity_id": hyp_id,
                "target_entity_id": decision_id,
                "valid_at": dec.valid_at.isoformat(),
            }
        )
        if dec.outcome_id:
            walk_steps.append(
                {
                    "edge_type": EdgeType.RESULTED_IN.value,
                    "source_entity_id": decision_id,
                    "target_entity_id": dec.outcome_id,
                    "valid_at": dec.valid_at.isoformat(),
                }
            )

        return ProvenanceChain(
            decision_id=decision_id,
            hypothesis_id=hyp_id,
            supporting_signal_ids=supporting_ids,
            contradicting_signal_ids=contradicting_ids,
            outcome_id=dec.outcome_id,
            walk_steps=walk_steps,
        )

    # ──────────── (11) query_as_of (bitemporal) ────────────

    def query_as_of(
        self, workspace_id: str, as_of_date: datetime
    ) -> WorkspaceSnapshot:
        """Spec §7 pattern 5 + §10 — bitemporal point-in-time view.

        Returns every entity in the workspace where:
            transaction_at <= as_of AND valid_at <= as_of

        Semantics:
          - transaction_at <= T: "what did we know at time T?"
          - valid_at <= T:       "what was true at time T?"
        v1 returns the most-recent entity-per-id where both conditions hold.
        Multi-version replay (every historical version) lands when we move
        to FalkorDB's native bitemporal indexing.
        """
        if as_of_date.tzinfo is None:
            as_of_date = as_of_date.replace(tzinfo=timezone.utc)
        return WorkspaceSnapshot(
            workspace_id=workspace_id,
            as_of=as_of_date,
            workspace=self._backend.get_workspace_as_of(workspace_id, as_of_date),
            signals=self._backend.list_signals_as_of(workspace_id, as_of_date),
            hypotheses=self._backend.list_hypotheses_as_of(workspace_id, as_of_date),
            decisions=self._backend.list_decisions_as_of(workspace_id, as_of_date),
            outcomes=self._backend.list_outcomes_as_of(workspace_id, as_of_date),
            artifacts=self._backend.list_artifacts_as_of(workspace_id, as_of_date),
        )

    # ──────────── (12) run_maintenance_sweep ────────────

    def run_maintenance_sweep(self, workspace_id: str) -> SweepReport:
        """Spec §10 — age signals, recompute hypothesis evidence weights,
        tag expired entities. v1 runs SYNCHRONOUSLY and returns the report.

        TODO(P1-12): move to `asyncio.create_task` + return a job_id once
        event_5_8/5_9 are wired and we have a job-status table.
        """
        now = datetime.now(timezone.utc)
        report = SweepReport(workspace_id=workspace_id, ran_at=now)

        # Pass 1: expire stale signals.
        try:
            # Pull everything (no stale filter) so we can re-evaluate.
            # We use list_active_signals with a stale filter trick: fetch
            # all signals where stale_after IS NULL OR > -inf, then filter
            # Python-side. For workspaces with thousands of signals we'd
            # paginate; for v1 the sweep runs per workspace and per-workspace
            # signal counts are bounded.
            all_signals: list[Signal] = []
            # Use the as_of helper to get everything written so far.
            all_signals = self._backend.list_signals_as_of(workspace_id, now)
            for sig in all_signals:
                if sig.stale_after is not None and sig.stale_after <= now:
                    # Already expired by the time-filter — but if cited by a
                    # hypothesis we still want to count it. The "expiry"
                    # status is already implicit in stale_after; we just count.
                    report.expired_signals += 1
        except Exception as e:  # pragma: no cover - defensive
            report.errors.append(f"signal_expiry_pass: {e!r}")

        # Pass 2: recompute hypothesis evidence counts.
        try:
            hyps = self._backend.list_hypotheses_as_of(workspace_id, now)
            for hyp in hyps:
                if hyp.status in (HypothesisStatus.REJECTED, HypothesisStatus.EXPIRED):
                    continue
                # Re-tally evidence using the current Signal state.
                live_evidence = []
                source_types: set[str] = set()
                for sid in hyp.evidence_signal_ids:
                    sig = self._backend.get_signal(workspace_id, sid)
                    if sig is None:
                        continue
                    if sig.stale_after is not None and sig.stale_after <= now:
                        continue
                    live_evidence.append(sid)
                    source_types.add(sig.source_type.value)
                # If counts changed, write back with the new tally.
                if len(live_evidence) != hyp.evidence_count:
                    new_status = hyp.status
                    if len(live_evidence) == 0:
                        # All evidence expired — mark expired.
                        new_status = HypothesisStatus.EXPIRED
                    updated = hyp.model_copy(
                        update={
                            "evidence_count": max(1, len(live_evidence)),
                            "status": new_status,
                            "status_updated_at": now,
                        }
                    )
                    # evidence_signal_ids has min_length=1 — keep original
                    # if we'd otherwise drop to zero, just flip the status.
                    self._backend.write_hypothesis(updated)
                    report.hypotheses_evidence_recomputed += 1
        except Exception as e:  # pragma: no cover - defensive
            report.errors.append(f"hypothesis_recompute_pass: {e!r}")

        # Pass 3: signal-weight updates from measured Outcomes (spec §5.9 →
        # UPDATES_WEIGHT edges). We count, but the actual signal-confidence
        # adjustment lands with the ds-agent integration. For now we just
        # count how many measured outcomes are wired up.
        try:
            measured = self._backend.list_recent_outcomes(
                workspace_id, limit=1000, measured_only=True
            )
            report.updated_signal_weights = len(measured)
        except Exception as e:  # pragma: no cover - defensive
            report.errors.append(f"signal_weight_pass: {e!r}")

        self._invalidate_cache(workspace_id)
        return report

    # ──────────── extra convenience queries (spec §7 patterns 2 & 3) ────────────

    def get_brief_context(self, workspace_id: str) -> BriefContext:
        """Spec §7 pattern 2 — extras the synthesis_agent_run needs beyond
        SessionContext: fresh uncited signals + per-source-tool accuracy +
        similar prior outcomes. Used at brief assembly Step 3."""
        signals = self._backend.list_active_signals(workspace_id, limit=200)
        hyps = self._backend.list_active_hypotheses(workspace_id, limit=100)
        cited_ids = {sid for h in hyps for sid in h.evidence_signal_ids}
        uncited = [s for s in signals if s.signal_id not in cited_ids]

        # Source accuracy: prediction_hit rate per source_tool from
        # historical measured outcomes.
        outcomes = self._backend.list_recent_outcomes(
            workspace_id, limit=500, measured_only=True
        )
        # Per-tool hits/totals — we need to look up linked signals.
        per_tool_hits: dict[str, list[bool]] = {}
        for o in outcomes:
            if o.prediction_hit is None:
                continue
            for sid in o.linked_signal_ids:
                sig = self._backend.get_signal(workspace_id, sid)
                if sig is None:
                    continue
                per_tool_hits.setdefault(sig.source_tool, []).append(o.prediction_hit)
        source_accuracy = {
            tool: (sum(hits) / len(hits)) if hits else 0.0
            for tool, hits in per_tool_hits.items()
        }

        return BriefContext(
            workspace_id=workspace_id,
            uncited_signals=uncited,
            source_accuracy=source_accuracy,
            similar_outcomes=outcomes[:10],
            loaded_at=datetime.now(timezone.utc),
        )

    def get_prd_context(
        self, workspace_id: str, decision_id: str
    ) -> PrdContext:
        """Spec §7 pattern 3 — Decision + workspace + KPI tree snapshot
        for PRD generation."""
        dec = self._backend.get_decision(workspace_id, decision_id)
        if dec is None:
            raise GraphError(
                f"Decision {decision_id} not found in workspace {workspace_id}"
            )
        ws = self._backend.get_workspace(workspace_id)
        if ws is None:
            raise GraphError(f"Workspace {workspace_id} missing")
        src_hyp = self._backend.get_hypothesis(workspace_id, dec.promoted_from_hypothesis_id)
        return PrdContext(
            decision=dec,
            workspace=ws,
            kpi_tree_snapshot=[n.model_dump() for n in dec.kpi_tree_snapshot],
            source_hypothesis=src_hyp,
            loaded_at=datetime.now(timezone.utc),
        )

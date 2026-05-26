"""KG bitemporal write events — the 9 named operations from spec §5.

Spec source: KG_Engineering_Spec §5.1 through §5.9. Each event is a
named, atomic orchestration over the underlying GraphFacade — they
are the only entry points the rest of the app should call into when
writing to the KG.

Module layout:
  - One `event_5_X_<name>` function per spec section.
  - One Pydantic payload per event (XPayload) so signatures stay typed
    and the FastAPI layer above this module can plug straight in.
  - `run_maintenance_sweep_async(graph, workspace_id)` — the async
    wrapper that wraps `maintenance.run_maintenance_sweep` in an
    `asyncio.create_task` so the request handler returns immediately.

Atomicity strategy:
  - **SqliteBackend**: every event runs inside a manual rollback envelope
    (`_WriteJournal`). We record every (entity_type, entity_id) we touch
    and, on failure, restore the prior state or delete the new node.
    This is good enough for MVP — SQLite is fast and the journal lives
    in memory for the duration of the event.
  - **FalkorBackend**: FalkorDB lacks true MVCC transactions. The
    spec-mandated approach is a saga pattern (compensating writes per
    step). That implementation is **deferred to a follow-up PR**; this
    module logs a TODO at the top of the rollback path.

Engineering decisions (Apurva, 2026-05-26):
  1. Maintenance sweep is fired **after** the event's atomic block
     succeeds, never before. If the event fails, the sweep is not
     queued — we don't want to spend CPU on a workspace that just
     failed a write.
  2. Hypothesis status promotion (candidate → proposed) is checked at
     write time in §5.2. The rule (evidence_count >= 3 AND >= 2 distinct
     source_types) lives here, not on the entity, because the entity
     doesn't see edge state.
  3. Cross-customer prior bootstrap (§5.1) uses synthetic hypothesis
     IDs prefixed `boot-` so they can be backfilled into the real
     similarity engine when Cognee is online.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

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
    Signal,
    SignalSourceType,
    Workspace,
    WorkspaceStage,
    WorkspaceStrategy,
    SIGNAL_STALENESS_BY_SOURCE_TYPE,
)
from app.graph.entity_resolution import resolve_or_create_signal
from app.graph.exceptions import GraphError
from app.graph.facade import GraphFacade
from app.graph.maintenance import SweepReport, run_maintenance_sweep

logger = logging.getLogger(__name__)


# ─────────────────────── helpers ───────────────────────


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _bitemporal_pair(real_when: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """Return (valid_at, transaction_at) with the spec-required gap.

    `valid_at` is when the fact was true; defaults to now-1us. `transaction_at`
    is always strictly greater (`+1µs` guarantee).
    """
    txn = _utcnow()
    val = real_when or (txn - timedelta(microseconds=1))
    if val >= txn:
        # Caller passed a future-dated real_when — bump txn to keep the invariant.
        txn = val + timedelta(microseconds=1)
    return val, txn


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _confidence_tier(score: float) -> ConfidenceTier:
    if score >= 0.85:
        return ConfidenceTier.VERY_HIGH
    if score >= 0.7:
        return ConfidenceTier.HIGH
    if score >= 0.5:
        return ConfidenceTier.MEDIUM
    return ConfidenceTier.LOW


def _stale_after_for(source_type: SignalSourceType, provenance: ProvenanceTag) -> Optional[datetime]:
    """Per-source/provenance staleness window. outcome-measured → None."""
    if provenance == ProvenanceTag.OUTCOME_MEASURED:
        return None
    days = SIGNAL_STALENESS_BY_SOURCE_TYPE.get(source_type, 30)
    return _utcnow() + timedelta(days=days)


# ─────────────────────── rollback journal ───────────────────────


class _WriteJournal:
    """Records every entity touched in an event so we can roll back on failure.

    Per-entity-type snapshot of pre-event state — if the key was absent,
    we drop the row on rollback; otherwise we restore the prior payload.

    NOTE: This works on `SqliteBackend` because writes are upserts and we
    can re-upsert the prior state. On `FalkorBackend` the saga pattern
    will need compensating writes per step — deferred (see module docstring).
    """

    def __init__(self, graph: GraphFacade, workspace_id: str):
        self.graph = graph
        self.workspace_id = workspace_id
        self._pre_state: list[tuple[str, str, Any]] = []  # (kind, id, prior_entity_or_None)
        self._new_edges: list[int] = []  # not tracked precisely yet
        self._committed = False

    def snapshot(self, kind: str, entity_id: str) -> None:
        """Capture pre-write state for one entity. Call BEFORE write."""
        prior = self._fetch(kind, entity_id)
        self._pre_state.append((kind, entity_id, prior))

    def _fetch(self, kind: str, entity_id: str):
        if kind == "workspace":
            return self.graph.get_workspace(entity_id)
        if kind == "signal":
            return self.graph.get_signal(self.workspace_id, entity_id)
        if kind == "hypothesis":
            return self.graph.get_hypothesis(self.workspace_id, entity_id)
        if kind == "decision":
            return self.graph.get_decision(self.workspace_id, entity_id)
        if kind == "outcome":
            return self.graph.get_outcome(self.workspace_id, entity_id)
        if kind == "artifact":
            return self.graph.get_artifact(self.workspace_id, entity_id)
        return None

    def _restore(self, kind: str, entity_id: str, prior) -> None:
        backend = self.graph._backend
        if prior is not None:
            # Re-upsert prior state via backend (bypasses tenant check, intentional).
            if kind == "workspace":
                backend.write_workspace(prior)
            elif kind == "signal":
                backend.write_signal(prior)
            elif kind == "hypothesis":
                backend.write_hypothesis(prior)
            elif kind == "decision":
                backend.write_decision(prior)
            elif kind == "outcome":
                backend.write_outcome(prior)
            elif kind == "artifact":
                backend.write_artifact(prior)
            return
        # prior was None → row didn't exist before; nuke it.
        table_map = {
            "workspace": ("kg_workspaces", "workspace_id"),
            "signal": ("kg_signals", "signal_id"),
            "hypothesis": ("kg_hypotheses", "hypothesis_id"),
            "decision": ("kg_decisions", "decision_id"),
            "outcome": ("kg_outcomes", "outcome_id"),
            "artifact": ("kg_artifacts", "artifact_id"),
        }
        if kind not in table_map:
            return
        # SqliteBackend-specific cleanup — guarded by attribute check so
        # FalkorBackend doesn't blow up; on Falkor rollback is a TODO.
        if not hasattr(backend, "_conn"):
            logger.warning(
                "Rollback for non-SQLite backend is not implemented — "
                "row kind=%s id=%s left in place. TODO: implement saga "
                "compensation for FalkorBackend.",
                kind,
                entity_id,
            )
            return
        table, col = table_map[kind]
        with backend._conn() as c:
            if kind == "workspace":
                c.execute(f"DELETE FROM {table} WHERE {col} = ?", (entity_id,))
            else:
                c.execute(
                    f"DELETE FROM {table} WHERE workspace_id = ? AND {col} = ?",
                    (self.workspace_id, entity_id),
                )

    def commit(self) -> None:
        self._committed = True

    def rollback(self) -> None:
        if self._committed:
            return
        for kind, entity_id, prior in reversed(self._pre_state):
            try:
                self._restore(kind, entity_id, prior)
            except Exception:  # pragma: no cover
                logger.exception("rollback step failed kind=%s id=%s", kind, entity_id)
        # Edge rollback: the SQLite kg_edges table has no PK we can target
        # for new inserts cleanly without journaling row ids. For MVP we
        # rely on the fact that downstream readers filter by edge_type +
        # source_entity_id and the entity is gone → the edge is orphaned
        # but unreachable. A future PR adds an `event_uuid` column to
        # kg_edges so we can DELETE by event.
        # TODO(P1-12): kg_edges event_uuid column + targeted rollback.

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.rollback()
            return False
        self.commit()
        return False


# ─────────────────────── payload models ───────────────────────


class OnboardingCompletePayload(BaseModel):
    """Inputs for §5.1 onboarding_complete."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    company_name: str
    industry: str
    stage: WorkspaceStage
    business_model: str
    kpi_tree: list[KpiTreeNode] = Field(default_factory=list)
    strategy: WorkspaceStrategy = Field(default_factory=WorkspaceStrategy)
    competitors: list[str] = Field(default_factory=list)
    initial_signals: list[dict[str, Any]] = Field(
        default_factory=list,
        description="3-10 pm-manual signals captured during onboarding (raw dicts; converted to Signal entities).",
    )
    bootstrap_hypotheses: list[dict[str, Any]] = Field(
        default_factory=list,
        description="2-3 cross-customer prior hypotheses (claim + predicted_metric + impact range).",
    )


class ConnectorSyncPayload(BaseModel):
    """Inputs for §5.2 connector_sync."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    connector: str = Field(..., description="amplitude / mixpanel / zendesk / linear / etc.")
    new_signals: list[dict[str, Any]] = Field(default_factory=list)
    updated_signal_ids: list[str] = Field(
        default_factory=list,
        description="Signals that got fresh evidence in this sync — valid_at bumped, cited_by re-evaluated.",
    )


class SynthesisAgentRunPayload(BaseModel):
    """Inputs for §5.3 synthesis_agent_run."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    brief_id: str
    recommendations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="One per Brief rec; each becomes a Hypothesis.",
    )


class BriefRecommendationDismissedPayload(BaseModel):
    """Inputs for §5.4 brief_recommendation_dismissed."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    hypothesis_id: str
    dismissed_reason: DismissedReason
    pm_note: Optional[str] = None


class BriefRecommendationApprovedPayload(BaseModel):
    """Inputs for §5.5 brief_recommendation_approved."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    hypothesis_id: str
    approved_by_user_id: str
    reasoning: str = Field(..., min_length=10)


class PrdGeneratedPayload(BaseModel):
    """Inputs for §5.6 prd_generated."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    decision_id: str
    feature_id: str
    prd_json: dict[str, Any]


class ArtifactEditPayload(BaseModel):
    """Inputs for §5.7 artifact_edit. The delta classifier is invoked
    inside the event — pass `classifier_override` to inject a mock."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    artifact_id: str
    edit_description: str = Field(..., min_length=1)
    edit_payload: dict[str, Any] = Field(default_factory=dict)
    edit_distance: int = Field(default=1, ge=1)


class FeatureShippedPayload(BaseModel):
    """Inputs for §5.8 feature_shipped."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    decision_id: str
    feature_name: str
    metric_measured: str
    shipped_at: Optional[datetime] = None


class OutcomeMeasuredPayload(BaseModel):
    """Inputs for §5.9 outcome_measured."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    outcome_id: str
    actual_impact: float
    measured_at: Optional[datetime] = None
    pm_annotation: Optional[str] = None


# ─────────────────────── delta classifier (rule-based stub) ───────────────────────


class _DeltaCategory:
    CONTEXT_GAP = "context_gap"
    PREFERENCE = "preference"
    RECURRING_PATTERN = "recurring_pattern"
    OTHER = "other"


def _default_delta_classifier(payload: ArtifactEditPayload) -> str:
    """Rule-based stub. Returns one of _DeltaCategory.*.

    The eventual implementation calls Claude with the edit + prior
    artifact context. For now we look at keywords in the description —
    sufficient for testing the routing logic.
    """
    text = payload.edit_description.lower()
    if any(k in text for k in ("missing", "context", "background", "evidence", "data on")):
        return _DeltaCategory.CONTEXT_GAP
    if any(k in text for k in ("prefer", "style", "tone", "format", "always use")):
        return _DeltaCategory.PREFERENCE
    if any(k in text for k in ("again", "same as", "third time", "pattern")):
        return _DeltaCategory.RECURRING_PATTERN
    return _DeltaCategory.OTHER


# Threshold for promoting a recurring_pattern delta into a candidate
# Hypothesis. Spec §5.7 says "3+ matches" — we count by hashing edit_description.
RECURRING_PATTERN_MIN_HITS: int = 3


# ─────────────────────── event 5.1 onboarding_complete ───────────────────────


def event_5_1_onboarding_complete(
    graph: GraphFacade, payload: OnboardingCompletePayload
) -> dict[str, Any]:
    """§5.1 — Create Workspace + 3-10 pm-manual Signals + 2-3 bootstrap Hypotheses.

    Returns a dict with the IDs of everything created (useful for the
    HTTP layer that called us and wants to echo IDs back).
    """
    ws_id = payload.workspace_id
    created_signal_ids: list[str] = []
    created_hypothesis_ids: list[str] = []
    val, txn = _bitemporal_pair()
    now = txn

    journal = _WriteJournal(graph, ws_id)
    try:
        # 1) Workspace
        journal.snapshot("workspace", ws_id)
        ws = Workspace(
            workspace_id=ws_id,
            valid_at=val,
            transaction_at=txn,
            company_name=payload.company_name,
            industry=payload.industry,
            stage=payload.stage,
            business_model=payload.business_model,
            kpi_tree=payload.kpi_tree,
            strategy=payload.strategy,
            competitors=payload.competitors,
            created_at=now,
            updated_at=now,
        )
        graph.write_workspace(ws_id, ws)

        # 2) Initial pm-manual signals (3-10 per spec; we accept any count
        # >=0 here — the HTTP validator owns the lower bound).
        for raw in payload.initial_signals:
            sig_id = raw.get("signal_id") or _new_id("sig-onb")
            content = raw["content"]
            source_type = SignalSourceType(raw.get("source_type", SignalSourceType.MANUAL.value))
            source_tool = raw.get("source_tool", "manual")
            v, t = _bitemporal_pair()
            sig = Signal(
                workspace_id=ws_id,
                valid_at=v,
                transaction_at=t,
                signal_id=sig_id,
                content=content,
                source_type=source_type,
                source_tool=source_tool,
                provenance_tag=ProvenanceTag.PM_MANUAL,
                confidence=float(raw.get("confidence", 0.6)),
                stale_after=_utcnow() + timedelta(days=60),  # spec §5.1: pm-manual = +60d
                kpi_relevance=list(raw.get("kpi_relevance", [])),
            )
            journal.snapshot("signal", sig_id)
            graph.write_signal(ws_id, sig)
            created_signal_ids.append(sig_id)

        # 3) Cross-customer bootstrap hypotheses (2-3 per spec; confidence
        # 0.3, status=candidate, agent_inferred provenance).
        for raw in payload.bootstrap_hypotheses:
            hyp_id = raw.get("hypothesis_id") or _new_id("hyp-boot")
            v, t = _bitemporal_pair()
            # Bootstrap hypotheses need at least one supporting signal —
            # the spec implies a synthetic "cross-customer prior" signal.
            # If the caller didn't pass evidence_signal_ids, we attach
            # the first onboarding signal (best-effort wiring).
            evidence_ids = list(raw.get("evidence_signal_ids", []))
            if not evidence_ids and created_signal_ids:
                evidence_ids = [created_signal_ids[0]]
            if not evidence_ids:
                # Synthesize a placeholder signal so the Pydantic
                # min_length=1 invariant on evidence_signal_ids holds.
                # This is a documented compromise — bootstrap hypotheses
                # carry an explicit cross-customer-prior reference.
                placeholder_id = _new_id("sig-prior")
                ps_v, ps_t = _bitemporal_pair()
                placeholder = Signal(
                    workspace_id=ws_id,
                    valid_at=ps_v,
                    transaction_at=ps_t,
                    signal_id=placeholder_id,
                    content=f"Cross-customer prior: {raw.get('claim', '')[:80]}",
                    source_type=SignalSourceType.AGENT_INFERRED,
                    source_tool="bootstrap",
                    provenance_tag=ProvenanceTag.AGENT_INFERRED,
                    confidence=0.3,
                    stale_after=_utcnow() + timedelta(days=14),
                )
                journal.snapshot("signal", placeholder_id)
                graph.write_signal(ws_id, placeholder)
                created_signal_ids.append(placeholder_id)
                evidence_ids = [placeholder_id]
            hyp = Hypothesis(
                workspace_id=ws_id,
                valid_at=v,
                transaction_at=t,
                hypothesis_id=hyp_id,
                claim=raw["claim"],
                predicted_metric=raw.get("predicted_metric", "tbd"),
                predicted_impact_low=float(raw.get("predicted_impact_low", 1.0)),
                predicted_impact_high=float(raw.get("predicted_impact_high", 3.0)),
                predicted_impact_basis=raw.get(
                    "predicted_impact_basis",
                    "Cross-customer prior — refine after first connector sync.",
                ),
                status=HypothesisStatus.CANDIDATE,
                evidence_signal_ids=evidence_ids,
                evidence_count=len(evidence_ids),
                confidence_composite=0.3,
                confidence_tier=ConfidenceTier.LOW,
                reversal_condition=raw.get(
                    "reversal_condition",
                    "If first connector sync shows no signal pattern match, drop this prior.",
                ),
                created_at=now,
                status_updated_at=now,
            )
            journal.snapshot("hypothesis", hyp_id)
            graph.write_hypothesis(ws_id, hyp)
            created_hypothesis_ids.append(hyp_id)

        journal.commit()
        return {
            "workspace_id": ws_id,
            "signal_ids": created_signal_ids,
            "hypothesis_ids": created_hypothesis_ids,
        }
    except Exception as exc:
        journal.rollback()
        raise GraphError(f"event_5_1_onboarding_complete failed: {exc}") from exc


# ─────────────────────── event 5.2 connector_sync ───────────────────────


def _maybe_promote_candidate_to_proposed(
    graph: GraphFacade, workspace_id: str, hyp: Hypothesis
) -> Optional[Hypothesis]:
    """Spec invariant: candidate → proposed needs evidence_count ≥3
    AND ≥2 distinct source_types. Returns the updated Hypothesis if
    promoted, otherwise None.
    """
    if hyp.status != HypothesisStatus.CANDIDATE:
        return None
    if hyp.evidence_count < 3:
        return None
    # Count distinct source_types across the linked signals.
    seen_types: set[str] = set()
    for sig_id in hyp.evidence_signal_ids:
        sig = graph.get_signal(workspace_id, sig_id)
        if sig is None:
            continue
        seen_types.add(sig.source_type.value)
    if len(seen_types) < 2:
        return None
    now = _utcnow()
    promoted = hyp.model_copy(
        update={
            "status": HypothesisStatus.PROPOSED,
            "status_updated_at": now,
            "transaction_at": now,
        }
    )
    return promoted


def event_5_2_connector_sync(
    graph: GraphFacade, payload: ConnectorSyncPayload
) -> dict[str, Any]:
    """§5.2 — Ingest connector data. New Signals + bumped existing Signals
    + recompute Hypothesis evidence_count, possibly promoting candidate→proposed.
    """
    ws_id = payload.workspace_id
    created: list[str] = []
    bumped: list[str] = []
    promoted: list[str] = []

    journal = _WriteJournal(graph, ws_id)
    try:
        # 1) New signals — entity-resolve each one against existing.
        for raw in payload.new_signals:
            sig_id = raw.get("signal_id") or _new_id("sig-sync")
            content = raw["content"]
            source_type = SignalSourceType(raw["source_type"])
            source_tool = raw.get("source_tool", payload.connector)
            v, t = _bitemporal_pair()
            candidate = Signal(
                workspace_id=ws_id,
                valid_at=v,
                transaction_at=t,
                signal_id=sig_id,
                content=content,
                source_type=source_type,
                source_tool=source_tool,
                provenance_tag=ProvenanceTag.CONNECTOR_INGEST,
                confidence=float(raw.get("confidence", 0.7)),
                stale_after=_stale_after_for(source_type, ProvenanceTag.CONNECTOR_INGEST),
                kpi_relevance=list(raw.get("kpi_relevance", [])),
            )
            existing, was_new = resolve_or_create_signal(graph, ws_id, candidate)
            if was_new:
                journal.snapshot("signal", sig_id)
                graph.write_signal(ws_id, candidate)
                created.append(sig_id)
            else:
                # Bump existing — refresh valid_at and merge cited_by.
                journal.snapshot("signal", existing.signal_id)
                bumped_sig = existing.model_copy(
                    update={
                        "valid_at": candidate.valid_at,
                        "transaction_at": candidate.transaction_at,
                        "confidence": max(existing.confidence, candidate.confidence),
                        "stale_after": _stale_after_for(
                            existing.source_type, existing.provenance_tag
                        ),
                    }
                )
                graph.write_signal(ws_id, bumped_sig)
                bumped.append(existing.signal_id)

        # 2) Caller-explicit updated_signal_ids: just refresh valid_at.
        for sig_id in payload.updated_signal_ids:
            existing = graph.get_signal(ws_id, sig_id)
            if existing is None:
                continue
            journal.snapshot("signal", sig_id)
            v, t = _bitemporal_pair()
            graph.write_signal(
                ws_id,
                existing.model_copy(
                    update={
                        "valid_at": v,
                        "transaction_at": t,
                        "stale_after": _stale_after_for(
                            existing.source_type, existing.provenance_tag
                        ),
                    }
                ),
            )
            if sig_id not in bumped:
                bumped.append(sig_id)

        # 3) Recompute hypothesis evidence_count from current SUPPORTS edges.
        #    Promote candidate→proposed where invariant is met.
        backend = graph._backend
        for hyp_id in backend.all_entity_ids(ws_id).get("hypotheses", []):
            hyp = graph.get_hypothesis(ws_id, hyp_id)
            if hyp is None:
                continue
            incoming = graph.edges_to(ws_id, hyp_id, edge_type=EdgeType.SUPPORTS)
            evidence_ids = sorted({e.source_entity_id for e in incoming})
            if not evidence_ids:
                continue
            new_count = len(evidence_ids)
            if (
                new_count == hyp.evidence_count
                and set(evidence_ids) == set(hyp.evidence_signal_ids)
            ):
                # No change — still check for promotion since count may
                # be at threshold from a prior write.
                pass
            else:
                journal.snapshot("hypothesis", hyp_id)
                hyp = hyp.model_copy(
                    update={
                        "evidence_count": new_count,
                        "evidence_signal_ids": evidence_ids,
                        "transaction_at": _utcnow(),
                    }
                )
                graph.write_hypothesis(ws_id, hyp)
            maybe = _maybe_promote_candidate_to_proposed(graph, ws_id, hyp)
            if maybe is not None:
                # snapshot already captured above (or earlier snapshot still valid)
                graph.write_hypothesis(ws_id, maybe)
                promoted.append(hyp_id)

        journal.commit()
        return {"created": created, "bumped": bumped, "promoted": promoted}
    except Exception as exc:
        journal.rollback()
        raise GraphError(f"event_5_2_connector_sync failed: {exc}") from exc


# ─────────────────────── event 5.3 synthesis_agent_run ───────────────────────


def event_5_3_synthesis_agent_run(
    graph: GraphFacade, payload: SynthesisAgentRunPayload
) -> dict[str, Any]:
    """§5.3 — Reads workspace + active hypotheses + recent decisions +
    measured outcomes BEFORE writing. Creates one Hypothesis per Brief rec.
    """
    ws_id = payload.workspace_id

    # 1) READ BEFORE WRITE — explicit, spec-mandated.
    context = graph.load_session_context(ws_id)
    if context["workspace"] is None:
        raise GraphError(f"synthesis_agent_run: workspace {ws_id} not found")

    # The reads are used by the Synthesis agent upstream; here we just
    # log them for audit. Real ds-agent integration lands in a follow-up.
    logger.info(
        "synthesis_agent_run brief=%s ws=%s active_hyp=%d recent_dec=%d recent_out=%d",
        payload.brief_id,
        ws_id,
        len(context["active_hypotheses"]),
        len(context["recent_decisions"]),
        len(context["recent_outcomes"]),
    )

    created_hypothesis_ids: list[str] = []
    journal = _WriteJournal(graph, ws_id)
    try:
        now = _utcnow()
        for idx, rec in enumerate(payload.recommendations):
            hyp_id = rec.get("hypothesis_id") or _new_id("hyp-syn")
            v, t = _bitemporal_pair()
            evidence_ids = list(rec.get("evidence_signal_ids", []))
            if not evidence_ids:
                # Synthesis without evidence is a spec violation upstream;
                # we still need >=1 to satisfy Pydantic. Use a sentinel
                # the maintenance sweep can flag.
                logger.warning(
                    "synthesis_agent_run rec idx=%d has no evidence_signal_ids; using sentinel",
                    idx,
                )
                evidence_ids = ["sig-missing-evidence"]
            confidence = float(rec.get("confidence_composite", 0.65))
            hyp = Hypothesis(
                workspace_id=ws_id,
                valid_at=v,
                transaction_at=t,
                hypothesis_id=hyp_id,
                claim=rec["claim"],
                predicted_metric=rec.get("predicted_metric", "tbd"),
                predicted_impact_low=float(rec.get("predicted_impact_low", 1.0)),
                predicted_impact_high=float(rec.get("predicted_impact_high", 3.0)),
                predicted_impact_basis=rec.get(
                    "predicted_impact_basis",
                    "Inferred from synthesis agent — see ds_agent_finding_json.",
                ),
                status=HypothesisStatus.PROPOSED,
                evidence_signal_ids=evidence_ids,
                evidence_count=max(1, len(evidence_ids)),
                confidence_composite=confidence,
                confidence_tier=_confidence_tier(confidence),
                reversal_condition=rec.get(
                    "reversal_condition",
                    "If post-launch metric moves opposite the prediction by >1pp, revert.",
                ),
                created_at=now,
                status_updated_at=now,
                brief_id=payload.brief_id,
                brief_rank=rec.get("brief_rank"),
                ds_agent_finding_json=rec.get("ds_agent_finding_json"),
            )
            journal.snapshot("hypothesis", hyp_id)
            graph.write_hypothesis(ws_id, hyp)
            created_hypothesis_ids.append(hyp_id)
            # Mint a SUPPORTS edge per evidence signal so the Brief
            # display + maintenance sweep agree on evidence count.
            for sig_id in evidence_ids:
                if sig_id == "sig-missing-evidence":
                    continue
                if graph.get_signal(ws_id, sig_id) is None:
                    continue
                ev, et = _bitemporal_pair()
                graph.write_edge(
                    ws_id,
                    Edge(
                        workspace_id=ws_id,
                        valid_at=ev,
                        transaction_at=et,
                        edge_type=EdgeType.SUPPORTS,
                        source_entity_id=sig_id,
                        source_entity_type="Signal",
                        target_entity_id=hyp_id,
                        target_entity_type="Hypothesis",
                        source="synthesis_agent_run",
                        confidence=confidence,
                    ),
                )
        journal.commit()
        return {
            "hypothesis_ids": created_hypothesis_ids,
            "context_snapshot_sizes": {
                "active_hypotheses": len(context["active_hypotheses"]),
                "recent_decisions": len(context["recent_decisions"]),
                "recent_outcomes": len(context["recent_outcomes"]),
            },
        }
    except Exception as exc:
        journal.rollback()
        raise GraphError(f"event_5_3_synthesis_agent_run failed: {exc}") from exc


# ─────────────────────── event 5.4 brief_recommendation_dismissed ─────────


def event_5_4_brief_recommendation_dismissed(
    graph: GraphFacade, payload: BriefRecommendationDismissedPayload
) -> dict[str, Any]:
    """§5.4 — Hypothesis.status=rejected + write a pm-manual learning Signal."""
    ws_id = payload.workspace_id
    hyp = graph.get_hypothesis(ws_id, payload.hypothesis_id)
    if hyp is None:
        raise GraphError(
            f"event_5_4: hypothesis {payload.hypothesis_id} not found in {ws_id}"
        )

    journal = _WriteJournal(graph, ws_id)
    try:
        now = _utcnow()
        # 1) Set status=rejected on the hypothesis.
        journal.snapshot("hypothesis", payload.hypothesis_id)
        rejected = hyp.model_copy(
            update={
                "status": HypothesisStatus.REJECTED,
                "status_updated_at": now,
                "transaction_at": now,
                "dismissed_reason": payload.dismissed_reason,
            }
        )
        graph.write_hypothesis(ws_id, rejected)

        # 2) Write a pm-manual learning Signal that captures the dismissal.
        learning_id = _new_id("sig-learn")
        v, t = _bitemporal_pair()
        learning = Signal(
            workspace_id=ws_id,
            valid_at=v,
            transaction_at=t,
            signal_id=learning_id,
            content=(
                f"PM dismissed hypothesis {payload.hypothesis_id}: "
                f"reason={payload.dismissed_reason.value}. "
                f"Note: {payload.pm_note or '(none)'}"
            ),
            source_type=SignalSourceType.MANUAL,
            source_tool="manual",
            provenance_tag=ProvenanceTag.PM_MANUAL,
            confidence=0.7,
            stale_after=_utcnow() + timedelta(days=60),
            kpi_relevance=[],
        )
        journal.snapshot("signal", learning_id)
        graph.write_signal(ws_id, learning)
        journal.commit()
        return {
            "hypothesis_id": payload.hypothesis_id,
            "learning_signal_id": learning_id,
        }
    except Exception as exc:
        journal.rollback()
        raise GraphError(f"event_5_4_brief_recommendation_dismissed failed: {exc}") from exc


# ─────────────────────── event 5.5 brief_recommendation_approved ─────────


def event_5_5_brief_recommendation_approved(
    graph: GraphFacade, payload: BriefRecommendationApprovedPayload
) -> dict[str, Any]:
    """§5.5 — Hypothesis.status=confirmed + Decision (with frozen snapshots) + PROMOTED_TO edge."""
    ws_id = payload.workspace_id
    hyp = graph.get_hypothesis(ws_id, payload.hypothesis_id)
    if hyp is None:
        raise GraphError(
            f"event_5_5: hypothesis {payload.hypothesis_id} not found in {ws_id}"
        )
    ws = graph.get_workspace(ws_id)
    if ws is None:
        raise GraphError(f"event_5_5: workspace {ws_id} not found")

    journal = _WriteJournal(graph, ws_id)
    try:
        now = _utcnow()

        # 1) Confirm the hypothesis.
        journal.snapshot("hypothesis", payload.hypothesis_id)
        confirmed = hyp.model_copy(
            update={
                "status": HypothesisStatus.CONFIRMED,
                "status_updated_at": now,
                "transaction_at": now,
            }
        )
        graph.write_hypothesis(ws_id, confirmed)

        # 2) Build evidence_snapshot: gather all SUPPORTS signals + their
        #    confidence at this moment. Frozen at approval time per spec.
        evidence_snapshot: dict[str, Any] = {
            "hypothesis_id": payload.hypothesis_id,
            "signals": [],
        }
        for sig_id in confirmed.evidence_signal_ids:
            sig = graph.get_signal(ws_id, sig_id)
            if sig is None:
                continue
            evidence_snapshot["signals"].append(
                {
                    "signal_id": sig.signal_id,
                    "content": sig.content,
                    "confidence": sig.confidence,
                    "source_type": sig.source_type.value,
                    "source_tool": sig.source_tool,
                }
            )

        # 3) Create the Decision — promoted_from_hypothesis_id REQUIRED.
        dec_id = _new_id("dec")
        v, t = _bitemporal_pair()
        decision = Decision(
            workspace_id=ws_id,
            valid_at=v,
            transaction_at=t,
            decision_id=dec_id,
            promoted_from_hypothesis_id=payload.hypothesis_id,
            claim=confirmed.claim,
            reasoning=payload.reasoning,
            approved_by_user_id=payload.approved_by_user_id,
            approved_at=now,
            evidence_snapshot=evidence_snapshot,
            kpi_tree_snapshot=list(ws.kpi_tree),
            reversal_condition=confirmed.reversal_condition,
        )
        journal.snapshot("decision", dec_id)
        graph.promote_hypothesis_to_decision(ws_id, decision)

        # Back-link from hypothesis.
        journal.snapshot("hypothesis", payload.hypothesis_id)
        graph.write_hypothesis(
            ws_id,
            confirmed.model_copy(
                update={
                    "promoted_to_decision_id": dec_id,
                    "transaction_at": _utcnow(),
                }
            ),
        )

        # 4) PROMOTED_TO edge.
        ev, et = _bitemporal_pair()
        graph.write_edge(
            ws_id,
            Edge(
                workspace_id=ws_id,
                valid_at=ev,
                transaction_at=et,
                edge_type=EdgeType.PROMOTED_TO,
                source_entity_id=payload.hypothesis_id,
                source_entity_type="Hypothesis",
                target_entity_id=dec_id,
                target_entity_type="Decision",
                source="brief_recommendation_approved",
                confidence=confirmed.confidence_composite,
            ),
        )
        journal.commit()
        return {"decision_id": dec_id, "hypothesis_id": payload.hypothesis_id}
    except Exception as exc:
        journal.rollback()
        raise GraphError(f"event_5_5_brief_recommendation_approved failed: {exc}") from exc


# ─────────────────────── event 5.6 prd_generated ───────────────────────


def event_5_6_prd_generated(
    graph: GraphFacade, payload: PrdGeneratedPayload
) -> dict[str, Any]:
    """§5.6 — Set Decision.feature_id + prd_generated_at + create Artifact + MOTIVATED edge."""
    ws_id = payload.workspace_id
    dec = graph.get_decision(ws_id, payload.decision_id)
    if dec is None:
        raise GraphError(f"event_5_6: decision {payload.decision_id} not found in {ws_id}")

    journal = _WriteJournal(graph, ws_id)
    try:
        now = _utcnow()
        # Decision is immutable EXCEPT for the spec-allowed late-bind
        # fields: feature_id, prd_generated_at, outcome_id. We use
        # model_copy + facade.promote_hypothesis_to_decision, which
        # tolerates same evidence_snapshot.
        journal.snapshot("decision", payload.decision_id)
        updated_dec = dec.model_copy(
            update={
                "feature_id": payload.feature_id,
                "prd_generated_at": now,
                "transaction_at": now,
            }
        )
        # Re-promote: facade enforces evidence_snapshot is unchanged —
        # which it is, since we only touched the late-bind fields.
        graph.promote_hypothesis_to_decision(ws_id, updated_dec)

        # Create the Artifact as v1 — edit-distance baseline.
        art_id = _new_id("art-prd")
        v, t = _bitemporal_pair()
        artifact = Artifact(
            workspace_id=ws_id,
            valid_at=v,
            transaction_at=t,
            artifact_id=art_id,
            artifact_type=ArtifactType.PRD,
            version=1,
            agent_output_snapshot=payload.prd_json,
            current_version=1,
            edit_distance_from_v1=0,
            source_decision_id=payload.decision_id,
            source_hypothesis_id=dec.promoted_from_hypothesis_id,
        )
        journal.snapshot("artifact", art_id)
        graph.write_artifact(ws_id, artifact)

        # MOTIVATED edge: Decision → Artifact (spec §4).
        ev, et = _bitemporal_pair()
        graph.write_edge(
            ws_id,
            Edge(
                workspace_id=ws_id,
                valid_at=ev,
                transaction_at=et,
                edge_type=EdgeType.MOTIVATED,
                source_entity_id=payload.decision_id,
                source_entity_type="Decision",
                target_entity_id=art_id,
                target_entity_type="Artifact",
                source="prd_generated",
                confidence=1.0,
            ),
        )
        journal.commit()
        return {"artifact_id": art_id, "decision_id": payload.decision_id}
    except Exception as exc:
        journal.rollback()
        raise GraphError(f"event_5_6_prd_generated failed: {exc}") from exc


# ─────────────────────── event 5.7 artifact_edit ───────────────────────


# Module-level counter for recurring-pattern detection. Keyed by
# (workspace_id, edit_description_hash). Reset between processes —
# good enough for MVP, replaced by graph query when Cognee lands.
_RECURRING_PATTERN_COUNTS: dict[tuple[str, str], int] = {}


def _pattern_key(workspace_id: str, edit_description: str) -> tuple[str, str]:
    # Normalize whitespace + lowercase for a slightly tolerant match.
    norm = " ".join(edit_description.lower().split())
    return workspace_id, norm


def event_5_7_artifact_edit(
    graph: GraphFacade,
    payload: ArtifactEditPayload,
    classifier_override: Optional[Callable[[ArtifactEditPayload], str]] = None,
) -> dict[str, Any]:
    """§5.7 — Classify the edit, route the write-back, bump artifact version+distance.

    `classifier_override` lets tests inject a deterministic classifier
    without touching the LLM seam.
    """
    ws_id = payload.workspace_id
    art = graph.get_artifact(ws_id, payload.artifact_id)
    if art is None:
        raise GraphError(f"event_5_7: artifact {payload.artifact_id} not found in {ws_id}")

    classifier = classifier_override or _default_delta_classifier
    category = classifier(payload)

    journal = _WriteJournal(graph, ws_id)
    try:
        now = _utcnow()

        # 1) Bump artifact: current_version + edit_distance_from_v1.
        journal.snapshot("artifact", payload.artifact_id)
        updated_art = art.model_copy(
            update={
                "current_version": art.current_version + 1,
                "edit_distance_from_v1": art.edit_distance_from_v1 + payload.edit_distance,
                "transaction_at": now,
            }
        )
        graph.write_artifact(ws_id, updated_art)

        write_back: dict[str, Any] = {"category": category}

        # 2) Route by category.
        if category == _DeltaCategory.CONTEXT_GAP:
            sig_id = _new_id("sig-gap")
            v, t = _bitemporal_pair()
            sig = Signal(
                workspace_id=ws_id,
                valid_at=v,
                transaction_at=t,
                signal_id=sig_id,
                content=f"Context gap from artifact edit: {payload.edit_description}",
                source_type=SignalSourceType.AGENT_INFERRED,
                source_tool="delta_classifier",
                provenance_tag=ProvenanceTag.AGENT_INFERRED,
                confidence=0.6,
                stale_after=_utcnow() + timedelta(days=14),
            )
            journal.snapshot("signal", sig_id)
            graph.write_signal(ws_id, sig)
            write_back["signal_id"] = sig_id

        elif category == _DeltaCategory.PREFERENCE:
            ws = graph.get_workspace(ws_id)
            if ws is not None:
                journal.snapshot("workspace", ws_id)
                prefs = dict(ws.preferences)
                # Key the preference by artifact_type for future agent runs.
                bucket = prefs.setdefault(art.artifact_type.value, [])
                if not isinstance(bucket, list):
                    bucket = [bucket]
                bucket.append(payload.edit_description)
                prefs[art.artifact_type.value] = bucket
                graph.write_workspace(
                    ws_id,
                    ws.model_copy(
                        update={
                            "preferences": prefs,
                            "transaction_at": _utcnow(),
                            "updated_at": _utcnow(),
                        }
                    ),
                )
                write_back["preferences_updated"] = True

        elif category == _DeltaCategory.RECURRING_PATTERN:
            key = _pattern_key(ws_id, payload.edit_description)
            _RECURRING_PATTERN_COUNTS[key] = _RECURRING_PATTERN_COUNTS.get(key, 0) + 1
            hits = _RECURRING_PATTERN_COUNTS[key]
            write_back["pattern_hits"] = hits
            if hits >= RECURRING_PATTERN_MIN_HITS:
                # Create a candidate Hypothesis with synthetic evidence.
                hyp_id = _new_id("hyp-pat")
                # We need a signal to attach as evidence (Pydantic min_length=1).
                seed_sig_id = _new_id("sig-pat")
                sv, st = _bitemporal_pair()
                seed = Signal(
                    workspace_id=ws_id,
                    valid_at=sv,
                    transaction_at=st,
                    signal_id=seed_sig_id,
                    content=f"Recurring artifact-edit pattern ({hits}x): {payload.edit_description}",
                    source_type=SignalSourceType.AGENT_INFERRED,
                    source_tool="delta_classifier",
                    provenance_tag=ProvenanceTag.AGENT_INFERRED,
                    confidence=0.6,
                    stale_after=_utcnow() + timedelta(days=14),
                )
                journal.snapshot("signal", seed_sig_id)
                graph.write_signal(ws_id, seed)
                v, t = _bitemporal_pair()
                hyp = Hypothesis(
                    workspace_id=ws_id,
                    valid_at=v,
                    transaction_at=t,
                    hypothesis_id=hyp_id,
                    claim=(
                        "Recurring PM edit on PRDs: "
                        f"{payload.edit_description[:200]}"
                    ),
                    predicted_metric="prd_edit_distance_v1",
                    predicted_impact_low=0.0,
                    predicted_impact_high=1.0,
                    predicted_impact_basis=(
                        f"Observed {hits}+ matching edits on artifacts of type "
                        f"{art.artifact_type.value}; treat as a candidate prior."
                    ),
                    status=HypothesisStatus.CANDIDATE,
                    evidence_signal_ids=[seed_sig_id],
                    evidence_count=1,
                    confidence_composite=0.4,
                    confidence_tier=ConfidenceTier.LOW,
                    reversal_condition=(
                        "If pattern stops matching for 30 days, drop the prior."
                    ),
                    created_at=now,
                    status_updated_at=now,
                )
                journal.snapshot("hypothesis", hyp_id)
                graph.write_hypothesis(ws_id, hyp)
                write_back["candidate_hypothesis_id"] = hyp_id

        # _DeltaCategory.OTHER → no write-back, only artifact bump.

        journal.commit()
        return {
            "artifact_id": payload.artifact_id,
            "new_version": updated_art.current_version,
            "edit_distance_from_v1": updated_art.edit_distance_from_v1,
            "delta_category": category,
            **write_back,
        }
    except Exception as exc:
        journal.rollback()
        raise GraphError(f"event_5_7_artifact_edit failed: {exc}") from exc


# ─────────────────────── event 5.8 feature_shipped ───────────────────────


def event_5_8_feature_shipped(
    graph: GraphFacade, payload: FeatureShippedPayload
) -> dict[str, Any]:
    """§5.8 — Create Outcome (actual_impact=None) + RESULTED_IN edge + back-link on Decision.
    Triggers an async maintenance sweep."""
    ws_id = payload.workspace_id
    dec = graph.get_decision(ws_id, payload.decision_id)
    if dec is None:
        raise GraphError(f"event_5_8: decision {payload.decision_id} not found in {ws_id}")

    # The Outcome is anchored to the linked Artifact (RESULTED_IN goes
    # Artifact → Outcome per spec §4 / EDGE_DIRECTION_TABLE). Find the
    # PRD artifact for this decision.
    artifact_id: Optional[str] = None
    backend = graph._backend
    for aid in backend.all_entity_ids(ws_id).get("artifacts", []):
        art = graph.get_artifact(ws_id, aid)
        if art and art.source_decision_id == payload.decision_id:
            artifact_id = aid
            break

    journal = _WriteJournal(graph, ws_id)
    try:
        now = _utcnow()
        shipped_at = payload.shipped_at or now

        outcome_id = _new_id("out")
        v, t = _bitemporal_pair()
        outcome = Outcome(
            workspace_id=ws_id,
            valid_at=v,
            transaction_at=t,
            outcome_id=outcome_id,
            linked_decision_id=payload.decision_id,
            linked_hypothesis_id=dec.promoted_from_hypothesis_id,
            linked_signal_ids=[
                s["signal_id"]
                for s in dec.evidence_snapshot.get("signals", [])
                if "signal_id" in s
            ],
            feature_name=payload.feature_name,
            shipped_at=shipped_at,
            metric_measured=payload.metric_measured,
            predicted_impact_low=0.0,  # copied from Decision at creation per spec
            predicted_impact_high=0.0,
            # actual_impact intentionally None — fills at §5.9.
        )
        # Spec §3.5 says predicted_impact_low/high are copied at Decision
        # CREATION not at ship time. We pull them off the Hypothesis the
        # Decision was promoted from.
        src_hyp = graph.get_hypothesis(ws_id, dec.promoted_from_hypothesis_id)
        if src_hyp is not None:
            outcome = outcome.model_copy(
                update={
                    "predicted_impact_low": src_hyp.predicted_impact_low,
                    "predicted_impact_high": src_hyp.predicted_impact_high,
                }
            )

        journal.snapshot("outcome", outcome_id)
        graph.write_outcome(ws_id, outcome)

        # Back-link on Decision (allowed late-bind: outcome_id).
        journal.snapshot("decision", payload.decision_id)
        graph.promote_hypothesis_to_decision(
            ws_id,
            dec.model_copy(update={"outcome_id": outcome_id, "transaction_at": _utcnow()}),
        )

        # RESULTED_IN edge: Artifact → Outcome.
        if artifact_id is not None:
            ev, et = _bitemporal_pair()
            graph.write_edge(
                ws_id,
                Edge(
                    workspace_id=ws_id,
                    valid_at=ev,
                    transaction_at=et,
                    edge_type=EdgeType.RESULTED_IN,
                    source_entity_id=artifact_id,
                    source_entity_type="Artifact",
                    target_entity_id=outcome_id,
                    target_entity_type="Outcome",
                    source="feature_shipped",
                    confidence=1.0,
                    metadata={"shipped_at": shipped_at.isoformat()},
                ),
            )
        else:
            logger.warning(
                "event_5_8: no Artifact found for decision=%s; skipping RESULTED_IN edge",
                payload.decision_id,
            )

        journal.commit()
        # Async maintenance sweep — fire-and-forget per spec §10.
        run_maintenance_sweep_async(graph, ws_id)
        return {"outcome_id": outcome_id, "artifact_id": artifact_id}
    except Exception as exc:
        journal.rollback()
        raise GraphError(f"event_5_8_feature_shipped failed: {exc}") from exc


# ─────────────────────── event 5.9 outcome_measured ───────────────────────


def event_5_9_outcome_measured(
    graph: GraphFacade, payload: OutcomeMeasuredPayload
) -> dict[str, Any]:
    """§5.9 — Fill actual_impact + prediction_hit/delta + VALIDATES + UPDATES_WEIGHT edges.
    Triggers async maintenance sweep."""
    ws_id = payload.workspace_id
    outcome = graph.get_outcome(ws_id, payload.outcome_id)
    if outcome is None:
        raise GraphError(f"event_5_9: outcome {payload.outcome_id} not found in {ws_id}")

    journal = _WriteJournal(graph, ws_id)
    try:
        now = payload.measured_at or _utcnow()

        # Compute prediction_hit + delta.
        prediction_hit = outcome.predicted_impact_low <= payload.actual_impact <= outcome.predicted_impact_high
        midpoint = (outcome.predicted_impact_low + outcome.predicted_impact_high) / 2.0
        prediction_delta = payload.actual_impact - midpoint

        journal.snapshot("outcome", payload.outcome_id)
        updated_outcome = outcome.model_copy(
            update={
                "actual_impact": payload.actual_impact,
                "actual_impact_measured_at": now,
                "prediction_hit": prediction_hit,
                "prediction_delta": prediction_delta,
                "pm_annotation": payload.pm_annotation,
                "transaction_at": _utcnow(),
            }
        )
        graph.write_outcome(ws_id, updated_outcome)

        # VALIDATES edge: Outcome → Hypothesis.
        ev, et = _bitemporal_pair()
        graph.write_edge(
            ws_id,
            Edge(
                workspace_id=ws_id,
                valid_at=ev,
                transaction_at=et,
                edge_type=EdgeType.VALIDATES,
                source_entity_id=payload.outcome_id,
                source_entity_type="Outcome",
                target_entity_id=outcome.linked_hypothesis_id,
                target_entity_type="Hypothesis",
                source="outcome_measured",
                confidence=1.0,
                metadata={
                    "prediction_hit": bool(prediction_hit),
                    "prediction_delta": float(prediction_delta),
                    "actual_impact": float(payload.actual_impact),
                },
            ),
        )

        # UPDATES_WEIGHT edges: Outcome → Signal (one per linked signal).
        for sig_id in outcome.linked_signal_ids:
            if graph.get_signal(ws_id, sig_id) is None:
                continue
            ev, et = _bitemporal_pair()
            graph.write_edge(
                ws_id,
                Edge(
                    workspace_id=ws_id,
                    valid_at=ev,
                    transaction_at=et,
                    edge_type=EdgeType.UPDATES_WEIGHT,
                    source_entity_id=payload.outcome_id,
                    source_entity_type="Outcome",
                    target_entity_id=sig_id,
                    target_entity_type="Signal",
                    source="outcome_measured",
                    confidence=1.0,
                    metadata={"prediction_hit": bool(prediction_hit)},
                ),
            )

        journal.commit()
        run_maintenance_sweep_async(graph, ws_id)
        return {
            "outcome_id": payload.outcome_id,
            "prediction_hit": bool(prediction_hit),
            "prediction_delta": float(prediction_delta),
        }
    except Exception as exc:
        journal.rollback()
        raise GraphError(f"event_5_9_outcome_measured failed: {exc}") from exc


# ─────────────────────── async maintenance sweep wrapper ───────────────────


def run_maintenance_sweep_async(graph: GraphFacade, workspace_id: str) -> Optional[asyncio.Task]:
    """Fire the maintenance sweep without blocking the caller.

    If there's a running event loop (FastAPI request context), schedule
    it as a task. Otherwise (CLI/tests) run synchronously — but caller
    is expected to await/observe explicitly.

    Returns the Task if scheduled, None if run synchronously.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop — synchronous fallback. This branch is only hit from
        # non-async test/CLI contexts; FastAPI handlers always have a loop.
        run_maintenance_sweep(graph, workspace_id)
        return None
    return loop.create_task(_run_maintenance_sweep_coro(graph, workspace_id))


async def _run_maintenance_sweep_coro(graph: GraphFacade, workspace_id: str) -> SweepReport:
    # The sweep is CPU-bound + SQLite-bound — run in a thread so we
    # don't block the loop. Falkor-bound writes will be async-native
    # when the backend lands.
    return await asyncio.to_thread(run_maintenance_sweep, graph, workspace_id)


# ─────────────────────── exports ───────────────────────


__all__ = [
    # event functions
    "event_5_1_onboarding_complete",
    "event_5_2_connector_sync",
    "event_5_3_synthesis_agent_run",
    "event_5_4_brief_recommendation_dismissed",
    "event_5_5_brief_recommendation_approved",
    "event_5_6_prd_generated",
    "event_5_7_artifact_edit",
    "event_5_8_feature_shipped",
    "event_5_9_outcome_measured",
    # async sweep
    "run_maintenance_sweep_async",
    # payloads
    "OnboardingCompletePayload",
    "ConnectorSyncPayload",
    "SynthesisAgentRunPayload",
    "BriefRecommendationDismissedPayload",
    "BriefRecommendationApprovedPayload",
    "PrdGeneratedPayload",
    "ArtifactEditPayload",
    "FeatureShippedPayload",
    "OutcomeMeasuredPayload",
    # constants
    "RECURRING_PATTERN_MIN_HITS",
]

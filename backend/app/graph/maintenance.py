"""Maintenance sweep — periodic graph upkeep.

Spec source: KG_Engineering_Spec §5.8 / §5.9 (triggers) + §8 (the sweep itself).

The sweep walks the workspace and does three things:
  1. **Expire stale signals** — `signal.stale_after < now()` → re-emit
     the Signal payload with provenance flag updated; downstream
     Hypothesis evidence_count is recomputed by step 3.
  2. **Update source reliability** — for every UPDATES_WEIGHT edge
     (minted by §5.9 outcome_measured), apply a delta to the relevant
     Signals' confidence. The §9 spec uses an exponentially-weighted
     update; for the transitional SQLite backend we apply a simpler
     linear nudge that's monotone in `prediction_hit`.
  3. **Recompute hypothesis evidence_count** from current SUPPORTS edges.
     This is the safety net for evidence_count drift caused by
     out-of-band Signal updates (e.g. partial connector_sync failures).

The sweep is **synchronous** here. The async wrapper that fires it
from write events 5.8 and 5.9 lives in `write_events.run_maintenance_sweep_async`.
Spec §10 says the sweep MUST never be awaited from a request handler —
that's enforced at the call-site, not here.

Engineering decision (Apurva): SweepReport returns counts only. We do
NOT return the affected entity IDs — at scale that list is huge, and
debug introspection should go through the entity_ids() helper instead.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.graph.edges import EdgeType
from app.graph.entities import Hypothesis, HypothesisStatus, ProvenanceTag, Signal
from app.graph.facade import GraphFacade

logger = logging.getLogger(__name__)


# Linear nudge applied per UPDATES_WEIGHT edge — capped so a single
# outcome can't flip a signal from 0→1 by itself. Replaced by the
# spec §9 EWMA when FalkorDB lands.
SIGNAL_RELIABILITY_DELTA_HIT: float = 0.05
SIGNAL_RELIABILITY_DELTA_MISS: float = -0.10


@dataclass
class SweepReport:
    """Summary of one maintenance sweep run."""

    workspace_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    expired_signals: int = 0
    updated_signal_weights: int = 0
    hypothesis_evidence_recomputed: int = 0
    errors: list[str] = field(default_factory=list)

    def duration_ms(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds() * 1000.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _expire_stale_signals(graph: GraphFacade, workspace_id: str, report: SweepReport) -> None:
    """Walk all active signals, mark expired (stale_after < now)."""
    now = _utcnow()
    # We can't call list_active_signals because that filters expired
    # ones out. Use the debug helper to enumerate IDs then fetch.
    backend = graph._backend  # internal access — maintenance is a privileged caller
    ids = backend.all_entity_ids(workspace_id).get("signals", [])
    for sig_id in ids:
        sig = graph.get_signal(workspace_id, sig_id)
        if sig is None:
            continue
        # outcome-measured signals never expire by spec invariant.
        if sig.provenance_tag == ProvenanceTag.OUTCOME_MEASURED:
            continue
        if sig.stale_after is not None and sig.stale_after < now:
            # We don't have a separate "expired" status field on Signal,
            # so the convention is: setting valid_at to stale_after marks
            # the moment the signal lapsed. We re-write to bump
            # transaction_at and let downstream queries treat it as stale.
            #
            # NOTE: list_active_signals filters by stale_after, so the
            # signal is already invisible to readers. This step is for
            # audit (transaction_at) and edge consistency, not visibility.
            try:
                updated = sig.model_copy(
                    update={"transaction_at": now}
                )
                graph.write_signal(workspace_id, updated)
                report.expired_signals += 1
            except Exception as exc:  # pragma: no cover — defensive
                report.errors.append(f"expire signal {sig_id}: {exc}")


def _update_signal_weights(
    graph: GraphFacade, workspace_id: str, report: SweepReport
) -> None:
    """For every UPDATES_WEIGHT edge, nudge the target Signal's confidence."""
    # We scan recent UPDATES_WEIGHT edges by sweeping all signals and
    # asking for incoming edges. Acceptable cost for SQLite/MVP scale.
    backend = graph._backend
    sig_ids = backend.all_entity_ids(workspace_id).get("signals", [])
    for sig_id in sig_ids:
        incoming = graph.edges_to(workspace_id, sig_id, edge_type=EdgeType.UPDATES_WEIGHT)
        if not incoming:
            continue
        sig = graph.get_signal(workspace_id, sig_id)
        if sig is None:
            continue
        delta = 0.0
        for edge in incoming:
            hit = bool(edge.metadata.get("prediction_hit", False))
            delta += SIGNAL_RELIABILITY_DELTA_HIT if hit else SIGNAL_RELIABILITY_DELTA_MISS
        new_conf = max(0.0, min(1.0, sig.confidence + delta))
        if new_conf == sig.confidence:
            continue
        try:
            updated = sig.model_copy(
                update={"confidence": new_conf, "transaction_at": _utcnow()}
            )
            graph.write_signal(workspace_id, updated)
            report.updated_signal_weights += 1
        except Exception as exc:  # pragma: no cover
            report.errors.append(f"update weight signal {sig_id}: {exc}")


def _recompute_hypothesis_evidence(
    graph: GraphFacade, workspace_id: str, report: SweepReport
) -> None:
    """For each Hypothesis, recompute evidence_count from current SUPPORTS edges."""
    backend = graph._backend
    hyp_ids = backend.all_entity_ids(workspace_id).get("hypotheses", [])
    for hyp_id in hyp_ids:
        hyp = graph.get_hypothesis(workspace_id, hyp_id)
        if hyp is None:
            continue
        incoming = graph.edges_to(workspace_id, hyp_id, edge_type=EdgeType.SUPPORTS)
        # evidence_count is # of distinct supporting signals (spec §5.2).
        evidence_ids = {e.source_entity_id for e in incoming}
        if not evidence_ids:
            # Leave it; never drop below 1 (Pydantic ge=1 invariant).
            continue
        new_count = max(1, len(evidence_ids))
        if new_count == hyp.evidence_count and set(hyp.evidence_signal_ids) == evidence_ids:
            continue
        try:
            updated = hyp.model_copy(
                update={
                    "evidence_count": new_count,
                    "evidence_signal_ids": sorted(evidence_ids),
                    "transaction_at": _utcnow(),
                    "status_updated_at": _utcnow(),
                }
            )
            graph.write_hypothesis(workspace_id, updated)
            report.hypothesis_evidence_recomputed += 1
        except Exception as exc:  # pragma: no cover
            report.errors.append(f"recompute hyp {hyp_id}: {exc}")


def run_maintenance_sweep(graph: GraphFacade, workspace_id: str) -> SweepReport:
    """Synchronous sweep. Use the async wrapper from write_events for §5.8/§5.9."""
    report = SweepReport(workspace_id=workspace_id, started_at=_utcnow())
    try:
        _expire_stale_signals(graph, workspace_id, report)
        _update_signal_weights(graph, workspace_id, report)
        _recompute_hypothesis_evidence(graph, workspace_id, report)
    except Exception as exc:  # pragma: no cover — defensive top-level guard
        report.errors.append(f"sweep top-level: {exc}")
        logger.exception("maintenance sweep failed for workspace=%s", workspace_id)
    finally:
        report.finished_at = _utcnow()
    logger.info(
        "maintenance_sweep ws=%s expired=%d weights=%d evidence=%d errors=%d dur_ms=%.1f",
        workspace_id,
        report.expired_signals,
        report.updated_signal_weights,
        report.hypothesis_evidence_recomputed,
        len(report.errors),
        report.duration_ms() or 0.0,
    )
    return report


__all__ = [
    "SweepReport",
    "run_maintenance_sweep",
    "SIGNAL_RELIABILITY_DELTA_HIT",
    "SIGNAL_RELIABILITY_DELTA_MISS",
]

"""DETECT — convergence computation over the brain (design §4 step 2).

Pure code, no LLM: for each theme, gather its inbound signal edges and
compute the quantitative dimensions of the §4c base score:
  - convergence breadth (distinct source_types agreeing)
  - effective evidence weight (confidence × source-accuracy weight × recency
    half-life decay, per #1)
  - revenue at stake (summed from signal properties)
  - competitive pressure (PRESSURES edges)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.graph.facade import GraphFacade
from app.graph.types import SOURCE_STALE_WINDOW_DAYS, Signal
from app.synthesis.scoring import voc_score

# Source types that represent REAL connected-source evidence (a connector sync
# or a corpus document run through the extractor), as opposed to onboarding /
# business-context / agent-inference SEEDED metadata. The seeded source types
# are pm_manual + agent_inferred (business_context_projection / ds anomaly
# inferences) and verbal_claim (unverified self-reported claims) — none of which
# represent a connected data source. Used by has_sufficient_evidence() to gate
# brief generation so a brand-new, source-less company gets an EMPTY brief
# instead of fabricated findings derived from onboarding metadata.
CONNECTED_SOURCE_TYPES: frozenset[str] = frozenset({
    "analytics", "project_mgmt", "communication", "customer_voice", "revenue",
    "outcome_measured",
})


@dataclass
class ThemeConvergence:
    theme_id: str
    theme_label: str
    signal_count: int = 0
    source_types: set[str] = field(default_factory=set)
    effective_weight: float = 0.0
    revenue_at_stake_usd: float = 0.0
    competitor_pressure: int = 0
    base_score: float = 0.0  # VoC Volume & Severity base (prioritize skill)
    evidence: list[dict] = field(default_factory=list)  # top signals for the LLM pass
    # Newest contributing signal's valid_at — the "is there fresher evidence?"
    # input to brief de-dup (synthesis/dedup.py). None when the theme has no
    # signals (it won't be emitted in that case).
    latest_signal_at: datetime | None = None
    # Distinct signals on this theme whose source_type is a connected source
    # (see CONNECTED_SOURCE_TYPES) — the real-evidence subset used by the
    # sufficiency gate. <= signal_count.
    connected_signal_count: int = 0
    # Provenance-origin counts (provenance["origin"], stamped at ingest by
    # extract_document). `upload` = PM-uploaded corpus doc; `connector` = a live
    # connector sync. Drive the UPLOAD-ONLY relaxation in the sufficiency gate:
    # when a tenant has zero connector-origin signals across all themes, the gate
    # treats its uploaded-doc evidence as good enough for a (single-source) brief
    # instead of an empty one. Both <= signal_count.
    upload_signal_count: int = 0
    connector_signal_count: int = 0

    @property
    def breadth(self) -> int:
        return len(self.source_types)

    @property
    def connected_breadth(self) -> int:
        """Distinct CONNECTED source types agreeing on this theme."""
        return len(self.source_types & CONNECTED_SOURCE_TYPES)


def _recency_factor(signal: Signal, now: datetime) -> float:
    """Half-life decay using the per-source_type window (#1). Never-expiring
    source types (outcome_measured) don't decay."""
    window = SOURCE_STALE_WINDOW_DAYS.get(signal.source_type)
    if not window:
        return 1.0
    age_days = max(0.0, (now - signal.valid_at).total_seconds() / 86400)
    return math.pow(0.5, age_days / window)


def compute_convergence(
    facade: GraphFacade,
    enterprise_id: str,
    *,
    max_evidence_per_theme: int = 8,
) -> list[ThemeConvergence]:
    """Score every theme by multi-source convergence. Returns themes sorted
    by (breadth, effective_weight) descending."""
    now = datetime.now(timezone.utc)
    themes = facade.query_entities(enterprise_id, type="theme")
    out: list[ThemeConvergence] = []

    # Batch ALL the graph reads up front instead of two queries per theme (the
    # old N+1: `edges_to` + `get_signals` inside the loop). Over a large theme
    # set — a mature tenant can carry 1000+ themes — the per-theme round-trips
    # made a first-time brief (no refresh-gate cache to fall back on) take
    # minutes and die on transient disconnects. One signal fetch + a few chunked
    # edge fetches, then all per-theme work is in-memory.
    theme_ids = {t.id for t in themes}
    signals_by_id = {s.id: s for s in facade.all_signals(enterprise_id)}
    edges_by_theme: dict[str, list] = {}
    for edge in facade.edges_from_many(enterprise_id, list(signals_by_id)):
        if edge.source_kind != "signal" or edge.target_id not in theme_ids:
            continue
        edges_by_theme.setdefault(edge.target_id, []).append(edge)

    for theme in themes:
        tc = ThemeConvergence(theme_id=theme.id, theme_label=theme.canonical_label)
        scored_evidence: list[tuple[float, dict]] = []
        # Dedup by source signal id (a signal can reach a theme via >1
        # relationship row, e.g. AFFECTS + PRESSURES). Without this, revenue /
        # signal_count / effective_weight double-count that signal. Mirrors the
        # `seen` dedup in evidence_kg's trail builder so the base score is
        # computed over DISTINCT signals.
        seen: set[str] = set()
        # Prebuilt above (edges_by_theme / signals_by_id) — no per-theme query.
        edges = edges_by_theme.get(theme.id, [])
        for edge in edges:
            if edge.source_kind != "signal" or edge.source_id in seen:
                continue
            sig = signals_by_id.get(edge.source_id)
            if sig is None or sig.properties.get("superseded_by"):
                continue
            seen.add(sig.id)
            w = sig.confidence * sig.weight * _recency_factor(sig, now)
            tc.signal_count += 1
            if tc.latest_signal_at is None or sig.valid_at > tc.latest_signal_at:
                tc.latest_signal_at = sig.valid_at
            tc.source_types.add(sig.source_type)
            if sig.source_type in CONNECTED_SOURCE_TYPES:
                tc.connected_signal_count += 1
            origin = (sig.provenance or {}).get("origin")
            if origin == "upload":
                tc.upload_signal_count += 1
            elif origin == "connector":
                tc.connector_signal_count += 1
            tc.effective_weight += w
            rev = sig.properties.get("revenue_at_risk_usd") or sig.properties.get("revenue_usd") or 0
            try:
                tc.revenue_at_stake_usd += float(rev)
            except (TypeError, ValueError):
                pass
            if edge.type == "PRESSURES" or sig.kind == "competitor_move":
                tc.competitor_pressure += 1
            scored_evidence.append((w, {
                "content": sig.content, "kind": sig.kind,
                "source_type": sig.source_type, "edge": edge.type,
                "weight": round(w, 3), "signal_id": sig.id,
            }))
        scored_evidence.sort(key=lambda t: -t[0])
        tc.evidence = [e for _, e in scored_evidence[:max_evidence_per_theme]]
        if tc.signal_count:
            # VoC Volume & Severity base score (prioritize skill), additive over
            # the computed dimensions: breadth of agreeing source types = volume,
            # mean per-signal evidence weight = severity × data-quality, with a
            # competitor-pressure trend bump. All factors land in 0..1 except the
            # trend modifier.
            tc.base_score = voc_score(
                impact=min(1.0, tc.breadth / 5.0),
                severity=min(1.0, tc.effective_weight / max(tc.signal_count, 1)),
                trend=1.0 + 0.1 * tc.competitor_pressure,
            )
            out.append(tc)

    out.sort(key=lambda t: (-t.breadth, -t.effective_weight))
    return out


def is_upload_only(convergence: list[ThemeConvergence]) -> bool:
    """Is this tenant's evidence UPLOAD-ONLY — i.e. derived solely from
    PM-uploaded corpus documents, with no live connector sources?

    True iff (a) at least one signal across all themes carries the ``upload``
    provenance origin (so there IS uploaded-doc evidence), AND (b) NO signal
    carries the ``connector`` origin (no live connector sync has contributed).
    The origin is stamped at ingest by extract_document; legacy / onboarding /
    research signals carry no origin and count as neither — so a tenant with
    only onboarding metadata is NOT upload-only (it has no upload-origin signal),
    and a tenant with any connector evidence is NOT upload-only. This is the
    narrowest provenance condition that distinguishes the upload-only case
    without touching connected-tenant behavior.
    """
    saw_upload = saw_connector = False
    for tc in convergence:
        if tc.connector_signal_count:
            saw_connector = True
        if tc.upload_signal_count:
            saw_upload = True
    return saw_upload and not saw_connector


def has_sufficient_evidence(
    convergence: list[ThemeConvergence],
    *,
    min_connected_signals: int = 3,
    require_multi_source: bool = True,
    min_upload_signals: int = 2,
) -> bool:
    """Is there enough REAL evidence to justify a brief?

    Pure + unit-testable. Returns True (generate a brief) when ANY of:

      - a theme shows multi-source convergence across CONNECTED sources
        (connected_breadth >= 2) — independent connected sources agreeing is
        the strongest possible signal that there is something real to say; OR
      - the total count of distinct connected-source signals across all themes
        is >= ``min_connected_signals`` (default 3); OR
      - the tenant is UPLOAD-ONLY (see is_upload_only) and has accumulated at
        least ``min_upload_signals`` (default 2) uploaded-doc signals. This is
        the loosened path for the single-uploaded-file case: a PM who uploads a
        file but has connected no live sources should still get a brief from
        those uploaded signals instead of a blank one. It fires ONLY when there
        are zero connector-origin signals, so a tenant that DOES have connected
        sources never reaches it and its gate behavior is unchanged.

    Otherwise the only evidence is onboarding / business-context / agent-inferred
    metadata (pm_manual, agent_inferred, verbal_claim) or a single thin source
    with no uploaded docs, so we return False and the caller emits an EMPTY brief
    rather than fabricating low-value findings from profile metadata.

    ``require_multi_source`` lets a deployment drop the breadth>=2 fast-path and
    rely purely on the connected-signal count (default keeps the breadth path).
    """
    if require_multi_source and any(
        tc.connected_breadth >= 2 for tc in convergence
    ):
        return True
    total_connected = sum(tc.connected_signal_count for tc in convergence)
    if total_connected >= min_connected_signals:
        return True
    # UPLOAD-ONLY relaxation — narrowly scoped: only when there are NO connector
    # sources at all. Connected-tenant behavior above is fully evaluated first
    # and is never affected by this branch.
    if is_upload_only(convergence):
        total_upload = sum(tc.upload_signal_count for tc in convergence)
        return total_upload >= min_upload_signals
    return False

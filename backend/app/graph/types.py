"""Knowledge Graph types — Entity / Signal / Relationship / Source records.

Matches contract S3 in `~/sprntly-shared-contracts.md`. Embedding is `list[float]`
in Python; persisted as `vector(1536)` in Postgres (pgvector).

`Signal.stale_after` is auto-computed from `source_type` per the #1 staleness
window table (KG_Engineering_Spec §3.2.1, locked 2026-05-28). `outcome_measured`
signals never expire (stale_after stays None).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid


# ── Retirement ───────────────────────────────────────────────────────────────
# A signal can leave the live picture two ways, both recorded in `properties`
# (never a delete — history stays queryable):
#
#   superseded_by — a NEWER signal replaces this fact (facade.supersede_signal).
#   expired_at    — the fact is simply gone, with no successor: a versioned
#                   document no longer asserts it (facade.expire_signals; today
#                   a roadmap bet dropped between roadmap versions).
#
# `stale_after` alone is NOT sufficient to retire a signal: the content readers
# (synthesis.convergence, graph.retrieval's theme-edge path, evidence_kg's trail)
# deliberately read UNFILTERED signals and decide for themselves, so a signal
# that is only stale still steers briefs, Ask answers and PRD evidence. Every
# one of those readers calls `signal_is_retired` so the two mechanisms can't
# drift apart.
_RETIRED_PROPERTY_KEYS: tuple[str, ...] = ("superseded_by", "expired_at")


def signal_is_retired(properties: Optional[dict]) -> bool:
    """True when a signal has been superseded or expired and must be excluded
    from briefs / retrieval / evidence. Takes the raw `properties` dict so
    callers can pass `sig.properties` without a None dance."""
    props = properties or {}
    return any(props.get(k) for k in _RETIRED_PROPERTY_KEYS)


# Closed relationship vocabulary (S3). Novel relationships from the extractor
# must be bucketed into RELATES_TO and flagged for human vocab review.
RELATIONSHIP_VOCAB: frozenset[str] = frozenset({
    "SUPPORTS", "CONTRADICTS", "ADDRESSES", "BLOCKED_BY", "AFFECTS",
    "REQUESTS", "PRESSURES", "SERVES", "IMPACTS", "ON", "PART_OF",
    "PROMOTED_TO", "EXPRESSED_AS", "VISUALIZES", "RESULTED_IN",
    "VALIDATES", "UPDATES_WEIGHT", "IMPLEMENTS", "REALIZES",
    "SCOPED_TO", "INFORMS", "RELATES_TO",
})

# Reserved entity types — the §2 decision/learning ledger spine. These carry
# extra required props in `properties` validated at the application layer.
RESERVED_ENTITY_TYPES: frozenset[str] = frozenset({
    "hypothesis", "decision", "outcome", "artifact",
})

# Signal source_type enum (must match the DB CHECK constraint in the migration).
SIGNAL_SOURCE_TYPES: frozenset[str] = frozenset({
    "analytics", "project_mgmt", "communication", "customer_voice", "revenue",
    "verbal_claim", "pm_manual", "agent_inferred", "outcome_measured",
})

# Source types that represent REAL connected-source evidence (a connector sync
# or a corpus document run through the extractor), as opposed to onboarding /
# business-context / agent-inference SEEDED metadata. The canonical home for
# this vocabulary — `synthesis.convergence` imports it from here rather than
# defining its own copy, so the extractor (which needs it to compute
# `Signal.evidence_eligible` below) and the brief-sufficiency gate can never
# drift apart. Moved here from `synthesis.convergence`; the name and values
# are unchanged, so `convergence.CONNECTED_SOURCE_TYPES` still resolves via
# convergence's re-export.
CONNECTED_SOURCE_TYPES: frozenset[str] = frozenset({
    "analytics", "project_mgmt", "communication", "customer_voice", "revenue",
    "outcome_measured",
})

# Provenance origins whose signals are NEVER evidence, whatever source_type
# they carry — today just `web_research` (facts scraped off the public web
# about the company's own footprint, app/company_research.py). See
# `synthesis.convergence` for the full rationale; moved here for the same
# reason as CONNECTED_SOURCE_TYPES above.
NON_EVIDENCE_ORIGINS: frozenset[str] = frozenset({"web_research"})


def compute_evidence_eligible(source_type: str, origin: Optional[str]) -> bool:
    """The policy `Signal.evidence_eligible` encodes: real connected-source
    evidence (source_type in CONNECTED_SOURCE_TYPES) that didn't arrive via a
    non-evidence origin (e.g. scraped web research). Shared by `Signal`
    construction (new signals) and `GraphFacade._row_to_signal` (reconstructing
    older rows whose typed column is still null) so both paths agree."""
    return source_type in CONNECTED_SOURCE_TYPES and origin not in NON_EVIDENCE_ORIGINS


# ── Ingestion triage taxonomy ────────────────────────────────────────────────
# Bounded, explicit, owned category list for the haiku triage pass that runs
# ahead of extraction on ingestion paths (app.graph.triage). Starting point:
# David's "max ~50 categories" idea from the discovery call, narrowed to what
# the classifier actually needs to distinguish for relevance-filtering and
# (future) category-based skill routing — comfortably under that ceiling.
# Versioned so a taxonomy revision is traceable in the decision-log audit
# trail (app.graph.triage.PROMPT_VERSION embeds this string): bump the
# version on any change to the category SET or their meaning; never mutate a
# shipped version's semantics in place, so content already classified under
# an old version stays interpretable.
#
# NOTE: pending Apurva/David sign-off — this is a reasoned starting draft,
# not yet confirmed with the team.
TRIAGE_TAXONOMY_VERSION = "triage-categories-v1"

TRIAGE_CATEGORIES: dict[str, str] = {
    "business_context": "Company/market background — business model, ICP, pricing, org structure",
    "product_prd": "Product requirements docs, specs, feature definitions",
    "roadmap": "Roadmap or forward-looking plan content — bets, timelines, sequencing",
    "escalation": "Urgent customer or exec escalations demanding a response",
    "customer_feedback": "Direct customer voice — requests, complaints, praise, verbatims",
    "support_ticket": "Support/service desk tickets and their resolutions",
    "sales_deal": "CRM/deal/pipeline content — blockers, competitive losses, close notes",
    "competitor_intel": "Competitor moves, positioning, or comparative analysis",
    "engineering_activity": "PRs, commits, technical delivery activity",
    "metric_report": "Analytics, dashboards, metrics, usage/funnel data",
    "meeting_notes": "Meeting summaries or transcripts",
    "research_report": "Market/user research findings not tied to a specific competitor",
    "decision_record": "A recorded decision and its rationale",
    "incident_report": "Outages, incidents, reliability events",
    "marketing_content": "Campaigns, external positioning/comms, marketing copy",
    "internal_admin": "Internal HR/administrative/ops content with no product signal",
    "legal_compliance": "Legal, contract, or compliance paperwork with no product signal",
    "other": "Doesn't fit the categories above but may still carry product signal",
}

# Per-source-type staleness window (#1, locked 2026-05-28 — reuses the
# KG_Engineering_Spec §3.2.1 table). None ⇒ never expires.
SOURCE_STALE_WINDOW_DAYS: dict[str, Optional[int]] = {
    "analytics":         30,
    "project_mgmt":      14,
    "communication":      7,
    "customer_voice":    30,
    "revenue":           30,
    "verbal_claim":       7,
    "pm_manual":         60,
    "agent_inferred":    14,
    "outcome_measured": None,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass
class Source:
    """A connected source (connector instance or agent) for an enterprise."""
    enterprise_id: str
    source_type: str
    label: Optional[str] = None
    config: dict = field(default_factory=dict)
    status: str = "active"
    id: str = field(default_factory=_uuid)


@dataclass
class Entity:
    """Universal node. `type` is emergent (theme/account/...) OR a reserved
    ledger type (hypothesis/decision/outcome/artifact). Themes/accounts/etc.
    are resolved+deduped via embedding similarity (#2)."""
    enterprise_id: str
    type: str
    canonical_label: str
    id: str = field(default_factory=_uuid)
    aliases: list[str] = field(default_factory=list)
    properties: dict = field(default_factory=dict)
    embedding: Optional[list[float]] = None
    valid_at: datetime = field(default_factory=_now)
    transaction_at: datetime = field(default_factory=_now)
    provenance: dict = field(default_factory=dict)
    confidence: float = 1.0


@dataclass
class Signal:
    """Atomic evidence. `stale_after` is auto-computed from `source_type`
    if not supplied — outcome_measured stays None (never expires).

    `skill_id`/`origin`/`channel`/`evidence_eligible` are typed promotions of
    what used to be informal `provenance` dict keys (`provenance["skill_id"]`
    is stamped by extract_document's skill routing; `origin`/`channel`
    predate that). Every pre-existing row/caller still only sets the dict —
    `__post_init__` below
    falls back to the dict value for any typed field left unset, so old data
    and old call sites keep working unchanged during the transition. New
    callers (extract_document) set both, belt-and-braces, so the dict stays
    the read path nothing has to migrate off of."""
    enterprise_id: str
    source_type: str
    kind: str
    content: str
    id: str = field(default_factory=_uuid)
    source_id: Optional[str] = None
    properties: dict = field(default_factory=dict)
    embedding: Optional[list[float]] = None
    valid_at: datetime = field(default_factory=_now)
    transaction_at: datetime = field(default_factory=_now)
    stale_after: Optional[datetime] = None
    confidence: float = 1.0
    weight: float = 1.0
    provenance: dict = field(default_factory=dict)
    skill_id: Optional[str] = None
    origin: Optional[str] = None
    channel: Optional[str] = None
    evidence_eligible: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.source_type not in SIGNAL_SOURCE_TYPES:
            raise ValueError(
                f"Unknown signal source_type: {self.source_type!r}. "
                f"Allowed: {sorted(SIGNAL_SOURCE_TYPES)}"
            )
        if self.stale_after is None:
            window = SOURCE_STALE_WINDOW_DAYS.get(self.source_type)
            if window is not None:
                self.stale_after = self.valid_at + timedelta(days=window)
        # Transition-safety fallback (see class docstring): an explicit typed
        # kwarg always wins; otherwise fall back to the informal provenance
        # dict key so old rows and old call sites resolve identically to
        # before this promotion.
        if self.skill_id is None:
            self.skill_id = self.provenance.get("skill_id")
        if self.origin is None:
            self.origin = self.provenance.get("origin")
        if self.channel is None:
            self.channel = self.provenance.get("channel")
        if self.evidence_eligible is None:
            self.evidence_eligible = compute_evidence_eligible(self.source_type, self.origin)


@dataclass
class Relationship:
    """Typed edge between two nodes (entity↔entity or entity↔signal).
    `type` must be in RELATIONSHIP_VOCAB."""
    enterprise_id: str
    type: str
    source_kind: str  # 'entity' | 'signal'
    source_id: str
    target_kind: str  # 'entity' | 'signal'
    target_id: str
    properties: dict = field(default_factory=dict)
    confidence: float = 1.0
    valid_at: datetime = field(default_factory=_now)
    transaction_at: datetime = field(default_factory=_now)
    provenance: dict = field(default_factory=dict)
    id: Optional[int] = None

    def __post_init__(self) -> None:
        if self.type not in RELATIONSHIP_VOCAB:
            raise ValueError(
                f"Relationship type {self.type!r} not in closed vocabulary. "
                f"Novel relationships → use 'RELATES_TO' and flag for vocab review."
            )
        if self.source_kind not in ("entity", "signal"):
            raise ValueError(f"source_kind must be 'entity' or 'signal': {self.source_kind!r}")
        if self.target_kind not in ("entity", "signal"):
            raise ValueError(f"target_kind must be 'entity' or 'signal': {self.target_kind!r}")

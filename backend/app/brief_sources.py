"""Real-source resolution for brief provenance chips.

The KG extractor CLASSIFIES a signal's `source_type` from document content —
so a plain uploaded PDF full of numbers can mint `analytics` signals for a
company that has never connected (or uploaded) any analytics source. Those
inferred types then surface as source chips on Top Insights cards ("From:
Analytics"), claiming provenance the company doesn't have (Apurva ruling
2026-07-27: chips must show REAL sources only — if the company has analytics
and it fed the finding, show it; otherwise never).

This module computes the set of signal source_types a company can HONESTLY
display, derived from what actually exists:

  - ACTIVE connections, mapped connector-type → signal source_type
    (an Amplitude connection makes `analytics` real; Slack makes
    `communication` real, …);
  - connector-category uploads (the file_categories sidecar), mapped through
    catalog.EVIDENCE_UPLOAD_CATEGORIES exactly as ingestion maps them
    (a CSV uploaded into the Voice category makes `customer_voice` real);
  - `pm_manual` is always allowed — the PM's own recorded input is real by
    definition.

Display-side consumers (synthesis/top_insights_skill) intersect a finding's
converged source_types with this set; findings whose evidence is only
uncategorized uploads fall back to the honest DOCUMENTS_SOURCE pseudo-type
("Uploaded documents") instead of the extractor's guess.

Fail-open: any infra error returns None ("don't filter") — provenance polish
must never block or degrade brief generation.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.connectors.catalog import (
    ANALYTICS,
    COMMUNICATION,
    CRM,
    CUSTOMER_VOICE,
    EVIDENCE_UPLOAD_CATEGORIES,
    MEETINGS,
    MONITORING,
    REVENUE,
    TASK_MANAGEMENT,
    types_for,
)

logger = logging.getLogger(__name__)

#: Display pseudo-source for findings whose real provenance is uncategorized
#: uploads. Not a KG source_type — it exists only on the display path.
DOCUMENTS_SOURCE = "documents"

#: connector type (catalog vocabulary) → signal source_type it makes real.
#: DOCUMENTS/CODE/DESIGN connectors ground context, not evidence chips, so
#: they map to nothing here (their signals fall back to DOCUMENTS_SOURCE).
_CONNECTOR_TYPE_TO_SOURCE_TYPE: dict[str, str] = {
    ANALYTICS: "analytics",
    MONITORING: "analytics",
    REVENUE: "revenue",
    CRM: "revenue",
    CUSTOMER_VOICE: "customer_voice",
    MEETINGS: "customer_voice",
    COMMUNICATION: "communication",
    TASK_MANAGEMENT: "project_mgmt",
}

#: Source types that are real regardless of connections/uploads.
_ALWAYS_ALLOWED: frozenset[str] = frozenset({"pm_manual"})


def allowed_source_types(company_id: str, slug: str) -> Optional[set[str]]:
    """The signal source_types this company may honestly display, or None on
    infra failure (callers must then skip filtering — fail open)."""
    try:
        allowed: set[str] = set(_ALWAYS_ALLOWED)
        # Active connections → their connector types → signal source types.
        from app import db

        for conn in db.list_connections(company_id):
            if conn.get("status") != "active":
                continue
            for ctype in types_for(conn.get("provider")):
                st = _CONNECTOR_TYPE_TO_SOURCE_TYPE.get(ctype)
                if st:
                    allowed.add(st)
        # Connector-category uploads → the category's default source type
        # (the same mapping ingestion stamps onto their signals).
        from app import datasets

        for category in set(datasets.read_file_categories(slug).values()):
            entry = EVIDENCE_UPLOAD_CATEGORIES.get(category)
            if entry:
                allowed.add(entry[0])
        return allowed
    except Exception:  # noqa: BLE001 — fail open, never degrade the brief
        logger.exception(
            "brief sources: could not resolve real sources for %s", company_id
        )
        return None


def display_source_types(
    source_types: set[str] | frozenset[str],
    allowed: Optional[set[str]],
) -> list[str]:
    """A finding's converged source_types reduced to what may be displayed.

    None `allowed` ⇒ no filtering (fail-open). An empty intersection falls
    back to [DOCUMENTS_SOURCE]: the signals exist (they came from the
    company's own uploaded documents) — the extractor's channel guess is what
    isn't real. Sorted for stable output."""
    if allowed is None:
        return sorted(source_types)
    kept = sorted(t for t in source_types if t in allowed)
    return kept or [DOCUMENTS_SOURCE]


#: Human labels for chips, rendered server-side so the enforced chip list and
#: the model's prose can't drift apart. Mirrors the web humanizeSource tone.
SOURCE_LABELS: dict[str, str] = {
    "analytics": "Analytics",
    "revenue": "Revenue",
    "customer_voice": "Customer voice",
    "communication": "Communication",
    "project_mgmt": "Project management",
    "pm_manual": "PM notes",
    "verbal_claim": "Stakeholder claims",
    "outcome_measured": "Measured outcomes",
    "agent_inferred": "Inferred",
    DOCUMENTS_SOURCE: "Uploaded documents",
}


def source_label(source_type: str) -> str:
    return SOURCE_LABELS.get(source_type, source_type.replace("_", " ").title())

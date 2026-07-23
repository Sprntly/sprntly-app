"""Data-source gate for user-triggered brief generation.

The onboarding flow only kicks the first brief when a real data source is
connected (web: `hasDataSourceConnection` over NON_EVIDENCE_CATEGORIES). This
module enforces the SAME rule at the backend generation entry points —
/v1/brief/regenerate, /v1/brief/regenerate-all, /v1/datasets/{slug}/generate —
so every surface that can request a brief (Regenerate button, the brief page's
empty-state auto-kick, chat-initiated asks, onboarding) behaves identically:
without a data source, generation is refused up front and the user sees the
needs-more-data state instead of burning a synthesis run to reach it.

A company HAS a data source when either:
  - an ACTIVE connection exists for an evidence-bearing provider
    (see app.connectors.catalog.is_evidence_provider), or
    — the `uploads` connector (the user's own named document sources) is one
    of these, so it satisfies the gate through the ORDINARY connection path
    with no special case here, or
  - the user has uploaded source files (the corpus `raw/` dir), which have
    always been able to drive a brief on their own. The workspace-context file
    onboarding seeds automatically is excluded — onboarding info alone must
    not produce a brief.

The weekly scheduler is deliberately NOT gated here: scheduled runs go through
generate_brief_for's own refresh-gating and empty-KG handling.
"""
from __future__ import annotations

import logging

from app import datasets
from app.connectors.catalog import is_evidence_provider

logger = logging.getLogger(__name__)

#: The message every gated surface shows. Matches the empty-KG failure copy in
#: routes/brief.py::_synthesis_generate_bg so the refused-up-front path and the
#: organic empty-KG path read identically to the user.
NO_DATA_SOURCE_MESSAGE = (
    "No data to generate a brief from yet — upload files "
    "or connect a data source, then regenerate."
)

#: Files onboarding writes into the corpus automatically (not user uploads).
_AUTO_SEEDED_FILENAMES = frozenset({"sprntly-workspace-context.md"})


def _has_evidence_connection(company_id: str) -> bool:
    """True iff any ACTIVE connection is an evidence-bearing provider."""
    from app import db

    try:
        connections = db.list_connections(company_id)
    except Exception:  # noqa: BLE001 — fail open: gate must never block on infra
        logger.exception("brief gate: could not list connections for %s", company_id)
        return True
    return any(
        c.get("status") == "active" and is_evidence_provider(c.get("provider"))
        for c in connections
    )


def _has_uploaded_sources(slug: str) -> bool:
    """True iff the corpus has user-uploaded raw files (auto-seeds excluded)."""
    try:
        raw_dir = datasets.raw_path(slug)
        if not raw_dir.exists():
            return False
        return any(
            p.is_file() and p.name not in _AUTO_SEEDED_FILENAMES
            for p in raw_dir.iterdir()
        )
    except Exception:  # noqa: BLE001 — fail open, same rationale as above
        logger.exception("brief gate: could not inspect uploads for %s", slug)
        return True


def has_brief_data_source(company_id: str, slug: str) -> bool:
    """True iff this company has any source that can feed a brief."""
    return _has_evidence_connection(company_id) or _has_uploaded_sources(slug)

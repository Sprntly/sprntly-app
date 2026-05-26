"""Brief persistence.

The legacy `briefs` table (one is_current row per dataset, payload as
jsonb) remains the source of truth for the `/v1/brief/current` UI
endpoint. The Comprehensive flow writes there too so the existing
frontend keeps working without per-route migration.

The cache layer (`app.brief.cache`) is separate: it keys by
(workspace_id, week_start) and is the dedupe mechanism for repeated
manual triggers within a week. The `briefs` table is for "what's the
latest", `cached_briefs` is for "what did we ship this Monday".
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.db import save_brief
from app.synthesis.brief_assembly import Brief

logger = logging.getLogger(__name__)


# Bumped any time the Brief payload shape changes. Wired into the
# stale-brief invalidation pass on service startup.
COMPREHENSIVE_BRIEF_SCHEMA_VERSION = 1


def persist_brief(
    brief: Brief, *, dataset_slug: Optional[str] = None
) -> int:
    """Persist a freshly-assembled Brief to the `briefs` table.

    Args:
        brief: the assembled Brief object.
        dataset_slug: optional UI-facing slug (the `briefs.dataset`
            column). Falls back to `brief.workspace_id` so a workspace
            without an explicit slug still gets persisted.

    Returns the new brief row's primary key.
    """
    dataset = dataset_slug or brief.workspace_id
    payload: dict[str, Any] = brief.model_dump(mode="json")
    week_label = brief.generated_at.strftime("Week of %Y-%m-%d")
    brief_id = save_brief(
        dataset=dataset,
        week_label=week_label,
        payload=payload,
        schema_version=COMPREHENSIVE_BRIEF_SCHEMA_VERSION,
    )
    logger.info(
        "Persisted Comprehensive Brief id=%s workspace=%s dataset=%s",
        brief_id,
        brief.workspace_id,
        dataset,
    )
    return brief_id


__all__ = [
    "persist_brief",
    "COMPREHENSIVE_BRIEF_SCHEMA_VERSION",
]

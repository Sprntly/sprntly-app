"""HTTP layer for the Research Agent.

  GET  /v1/research/digest?dataset=<slug>
       -> CompetitiveDigest for the given dataset's competitors

Phase-1 contract:
- The list of competitors per workspace lives on the dataset row as a
  jsonb `competitors` column (migration lands in a follow-up PR; for
  now we read from an in-memory lookup populated by the workspace
  setup flow, or fall back to []).
- The endpoint is best-effort: even when every source returns empty,
  we still return a well-formed CompetitiveDigest with a "no notable
  activity" highlight so the Brief's Competitive Pulse section
  renders cleanly.
- The endpoint is synchronous and currently does the network fan-out
  in-process. Once we measure real-world latency we'll move the fetch
  to a background worker and have this route serve from a cache.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_session
from app.db import dataset_exists, get_dataset
from app.research import generate_weekly_digest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/research", tags=["research"])


def _competitors_for_dataset(dataset_slug: str) -> list[dict[str, Any]]:
    """Look up the configured competitors for a dataset.

    Phase-1: no schema column exists yet, so we always return []. The
    digest still produces a valid (empty) CompetitiveDigest, which is
    the behaviour the Brief renderer expects when the competitive
    connector isn't configured.

    When the `datasets.competitors jsonb` migration lands, this is
    the single function to update — switch the body to read
    `get_dataset(slug)["competitors"]` and the rest of the pipeline
    starts producing real digests automatically.
    """
    row = get_dataset(dataset_slug)
    if not row:
        return []
    # Tolerate three shapes during the transition:
    # 1. row["competitors"] is already a list of dicts (target shape)
    # 2. it's under row["config"]["competitors"] (interim, if we end
    #    up bundling everything into a single config blob)
    # 3. it's missing entirely (Phase-1 default)
    raw = row.get("competitors")
    if raw is None and isinstance(row.get("config"), dict):
        raw = row["config"].get("competitors")
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    return []


@router.get("/digest")
def digest(
    dataset: str,
    _session: dict = Depends(require_session),
):
    """Return the weekly competitive digest for the dataset's competitors.

    404 if the dataset doesn't exist. 200 with a well-formed (possibly
    empty-of-pulses) digest otherwise — the absence of competitor
    config is a configuration state, not an error.
    """
    if not dataset_exists(dataset):
        raise HTTPException(404, f"Dataset {dataset!r} does not exist")

    competitors = _competitors_for_dataset(dataset)
    if not competitors:
        logger.info(
            "No competitors configured for dataset %s; returning empty digest",
            dataset,
        )

    result = generate_weekly_digest(dataset, competitors)
    return result.model_dump()

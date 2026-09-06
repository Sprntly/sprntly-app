"""Extraction results for run-scoped prose, keyed by the bytes that produced them.

WHY A DURABLE CACHE AND NOT A PROCESS-LOCAL ONE. A model call is a draw, not a
lookup — the discipline `crucible.figure_class.classify_figures` states for
figure classes, for exactly this reason: two draws over one corpus produced two
different committed totals, so the same evidence ranked two different ways. An
attached document has the same problem and a worse blast radius, because the
reader chose the file and will re-attach it. Without a cache that outlives the
worker process, the identical PDF sent to two runs yields two claim sets, two
clusterings and two rankings, and the engine asserts a reproducibility it does
not have.

WHAT THIS IS NOT. It is not the knowledge graph and it is not an ingest. A row
here is reachable only by the sha256 of text the reader has just handed over
again; nothing queries it by company, by theme or by content, and no analysis
reads it except as a substitute for a call it was about to make anyway. The
attachment's own BYTES are already durable — `attachments_storage` keeps them
under the workspace prefix, and `/approve` reads them back — so this adds no
new retention of anything the workspace was not already holding.

FAIL-SOFT IS LOAD-BEARING, and mirrors `app.db.design_agent_map_cache`: every
call is guarded, a missing table or an outage is a cache MISS, and a failed
write is a no-op. The caller then pays for the extraction it was going to pay
for. A cache may never be the reason a run fails.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_TABLE = "crucible_prose_extractions"


def get(
    *, enterprise_id: str, content_sha256: str, prompt_version: str,
) -> Optional[dict]:
    """The cached extraction output for this text under this prompt, or None.

    THE MODEL'S WHOLE `{"signals": [...]}` OBJECT, not the array inside it, so
    a cache hit and a live call hand the caller identically-shaped data and
    there is no unwrapping that only one of the two paths performs.

    None means "call the model" and is returned for a real miss, a malformed
    row and any database error alike — the three are indistinguishable to the
    caller on purpose, because the correct response to all of them is the same.

    NO TTL. The key is the sha256 of the exact text plus the prompt version, so
    there is nothing for time to invalidate: the same bytes under the same
    prompt have the same right answer next month. A prompt change is a new key
    and the old rows simply stop being read.
    """
    if not (enterprise_id and content_sha256 and prompt_version):
        return None
    try:
        from app.db.client import require_client

        rows = (
            require_client().table(_TABLE).select("output")
            .eq("enterprise_id", enterprise_id)
            .eq("content_sha256", content_sha256)
            .eq("prompt_version", prompt_version)
            .limit(1)
            .execute()
        ).data or []
    except Exception:  # noqa: BLE001 — see the module docstring
        logger.warning("crucible prose cache: read failed for %s; treating as "
                       "a miss", content_sha256[:12], exc_info=True)
        return None
    if not rows:
        return None
    output = rows[0].get("output")
    # A ROW WHOSE PAYLOAD IS THE WRONG SHAPE IS A MISS, NOT AN ERROR. It can
    # only have got there by a hand edit or an older writer, and re-extracting
    # is both cheap and correct; passing it through would hand the projection
    # something it would have to defend against a second time.
    if not isinstance(output, dict) or not isinstance(
            output.get("signals"), list):
        return None
    return output


def put(
    *, enterprise_id: str, content_sha256: str, prompt_version: str,
    output: Any,
) -> None:
    """Store one extraction. Silent on any failure — see the module docstring.

    UPSERT on the natural key, so two runs racing on the same attachment
    settle on one row rather than accumulating duplicates. Which of the two
    draws wins does not matter: it matters only that every LATER run gets the
    same one.
    """
    if not (enterprise_id and content_sha256 and prompt_version):
        return
    if not isinstance(output, dict) or not isinstance(
            output.get("signals"), list):
        return
    try:
        from app.db.client import require_client

        require_client().table(_TABLE).upsert(
            {
                "enterprise_id": enterprise_id,
                "content_sha256": content_sha256,
                "prompt_version": prompt_version,
                "output": output,
            },
            on_conflict="enterprise_id,content_sha256,prompt_version",
        ).execute()
    except Exception:  # noqa: BLE001 — a failed cache write costs the next
        # run one extraction call and nothing else.
        logger.warning("crucible prose cache: write failed for %s",
                       content_sha256[:12], exc_info=True)

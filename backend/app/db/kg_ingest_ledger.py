"""kg_ingest_ledger store — content hashes already paid for at extraction.

One row = one RawRecord rendering (sha256) that has been through
`extract_document` for an enterprise. The ingest runner consults this BEFORE
the LLM call so a connector re-sync (which re-pulls everything) never re-pays
extraction for records it already processed; a record whose content changed
renders to a new hash and is extracted again.

Advisory-only semantics: every failure here FAILS OPEN. A read error means
"nothing seen" (the sync extracts everything, exactly the pre-ledger
behavior); a write error is swallowed (the next sync re-extracts that batch
once more). The ledger can only ever save money, never lose data.
"""
from __future__ import annotations

import logging

from app.db.client import require_client

logger = logging.getLogger(__name__)

_TABLE = "kg_ingest_ledger"
# PostgREST `in_` filters ride the URL — chunk to keep requests comfortably
# under length limits (64-char hashes → ~200 per request).
_CHUNK = 200


def seen_hashes(
    enterprise_id: str, hashes: list[str], *, client=None
) -> set[str]:
    """Return the subset of `hashes` already recorded for this enterprise.

    Fail-open: any read error returns an empty set, so the caller extracts
    everything (pre-ledger behavior) rather than skipping unextracted data.
    """
    if not hashes:
        return set()
    try:
        cli = client or require_client()
        found: set[str] = set()
        for i in range(0, len(hashes), _CHUNK):
            r = (
                cli.table(_TABLE)
                .select("content_hash")
                .eq("enterprise_id", enterprise_id)
                .in_("content_hash", hashes[i : i + _CHUNK])
                .execute()
            )
            found.update(row["content_hash"] for row in (r.data or []))
        return found
    except Exception:  # noqa: BLE001 — advisory only, never break a sync
        logger.exception("kg_ingest_ledger read failed (extracting everything)")
        return set()


def record_hashes(
    enterprise_id: str, provider: str, hashes: list[str], *, client=None
) -> None:
    """Record hashes whose records were successfully extracted. Best-effort:
    a write failure is swallowed — the next sync just re-extracts them once."""
    if not hashes:
        return
    try:
        cli = client or require_client()
        rows = [
            {
                "enterprise_id": enterprise_id,
                "content_hash": h,
                "provider": provider,
            }
            for h in dict.fromkeys(hashes)  # de-dupe, order-preserving
        ]
        for i in range(0, len(rows), _CHUNK):
            cli.table(_TABLE).upsert(
                rows[i : i + _CHUNK],
                on_conflict="enterprise_id,content_hash",
                ignore_duplicates=True,
            ).execute()
    except Exception:  # noqa: BLE001 — advisory only, never break a sync
        logger.exception("kg_ingest_ledger write failed (will re-extract next sync)")

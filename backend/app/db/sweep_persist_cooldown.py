"""sweep_persist_cooldown store — per-(company, provider) rate limit for the
cross-connector sweep's persistence run (connector_lookup/sweep_persist.py).

Sibling to kg_ingest_ledger, deliberately a SEPARATE table: the ledger's
`created_at` only advances on a WRITE, and this gate has to hold even when a
run writes nothing at all — the steady state once a provider's content is
fully ledger-deduped, and the Slack case, which never writes a ledger hit
that collides with the scheduled pull in the first place (see
connector_lookup/slack.py). Both would leave the ledger's timestamp frozen,
so gating on it would silently stop gating exactly when it matters most. This
table instead records that a (company, provider) pair was PROCESSED, whether
or not anything was written.

Advisory-only, fail-open like kg_ingest_ledger: a read error means "not in
cooldown" (proceed), and a write error just means the next check re-reads
sooner than intended. Getting this wrong costs redundant enrichment fetches
during an outage, never a missed skip — the content-hash ledger underneath
this is still the correctness backstop for what actually gets extracted.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)

_TABLE = "sweep_persist_cooldown"


def in_cooldown(
    enterprise_id: str, provider: str, *, hours: float, client=None
) -> bool:
    """True when this (enterprise, provider) pair was marked within the last
    `hours` — the caller should skip enrichment AND extraction entirely for
    it this run (AC-A5). Fail-open: any read error returns False."""
    try:
        cli = client or require_client()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=max(hours, 0.0))
        ).isoformat()
        r = (
            cli.table(_TABLE)
            .select("provider")
            .eq("enterprise_id", enterprise_id)
            .eq("provider", provider)
            .gt("last_run_at", cutoff)
            .limit(1)
            .execute()
        )
        return bool(r.data)
    except Exception:  # noqa: BLE001 — advisory only, never block a run
        logger.exception(
            "sweep_persist_cooldown read failed for %s/%s (not blocking)",
            enterprise_id, provider,
        )
        return False


def mark_run(enterprise_id: str, provider: str, *, client=None) -> None:
    """Record that this (enterprise, provider) pair was just processed.
    Best-effort: a write failure just means the next sweep re-checks sooner
    than the configured interval, degrading to today's per-question
    behaviour for this provider until the write succeeds again."""
    try:
        cli = client or require_client()
        cli.table(_TABLE).upsert(
            {
                "enterprise_id": enterprise_id,
                "provider": provider,
                "last_run_at": utc_now(),
            },
            on_conflict="enterprise_id,provider",
        ).execute()
    except Exception:  # noqa: BLE001 — advisory only, next run just re-checks sooner
        logger.exception(
            "sweep_persist_cooldown write failed for %s/%s", enterprise_id, provider,
        )

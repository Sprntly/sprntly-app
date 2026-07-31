"""One-off: replay a workspace's historical Fireflies calls into the KG.

Why this exists
---------------
The scheduled sync only ever fetched the 25 most recent meetings (a page cap
mistaken for a window — see pullers/fireflies.pull), so a workspace's history
was never ingested. This walks a bounded date window and extracts each call as
its own document, so every signal lands with the MEETING's date in `valid_at`
and the transcript id in provenance.

Safety
------
  • Read-only against Fireflies; writes only kg_signal / kg_entity rows.
  • Idempotent twice over: the content-hash ledger skips records already
    extracted, and signal ids are uuid5(enterprise|content) so a re-run
    cannot duplicate a fact.
  • --dry-run prints exactly what WOULD be extracted (and the model-call
    count) without touching the KG or spending a token.
  • Backfilled signals are born stale (stale_after derives from valid_at), so
    replaying history does NOT push old evidence into current briefs.

Usage
-----
    python scripts/backfill_fireflies_history.py \
        --company <uuid> --since 2026-01-01 [--until 2026-08-01] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from app import db
from app.connectors.tokens import decrypt_token_json
from app.graph.facade import GraphFacade
from app.kg_ingest import runner
from app.kg_ingest.pullers import fireflies

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill")

_PROVIDER = "fireflies"


def _day(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--company", required=True, help="company/enterprise uuid")
    ap.add_argument("--since", required=True, type=_day, metavar="YYYY-MM-DD")
    ap.add_argument("--until", type=_day, metavar="YYYY-MM-DD",
                    help="defaults to now")
    ap.add_argument("--limit", type=int, default=1000,
                    help="ceiling on calls fetched (default 1000)")
    ap.add_argument("--dry-run", action="store_true",
                    help="list the calls and exit — no extraction, no cost")
    args = ap.parse_args()
    until = args.until or datetime.now(timezone.utc)

    row = db.get_connection(args.company, _PROVIDER)
    if not row:
        logger.error("no %s connection for company %s", _PROVIDER, args.company)
        return 1
    api_key = json.loads(decrypt_token_json(row["token_json_encrypted"]))["api_key"]

    records = list(fireflies.pull(
        api_key, since=args.since, until=until, limit=args.limit,
    ))
    # Oldest first: signal ids are content-keyed, so when two calls assert the
    # SAME fact the first write wins and later ones are deduped. Ascending order
    # therefore dates a repeated fact from when it was FIRST said, which is the
    # answer "since when have customers wanted X?" needs.
    records.sort(key=lambda r: r.timestamp or "")

    logger.info("%d calls in %s..%s", len(records),
                args.since.date(), until.date())
    if not records:
        return 0
    logger.info("oldest: %s  newest: %s",
                records[0].timestamp, records[-1].timestamp)

    if args.dry_run:
        for r in records:
            print(f"  {(r.timestamp or '?')[:10]}  {r.title[:70]}  [{r.external_id}]")
        print(f"\nDRY RUN — would extract {len(records)} documents "
              f"(≤{len(records)} model calls; the ledger may skip some).")
        return 0

    result = runner.sync_provider(
        GraphFacade(), args.company, _PROVIDER, token=api_key, records=records,
    )
    logger.info("done: %s", result)
    if result.get("errors"):
        logger.warning("%d batch error(s) — re-run to retry them",
                       len(result["errors"]))
        for e in result["errors"][:5]:
            logger.warning("  %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())

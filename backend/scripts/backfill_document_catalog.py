"""One-shot backfill: register every ALREADY-UPLOADED document in the catalog.

    # Dry run is the DEFAULT — counts what would be registered, writes nothing:
    python scripts/backfill_document_catalog.py

    # One tenant first, always:
    python scripts/backfill_document_catalog.py --apply --company <company_id>

    # Then, only with explicit owner approval for the target environment:
    python scripts/backfill_document_catalog.py --apply

New uploads catalog themselves on the way in, so this exists only to cover
files uploaded BEFORE the catalog existed. Drive and Confluence documents are
covered by their next sync (their registration rides the same pull that
already re-reads them) and are deliberately not backfilled here — this script
reads `document_source_file` and nothing else.

Cost + safety, stated plainly because this script spends money:

  * Each newly registered document costs ONE fast-model summary call plus one
    embedding — roughly $0.001 per document. A thousand-document tenant is
    about a dollar.
  * It is idempotent by content hash, not by bookkeeping: a second run finds
    every hash unchanged and pays nothing. That is also what makes it safe to
    re-run after a partial failure.
  * Per-file and per-company error isolation — one bad document or one bad
    tenant logs and the run continues.
  * `--dry-run` is the DEFAULT. Nothing is written without `--apply`.
  * Nothing READS the catalog yet, so this changes no user-visible behaviour
    in either direction. It only pre-pays work a later change will consume.

Running this against staging or production is an owner decision, per
environment — it is not implied by shipping the script.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.client import require_client  # noqa: E402
from app.document_sources import (  # noqa: E402
    backfill_catalog,
    list_document_sources,
    list_source_files,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_document_catalog")


def _companies(explicit: str | None) -> list[str]:
    if explicit:
        return [explicit]
    rows = require_client().table("companies").select("id").execute().data or []
    return [r["id"] for r in rows]


def _planned(company_id: str) -> int:
    return sum(
        len(list_source_files(company_id, s.id))
        for s in list_document_sources(company_id)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="actually register (default: dry run)")
    parser.add_argument("--company", help="one company id (default: all)")
    parser.add_argument("--limit", type=int, default=0,
                        help="max documents to register per company")
    args = parser.parse_args()

    totals = {"registered": 0, "skipped": 0, "errors": 0}
    for company_id in _companies(args.company):
        try:
            if not args.apply:
                planned = _planned(company_id)
                if planned:
                    logger.info("WOULD %s — up to %s document(s)",
                                company_id, planned)
                continue
            counts = backfill_catalog(
                company_id, limit=args.limit or None
            )
            if any(counts.values()):
                logger.info("OK    %s — %s", company_id, counts)
            for k in totals:
                totals[k] += counts[k]
        except Exception:  # noqa: BLE001 — per-company isolation
            logger.exception("ERROR %s — backfill failed", company_id)
            totals["errors"] += 1

    logger.info("%s: %s", "applied" if args.apply else "dry run", totals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

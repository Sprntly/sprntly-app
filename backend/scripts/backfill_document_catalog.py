"""One-shot backfill: register every ALREADY-UPLOADED document in the catalog.

    # Dry run is the DEFAULT — counts what would be registered, writes nothing:
    python scripts/backfill_document_catalog.py

    # One tenant first, always:
    python scripts/backfill_document_catalog.py --apply --company <company_id>

    # Then, only with explicit owner approval for the target environment:
    python scripts/backfill_document_catalog.py --apply

New uploads catalog themselves on the way in, so this exists only to cover
files uploaded BEFORE the catalog existed.

CONFLUENCE is covered by its next sync — its puller re-reads every page, so
registration rides that pull. DRIVE IS NOT, and the original version of this
docstring said it was. `drive_extract` only sees files whose `modifiedTime`
moved, and catalog registration lives inside that per-file loop, so a Drive
file synced before the catalog shipped stays unregistered until a human edits
it in Drive. Measured on the shared database 2026-08-07, that left ONE
`google_drive` row against 27 `confluence` ones. `--drive` covers it, reading
the markdown the sync already wrote rather than calling Google:

    python scripts/backfill_document_catalog.py --drive --company <id>
    python scripts/backfill_document_catalog.py --drive --apply --company <id>

Run `document_bodies.backfill_drive_markdown_paths` first for any tenant whose
Drive files predate markdown-location tracking — a file whose body cannot be
located is skipped here (counted `no_body`), because an Index entry with
nothing behind it is worse than an absent one.

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
  * THE CATALOG IS NOW READ ON THE ANSWER PATH — this line used to say
    nothing read it, and that stopped being true when `document_grounding`
    began indexing and ranking from it. Registering a document therefore DOES
    change answers: it becomes visible in the document Index (so the model
    stops saying the workspace has no such file), rankable by topic, and
    resolvable as the subject of a question. That is the intended effect and
    the reason to run this, but it is no longer a no-op, and a run against a
    live tenant should be treated as a behaviour change rather than as
    pre-paid work.

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
    backfill_drive_catalog,
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
    parser.add_argument(
        "--drive", action="store_true",
        help=(
            "backfill already-synced Google Drive files instead of uploads "
            "(reads the corpus markdown the sync wrote; never calls Google)"
        ),
    )
    args = parser.parse_args()

    # Drive counts `no_body` — files whose markdown location was never
    # recorded — apart from errors, because it is the expected outcome for an
    # older tenant and not a fault. Kept out of the uploads totals so a run of
    # either mode reports only the keys that mode can produce.
    totals = (
        {"registered": 0, "skipped": 0, "no_body": 0, "errors": 0} if args.drive
        else {"registered": 0, "skipped": 0, "errors": 0}
    )
    for company_id in _companies(args.company):
        try:
            if args.drive:
                # Drive's dry run goes through the SAME function with
                # apply=False rather than a separate count, so what a dry run
                # reports and what an apply does cannot drift: the skip
                # decisions (hash unchanged, body unlocatable) are made once,
                # in one place.
                counts = backfill_drive_catalog(
                    company_id, apply=args.apply, limit=args.limit or None
                )
                if any(counts.values()):
                    logger.info(
                        "%s %s — %s",
                        "OK   " if args.apply else "WOULD", company_id, counts,
                    )
                for k in totals:
                    totals[k] += counts[k]
                continue
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

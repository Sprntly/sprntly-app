"""One-shot backfill: record where each already-synced Drive file's text landed.

    # Dry run is the DEFAULT — counts what would be recorded, writes nothing:
    python scripts/backfill_drive_md_paths.py

    # One tenant first, always:
    python scripts/backfill_drive_md_paths.py --apply --company <company_id>

    # Then, only with explicit owner approval for the target environment:
    python scripts/backfill_drive_md_paths.py --apply

Newly synced Drive files record their own location on the way in, so this
exists only for files synced BEFORE that was kept. Without it those files stay
catalogued, summarised and rankable with no readable body — and they will not
fix themselves, because Drive re-fetches a file only when its `modifiedTime`
changes, so an untouched document is never re-synced.

Cost + safety:

  * Costs NOTHING to run. No model calls, no embeddings — it reads directory
    listings and writes a small config fragment per file.
  * Idempotent: a file that already has a location is skipped, so a second run
    writes nothing. That is also what makes it safe after a partial failure.
  * It REFUSES to guess. Where two Drive files normalised to one markdown name
    the corpus holds `name.md` and `name.1.md`, and nothing recorded which
    file took which. Both are left unset and reported as `ambiguous`. A wrong
    path silently serves another document's text under this document's name,
    which the user cannot detect — strictly worse than no text at all.
  * `--dry-run` is the DEFAULT. Nothing is written without `--apply`.

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
from app.document_bodies import backfill_drive_markdown_paths  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_drive_md_paths")


def _companies(explicit: str | None) -> list[str]:
    if explicit:
        return [explicit]
    rows = require_client().table("companies").select("id").execute().data or []
    return [r["id"] for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", help="one company id; default is every company")
    parser.add_argument(
        "--apply", action="store_true",
        help="write. Without this the run only counts.",
    )
    args = parser.parse_args()

    totals = {"updated": 0, "ambiguous": 0, "already_set": 0, "unresolved": 0}
    for company_id in _companies(args.company):
        try:
            counts = backfill_drive_markdown_paths(company_id, apply=args.apply)
        except Exception:  # noqa: BLE001 — per-tenant isolation
            logger.exception("backfill failed for %s", company_id)
            continue
        if any(counts.values()):
            logger.info("%s: %s", company_id, counts)
        for key, value in counts.items():
            totals[key] += value

    mode = "APPLIED" if args.apply else "DRY RUN (nothing written)"
    logger.info("%s — %s", mode, totals)
    if totals["ambiguous"]:
        logger.warning(
            "%d file(s) left unset because their markdown name collided with "
            "another file's. These need a re-sync (edit the file in Drive to "
            "change its modifiedTime) rather than a guess.",
            totals["ambiguous"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

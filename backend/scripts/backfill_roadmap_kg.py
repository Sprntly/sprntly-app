"""One-shot backfill: push every ALREADY-UPLOADED roadmap into the knowledge graph.

    # Dry run is the DEFAULT — reads roadmap_doc/kg_source only, writes nothing:
    python scripts/backfill_roadmap_kg.py

    # Then, only with explicit owner approval for the target environment:
    python scripts/backfill_roadmap_kg.py --apply --limit 5

You almost certainly do NOT need this. `synthesis_brief.seed_incremental` already
grandfathers every pre-existing roadmap: the next brief generation for a company
ingests its stored roadmap, ledger-deduped, at zero extra cost. This script only
exists to force that ingest EARLY (e.g. so a demo tenant's roadmap shows up in
Ask before anyone regenerates its brief).

Cost + safety, stated plainly because this script spends money and mutates a
shared graph:

  * Each backfilled roadmap costs one extraction call per 4000-char chunk (plus
    embeddings). A company with a long roadmap deck can be 25 calls.
  * Ingest has REPLACE semantics — extracting the current roadmap version
    expires this workspace's older roadmap signals. On a first backfill there are
    none, so nothing is expired; re-running after a roadmap was replaced outside
    this script is what expiry is for.
  * Everything is per-workspace error-isolated: one bad roadmap logs and the run
    continues.
  * `--dry-run` is the DEFAULT. Nothing is written without `--apply`.

Running this against staging or production is an owner decision, per-environment
— it is not implied by shipping the script.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.client import require_client  # noqa: E402
from app.graph.facade import GraphFacade  # noqa: E402
from app.kg_ingest.roadmap import (  # noqa: E402
    _already_ingested,
    content_sha,
    ingest_roadmap,
)

logger = logging.getLogger("backfill_roadmap_kg")


def _stored_roadmaps() -> list[dict]:
    """Every stored roadmap: [{company_id, workspace_id, filename, version,
    chars, text}], newest-uploaded first."""
    rows = (
        require_client().table("roadmap_doc")
        .select("company_id,workspace_id,filename,version,uploaded_at,extracted_text")
        .execute().data or []
    )
    rows.sort(key=lambda r: r.get("uploaded_at") or "", reverse=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually ingest (default is a read-only dry run)")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N roadmaps (0 = no limit)")
    ap.add_argument("--company", default=None,
                    help="restrict to one company id")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    facade = GraphFacade()
    rows = _stored_roadmaps()
    if args.company:
        rows = [r for r in rows if r.get("company_id") == args.company]

    planned = 0
    for row in rows:
        company_id = row.get("company_id")
        workspace_id = row.get("workspace_id")
        text = (row.get("extracted_text") or "").strip()
        label = f"{company_id} ws={workspace_id} {row.get('filename')!r} v{row.get('version')}"
        if not company_id:
            continue
        if not text:
            logger.info("SKIP  %s — no extractable text", label)
            continue
        try:
            if _already_ingested(facade, company_id,
                                 content_sha(company_id, workspace_id, text)):
                logger.info("SKIP  %s — already in KG (ledger hit)", label)
                continue
        except Exception:  # noqa: BLE001 — per-row isolation
            logger.exception("ERROR %s — ledger check failed", label)
            continue
        if args.limit and planned >= args.limit:
            logger.info("STOP  --limit %s reached", args.limit)
            break
        planned += 1
        if not args.apply:
            logger.info("WOULD %s — %s chars, ~%s extraction call(s)",
                        label, len(text), max(1, -(-len(text) // 4000)))
            continue
        try:
            result = ingest_roadmap(company_id, workspace_id, facade=facade)
            logger.info("OK    %s — %s", label, result)
        except Exception:  # noqa: BLE001 — per-row isolation
            logger.exception("ERROR %s — ingest failed", label)

    logger.info("%s %s roadmap(s)", "ingested" if args.apply else "would ingest",
                planned)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

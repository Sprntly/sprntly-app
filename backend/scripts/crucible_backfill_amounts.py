"""Operator CLI: backfill `kg_signal.properties.amount`/`.currency` for one
company's historical signals from the stated figure already sitting in their
`content` paraphrase. Zero LLM calls, zero provider fetches.

DRY-RUN BY DEFAULT. Pass `--apply` for a real write; without it this reads
every eligible signal, reports exactly what it would change, and writes
nothing. There is no "all companies" mode — `--company` is always required.

Usage:
    python -m scripts.crucible_backfill_amounts --company <enterprise-id>
    python -m scripts.crucible_backfill_amounts --company <enterprise-id> --apply
    python -m scripts.crucible_backfill_amounts --company <enterprise-id> --limit 50

See `app.crucible.backfill` for the parsing rules, the eligibility gate, and
why a backfilled row is marked with a distinct `certainty` rather than
written indistinguishably from an ingest-time figure.
"""
from __future__ import annotations

import argparse
import json
import sys

from app.crucible.backfill import run_backfill


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--company", required=True, help="enterprise_id / companies.id — always scoped to one company")
    ap.add_argument("--apply", action="store_true", help="Write changes. Omit for a dry run (the default).")
    ap.add_argument("--limit", type=int, default=None, help="Cap the number of signals examined (testing/staging a partial run).")
    args = ap.parse_args()

    result = run_backfill(company_id=args.company, apply=args.apply, limit=args.limit)

    print(f"mode:            {result['mode']}")
    print(f"company_id:      {result['company_id']}")
    print(f"pattern_version: {result['pattern_version']}")
    print(f"run_id:          {result['run_id']}")
    print(f"examined:        {result['examined']}")
    print(f"enriched:        {result['enriched']}")
    print(f"skipped total:   {result['total_skipped']}")
    for reason, count in result["skipped"].items():
        print(f"  - {reason}: {count}")
    if not args.apply and result["enriched"]:
        print(
            "\nDRY RUN — nothing was written. Re-run with --apply to write "
            f"{result['enriched']} signal(s)."
        )
    print("\n" + json.dumps(result, indent=2))


if __name__ == "__main__":
    sys.exit(main())

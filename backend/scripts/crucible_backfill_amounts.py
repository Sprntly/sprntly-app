"""Operator CLI: backfill `kg_signal.properties.amount`/`.currency` for one
company's historical signals from the stated figure already sitting in their
`content` paraphrase. Zero LLM calls, zero provider fetches.

DRY-RUN BY DEFAULT. Pass `--apply` for a real write; without it this reads
every eligible signal, reports exactly what it would change, and writes
nothing. There is no "all companies" mode — `--company` is always required.

`--purge` runs the inverse: it clears `amount`/`currency`/`certainty` from
exactly the signals a PREVIOUS run of this sweep enriched, so a corrected
pattern can be applied to rows the sweep's own idempotency guard would
otherwise skip forever. It obeys the same dry-run-by-default rule, so
destroying data takes two explicit flags (`--purge --apply`), never one.

ALWAYS READ THE AMOUNT DISTRIBUTION BEFORE `--apply`. The counts can be
perfect on a run that mints a nonsense figure; the min/median/max/top-10
block is the part that shows whether what would be written is true.

Usage:
    python -m scripts.crucible_backfill_amounts --company <enterprise-id>
    python -m scripts.crucible_backfill_amounts --company <enterprise-id> --apply
    python -m scripts.crucible_backfill_amounts --company <enterprise-id> --limit 50
    python -m scripts.crucible_backfill_amounts --company <enterprise-id> --purge
    python -m scripts.crucible_backfill_amounts --company <enterprise-id> --purge --apply

See `app.crucible.backfill` for the parsing rules, the eligibility gate, and
why a backfilled row is marked with a distinct `certainty` rather than
written indistinguishably from an ingest-time figure.
"""
from __future__ import annotations

import argparse
import json
import sys

from app.crucible.backfill import purge_backfilled_amounts, run_backfill


def _print_amounts(result: dict) -> None:
    """The distribution block. Printed for every mode, including dry runs —
    a dry run is exactly where a wrong magnitude is supposed to be caught."""
    amounts = result.get("amounts")
    if not amounts:
        print("amounts:         (none)")
        return
    print(f"amounts:         count={amounts['count']}")
    print(f"  min:           {amounts['min']:,.2f}")
    print(f"  median:        {amounts['median']:,.2f}")
    print(f"  max:           {amounts['max']:,.2f}")
    print("  top 10:        " + ", ".join(f"{a:,.2f}" for a in amounts["top_10"]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--company", required=True, help="enterprise_id / companies.id — always scoped to one company")
    ap.add_argument("--apply", action="store_true", help="Write changes. Omit for a dry run (the default).")
    ap.add_argument("--limit", type=int, default=None, help="Cap the number of signals examined (testing/staging a partial run).")
    ap.add_argument("--purge", action="store_true", help="Clear amount/currency/certainty from rows a previous sweep enriched, instead of enriching. Still dry-run unless --apply.")
    args = ap.parse_args()

    if args.purge:
        result = purge_backfilled_amounts(
            company_id=args.company, apply=args.apply, limit=args.limit,
        )
        print(f"mode:            purge / {result['mode']}")
        print(f"company_id:      {result['company_id']}")
        print(f"examined:        {result['examined']}")
        print(f"cleared:         {result['cleared']}")
        _print_amounts(result)
        if not args.apply and result["cleared"]:
            print(
                "\nDRY RUN — nothing was cleared. Re-run with --purge --apply to "
                f"clear {result['cleared']} signal(s)."
            )
        print("\n" + json.dumps(result, indent=2))
        return

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
    _print_amounts(result)
    if not args.apply and result["enriched"]:
        print(
            "\nDRY RUN — nothing was written. Re-run with --apply to write "
            f"{result['enriched']} signal(s)."
        )
    print("\n" + json.dumps(result, indent=2))


if __name__ == "__main__":
    sys.exit(main())

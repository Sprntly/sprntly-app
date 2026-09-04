"""Operator CLI: attribute one company's historical signals to the
organisation whose call they came from, by joining `kg_signal.source_call_id`
to `call_index.account`. Zero LLM calls, zero provider fetches, no text
parsing.

WHY A SECOND TOOL RATHER THAN A FLAG ON THE AMOUNT ONE. The two sweeps write
different keys from different sources under different version constants, and
an operator running one must never half-run the other. Sibling of
`scripts.crucible_backfill_amounts`; same safety posture throughout.

DRY-RUN BY DEFAULT. Pass `--apply` for a real write; without it this reads
every eligible signal, reports exactly what it would change, and writes
nothing. There is no "all companies" mode — `--company` is always required.

`--purge` runs the inverse: it clears `account`/`account_source` from exactly
the signals a PREVIOUS run of this sweep wrote — never a name an extraction
pass read out of a customer's own words, which carries no such marker. It
obeys the same dry-run-by-default rule, so destroying data takes two explicit
flags (`--purge --apply`), never one.

ALWAYS READ THE ACCOUNT BLOCK BEFORE `--apply`. The counts can be perfect on
a run that attributes hundreds of signals to one wrong name; the
distinct-spelling count and the top-names list are the part that shows
whether what would be written is true.

AND READ `distinct_spellings` AS SPELLINGS, NOT COMPANIES. Nothing in this
system canonicalises an organisation name. "Acme", "Acme Corp" and a
domain-derived "Acme" are three separate accounts to every consumer. This
sweep's own output is internally consistent — one derivation function, so the
same customer domain always yields the same string — but it will not merge
with a name a transcript produced.

Usage:
    python -m scripts.crucible_backfill_accounts --company <enterprise-id>
    python -m scripts.crucible_backfill_accounts --company <enterprise-id> --apply
    python -m scripts.crucible_backfill_accounts --company <enterprise-id> --limit 50
    python -m scripts.crucible_backfill_accounts --company <enterprise-id> --kind feature_request --kind commercial_term
    python -m scripts.crucible_backfill_accounts --company <enterprise-id> --purge
    python -m scripts.crucible_backfill_accounts --company <enterprise-id> --purge --apply

See `app.crucible.backfill` for the derivation rule, the eligibility gate, and
why a name is taken from the call's participant domains rather than parsed out
of the signal's own text.
"""
from __future__ import annotations

import argparse
import json
import sys

from app.crucible.backfill import (
    purge_backfilled_account_names,
    run_account_backfill,
)


def _print_accounts(result: dict) -> None:
    """The distribution block. Printed for every mode, including dry runs —
    a dry run is exactly where a wrong attribution is supposed to be caught."""
    accounts = result.get("accounts")
    if not accounts:
        print("accounts:        (none)")
        return
    print(f"accounts:        count={accounts['count']}")
    print(f"  spellings:     {accounts['distinct_spellings']} (NOT a company count — no resolver exists)")
    print("  most frequent:")
    for name, count in accounts["top"]:
        print(f"    {count:>6}  {name}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--company", required=True, help="enterprise_id / companies.id — always scoped to one company")
    ap.add_argument("--apply", action="store_true", help="Write changes. Omit for a dry run (the default).")
    ap.add_argument("--limit", type=int, default=None, help="Cap the number of signals examined (testing/staging a partial run).")
    ap.add_argument("--kind", action="append", default=None, dest="kinds",
                    help="Signal kind to sweep; repeatable. Defaults to commercial_term + pricing, the population a stated figure can attach to.")
    ap.add_argument("--purge", action="store_true", help="Clear account/account_source from rows a previous sweep wrote, instead of attributing. Still dry-run unless --apply.")
    args = ap.parse_args()

    if args.purge:
        result = purge_backfilled_account_names(
            company_id=args.company, apply=args.apply, limit=args.limit,
            kinds=args.kinds,
        )
        print(f"mode:            purge / {result['mode']}")
        print(f"company_id:      {result['company_id']}")
        print(f"kinds:           {', '.join(result['kinds'])}")
        print(f"examined:        {result['examined']}")
        print(f"cleared:         {result['cleared']}")
        _print_accounts(result)
        if not args.apply and result["cleared"]:
            print(
                "\nDRY RUN — nothing was cleared. Re-run with --purge --apply to "
                f"clear {result['cleared']} signal(s)."
            )
        print("\n" + json.dumps(result, indent=2))
        return

    result = run_account_backfill(
        company_id=args.company, apply=args.apply, limit=args.limit,
        kinds=args.kinds,
    )

    print(f"mode:            {result['mode']}")
    print(f"company_id:      {result['company_id']}")
    print(f"pattern_version: {result['pattern_version']}")
    print(f"run_id:          {result['run_id']}")
    print(f"kinds:           {', '.join(result['kinds'])}")
    print(f"examined:        {result['examined']}")
    print(f"attributed:      {result['enriched']}")
    print(f"skipped total:   {result['total_skipped']}")
    for reason, count in result["skipped"].items():
        print(f"  - {reason}: {count}")
    _print_accounts(result)
    if not args.apply and result["enriched"]:
        print(
            "\nDRY RUN — nothing was written. Re-run with --apply to attribute "
            f"{result['enriched']} signal(s)."
        )
    print("\n" + json.dumps(result, indent=2))


if __name__ == "__main__":
    sys.exit(main())

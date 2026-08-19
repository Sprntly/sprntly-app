"""READ-ONLY: could anything we run reach a real person at the spike tenant?

Writes nothing, sends nothing. Answers one question: does this company hold
addresses in the tables that FEED outbound mail/notifications (members, invites,
brief delivery, Slack targets), as opposed to merely holding third-party
addresses as inert evidence inside call transcripts and signals.

The distinction is the whole point. An address sitting in `kg_signal.properties`
because a Fireflies transcript listed a meeting participant is data. An address
in `company_members` or a brief schedule is a RECIPIENT.
"""
from __future__ import annotations

import argparse
import collections
import json
import re

from app.db.client import require_client

EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def probe(table: str, key: str, cid: str, cols: str = "*") -> tuple[list[dict], str | None]:
    try:
        rows = (require_client().table(table).select(cols).eq(key, cid)
                .limit(500).execute()).data or []
        return rows, None
    except Exception as e:                      # table/column drift, not fatal
        return [], f"{type(e).__name__}: {e}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--domain", required=True, help="the tenant's own email domain")
    a = ap.parse_args()
    cid, domain = a.company, a.domain.lower()

    print("=" * 72)
    print("RECIPIENT SURFACES — tables that can cause an outbound message")
    print("=" * 72)
    for table, key in (
        ("company_members", "company_id"),
        ("invites", "company_id"),
        ("notification_settings", "company_id"),
        ("connections", "company_id"),
    ):
        rows, err = probe(table, key, cid)
        if err:
            print(f"\n{table}: (not readable) {err[:90]}")
            continue
        blob = json.dumps(rows, default=str)
        hits = sorted({e.lower() for e in EMAIL.findall(blob)})
        at_tenant = [h for h in hits if h.endswith("@" + domain)]
        print(f"\n{table}: {len(rows)} row(s)")
        print(f"  addresses present : {hits if hits else 'none'}")
        print(f"  AT TENANT DOMAIN  : {at_tenant if at_tenant else 'NONE'}")

    print()
    print("=" * 72)
    print("EVIDENCE SURFACES — addresses held as data, not as recipients")
    print("=" * 72)
    sigs: list[dict] = []
    i = 0
    while i < 40:
        chunk = (require_client().table("kg_signal")
                 .select("content,properties")
                 .eq("enterprise_id", cid)
                 .range(i * 1000, i * 1000 + 999).execute()).data or []
        sigs.extend(chunk)
        if len(chunk) < 1000:
            break
        i += 1
    blob = json.dumps(sigs, default=str)
    found = collections.Counter(e.lower() for e in EMAIL.findall(blob))
    tenant_side = {e: n for e, n in found.items() if e.endswith("@" + domain)}
    print(f"\nkg_signal: {len(sigs)} rows scanned")
    print(f"  distinct addresses         : {len(found)}")
    print(f"  at the tenant's own domain : {len(tenant_side)}")
    for e, n in sorted(tenant_side.items(), key=lambda kv: -kv[1])[:15]:
        print(f"    {e}  x{n}")
    other = [e for e in found if not e.endswith("@" + domain)]
    print(f"  at OTHER domains (their customers/prospects): {len(other)}")
    for e in sorted(other)[:15]:
        print(f"    {e}")


if __name__ == "__main__":
    main()

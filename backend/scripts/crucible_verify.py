"""READ-ONLY adversarial check on one spike finding. Writes nothing.

The spike proposed: a promised integration is repeatedly dated "1-2 weeks" to
several accounts and is still not shipped. That is only a finding if the
mentions are SPREAD OVER TIME. If they all landed in one week it is one
conversation echoing, not a pattern, and the finding should die here.
"""
from __future__ import annotations

import argparse
import re

from app.db.client import require_client


def page(cid: str, cols: str) -> list[dict]:
    c = require_client()
    rows: list[dict] = []
    i = 0
    while i < 40:
        chunk = (c.table("kg_signal").select(cols).eq("enterprise_id", cid)
                 .range(i * 1000, i * 1000 + 999).execute()).data or []
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        i += 1
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--term", required=True)
    a = ap.parse_args()

    sigs = page(a.company, "id,kind,source_type,content,properties,valid_at")
    term = a.term.lower()
    hits = [s for s in sigs if term in (s.get("content") or "").lower()]
    hits.sort(key=lambda s: str(s.get("valid_at")))

    print(f"signals mentioning {a.term!r}: {len(hits)}\n")
    weeks = set()
    for s in hits:
        d = str(s.get("valid_at"))[:10]
        weeks.add(str(s.get("valid_at"))[:7])
        props = s.get("properties") or {}
        who = ""
        if isinstance(props, dict):
            for k in ("customer", "poc_customer", "account", "prospect", "organization"):
                if props.get(k):
                    who = f"[{props[k]}]"
                    break
        eta = "  <<ETA" if re.search(r"\b1\s*[-–]\s*2\s*weeks?\b|\bnext week\b|\bweeks?\b",
                                     (s.get("content") or "").lower()) else ""
        print(f"{d}  {s.get('kind'):<16} {who:<32} {(s.get('content') or '')[:150]}{eta}")

    print(f"\ndistinct months mentioned: {sorted(weeks)}")


if __name__ == "__main__":
    main()

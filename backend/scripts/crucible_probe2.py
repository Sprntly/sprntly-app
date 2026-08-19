"""READ-ONLY: what does a tenant's signals actually carry? Writes nothing.

Sizing is the question. A claim can only be sized if something on it names a
population or a value, so this counts which `properties` keys exist and how
often, plus the edge vocabulary and the entity types available to group by.
"""
from __future__ import annotations

import argparse
import collections
import json

from app.db.client import require_client


def page(table: str, key: str, cid: str, cols: str, cap: int = 40) -> list[dict]:
    c = require_client()
    rows: list[dict] = []
    i = 0
    while i < cap:
        chunk = (
            c.table(table).select(cols).eq(key, cid)
            .range(i * 1000, i * 1000 + 999).execute()
        ).data or []
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        i += 1
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    a = ap.parse_args()
    cid = a.company

    sig = page("kg_signal", "enterprise_id", cid,
               "id,kind,source_type,properties,confidence,weight")
    print(f"signals: {len(sig)}")

    keys = collections.Counter()
    numeric_keys = collections.Counter()
    for s in sig:
        p = s.get("properties") or {}
        if not isinstance(p, dict):
            continue
        for k, v in p.items():
            keys[k] += 1
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                numeric_keys[k] += 1
    print("\nproperties keys (top 30):")
    for k, n in keys.most_common(30):
        print(f"  {k:34s} {n:5d}   numeric={numeric_keys.get(k, 0)}")

    print("\nsample properties payloads:")
    shown = 0
    for s in sig:
        p = s.get("properties") or {}
        if isinstance(p, dict) and len(p) >= 2:
            print(f"  [{s.get('kind')}] {json.dumps(p, default=str)[:300]}")
            shown += 1
            if shown >= 6:
                break

    print("\nconfidence distribution:",
          dict(collections.Counter(round(float(s.get("confidence") or 0), 1)
                                   for s in sig).most_common()))
    emb = -1
    print(f"signals with embedding: {emb} / {len(sig)}")

    ent = page("kg_entity", "enterprise_id", cid, "id,type,canonical_label")
    print(f"\nentities: {len(ent)}")
    print("entity types:", dict(collections.Counter(e.get("type") for e in ent).most_common()))

    rel = page("kg_relationship", "enterprise_id", cid, "type,source_kind,source_id,target_kind,target_id")
    print(f"\nedges: {len(rel)}")
    print("edge types:", dict(collections.Counter(r.get("type") for r in rel).most_common()))


if __name__ == "__main__":
    main()

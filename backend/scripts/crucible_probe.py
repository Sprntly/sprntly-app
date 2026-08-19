"""READ-ONLY probe: inventory one company's evidence for the Goal Analysis spike.

Writes nothing. Counts what Crucible would have to read for a given tenant:
KG signals by source_type/kind, catalogued documents by provider, and the
company's own KPI tree (the candidate goal definitions Stage 0 would adopt).

    python scripts/crucible_probe.py --find "chaos"
    python scripts/crucible_probe.py --company <uuid>
"""
from __future__ import annotations

import argparse
import collections
import json
import sys

from app.db.client import require_client


def find_companies(term: str) -> list[dict]:
    c = require_client()
    res = c.table("companies").select("id,slug,display_name,kpi_tree").or_(
        f"slug.ilike.%{term}%,display_name.ilike.%{term}%"
    ).execute()
    return res.data or []


def inventory(company_id: str) -> dict:
    c = require_client()
    out: dict = {"company_id": company_id}

    row = (
        c.table("companies").select("id,slug,display_name,kpi_tree")
        .eq("id", company_id).limit(1).execute()
    ).data
    out["company"] = row[0] if row else None

    sig: list[dict] = []
    page = 0
    while True:
        chunk = (
            c.table("kg_signal")
            .select("source_type,kind,origin,valid_at,content")
            .eq("enterprise_id", company_id)
            .range(page * 1000, page * 1000 + 999)
            .execute()
        ).data or []
        sig.extend(chunk)
        if len(chunk) < 1000 or page > 30:
            break
        page += 1
    out["signal_count"] = len(sig)
    out["by_source_type"] = dict(
        collections.Counter(s.get("source_type") for s in sig).most_common()
    )
    out["by_kind"] = dict(collections.Counter(s.get("kind") for s in sig).most_common(20))
    out["by_origin"] = dict(collections.Counter(s.get("origin") for s in sig).most_common())
    dates = sorted(s.get("valid_at") or "" for s in sig)
    out["valid_at_range"] = [dates[0], dates[-1]] if dates else None
    out["sample_content"] = [(s.get("content") or "")[:160] for s in sig[:5]]

    try:
        docs = (
            c.table("document_catalog")
            .select("provider,title,doc_date")
            .eq("company_id", company_id)
            .limit(2000)
            .execute()
        ).data or []
        out["document_count"] = len(docs)
        out["docs_by_provider"] = dict(
            collections.Counter(d.get("provider") for d in docs).most_common()
        )
        out["doc_titles"] = [d.get("title") for d in docs[:25]]
    except Exception as e:  # table/column drift should not kill the probe
        out["document_catalog_error"] = f"{type(e).__name__}: {e}"

    for table, key in (
        ("kg_entity", "enterprise_id"),
        ("kg_source", "enterprise_id"),
        ("reports", "company_id"),
        ("prds", "company_id"),
        ("conversations", "company_id"),
    ):
        try:
            r = c.table(table).select("id", count="exact").eq(key, company_id).limit(1).execute()
            out[f"{table}_count"] = r.count
        except Exception as e:
            out[f"{table}_error"] = f"{type(e).__name__}: {e}"

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--find")
    ap.add_argument("--company")
    a = ap.parse_args()

    if a.find:
        rows = find_companies(a.find)
        for r in rows:
            print(json.dumps({k: r.get(k) for k in ("id", "slug", "display_name")}, default=str)[:800])
        if not rows:
            print("no match")
        return 0

    if a.company:
        print(json.dumps(inventory(a.company), indent=2, default=str))
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Use the knowledge graph's OWN themes to group claims.

WHY THIS REPLACED EMBEDDING CLUSTERING AS THE PRIMARY PATH.

`app/crucible/cluster.py` derives themes by clustering signal embeddings. It
works, and on a real tenant it produced 1,744 groups labelled with truncated
sentences — "Completed MRT stages currently cannot be revised, leaving
documentation errors…". Meanwhile the graph beside it already held **2,345
theme entities** joined to **12,932 of 15,569 signals** by `signal -> entity`
relationships, labelled by the extractor as "Parts request dashboard", "Bulk
Pause Endpoint", "Machine record data quality".

So the engine was re-deriving, worse, semantics the KG had already computed and
stored. That is the answer to "why aren't we understanding the knowledge
graph": we weren't reading it. Themes first, embeddings only for the signals
the graph left unthemed.

WHAT THIS IS NOT. It is not a second source of truth. A theme edge is an
assignment the extractor already made and a human can already see elsewhere in
the product, so grouping by it makes a run's output line up with the rest of
Sprntly rather than inventing a private taxonomy.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Iterable, Mapping, Optional, Sequence

from app.crucible.types import Claim

logger = logging.getLogger(__name__)

#: Rows per page. Relationships are small; entities carry an embedding, so they
#: are fetched by explicit id in batches instead of scanned.
_REL_PAGE = 1000
_ENTITY_BATCH = 200

#: `kg_entity.type` that represents a theme. The graph also holds hypothesis /
#: artifact / decision entities, which are NOT groupings of evidence and would
#: merge unrelated signals if treated as such.
THEME_TYPE = "theme"


def load_theme_map(company_id: str) -> dict[str, tuple[str, str]]:
    """signal id -> (theme entity id, theme label), from the graph.

    Read in two steps because the tables are shaped very differently: the
    relationship rows are narrow and page cheaply, while `kg_entity` carries an
    embedding column that makes a scan expensive — so entities are fetched by
    the ids the relationships actually referenced.
    """
    from app.db.client import require_client

    client = require_client()

    edges: list[dict] = []
    for page in range(60):
        try:
            chunk = (
                client.table("kg_relationship")
                .select("source_id,target_id,type")
                .eq("enterprise_id", company_id)
                .eq("source_kind", "signal")
                .eq("target_kind", "entity")
                .order("id")
                .range(page * _REL_PAGE, page * _REL_PAGE + _REL_PAGE - 1)
                .execute()
            ).data or []
        except Exception:  # noqa: BLE001 — no graph is a degraded run, not a
            # dead one: clustering falls back to embeddings.
            logger.exception("crucible: theme edges unreadable for %s", company_id)
            return {}
        edges.extend(chunk)
        if len(chunk) < _REL_PAGE:
            break

    if not edges:
        return {}

    target_ids = sorted({e["target_id"] for e in edges if e.get("target_id")})
    themes: dict[str, str] = {}
    for i in range(0, len(target_ids), _ENTITY_BATCH):
        batch = target_ids[i : i + _ENTITY_BATCH]
        try:
            rows = (
                client.table("kg_entity")
                .select("id,type,canonical_label")
                .in_("id", batch)
                .execute()
            ).data or []
        except Exception:  # noqa: BLE001 — a lost batch costs those themes only
            logger.warning("crucible: entity batch %d unreadable", i // _ENTITY_BATCH)
            continue
        for r in rows:
            if r.get("type") == THEME_TYPE and (r.get("canonical_label") or "").strip():
                themes[r["id"]] = r["canonical_label"].strip()

    out: dict[str, tuple[str, str]] = {}
    for e in edges:
        label = themes.get(e.get("target_id"))
        if not label:
            continue
        signal_id = str(e.get("source_id"))
        # FIRST EDGE WINS, in id order. A signal can be joined to several
        # themes; picking deterministically matters more than picking the
        # "best" one, because a run that groups differently on a re-run is not
        # reproducible, which is the whole claim this engine makes.
        out.setdefault(signal_id, (e["target_id"], label))
    return out


def assign_themes(
    claims: Sequence[Claim],
    theme_map: Mapping[str, tuple[str, str]],
) -> tuple[list[Claim], list[int], dict]:
    """Stamp the graph's theme onto every claim it knows about.

    Returns the claims, the indexes of those the graph did NOT theme (so the
    caller can fall back to embeddings for exactly those), and stats.
    """
    out = list(claims)
    unthemed: list[int] = []
    for i, claim in enumerate(claims):
        found = theme_map.get(str(claim.id))
        if not found:
            unthemed.append(i)
            continue
        entity_id, label = found
        out[i] = replace(out[i], subject_cluster_id=f"kg:{entity_id}", subject=label)
    return out, unthemed, {
        "themed": len(claims) - len(unthemed),
        "unthemed": len(unthemed),
        "kg_themes": len({v[0] for v in theme_map.values()}),
    }

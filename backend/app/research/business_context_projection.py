"""Project the BusinessContext doc into the knowledge graph.

The doc (companies.business_context) is the source of truth; this is a derived,
IDEMPOTENT projection so downstream agents can read segments/constraints as
first-class KG nodes:

  - users_segments.segments[]  → `segment` entities (find-or-create by embedding,
                                  like competitor entities).
  - market_competition.main_alternatives → `competitor` entities ensured to exist.
  - goals_strategy.known_constraints + business_model.good_outcome → signals,
    source_type "pm_manual" when the leaf's src is user-authoritative, else
    "agent_inferred".

Idempotency: entities are deduped by embedding similarity (τ_high); signals are
deduped by a stable content key recorded in properties — a re-run finds the
existing signal and skips it rather than appending a duplicate.
"""
from __future__ import annotations

import logging

from app.business_context import BusinessContext, Meta
from app.graph.config_layers import resolve_config
from app.graph.embeddings import embed_texts
from app.graph.facade import GraphFacade
from app.graph.types import Entity, Signal

logger = logging.getLogger(__name__)

AGENT = "business_context"


def _ensure_entity(
    facade: GraphFacade, enterprise_id: str, type_: str, label: str, tau_high: float
) -> str:
    """find-or-create an entity of `type_` for `label` (embedding kNN dedupe)."""
    vec = embed_texts([label])[0]
    candidates = facade.find_candidates(enterprise_id, type_, vec, k=3)
    if candidates and candidates[0][1] >= tau_high:
        return candidates[0][0].id
    ent = Entity(
        enterprise_id=enterprise_id, type=type_, canonical_label=label,
        embedding=vec, provenance={"agent": AGENT},
    )
    facade.create_entity(enterprise_id, ent)
    return ent.id


def _signal_exists(facade: GraphFacade, enterprise_id: str, key: str) -> bool:
    """A signal carrying this stable `bizctx_key` already lives in the graph?"""
    for sig in facade.active_signals(enterprise_id):
        if (sig.properties or {}).get("bizctx_key") == key:
            return True
    return False


def _ensure_signal(
    facade: GraphFacade, enterprise_id: str, *, key: str, kind: str,
    content: str, src_meta: Meta,
) -> bool:
    """Write a signal once (deduped by `key`). source_type is pm_manual for a
    user-authoritative leaf, else agent_inferred. Returns True if created."""
    if _signal_exists(facade, enterprise_id, key):
        return False
    source_type = "pm_manual" if src_meta.is_user_authoritative else "agent_inferred"
    facade.write_signal(enterprise_id, Signal(
        enterprise_id=enterprise_id, source_type=source_type, kind=kind,
        content=content,
        properties={"bizctx_key": key, "agent": AGENT},
        provenance={"agent": AGENT, "src": src_meta.src},
    ))
    return True


def project_business_context(
    facade: GraphFacade, enterprise_id: str, doc: BusinessContext
) -> dict:
    """Idempotently project the doc into the KG. Returns counts of what was
    created this run (existing nodes are left untouched)."""
    cfg = resolve_config(enterprise_id)
    tau_high = cfg["resolution"]["tau_high"]
    created = {"segments": 0, "competitors": 0, "signals": 0}

    # Segments → segment entities.
    for seg in doc.users_segments.segments:
        nm = seg.name
        if not (isinstance(nm, Meta) and nm.is_known):
            continue
        label = str(nm.value).strip()
        if not label:
            continue
        before = len(facade.query_entities(enterprise_id, type="segment"))
        _ensure_entity(facade, enterprise_id, "segment", label, tau_high)
        after = len(facade.query_entities(enterprise_id, type="segment"))
        created["segments"] += max(0, after - before)

    # main_alternatives → competitor entities ensured to exist.
    alts = doc.market_competition.main_alternatives
    if isinstance(alts, Meta) and alts.is_known:
        names = alts.value if isinstance(alts.value, list) else [alts.value]
        for raw in names:
            label = str(raw).strip()
            if not label or label.lower() in ("diy", "do nothing", "diy/do nothing"):
                continue
            before = len(facade.query_entities(enterprise_id, type="competitor"))
            _ensure_entity(facade, enterprise_id, "competitor", label, tau_high)
            after = len(facade.query_entities(enterprise_id, type="competitor"))
            created["competitors"] += max(0, after - before)

    # known_constraints → signals.
    kc = doc.goals_strategy.known_constraints
    if isinstance(kc, Meta) and kc.is_known:
        items = kc.value if isinstance(kc.value, list) else [kc.value]
        for raw in items:
            text = str(raw).strip()
            if not text:
                continue
            key = f"constraint:{text.lower()}"
            if _ensure_signal(facade, enterprise_id, key=key, kind="constraint",
                              content=text, src_meta=kc):
                created["signals"] += 1

    # good_outcome → a signal (the lens for ranking).
    go = doc.business_model.good_outcome
    if isinstance(go, Meta) and go.is_known:
        text = str(go.value).strip()
        if text:
            key = f"good_outcome:{text.lower()}"
            if _ensure_signal(facade, enterprise_id, key=key, kind="good_outcome",
                              content=text, src_meta=go):
                created["signals"] += 1

    return created

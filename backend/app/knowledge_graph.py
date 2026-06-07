"""Lightweight knowledge graph: entity extraction + NetworkX in-memory graph.

Entities and relationships are extracted by Claude from the corpus,
persisted in Supabase, and loaded into a NetworkX DiGraph for
signal clustering during brief generation.
"""
from __future__ import annotations

import logging
from typing import Any

import networkx as nx

from app.corpus import load_corpus
from app.db.knowledge import (
    clear_entities,
    list_entities,
    list_relationships,
    upsert_entity,
    upsert_relationship,
)
from app.llm import call_json

logger = logging.getLogger(__name__)

ENTITY_EXTRACTION_SYSTEM = """\
You are a knowledge extraction system. Given a product knowledge corpus,
extract structured entities and relationships.

Entity types:
- product: Products, features, or services
- metric: KPIs, measurements, scores (e.g., NPS, DAU, churn rate)
- customer_segment: User groups, personas, market segments
- competitor: Named competitors
- trend: Market trends, growth patterns, emerging themes
- issue: Problems, bugs, complaints, pain points
- opportunity: Untapped potential, growth areas, feature gaps

Relationship types:
- drives: X drives Y (e.g., "API Access" drives "revenue growth")
- correlates_with: X correlates with Y
- competes_with: X competes with Y
- indicates: X indicates Y (e.g., "NPS drop" indicates "churn risk")
- blocks: X blocks Y
- part_of: X is part of Y

Rules:
- Extract 10-30 entities max (focus on the most important)
- Extract 10-40 relationships max
- Confidence: 0.9 for explicit data, 0.7 for strong inference, 0.5 for weak signal
- Include the source filename for each entity
- Relationship evidence should be a short quote or paraphrase from the corpus
"""

ENTITY_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "name": {"type": "string"},
                    "attributes": {"type": "object"},
                    "source": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["type", "name", "confidence"],
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_entity": {"type": "string"},
                    "target_entity": {"type": "string"},
                    "relation": {"type": "string"},
                    "weight": {"type": "number"},
                    "evidence": {"type": "string"},
                },
                "required": ["source_entity", "target_entity", "relation"],
            },
        },
    },
    "required": ["entities", "relationships"],
}


def extract_entities(dataset: str) -> dict[str, Any]:
    """Extract entities and relationships from the corpus using Claude.

    Returns the raw extraction result dict.
    """
    try:
        corpus = load_corpus(dataset)
    except (FileNotFoundError, RuntimeError):
        logger.warning("No corpus for %s, skipping entity extraction", dataset)
        return {"entities": [], "relationships": []}

    user = (
        f"Dataset: {dataset}\n\n"
        f"Corpus:\n\n{corpus.joined()}\n\n"
        f"Extract the key entities and relationships from this knowledge base."
    )

    return call_json(
        system=ENTITY_EXTRACTION_SYSTEM,
        user=user,
        schema=ENTITY_EXTRACTION_SCHEMA,
        max_tokens=8000,
        user_cacheable_prefix=f"Corpus:\n\n{corpus.joined()}",
    )


def persist_entities(dataset: str, extraction: dict[str, Any]) -> dict[str, int]:
    """Persist extracted entities and relationships to Supabase.

    Clears existing entities for the dataset and re-inserts.
    Returns counts.
    """
    clear_entities(dataset)

    entity_map: dict[str, int] = {}  # name -> id
    entity_count = 0

    for ent in extraction.get("entities", []):
        name = ent.get("name", "").strip()
        if not name:
            continue
        row = upsert_entity(
            dataset=dataset,
            entity_type=ent.get("type", "unknown"),
            name=name,
            attributes=ent.get("attributes") or {},
            source_file=ent.get("source"),
            confidence=ent.get("confidence", 0.5),
        )
        entity_map[name] = row.get("id", 0)
        entity_count += 1

    rel_count = 0
    for rel in extraction.get("relationships", []):
        src_name = rel.get("source_entity", "").strip()
        tgt_name = rel.get("target_entity", "").strip()
        src_id = entity_map.get(src_name)
        tgt_id = entity_map.get(tgt_name)
        if not src_id or not tgt_id:
            continue
        upsert_relationship(
            dataset=dataset,
            source_entity_id=src_id,
            target_entity_id=tgt_id,
            relation=rel.get("relation", "related"),
            weight=rel.get("weight", 1.0),
            evidence=rel.get("evidence"),
        )
        rel_count += 1

    return {"entities": entity_count, "relationships": rel_count}


def build_graph(dataset: str) -> nx.DiGraph:
    """Load entities + relationships from Supabase and build a NetworkX graph."""
    G = nx.DiGraph()

    entities = list_entities(dataset)
    for ent in entities:
        G.add_node(
            ent["id"],
            name=ent["name"],
            entity_type=ent["entity_type"],
            confidence=ent.get("confidence", 0.5),
            attributes=ent.get("attributes", {}),
            source_file=ent.get("source_file"),
        )

    relationships = list_relationships(dataset)
    for rel in relationships:
        G.add_edge(
            rel["source_entity_id"],
            rel["target_entity_id"],
            relation=rel["relation"],
            weight=rel.get("weight", 1.0),
            evidence=rel.get("evidence"),
        )

    return G


def refresh_graph(dataset: str) -> dict[str, Any]:
    """Full refresh: extract entities from corpus → persist → build graph.

    Returns stats about the graph.
    """
    extraction = extract_entities(dataset)
    counts = persist_entities(dataset, extraction)
    G = build_graph(dataset)

    stats = {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        **counts,
    }
    logger.info("Knowledge graph refreshed for %s: %s", dataset, stats)
    return stats


def get_signal_clusters(dataset: str) -> list[dict[str, Any]]:
    """Find convergence signal clusters in the knowledge graph.

    Returns clusters of related entities sorted by aggregate confidence,
    used by signal fusion to weight brief insights.
    """
    G = build_graph(dataset)
    if G.number_of_nodes() == 0:
        return []

    clusters: list[dict[str, Any]] = []

    # Find high-centrality nodes (entities connected to many others)
    if G.number_of_nodes() > 1:
        try:
            centrality = nx.degree_centrality(G)
        except Exception:
            centrality = {}
    else:
        centrality = {n: 1.0 for n in G.nodes}

    # Build clusters around high-centrality nodes
    for node_id, cent_score in sorted(centrality.items(), key=lambda x: -x[1])[:10]:
        node_data = G.nodes[node_id]
        neighbors = []

        for neighbor_id in G.successors(node_id):
            edge_data = G.edges[node_id, neighbor_id]
            n_data = G.nodes[neighbor_id]
            neighbors.append({
                "name": n_data.get("name", ""),
                "type": n_data.get("entity_type", ""),
                "relation": edge_data.get("relation", ""),
                "evidence": edge_data.get("evidence", ""),
            })

        for neighbor_id in G.predecessors(node_id):
            edge_data = G.edges[neighbor_id, node_id]
            n_data = G.nodes[neighbor_id]
            neighbors.append({
                "name": n_data.get("name", ""),
                "type": n_data.get("entity_type", ""),
                "relation": f"(inverse) {edge_data.get('relation', '')}",
                "evidence": edge_data.get("evidence", ""),
            })

        if neighbors:
            clusters.append({
                "central_entity": node_data.get("name", ""),
                "entity_type": node_data.get("entity_type", ""),
                "centrality": cent_score,
                "confidence": node_data.get("confidence", 0.5),
                "connections": neighbors,
                "source_diversity": len(set(
                    n.get("type", "") for n in neighbors
                )),
            })

    return clusters

"""Lightweight knowledge graph: NetworkX in-memory signal clustering.

Entities and relationships persisted in Supabase are loaded into a NetworkX
DiGraph and clustered into convergence signals, used by signal fusion to weight
brief insights (the legacy CLI brief path, app.signal_fusion.fuse_signals).
"""
from __future__ import annotations

from typing import Any

import networkx as nx

from app.db.knowledge import (
    list_entities,
    list_relationships,
)


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

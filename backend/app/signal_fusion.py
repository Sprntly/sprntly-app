"""Signal fusion: aggregate and rank signals before brief generation.

Gathers corpus metadata, knowledge graph clusters, and source freshness
to produce a ranked signal context that prepends the brief prompt.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from app.config import settings
from app.corpus import load_corpus

logger = logging.getLogger(__name__)

# Source type weights: how much to trust each source type
SOURCE_WEIGHTS: dict[str, float] = {
    "upload": 1.0,         # User-uploaded analytics files
    "slack": 0.8,          # Real-time team conversations
    "hubspot": 0.9,        # CRM data (structured)
    "google_drive": 0.8,   # Shared documents
    "github": 0.7,         # Engineering signals
    "marketing_agent": 0.6,  # Scraped, less reliable
    "competitor_agent": 0.6,
    "ds_agent": 0.9,       # DS Agent analysis output
    "unknown": 0.5,
}


def _infer_source_type(filename: str) -> str:
    """Infer the data source from a corpus filename."""
    name = filename.lower()
    if name.startswith("slack_"):
        return "slack"
    if name.startswith("hubspot_"):
        return "hubspot"
    if name.startswith("marketing_"):
        return "marketing_agent"
    if name.startswith("competitor_"):
        return "competitor_agent"
    if name.startswith("ds_agent"):
        return "ds_agent"
    if name.startswith("github_"):
        return "github"
    if name.startswith("google_drive_") or name.startswith("gdrive_"):
        return "google_drive"
    if name.startswith("onboarding_"):
        return "upload"
    return "upload"


def _freshness_score(file_path: str) -> float:
    """Score 0-1 based on how recent the file is.

    Last 24h = 1.0, last 7d = 0.7, last 30d = 0.5, older = 0.3
    """
    try:
        mtime = os.path.getmtime(file_path)
        age_hours = (time.time() - mtime) / 3600
        if age_hours < 24:
            return 1.0
        if age_hours < 168:  # 7 days
            return 0.7
        if age_hours < 720:  # 30 days
            return 0.5
        return 0.3
    except OSError:
        return 0.3


def fuse_signals(dataset: str) -> str:
    """Build a ranked signal context string for the brief prompt.

    Analyzes corpus files by source type, freshness, and KG clusters
    to produce a priority-ordered summary the brief LLM can use.
    """
    try:
        corpus = load_corpus(dataset)
    except (FileNotFoundError, RuntimeError):
        return ""

    # Build per-source metadata
    source_signals: list[dict[str, Any]] = []

    for doc in corpus.docs:
        source_type = _infer_source_type(doc.name)
        freshness = _freshness_score(doc.path)
        source_weight = SOURCE_WEIGHTS.get(source_type, 0.5)
        score = freshness * source_weight
        char_count = len(doc.text)

        source_signals.append({
            "name": doc.name,
            "source_type": source_type,
            "freshness": freshness,
            "source_weight": source_weight,
            "score": score,
            "chars": char_count,
        })

    # Sort by score descending
    source_signals.sort(key=lambda s: -s["score"])

    # Count source diversity
    source_types = set(s["source_type"] for s in source_signals)
    diversity_bonus = 1.5 if len(source_types) >= 3 else 1.0

    # Try to load KG clusters (graceful fallback if tables don't exist)
    clusters_text = ""
    try:
        from app.knowledge_graph import get_signal_clusters
        clusters = get_signal_clusters(dataset)
        if clusters:
            cluster_lines = []
            for cl in clusters[:5]:
                connections_str = ", ".join(
                    f"{c['name']} ({c['relation']})" for c in cl["connections"][:5]
                )
                cluster_lines.append(
                    f"- **{cl['central_entity']}** ({cl['entity_type']}, "
                    f"confidence={cl['confidence']:.1f}, "
                    f"connections={len(cl['connections'])}): "
                    f"{connections_str}"
                )
            clusters_text = (
                "\n### Knowledge Graph Clusters\n"
                "These entities have the strongest cross-source connections:\n\n"
                + "\n".join(cluster_lines) + "\n"
            )
    except Exception:
        pass  # KG tables may not exist yet

    # Format the signal context
    lines = [
        f"### Signal Ranking ({len(source_signals)} sources, "
        f"{len(source_types)} source types, "
        f"diversity bonus: {diversity_bonus:.1f}x)\n",
        "| Priority | Source | Type | Freshness | Weight | Score |",
        "|----------|--------|------|-----------|--------|-------|",
    ]
    for i, sig in enumerate(source_signals, 1):
        lines.append(
            f"| {i} | {sig['name']} | {sig['source_type']} | "
            f"{sig['freshness']:.1f} | {sig['source_weight']:.1f} | "
            f"{sig['score']:.2f} |"
        )

    lines.append(
        f"\n**Prioritization guidance:** Insights backed by 3+ source types "
        f"should rank higher. Source types present: {', '.join(sorted(source_types))}."
    )

    if clusters_text:
        lines.append(clusters_text)

    return "\n".join(lines)

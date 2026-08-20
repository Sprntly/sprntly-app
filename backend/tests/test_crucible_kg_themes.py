"""Grouping by the knowledge graph's OWN themes.

This module exists because the engine was re-deriving, worse, semantics the KG
had already computed. Embedding clustering produced 1,744 groups labelled with
truncated sentences; the graph beside it held 2,345 theme entities joined to
12,932 of 15,569 signals, labelled "Parts request dashboard", "Bulk Pause
Endpoint". Same corpus, after switching: 165 findings -> 430, and sizeable
findings 3 -> 24.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.crucible.kg_themes import assign_themes
from app.crucible.types import Claim, PopulationFilter

NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)


def claim(cid: str) -> Claim:
    return Claim(
        id=cid, assertion=f"claim {cid}", type="mechanism", subject="",
        source_id="cv", artifact_id="doc", artifact_type="t",
        strength="reported", observed_at=NOW, authoritative=True,
        population=PopulationFilter(), direction="neutral",
    )


def test_a_claim_takes_the_graphs_theme_and_its_label():
    claims = [claim("s1"), claim("s2")]
    out, unthemed, stats = assign_themes(
        claims, {"s1": ("e1", "Parts request dashboard"),
                 "s2": ("e1", "Parts request dashboard")})
    assert out[0].subject == "Parts request dashboard"
    assert out[0].subject_cluster_id == out[1].subject_cluster_id
    assert stats["themed"] == 2 and stats["unthemed"] == 0


def test_two_themes_are_two_groups():
    out, _, _ = assign_themes(
        [claim("s1"), claim("s2")],
        {"s1": ("e1", "Bulk Pause Endpoint"), "s2": ("e2", "MCP integration")})
    assert out[0].subject_cluster_id != out[1].subject_cluster_id


def test_an_unthemed_claim_is_HANDED_BACK_not_guessed_at():
    """The graph themed 83% of a real tenant, not 100%. The rest must reach the
    embedding fallback rather than being left unset — unset falls through to
    grouping by kind, which is the failure that started all of this."""
    out, unthemed, stats = assign_themes(
        [claim("s1"), claim("s2")], {"s1": ("e1", "Sales Pipeline")})
    assert unthemed == [1]
    assert out[1].subject_cluster_id is None
    assert stats["unthemed"] == 1


def test_no_graph_at_all_hands_everything_back():
    out, unthemed, stats = assign_themes([claim("s1"), claim("s2")], {})
    assert unthemed == [0, 1]
    assert stats["themed"] == 0


def test_the_cluster_id_is_namespaced_so_it_cannot_collide():
    """Embedding clusters are `c0`, `c1`… and ungroupable ones carry their own
    marker. A graph theme keyed on a bare entity id could collide with either."""
    out, _, _ = assign_themes([claim("s1")], {"s1": ("c0", "Sales Pipeline")})
    assert out[0].subject_cluster_id == "kg:c0"

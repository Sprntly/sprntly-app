"""Tests for `app.graph` — meta-model dataclasses + GraphFacade + decision log.

Uses the shared isolated_settings fixture (in-memory fake Supabase). pgvector
`find_candidates` is integration-tested separately against real Supabase;
in the fake it returns []."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# ---------- types ----------

def test_signal_auto_stale_after_per_source_type(isolated_settings):
    from app.graph.types import (
        Signal,
        SOURCE_STALE_WINDOW_DAYS,
    )

    valid = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
    cases = {
        "analytics":      30,
        "project_mgmt":   14,
        "communication":   7,
        "customer_voice": 30,
        "revenue":        30,
        "pm_manual":      60,
        "agent_inferred": 14,
    }
    for src, days in cases.items():
        s = Signal(enterprise_id="e", source_type=src, kind="x", content="c", valid_at=valid)
        assert s.stale_after == valid + timedelta(days=days), src
        assert SOURCE_STALE_WINDOW_DAYS[src] == days

    # outcome_measured never expires
    s_out = Signal(enterprise_id="e", source_type="outcome_measured", kind="x",
                   content="c", valid_at=valid)
    assert s_out.stale_after is None


def test_signal_rejects_unknown_source_type(isolated_settings):
    from app.graph.types import Signal
    with pytest.raises(ValueError, match="source_type"):
        Signal(enterprise_id="e", source_type="not_a_real_type", kind="x", content="c")


def test_relationship_validates_closed_vocab(isolated_settings):
    from app.graph.types import Relationship
    Relationship(enterprise_id="e", type="ADDRESSES", source_kind="entity",
                 source_id="a", target_kind="entity", target_id="b")
    with pytest.raises(ValueError, match="closed vocabulary"):
        Relationship(enterprise_id="e", type="NOT_A_REAL_EDGE", source_kind="entity",
                     source_id="a", target_kind="entity", target_id="b")


def test_relationship_validates_node_kinds(isolated_settings):
    from app.graph.types import Relationship
    with pytest.raises(ValueError, match="source_kind"):
        Relationship(enterprise_id="e", type="ADDRESSES", source_kind="bogus",
                     source_id="a", target_kind="entity", target_id="b")


# ---------- facade ----------

@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


@pytest.fixture
def ent_id():
    # Use string IDs (fake Supabase uses TEXT primary keys).
    return "ent-a-001"


def test_create_and_get_entity(facade):
    from app.graph import Entity
    e = Entity(enterprise_id="ent-A", type="theme", canonical_label="AI authoring",
               aliases=["in-editor AI writing"], properties={"area": "editor"})
    facade.create_entity("ent-A", e)
    got = facade.get_entity("ent-A", e.id)
    assert got is not None
    assert got.type == "theme"
    assert got.canonical_label == "AI authoring"
    assert "in-editor AI writing" in got.aliases
    assert got.properties == {"area": "editor"}


def test_tenant_violation_on_mismatched_enterprise(facade):
    from app.graph import Entity, TenantViolationError
    e = Entity(enterprise_id="ent-A", type="theme", canonical_label="X")
    with pytest.raises(TenantViolationError):
        facade.create_entity("ent-B", e)   # caller says B, entity says A


def test_get_entity_is_tenant_scoped(facade):
    from app.graph import Entity
    a = Entity(enterprise_id="ent-A", type="theme", canonical_label="A-theme")
    b = Entity(enterprise_id="ent-B", type="theme", canonical_label="B-theme")
    facade.create_entity("ent-A", a)
    facade.create_entity("ent-B", b)
    assert facade.get_entity("ent-A", a.id) is not None
    assert facade.get_entity("ent-B", a.id) is None    # A's entity invisible from B
    assert facade.get_entity("ent-A", b.id) is None    # B's entity invisible from A


def test_write_signal_and_active_signals_filters_stale(facade):
    from app.graph import Signal
    now = datetime.now(timezone.utc)
    fresh = Signal(enterprise_id="ent-A", source_type="communication",
                   kind="feature_request", content="add foo", valid_at=now)
    stale = Signal(enterprise_id="ent-A", source_type="communication",
                   kind="feature_request", content="old bar",
                   valid_at=now - timedelta(days=30))   # > 7d window → stale
    facade.write_signal("ent-A", fresh)
    facade.write_signal("ent-A", stale)
    active = facade.active_signals("ent-A")
    ids = {s.id for s in active}
    assert fresh.id in ids
    assert stale.id not in ids


def test_active_signals_filter_by_source_type(facade):
    from app.graph import Signal
    facade.write_signal("ent-A", Signal(enterprise_id="ent-A", source_type="revenue",
                                         kind="deal_blocker", content="$1M at risk"))
    facade.write_signal("ent-A", Signal(enterprise_id="ent-A", source_type="communication",
                                         kind="feature_request", content="add bar"))
    only_rev = facade.active_signals("ent-A", source_types=["revenue"])
    assert len(only_rev) == 1
    assert only_rev[0].source_type == "revenue"


def test_write_relationship_and_edges_from_to(facade):
    from app.graph import Entity, Relationship
    theme = Entity(enterprise_id="ent-A", type="theme", canonical_label="checkout")
    account = Entity(enterprise_id="ent-A", type="account", canonical_label="acme")
    facade.create_entity("ent-A", theme)
    facade.create_entity("ent-A", account)
    rel = Relationship(enterprise_id="ent-A", type="REQUESTS",
                       source_kind="entity", source_id=account.id,
                       target_kind="entity", target_id=theme.id)
    facade.write_relationship("ent-A", rel)

    out = facade.edges_from("ent-A", account.id)
    inc = facade.edges_to("ent-A", theme.id)
    assert len(out) == 1 and out[0].type == "REQUESTS"
    assert len(inc) == 1 and inc[0].source_id == account.id


def test_edges_filter_by_type(facade):
    from app.graph import Entity, Relationship
    a = Entity(enterprise_id="ent-A", type="account", canonical_label="acme")
    t = Entity(enterprise_id="ent-A", type="theme", canonical_label="x")
    facade.create_entity("ent-A", a)
    facade.create_entity("ent-A", t)
    facade.write_relationship("ent-A", Relationship(
        enterprise_id="ent-A", type="REQUESTS", source_kind="entity",
        source_id=a.id, target_kind="entity", target_id=t.id))
    facade.write_relationship("ent-A", Relationship(
        enterprise_id="ent-A", type="BLOCKED_BY", source_kind="entity",
        source_id=a.id, target_kind="entity", target_id=t.id))
    assert len(facade.edges_from("ent-A", a.id, type="REQUESTS")) == 1
    assert len(facade.edges_from("ent-A", a.id, type="BLOCKED_BY")) == 1
    assert len(facade.edges_from("ent-A", a.id)) == 2


def test_load_session_context_returns_top_N_by_type(facade):
    from app.graph import Entity
    for i in range(12):
        facade.create_entity("ent-A", Entity(
            enterprise_id="ent-A", type="hypothesis", canonical_label=f"hyp-{i}"))
    for i in range(7):
        facade.create_entity("ent-A", Entity(
            enterprise_id="ent-A", type="decision", canonical_label=f"dec-{i}"))
    for i in range(4):
        facade.create_entity("ent-A", Entity(
            enterprise_id="ent-A", type="outcome", canonical_label=f"out-{i}"))
    ctx = facade.load_session_context("ent-A")
    assert ctx["enterprise_id"] == "ent-A"
    assert len(ctx["active_hypotheses"]) == 10
    assert len(ctx["recent_decisions"]) == 5
    assert len(ctx["recent_outcomes"]) == 3


def test_supersede_signal_records_in_properties(facade):
    from app.graph import Signal
    old = Signal(enterprise_id="ent-A", source_type="revenue",
                 kind="deal_blocker", content="acme at risk")
    new = Signal(enterprise_id="ent-A", source_type="revenue",
                 kind="deal_reopened", content="acme reopened")
    facade.write_signal("ent-A", old)
    facade.write_signal("ent-A", new)
    facade.supersede_signal("ent-A", old.id, new.id)
    got = facade.get_signal("ent-A", old.id)
    assert got is not None
    assert got.properties.get("superseded_by") == new.id
    assert "superseded_at" in got.properties


def test_supersede_rejects_cross_tenant(facade):
    from app.graph import Signal
    a = Signal(enterprise_id="ent-A", source_type="revenue", kind="x", content="a")
    facade.write_signal("ent-A", a)
    with pytest.raises(ValueError, match="not found"):
        facade.supersede_signal("ent-B", a.id, "anything")


# ---------- batched get_signals (N+1 kill) ----------

def test_get_signals_batched_returns_dict(facade):
    from app.graph import Signal
    a = Signal(enterprise_id="ent-A", source_type="revenue", kind="x", content="aa")
    b = Signal(enterprise_id="ent-A", source_type="communication", kind="y", content="bb")
    facade.write_signal("ent-A", a)
    facade.write_signal("ent-A", b)
    got = facade.get_signals("ent-A", [a.id, b.id])
    assert set(got) == {a.id, b.id}
    assert got[a.id].content == "aa"
    assert got[b.id].content == "bb"


def test_get_signals_empty_list_returns_empty_dict(facade):
    assert facade.get_signals("ent-A", []) == {}


def test_get_signals_dedups_and_skips_missing_and_is_tenant_scoped(facade):
    from app.graph import Signal
    a = Signal(enterprise_id="ent-A", source_type="revenue", kind="x", content="aa")
    other = Signal(enterprise_id="ent-B", source_type="revenue", kind="x", content="cc")
    facade.write_signal("ent-A", a)
    facade.write_signal("ent-B", other)
    # duplicate id + a non-existent id + another tenant's id
    got = facade.get_signals("ent-A", [a.id, a.id, "does-not-exist", other.id])
    assert set(got) == {a.id}        # missing + cross-tenant absent, dup collapsed
    assert got[a.id].content == "aa"


def test_get_signals_single_query(facade, monkeypatch):
    """The batch must issue ONE underlying read, not one per id."""
    from app.graph import Signal
    sigs = []
    for i in range(5):
        s = Signal(enterprise_id="ent-A", source_type="revenue", kind="x",
                   content=f"s{i}")
        facade.write_signal("ent-A", s)
        sigs.append(s)

    calls = {"n": 0}
    orig_table = facade._client.table

    def _counting_table(name):
        if name == "kg_signal":
            calls["n"] += 1
        return orig_table(name)

    monkeypatch.setattr(facade._client, "table", _counting_table)
    got = facade.get_signals("ent-A", [s.id for s in sigs])
    assert len(got) == 5
    assert calls["n"] == 1   # exactly one kg_signal table access for the batch


# ---------- decision log ----------

def test_log_agent_decision_round_trip(isolated_settings):
    from app.graph import log_agent_decision
    log_agent_decision(
        enterprise_id="ent-A",
        agent="synthesis",
        decision_type="rank",
        factors={"scoring_profile": "v0", "candidates": 5},
        reasoning="Top theme serves Q3 churn goal; $1.4M deals blocked",
        output={"top_theme_id": "t-001"},
        model="claude-sonnet-4-6",
        prompt_version="synth-rank-v1",
        confidence=0.82,
        kg_refs=["t-001", "deal-acme", "deal-globex"],
    )
    rows = isolated_settings["supabase"].table("agent_decision_log") \
        .select("*").eq("enterprise_id", "ent-A").execute().data
    assert len(rows) == 1
    r = rows[0]
    assert r["agent"] == "synthesis"
    assert r["decision_type"] == "rank"
    assert r["factors"] == {"scoring_profile": "v0", "candidates": 5}
    assert r["output"] == {"top_theme_id": "t-001"}
    assert r["model"] == "claude-sonnet-4-6"
    assert r["confidence"] == 0.82
    assert r["kg_refs"] == ["t-001", "deal-acme", "deal-globex"]
    assert r["reasoning"].startswith("Top theme")


def test_log_agent_decision_swallows_write_failure(isolated_settings):
    """A failed audit write must NEVER raise into the caller's primary flow —
    fire-and-forget swallows + logs it."""
    from app.graph import log_agent_decision

    class _BoomClient:
        def table(self, name):  # noqa: ARG002
            raise RuntimeError("supabase is down")

    # Should not raise even though the underlying insert blows up.
    out = log_agent_decision(
        enterprise_id="ent-A",
        agent="synthesis",
        decision_type="rank",
        client=_BoomClient(),
    )
    assert out is None  # failed write yields no id, but no exception escapes


def test_flush_decision_log_is_safe_to_call(isolated_settings):
    """flush_decision_log drains pending writes and never raises (no-op inline
    under pytest)."""
    from app.graph import flush_decision_log, log_agent_decision

    log_agent_decision(
        enterprise_id="ent-A", agent="synthesis", decision_type="rank",
    )
    flush_decision_log()  # must not raise
    rows = isolated_settings["supabase"].table("agent_decision_log") \
        .select("*").eq("enterprise_id", "ent-A").execute().data
    assert len(rows) == 1  # inline-under-pytest: row is present immediately


def test_create_source_is_idempotent_on_duplicate_id(facade):
    """create_source upserts by id, so re-seeding an unchanged doc (deterministic
    id) is a no-op update, not a duplicate-key crash.

    Regression: a plain INSERT threw 23505 on every re-seed of an unchanged
    corpus doc, aborting seeding and leaving the brief empty. See
    facade.create_source / synthesis_brief._seed_from_corpus.
    """
    from app.graph import Source

    src = Source(
        enterprise_id="ent-A", source_type="corpus_doc", label="doc-1",
        config={"content_sha": "abc"}, id="fixed-source-id",
    )
    facade.create_source("ent-A", src)
    # Second call with the SAME id must not raise (previously duplicate-key).
    updated = Source(
        enterprise_id="ent-A", source_type="corpus_doc", label="doc-1-relabeled",
        config={"content_sha": "abc"}, id="fixed-source-id",
    )
    facade.create_source("ent-A", updated)  # no exception

    rows = facade.list_sources("ent-A", source_type="corpus_doc")
    assert len(rows) == 1                      # one row, not two
    assert rows[0].id == "fixed-source-id"
    assert rows[0].label == "doc-1-relabeled"  # upsert applied the new values

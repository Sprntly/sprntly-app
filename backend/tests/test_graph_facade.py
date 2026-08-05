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


# ---------- typed field promotion (skill_id/origin/channel/evidence_eligible) ----------

def test_signal_typed_fields_default_none_and_evidence_eligible_computed(isolated_settings):
    from app.graph.types import Signal
    s = Signal(enterprise_id="e", source_type="revenue", kind="x", content="c")
    assert s.skill_id is None
    assert s.origin is None
    assert s.channel is None
    # revenue is a CONNECTED_SOURCE_TYPES member and origin is None (not a
    # NON_EVIDENCE_ORIGIN) → eligible by default.
    assert s.evidence_eligible is True


def test_signal_typed_fields_fall_back_to_provenance_dict(isolated_settings):
    """A caller that only sets the informal provenance dict (every
    pre-existing construction site) still gets the typed fields populated —
    the transition-safety fallback in __post_init__."""
    from app.graph.types import Signal
    s = Signal(enterprise_id="e", source_type="revenue", kind="x", content="c",
              provenance={"skill_id": "jira-extraction", "origin": "connector",
                          "channel": "upload"})
    assert s.skill_id == "jira-extraction"
    assert s.origin == "connector"
    assert s.channel == "upload"


def test_signal_typed_kwarg_wins_over_provenance_dict(isolated_settings):
    from app.graph.types import Signal
    s = Signal(enterprise_id="e", source_type="revenue", kind="x", content="c",
              provenance={"origin": "connector"}, origin="upload")
    assert s.origin == "upload"


def test_signal_evidence_eligible_explicit_kwarg_wins(isolated_settings):
    from app.graph.types import Signal
    s = Signal(enterprise_id="e", source_type="revenue", kind="x", content="c",
              evidence_eligible=False)
    assert s.evidence_eligible is False


def test_row_to_signal_falls_back_to_provenance_dict_for_pre_migration_rows(isolated_settings):
    """A pre-migration row (typed columns null in the DB, values only in the
    provenance dict) reconstructs with the typed fields populated from the
    dict — GraphFacade._row_to_signal's read-side fallback."""
    import uuid
    from app.graph import GraphFacade
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    sig_id = str(uuid.uuid4())
    isolated_settings["supabase"].table("kg_signal").insert({
        "id": sig_id, "enterprise_id": "ent-A", "source_type": "revenue",
        "kind": "finding", "content": "pre-migration row", "properties": {},
        "valid_at": now, "transaction_at": now, "provenance": {
            "skill_id": "hubspot-extraction", "origin": "connector",
            "channel": "upload",
        },
        # typed columns left unset → null, exactly like a real pre-migration row
    }).execute()

    facade = GraphFacade()
    sig = facade.get_signal("ent-A", sig_id)
    assert sig is not None
    assert sig.skill_id == "hubspot-extraction"
    assert sig.origin == "connector"
    assert sig.channel == "upload"
    # evidence_eligible column also null → computed on the fly from
    # source_type + origin (same policy as a fresh Signal would apply).
    assert sig.evidence_eligible is True


def test_row_to_signal_prefers_typed_column_over_provenance_dict(isolated_settings):
    """When both are present (post-migration row), the typed DB column wins —
    it's the more authoritative source once it exists."""
    import uuid
    from app.graph import GraphFacade
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    sig_id = str(uuid.uuid4())
    isolated_settings["supabase"].table("kg_signal").insert({
        "id": sig_id, "enterprise_id": "ent-A", "source_type": "revenue",
        "kind": "finding", "content": "post-migration row", "properties": {},
        "valid_at": now, "transaction_at": now,
        "provenance": {"origin": "upload"},  # stale/mismatched dict value
        "origin": "connector",               # the typed column is authoritative
    }).execute()

    facade = GraphFacade()
    sig = facade.get_signal("ent-A", sig_id)
    assert sig.origin == "connector"


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


# ---------- active_signals: ordering, limit, column list (page-cap defect) ----------

def _seed_raw_signals(db, enterprise_id: str, n: int, id_prefix: str = "s",
                       transaction_at: str = "2026-06-01T00:00:00+00:00", **extra):
    """Insert `n` kg_signal rows directly (bypassing Signal/write_signal) — fast
    enough for 1000+ row page-cap seeds. Every row shares `transaction_at`
    unless overridden per-row via `extra`."""
    rows = [{
        "id": f"{id_prefix}-{i}", "enterprise_id": enterprise_id,
        "source_type": "revenue", "kind": "x", "content": "y",
        "properties": {}, "provenance": {},
        "valid_at": transaction_at, "transaction_at": transaction_at,
        **extra,
    } for i in range(n)]
    db.table("kg_signal").insert(rows).execute()
    return rows


def test_active_signals_survives_server_page_caps(isolated_settings, monkeypatch):
    """Regression: PostgREST caps an unlimited select at ~1000 rows. The old
    unordered, unlimited scan could return a page holding only OLD signals
    once a tenant passed that cap — chat's freshly-synced connector data
    became permanently invisible with no error. Fetching newest-
    transaction-first with an explicit LIMIT makes the result correct no
    matter how many rows the tenant has. FAILS on unfixed code."""
    from tests import _fake_supabase as fake
    from app.graph import GraphFacade

    db = isolated_settings["supabase"]
    # 1200 older signals inserted FIRST (so an unordered capped page is
    # all-old), then one signal newer than all of them.
    _seed_raw_signals(db, "ent-A", 1200, id_prefix="s-old")
    db.table("kg_signal").insert({
        "id": "s-new", "enterprise_id": "ent-A", "source_type": "revenue",
        "kind": "x", "content": "y", "properties": {}, "provenance": {},
        "valid_at": "2026-07-01T00:00:00+00:00",
        "transaction_at": "2026-07-01T00:00:00+00:00",
    }).execute()

    # Mimic the server-side page cap: an execute() with no explicit limit
    # returns at most 1000 rows (exactly PostgREST's behavior).
    orig_execute = fake._Query.execute

    def capped_execute(self):
        res = orig_execute(self)
        if getattr(self, "_limit", None) is None and isinstance(res.data, list):
            res.data = res.data[:1000]
        return res

    monkeypatch.setattr(fake._Query, "execute", capped_execute)

    ids = {s.id for s in GraphFacade().active_signals("ent-A")}
    assert "s-new" in ids


def test_newest_signal_reaches_the_chat_recency_window_past_the_page_cap(
    isolated_settings, monkeypatch,
):
    """Same page-cap scenario, stated in the chat consumer's own terms
    (retrieval.py sorts active_signals()'s result by transaction_at desc and
    keeps the top 8 — _RECENT_SIGNALS). This is the user-visible symptom, a
    distinct assertion from the facade-level one above. FAILS on unfixed
    code."""
    from tests import _fake_supabase as fake
    from app.graph import GraphFacade

    db = isolated_settings["supabase"]
    _seed_raw_signals(db, "ent-A", 1200, id_prefix="s-old")
    db.table("kg_signal").insert({
        "id": "s-new", "enterprise_id": "ent-A", "source_type": "revenue",
        "kind": "x", "content": "y", "properties": {}, "provenance": {},
        "valid_at": "2026-07-01T00:00:00+00:00",
        "transaction_at": "2026-07-01T00:00:00+00:00",
    }).execute()

    orig_execute = fake._Query.execute

    def capped_execute(self):
        res = orig_execute(self)
        if getattr(self, "_limit", None) is None and isinstance(res.data, list):
            res.data = res.data[:1000]
        return res

    monkeypatch.setattr(fake._Query, "execute", capped_execute)

    recent = GraphFacade().active_signals("ent-A")
    recent.sort(key=lambda s: s.transaction_at, reverse=True)
    top8_ids = {s.id for s in recent[:8]}
    assert "s-new" in top8_ids


def test_active_signals_query_is_ordered_and_limited(facade, monkeypatch):
    """Boundary assertion — captures the args passed to `.order()`/`.limit()`
    rather than inferring order from returned rows (the fake ignores neither
    of these, unlike `.select()`, so this is a faithful check)."""
    from tests import _fake_supabase as fake

    calls: dict[str, Any] = {"order": None, "limit": None}
    orig_order = fake._Query.order
    orig_limit = fake._Query.limit

    def spy_order(self, col, desc=False):
        calls["order"] = (col, desc)
        return orig_order(self, col, desc)

    def spy_limit(self, n):
        calls["limit"] = n
        return orig_limit(self, n)

    monkeypatch.setattr(fake._Query, "order", spy_order)
    monkeypatch.setattr(fake._Query, "limit", spy_limit)

    facade.active_signals("ent-A")

    assert calls["order"] == ("transaction_at", True)
    assert calls["limit"] == 1000


def test_active_signals_select_list_excludes_the_embedding_column(facade, monkeypatch):
    """THE TRAP: the fake ignores the column list entirely and always runs
    `SELECT *` under the hood, so a row-content assertion proves nothing — a
    typo/omission here would pass every fake-backed test and break
    production. Assert at the query boundary instead: the exact string
    passed to `.select()`."""
    from tests import _fake_supabase as fake

    captured: list[str] = []
    orig_select = fake._Query.select

    def spy_select(self, cols="*", count=None):
        captured.append(cols)
        return orig_select(self, cols, count)

    monkeypatch.setattr(fake._Query, "select", spy_select)

    facade.active_signals("ent-A")

    assert captured, "select() was never called"
    cols_str = captured[-1]
    assert "embedding" not in cols_str
    col_list = {c.strip() for c in cols_str.split(",")}
    expected = {
        "id", "enterprise_id", "source_id", "source_type", "kind", "content",
        "properties", "valid_at", "transaction_at", "stale_after", "confidence",
        "weight", "provenance", "created_at", "skill_id", "origin", "channel",
        "evidence_eligible",
    }
    assert expected <= col_list


def test_active_signals_returns_the_same_set_below_the_limit(facade):
    """No caller loses rows under the limit: a 20-signal tenant returns the
    identical id set the pre-fix code would have (the DB limit is 1000, and
    the Python-side stale/source_type/since filtering is unchanged)."""
    from app.graph import Signal
    ids = set()
    for i in range(20):
        sig = Signal(enterprise_id="ent-A", source_type="revenue",
                     kind="x", content=f"c-{i}")
        facade.write_signal("ent-A", sig)
        ids.add(sig.id)
    got = {s.id for s in facade.active_signals("ent-A")}
    assert got == ids


def test_active_signals_source_type_filter_unchanged(facade):
    from app.graph import Signal
    facade.write_signal("ent-A", Signal(enterprise_id="ent-A", source_type="revenue",
                                         kind="x", content="rev"))
    facade.write_signal("ent-A", Signal(enterprise_id="ent-A", source_type="communication",
                                         kind="x", content="comm"))
    got = facade.active_signals("ent-A", source_types=["revenue"])
    assert {s.source_type for s in got} == {"revenue"}


def test_active_signals_since_filter_unchanged(facade):
    from app.graph import Signal
    now = datetime.now(timezone.utc)
    old = Signal(enterprise_id="ent-A", source_type="revenue", kind="x", content="old",
                 transaction_at=now - timedelta(days=10))
    new = Signal(enterprise_id="ent-A", source_type="revenue", kind="x", content="new",
                 transaction_at=now)
    facade.write_signal("ent-A", old)
    facade.write_signal("ent-A", new)
    got = facade.active_signals("ent-A", since=now - timedelta(days=1))
    ids = {s.id for s in got}
    assert new.id in ids
    assert old.id not in ids


def test_signals_from_active_signals_carry_no_embedding(facade):
    from app.graph import Signal
    sig = Signal(enterprise_id="ent-A", source_type="revenue", kind="x", content="c",
                 properties={"k": "v"},
                 provenance={"skill_id": "jira-extraction", "origin": "connector",
                             "channel": "upload"},
                 confidence=0.7, weight=0.9)
    facade.write_signal("ent-A", sig)
    got = facade.active_signals("ent-A")
    assert len(got) == 1
    out = got[0]
    assert out.embedding is None
    assert out.properties == {"k": "v"}
    assert out.provenance == {"skill_id": "jira-extraction", "origin": "connector",
                               "channel": "upload"}
    assert out.confidence == 0.7
    assert out.weight == 0.9
    assert out.skill_id == "jira-extraction"
    assert out.origin == "connector"
    assert out.channel == "upload"
    assert out.evidence_eligible is True


def test_active_signals_empty_tenant_returns_empty_list(facade):
    assert facade.active_signals("ent-nobody-here") == []


def test_active_signals_all_rows_stale_returns_empty_list(facade):
    """The DB limit returns rows; the Python stale filter removes all of them
    (not an exception)."""
    from app.graph import Signal
    stale = Signal(enterprise_id="ent-A", source_type="communication", kind="x",
                   content="old",
                   valid_at=datetime.now(timezone.utc) - timedelta(days=30))
    facade.write_signal("ent-A", stale)
    assert facade.active_signals("ent-A") == []


def test_active_signals_explicit_limit_argument_is_honoured(facade):
    """Proves the keyword-only `limit` parameter is actually wired to the
    query, not just accepted and ignored."""
    from app.graph import Signal
    for i in range(10):
        facade.write_signal("ent-A", Signal(enterprise_id="ent-A", source_type="revenue",
                                             kind="x", content=f"c-{i}"))
    got = facade.active_signals("ent-A", limit=5)
    assert len(got) == 5


def test_active_signals_tenant_isolation_holds_under_ordering_and_limit(isolated_settings):
    """Seed two tenants at 1200 rows each; neither sees the other's rows,
    ordering and limit notwithstanding."""
    from app.graph import GraphFacade

    facade = GraphFacade()
    db = isolated_settings["supabase"]
    _seed_raw_signals(db, "ent-A", 1200, id_prefix="ent-A-sig")
    _seed_raw_signals(db, "ent-B", 1200, id_prefix="ent-B-sig")

    a_ids = {s.id for s in facade.active_signals("ent-A")}
    b_ids = {s.id for s in facade.active_signals("ent-B")}
    assert a_ids, "expected ent-A to have signals"
    assert b_ids, "expected ent-B to have signals"
    assert a_ids.isdisjoint(b_ids)
    assert all(i.startswith("ent-A-sig-") for i in a_ids)
    assert all(i.startswith("ent-B-sig-") for i in b_ids)


def test_limit_reached_log_carries_identifiers_only(isolated_settings, caplog):
    """If a limit-reached log fires, it carries `enterprise_id` and counts
    only — never signal content or provenance."""
    import logging as _logging

    from app.graph import GraphFacade

    db = isolated_settings["supabase"]
    sentinel_content = "SENTINEL-DO-NOT-LOG-CONTENT"
    sentinel_marker = "SENTINEL-DO-NOT-LOG-PROVENANCE"
    rows = [{
        "id": f"s-{i}", "enterprise_id": "ent-A", "source_type": "revenue",
        "kind": "x",
        "content": sentinel_content if i == 0 else "y",
        "properties": {},
        "provenance": {"secret": sentinel_marker} if i == 0 else {},
        "valid_at": "2026-06-01T00:00:00+00:00",
        "transaction_at": "2026-06-01T00:00:00+00:00",
    } for i in range(5)]
    db.table("kg_signal").insert(rows).execute()

    with caplog.at_level(_logging.INFO, logger="app.graph.facade"):
        GraphFacade().active_signals("ent-A", limit=5)

    limit_records = [r for r in caplog.records if "limit" in r.getMessage().lower()]
    assert limit_records, "expected a limit-reached log record when len(rows) == limit"
    for r in limit_records:
        msg = r.getMessage()
        assert sentinel_content not in msg
        assert sentinel_marker not in msg
        assert "ent-A" in msg


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


# ---------- ensure_company_entity (tenant root anchor) ----------

def test_ensure_company_entity_creates_once(facade):
    from app.graph.types import COMPANY_ENTITY_TYPE

    company_id = facade.ensure_company_entity("ent-A", label="Acme Inc")
    ent = facade.get_entity("ent-A", company_id)
    assert ent is not None
    assert ent.type == COMPANY_ENTITY_TYPE
    assert ent.canonical_label == "Acme Inc"

    all_company = facade.query_entities("ent-A", type=COMPANY_ENTITY_TYPE)
    assert len(all_company) == 1


def test_ensure_company_entity_is_idempotent_and_does_not_rename(facade):
    first_id = facade.ensure_company_entity("ent-A", label="Acme Inc")
    # A second call (e.g. a different label the second time) finds the
    # existing node rather than creating a duplicate or renaming it.
    second_id = facade.ensure_company_entity("ent-A", label="A Different Name")
    assert second_id == first_id

    ent = facade.get_entity("ent-A", first_id)
    assert ent.canonical_label == "Acme Inc"  # unchanged
    assert len(facade.query_entities("ent-A", type="company")) == 1


def _raw_kg_entity(facade, enterprise_id: str, entity_id: str) -> dict:
    """Entity has no `updated_at` field — read the raw row for it."""
    r = (
        facade._tbl("kg_entity")
        .select("canonical_label, created_at, updated_at")
        .eq("enterprise_id", enterprise_id)
        .eq("id", entity_id)
        .execute()
    )
    return r.data[0]


def test_ensure_company_entity_relabel_true_renames_existing(facade):
    """`relabel=True` (opt-in) DOES rename an already-existing company entity
    when a truthy, different label is supplied — the fix for a root created
    via a non-business-context path first (e.g. roadmap upload) that would
    otherwise stay stuck with its fallback label forever."""
    first_id = facade.ensure_company_entity("ent-A", label="ent-A")  # UUID-fallback-style
    before = _raw_kg_entity(facade, "ent-A", first_id)

    second_id = facade.ensure_company_entity("ent-A", label="Acme Inc", relabel=True)
    assert second_id == first_id  # still the same node, never duplicated

    after = _raw_kg_entity(facade, "ent-A", first_id)
    assert after["canonical_label"] == "Acme Inc"
    assert len(facade.query_entities("ent-A", type="company")) == 1
    # updated_at actually bumped (never equal to what it was pre-relabel).
    assert after["updated_at"] != before["updated_at"]


def test_ensure_company_entity_relabel_true_is_a_noop_without_a_real_label(facade):
    """`relabel=True` with no label (or an unknown/empty one) must never
    overwrite the existing label with a fallback — it only fires when a real
    name is actually available."""
    first_id = facade.ensure_company_entity("ent-A", label="Acme Inc")
    before = _raw_kg_entity(facade, "ent-A", first_id)

    same_id = facade.ensure_company_entity("ent-A", label=None, relabel=True)
    assert same_id == first_id
    after = _raw_kg_entity(facade, "ent-A", first_id)
    assert after["canonical_label"] == "Acme Inc"
    assert after["updated_at"] == before["updated_at"]  # no write happened


def test_ensure_company_entity_relabel_true_is_idempotent_on_unchanged_label(facade):
    """Re-running with the SAME label + relabel=True must not write (no
    spurious updated_at bump on every idempotent refresh re-run)."""
    first_id = facade.ensure_company_entity("ent-A", label="Acme Inc")
    before = _raw_kg_entity(facade, "ent-A", first_id)

    facade.ensure_company_entity("ent-A", label="Acme Inc", relabel=True)
    after = _raw_kg_entity(facade, "ent-A", first_id)
    assert after["canonical_label"] == "Acme Inc"
    assert after["updated_at"] == before["updated_at"]


def test_ensure_company_entity_falls_back_to_display_name(facade, isolated_settings):
    """No label passed, but a `companies` row with a display_name exists for
    this enterprise → the created entity's canonical_label is the
    display_name, not the raw enterprise_id."""
    isolated_settings["supabase"].table("companies").insert(
        {"id": "ent-A", "slug": "acme", "display_name": "Acme Inc"}
    ).execute()

    company_id = facade.ensure_company_entity("ent-A")
    ent = facade.get_entity("ent-A", company_id)
    assert ent.canonical_label == "Acme Inc"


def test_ensure_company_entity_falls_back_to_enterprise_id_label_as_last_resort(facade):
    """No label passed AND no `companies` row for this enterprise (e.g. a
    legacy/demo dataset with no tenant row) → falls all the way back to the
    raw enterprise_id, same as before this fallback was improved.

    (`companies.display_name` is a NOT NULL column per the schema — a
    `companies` row existing with a null/empty display_name isn't a state
    the real DB can produce, so "no row at all" is the only realistic
    last-resort case worth covering here.)"""
    company_id = facade.ensure_company_entity("ent-A")
    ent = facade.get_entity("ent-A", company_id)
    assert ent.canonical_label == "ent-A"


def test_ensure_company_entity_is_tenant_scoped(facade):
    a_id = facade.ensure_company_entity("ent-A", label="Acme")
    b_id = facade.ensure_company_entity("ent-B", label="Beta")
    assert a_id != b_id
    assert facade.get_entity("ent-B", a_id) is None
    assert facade.get_entity("ent-A", b_id) is None

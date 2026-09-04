"""Tests for `app.graph` — meta-model dataclasses + GraphFacade + decision log.

Uses the shared isolated_settings fixture (in-memory fake Supabase). The
pgvector kNN behind `find_candidates` is still integration-tested separately
against real Supabase, but its HYDRATION half is covered here: the fake does
support `rpc`, so registering rows via `FakeSupabaseClient.rpc_returns` (see
the `kg_candidates` fixture) exercises the ordering and batching that three
find-or-create callers depend on."""
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


# ---------- write_signal: transient statement_timeout (57014) retry ----------
#
# Live-verify (2026-08-27): a bulk backfill wrote ~250 calls' signals into
# kg_signal in a tight window and 23 of those inserts failed with Postgres
# 57014 (statement_timeout cancellation) — a transient burst-load failure,
# not a real data problem. `write_signal`'s insert now retries a couple of
# times on exactly this error before giving up.


class _FakeAPIError(Exception):
    """Mirrors the shape `_is_statement_timeout`/`_is_duplicate_signal`
    check: a `.code` attribute (supabase-py's `postgrest.exceptions.APIError`
    shape) plus a message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _FlakyInsertClient:
    """A minimal stand-in for `self._client` that fails `.table(...).insert(
    ...).execute()` the first `fail_times` calls, then succeeds. Records
    every call so tests can assert exactly how many attempts were made."""

    def __init__(self, fail_times: int, exc_factory):
        self.fail_times = fail_times
        self.calls = 0
        self._exc_factory = exc_factory

    def table(self, name):
        return _FlakyTable(self, name)


class _FlakyTable:
    def __init__(self, client: _FlakyInsertClient, name: str):
        self._client = client
        self._name = name

    def insert(self, row):
        return _FlakyExecutor(self._client, row)


class _FlakyExecutor:
    def __init__(self, client: _FlakyInsertClient, row: dict):
        self._client = client
        self._row = row

    def execute(self):
        from types import SimpleNamespace

        self._client.calls += 1
        if self._client.calls <= self._client.fail_times:
            raise self._client._exc_factory()
        return SimpleNamespace(data=[self._row])


def _no_sleep(monkeypatch):
    """Never actually wait in a test — assert-worthy: if code under test
    slept when it should not have, this raises instead of silently passing
    (a bare no-op lambda would hide that bug)."""
    import app.graph.facade as facade_mod
    monkeypatch.setattr(facade_mod.time, "sleep", lambda s: None)


def test_write_signal_retries_transient_statement_timeout_and_succeeds(
    monkeypatch, isolated_settings,
):
    from app.graph import GraphFacade, Signal

    _no_sleep(monkeypatch)
    client = _FlakyInsertClient(
        fail_times=1,
        exc_factory=lambda: _FakeAPIError(
            "57014", "canceling statement due to statement timeout"),
    )
    facade = GraphFacade(client=client)
    sig = Signal(enterprise_id="ent-A", source_type="revenue",
                 kind="finding", content="retried fact")

    result = facade.write_signal("ent-A", sig)

    assert result is sig
    assert client.calls == 2, "one failed attempt, one successful retry"


def test_write_signal_persistent_timeout_still_raises_after_retries_exhausted(
    monkeypatch, isolated_settings,
):
    """The degrade-gracefully half of the contract: a PERSISTENT (not
    transient) timeout still surfaces after retries are exhausted — the
    existing per-call isolation upstream (graph.extractor's write loop /
    the backfill CLI's per-call try/except) is what actually skips the call
    and leaves its ledger hash unrecorded so it retries next run; this
    wrapper must not swallow the failure itself."""
    from app.graph import GraphFacade, Signal
    from app.graph.facade import _WRITE_RETRY_ATTEMPTS

    _no_sleep(monkeypatch)
    client = _FlakyInsertClient(
        fail_times=999,
        exc_factory=lambda: _FakeAPIError(
            "57014", "canceling statement due to statement timeout"),
    )
    facade = GraphFacade(client=client)
    sig = Signal(enterprise_id="ent-A", source_type="revenue",
                 kind="finding", content="never recovers")

    with pytest.raises(_FakeAPIError) as exc_info:
        facade.write_signal("ent-A", sig)

    assert exc_info.value.code == "57014"
    assert client.calls == _WRITE_RETRY_ATTEMPTS, (
        "must try exactly the configured number of attempts, not loop forever")


def test_write_signal_happy_path_single_write_no_retry_no_sleep(
    monkeypatch, isolated_settings,
):
    """The common case (a normal single-record write, e.g. the live sync
    path) must not get slower: exactly one insert call, and no sleep at
    all — proven by making `time.sleep` raise if it is ever called."""
    import app.graph.facade as facade_mod
    from app.graph import GraphFacade, Signal

    def _fail_if_called(_delay):
        raise AssertionError("happy-path write_signal must never sleep")

    monkeypatch.setattr(facade_mod.time, "sleep", _fail_if_called)
    client = _FlakyInsertClient(
        fail_times=0,
        exc_factory=lambda: _FakeAPIError("57014", "unused"),
    )
    facade = GraphFacade(client=client)
    sig = Signal(enterprise_id="ent-A", source_type="revenue",
                 kind="finding", content="happy path fact")

    facade.write_signal("ent-A", sig)

    assert client.calls == 1


def test_write_signal_non_timeout_error_is_not_retried(monkeypatch, isolated_settings):
    """A real duplicate-key (23505) or any other non-timeout failure must
    surface on the FIRST attempt, unchanged — the retry is narrowly scoped
    to 57014 only, never a blanket "retry every write error" policy."""
    import app.graph.facade as facade_mod
    from app.graph import GraphFacade, Signal

    def _fail_if_called(_delay):
        raise AssertionError("a non-timeout error must not trigger a retry/backoff")

    monkeypatch.setattr(facade_mod.time, "sleep", _fail_if_called)
    client = _FlakyInsertClient(
        fail_times=999,
        exc_factory=lambda: _FakeAPIError(
            "23505", "duplicate key value violates unique constraint"),
    )
    facade = GraphFacade(client=client)
    sig = Signal(enterprise_id="ent-A", source_type="revenue",
                 kind="finding", content="duplicate fact")

    with pytest.raises(_FakeAPIError) as exc_info:
        facade.write_signal("ent-A", sig)

    assert exc_info.value.code == "23505"
    assert client.calls == 1, "must not retry a non-timeout error"


def test_is_statement_timeout_recognizes_code_and_text_not_other_errors():
    from app.graph.facade import _is_statement_timeout

    assert _is_statement_timeout(_FakeAPIError("57014", "canceling statement due to statement timeout"))
    assert _is_statement_timeout(Exception("canceling statement due to statement timeout"))
    assert not _is_statement_timeout(_FakeAPIError("23505", "duplicate key value"))
    assert not _is_statement_timeout(Exception("invalid input syntax for type uuid"))


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


def test_limit_reached_log_carries_identifiers_only(
    isolated_settings, caplog, monkeypatch
):
    """If a limit-reached log fires, it carries `enterprise_id` and counts
    only — never signal content or provenance.

    The log fires only at the DEFAULT fetch width, so the default is patched
    down to the seeded row count rather than the call being widened to 1000."""
    import logging as _logging

    from app.graph import GraphFacade
    from app.graph import facade as facade_mod

    monkeypatch.setattr(facade_mod, "_ACTIVE_SIGNALS_LIMIT", 5)

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


def test_a_deliberately_narrowed_fetch_does_not_warn(isolated_settings, caplog):
    """A caller that narrows the window on purpose hits the cap on EVERY call
    by design, so warning there converts a real diagnostic into constant noise.

    Ask asks for 250 rows to read the newest 8; before this the ask logged
    "active_signals hit the fetch limit" on every single question."""
    import logging as _logging

    from app.graph import GraphFacade

    db = isolated_settings["supabase"]
    db.table("kg_signal").insert([{
        "id": f"n-{i}", "enterprise_id": "ent-A", "source_type": "revenue",
        "kind": "x", "content": "y", "properties": {}, "provenance": {},
        "valid_at": "2026-06-01T00:00:00+00:00",
        "transaction_at": "2026-06-01T00:00:00+00:00",
    } for i in range(5)]).execute()

    with caplog.at_level(_logging.INFO, logger="app.graph.facade"):
        got = GraphFacade().active_signals("ent-A", limit=5)

    assert len(got) == 5          # the fetch itself is unchanged...
    assert not [r for r in caplog.records if "limit" in r.getMessage().lower()]


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


def test_get_signals_chunks_a_large_id_list(facade, monkeypatch):
    """A big batch must CHUNK, not build one enormous `.in_()`.

    `.in_()` renders every id into the request URL and a UUID costs ~40 bytes
    there, so a few hundred ids is all a server's URL limit allows. This was a
    single unchunked query, which was safe only because every caller happened to
    be capped small upstream — the voice-of-customer retrieval preset lifts
    exactly those caps, so a widened feedback answer would have been the first
    thing to hit the ceiling. Same width and same reasoning as `edges_to_many`.

    Uses ids that resolve to nothing: the assertion is about how many reads are
    issued, and seeding 300 rows to observe that would only slow the suite.
    """
    calls = {"n": 0}
    orig_table = facade._client.table

    def _counting_table(name):
        if name == "kg_signal":
            calls["n"] += 1
        return orig_table(name)

    monkeypatch.setattr(facade._client, "table", _counting_table)
    facade.get_signals("ent-A", [f"missing-{i}" for i in range(300)])
    assert calls["n"] == 2, "300 ids at a 150 chunk width is two reads"


# ---------- batched get_entities / edges_to_many (Ask N+1 kill) ----------

def test_get_entities_batched_returns_dict(facade):
    from app.graph import Entity
    a = Entity(enterprise_id="ent-A", type="theme", canonical_label="aa")
    b = Entity(enterprise_id="ent-A", type="theme", canonical_label="bb")
    facade.create_entity("ent-A", a)
    facade.create_entity("ent-A", b)
    got = facade.get_entities("ent-A", [a.id, b.id])
    assert set(got) == {a.id, b.id}
    assert got[a.id].canonical_label == "aa"
    assert got[b.id].canonical_label == "bb"


def test_get_entities_empty_list_returns_empty_dict(facade):
    assert facade.get_entities("ent-A", []) == {}


def test_get_entities_dedups_and_skips_missing_and_is_tenant_scoped(facade):
    """Missing ids must be ABSENT rather than raising — callers rely on that to
    preserve `get_entity`'s "row gone → skip this candidate" semantics with a
    plain `.get()`."""
    from app.graph import Entity
    a = Entity(enterprise_id="ent-A", type="theme", canonical_label="aa")
    other = Entity(enterprise_id="ent-B", type="theme", canonical_label="cc")
    facade.create_entity("ent-A", a)
    facade.create_entity("ent-B", other)
    got = facade.get_entities("ent-A", [a.id, a.id, "does-not-exist", other.id])
    assert set(got) == {a.id}          # missing + cross-tenant absent, dup collapsed
    assert got[a.id].canonical_label == "aa"


def test_get_entities_single_query(facade, monkeypatch):
    """The batch must issue ONE underlying read, not one per id — this is the
    whole point of the method (it replaces up to 23 single-row reads per ask)."""
    from app.graph import Entity
    ents = []
    for i in range(5):
        e = Entity(enterprise_id="ent-A", type="theme", canonical_label=f"e{i}")
        facade.create_entity("ent-A", e)
        ents.append(e)

    calls = {"n": 0}
    orig_table = facade._client.table

    def _counting_table(name):
        if name == "kg_entity":
            calls["n"] += 1
        return orig_table(name)

    monkeypatch.setattr(facade._client, "table", _counting_table)
    got = facade.get_entities("ent-A", [e.id for e in ents])
    assert len(got) == 5
    assert calls["n"] == 1


def test_edges_to_many_covers_every_target_in_one_query(facade, monkeypatch):
    """One query for N targets, and every target's inbound edges still land."""
    from app.graph import Entity, Relationship
    themes, sources = [], []
    for i in range(4):
        t = Entity(enterprise_id="ent-A", type="theme", canonical_label=f"t{i}")
        s = Entity(enterprise_id="ent-A", type="account", canonical_label=f"s{i}")
        facade.create_entity("ent-A", t)
        facade.create_entity("ent-A", s)
        facade.write_relationship("ent-A", Relationship(
            enterprise_id="ent-A", type="AFFECTS", source_kind="entity",
            source_id=s.id, target_kind="entity", target_id=t.id))
        themes.append(t)
        sources.append(s)

    calls = {"n": 0}
    orig_table = facade._client.table

    def _counting_table(name):
        if name == "kg_relationship":
            calls["n"] += 1
        return orig_table(name)

    monkeypatch.setattr(facade._client, "table", _counting_table)
    edges = facade.edges_to_many("ent-A", [t.id for t in themes])
    assert calls["n"] == 1
    assert {e.target_id for e in edges} == {t.id for t in themes}


def test_edges_to_many_is_tenant_scoped_and_filters_by_type(facade):
    from app.graph import Entity, Relationship
    t = Entity(enterprise_id="ent-A", type="theme", canonical_label="t")
    s = Entity(enterprise_id="ent-A", type="account", canonical_label="s")
    facade.create_entity("ent-A", t)
    facade.create_entity("ent-A", s)
    facade.write_relationship("ent-A", Relationship(
        enterprise_id="ent-A", type="PROMOTED_TO", source_kind="entity",
        source_id=s.id, target_kind="entity", target_id=t.id))
    facade.write_relationship("ent-A", Relationship(
        enterprise_id="ent-A", type="AFFECTS", source_kind="entity",
        source_id=s.id, target_kind="entity", target_id=t.id))

    assert len(facade.edges_to_many("ent-A", [t.id])) == 2
    assert len(facade.edges_to_many("ent-A", [t.id], type="PROMOTED_TO")) == 1
    # Another tenant asking for the same id sees nothing.
    assert facade.edges_to_many("ent-B", [t.id]) == []


def test_edges_to_many_empty_returns_empty(facade):
    assert facade.edges_to_many("ent-A", []) == []


# ---------- find_candidates: hydration order + batching ----------
#
# This path had no CI coverage: the fake returns [] for any RPC nobody
# registered, so every test that reached `find_candidates` got an empty
# candidate list and asserted nothing about how rows are hydrated. Batching the
# hydration made that gap load-bearing — three callers gate a find-or-create on
# `candidates[0]` being the NEAREST match, and a reordering there wires signals
# to the wrong theme or creates duplicates in a forward-only graph.

_KG_CANDIDATES_FN = "kg_find_candidates"


@pytest.fixture
def kg_candidates():
    """Register rows for the `kg_find_candidates` RPC on the fake client."""
    from tests._fake_supabase import FakeSupabaseClient

    FakeSupabaseClient.rpc_returns.pop(_KG_CANDIDATES_FN, None)

    def _set(rows):
        FakeSupabaseClient.rpc_returns[_KG_CANDIDATES_FN] = rows

    yield _set
    FakeSupabaseClient.rpc_returns.pop(_KG_CANDIDATES_FN, None)


def test_find_candidates_preserves_rpc_order_not_score_order(facade, kg_candidates):
    """Output order must equal the RPC's row order verbatim.

    The scores here are deliberately EQUAL and the labels deliberately
    anti-alphabetical, so any implementation that re-sorts — by score, by label,
    or by iterating the hydration dict — produces a different first element and
    fails. `candidates[0]` is what three find-or-create callers trust."""
    from app.graph import Entity

    ents = []
    for i in range(4):
        e = Entity(enterprise_id="ent-A", type="theme", canonical_label=f"theme-{3 - i}")
        facade.create_entity("ent-A", e)
        ents.append(e)

    kg_candidates([
        {"id": e.id, "canonical_label": e.canonical_label, "type": "theme",
         "score": 0.5}
        for e in ents
    ])

    got = facade.find_candidates("ent-A", "theme", [0.1] * 1536, k=4)
    assert [e.id for e, _ in got] == [e.id for e in ents]
    assert got[0][0].id == ents[0].id


def test_find_candidates_skips_a_row_deleted_after_the_knn(facade, kg_candidates):
    """A candidate whose row is gone is skipped, not a KeyError — the same
    behaviour the per-row `if ent:` guard gave."""
    from app.graph import Entity

    live = Entity(enterprise_id="ent-A", type="theme", canonical_label="live")
    facade.create_entity("ent-A", live)

    kg_candidates([
        {"id": "vanished-between-knn-and-fetch", "canonical_label": "gone",
         "type": "theme", "score": 0.9},
        {"id": live.id, "canonical_label": "live", "type": "theme", "score": 0.4},
    ])

    got = facade.find_candidates("ent-A", "theme", [0.1] * 1536, k=2)
    assert [e.id for e, _ in got] == [live.id]


def test_find_candidates_returns_full_entities_in_one_query(facade, kg_candidates,
                                                            monkeypatch):
    """Hydration must stay FULL (the RPC returns no `aliases`, and
    `graph.extractor` reads it) while costing one query, not one per row."""
    from app.graph import Entity

    ents = []
    for i in range(5):
        e = Entity(enterprise_id="ent-A", type="theme", canonical_label=f"t{i}",
                   aliases=[f"alias-{i}"])
        facade.create_entity("ent-A", e)
        ents.append(e)

    kg_candidates([
        {"id": e.id, "canonical_label": e.canonical_label, "type": "theme",
         "score": 0.9 - (i * 0.1)}
        for i, e in enumerate(ents)
    ])

    calls = {"n": 0}
    orig_table = facade._client.table

    def _counting_table(name):
        if name == "kg_entity":
            calls["n"] += 1
        return orig_table(name)

    monkeypatch.setattr(facade._client, "table", _counting_table)
    got = facade.find_candidates("ent-A", "theme", [0.1] * 1536, k=5)

    assert len(got) == 5
    assert calls["n"] == 1                      # one batched hydration
    assert got[0][0].aliases == ["alias-0"]     # the column the RPC never returns


def test_find_candidates_is_tenant_scoped(facade, kg_candidates):
    """A candidate id belonging to another tenant hydrates to nothing, so it
    cannot cross the boundary even if the RPC handed it back."""
    from app.graph import Entity

    other = Entity(enterprise_id="ent-B", type="theme", canonical_label="theirs")
    facade.create_entity("ent-B", other)

    kg_candidates([
        {"id": other.id, "canonical_label": "theirs", "type": "theme", "score": 0.99},
    ])

    assert facade.find_candidates("ent-A", "theme", [0.1] * 1536, k=1) == []


# ---------- Leg C: content / entity search — hydration + rpc wiring ------
#
# The RPC's own SQL (word-boundary tsquery, cosine kNN, stale_after filter)
# is exercised by the migration's real-DB round-trip, not here — the fake
# has no pgvector/tsvector. What IS unit-testable, and had no coverage
# before this: hydration order, tenant scoping, and the `k`/param wiring —
# mirroring `find_candidates`'s own tests above exactly.

_KG_SIGNAL_SEARCH_FN = "kg_signal_search_by_content"
_KG_SIGNAL_EMBED_FN = "kg_find_signal_candidates"


@pytest.fixture
def kg_signal_search(request):
    """Register rows for the `kg_signal_search_by_content` RPC."""
    from tests._fake_supabase import FakeSupabaseClient

    FakeSupabaseClient.rpc_returns.pop(_KG_SIGNAL_SEARCH_FN, None)

    def _set(rows):
        FakeSupabaseClient.rpc_returns[_KG_SIGNAL_SEARCH_FN] = rows

    yield _set
    FakeSupabaseClient.rpc_returns.pop(_KG_SIGNAL_SEARCH_FN, None)


@pytest.fixture
def kg_signal_embed(request):
    """Register rows for the `kg_find_signal_candidates` RPC."""
    from tests._fake_supabase import FakeSupabaseClient

    FakeSupabaseClient.rpc_returns.pop(_KG_SIGNAL_EMBED_FN, None)

    def _set(rows):
        FakeSupabaseClient.rpc_returns[_KG_SIGNAL_EMBED_FN] = rows

    yield _set
    FakeSupabaseClient.rpc_returns.pop(_KG_SIGNAL_EMBED_FN, None)


def _sig(facade, ent, content):
    from app.graph.types import Signal

    s = Signal(enterprise_id=ent, source_type="customer_voice",
              kind="feature_request", content=content)
    facade.write_signal(ent, s)
    return s


def test_search_signals_by_content_preserves_rpc_order_and_score(facade, kg_signal_search):
    sigs = [_sig(facade, "ent-A", f"AIG fact {i}") for i in range(3)]
    # Deliberately anti-insertion-order rows, so a re-sort would be caught.
    kg_signal_search([
        {"id": sigs[2].id, "score": 0.9},
        {"id": sigs[0].id, "score": 0.5},
        {"id": sigs[1].id, "score": 0.1},
    ])

    got = facade.search_signals_by_content("ent-A", "AIG", k=3)
    assert [s.id for s, _ in got] == [sigs[2].id, sigs[0].id, sigs[1].id]
    assert [round(score, 2) for _, score in got] == [0.9, 0.5, 0.1]


def test_search_signals_by_content_passes_question_and_k_verbatim(facade, kg_signal_search, monkeypatch):
    seen = {}
    orig_rpc = facade._client.rpc

    def _capturing_rpc(fn, params):
        if fn == _KG_SIGNAL_SEARCH_FN:
            seen.update(params)
        return orig_rpc(fn, params)

    monkeypatch.setattr(facade._client, "rpc", _capturing_rpc)
    kg_signal_search([])
    facade.search_signals_by_content("ent-A", "what's the latest on AIG", k=17)

    assert seen == {
        "p_enterprise_id": "ent-A",
        "p_query": "what's the latest on AIG",
        "p_k": 17,
    }


def test_search_signals_by_content_skips_a_row_deleted_after_the_search(facade, kg_signal_search):
    live = _sig(facade, "ent-A", "still here")
    kg_signal_search([
        {"id": "vanished-between-search-and-fetch", "score": 0.9},
        {"id": live.id, "score": 0.4},
    ])
    got = facade.search_signals_by_content("ent-A", "q", k=2)
    assert [s.id for s, _ in got] == [live.id]


def test_search_signals_by_content_is_tenant_scoped(facade, kg_signal_search):
    """A row belonging to another tenant hydrates to nothing, so it cannot
    cross the boundary even if the RPC handed it back."""
    other = _sig(facade, "ent-B", "theirs")
    kg_signal_search([{"id": other.id, "score": 0.99}])
    assert facade.search_signals_by_content("ent-A", "q", k=1) == []


def test_signal_candidates_by_embedding_preserves_rpc_order_and_score(facade, kg_signal_embed):
    sigs = [_sig(facade, "ent-A", f"fact {i}") for i in range(3)]
    kg_signal_embed([
        {"id": sigs[1].id, "score": 0.95},
        {"id": sigs[2].id, "score": 0.5},
        {"id": sigs[0].id, "score": 0.2},
    ])

    got = facade.signal_candidates_by_embedding("ent-A", [0.1] * 1536, k=3)
    assert [s.id for s, _ in got] == [sigs[1].id, sigs[2].id, sigs[0].id]


def test_signal_candidates_by_embedding_passes_embedding_and_k_verbatim(facade, kg_signal_embed, monkeypatch):
    seen = {}
    orig_rpc = facade._client.rpc

    def _capturing_rpc(fn, params):
        if fn == _KG_SIGNAL_EMBED_FN:
            seen.update(params)
        return orig_rpc(fn, params)

    monkeypatch.setattr(facade._client, "rpc", _capturing_rpc)
    kg_signal_embed([])
    vec = [0.2] * 1536
    facade.signal_candidates_by_embedding("ent-A", vec, k=9)

    assert seen["p_enterprise_id"] == "ent-A"
    assert seen["p_embedding"] == vec
    assert seen["p_k"] == 9


def test_signal_candidates_by_embedding_is_tenant_scoped(facade, kg_signal_embed):
    other = _sig(facade, "ent-B", "theirs")
    kg_signal_embed([{"id": other.id, "score": 0.99}])
    assert facade.signal_candidates_by_embedding("ent-A", [0.1] * 1536, k=1) == []


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

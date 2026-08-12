"""Ask Planner — the per-company catalog caches, and the single catalog read.

Building the planner prompt costs three independent DB reads (custom skills,
connected providers, the document catalog) and the gates then read the catalog a
SECOND time to validate the model's picks. On a measured live turn that was
~2.3s of prompt assembly plus ~2s of gates inside a 17.4s POST /v1/chat/intent,
re-paid on every message.

What this file holds:

  * the reads happen ONCE per company per TTL, not once per message
  * the catalog is read ONCE per plan, not twice
  * the caches are KEYED BY COMPANY — the failure mode that matters is not a
    stale read, it is one tenant being served another tenant's skill names or
    document titles (the rule `_router_menu` carries in qa_agent and CLAUDE.md
    names explicitly)
  * the `documents` gate still validates; passing the ids in changes who
    fetched them, never whether they are checked
  * a catalog that cannot be read still yields a plan

No network / LLM / DB: `ask_planner.llm_call` is patched directly and every
catalog read is a counting stub.
"""
from __future__ import annotations

import pytest

import app.ask_planner as ap
import app.db.custom_skills as custom_skills_db
import app.document_catalog as document_catalog
import app.skills.resolver as resolver
from app.connector_lookup import registry

COMPANY = "co-acme-7f3d"
OTHER = "co-globex-11b2"


class _Doc:
    """The two `CatalogDocument` fields the planner touches."""

    def __init__(self, external_id, title="A doc", summary="", provider="slack"):
        self.external_id = external_id
        self.title = title
        self.summary = summary
        self.provider = provider


class _Result:
    def __init__(self, output):
        self.output = output


def _plan_out(**overrides):
    out = {
        "reason": "because",
        "company_skill_id": "none",
        "company_confidence": 0.0,
        "pipeline_id": "none",
        "confidence": 0.0,
        "sources": [],
        "include_knowledge_graph": True,
        "web_search": False,
        "constraints": None,
        "in_scope": True,
    }
    out.update(overrides)
    return out


@pytest.fixture(autouse=True)
def _clear_caches():
    """The caches are module-level and outlive a test.

    Without this a hit written by one test silently satisfies the next one's
    read and its call-count assertion passes for the wrong reason — which would
    make this whole file prove nothing."""
    ap._connected_cache.clear()
    ap._custom_block_cache.clear()
    ap._documents_cache.clear()
    yield
    ap._connected_cache.clear()
    ap._custom_block_cache.clear()
    ap._documents_cache.clear()


@pytest.fixture
def counts(monkeypatch):
    """Every catalog read the planner makes, counted per company."""
    tally = {"connected": [], "skills": [], "documents": []}

    def _connected(cid):
        tally["connected"].append(cid)
        return ["slack"]

    def _skills(cid):
        tally["skills"].append(cid)
        return []

    def _documents(cid, **kwargs):
        tally["documents"].append(cid)
        return [_Doc(f"doc-{cid}")]

    monkeypatch.setattr(registry, "connected_providers", _connected)
    monkeypatch.setattr(custom_skills_db, "list_custom_skills", _skills)
    monkeypatch.setattr(resolver, "get_custom_skill", lambda cid, wanted: None)
    monkeypatch.setattr(document_catalog, "list_documents", _documents)
    return tally


def _stub_planner(monkeypatch, payload=None):
    monkeypatch.setattr(ap, "llm_call", lambda **k: _Result(payload or _plan_out()))


# ── the reads happen once, not per message ───────────────────────────────────

def test_three_catalog_reads_are_paid_once_across_many_plans(monkeypatch, counts):
    _stub_planner(monkeypatch)

    for _ in range(3):
        ap.plan("what changed this week", enterprise_id=COMPANY)

    # Three plans, one read each — the point of the whole change.
    assert counts["connected"] == [COMPANY]
    assert counts["skills"] == [COMPANY]
    assert counts["documents"] == [COMPANY]


def test_the_document_catalog_is_read_once_per_plan_not_twice(monkeypatch, counts):
    """The prompt block and the `documents` gate share one read.

    They used to be two `list_documents` calls for the same rows on every plan —
    one to render the block, one inside `_gate_documents` to check the ids back."""
    _stub_planner(monkeypatch, _plan_out(documents=[f"doc-{COMPANY}"]))

    plan = ap.plan("summarize that doc", enterprise_id=COMPANY)

    assert counts["documents"] == [COMPANY]
    # And the gate still ran: the id survived because it is genuinely in the
    # catalog, not because validation was skipped.
    assert plan.documents == [f"doc-{COMPANY}"]


# ── the failure mode that matters: cross-tenant ──────────────────────────────

def test_each_company_reads_its_own_catalogs(monkeypatch, counts):
    _stub_planner(monkeypatch)

    ap.plan("q", enterprise_id=COMPANY)
    ap.plan("q", enterprise_id=OTHER)
    ap.plan("q", enterprise_id=COMPANY)  # served from cache, no second read

    assert counts["connected"] == [COMPANY, OTHER]
    assert counts["skills"] == [COMPANY, OTHER]
    assert counts["documents"] == [COMPANY, OTHER]


def test_one_company_never_receives_another_companys_documents(monkeypatch, counts):
    """The cache must not let COMPANY's rows validate for OTHER.

    A process-global cache keyed on nothing would do exactly that, which is the
    leak this keying exists to prevent — the same rule `_document_block`'s own
    docstring states."""
    _stub_planner(monkeypatch, _plan_out(documents=[f"doc-{COMPANY}"]))
    ap.plan("q", enterprise_id=COMPANY)  # warms COMPANY's catalog

    # OTHER asks for COMPANY's document id. Its own catalog holds `doc-OTHER`,
    # so the gate must drop it.
    plan = ap.plan("q", enterprise_id=OTHER)
    assert plan.documents == []


def test_conversation_scoped_reads_are_never_cached(monkeypatch):
    """Only the company-wide catalog is cached.

    A conversation-scoped read returns rows belonging to ONE conversation, and
    nothing would ever invalidate a per-conversation key — the write paths know
    a company, not a conversation — so with entries that live until restart it
    would be wrong forever. It passes straight through instead, and it is not on
    the hot path: `plan()` only ever asks for the company-wide catalog."""
    seen = []

    def _documents(cid, *, conversation_id=None, user_id=None, limit=None):
        seen.append((cid, conversation_id, user_id))
        return [_Doc(f"doc-{conversation_id}")]

    monkeypatch.setattr(document_catalog, "list_documents", _documents)

    ap._cached_documents(COMPANY, conversation_id=1, user_id="u1")
    ap._cached_documents(COMPANY, conversation_id=1, user_id="u1")

    # Read twice, fetched twice — no stale conversation rows can be pinned.
    assert seen == [(COMPANY, 1, "u1"), (COMPANY, 1, "u1")]

    # …and a scoped read must not poison the company-wide entry either.
    company_wide = ap._cached_documents(COMPANY)
    assert [d.external_id for d in company_wide] == ["doc-None"]


# ── the gate still gates ─────────────────────────────────────────────────────

def test_an_invented_document_id_is_still_dropped(monkeypatch, counts):
    """Handing the ids in must not become a way to skip validation."""
    _stub_planner(monkeypatch, _plan_out(documents=["doc-that-does-not-exist"]))

    plan = ap.plan("q", enterprise_id=COMPANY)
    assert plan.documents == []


def test_gate_documents_still_reads_for_callers_that_pass_nothing(monkeypatch):
    """`known_documents` is optional — every caller outside `plan()`, and every
    existing gate test, still takes the self-fetching path."""
    calls = []

    def _documents(cid, **kwargs):
        calls.append(cid)
        return [_Doc("doc-1")]

    monkeypatch.setattr(document_catalog, "list_documents", _documents)

    assert ap._gate_documents(["doc-1"], COMPANY) == ["doc-1"]
    assert calls == [COMPANY]


def test_passed_ids_are_used_verbatim_without_a_read(monkeypatch):
    def _boom(*a, **k):  # pragma: no cover — must never be reached
        raise AssertionError("the catalog was read despite known_documents")

    monkeypatch.setattr(document_catalog, "list_documents", _boom)

    assert ap._gate_documents(["doc-1"], COMPANY, {"doc-1"}) == ["doc-1"]
    assert ap._gate_documents(["nope"], COMPANY, {"doc-1"}) == []


# ── invalidation + degradation ───────────────────────────────────────────────

def test_invalidate_forces_the_next_plan_to_re_read(monkeypatch, counts):
    """For a caller that just connected a provider or uploaded a skill and wants
    the next message planned against it rather than waiting out the TTL."""
    _stub_planner(monkeypatch)

    ap.plan("q", enterprise_id=COMPANY)
    ap.invalidate_catalog_cache(COMPANY)
    ap.plan("q", enterprise_id=COMPANY)

    assert counts["connected"] == [COMPANY, COMPANY]
    assert counts["skills"] == [COMPANY, COMPANY]
    assert counts["documents"] == [COMPANY, COMPANY]


def test_invalidating_one_company_leaves_another_cached(monkeypatch, counts):
    _stub_planner(monkeypatch)

    ap.plan("q", enterprise_id=COMPANY)
    ap.plan("q", enterprise_id=OTHER)
    ap.invalidate_catalog_cache(COMPANY)
    ap.plan("q", enterprise_id=COMPANY)
    ap.plan("q", enterprise_id=OTHER)

    assert counts["connected"] == [COMPANY, OTHER, COMPANY]
    assert counts["documents"] == [COMPANY, OTHER, COMPANY]


def test_a_catalog_that_cannot_be_read_still_yields_a_plan(monkeypatch):
    """Unchanged from before the cache: the catalog must never break a plan."""
    def _boom(*a, **k):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(document_catalog, "list_documents", _boom)
    monkeypatch.setattr(registry, "connected_providers", lambda cid: [])
    monkeypatch.setattr(custom_skills_db, "list_custom_skills", lambda cid: [])
    monkeypatch.setattr(resolver, "get_custom_skill", lambda cid, wanted: None)
    _stub_planner(monkeypatch, _plan_out(documents=["doc-1"]))

    plan = ap.plan("q", enterprise_id=COMPANY)
    assert plan.documents == []
    assert plan.action == ap.ACTION_ANSWER


# ── the write paths that keep it correct ─────────────────────────────────────
#
# Entries live until the process restarts, so invalidation is not a nicety here
# — it IS the correctness mechanism. Each test below asserts one write path
# actually calls it, because a path that forgets leaves that company planning
# against data that no longer exists for the life of the process.

def test_writing_a_connection_drops_the_planner_cache(monkeypatch):
    import app.db.connections as connections

    dropped = []
    monkeypatch.setattr(ap, "invalidate_catalog_cache", lambda cid: dropped.append(cid))
    connections._drop_planner_cache(COMPANY)
    assert dropped == [COMPANY]


def test_writing_a_custom_skill_drops_the_planner_cache(monkeypatch):
    dropped = []
    monkeypatch.setattr(ap, "invalidate_catalog_cache", lambda cid: dropped.append(cid))
    custom_skills_db._drop_planner_cache(COMPANY)
    assert dropped == [COMPANY]


def test_writing_a_document_drops_the_planner_cache(monkeypatch):
    dropped = []
    monkeypatch.setattr(ap, "invalidate_catalog_cache", lambda cid: dropped.append(cid))
    document_catalog._drop_planner_cache(COMPANY)
    assert dropped == [COMPANY]


@pytest.mark.parametrize(
    "module, names",
    [
        ("app.db.connections", ["upsert_connection", "delete_connection"]),
        ("app.db.custom_skills", [
            "insert_custom_skill", "update_custom_skill",
            "delete_custom_skill", "detach_skills_from_source",
        ]),
        ("app.document_catalog", ["register_document", "deregister_document"]),
        ("app.db.artifact_templates", [
            "insert_template", "update_template", "set_compile_result",
            "set_template_summary", "delete_template",
        ]),
    ],
)
def test_every_known_write_path_invalidates(module, names):
    """The enumerated list, asserted against the source.

    Crude on purpose. The failure this guards is someone editing one of these
    functions and dropping the invalidate call, which no behavioural test would
    catch without a live DB — and which would be invisible until a customer's
    planner went stale for a week."""
    import importlib
    import inspect

    mod = importlib.import_module(module)
    for name in names:
        src = inspect.getsource(getattr(mod, name))
        assert "_drop_planner_cache" in src, f"{module}.{name} no longer invalidates"


def test_invalidation_never_breaks_a_write(monkeypatch):
    """A write must survive a cache that cannot be cleared. Losing the drop
    means a stale plan; losing the write means losing the user's data."""
    import app.db.connections as connections

    def _boom(cid):
        raise RuntimeError("planner import exploded")

    monkeypatch.setattr(ap, "invalidate_catalog_cache", _boom)
    connections._drop_planner_cache(COMPANY)  # must not raise
    custom_skills_db._drop_planner_cache(COMPANY)
    document_catalog._drop_planner_cache(COMPANY)


def test_a_failed_catalog_read_is_not_cached_as_success(monkeypatch):
    """A read that blew up must not pin an empty catalog for the whole TTL — the
    next plan retries. (`_cached_documents` caches the [] it degrades to, so
    this pins the behaviour either way rather than leaving it accidental.)"""
    calls = []

    def _documents(cid, **kwargs):
        calls.append(cid)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return [_Doc("doc-1")]

    monkeypatch.setattr(document_catalog, "list_documents", _documents)

    first = ap._cached_documents(COMPANY)
    assert first == []
    ap.invalidate_catalog_cache(COMPANY)
    second = ap._cached_documents(COMPANY)
    assert [d.external_id for d in second] == ["doc-1"]

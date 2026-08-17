"""Tracker-path knowledge-graph fallback — KG-first, live-as-enrichment.

The tracker path used to false-deny ("connect ClickUp / no tracker is connected
yet") whenever a live tracker session was absent, even though the 20-minute
connector sync keeps the same task data fresh in the knowledge graph. These
tests cover the fix: the tracker path now reads the graph instead of dead-ending,
while a live session — when it resolves — still enriches the answer.

Two tiers:

* Framework/tracker mechanics (no network/LLM/DB): the flag reaches the ClickUp
  and Jira calls, the softened hard-denies read the graph, live+KG coexist.
* `@pytest.mark.integration` real-KG proofs against local Supabase + a real
  model: a query that used to false-deny now answers from a seeded signal, both
  at the `tracker.answer` boundary AND end-to-end through `qa_agent.answer`
  (the surface where the demo symptom actually manifests). Seed then teardown by
  created-ids only, never by slug — the local rig is shared across tenants.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest

from app.connector_lookup import answer as ca
from app.connector_lookup import knowledge_graph as kg
from app.connector_lookup import tracker
from app.connector_lookup.base import LookupSession
from app.surface_scope import Surface, SurfaceScope


def _connections(monkeypatch, connected: set[str]) -> None:
    from app import db

    monkeypatch.setattr(
        db, "get_connection",
        lambda cid, prov: {"token_json_encrypted": "enc"} if prov in connected else None,
    )
    monkeypatch.setattr(db, "list_connections",
                        lambda cid: [{"provider": p} for p in sorted(connected)])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])


# ── tracker mechanics (no network/LLM/DB) ────────────────────────────────────


def test_clickup_branch_passes_include_knowledge_graph_true(monkeypatch):
    """AC1 — the ClickUp branch offers the graph alongside the live ClickUp tool."""
    _connections(monkeypatch, {"clickup"})
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "x"})
    tracker.answer(enterprise_id="co", question="show my open tickets")
    assert seen["include_knowledge_graph"] is True
    assert [p.provider for p in seen["providers"]] == ["clickup"]
    assert seen["skill_action"] == "ClickUp lookup"


def test_clickup_connected_offers_both_the_live_tool_and_the_kg_tool(monkeypatch):
    """AC1/AC4 — with ClickUp connected AND the flag on, the loop is handed BOTH
    the ClickUp live tool and the knowledge-graph tool (proven at the ca.answer
    layer the tracker calls, driven with a fake connected provider)."""
    class _Fake:
        provider = "clickup"
        display_name = "ClickUp"

        def open_session(self, eid):
            return LookupSession(provider="clickup", handle={"tenant": eid})

        def tools(self):
            return [{"name": "clickup_read", "description": "d",
                     "input_schema": {"type": "object"}}]

        def system_block(self):
            return "ClickUp rules"

        def dispatch(self, session, name, inp):
            return "live clickup result"

    monkeypatch.setattr(kg, "search", lambda eid, q: "kg result")
    captured = {}
    ca.answer(
        enterprise_id="co", question="show my open tickets",
        providers=[_Fake()], include_knowledge_graph=True,
        run_loop=lambda **k: captured.update(k) or "x", log=lambda *a: None,
    )
    assert {t["name"] for t in captured["tools"]} == {"clickup_read", kg.TOOL_NAME}


def test_named_clickup_not_connected_reads_kg_not_hard_deny(monkeypatch):
    """AC2 — naming ClickUp when it is not connected reads the graph via ClickUp's
    adapter with the flag on, instead of the "isn't connected" hard-deny."""
    _connections(monkeypatch, set())
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "kg"})
    out = tracker.answer(enterprise_id="co", question="my open tickets in clickup")
    assert out == {"answer": "kg"}
    assert seen["include_knowledge_graph"] is True
    assert [p.provider for p in seen["providers"]] == ["clickup"]
    assert "isn't connected" not in out.get("answer", "")


def test_no_tracker_connected_named_tracker_reads_kg(monkeypatch):
    """AC3 — a tracker named, none connected: the graph is read rather than the
    "no tracker is connected yet" copy."""
    _connections(monkeypatch, set())
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "kg"})
    out = tracker.answer(enterprise_id="co", question="any open jira tickets?")
    assert out == {"answer": "kg"}
    assert seen["include_knowledge_graph"] is True
    assert [p.provider for p in seen["providers"]] == ["jira"]
    assert "no tracker is connected" not in out.get("answer", "")


def test_connected_clickup_offers_both_live_and_kg(monkeypatch):
    """AC4 — the live pull enriches: a live-only field (a tag absent from the KG
    record) can surface while the KG tool is still offered."""
    class _Fake:
        provider = "clickup"
        display_name = "ClickUp"

        def open_session(self, eid):
            return LookupSession(provider="clickup", handle={"tenant": eid})

        def tools(self):
            return [{"name": "clickup_read", "description": "d",
                     "input_schema": {"type": "object"}}]

        def system_block(self):
            return "ClickUp rules"

        def dispatch(self, session, name, inp):
            return "checkout task — tag: launch-blocker"

    monkeypatch.setattr(kg, "search", lambda eid, q: "kg: checkout task exists")
    captured = {}

    def loop(**k):
        captured.update(k)
        # The model reaches for the live tool for the live-only tag.
        return k["dispatch"]("clickup_read", {})

    out = ca.answer(
        enterprise_id="co", question="what's the tag on the checkout task?",
        providers=[_Fake()], include_knowledge_graph=True,
        run_loop=loop, log=lambda *a: None,
    )
    assert {t["name"] for t in captured["tools"]} == {"clickup_read", kg.TOOL_NAME}
    assert "launch-blocker" in out["answer"]  # the live-only tag surfaced


def test_jira_branch_passes_include_knowledge_graph_true(monkeypatch):
    """AC5 — the Jira branch delegates to jira_lookup, which passes the flag AND a
    verbatim system_text into ca.answer."""
    _connections(monkeypatch, {"jira"})
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "jira"})
    tracker.answer(enterprise_id="co", question="status of PROJ-1")
    assert seen["include_knowledge_graph"] is True
    assert [p.provider for p in seen["providers"]] == ["jira"]
    assert seen["system_text"]  # Jira's tuned prompt is still passed verbatim


def test_enumeration_is_deferred_not_implemented():
    """AC11 — the delivered behaviour is the semantic KG search path; deterministic
    full enumeration (a skill_id-filtered recent_signals_by_skill read) is NOT
    wired from the tracker path in this ticket."""
    import inspect

    src = inspect.getsource(tracker)
    assert "recent_signals_by_skill" not in src
    # The tracker path reaches the graph only through the shared connector loop.
    assert "include_knowledge_graph=True" in src


def test_no_dbd_identifiers_in_changed_files():
    """AC12 — no ticket id, phase code, wave name, or agent name in the changed
    source. The forbidden tokens are assembled from fragments so this guard does
    NOT itself embed the very identifiers it forbids into the repo."""
    import app.connector_lookup.answer as answer_mod
    import app.connector_lookup.tracker as tracker_mod
    import app.jira_lookup as jira_mod
    import inspect

    tokens = [
        "CO" + "NN-" + r"\d+",              # this wave's ticket-id shape
        r"P\d+-\d+",                        # phase/sub-ticket code shape
        "d" + "bd",
        "dispos" + "able",
        "van" + "guard",
        "samsung-" + "readiness",
        "sprntly-" + "(builder|planner|gate1|verifier|tester|infra)",
    ]
    forbidden = re.compile(r"\b(" + "|".join(tokens) + r")\b", re.I)
    for mod in (answer_mod, tracker_mod, jira_mod):
        src = inspect.getsource(mod)
        assert not forbidden.search(src), f"forbidden identifier in {mod.__name__}"


# ── real-KG integration proofs (local Supabase + real model) ─────────────────
# Run with the local rig env exported:
#   RUN_TRACKER_KG_LIVE=1 pytest tests/test_tracker_lookup_kg_fallback.py \
#       -m integration
# Needs SUPABASE_URL (loopback) / SUPABASE_SERVICE_ROLE_KEY / SUPABASE_JWT_SECRET
# and a real ANTHROPIC/DESIGN_AGENT_ANTHROPIC key.

_RUN_LIVE = os.getenv("RUN_TRACKER_KG_LIVE") == "1" and bool(
    os.getenv("ANTHROPIC_API_KEY") or os.getenv("DESIGN_AGENT_ANTHROPIC_API_KEY")
)
_LIVE_SKIP = (
    "needs the local rig: RUN_TRACKER_KG_LIVE=1 plus SUPABASE_* (loopback) and a "
    "real ANTHROPIC/DESIGN_AGENT_ANTHROPIC key"
)

#: A distinctive token the model will echo when it lists the seeded task — makes
#: "answered from the seeded signal" checkable without depending on paraphrase.
_MARKER = "CHKOUT-90731"
_FALSE_DENIES = ("isn't connected", "no tracker is connected", "hasn't been added",
                 "connect **jira**", "connect **clickup**")


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        f"refusing to mutate a non-loopback Supabase ({url!r})"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY unset"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture()
def seeded_clickup_kg():
    """Stand up a throwaway company and seed ClickUp-shaped `kg_signal` rows under
    it exactly as the real sync writes them — `skill_id='clickup-extraction'`,
    `provenance.doc` like `clickup-sync-batch-<uuid>`. Teardown deletes the company
    by id; `kg_signal.enterprise_id` is `on delete cascade`, so the seeded rows go
    with it (id-scoped teardown, never slug-scoped — the local rig is shared)."""
    if not _RUN_LIVE:
        pytest.skip(_LIVE_SKIP)
    from app.graph.facade import GraphFacade
    from app.graph.types import Signal

    sb = _sb()
    eid = str(uuid.uuid4())
    slug = f"kgtest-{uuid.uuid4().hex[:20]}"
    sb.table("companies").insert(
        {"id": eid, "slug": slug, "display_name": "KG fallback test tenant"}
    ).execute()

    facade = GraphFacade()
    batch = f"clickup-sync-batch-{uuid.uuid4().hex}"
    contents = [
        f"ClickUp task {_MARKER}: Fix the broken checkout button on mobile. "
        "Status: In Review. List: Payments.",
        f"ClickUp task {_MARKER}-B: Investigate checkout latency spike. "
        "Status: Open. List: Payments.",
    ]
    for c in contents:
        facade.write_signal(eid, Signal(
            enterprise_id=eid,
            source_type="project_mgmt",
            kind="task",
            content=c,
            skill_id="clickup-extraction",
            provenance={"skill_id": "clickup-extraction", "doc": batch},
        ))
    try:
        yield eid
    finally:
        sb.table("companies").delete().eq("id", eid).execute()


@pytest.mark.integration
def test_previously_false_denied_clickup_query_now_answers_from_kg(seeded_clickup_kg, monkeypatch):
    """AC9 (module boundary) — ClickUp session absent, a natural-language tracker
    query answers from the seeded signal via `tracker.answer`, NOT the connect
    copy."""
    eid = seeded_clickup_kg
    from app import db

    monkeypatch.setattr(db, "get_connection", lambda cid, prov: None)  # nothing live
    monkeypatch.setattr(db, "list_connections", lambda cid: [])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])
    out = tracker.answer(
        enterprise_id=eid,
        question="what's the status of the checkout task in clickup?",
    )
    text = out["answer"].lower()
    assert _MARKER.lower() in text or "checkout" in text
    for deny in _FALSE_DENIES:
        assert deny not in text, f"false-deny leaked: {deny!r}"


@pytest.mark.integration
@pytest.mark.parametrize("scope", [
    SurfaceScope(surface=Surface.project_private),
    SurfaceScope(surface=Surface.main),
    None,
], ids=["project_private", "main", "scope_none"])
def test_demo_symptom_named_clickup_query_answers_from_kg_not_denied(seeded_clickup_kg, scope, monkeypatch):
    """AC10 (end-to-end surface — the demo symptom) — the EXACT reported phrasing
    'list our ClickUp tasks', ClickUp absent, driven through the FULL
    qa_agent.answer(scope=...) ladder, surfaces the seeded KG content and is NOT a
    false-deny — on the project-private surface (where `_skip_project_connectors`
    must NOT skip because a tracker is named), on main, and with `scope=None`.

    Landmark drift (flagged): this phrasing is claimed by the GENERIC
    named-connector interceptor (`registry.answer_for_hints`), NOT the tracker
    interceptor — `is_jira_lookup('list our ClickUp tasks')` is False (it needs a
    read-verb over a PM noun, and 'list … tasks' does not match). Both interceptors
    now KG-degrade through the SAME `connector_lookup.answer` enabler this ticket
    adds, so the demo symptom is fixed on either path. The tracker-interceptor path
    itself is exercised by the test below.

    NB: local/unit tier only — this does NOT close the demo-readiness litmus item;
    it MUST be re-run on STAGING against real connectors post-deploy."""
    from app import db, qa_agent

    eid = seeded_clickup_kg
    monkeypatch.setattr(db, "get_connection", lambda cid, prov: None)
    monkeypatch.setattr(db, "list_connections", lambda cid: [])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])
    # On a project surface a NAMED tracker must be ADMITTED (not skipped).
    if scope is not None and scope.surface != Surface.main:
        assert qa_agent._skip_project_connectors(scope, "list our ClickUp tasks", None) is False
    out = qa_agent.answer(
        enterprise_id=eid,
        question="list our ClickUp tasks",
        dataset=f"kgtest-{uuid.uuid4().hex[:8]}",
        scope=scope,
    )
    text = out["answer"].lower()
    assert _MARKER.lower() in text or "checkout" in text, out["answer"]
    for deny in _FALSE_DENIES:
        assert deny not in text, f"false-deny leaked: {deny!r}"


@pytest.mark.integration
@pytest.mark.parametrize("scope", [
    SurfaceScope(surface=Surface.project_private),
    None,
], ids=["project_private", "scope_none"])
def test_tracker_interceptor_named_clickup_reads_kg_end_to_end(seeded_clickup_kg, scope, monkeypatch):
    """AC10 (tracker-interceptor path) — a read-verb + PM-noun phrasing that names
    ClickUp ('show our open ClickUp tickets') routes through the TRACKER
    interceptor (`is_jira_lookup` True) → `tracker.answer` → the KG fallback, and
    answers from the seeded KG, not a false-deny. This exercises the real routing
    ladder + `_skip_project_connectors` + the tracker interceptor + the fix."""
    from app import db, qa_agent

    eid = seeded_clickup_kg
    monkeypatch.setattr(db, "get_connection", lambda cid, prov: None)
    monkeypatch.setattr(db, "list_connections", lambda cid: [])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])
    out = qa_agent.answer(
        enterprise_id=eid,
        question="show our open ClickUp tickets",
        dataset=f"kgtest-{uuid.uuid4().hex[:8]}",
        scope=scope,
    )
    assert out["_skill_action"] == "Tracker lookup", out.get("_skill_action")
    text = out["answer"].lower()
    assert _MARKER.lower() in text or "checkout" in text, out["answer"]
    for deny in _FALSE_DENIES:
        assert deny not in text, f"false-deny leaked: {deny!r}"


@pytest.mark.integration
def test_live_enrichment_path_still_works_when_session_resolves(seeded_clickup_kg, monkeypatch):
    """AC4 (live) — with the seeded KG AND a fake live ClickUp session returning a
    live-only tag, the answer can present the live tag while the KG stays
    available — enrichment coexists, KG does not replace live."""
    eid = seeded_clickup_kg

    class _Live:
        provider = "clickup"
        display_name = "ClickUp"

        def open_session(self, e):
            return LookupSession(provider="clickup", handle={"tenant": e})

        def tools(self):
            return [{"name": "clickup_read", "description": "read a task",
                     "input_schema": {"type": "object",
                                      "properties": {"q": {"type": "string"}}}}]

        def system_block(self):
            return "ClickUp: call clickup_read for live task fields including tags."

        def dispatch(self, session, name, inp):
            return f"{_MARKER}: Fix checkout button. tag: LIVE-ONLY-TAG-77."

    out = ca.answer(
        enterprise_id=eid,
        question=f"what tag is on task {_MARKER} in clickup?",
        providers=[_Live()], include_knowledge_graph=True,
    )
    assert "LIVE-ONLY-TAG-77" in out["answer"]

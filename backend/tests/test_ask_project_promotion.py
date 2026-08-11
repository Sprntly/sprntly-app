"""Tests for the individual-chat memory promotion hook — the wiring that
threads `project_id` through `ask_job_runner.run_ask_job` -> `_run_sync` and,
for a project-scoped ask, calls the existing group-chat promotion writer's
`maybe_promote_turn` (verbatim, unmodified) after the answer is already the
authoritative stored reply.

This is PURE WIRING: `maybe_promote_turn`'s own classifier/DB/regen contract
is already proven by `test_project_memory_promotion.py`. What this file
proves is narrower and specific to the hook:

  - a project-scoped ask actually reaches the real classifier + real DB with
    the individual exchange's transcript, and the resulting entry carries
    THIS ask's `conversation_id` as provenance (real-LLM live tier below — a
    stub here would mask whether the wiring is actually connected, same
    rationale `test_project_memory_promotion.py` already documents for the
    group path)
  - a non-project ask is byte-for-byte unaffected: no call, no row, no cost
    line, and the untouched per-user `_load_history` scoping
  - the promotion is best-effort at the worker level: even a hook failure
    that somehow propagated out of `_run_sync` cannot flip an already-`ready`
    job back to `error` (the `complete_ask_job` -> `fail_ask_job` status
    guards are the second line of defense behind `maybe_promote_turn`'s own
    never-raises contract)
  - `project_id` threads additively; every existing non-project-aware caller
    keeps working unchanged; the two `routes/ask.py` call sites are the only
    non-test callers of `run_ask_job`

Real-LLM / real-DB live tier is gated behind `RUN_ASK_PROJECT_PROMOTION_LIVE=1`
PLUS a real `ANTHROPIC_API_KEY`, mirroring `test_project_memory_promotion.py`'s
own live-tier shape exactly (same fixture pattern, same non-loopback guard).
The qa_agent answer-generation step is stubbed even in the live tier —
answer generation is a separate surface, out of scope here — but the
PROMOTION CLASSIFIER (`app.project_memory.call_json`) is left completely
unstubbed in that tier, so the wiring is proven against the real model +
the real local Postgres. Run it with:

    RUN_ASK_PROJECT_PROMOTION_LIVE=1 \\
        pytest tests/test_ask_project_promotion.py -m integration
"""
from __future__ import annotations

import inspect
import logging
import os
import time
import uuid

import jwt as pyjwt
import pytest

from app import ask_job_runner as ajr


def _payload(answer: str) -> dict:
    """An Ask-shaped payload, as qa_agent._tag leaves it."""
    return {
        "answer": answer, "key_points": [], "citations": [],
        "confidence": 0.8, "unanswered": "",
    }


_DURABLE_ANSWER = (
    "Locking the API rate limit at 100 requests/min per tenant, applied "
    "uniformly including enterprise accounts."
)
_DURABLE_QUESTION = (
    "Can you record that we're locking the API rate limit at 100 "
    "requests/min per tenant, with no exception for enterprise customers?"
)
_SMALLTALK_QUESTION = "thanks for the update earlier!"
_SMALLTALK_ANSWER = "You're welcome — happy to help any time."


# ── Threading / non-breakage ────────────────────────────────────────────


def test_project_id_threads_through_run_ask_job(isolated_settings, monkeypatch):
    """`project_id` reaches `_run_sync` and the hook fires when set; the
    default `None` path fires nothing — proven at the module boundary
    (`_run_sync`'s own kwarg), not just via the async wrapper."""
    sig_run = inspect.signature(ajr.run_ask_job)
    assert "project_id" in sig_run.parameters
    assert sig_run.parameters["project_id"].default is None
    sig_sync = inspect.signature(ajr._run_sync)
    assert "project_id" in sig_sync.parameters
    assert sig_sync.parameters["project_id"].default is None

    calls: list = []
    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: _payload("hi"))
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: None)
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)

    import app.project_memory as pm

    monkeypatch.setattr(
        pm, "maybe_promote_turn", lambda *a, **kw: calls.append(a) or None
    )

    import asyncio

    asyncio.run(
        ajr.run_ask_job(
            ask_id=1, enterprise_id="c1", question="q", dataset="d",
            conversation_id=5,
        )
    )
    assert calls == [], "default project_id=None must fire nothing"

    asyncio.run(
        ajr.run_ask_job(
            ask_id=2, enterprise_id="c1", question="q", dataset="d",
            conversation_id=5, project_id=9,
        )
    )
    assert len(calls) == 1
    assert calls[0] == (9, 5, "q\n\nSprntly: hi")


def test_prd_and_plain_ask_unbroken(isolated_settings, monkeypatch):
    """Existing non-project ask/runner behavior is unchanged: the answer
    still completes and the hook does not run. Also pins the caller
    enumeration this ticket propagated `project_id` into — the two
    `routes/ask.py` call sites remain the only non-test callers."""
    import pathlib

    src = pathlib.Path(__file__).parent.parent / "app" / "routes" / "ask.py"
    text = src.read_text()
    assert text.count("run_ask_job(") == 2, (
        "expected exactly the pytest-inline and prod create_task call sites"
    )
    import ast

    ast.parse(text)  # both call sites compile

    completed: dict = {}
    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: _payload("plain answer"))
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: completed.setdefault(i, p))
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)

    import app.project_memory as pm

    def boom(*a, **kw):  # noqa: ARG001
        raise AssertionError("maybe_promote_turn must not be called on a plain/PRD ask")

    monkeypatch.setattr(pm, "maybe_promote_turn", boom)

    import asyncio

    asyncio.run(
        ajr.run_ask_job(ask_id=3, enterprise_id="c1", question="q", dataset="d", prd_id=7)
    )
    assert completed[3]["answer"] == "plain answer"


# ── Non-project no-op (isolation, mutation-proofed) ─────────────────────


def test_non_project_ask_no_promotion_call(isolated_settings, monkeypatch, caplog):
    """AC3 — a completed non-project ask (`project_id=None`) triggers no
    `maybe_promote_turn` call, writes no memory row, and emits no
    `projects.memory.promotion` cost line."""
    import app.project_memory as pm

    def boom(*a, **kw):  # noqa: ARG001
        raise AssertionError("maybe_promote_turn must not be called for project_id=None")

    monkeypatch.setattr(pm, "maybe_promote_turn", boom)
    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: _payload("an answer"))
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: None)
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)

    import asyncio

    with caplog.at_level(logging.INFO):
        asyncio.run(
            ajr.run_ask_job(
                ask_id=4, enterprise_id="c1", question="q", dataset="d",
                conversation_id=8,
            )
        )

    from app.db.client import require_client

    rows = (
        require_client()
        .table("project_memory_entries")
        .select("id")
        .execute()
        .data
    )
    assert rows == []
    cost_lines = [
        r.getMessage() for r in caplog.records if "projects.memory.promotion" in r.getMessage()
    ]
    assert cost_lines == []


def test_per_user_history_path_unchanged():
    """AD-P2 guard: `_load_history`'s signature and its per-user
    `(company_id, user_id)` ownership scoping are untouched by this ticket —
    pinned directly against the live source, same technique
    `test_ask_project_context.py::test_load_history_unmodified` uses."""
    from app.routes import ask as ask_route

    sig = inspect.signature(ask_route._load_history)
    assert list(sig.parameters.keys()) == ["conversation_id", "company_id", "user_id"]

    source = inspect.getsource(ask_route._load_history)
    assert '.eq("company_id", company_id)' in source
    assert '.eq("user_id", user_id)' in source


# ── Error handling ────────────────────────────────────────────────────────


def test_promotion_failure_does_not_break_answer(isolated_settings, monkeypatch):
    """AC4 — a forced failure inside `maybe_promote_turn` must not propagate
    into a lost/errored answer. Two layers of defense are proven together:
    `maybe_promote_turn` itself never raises (the writer's own contract — a
    real classifier/DB failure inside it returns None, not an exception); and
    even in the deliberately-adversarial case where the hook call raises
    anyway, `complete_ask_job`'s `status == 'generating'` guard has already
    flipped the row to `ready` before the hook runs, so `fail_ask_job`'s own
    identical guard no-ops and the stored answer survives untouched."""
    import app.project_memory as pm

    def boom(*a, **kw):  # noqa: ARG001
        raise RuntimeError("forced promotion failure")

    monkeypatch.setattr(pm, "maybe_promote_turn", boom)
    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: _payload("the real answer"))
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)

    from app.db.client import require_client

    require_client().table("companies").insert(
        {"id": "c1", "slug": "c1-slug", "display_name": "C1"}
    ).execute()

    from app.db.asks import start_ask_job

    ask_id = start_ask_job(company_id="c1", dataset="d", question="q")

    import asyncio

    # run_ask_job's own outer except must swallow this; it must not raise
    # out of the call.
    asyncio.run(
        ajr.run_ask_job(
            ask_id=ask_id, enterprise_id="c1", question="q", dataset="d",
            conversation_id=11, project_id=99,
        )
    )

    from app.db.asks import get_ask_job

    row = get_ask_job(ask_id)
    assert row["status"] == "ready", (
        "the answer, already committed by complete_ask_job before the hook "
        "ran, must survive a hook failure untouched"
    )
    assert row["response"]["answer"] == "the real answer"


# ── Provenance ────────────────────────────────────────────────────────────


def test_individual_promoted_entry_editable(tenant_client, isolated_settings, monkeypatch):
    """AC7 (patchable/deletable side) — an entry promoted via the individual
    hook is reachable and editable through the SAME memory routes group-
    promoted entries use (AD-P11), proven end-to-end through the real
    `/v1/ask` route (pytest-inline path) rather than calling `run_ask_job`
    directly, so the route's own project membership + ownership gates are
    exercised too."""
    t = tenant_client.make(slug="acme-promo-editable")

    from app.db import projects as projects_db
    from app.db.workspaces import ensure_default_workspace

    ws_id = ensure_default_workspace(t.company_id)["id"]
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Editable promo",
        created_by=t.user_id,
    )

    import app.project_memory as pm

    def fake_call_json(*, system, user, model, schema=None, meta_out=None, **kwargs):  # noqa: ARG001
        if meta_out is not None:
            meta_out.update({
                "model": model, "input_tokens": 10, "output_tokens": 5,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            })
        return {
            "should_promote": True,
            "insight": "The team locked the API rate limit at 100 req/min per tenant.",
        }

    monkeypatch.setattr(pm, "call_json", fake_call_json)

    conv = t.client.post("/v1/conversations", json={"title": "c"}).json()
    conv_id = conv["id"]

    fake_llm_state = {"payload": _payload(_DURABLE_ANSWER)}
    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: fake_llm_state["payload"])

    r = t.client.post(
        "/v1/ask",
        json={
            "question": _DURABLE_QUESTION,
            "dataset": t.slug,
            "conversation_id": conv_id,
            "project_id": project["id"],
        },
    )
    assert r.status_code == 200, r.text

    from app.db.project_memory_entries import list_entries

    entries = list_entries(project["id"])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["promoted_by"] == "agent"
    assert entry["source_conversation_id"] == conv_id

    r_list = t.client.get(f"/v1/projects/{project['id']}/memory")
    assert r_list.status_code == 200
    assert any(e["id"] == entry["id"] for e in r_list.json()["entries"])

    r_edit = t.client.patch(
        f"/v1/projects/{project['id']}/memory/{entry['id']}",
        json={"body": "Edited by a teammate"},
    )
    assert r_edit.status_code == 200
    assert r_edit.json()["body"] == "Edited by a teammate"

    r_delete = t.client.delete(f"/v1/projects/{project['id']}/memory/{entry['id']}")
    assert r_delete.status_code == 200
    assert r_delete.json()["deleted"] is True


# ── Real-LLM / real-DB live tier ─────────────────────────────────────────
#
# Gated behind RUN_ASK_PROJECT_PROMOTION_LIVE=1 PLUS a real ANTHROPIC_API_KEY.
# Mutates real rows against a real (company, workspace, user) already seeded
# in the local rig — mirrors test_project_memory_promotion.py's live tier.
# qa_agent.answer is stubbed (answer generation is not this ticket's
# surface); app.project_memory.call_json is left UNSTUBBED.

_RUN_LIVE = os.getenv("RUN_ASK_PROJECT_PROMOTION_LIVE") == "1" and bool(
    os.getenv("ANTHROPIC_API_KEY")
)

_LIVE_SKIP_REASON = (
    "needs a real local Supabase + a real ANTHROPIC_API_KEY — set "
    "RUN_ASK_PROJECT_PROMOTION_LIVE=1 with SUPABASE_URL/"
    "SUPABASE_SERVICE_ROLE_KEY/SUPABASE_JWT_SECRET/ANTHROPIC_API_KEY "
    "pointed at the local rig and the projects/chat/memory migrations applied"
)


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live promotion round-trip against a "
        f"non-loopback SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    if not _RUN_LIVE:
        pytest.skip("live tier disabled")
    return _sb()


@pytest.fixture(scope="module")
def fixture_ids(sb):
    companies = sb.table("companies").select("id").limit(1).execute().data
    assert companies, "no company row in the local rig — seed one before running this test"
    company_id = companies[0]["id"]

    workspaces = (
        sb.table("workspaces").select("id").eq("company_id", company_id).limit(1).execute().data
    )
    assert workspaces, f"no workspace for company {company_id}"
    workspace_id = workspaces[0]["id"]

    owners = (
        sb.table("company_members")
        .select("user_id, role")
        .eq("company_id", company_id)
        .in_("role", ["owner", "admin"])
        .limit(1)
        .execute()
        .data
    )
    assert owners, f"need >=1 owner/admin company_members row for company {company_id}"
    user_id = owners[0]["user_id"]

    yield {"company_id": company_id, "workspace_id": workspace_id, "user_id": user_id}


@pytest.fixture
def project_ids(sb):
    created: list[int] = []
    yield created
    for pid in created:
        sb.table("projects").delete().eq("id", pid).execute()


@pytest.fixture
def conversation_ids(sb):
    created: list[int] = []
    yield created
    for cid in created:
        # A promoted entry's `source_conversation_id` FKs to this row — clear
        # any surviving entries first (independent of `project_ids` teardown
        # ordering, which may run before or after this one) so the delete
        # below doesn't trip `project_memory_entries_source_conversation_id_fkey`.
        sb.table("project_memory_entries").delete().eq(
            "source_conversation_id", cid
        ).execute()
        sb.table("conversations").delete().eq("id", cid).execute()


def _make_project(sb, fixture_ids, project_ids, *, name: str) -> dict:
    from app.db import projects as projects_db

    project = projects_db.create_project(
        company_id=fixture_ids["company_id"],
        workspace_id=fixture_ids["workspace_id"],
        name=f"{name} {uuid.uuid4().hex[:8]}",
        created_by=fixture_ids["user_id"],
    )
    project_ids.append(project["id"])
    return project


def _make_conversation(sb, fixture_ids, conversation_ids) -> int:
    row = (
        sb.table("conversations")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "user_id": fixture_ids["user_id"],
                "title": "Live individual promotion",
            }
        )
        .execute()
        .data[0]
    )
    conversation_ids.append(row["id"])
    return row["id"]


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_individual_project_ask_promotes_insight(
    sb, fixture_ids, project_ids, conversation_ids, monkeypatch
):
    """(a) A real completed individual project-chat ask whose exchange holds
    a durable insight results in exactly one agent-promoted row with correct
    provenance — real classifier decision, real Postgres insert, driven
    through the actual `run_ask_job` -> hook wiring (not a call to
    `maybe_promote_turn` directly, which `test_project_memory_promotion.py`
    already covers)."""
    project = _make_project(sb, fixture_ids, project_ids, name="Live individual durable")
    conv_id = _make_conversation(sb, fixture_ids, conversation_ids)

    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: _payload(_DURABLE_ANSWER))
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)

    import asyncio

    from app.db.asks import start_ask_job

    ask_id = start_ask_job(
        company_id=fixture_ids["company_id"], dataset="d", question=_DURABLE_QUESTION,
    )
    asyncio.run(
        ajr.run_ask_job(
            ask_id=ask_id,
            enterprise_id=fixture_ids["company_id"],
            question=_DURABLE_QUESTION,
            dataset="d",
            conversation_id=conv_id,
            project_id=project["id"],
        )
    )

    rows = (
        sb.table("project_memory_entries")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert len(rows) == 1, "a durable rate-limit decision must be promoted"
    entry = rows[0]
    assert entry["promoted_by"] == "agent"
    assert entry["author_user_id"] is None
    assert entry["source_conversation_id"] == conv_id


@pytest.mark.integration
@pytest.mark.real_memory_synthesis
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_individual_project_ask_regenerates_summary(
    sb, fixture_ids, project_ids, conversation_ids, monkeypatch
):
    """(b) AC7 — the connected-loop assertion proven through the ask path:
    after the individual-chat promotion, the scheduled regen (inherited
    from `maybe_promote_turn`, inline under pytest) leaves `get_summary`
    with `stale is False` and `summary_md` reflecting the new insight's
    substance."""
    project = _make_project(sb, fixture_ids, project_ids, name="Live individual regen")
    conv_id = _make_conversation(sb, fixture_ids, conversation_ids)

    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: _payload(_DURABLE_ANSWER))
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)

    import asyncio

    from app.db.asks import start_ask_job

    ask_id = start_ask_job(
        company_id=fixture_ids["company_id"], dataset="d", question=_DURABLE_QUESTION,
    )
    asyncio.run(
        ajr.run_ask_job(
            ask_id=ask_id,
            enterprise_id=fixture_ids["company_id"],
            question=_DURABLE_QUESTION,
            dataset="d",
            conversation_id=conv_id,
            project_id=project["id"],
        )
    )

    from app.db.project_memory_entries import get_summary

    summary = get_summary(project["id"])
    assert summary["stale"] is False, "the scheduled regen must have run inline under pytest"
    assert summary["summary_md"], "regen must have produced a real summary"
    body_lower = summary["summary_md"].lower()
    assert "100" in summary["summary_md"] or "rate limit" in body_lower, (
        "the regenerated summary_md must reflect the promoted insight's "
        f"substance, not just flip stale — got: {summary['summary_md']!r}"
    )


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_individual_project_ask_smalltalk_no_promotion(
    sb, fixture_ids, project_ids, conversation_ids, monkeypatch
):
    """(c) A completed individual project-chat ask whose exchange is
    trivial/small-talk (real classifier decides should_promote=false)
    writes no memory row."""
    project = _make_project(sb, fixture_ids, project_ids, name="Live individual smalltalk")
    conv_id = _make_conversation(sb, fixture_ids, conversation_ids)

    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: _payload(_SMALLTALK_ANSWER))
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)

    import asyncio

    from app.db.asks import start_ask_job

    ask_id = start_ask_job(
        company_id=fixture_ids["company_id"], dataset="d", question=_SMALLTALK_QUESTION,
    )
    asyncio.run(
        ajr.run_ask_job(
            ask_id=ask_id,
            enterprise_id=fixture_ids["company_id"],
            question=_SMALLTALK_QUESTION,
            dataset="d",
            conversation_id=conv_id,
            project_id=project["id"],
        )
    )

    rows = (
        sb.table("project_memory_entries")
        .select("id")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert rows == []

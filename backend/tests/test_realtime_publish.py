"""Tests for `app/realtime.py` — the best-effort Supabase Realtime Broadcast
publish helper (AD-P21/AD-P7) — and its wiring into the three publish-on-write
call-sites: the two group-turn writes (human + assistant,
`routes/projects.py`) and the delegated-brief delivery
(`project_delegation.py`).

Covers:
  - the envelope: one POST, `{topic, event, payload, private:true}`, the
    service-role `apikey`/bearer headers (AC1)
  - no-op when Realtime env is unconfigured (AC2)
  - best-effort swallow: HTTP raise -> None, one warning, no payload content
    in the log (AC3, AC9)
  - DTO shaping: the group/brief payloads are the SAME shape
    `list_group_turns`/`list_individual_turns` return -- never the raw
    `conversation_turns` insert row (AC4, AC6)
  - wiring: human group turn, assistant group turn, and a delivered brief
    each fire exactly one publish on the right topic/event (AC4, AC5, AC6)
  - no publish on any delegation decline path (AC7)
  - best-effort MUTATION-PROOF: forcing the broadcast POST to raise never
    fails the underlying group-turn write or the brief delivery -- turn
    persists / fact recorded / response+return value unchanged either way
    (AC8)

`fake_group_llm` mirrors `test_group_chat_turns.py`'s fixture of the same
name (not importable across test files) -- patches `app.llm.run_tool_loop`
(the group-agent reply path's call, via the unified answer engine's sixth
ladder branch) so no test here ever hits Anthropic.
"""
from __future__ import annotations

import logging

import pytest

from app import realtime
from tests._company_helpers import company_client


def _create_project(ctx, *, name: str = "Realtime publish project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def _seed_assignee(project_id: int, *, name: str = "Fortune Adeyemi", role: str = "Designer") -> str:
    from app.db import projects as projects_db
    from app.db.client import require_client
    import uuid

    user_id = "assignee-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": user_id, "email": f"{user_id}@co.com", "full_name": name, "role": role}
    ).execute()
    projects_db.add_member(project_id, user_id)
    return user_id


def _stub_brief_llm(monkeypatch, *, reply: str = "Here is the brief. Please proceed."):
    from app import project_delegation

    calls: list[dict] = []

    def _fake_call_md(*, system, user, model, meta_out=None, **kwargs):  # noqa: ARG001
        calls.append({"system": system, "user": user})
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model,
                    "input_tokens": 60,
                    "output_tokens": 20,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            )
        return reply

    monkeypatch.setattr(project_delegation, "call_md", _fake_call_md)
    return calls


@pytest.fixture
def fake_group_llm(isolated_settings, monkeypatch):
    """Patches the ONE call site the group-agent reply path uses
    (`app.llm.run_tool_loop`, via the unified answer engine's sixth ladder
    branch) so no test here ever hits Anthropic. Mirrors
    `test_group_chat_turns.py::fake_group_llm`."""
    state: dict = {
        "calls": [],
        "reply": "On it -- I'll take a look and report back.",
        "raise_error": False,
    }

    def _fake_run_tool_loop(*, system, user, tools, dispatch, model, meta_out=None, **kwargs):
        state["calls"].append({"system": system, "user": user, "model": model})
        if state["raise_error"]:
            raise RuntimeError("simulated LLM failure")
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model,
                    "input_tokens": 120,
                    "output_tokens": 42,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            )
        return state["reply"]

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_run_tool_loop)
    return state


class _Resp:
    def __init__(self, status_code: int = 202):
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _spy_publish(monkeypatch):
    """Replace `realtime.httpx.post` with a spy that records every call and
    returns a 202, without any real network traffic -- same shape as
    `test_welcome_email.py`'s `_fake_post` pattern."""
    calls: list[dict] = []

    def _fake_post(url, **kwargs):
        calls.append({"url": url, "headers": kwargs.get("headers"), "json": kwargs.get("json")})
        return _Resp(202)

    monkeypatch.setattr(realtime.httpx, "post", _fake_post)
    return calls


# ── Creation / serialization (AC1, AC4, AC6) ──────────────────────────────


def test_publish_broadcast_posts_single_private_message(isolated_settings, monkeypatch):
    calls = _spy_publish(monkeypatch)

    realtime.publish_broadcast("project:1", "turn.created", {"id": 1, "content": "hi"})

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://fake.supabase.co/realtime/v1/api/broadcast"
    assert call["headers"]["apikey"] == "fake-service-role-key"
    assert call["headers"]["Authorization"] == "Bearer fake-service-role-key"
    assert call["json"] == {
        "messages": [
            {
                "topic": "project:1",
                "event": "turn.created",
                "payload": {"id": 1, "content": "hi"},
                "private": True,
            }
        ]
    }


def test_group_publish_payload_is_shaped_dto(isolated_settings, monkeypatch, fake_group_llm):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    from app.db import projects as projects_db
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": ctx.user_id, "full_name": "Ada Lovelace", "role": "Engineer"}
    ).execute()
    # A SECOND human member — a solo (single-human) project now bypasses the
    # gate entirely and always replies (the solo-project auto-respond fix),
    # which would publish a SECOND (assistant) broadcast this test doesn't
    # want to shape-assert on.
    projects_db.add_member(project["id"], "second-human")

    calls = _spy_publish(monkeypatch)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns", json={"content": "morning team"}
    )
    assert r.status_code == 200

    assert len(calls) == 1
    payload = calls[0]["json"]["messages"][0]["payload"]
    # The broadcast now carries the execution-run status keys too, so the
    # realtime shape matches the poll read (AC16) — still a hard whitelist, no
    # raw-row-only column leaks. run_status is None here (broadcast happens
    # before any run is scheduled for this human turn). `reply` rides the
    # whitelist for assistant turns (the full structured reply); on a human
    # turn it is simply None.
    assert set(payload.keys()) == {
        "id", "role", "content", "author_user_id", "author_name",
        "author_job_role", "created_at", "reply", "run_status", "error_class",
    }
    assert payload["reply"] is None
    assert payload["run_status"] is None
    assert payload["error_class"] is None
    assert payload["author_name"] == "Ada Lovelace"
    assert payload["author_job_role"] == "Engineer"
    assert payload["role"] == "user"
    assert payload["content"] == "morning team"
    # No raw-row-only column (e.g. conversation_id) ever leaks.
    assert "conversation_id" not in payload


def test_brief_publish_payload_is_shaped_dto(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _stub_brief_llm(monkeypatch)

    calls = _spy_publish(monkeypatch)

    from app import project_delegation
    from app.db import projects as projects_db

    roster = projects_db.list_members(project["id"])
    result = project_delegation.handle_delegate_task(
        project_id=project["id"],
        assigner_user_id=ctx.user_id,
        source_conversation_id=1,
        source_turn_id=1,
        roster=roster,
        dataset="",
        company_id="unused-in-fake-db",
        tool_input={"assignee": "Fortune", "task_summary": "Draft the pricing page"},
    )
    assert "Sent the brief" in result

    assert len(calls) == 1
    payload = calls[0]["json"]["messages"][0]["payload"]
    assert set(payload.keys()) == {"id", "role", "content", "created_at"}
    assert payload["role"] == "assistant"
    assert payload["content"] != ""
    # No raw-row-only column (author_user_id/conversation_id) ever leaks.
    assert "conversation_id" not in payload
    assert "author_user_id" not in payload


# ── Error handling / best-effort (AC2, AC3, AC9) ──────────────────────────


def test_publish_noop_when_unconfigured(isolated_settings, monkeypatch):
    monkeypatch.setattr(
        realtime.config_mod.settings, "supabase_url", "", raising=False
    )
    calls = _spy_publish(monkeypatch)

    realtime.publish_broadcast("project:1", "turn.created", {"id": 1})

    assert calls == []


def test_publish_swallows_http_error(isolated_settings, monkeypatch, caplog):
    def _boom(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(realtime.httpx, "post", _boom)

    with caplog.at_level(logging.WARNING, logger="app.realtime"):
        result = realtime.publish_broadcast(
            "project:1", "turn.created", {"id": 1, "content": "SECRET_DO_NOT_LOG"}
        )
    assert result is None

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "topic=project:1" in msg
    assert "event=turn.created" in msg
    assert "RuntimeError" in msg
    assert "SECRET_DO_NOT_LOG" not in msg


def test_publish_failure_does_not_fail_group_write(isolated_settings, monkeypatch, fake_group_llm):
    """Best-effort MUTATION-PROOF (AC8): force the broadcast POST to raise
    -> the human group-turn write and its response must be entirely
    unaffected. If `publish_broadcast`'s own try/except regressed, this
    would turn RED (500 / turn missing)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    # A SECOND human member — see test_group_publish_payload_is_shaped_dto;
    # otherwise the solo-project auto-respond shortcut adds an assistant
    # turn this write-isolation proof isn't about.
    from app.db import projects as projects_db

    projects_db.add_member(project["id"], "second-human")

    def _boom(url, **kwargs):
        raise RuntimeError("realtime down")

    monkeypatch.setattr(realtime.httpx, "post", _boom)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns", json={"content": "hello team"}
    )
    assert r.status_code == 200
    assert r.json()["content"] == "hello team"
    assert r.json()["role"] == "user"

    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project["id"])
    turns = conversations_db.list_group_turns(conv["id"])
    assert [t["content"] for t in turns] == ["hello team"]


def test_publish_failure_does_not_fail_brief_delivery(isolated_settings, monkeypatch):
    """Best-effort MUTATION-PROOF (AC8): force the broadcast POST to raise
    -> the brief is still delivered, the `project_delegations` fact is
    still recorded, and `handle_delegate_task` returns its normal string."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _stub_brief_llm(monkeypatch)

    def _boom(url, **kwargs):
        raise RuntimeError("realtime down")

    monkeypatch.setattr(realtime.httpx, "post", _boom)

    from app import project_delegation
    from app.db import projects as projects_db
    from app.db.client import require_client

    roster = projects_db.list_members(project["id"])
    result = project_delegation.handle_delegate_task(
        project_id=project["id"],
        assigner_user_id=ctx.user_id,
        source_conversation_id=1,
        source_turn_id=1,
        roster=roster,
        dataset="",
        company_id="unused-in-fake-db",
        tool_input={"assignee": "Fortune", "task_summary": "Draft the pricing page"},
    )
    assert "Sent the brief" in result  # normal string, NOT the decline path

    assert (
        len(require_client().table("project_delegations").select("id").execute().data) == 1
    )
    delivered = (
        require_client()
        .table("conversations")
        .select("id")
        .eq("project_id", project["id"])
        .eq("kind", "individual")
        .execute()
        .data
    )
    assert len(delivered) == 1
    turns = (
        require_client()
        .table("conversation_turns")
        .select("id")
        .eq("conversation_id", delivered[0]["id"])
        .execute()
        .data
    )
    assert len(turns) == 1


def test_publish_failure_when_group_reread_raises_does_not_fail_write(
    isolated_settings, monkeypatch, fake_group_llm
):
    """Best-effort MUTATION-PROOF (AD-P22): force the DTO-shaping RE-READ
    (`list_group_turns`, called by `_publish_group_turn_created`
    IMMEDIATELY after the human turn already persisted -- the first
    `list_group_turns` call this route makes) to raise -> the route must
    still return 200 with the turn's body, and the turn must still be the
    one persisted row. Before the AD-P22 fix, this re-read sat outside any
    try/except in `post_group_turn_route`, so this exact failure 500'd a
    request whose write had already succeeded. The pre-filter in
    `project_group_gate` short-circuits "hello team" (<=4 words, no "?",
    no agent cue) to `respond=False` with NO classifier call, so the
    route's LATER `list_group_turns` call for the smart-interjection
    gate -- which must keep working -- never hits an LLM either."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    # A SECOND human member — a solo (single-human) project now bypasses the
    # gate + prefilter entirely and always replies (the solo-project
    # auto-respond fix), which would add an assistant turn this re-read
    # isolation proof isn't about.
    from app.db import projects as projects_db

    projects_db.add_member(project["id"], "second-human")

    from app.db import conversations as conversations_db

    real_list_group_turns = conversations_db.list_group_turns
    calls = {"n": 0}

    def _boom_on_first_call(conversation_id, since=None):
        calls["n"] += 1
        if calls["n"] == 1:  # the publish-prep re-read -- must not break the write
            raise RuntimeError("transient DB hiccup")
        return real_list_group_turns(conversation_id, since=since)

    monkeypatch.setattr(conversations_db, "list_group_turns", _boom_on_first_call)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns", json={"content": "hello team"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "hello team"
    assert r.json()["role"] == "user"

    turns = real_list_group_turns(
        conversations_db.get_group_chat(project["id"])["id"]
    )
    assert [t["content"] for t in turns] == ["hello team"]


def test_publish_failure_when_brief_reread_raises_does_not_fail_delivery(
    isolated_settings, monkeypatch
):
    """Best-effort MUTATION-PROOF (AD-P22): force the DTO-shaping RE-READ
    (`list_individual_turns`, called by `_publish_brief_delivered` AFTER
    the brief turn + `project_delegations` fact already persisted) to
    raise -> `handle_delegate_task` must still return the success string,
    with the fact and the individual turn both durably recorded. Before
    the AD-P22 fix, this re-read sat inside the SAME try/except that
    returns the decline string, so a re-read failure mis-reported an
    already-successful delivery as failed."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _stub_brief_llm(monkeypatch)

    from app import project_delegation
    from app.db import projects as projects_db
    from app.db.client import require_client

    def _boom(conversation_id, user_id, since=None):
        raise RuntimeError("transient DB hiccup")

    monkeypatch.setattr(project_delegation, "list_individual_turns", _boom)

    roster = projects_db.list_members(project["id"])
    result = project_delegation.handle_delegate_task(
        project_id=project["id"],
        assigner_user_id=ctx.user_id,
        source_conversation_id=1,
        source_turn_id=1,
        roster=roster,
        dataset="",
        company_id="unused-in-fake-db",
        tool_input={"assignee": "Fortune", "task_summary": "Draft the pricing page"},
    )
    assert "Sent the brief" in result  # normal string, NOT the decline path

    assert (
        len(require_client().table("project_delegations").select("id").execute().data) == 1
    )
    delivered = (
        require_client()
        .table("conversations")
        .select("id")
        .eq("project_id", project["id"])
        .eq("kind", "individual")
        .execute()
        .data
    )
    assert len(delivered) == 1
    turns = (
        require_client()
        .table("conversation_turns")
        .select("id")
        .eq("conversation_id", delivered[0]["id"])
        .execute()
        .data
    )
    assert len(turns) == 1


# ── Call-site wiring (AC4, AC5, AC6, AC7) ─────────────────────────────────


def test_post_group_turn_publishes_turn_created(isolated_settings, monkeypatch, fake_group_llm):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    # A SECOND human member — see test_group_publish_payload_is_shaped_dto;
    # otherwise the solo-project auto-respond shortcut always replies.
    from app.db import projects as projects_db

    projects_db.add_member(project["id"], "second-human")
    calls = _spy_publish(monkeypatch)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns", json={"content": "no mention here"}
    )
    assert r.status_code == 200
    assert fake_group_llm["calls"] == []  # no LLM call -> no assistant publish

    assert len(calls) == 1
    msg = calls[0]["json"]["messages"][0]
    assert msg["topic"] == f"project:{project['id']}"
    assert msg["event"] == "turn.created"
    assert msg["payload"]["role"] == "user"


def test_agent_reply_publishes_assistant_turn(isolated_settings, monkeypatch, fake_group_llm):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    calls = _spy_publish(monkeypatch)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "@Sprntly please delegate this to Fortune"},
    )
    assert r.status_code == 200
    assert len(fake_group_llm["calls"]) == 1

    # One publish for the human turn, one for the assistant reply.
    assert len(calls) == 2
    topics = [c["json"]["messages"][0]["topic"] for c in calls]
    events = [c["json"]["messages"][0]["event"] for c in calls]
    assert topics == [f"project:{project['id']}"] * 2
    assert events == ["turn.created", "turn.created"]

    assistant_payload = calls[1]["json"]["messages"][0]["payload"]
    assert assistant_payload["role"] == "assistant"
    assert assistant_payload["author_user_id"] is None


def test_delivered_brief_publishes_on_per_user_topic(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _stub_brief_llm(monkeypatch)
    calls = _spy_publish(monkeypatch)

    from app import project_delegation
    from app.db import projects as projects_db

    roster = projects_db.list_members(project["id"])
    result = project_delegation.handle_delegate_task(
        project_id=project["id"],
        assigner_user_id=ctx.user_id,
        source_conversation_id=1,
        source_turn_id=1,
        roster=roster,
        dataset="",
        company_id="unused-in-fake-db",
        tool_input={"assignee": "Fortune", "task_summary": "Draft the pricing page"},
    )
    assert "Sent the brief" in result

    assert len(calls) == 1
    msg = calls[0]["json"]["messages"][0]
    assert msg["topic"] == f"project:{project['id']}:user:{assignee_id}"
    assert msg["event"] == "brief.delivered"
    # Never the group channel -- a private brief on `project:{id}` would leak.
    assert msg["topic"] != f"project:{project['id']}"


def test_no_match_decline_no_publish(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _seed_assignee(project["id"])
    calls_brief = _stub_brief_llm(monkeypatch)
    calls = _spy_publish(monkeypatch)

    from app import project_delegation
    from app.db import projects as projects_db

    roster = projects_db.list_members(project["id"])
    result = project_delegation.handle_delegate_task(
        project_id=project["id"],
        assigner_user_id=ctx.user_id,
        source_conversation_id=1,
        source_turn_id=1,
        roster=roster,
        dataset="",
        company_id="unused-in-fake-db",
        tool_input={"assignee": "Nobody Here", "task_summary": "Draft the pricing page"},
    )
    assert "?" in result
    assert calls_brief == [], "no_match must never reach the brief call"
    assert calls == [], "no_match must publish zero times"


def test_guard_failure_decline_no_publish(isolated_settings, monkeypatch):
    """A resolved assignee that fails the double-membership re-check
    (AD-P16/AD-P18 -- the load-bearing IDOR gate; the closest actual
    decline path in the current code to a "self-delegation" guard, which
    is NOT separately implemented in `handle_delegate_task` today) writes
    no turn and publishes zero times."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _seed_assignee(project["id"])
    _stub_brief_llm(monkeypatch)
    calls = _spy_publish(monkeypatch)

    from app import project_delegation
    from app.db import projects as projects_db

    outsider = {"user_id": "outsider-not-a-member", "name": "Outsider", "job_role": None}
    monkeypatch.setattr(
        project_delegation, "resolve_member",
        lambda project_id, needle: {"status": "resolved", "member": outsider},
    )

    roster = projects_db.list_members(project["id"])
    result = project_delegation.handle_delegate_task(
        project_id=project["id"],
        assigner_user_id=ctx.user_id,
        source_conversation_id=1,
        source_turn_id=1,
        roster=roster,
        dataset="",
        company_id="unused-in-fake-db",
        tool_input={"assignee": "Outsider", "task_summary": "Draft the pricing page"},
    )
    assert "only hand tasks between members" in result
    assert calls == [], "a gate-failure decline must publish zero times"


# ── Regression / non-breakage (AC10) ──────────────────────────────────────


def test_turn_write_and_delegate_contracts_unchanged(isolated_settings, monkeypatch, fake_group_llm):
    """The group route's response body shape and `handle_delegate_task`'s
    signature/return/ordering are identical to pre-ticket -- publish-on-write
    is a pure side-effect, never a response-shape change."""
    import inspect

    from app import project_delegation

    sig = inspect.signature(project_delegation.handle_delegate_task)
    assert list(sig.parameters) == [
        "project_id", "assigner_user_id", "source_conversation_id",
        "source_turn_id", "roster", "dataset", "company_id", "tool_input",
    ]
    assert sig.return_annotation in (str, "str")

    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _spy_publish(monkeypatch)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns", json={"content": "hello team"}
    )
    assert r.status_code == 200
    body = r.json()
    # Response is still the raw inserted turn row (unchanged shape) --
    # publish-on-write is a pure side-effect, never a response-shape change.
    assert body["role"] == "user"
    assert body["content"] == "hello team"
    assert body["author_user_id"] == ctx.user_id
    assert "id" in body and "created_at" in body

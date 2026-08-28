"""Tests for `app/realtime.py` — the best-effort Supabase Realtime Broadcast
publish helper (AD-P21/AD-P7) — and its wiring into the delegated-brief
delivery publish-on-write call-site (`project_delegation.py`). The group-turn
publish call-sites this file originally also covered were removed with the
group-chat backend.

Covers:
  - the envelope: one POST, `{topic, event, payload, private:true}`, the
    service-role `apikey`/bearer headers (AC1)
  - no-op when Realtime env is unconfigured (AC2)
  - best-effort swallow: HTTP raise -> None, one warning, no payload content
    in the log (AC3, AC9)
  - DTO shaping: the brief payload is the SAME shape `list_individual_turns`
    returns -- never the raw `conversation_turns` insert row (AC4, AC6)
  - wiring: a delivered brief fires exactly one publish on the right
    topic/event (AC6)
  - no publish on any delegation decline path (AC7)
  - best-effort MUTATION-PROOF: forcing the broadcast POST to raise never
    fails the brief delivery -- fact recorded / return value unchanged
    either way (AC8)
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


def test_delegate_contract_unchanged(isolated_settings, monkeypatch):
    """`handle_delegate_task`'s signature/return/ordering are identical to
    pre-ticket -- publish-on-write is a pure side-effect, never a
    signature change."""
    import inspect

    from app import project_delegation

    sig = inspect.signature(project_delegation.handle_delegate_task)
    assert list(sig.parameters) == [
        "project_id", "assigner_user_id", "source_conversation_id",
        "source_turn_id", "roster", "dataset", "company_id", "tool_input",
        # Added by "delegate to teammates and stop fabricating their responses"
        # (9513cc26): the source turn's own text, so the handler quotes what was
        # actually said instead of inventing the teammate's words. Optional and
        # keyword-only, so every existing caller is unaffected — which is what
        # this non-breakage guard is really asserting.
        "source_content",
    ]
    assert sig.return_annotation in (str, "str")

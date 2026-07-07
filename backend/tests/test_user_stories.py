"""User stories → ClickUp: skill binding, generation shape, ClickUp create_task,
push error-isolation, and the generate/push routes (review-before-write).

All ClickUp HTTP and the LLM gateway are mocked — these tests never reach the
real ClickUp API or a real model.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

from app.graph.gateway import LLMResult
from app.skills.loader import get_skill
from app.stories.generate import Story, generate_user_stories
from app.stories.push import (
    ClickUpNotConnectedError,
    push_stories_to_clickup,
)

from tests._company_helpers import company_client, seed_connection


@pytest.fixture(autouse=True)
def _stub_clickup_task_map(monkeypatch):
    """Default the ticket→ClickUp-task map to empty (create path) + no-op save,
    so push tests don't hit Supabase. The idempotency test overrides the getter."""
    import app.stories.push as push_mod
    monkeypatch.setattr(push_mod, "get_clickup_task_id", lambda *a, **k: None)
    monkeypatch.setattr(push_mod, "save_clickup_task_id", lambda *a, **k: None)


def _llm_result(output):
    return LLMResult(
        output=output, model="claude-sonnet-4-6",
        prompt_version="user-stories-v1+user-stories@deadbeef",
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="tool_use",
    )


_TWO_STORIES = {
    "stories": [
        {
            "title": "Connect ClickUp",
            "body": "As a PM, I want to connect ClickUp, so that I can push stories.",
            "acceptance_criteria": [
                "Given a valid token, When I connect, Then it succeeds.",
                "Given a bad token, When I connect, Then I see an error.",
            ],
            "priority": "high",
            "route": "agent-ready",
        },
        {
            "title": "Review before push",
            "body": "As a PM, I want to review stories, so that I control my tracker.",
            "acceptance_criteria": ["Given generated stories, Then nothing is written yet."],
            "priority": "normal",
            "route": "needs-human",
        },
    ]
}


# ───────────────────────── skill is vendored + binds ──────────────────────────

def test_user_stories_skill_is_vendored():
    spec = get_skill("user-stories")
    assert spec.id == "user-stories"
    # The vendored skill is the delivery-tickets rewrite: PRD requirements →
    # tracker-ready tickets, auto story-map sizing, acceptance criteria inherited
    # from the spec, multi-tool sync via the field-mapping reference.
    assert "story map" in spec.method and "acceptance criteria" in spec.method
    assert "field-mapping.md" in spec.method


def _tool_msg(payload):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name="submit_response",
                                 input=payload)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                              cache_creation_input_tokens=0,
                              cache_read_input_tokens=2),
        stop_reason="tool_use",
    )


def test_generation_binds_user_stories_skill_to_gateway(isolated_settings, monkeypatch):
    """The skill's METHOD reaches the model prompt and pins the prompt_version."""
    from app import llm

    captured: dict = {}

    def _create(**kw):
        captured.update(kw)
        return _tool_msg(_TWO_STORIES)

    # user-stories generation runs long_output=True → the gateway streams, so the
    # fake client must also satisfy the `messages.stream(...)` protocol.
    from tests._fake_anthropic import _FakeStream

    def _stream(**kw):
        captured.update(kw)
        return _FakeStream(_tool_msg(_TWO_STORIES))

    monkeypatch.setattr(
        llm, "get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=_create, stream=_stream)),
    )

    spec = get_skill("user-stories")
    stories = generate_user_stories("ent-A", insight="Users want SSO.")
    prefix_text = captured["messages"][0]["content"][0]["text"]
    assert prefix_text.startswith(f"## METHOD (skill: user-stories @{spec.content_hash})")
    assert len(stories) == 2


# ───────────────────────── generation shape ──────────────────────────────────

def test_generate_returns_structured_stories(isolated_settings, monkeypatch):
    import app.stories.generate as gen
    monkeypatch.setattr(gen, "llm_call", lambda **kw: _llm_result(_TWO_STORIES))

    stories = generate_user_stories("ent-A", insight="some insight")
    assert [type(s) for s in stories] == [Story, Story]
    first = stories[0]
    assert first.title == "Connect ClickUp"
    assert "As a PM" in first.body and "so that" in first.body
    assert len(first.acceptance_criteria) == 2
    assert first.priority == "high"
    assert first.clickup_priority() == 2  # high -> 2 on ClickUp's 1-4 scale


def test_generate_requires_exactly_one_source(isolated_settings):
    with pytest.raises(ValueError):
        generate_user_stories("ent-A")
    with pytest.raises(ValueError):
        generate_user_stories("ent-A", prd_id=1, insight="x")


def test_generate_passes_prd_part_b_to_model(isolated_settings, monkeypatch):
    """A PRD with Part B (llm_part) is fed into the model input (spec-aware)."""
    import app.stories.generate as gen
    monkeypatch.setattr(
        gen, "get_prd_rendered",
        lambda pid: {"title": "T", "payload_md": "Part A prose",
                     "llm_part": "PART-B-SPEC-MARKER"},
    )
    seen: dict = {}

    def _capture(**kw):
        seen.update(kw)
        return _llm_result(_TWO_STORIES)

    monkeypatch.setattr(gen, "llm_call", _capture)
    generate_user_stories("ent-A", prd_id=7)
    assert "PART-B-SPEC-MARKER" in seen["input"]
    assert seen["skill"] == "user-stories"
    assert seen["agent"] == "user_stories"


def test_generate_unknown_prd_raises(isolated_settings, monkeypatch):
    import app.stories.generate as gen
    from app.stories.generate import PRDNotFoundError
    monkeypatch.setattr(gen, "get_prd_rendered", lambda pid: None)
    with pytest.raises(PRDNotFoundError):
        generate_user_stories("ent-A", prd_id=999)


def test_story_to_description_renders_criteria():
    s = Story(title="X", body="As a user, I want Y, so that Z.",
              acceptance_criteria=["Given a, When b, Then c."], route="agent-ready")
    desc = s.to_description()
    assert "As a user" in desc
    assert "Given a, When b, Then c." in desc
    assert "agent-ready" in desc


# ───────────────────────── ClickUp create_task (HTTP mocked) ──────────────────

def test_create_task_posts_correct_url_payload_and_auth(monkeypatch):
    from app.connectors import clickup_oauth

    calls: dict = {}

    def _fake_post(url, json=None, headers=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        calls["headers"] = headers
        return SimpleNamespace(
            ok=True, status_code=200,
            json=lambda: {"id": "abc123", "url": "https://app.clickup.com/t/abc123"},
        )

    monkeypatch.setattr(clickup_oauth.requests, "post", _fake_post)
    out = clickup_oauth.create_task(
        "raw-token-xyz", "list-9",
        name="Story title", description="body", priority=2,
    )
    assert calls["url"] == "https://api.clickup.com/api/v2/list/list-9/task"
    assert calls["json"] == {"name": "Story title", "description": "body", "priority": 2}
    # Raw token, NO "Bearer " prefix (the ClickUp auth quirk).
    assert calls["headers"]["Authorization"] == "raw-token-xyz"
    assert out == {"id": "abc123", "url": "https://app.clickup.com/t/abc123"}


def test_create_task_raises_on_clickup_error(monkeypatch):
    from fastapi import HTTPException

    from app.connectors import clickup_oauth

    # A generic upstream failure (5xx) raises HTTPException so push isolates it
    # as a per-task error.
    monkeypatch.setattr(
        clickup_oauth.requests, "post",
        lambda *a, **k: SimpleNamespace(ok=False, status_code=500, text="boom"),
    )
    with pytest.raises(HTTPException):
        clickup_oauth.create_task("t", "l", name="n")


def test_create_task_raises_auth_expired_on_401(monkeypatch):
    # A rejected token (401/403) is distinct: ClickUp has no refresh token, so
    # we surface ClickUpAuthExpiredError → the route turns it into a reconnect.
    from app.connectors import clickup_oauth

    monkeypatch.setattr(
        clickup_oauth.requests, "post",
        lambda *a, **k: SimpleNamespace(ok=False, status_code=401, text="nope"),
    )
    with pytest.raises(clickup_oauth.ClickUpAuthExpiredError):
        clickup_oauth.create_task("t", "l", name="n")


def test_list_lists_walks_teams_spaces(monkeypatch):
    from app.connectors import clickup_oauth

    responses = {
        "/team": {"teams": [{"id": "team1"}]},
        "/team/team1/space": {"spaces": [{"id": "sp1", "name": "Space One"}]},
        "/space/sp1/list": {"lists": [{"id": "L1", "name": "Backlog"}]},
        "/space/sp1/folder": {"folders": [
            {"name": "Sprint", "lists": [{"id": "L2", "name": "Current"}]}
        ]},
    }
    monkeypatch.setattr(clickup_oauth, "_get",
                        lambda token, path, params=None: responses[path])
    lists = clickup_oauth.list_lists("tok")
    ids = {l["id"] for l in lists}
    assert ids == {"L1", "L2"}
    by_id = {l["id"]: l for l in lists}
    assert by_id["L2"]["folder"] == "Sprint"
    assert by_id["L1"]["space"] == "Space One"


# ───────────────────────── push: decrypt + error isolation ────────────────────

def _seed_clickup_token(monkeypatch, company_id, token="real-token"):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    import importlib
    import sys
    importlib.reload(sys.modules["app.config"])
    importlib.reload(sys.modules["app.connectors.tokens"])
    seed_connection(company_id=company_id, provider="clickup",
                    token_blob={"access_token": token})


def test_push_error_isolation_one_fails_rest_continue(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_clickup_token(monkeypatch, ctx.company_id)

    from app.connectors import clickup_oauth

    def _create_task(token, list_id, *, name, description=None, markdown_description=None, priority=None, extra=None):
        if name == "boom":
            raise RuntimeError("clickup 500")
        return {"id": f"id-{name}", "url": f"u-{name}"}

    monkeypatch.setattr(clickup_oauth, "create_task", _create_task)

    stories = [
        Story(title="ok1", body="b"),
        Story(title="boom", body="b"),
        Story(title="ok2", body="b"),
    ]
    result = push_stories_to_clickup(ctx.company_id, "list-1", stories)
    assert [c["story"] for c in result["created"]] == ["ok1", "ok2"]
    assert len(result["errors"]) == 1
    assert result["errors"][0]["story"] == "boom"
    assert "clickup 500" in result["errors"][0]["error"]


def test_push_maps_canonical_fields_to_clickup(isolated_settings, monkeypatch):
    """The canonical ticket's fields map onto the ClickUp task: a rich markdown
    body, labels → tags, story points → points."""
    ctx = company_client(monkeypatch)
    _seed_clickup_token(monkeypatch, ctx.company_id)

    from app.connectors import clickup_oauth

    seen: dict = {}

    def _create_task(token, list_id, *, name, description=None, markdown_description=None, priority=None, extra=None):
        seen.update(name=name, markdown_description=markdown_description, priority=priority, extra=extra)
        return {"id": "cu-1", "url": "u"}

    monkeypatch.setattr(clickup_oauth, "create_task", _create_task)

    story = Story(
        title="Battle card", body="As an AE, I want a card, so that I can run the play.",
        what="Create a one-page battle card.", user_story="As an AE, I want a card, so that I can run the play.",
        acceptance_criteria=["Given X, When Y, Then Z."],
        labels=["sales-enablement", "competitive"], story_points=3, priority="urgent",
    )
    push_stories_to_clickup(ctx.company_id, "list-1", [story])

    # Rich body (markdown), not a plain description.
    assert "**What**" in seen["markdown_description"]
    assert seen["priority"] == 1  # urgent → 1
    assert seen["extra"]["tags"] == ["sales-enablement", "competitive"]
    assert seen["extra"]["points"] == 3


def test_push_syncs_subtasks_and_dependencies(isolated_settings, monkeypatch):
    """A newly-created task gets a Child-issues checklist (one item per subtask,
    [P] stripped), and in-batch blocked_by links become ClickUp dependencies."""
    ctx = company_client(monkeypatch)
    _seed_clickup_token(monkeypatch, ctx.company_id)

    from app.connectors import clickup_oauth

    ids = iter(["cu-A", "cu-B"])
    monkeypatch.setattr(clickup_oauth, "create_task",
                        lambda *a, **k: {"id": next(ids), "url": "u"})

    checklist_items: list[str] = []
    deps: list[tuple[str, str]] = []
    monkeypatch.setattr(clickup_oauth, "create_checklist", lambda tok, tid, name: "cl-1")
    monkeypatch.setattr(clickup_oauth, "create_checklist_item",
                        lambda tok, cid, name, resolved=False: checklist_items.append(name))
    monkeypatch.setattr(clickup_oauth, "add_dependency",
                        lambda tok, tid, *, depends_on: deps.append((tid, depends_on)))

    one_pager = Story(title="One-Pager", body="b")
    battle_card = Story(
        title="Battle Card", body="b",
        subtasks=["Pull win/loss notes", "[P] Draft objection table"],
        blocked_by=["T-1 — One-Pager"],  # references One-Pager by title
    )
    push_stories_to_clickup(ctx.company_id, "list-1", [one_pager, battle_card])

    # Subtasks became checklist items with the [P] marker stripped.
    assert checklist_items == ["Pull win/loss notes", "Draft objection table"]
    # Battle Card (cu-B) waits on One-Pager (cu-A).
    assert deps == [("cu-B", "cu-A")]


def test_push_is_idempotent_updates_existing_task(isolated_settings, monkeypatch):
    """A re-push of a ticket already synced to this list UPDATEs the existing
    ClickUp task instead of creating a duplicate."""
    ctx = company_client(monkeypatch)
    _seed_clickup_token(monkeypatch, ctx.company_id)

    import app.stories.push as push_mod
    from app.connectors import clickup_oauth

    story = Story(title="Battle card", body="As an AE, I want a card, so that I can run the play.")
    # This ticket was pushed before → its ClickUp task id is on file.
    monkeypatch.setattr(push_mod, "get_clickup_task_id", lambda c, l, t: "cu-existing")

    created_calls, updated_calls = [], []

    def _create_task(token, list_id, *, name, **kw):
        created_calls.append(name)
        return {"id": "cu-new", "url": "u"}

    def _update_task(token, task_id, *, name=None, **kw):
        updated_calls.append(task_id)
        return {"id": task_id, "url": "u"}

    monkeypatch.setattr(clickup_oauth, "create_task", _create_task)
    monkeypatch.setattr(clickup_oauth, "update_task", _update_task)

    result = push_stories_to_clickup(ctx.company_id, "list-1", [story])
    assert created_calls == []              # no duplicate created
    assert updated_calls == ["cu-existing"]  # existing task updated
    assert result["created"][0]["updated"] is True
    assert result["created"][0]["task_id"] == "cu-existing"


def test_push_not_connected_raises(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    # No clickup connection seeded.
    with pytest.raises(ClickUpNotConnectedError):
        push_stories_to_clickup(ctx.company_id, "list-1", [Story(title="x", body="b")])


# ───────────────────────── routes (dep-override + tenant) ─────────────────────

def test_route_generate_returns_a_job_not_a_hung_request(isolated_settings, monkeypatch):
    # Generation is now fire-and-forget: the POST returns a job id immediately
    # instead of blocking on the multi-minute LLM call. The full
    # generate→poll→stories flow is covered in test_routes_stories_async.py.
    ctx = company_client(monkeypatch)
    import app.routes.stories as routes
    monkeypatch.setattr(
        routes, "generate_user_stories",
        lambda enterprise_id, **kw: [
            Story(title="S1", body="As a x, I want y, so that z.",
                  acceptance_criteria=["Given, When, Then."], priority="low"),
        ],
    )
    r = ctx.client.post("/v1/stories/generate", json={"insight": "hello"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "generating"
    assert isinstance(data["job_id"], int)
    # No stories inline — they arrive via GET /v1/stories/jobs/{job_id}.
    assert "stories" not in data


def test_route_generate_rejects_both_sources(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/stories/generate",
                        json={"prd_id": 1, "insight": "x"})
    assert r.status_code == 400


def test_route_push_creates_tasks(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_clickup_token(monkeypatch, ctx.company_id)

    from app.connectors import clickup_oauth
    posted = []

    def _create_task(token, list_id, *, name, description=None, markdown_description=None, priority=None, extra=None):
        posted.append((list_id, name, priority))
        return {"id": "T1", "url": "https://app.clickup.com/t/T1"}

    monkeypatch.setattr(clickup_oauth, "create_task", _create_task)

    r = ctx.client.post("/v1/stories/push", json={
        "list_id": "list-42",
        "stories": [{
            "title": "Push me", "body": "As a x, I want y, so that z.",
            "acceptance_criteria": ["Given, When, Then."], "priority": "urgent",
        }],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"][0]["task_id"] == "T1"
    assert body["errors"] == []
    assert posted == [("list-42", "Push me", 1)]  # urgent -> 1


def test_route_push_not_connected_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/stories/push", json={
        "list_id": "L", "stories": [{"title": "x", "body": "b"}],
    })
    assert r.status_code == 404


def test_route_lists_not_connected_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/stories/lists")
    assert r.status_code == 404


def test_route_generate_requires_company(isolated_settings, monkeypatch):
    """No bearer → require_company rejects (tenant gate)."""
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/stories/generate", json={"insight": "x"},
                        headers={"Authorization": ""})
    assert r.status_code in (401, 403)

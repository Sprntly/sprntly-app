"""Tests for the group-chat surface: `db/conversations.py`'s new
`create_group_chat`/`get_group_chat`/`list_group_turns`/`post_group_turn`/
`user_in_group_roster` helpers, and the `/v1/projects/{id}/group*` routes.

Covers:
  - one `kind='group'` conversation per project, idempotent create (AC1)
  - roster seeded from `project_members` at creation (AC2)
  - human turns store `author_user_id` + `role='user'` (AC3)
  - human-to-human turns never call the LLM (AC4)
  - an `@Sprntly` mention triggers exactly one assistant turn + one
    structured cost-summary log line (AC5), case-insensitively
  - a failed mention-triggered LLM call is best-effort: no assistant turn,
    the human turn still persists, never raises (AC6)
  - poll read (`since` cursor), ascending, with author name/role (AC7)
  - membership gate (403 same-tenant non-member, 404 foreign tenant, AC8)
  - the AD-P2 isolation regression: the group path can never read/write an
    individual chat's turns, and the per-user helpers this ticket must not
    touch are byte-for-byte unchanged (AC9)
  - observability: create/post logs carry only ids, never turn content
    (AC10)

`fake_group_llm` patches `app.routes.projects.run_tool_loop` directly (NOT
the `fake_llm` fixture in conftest, which only patches `call_json` — the
group agent reply path uses `run_tool_loop`, AD-P15's tool-on-the-reply-call
wiring for `delegate_task`). The fake returns plain reply text and does NOT
invoke `dispatch(...)`, simulating a no-tool-call turn — none of these
tests exercise delegation.
"""
from __future__ import annotations

import hashlib
import inspect
import logging

import pytest

from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member


def _create_project(ctx, *, name: str = "Group chat project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


@pytest.fixture
def fake_group_llm(isolated_settings, monkeypatch):
    """Patches the ONE call site the group-agent reply path uses
    (`app.routes.projects.run_tool_loop`) so no test ever hits Anthropic.
    `state["calls"]` is the no-LLM-for-human-turns assertion point. The fake
    does NOT invoke `dispatch(...)` — every test in this file simulates a
    no-tool-call turn (delegation itself is covered in
    `test_project_delegation.py`)."""
    state: dict = {
        "calls": [],
        "reply": "On it — I'll take a look and report back.",
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

    import app.routes.projects as projects_route

    monkeypatch.setattr(projects_route, "run_tool_loop", _fake_run_tool_loop)
    return state


# ── Creation ─────────────────────────────────────────────────────────────


def test_create_group_chat_one_per_project(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db.client import require_client

    first = conversations_db.create_group_chat(project["id"], ctx.user_id)
    second = conversations_db.create_group_chat(project["id"], ctx.user_id)
    assert first["id"] == second["id"]
    assert first["kind"] == "group"
    assert first["project_id"] == project["id"]

    rows = (
        require_client()
        .table("conversations")
        .select("id")
        .eq("project_id", project["id"])
        .eq("kind", "group")
        .execute()
        .data
    )
    assert len(rows) == 1

    # The HTTP route is idempotent the same way (AC1's literal wording).
    r1 = ctx.client.post(f"/v1/projects/{project['id']}/group")
    r2 = ctx.client.post(f"/v1/projects/{project['id']}/group")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"] == first["id"]


def test_group_chat_seeds_roster_from_members(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db import projects as projects_db
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": "member-2", "email": "m2@co.com"}
    ).execute()
    projects_db.add_member(project["id"], "member-2")

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)

    roster = (
        require_client()
        .table("project_chat_members")
        .select("user_id")
        .eq("conversation_id", conv["id"])
        .execute()
        .data
    )
    assert {r["user_id"] for r in roster} == {ctx.user_id, "member-2"}
    assert conversations_db.user_in_group_roster(conv["id"], ctx.user_id) is True
    assert conversations_db.user_in_group_roster(conv["id"], "someone-else") is False


# ── Serialization / retrieval ───────────────────────────────────────────


def test_post_group_turn_records_author(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)
    turn = conversations_db.post_group_turn(conv["id"], ctx.user_id, "hello team")
    assert turn["role"] == "user"
    assert turn["author_user_id"] == ctx.user_id
    assert turn["content"] == "hello team"


def test_list_group_turns_since_cursor(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": ctx.user_id, "full_name": "Ada Lovelace", "role": "Engineer"}
    ).execute()

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)
    t1 = conversations_db.post_group_turn(conv["id"], ctx.user_id, "first")
    t2 = conversations_db.post_group_turn(conv["id"], ctx.user_id, "second")

    turns = conversations_db.list_group_turns(conv["id"])
    assert [t["content"] for t in turns] == ["first", "second"]
    assert turns[0]["author_name"] == "Ada Lovelace"
    assert turns[0]["author_job_role"] == "Engineer"

    since_first = conversations_db.list_group_turns(conv["id"], since=t1["id"])
    assert [t["id"] for t in since_first] == [t2["id"]]


def test_agent_turn_null_author_sprntly_label(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)
    conversations_db.post_group_turn(conv["id"], None, "I'm on it.", role="assistant")

    turns = conversations_db.list_group_turns(conv["id"])
    assert turns[-1]["role"] == "assistant"
    assert turns[-1]["author_user_id"] is None
    assert turns[-1]["author_name"] == "Sprntly"
    assert turns[-1]["author_job_role"] is None


# ── LLM gating ───────────────────────────────────────────────────────────


def test_human_turn_no_llm_call(isolated_settings, monkeypatch, fake_group_llm):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    # A SECOND human member — a solo (single-human) project now bypasses the
    # gate entirely and always replies (the solo-project auto-respond fix),
    # so this "no LLM call for an unaddressed human turn" case needs a real
    # second person for the gate path to even be reachable.
    from app.db import projects as projects_db

    projects_db.add_member(project["id"], "second-human")

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns", json={"content": "morning team"}
    )
    assert r.status_code == 200
    assert fake_group_llm["calls"] == []

    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project["id"])
    turns = conversations_db.list_group_turns(conv["id"])
    assert [t["role"] for t in turns] == ["user"]


def test_mention_triggers_single_assistant_turn(
    isolated_settings, monkeypatch, fake_group_llm, caplog
):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        r = ctx.client.post(
            f"/v1/projects/{project['id']}/group/turns",
            json={"content": "@Sprntly can you summarize the last decision?"},
        )
    assert r.status_code == 200
    assert len(fake_group_llm["calls"]) == 1

    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project["id"])
    turns = conversations_db.list_group_turns(conv["id"])
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[1]["author_user_id"] is None
    assert turns[1]["content"] == fake_group_llm["reply"]

    cost_lines = [
        rec.getMessage()
        for rec in caplog.records
        if "mention_reply" in rec.getMessage()
    ]
    assert len(cost_lines) == 1
    assert "est_cost_usd=" in cost_lines[0]
    assert "mode=group" in cost_lines[0]
    assert f"project_id={project['id']}" in cost_lines[0]
    assert f"conversation_id={conv['id']}" in cost_lines[0]


def test_mention_llm_failure_best_effort(isolated_settings, monkeypatch, fake_group_llm):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    fake_group_llm["raise_error"] = True

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns", json={"content": "@Sprntly help"}
    )
    assert r.status_code == 200  # never raises to the caller (AD-P7)

    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project["id"])
    turns = conversations_db.list_group_turns(conv["id"])
    assert [t["role"] for t in turns] == ["user"]
    assert turns[0]["content"] == "@Sprntly help"


@pytest.mark.parametrize("mention", ["@Sprntly", "@sprntly", "@SPRNTLY"])
def test_mention_case_insensitive(isolated_settings, monkeypatch, fake_group_llm, mention):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns", json={"content": f"{mention} status?"}
    )
    assert r.status_code == 200
    assert len(fake_group_llm["calls"]) == 1


# ── Error handling / isolation (mutation-proofed — R4) ──────────────────


def test_non_member_post_forbidden(isolated_settings, monkeypatch, fake_group_llm):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _, non_member_headers = seed_same_tenant_non_member(ctx)

    r_post = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "hi"},
        headers=non_member_headers,
    )
    assert r_post.status_code == 403

    r_list = ctx.client.get(
        f"/v1/projects/{project['id']}/group/turns", headers=non_member_headers
    )
    assert r_list.status_code == 403

    r_create = ctx.client.post(
        f"/v1/projects/{project['id']}/group", headers=non_member_headers
    )
    assert r_create.status_code == 403

    assert fake_group_llm["calls"] == []


def test_foreign_project_group_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)

    from app.db import projects as projects_db

    foreign = projects_db.create_project(
        company_id="foreign-co",
        workspace_id="foreign-ws",
        name="Not mine",
        created_by="someone-else",
    )

    assert ctx.client.post(f"/v1/projects/{foreign['id']}/group").status_code == 404
    assert (
        ctx.client.get(f"/v1/projects/{foreign['id']}/group/turns").status_code == 404
    )
    assert (
        ctx.client.post(
            f"/v1/projects/{foreign['id']}/group/turns", json={"content": "x"}
        ).status_code
        == 404
    )


def test_individual_conversation_not_readable_via_group_path(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db.client import require_client

    individual = (
        require_client()
        .table("conversations")
        .insert(
            {
                "company_id": ctx.company_id,
                "user_id": ctx.user_id,
                "project_id": project["id"],
                "kind": "individual",
            }
        )
        .execute()
        .data[0]
    )
    require_client().table("conversation_turns").insert(
        {"conversation_id": individual["id"], "role": "user", "content": "private stuff"}
    ).execute()

    # The group path refuses to read OR write a private conversation id,
    # even called directly (defense-in-depth beyond the route wiring, which
    # never lets a client supply a conversation_id at all).
    assert conversations_db.list_group_turns(individual["id"]) == []
    assert conversations_db.post_group_turn(individual["id"], ctx.user_id, "leak?") is None

    private_turns = (
        require_client()
        .table("conversation_turns")
        .select("content")
        .eq("conversation_id", individual["id"])
        .execute()
        .data
    )
    assert [t["content"] for t in private_turns] == ["private stuff"]


# Frozen baseline captured at HEAD 07abbe33 (the release/projects tip this
# ticket branched from) via `inspect.getsource(...)` — BEFORE any of this
# ticket's edits. A byte-for-byte match on both source and signature proves
# neither per-user helper was touched (grep-equivalent, but mutation-proof:
# a single reordered line or added default fails this, not just a renamed
# symbol).
_LOAD_HISTORY_SHA256 = (
    "8b97d01beef106845de7ac3ece56eccd9b925bbaec6fd26f4abd112c9eb964e6"
)
_CONVERSATION_BELONGS_TO_COMPANY_SHA256 = (
    "06eab9be7edc449a933eaa69aeb9915a60172db02a11f847b8c42f5e0ff1c2ba"
)


def test_private_path_helpers_unchanged():
    from app.db.conversations import conversation_belongs_to_company
    from app.routes.ask import _load_history

    load_history_src = inspect.getsource(_load_history)
    belongs_src = inspect.getsource(conversation_belongs_to_company)

    assert hashlib.sha256(load_history_src.encode()).hexdigest() == _LOAD_HISTORY_SHA256, (
        "app.routes.ask._load_history changed — the per-user history path "
        "must stay byte-for-byte untouched (AD-P2/R4)"
    )
    assert (
        hashlib.sha256(belongs_src.encode()).hexdigest()
        == _CONVERSATION_BELONGS_TO_COMPANY_SHA256
    ), (
        "app.db.conversations.conversation_belongs_to_company changed — the "
        "per-user ownership check must stay byte-for-byte untouched (AD-P2/R4)"
    )

    assert str(inspect.signature(_load_history)) == (
        "(conversation_id: int | None, company_id: str, user_id: str) -> list[dict]"
    )
    assert str(inspect.signature(conversation_belongs_to_company)) == (
        "(conversation_id: 'int', company_id: 'str') -> 'bool'"
    )


def test_teammate_individual_conversation_id_still_private(isolated_settings, monkeypatch):
    """The regression AC9 literally asks for: a teammate's individual
    `conversation_id` cannot replay another user's private turns through
    the UNTOUCHED per-user path — proven by calling `_load_history` (the
    exact function this ticket must not modify) as a second user."""
    from app.db.client import require_client
    from app.routes.ask import _load_history

    ctx = company_client(monkeypatch)
    owner_conv = (
        require_client()
        .table("conversations")
        .insert({"company_id": ctx.company_id, "user_id": ctx.user_id, "title": "Private"})
        .execute()
        .data[0]
    )
    require_client().table("conversation_turns").insert(
        {"conversation_id": owner_conv["id"], "role": "user", "content": "owner's secret"}
    ).execute()

    teammate_id = "teammate-" + ctx.user_id
    history_as_teammate = _load_history(owner_conv["id"], ctx.company_id, teammate_id)
    assert history_as_teammate == []

    history_as_owner = _load_history(owner_conv["id"], ctx.company_id, ctx.user_id)
    assert history_as_owner == [{"role": "user", "content": "owner's secret"}]


# ── Edge cases ───────────────────────────────────────────────────────────


def test_group_turns_empty_since_latest(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)
    t1 = conversations_db.post_group_turn(conv["id"], ctx.user_id, "only turn")

    turns = conversations_db.list_group_turns(conv["id"], since=t1["id"])
    assert turns == []

    # Route-level: polling a project with no group chat yet is `[]`, not 404.
    other_project = _create_project(ctx, name="No chat yet")
    r = ctx.client.get(f"/v1/projects/{other_project['id']}/group/turns")
    assert r.status_code == 200
    assert r.json()["turns"] == []


# ── Observability ────────────────────────────────────────────────────────


def test_group_chat_logs_carry_only_identifiers(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    with caplog.at_level(logging.INFO, logger="app.routes.projects"):
        r_create = ctx.client.post(f"/v1/projects/{project['id']}/group")
        conv_id = r_create.json()["id"]
        r_post = ctx.client.post(
            f"/v1/projects/{project['id']}/group/turns",
            json={"content": "a turn nobody should log verbatim"},
        )
        turn_id = r_post.json()["id"]

    lines = [rec.getMessage() for rec in caplog.records]
    assert any(
        f"group_chat_created project_id={project['id']} conversation_id={conv_id}" == line
        for line in lines
    )
    assert any(
        f"group_turn_posted project_id={project['id']} conversation_id={conv_id} "
        f"turn_id={turn_id}" == line
        for line in lines
    )
    assert not any("nobody should log verbatim" in line for line in lines)

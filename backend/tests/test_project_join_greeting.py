"""Tests for `app/project_join_greeting.py::post_join_greeting` — the
best-effort on-join greeting for a newly-added project member.

Fast lane: monkeypatches the module's own seams (`get_project`,
`memory_db.get_summary`, `conversations_db.create_individual_project_chat`/
`post_individual_turn`) so every test is deterministic and hits no real DB.
Proves the CONTRACT (new-only, best-effort/never-raises, REUSE not
resynthesize — no fresh LLM call — and the `<!--more-->` composition rules).

The real-DB round trip lives in `test_project_join_greeting_live.py`
([[feedback_stubbed-e2e-masks-loop-behaviour]]).
"""
from __future__ import annotations

import logging

import pytest

import app.project_join_greeting as greeting_mod
from tests._company_helpers import company_client


@pytest.fixture
def fake_greeting_deps(monkeypatch):
    """Patches every seam `post_join_greeting` calls through, so a test can
    drive each branch (posted/failed/no-summary) without a real DB."""
    state: dict = {
        "project": {"id": 1, "name": "Dark Mode Launch"},
        "summary": {"summary_md": None, "entry_count": 0, "stale": False},
        "conversation": {"id": 501},
        "posted_turns": [],
        "raise_on_create_chat": False,
        "raise_on_post_turn": False,
    }

    def _get_project(project_id):
        proj = state["project"]
        return dict(proj) if proj and proj["id"] == project_id else None

    def _get_summary(project_id):  # noqa: ARG001
        return dict(state["summary"])

    def _create_chat(project_id, user_id):  # noqa: ARG001
        if state["raise_on_create_chat"]:
            raise RuntimeError("simulated create-chat failure")
        return dict(state["conversation"])

    def _post_turn(conversation_id, role, content):
        if state["raise_on_post_turn"]:
            raise RuntimeError("simulated post-turn failure")
        turn = {
            "id": len(state["posted_turns"]) + 1,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
        }
        state["posted_turns"].append(turn)
        return turn

    monkeypatch.setattr(greeting_mod, "get_project", _get_project)
    monkeypatch.setattr(greeting_mod.memory_db, "get_summary", _get_summary)
    monkeypatch.setattr(greeting_mod.conversations_db, "create_individual_project_chat", _create_chat)
    monkeypatch.setattr(greeting_mod.conversations_db, "post_individual_turn", _post_turn)
    return state


# ── post_join_greeting: new-only, one turn, best-effort (AC-5) ─────────────


def test_new_membership_posts_one_assistant_turn(fake_greeting_deps):
    greeting_mod.post_join_greeting(1, "user-1")
    posted = fake_greeting_deps["posted_turns"]
    assert len(posted) == 1
    assert posted[0]["role"] == "assistant"
    assert posted[0]["conversation_id"] == 501
    assert posted[0]["content"].strip() != ""


def test_greeting_failure_never_raises(fake_greeting_deps, caplog):
    fake_greeting_deps["raise_on_post_turn"] = True
    with caplog.at_level(logging.WARNING, logger="app.project_join_greeting"):
        result = greeting_mod.post_join_greeting(1, "user-1")
    assert result is None  # must not raise
    assert any("join_greeting_failed" in r.getMessage() for r in caplog.records)


def test_greeting_no_project_never_raises(fake_greeting_deps, caplog):
    with caplog.at_level(logging.WARNING, logger="app.project_join_greeting"):
        result = greeting_mod.post_join_greeting(999, "user-1")
    assert result is None
    assert fake_greeting_deps["posted_turns"] == []
    assert any("join_greeting_no_project" in r.getMessage() for r in caplog.records)


def test_greeting_reuses_get_summary_no_llm(fake_greeting_deps, monkeypatch, repo_root):
    calls: list[int] = []

    def _spy_get_summary(project_id):
        calls.append(project_id)
        return {"summary_md": "Cached summary from an earlier synthesis run.", "entry_count": 1, "stale": False}

    monkeypatch.setattr(greeting_mod.memory_db, "get_summary", _spy_get_summary)
    greeting_mod.post_join_greeting(1, "user-1")
    assert calls == [1]

    src = (repo_root / "app" / "project_join_greeting.py").read_text()
    assert "call_json" not in src
    assert "from app.llm" not in src
    assert "anthropic" not in src.lower()


# ── add_member route integration (AC-5) ─────────────────────────────────────


def _seed_addable_member(ctx, project, *, user_id: str, email: str):
    from app.db.client import require_client
    from app.db.workspaces import upsert_workspace_member

    require_client().table("profiles").insert({"id": user_id, "email": email}).execute()
    require_client().table("company_members").insert(
        {"id": f"cm-{user_id}", "company_id": ctx.company_id, "user_id": user_id, "role": "member"}
    ).execute()
    upsert_workspace_member(project["workspace_id"], user_id, "member")


def test_add_member_still_adds_when_greeting_fails(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Growing team"}).json()
    _seed_addable_member(ctx, project, user_id="member-fail", email="member-fail@co.com")

    def _raise(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("simulated internal greeting failure")

    monkeypatch.setattr(greeting_mod.conversations_db, "create_individual_project_chat", _raise)

    r = ctx.client.post(f"/v1/projects/{project['id']}/members", json={"email": "member-fail@co.com"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "member-fail"

    from app.db.client import require_client

    rows = (
        require_client()
        .table("project_members")
        .select("user_id")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert {row["user_id"] for row in rows} == {ctx.user_id, "member-fail"}


def test_reused_membership_posts_no_greeting(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Growing team"}).json()
    _seed_addable_member(ctx, project, user_id="member-reuse", email="member-reuse@co.com")

    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(
        greeting_mod, "post_join_greeting", lambda pid, uid: calls.append((pid, uid))
    )

    r1 = ctx.client.post(f"/v1/projects/{project['id']}/members", json={"email": "member-reuse@co.com"})
    assert r1.status_code == 200
    assert len(calls) == 1

    # Re-add: TIER_MEMBER — idempotent echo, never reaches post_join_greeting.
    r2 = ctx.client.post(f"/v1/projects/{project['id']}/members", json={"email": "member-reuse@co.com"})
    assert r2.status_code == 200
    assert len(calls) == 1, "re-adding an existing member must not post a duplicate greeting"


# ── _compose_greeting / _split_lead composition (AC-6) ──────────────────────


def test_compose_greeting_more_marker_long_summary():
    long_summary = (
        "The team decided to launch dark mode behind a feature flag first. "
        "Rollout will be staged by workspace, starting with internal users. "
        "Design finished the token audit last week and handed off specs. "
        "Engineering is targeting a two-sprint build with QA embedded "
        "throughout, and marketing wants a heads-up before the first "
        "external cohort sees it so they can line up messaging. "
        "Support flagged a handful of tickets asking for it already. "
        "Analytics will track adoption per workspace once it ships. "
        "The rollout plan gets reviewed again at the next sync."
    )
    result = greeting_mod._compose_greeting("Dark Mode Launch", long_summary)
    assert greeting_mod.MORE_MARKER in result
    lead, _, rest = result.partition(greeting_mod.MORE_MARKER)
    assert lead.strip() != ""
    assert rest.strip() != ""
    assert "Dark Mode Launch" in result


def test_compose_greeting_no_marker_short_summary():
    short_summary = "The team shipped the first milestone last week."
    result = greeting_mod._compose_greeting("Dark Mode Launch", short_summary)
    assert greeting_mod.MORE_MARKER not in result
    assert short_summary in result


def test_compose_greeting_fallback_no_summary():
    result = greeting_mod._compose_greeting("Dark Mode Launch", None)
    assert greeting_mod.MORE_MARKER not in result
    assert "Dark Mode Launch" in result
    # Never fabricates a "why" or an assignment — just points at the group chat.
    assert "group chat" in result.lower()

    empty_result = greeting_mod._compose_greeting("Dark Mode Launch", "   ")
    assert greeting_mod.MORE_MARKER not in empty_result

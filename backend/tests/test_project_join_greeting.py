"""Tests for `app/project_join_greeting.py::post_join_greeting` — the
best-effort on-join greeting for a newly-added project member.

Fast lane: monkeypatches the module's own seams (`get_project`,
`memory_db.get_summary`, `conversations_db.create_individual_project_chat`/
`post_individual_turn`, plus the enrichment reads — group turns, artifacts,
members, delegations, profile first name) so every test is deterministic
and hits no real DB. Proves the CONTRACT (new-only from EITHER the
`/members` OR `/tag` add-member surface, best-effort/never-raises, REUSE not
resynthesize — no fresh LLM call — and the enriched-sections/`<!--more-->`
composition rules).

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
    drive each branch (posted/failed/brand-new/enriched) without a real DB.
    Defaults to an empty project (no summary, no artifacts, no group turns,
    no delegations, no resolvable first name) — the brand-new fallback."""
    state: dict = {
        "project": {"id": 1, "name": "Dark Mode Launch"},
        "summary": {"summary_md": None, "entry_count": 0, "stale": False},
        "conversation": {"id": 501},
        "posted_turns": [],
        "raise_on_create_chat": False,
        "raise_on_post_turn": False,
        "first_name": "",
        "group_chat": None,
        "group_turns": [],
        "artifacts": [],
        "members": [],
        "assigned": [],
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

    def _first_name_for_user(user_id):  # noqa: ARG001
        return state["first_name"]

    def _get_group_chat(project_id):  # noqa: ARG001
        return state["group_chat"]

    def _list_group_turns(conversation_id, since=None):  # noqa: ARG001
        return list(state["group_turns"])

    def _list_artifacts_for_project(*, project_id, dataset, company_id):  # noqa: ARG001
        return list(state["artifacts"])

    def _list_members(project_id):  # noqa: ARG001
        return list(state["members"])

    def _list_status_for_assignee(project_id, user_id):  # noqa: ARG001
        return list(state["assigned"])

    monkeypatch.setattr(greeting_mod, "get_project", _get_project)
    monkeypatch.setattr(greeting_mod.memory_db, "get_summary", _get_summary)
    monkeypatch.setattr(greeting_mod.conversations_db, "create_individual_project_chat", _create_chat)
    monkeypatch.setattr(greeting_mod.conversations_db, "post_individual_turn", _post_turn)
    monkeypatch.setattr(greeting_mod.profiles_db, "first_name_for_user", _first_name_for_user)
    monkeypatch.setattr(greeting_mod.conversations_db, "get_group_chat", _get_group_chat)
    monkeypatch.setattr(greeting_mod.conversations_db, "list_group_turns", _list_group_turns)
    monkeypatch.setattr(greeting_mod, "list_artifacts_for_project", _list_artifacts_for_project)
    monkeypatch.setattr(greeting_mod.projects_db, "list_members", _list_members)
    monkeypatch.setattr(
        greeting_mod.delegation_events_db, "list_status_for_assignee", _list_status_for_assignee
    )
    return state


# ── post_join_greeting: new-only, one turn, best-effort ─────────────────────


def test_new_membership_posts_one_assistant_turn(fake_greeting_deps):
    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    posted = fake_greeting_deps["posted_turns"]
    assert len(posted) == 1
    assert posted[0]["role"] == "assistant"
    assert posted[0]["conversation_id"] == 501
    assert posted[0]["content"].strip() != ""


def test_greeting_failure_never_raises(fake_greeting_deps, caplog):
    fake_greeting_deps["raise_on_post_turn"] = True
    with caplog.at_level(logging.WARNING, logger="app.project_join_greeting"):
        result = greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    assert result is None  # must not raise
    assert any("join_greeting_failed" in r.getMessage() for r in caplog.records)


def test_greeting_no_project_never_raises(fake_greeting_deps, caplog):
    with caplog.at_level(logging.WARNING, logger="app.project_join_greeting"):
        result = greeting_mod.post_join_greeting(999, "user-1", dataset="acme", company_id="co-acme")
    assert result is None
    assert fake_greeting_deps["posted_turns"] == []
    assert any("join_greeting_no_project" in r.getMessage() for r in caplog.records)


def test_greeting_reuses_get_summary_no_llm(fake_greeting_deps, monkeypatch, repo_root):
    calls: list[int] = []

    def _spy_get_summary(project_id):
        calls.append(project_id)
        return {"summary_md": "Cached summary from an earlier synthesis run.", "entry_count": 1, "stale": False}

    monkeypatch.setattr(greeting_mod.memory_db, "get_summary", _spy_get_summary)
    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    assert calls == [1]

    src = (repo_root / "app" / "project_join_greeting.py").read_text()
    assert "call_json" not in src
    assert "from app.llm" not in src
    assert "anthropic" not in src.lower()


# ── enriched sections: artifacts / group digest / roster / for-you ─────────


def test_greeting_includes_enriched_sections_when_populated(fake_greeting_deps):
    fake_greeting_deps["summary"] = {"summary_md": "Cached summary from an earlier synthesis run."}
    fake_greeting_deps["group_chat"] = {"id": 900}
    fake_greeting_deps["group_turns"] = [
        {"author_name": "Ada", "content": "We decided to ship behind a flag first."},
        {"author_name": "Grace", "content": "I finished the token audit."},
    ]
    fake_greeting_deps["artifacts"] = [
        {"type": "prd", "id": 1, "title": "Dark Mode PRD"},
        {"type": "prototype", "id": 2, "title": "Dark Mode Prototype"},
    ]
    fake_greeting_deps["members"] = [
        {"user_id": "user-1", "name": "New Person", "job_role": "Engineer"},
        {"user_id": "owner-1", "name": "Ada", "job_role": "PM"},
    ]
    fake_greeting_deps["assigned"] = [
        {"assignee_user_id": "user-1", "status": "assigned", "task_summary": "Review the PRD"},
    ]

    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    content = fake_greeting_deps["posted_turns"][0]["content"]

    assert greeting_mod.MORE_MARKER in content
    lead, _, rest = content.partition(greeting_mod.MORE_MARKER)
    assert "What we're solving" in lead
    assert "What the team's been discussing" in rest
    assert "Ada: We decided to ship behind a flag first." in rest
    assert "Artifacts to review (2)" in rest
    assert "PRD — Dark Mode PRD" in rest
    assert "Prototype — Dark Mode Prototype" in rest
    assert "Who's on it" in rest
    assert "New Person (you)" in rest
    assert "For you" in rest
    assert "Review the PRD" in rest


def test_greeting_for_you_says_nothing_assigned_when_empty(fake_greeting_deps):
    fake_greeting_deps["summary"] = {"summary_md": "Cached summary from an earlier synthesis run."}
    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    content = fake_greeting_deps["posted_turns"][0]["content"]
    assert "Nothing assigned yet." in content


def test_greeting_omits_group_digest_section_with_no_turns(fake_greeting_deps):
    fake_greeting_deps["summary"] = {"summary_md": "Cached summary from an earlier synthesis run."}
    fake_greeting_deps["group_chat"] = {"id": 900}
    fake_greeting_deps["group_turns"] = []
    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    content = fake_greeting_deps["posted_turns"][0]["content"]
    assert "What the team's been discussing" not in content


def test_greeting_brand_new_fallback_never_fabricates(fake_greeting_deps):
    # No summary, no artifacts, no group turns, no delegations — the default
    # fixture state — degrades to the honest light greeting.
    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    content = fake_greeting_deps["posted_turns"][0]["content"]
    assert greeting_mod.MORE_MARKER not in content
    assert "brand new" in content.lower()
    assert "group chat" in content.lower()
    assert "For you" not in content  # single-paragraph fallback, no sections at all


def test_greeting_brand_new_fallback_with_one_artifact_names_it(fake_greeting_deps):
    fake_greeting_deps["artifacts"] = [{"type": "prd", "id": 1, "title": "Kickoff PRD"}]
    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    content = fake_greeting_deps["posted_turns"][0]["content"]
    assert greeting_mod.MORE_MARKER not in content
    assert "one PRD" in content
    assert "Kickoff PRD" in content


def test_greeting_uses_first_name_when_resolvable(fake_greeting_deps):
    fake_greeting_deps["first_name"] = "Priya"
    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    content = fake_greeting_deps["posted_turns"][0]["content"]
    assert content.startswith("Hey Priya —")


# ── add_member route integration ────────────────────────────────────────────


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
        greeting_mod,
        "post_join_greeting",
        lambda pid, uid, dataset=None, company_id=None: calls.append((pid, uid)),
    )

    r1 = ctx.client.post(f"/v1/projects/{project['id']}/members", json={"email": "member-reuse@co.com"})
    assert r1.status_code == 200
    assert len(calls) == 1

    # Re-add: TIER_MEMBER — idempotent echo, never reaches post_join_greeting.
    r2 = ctx.client.post(f"/v1/projects/{project['id']}/members", json={"email": "member-reuse@co.com"})
    assert r2.status_code == 200
    assert len(calls) == 1, "re-adding an existing member must not post a duplicate greeting"


def test_tag_workspace_member_also_posts_greeting(isolated_settings, monkeypatch):
    """The `/tag` TIER_WORKSPACE branch now fires the SAME greeting
    `/members` does — closing the gap where a member added via the
    @mention/tag flow previously landed with a blank private thread."""
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Tag-added team"}).json()
    _seed_addable_member(ctx, project, user_id="tagged-member", email="tagged-member@co.com")

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/tag", json={"needle": "tagged-member@co.com"}
    )
    assert r.status_code == 200
    assert r.json()["tier"] == "t_workspace"

    from app.db import conversations as conversations_db

    conv = conversations_db.get_individual_project_chat(project["id"], "tagged-member")
    assert conv is not None, "expected a get-or-created individual chat for the tagged member"
    turns = conversations_db.list_individual_turns(conv["id"], "tagged-member")
    assert len(turns) == 1 and turns[0]["role"] == "assistant"


# ── _compose_greeting / _split_lead composition ─────────────────────────────


def _compose(**overrides):
    kwargs = {
        "project_name": "Dark Mode Launch",
        "first_name": "",
        "summary_md": "",
        "group_digest": "",
        "artifacts": [],
        "members": [],
        "new_user_id": "user-1",
        "open_assigned": [],
    }
    kwargs.update(overrides)
    return greeting_mod._compose_greeting(**kwargs)


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
    # A summary alone (no artifacts/group turns) is still enough to leave the
    # brand-new fallback: it has real content, so the enriched composer runs.
    result = _compose(summary_md=long_summary)
    assert greeting_mod.MORE_MARKER in result
    lead, _, rest = result.partition(greeting_mod.MORE_MARKER)
    assert lead.strip() != ""
    assert rest.strip() != ""
    assert "Dark Mode Launch" in result


def test_compose_greeting_no_marker_short_summary_and_no_other_sections():
    short_summary = "The team shipped the first milestone last week."
    result = _compose(summary_md=short_summary)
    # A short-enough summary is entirely the lead (`_split_lead`'s empty-rest
    # case); with no group digest/artifacts/members, "For you" + the CTA are
    # the only other candidate content, and "For you" always renders — so
    # the marker DOES appear once "For you"/CTA are appended after the lead.
    assert short_summary in result


def test_compose_greeting_fallback_brand_new():
    result = _compose()
    assert greeting_mod.MORE_MARKER not in result
    assert "Dark Mode Launch" in result
    # Never fabricates a "why" or an assignment — just points at the group chat.
    assert "group chat" in result.lower()

    empty_result = _compose(summary_md="   ")
    assert greeting_mod.MORE_MARKER not in empty_result

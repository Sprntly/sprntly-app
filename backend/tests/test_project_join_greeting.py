"""Tests for `app/project_join_greeting.py::post_join_greeting` — the
best-effort on-join greeting for a newly-added project member.

Fast lane: monkeypatches the module's own seams (`get_project`,
`memory_db.get_summary`, `conversations_db.create_individual_project_chat`/
`post_individual_turn`, the enrichment reads — artifacts, members,
delegations, profile first name — and the module's single narrative LLM
call, `call_md`) so every test is deterministic and hits no real DB/network.
Proves the CONTRACT (new-only from EITHER the `/members` OR `/tag`
add-member surface, best-effort/never-raises, REUSE-not-resynthesize for the
memory summary, the narrative-pass/`<!--more-->` composition contract). The
group-chat helpers this path once had to avoid calling (`get_group_chat`/
`list_group_turns`) are now deleted outright — see
`test_group_removal_regression.py::test_group_modules_and_helpers_absent`.

The real-DB/real-LLM round trip lives in `test_project_join_greeting_live.py`.
"""
from __future__ import annotations

import inspect
import logging
import py_compile

import pytest

import app.project_join_greeting as greeting_mod
from tests._company_helpers import company_client


@pytest.fixture
def fake_greeting_deps(monkeypatch):
    """Patches every seam `post_join_greeting` calls through, so a test can
    drive each branch (posted/failed/enriched/fallback) without a real DB or
    a real model. Defaults to an empty project (no summary, no
    artifacts, no members, no delegations, no resolvable first name)."""
    state: dict = {
        "project": {"id": 1, "name": "Dark Mode Launch"},
        "summary": {"summary_md": None, "entry_count": 0, "stale": False},
        "conversation": {"id": 501},
        "posted_turns": [],
        "raise_on_create_chat": False,
        "raise_on_post_turn": False,
        "first_name": "",
        "artifacts": [],
        "members": [],
        "assigned": [],
        "llm_body": "Hey there — welcome to Dark Mode Launch.<!--more-->More detail here.",
        "raise_on_llm": False,
        "llm_calls": [],
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

    def _list_artifacts_for_project(*, project_id, dataset, company_id):  # noqa: ARG001
        return list(state["artifacts"])

    def _list_members(project_id):  # noqa: ARG001
        return list(state["members"])

    def _list_status_for_assignee(project_id, user_id):  # noqa: ARG001
        return list(state["assigned"])

    def _call_md(*, system, user, model, meta_out=None, **kwargs):  # noqa: ARG001
        state["llm_calls"].append({"system": system, "user": user, "model": model})
        if state["raise_on_llm"]:
            raise RuntimeError("simulated greeting LLM failure")
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model,
                    "input_tokens": 20,
                    "output_tokens": 15,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            )
        return state["llm_body"]

    monkeypatch.setattr(greeting_mod, "get_project", _get_project)
    monkeypatch.setattr(greeting_mod.memory_db, "get_summary", _get_summary)
    monkeypatch.setattr(greeting_mod.conversations_db, "create_individual_project_chat", _create_chat)
    monkeypatch.setattr(greeting_mod.conversations_db, "post_individual_turn", _post_turn)
    monkeypatch.setattr(greeting_mod.profiles_db, "first_name_for_user", _first_name_for_user)
    monkeypatch.setattr(greeting_mod, "list_artifacts_for_project", _list_artifacts_for_project)
    monkeypatch.setattr(greeting_mod.projects_db, "list_members", _list_members)
    monkeypatch.setattr(
        greeting_mod.delegation_events_db, "list_status_for_assignee", _list_status_for_assignee
    )
    monkeypatch.setattr(greeting_mod, "call_md", _call_md)
    return state


# ── post_join_greeting: new-only, one turn ──────────────────────────────────


def test_greeting_posts_single_assistant_individual_turn(fake_greeting_deps):
    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    posted = fake_greeting_deps["posted_turns"]
    assert len(posted) == 1
    assert posted[0]["role"] == "assistant"
    assert posted[0]["conversation_id"] == 501
    assert posted[0]["content"].strip() != ""


# ── Narrative-LLM-pass composition ──────────────────────────────────────────


def test_greeting_composes_item5_sections_with_marker(fake_greeting_deps):
    fake_greeting_deps["first_name"] = "Priya"
    fake_greeting_deps["llm_body"] = (
        "Hey Priya — welcome to Dark Mode Launch. The team is building a "
        "dark mode toggle for mobile settings.<!--more-->What's been done: "
        "the token audit is complete. Artifacts: PRD — Dark Mode PRD. "
        "Members: Ada (PM). Open questions: none flagged yet. Your items: "
        "you're assigned to review the PRD. Ask me anything about the "
        "project."
    )
    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    content = fake_greeting_deps["posted_turns"][0]["content"]

    assert content == fake_greeting_deps["llm_body"], "the stubbed LLM body is persisted as-is"
    assert greeting_mod.MORE_MARKER in content
    lead, _, rest = content.partition(greeting_mod.MORE_MARKER)
    assert lead.strip() != ""
    assert rest.strip() != ""
    assert "welcome to Dark Mode Launch" in lead
    assert "you're assigned to review the PRD" in rest


def test_greeting_references_assigned_delegation(fake_greeting_deps):
    """The member's own open delegations (`list_status_for_assignee`,
    `OPEN_STATES`-filtered) reach the LLM prompt — AC2c."""
    fake_greeting_deps["assigned"] = [
        {"assignee_user_id": "user-1", "status": "assigned", "task_summary": "Review the PRD"},
        {"assignee_user_id": "user-1", "status": "completed", "task_summary": "Old, closed item"},
    ]
    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    user_prompt = fake_greeting_deps["llm_calls"][0]["user"]
    assert "Review the PRD" in user_prompt
    assert "Old, closed item" not in user_prompt, "OPEN_STATES filter must exclude a completed item"


def test_greeting_prompt_includes_artifacts_and_members(fake_greeting_deps):
    fake_greeting_deps["artifacts"] = [{"type": "prd", "id": 1, "title": "Kickoff PRD", "status": "ready"}]
    fake_greeting_deps["members"] = [{"user_id": "owner-1", "name": "Ada", "job_role": "PM"}]
    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    user_prompt = fake_greeting_deps["llm_calls"][0]["user"]
    assert "Kickoff PRD" in user_prompt
    assert "Ada" in user_prompt


def test_greeting_calls_llm_exactly_once(fake_greeting_deps):
    """One LLM call per greeting, on member-add only — never per chat-open
    (the plan's explicit cost posture)."""
    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    assert len(fake_greeting_deps["llm_calls"]) == 1
    assert fake_greeting_deps["llm_calls"][0]["model"] == greeting_mod.DEFAULT_MODEL


def test_greeting_reuses_cached_summary_no_fresh_synthesis(fake_greeting_deps, monkeypatch):
    """`memory_db.get_summary` is a READ of the already-cached summary —
    the greeting must never trigger a fresh memory-SYNTHESIS run
    (`project_memory.regenerate_summary`) of its own."""
    calls: list[int] = []

    def _spy_get_summary(project_id):
        calls.append(project_id)
        return {"summary_md": "Cached summary from an earlier synthesis run.", "entry_count": 1}

    monkeypatch.setattr(greeting_mod.memory_db, "get_summary", _spy_get_summary)

    import app.project_memory as memory_mod

    def _boom_regen(*args, **kwargs):
        raise AssertionError("the greeting must never trigger a fresh summary regen")

    monkeypatch.setattr(memory_mod, "regenerate_summary", _boom_regen)

    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    assert calls == [1]


# ── Resilience: LLM failure / any-seam failure ──────────────────────────────


def test_greeting_llm_failure_falls_back_and_greets(fake_greeting_deps):
    fake_greeting_deps["raise_on_llm"] = True
    fake_greeting_deps["artifacts"] = [{"type": "prd", "id": 1, "title": "Kickoff PRD"}]
    result = greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    assert result is None  # must not raise
    posted = fake_greeting_deps["posted_turns"]
    assert len(posted) == 1
    assert posted[0]["role"] == "assistant"
    assert "Kickoff PRD" in posted[0]["content"]


def test_greeting_llm_empty_response_falls_back(fake_greeting_deps):
    fake_greeting_deps["llm_body"] = "   "  # blank after strip
    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    posted = fake_greeting_deps["posted_turns"]
    assert len(posted) == 1
    assert posted[0]["content"].strip() != ""


def test_greeting_never_raises_on_any_seam_failure(fake_greeting_deps, monkeypatch):
    fake_greeting_deps["raise_on_create_chat"] = True
    result = greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    assert result is None

    def _boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("simulated seam failure")

    for mod, attr in (
        (greeting_mod.projects_db, "list_members"),
        (greeting_mod.delegation_events_db, "list_status_for_assignee"),
        (greeting_mod, "list_artifacts_for_project"),
    ):
        monkeypatch.setattr(mod, attr, _boom)
        result = greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
        assert result is None


def test_greeting_failure_logs_warning(fake_greeting_deps, caplog):
    fake_greeting_deps["raise_on_post_turn"] = True
    with caplog.at_level(logging.WARNING, logger="app.project_join_greeting"):
        result = greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    assert result is None
    assert any("join_greeting_failed" in r.getMessage() for r in caplog.records)


def test_greeting_no_project_never_raises(fake_greeting_deps, caplog):
    with caplog.at_level(logging.WARNING, logger="app.project_join_greeting"):
        result = greeting_mod.post_join_greeting(999, "user-1", dataset="acme", company_id="co-acme")
    assert result is None
    assert fake_greeting_deps["posted_turns"] == []
    assert any("join_greeting_no_project" in r.getMessage() for r in caplog.records)


def test_greeting_uses_first_name_when_resolvable(fake_greeting_deps):
    fake_greeting_deps["first_name"] = "Priya"
    greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    user_prompt = fake_greeting_deps["llm_calls"][0]["user"]
    assert "Priya" in user_prompt


# ── Signature + callers (AC4) ───────────────────────────────────────────────


def test_greeting_signature_and_callers_stable(repo_root):
    sig = inspect.signature(greeting_mod.post_join_greeting)
    assert list(sig.parameters) == ["project_id", "user_id", "dataset", "company_id"]

    routes_src = (repo_root / "app" / "routes" / "projects.py").read_text()
    # The member-add post-insert side effects (this call among them) are now
    # DEFERRED off the request thread (see `_dispatch_member_add_side_
    # effects`/`_run_member_add_side_effects`) — both surfaces that grow the
    # roster (`add_member`'s new-membership branch, `tag_candidate_route`'s
    # TIER_WORKSPACE branch) route through the ONE shared dispatcher rather
    # than each calling `post_join_greeting` directly, so the literal call
    # site count dropped from 2 to 1. The real "two callers" invariant this
    # test originally pinned now lives in the dispatcher-call count instead.
    assert routes_src.count("project_join_greeting.post_join_greeting(") == 1
    # 3 total occurrences of the identifier: the `def`, plus the two call
    # sites (`add_member`, `tag_candidate_route`) — isolate the CALLS.
    assert routes_src.count("_dispatch_member_add_side_effects(") == 3
    assert routes_src.count("def _dispatch_member_add_side_effects(") == 1

    team_src = (repo_root / "app" / "db" / "team.py").read_text()
    assert "post_join_greeting(pid, user_id, dataset=dataset, company_id=company_id)" in team_src

    for rel in ("app/project_join_greeting.py", "app/routes/projects.py", "app/db/team.py"):
        py_compile.compile(str(repo_root / rel), doraise=True)


# ── No-schema guard (AC12/AC14, PI5) ────────────────────────────────────────


def test_no_bracketed_internal_reference_in_greeting_file(repo_root):
    """AC14: no stray bracketed internal cross-reference remains in the
    greeting file's docstring — the pre-existing bracketed reference is
    stripped while the docstring was rewritten for the LLM-pass change."""
    src = (repo_root / "app" / "project_join_greeting.py").read_text()
    assert "[[" not in src


def test_no_migration_or_column_in_diff(repo_root):
    """AC12: static guard — every file this ticket touches adds no
    migration, DDL, or new column. Every memory write goes through the
    existing `add_agent_promoted_entry` + `schedule_regen` flow."""
    import re as re_mod

    touched = (
        "app/project_join_greeting.py",
        "app/project_origin_seed.py",
        "app/project_from_prd.py",
        "app/project_memory.py",
        "app/routes/projects.py",
    )
    pattern = re_mod.compile(r"migrations/|ALTER |CREATE TABLE|DROP |ADD COLUMN")
    for rel in touched:
        src = (repo_root / rel).read_text()
        assert not pattern.search(src), f"{rel} must contain no migration/DDL text"


# ── Observability — identifiers only (AC11) ─────────────────────────────────


def test_greeting_log_line_has_no_pii(fake_greeting_deps, caplog):
    fake_greeting_deps["first_name"] = "Priya"
    fake_greeting_deps["llm_body"] = "SECRET_GREETING_BODY_DO_NOT_LOG<!--more-->rest of the secret body"
    fake_greeting_deps["members"] = [
        {"user_id": "owner-1", "name": "SECRET_MEMBER_NAME_DO_NOT_LOG", "job_role": "PM"}
    ]

    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")

    cost_lines = [
        r.getMessage() for r in caplog.records if "projects.greeting.compose" in r.getMessage()
    ]
    assert len(cost_lines) == 1
    assert "project_id=1" in cost_lines[0]
    assert "user_id=user-1" in cost_lines[0]
    assert "status=complete" in cost_lines[0]

    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRET_GREETING_BODY_DO_NOT_LOG" not in joined
    assert "SECRET_MEMBER_NAME_DO_NOT_LOG" not in joined
    assert "Priya" not in joined


def test_greeting_log_line_marks_fallback_status(fake_greeting_deps, caplog):
    fake_greeting_deps["raise_on_llm"] = True
    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        greeting_mod.post_join_greeting(1, "user-1", dataset="acme", company_id="co-acme")
    cost_lines = [
        r.getMessage() for r in caplog.records if "projects.greeting.compose" in r.getMessage()
    ]
    assert len(cost_lines) == 1
    assert "status=fallback" in cost_lines[0]
    assert "error_class=RuntimeError" in cost_lines[0]


# ── _ensure_marker / _fallback_greeting unit behaviour ──────────────────────


def test_ensure_marker_leaves_present_marker_untouched():
    body = f"Lead sentence.{greeting_mod.MORE_MARKER}Rest of the body."
    assert greeting_mod._ensure_marker(body) == body


def test_ensure_marker_inserts_when_model_omits_it():
    body = "A short sentence about the project. " * 30
    result = greeting_mod._ensure_marker(body)
    assert greeting_mod.MORE_MARKER in result


def test_ensure_marker_leaves_very_short_body_unmarked():
    body = "Hey — welcome."
    result = greeting_mod._ensure_marker(body)
    assert greeting_mod.MORE_MARKER not in result
    assert result == body


def test_fallback_greeting_never_fabricates():
    result = greeting_mod._fallback_greeting("", "Dark Mode Launch", [])
    assert greeting_mod.MORE_MARKER not in result
    assert "Dark Mode Launch" in result
    assert "nothing" in result.lower()
    assert "group chat" not in result.lower()


def test_fallback_greeting_names_one_artifact():
    result = greeting_mod._fallback_greeting(
        "Priya", "Dark Mode Launch", [{"type": "prd", "title": "Kickoff PRD"}]
    )
    assert result.startswith("Hey Priya —")
    assert "Kickoff PRD" in result


# ── Prompt property (content + negative-space) ──────────────────────────────


def test_greeting_system_prompt_properties():
    system = greeting_mod._GREETING_SYSTEM
    lowered = system.lower()
    assert system.strip() != ""
    assert greeting_mod.MORE_MARKER in system
    assert "grounded strictly" in lowered
    assert "never invent" in lowered
    assert "assigned" in lowered
    assert "group chat" not in lowered

    # Negative-space: a weak prompt lacking these rules must NOT satisfy the
    # check — proves the assertions aren't vacuous.
    weak_prompt = "Write a friendly welcome message for a new teammate."
    assert "grounded strictly" not in weak_prompt.lower()
    assert "never invent" not in weak_prompt.lower()


# ── add_member/tag route integration ────────────────────────────────────────


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
    """The `/tag` TIER_WORKSPACE branch fires the SAME greeting `/members`
    does — closing the gap where a member added via the @mention/tag flow
    previously landed with a blank private thread. Drives the real route
    end to end; the module-level autouse LLM stub (`conftest.py`'s
    `_no_background_greeting_synthesis`) keeps this off the real network."""
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

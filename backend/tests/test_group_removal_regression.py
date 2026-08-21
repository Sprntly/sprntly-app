"""Retirement / closed-world regression suite for the backend group-chat
removal.

Proves the closed-world invariants a future PR could silently regress:
`Surface` is a two-member enum, the three `/group*` routes 404, the
group-only DB helpers/modules are gone, the naming-trap module
(`project_group_context.py`) survives with its private/shared exports
intact, no group-row INSERT path remains, and the diff carries no
migration/schema change. Private + main non-regression coverage (delegation
forcing, context-fold, byte-identity) lives alongside the modules it
exercises — see `test_project_answer_collapse.py`.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from tests._company_helpers import company_client

_BACKEND_APP = Path(__file__).resolve().parent.parent / "app"
_REPO_ROOT = Path(__file__).resolve().parents[2]

# AC9's closed-world grep. The bare module-path substring `project_group_
# context` (the explicitly-KEPT naming-trap module, AC4) is excluded via the
# negative lookahead on the first alternative — every import of it elsewhere
# in the tree (`from app.project_group_context import ...`) is expected and
# harmless; `Surface.project_group` (a real removed symbol) still matches
# because it is never followed by `_context`.
_AC9_PATTERN = re.compile(
    r"project_group(?!_context)|group_chat|list_group_turns|create_group_chat|"
    r"post_group_turn|_GROUP_SCOPE_SYSTEM|_GROUP_HISTORY_TURNS|"
    r"_load_group_history|multi_party|prerendered_transcript|"
    r"assemble_group_agent_context|publish_group_turn_created|has_group_chat"
)

# AC6's closed-world grep — group-row INSERT paths gone.
_AC6_PATTERN = re.compile(r"kind=.group.|create_group_chat|post_group_turn")

# AC5's static no-migration guard. Scoped to files this ticket TOUCHED (not
# the whole backend/app tree, which legitimately contains unrelated
# CREATE-TABLE-shaped comments/code, e.g. `db/schema.py`'s legacy
# runtime-init helper).
_MIGRATION_PATTERN = re.compile(
    r"DROP TABLE|ALTER TABLE|DELETE FROM\s+(conversations|conversation_turns|"
    r"project_chat_members)|CREATE TABLE",
    re.IGNORECASE,
)

# Every backend module this ticket touched (relative to backend/app/).
_TOUCHED_FILES = [
    "surface_scope.py",
    "qa_agent.py",
    "routes/ask.py",
    "ask_job_runner.py",
    "context_assembler_project.py",
    "project_group_context.py",
    "db/conversations.py",
    "db/projects.py",
    "db/conversation_read_cursors.py",
    "routes/projects.py",
    "project_task_execution.py",
    "skill_router.py",
    "realtime.py",
    "db/delegation_events.py",
    "goal_report_chat_edit.py",
]


def _all_app_py_files() -> list[Path]:
    return sorted(_BACKEND_APP.rglob("*.py"))


def test_surface_enum_has_only_main_and_private():
    """AC2. `Surface` is exactly {main, project_private}."""
    from app.surface_scope import Surface

    assert {m.value for m in Surface} == {"main", "project_private"}
    assert getattr(Surface, "project_group", None) is None
    with pytest.raises(AttributeError):
        Surface.project_group  # noqa: B018 — the assertion IS the attribute access


def test_group_routes_return_404(isolated_settings, monkeypatch):
    """AC1. The three `/group*` routes are gone — no route registers a
    `/group` path under the projects router."""
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Route-gone project"}).json()
    project_id = project["id"]

    assert ctx.client.post(f"/v1/projects/{project_id}/group").status_code == 404
    assert ctx.client.get(f"/v1/projects/{project_id}/group/turns").status_code == 404
    assert (
        ctx.client.post(f"/v1/projects/{project_id}/group/turns", json={"content": "hi"}).status_code
        == 404
    )

    from app.routes.projects import router

    for route in router.routes:
        path = getattr(route, "path", "")
        assert "/group" not in path, f"a route still registers a /group path: {path}"


def test_group_modules_and_helpers_absent():
    """AC3. `project_group_realtime` is unimportable; the 10 `db/conversations`
    group helpers + `db/projects.get_group_chat_id`/`has_group_chat` are
    gone."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.project_group_realtime")

    from app.db import conversations as conversations_db
    from app.db import projects as projects_db

    removed_conversation_helpers = (
        "get_group_chat",
        "create_group_chat",
        "post_group_turn",
        "list_group_turns",
        "_seed_roster_from_members",
        "_author_display",
        "_attach_group_run_status",
        "user_in_group_roster",
        "get_group_turn_attachments",
        "set_group_turn_trigger_kind",
    )
    for name in removed_conversation_helpers:
        assert not hasattr(conversations_db, name), (
            f"db.conversations.{name} should have been deleted"
        )

    assert not hasattr(projects_db, "get_group_chat_id")


def test_has_group_chat_dto_field_absent(isolated_settings, monkeypatch):
    """AC3. `list_projects_for_workspace` (the `GET /v1/projects` DTO) no
    longer emits `has_group_chat`."""
    ctx = company_client(monkeypatch)
    ctx.client.post("/v1/projects", json={"name": "No has_group_chat field"})

    rows = ctx.client.get("/v1/projects").json()["projects"]
    assert rows
    for row in rows:
        assert "has_group_chat" not in row


def test_naming_trap_file_private_exports_survive():
    """AC4. `project_group_context.py` survives with its private/shared
    exports intact; the group-only exports are gone."""
    import app.project_group_context as pgc

    for name in (
        "assemble_private_project_context",
        "assemble_project_fact_core",
        "read_tools",
        "dispatch_read_tool",
    ):
        assert hasattr(pgc, name), f"project_group_context.{name} must survive"

    assert not hasattr(pgc, "assemble_group_agent_context")
    assert not hasattr(pgc, "EDIT_PRD_TOOL")

    import py_compile

    py_compile.compile(str(pgc.__file__), doraise=True)


def test_no_group_symbols_in_backend_tree():
    """AC9. Closed-world grep over `backend/app` product files — zero hits
    (the kept naming-trap module's own import path is excluded by
    `_AC9_PATTERN` itself, see its docstring above)."""
    hits: list[str] = []
    for path in _all_app_py_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _AC9_PATTERN.search(line):
                hits.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not hits, "forbidden group symbol(s) found:\n" + "\n".join(hits)


def test_group_insert_paths_removed():
    """AC6. No code path inserts a `conversations` row with `kind='group'`
    or writes a group `conversation_turns` row."""
    hits: list[str] = []
    for path in _all_app_py_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _AC6_PATTERN.search(line):
                hits.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not hits, "group-row INSERT path(s) found:\n" + "\n".join(hits)


def test_no_migration_or_schema_change_in_diff():
    """AC5, static guard (the ship-gate re-runs the live equivalent, and
    reconciles this against the actual diff — this working-tree-only guard
    cannot see history, per PI: no historical `git show` in CI tests). None
    of the backend files this ticket TOUCHED contains a DROP/ALTER/CREATE
    TABLE or a DELETE FROM the three group-adjacent tables — the DB stays
    untouched, only the code paths that wrote to it are removed. Scoped to
    touched files only — the wider `backend/app` tree legitimately contains
    unrelated CREATE-TABLE-shaped text elsewhere (e.g. `db/schema.py`), and
    `supabase/migrations/` legitimately contains HISTORICAL `ALTER TABLE
    conversations` statements pre-dating this ticket by unrelated features."""
    hits: list[str] = []
    for rel in _TOUCHED_FILES:
        path = _BACKEND_APP / rel
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _MIGRATION_PATTERN.search(line):
                hits.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not hits, "schema-change-shaped line(s) found in touched files:\n" + "\n".join(hits)


def test_touched_backend_files_py_compile():
    """Blast-radius compile guard: every backend module this ticket touched
    `py_compile`s clean."""
    import py_compile

    for rel in _TOUCHED_FILES:
        path = _BACKEND_APP / rel
        assert path.exists(), f"expected touched file missing: {rel}"
        py_compile.compile(str(path), doraise=True)

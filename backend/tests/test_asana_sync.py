"""Asana ticket sync — the Asana-specific pieces of the two-way engine:
get_task normalization (section = status), the _Tracker asana branch's
section-move status write (+ completed toggle), and push_stories_to_asana
idempotency. Mirrors the ClickUp/Jira coverage for the parts that differ.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.stories.generate import Story

CID = "11111111-2222-3333-4444-555555555555"
PROJ = "PROJ_GID"


# ── asana_oauth.get_task normalization (section → status, completed) ─────────


def test_get_task_normalizes_section_as_status():
    from app.connectors import asana_oauth

    payload = {"data": {
        "name": "Login flow", "notes": "As a user…", "completed": True,
        "permalink_url": "https://app.asana.com/0/1/2",
        "modified_at": "2026-07-15T10:00:00.000Z",
        "assignee": {"name": "Ada", "email": "ada@x.com"},
        "memberships": [
            {"project": {"gid": PROJ}, "section": {"gid": "sec2", "name": "Done"}},
        ],
    }}
    resp = MagicMock(status_code=200, ok=True)
    resp.json.return_value = payload
    with patch("app.connectors.asana_oauth.requests.get", return_value=resp):
        state = asana_oauth.get_task("tok", "task1", project_gid=PROJ)
    assert state["status"] == "Done"          # section name IS the status
    assert state["section_gid"] == "sec2"
    assert state["completed"] is True
    assert state["title"] == "Login flow"
    assert state["description"] == "As a user…"
    assert state["assignee"] == "Ada"
    assert state["url"] == "https://app.asana.com/0/1/2"
    assert state["priority"] is None and state["issue_type"] is None


def test_get_task_returns_empty_on_failure():
    from app.connectors import asana_oauth

    resp = MagicMock(status_code=500, ok=False, text="boom")
    with patch("app.connectors.asana_oauth.requests.get", return_value=resp):
        assert asana_oauth.get_task("tok", "task1", project_gid=PROJ) == {}


def test_get_task_isolates_auth_error_per_task():
    """A 401/403 on ONE task returns {} (skip this ticket), never raises —
    one forbidden/expired task must not abort the whole PRD sync pass
    (parity with clickup_oauth.get_task)."""
    from app.connectors import asana_oauth

    resp = MagicMock(status_code=401, ok=False, text="unauthorized")
    with patch("app.connectors.asana_oauth.requests.get", return_value=resp):
        assert asana_oauth.get_task("tok", "task1", project_gid=PROJ) == {}


def test_list_projects_spans_all_workspaces():
    """A user in several Asana workspaces can pick a project in ANY of them —
    list_projects unions projects across every visible workspace."""
    from app.connectors import asana_oauth

    def _get(_tok, path, params=None):
        if path == "/workspaces":
            return [{"gid": "ws1"}, {"gid": "ws2"}]
        assert path == "/projects"
        return {"ws1": [{"gid": "p1", "name": "Alpha"}],
                "ws2": [{"gid": "p2", "name": "Beta"}]}[params["workspace"]]

    with patch("app.connectors.asana_oauth._get", side_effect=_get):
        projects = asana_oauth.list_projects("tok")
    assert projects == [
        {"gid": "p1", "name": "Alpha"},
        {"gid": "p2", "name": "Beta"},
    ]


# ── _Tracker asana branch: status write = move to section (+ completed) ──────


def _asana_tracker(meta):
    """Build a _Tracker('asana', …) with creds + cached meta stubbed."""
    from app.stories import sync as sync_mod

    with patch.object(sync_mod, "_asana_creds", return_value="tok"), \
         patch("app.db.tracker_meta.get_cached_meta", return_value=meta):
        return sync_mod._Tracker("asana", CID, PROJ)


_META = {
    "provider": "asana", "destination_id": PROJ,
    "statuses": [
        {"id": "sec_todo", "name": "To Do", "color": None, "category": "open"},
        {"id": "sec_prog", "name": "In Progress", "color": None, "category": "in_progress"},
        {"id": "sec_done", "name": "Done", "color": None, "category": "done"},
    ],
    "priorities": [], "issue_types": None, "fields": [],
}


def test_set_status_moves_to_section_and_completes_on_done():
    from app.connectors import asana_oauth

    tracker = _asana_tracker(_META)
    with patch.object(asana_oauth, "add_task_to_section") as add, \
         patch.object(asana_oauth, "update_task") as upd:
        ok = tracker.set_status("t1", "Done")
    assert ok is True
    add.assert_called_once_with("tok", "sec_done", "t1")
    # A done-category section also marks the task completed.
    upd.assert_called_once_with("tok", "t1", completed=True)


def test_set_status_moves_to_section_and_uncompletes_on_non_done():
    from app.connectors import asana_oauth

    tracker = _asana_tracker(_META)
    with patch.object(asana_oauth, "add_task_to_section") as add, \
         patch.object(asana_oauth, "update_task") as upd:
        ok = tracker.set_status("t1", "In Progress")
    assert ok is True
    add.assert_called_once_with("tok", "sec_prog", "t1")
    upd.assert_called_once_with("tok", "t1", completed=False)


def test_set_status_unknown_section_is_noop_false():
    """A status that isn't one of the project's sections can't be placed →
    best-effort False, no API calls (mirrors an unknown ClickUp/Jira status)."""
    from app.connectors import asana_oauth

    tracker = _asana_tracker(_META)
    with patch.object(asana_oauth, "add_task_to_section") as add, \
         patch.object(asana_oauth, "update_task") as upd:
        ok = tracker.set_status("t1", "Nonexistent Column")
    assert ok is False
    add.assert_not_called()
    upd.assert_not_called()


def test_set_status_without_meta_is_noop_false():
    """No cached meta → no sections known → status write is a no-op (Asana has
    no fixed vocabulary to fall back to)."""
    from app.connectors import asana_oauth

    tracker = _asana_tracker(None)
    with patch.object(asana_oauth, "add_task_to_section") as add:
        assert tracker.set_status("t1", "Done") is False
    add.assert_not_called()


# ── push_stories_to_asana: create + idempotent update by mapping ─────────────


def test_push_creates_then_updates_by_mapping():
    from app.stories import push as push_mod

    story = Story(title="Login", body="Body")
    tid = story.stable_id()

    with patch.object(push_mod, "_asana_creds", return_value="tok"), \
         patch.object(push_mod, "get_asana_task_gid", return_value=None) as getmap, \
         patch.object(push_mod, "save_asana_task_gid") as savemap, \
         patch("app.connectors.asana_oauth.create_task",
               return_value={"gid": "T1", "url": "u"}) as create, \
         patch("app.connectors.asana_oauth.update_task") as update:
        r1 = push_mod.push_stories_to_asana(CID, PROJ, [story])
    assert r1["created"][0]["task_id"] == "T1"
    assert r1["created"][0]["updated"] is False
    create.assert_called_once()
    update.assert_not_called()
    savemap.assert_called_once_with(CID, PROJ, tid, "T1")
    getmap.assert_called_once_with(CID, PROJ, tid)

    # Second push: an existing mapping → UPDATE, never a duplicate create.
    with patch.object(push_mod, "_asana_creds", return_value="tok"), \
         patch.object(push_mod, "get_asana_task_gid", return_value="T1"), \
         patch.object(push_mod, "save_asana_task_gid") as savemap2, \
         patch("app.connectors.asana_oauth.create_task") as create2, \
         patch("app.connectors.asana_oauth.update_task",
               return_value={"gid": "T1", "url": "u"}) as update2:
        r2 = push_mod.push_stories_to_asana(CID, PROJ, [story])
    assert r2["created"][0]["updated"] is True
    create2.assert_not_called()
    update2.assert_called_once()
    savemap2.assert_not_called()


# ── Child issues → real native Asana subtasks (add-only, idempotent) ─────────


def test_push_asana_subtasks_creates_each_missing_child_stripping_marker():
    """Every non-blank child becomes a real subtask; the '[P]' parallel marker
    is stripped and blank entries are skipped."""
    from app.stories import push as push_mod

    created: list[str] = []

    def _create(_tok, _parent, *, name):
        created.append(name)
        return {"gid": f"S{len(created)}"}

    with patch.object(push_mod, "get_asana_task_gid", return_value=None), \
         patch.object(push_mod, "save_asana_task_gid") as save, \
         patch("app.connectors.asana_oauth.create_subtask", side_effect=_create):
        push_mod.push_asana_subtasks(
            CID, PROJ, "PARENT", "tid",
            ["Design API", "[P] Write tests", "   "], access_token="tok",
        )
    assert created == ["Design API", "Write tests"]
    assert save.call_count == 2


def test_push_asana_subtasks_skips_already_created():
    """A subtask already mapped (created on a prior pass) is not re-created —
    add-only idempotency by content hash (mirrors the Jira sub-task push)."""
    from app.stories import push as push_mod

    with patch.object(push_mod, "get_asana_task_gid", return_value="EXISTS"), \
         patch.object(push_mod, "save_asana_task_gid") as save, \
         patch("app.connectors.asana_oauth.create_subtask") as create:
        push_mod.push_asana_subtasks(
            CID, PROJ, "PARENT", "tid", ["Design API"], access_token="tok",
        )
    create.assert_not_called()
    save.assert_not_called()


def test_push_story_with_subtasks_creates_native_subtasks_and_keeps_them_out_of_notes():
    """A first push of a story with child issues creates the parent task AND a
    native subtask per child; the child list does not also live in the notes."""
    from app.connectors import asana_oauth
    from app.stories import push as push_mod

    story = Story(title="Parent", body="B")
    story.subtasks = ["Child A", "[P] Child B"]

    captured_notes: list[str] = []

    def _create_task(_tok, _proj, *, name, notes):
        captured_notes.append(notes or "")
        return {"gid": "PARENT", "url": "u"}

    with patch.object(push_mod, "_asana_creds", return_value="tok"), \
         patch.object(push_mod, "get_asana_task_gid", return_value=None), \
         patch.object(push_mod, "save_asana_task_gid"), \
         patch.object(asana_oauth, "create_task", side_effect=_create_task), \
         patch.object(asana_oauth, "create_subtask", return_value={"gid": "SUB"}) as sub:
        r = push_mod.push_stories_to_asana(CID, PROJ, [story])

    assert r["created"][0]["task_id"] == "PARENT"
    assert sub.call_count == 2  # both children created as real subtasks
    # The Child issues text section is not duplicated into the notes body.
    assert "Child A" not in captured_notes[0]
    assert "Child B" not in captured_notes[0]


def test_push_isolates_per_story_failure():
    from app.stories import push as push_mod

    ok_story = Story(title="Good", body="B")
    bad_story = Story(title="Bad", body="B")

    def _create(_tok, _proj, *, name, notes):
        if name == "Bad":
            raise RuntimeError("asana rejected")
        return {"gid": "G1", "url": "u"}

    with patch.object(push_mod, "_asana_creds", return_value="tok"), \
         patch.object(push_mod, "get_asana_task_gid", return_value=None), \
         patch.object(push_mod, "save_asana_task_gid"), \
         patch("app.connectors.asana_oauth.create_task", side_effect=_create):
        r = push_mod.push_stories_to_asana(CID, PROJ, [ok_story, bad_story])
    assert len(r["created"]) == 1 and r["created"][0]["story"] == "Good"
    assert len(r["errors"]) == 1 and r["errors"][0]["story"] == "Bad"

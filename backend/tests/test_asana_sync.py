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


def test_get_task_reports_gone_on_404():
    """A deleted Asana task (404/410) is reported as '__gone__' — distinct from
    a transient failure — so the sync re-pushes it instead of skipping."""
    from app.connectors import asana_oauth

    for code in (404, 410):
        resp = MagicMock(status_code=code, ok=False, text="not found")
        with patch("app.connectors.asana_oauth.requests.get", return_value=resp):
            assert asana_oauth.get_task("tok", "t1", project_gid=PROJ) == {"__gone__": True}


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


# ── Custom fields: metadata, read normalization, write encoding ──────────────


def test_normalize_asana_meta_maps_custom_fields_and_builtins():
    from app.connectors import tracker_meta as tm

    meta = tm.normalize_asana_meta("PROJ1", sections=[
        {"gid": "s1", "name": "To Do"},
    ], custom_fields=[
        {"gid": "cf_enum", "name": "Effort", "resource_subtype": "enum",
         "enum_options": [{"gid": "o_hi", "name": "High", "color": "red"},
                          {"gid": "o_lo", "name": "Low", "color": "blue"}]},
        {"gid": "cf_multi", "name": "Areas", "resource_subtype": "multi_enum",
         "enum_options": [{"gid": "a1", "name": "API"}]},
        {"gid": "cf_text", "name": "Notes", "resource_subtype": "text"},
        {"gid": "cf_num", "name": "Points", "resource_subtype": "number"},
        {"gid": "cf_date", "name": "Target", "resource_subtype": "date"},
        {"gid": "cf_people", "name": "Reviewer", "resource_subtype": "people"},
    ])
    by_id = {f["id"]: f for f in meta["fields"]}
    # Due date is the editable built-in (start date is intentionally NOT
    # editable — Asana rejects start_on writes without due_on).
    assert by_id["builtin:due_date"]["type"] == "date" and by_id["builtin:due_date"]["editable"]
    assert "builtin:start_date" not in by_id
    # Custom fields mapped to editor types + editable.
    assert by_id["cf_enum"]["type"] == "select" and by_id["cf_enum"]["editable"]
    assert by_id["cf_enum"]["options"] == [
        {"id": "o_hi", "name": "High", "color": "red"},
        {"id": "o_lo", "name": "Low", "color": "blue"}]
    assert by_id["cf_multi"]["type"] == "multiselect"
    assert by_id["cf_text"]["type"] == "text"
    assert by_id["cf_num"]["type"] == "number"
    assert by_id["cf_date"]["type"] == "date"
    assert by_id["cf_people"]["type"] == "users" and by_id["cf_people"]["editable"]


def test_cf_read_value_normalizes_each_type():
    from app.connectors import asana_oauth as ao

    assert ao._cf_read_value({"resource_subtype": "enum",
                              "enum_value": {"gid": "o1", "name": "High"}}) == {"id": "o1", "name": "High"}
    assert ao._cf_read_value({"resource_subtype": "multi_enum",
                              "multi_enum_values": [{"gid": "a", "name": "A"}]}) == [{"id": "a", "name": "A"}]
    assert ao._cf_read_value({"resource_subtype": "text", "text_value": "hi"}) == "hi"
    assert ao._cf_read_value({"resource_subtype": "number", "number_value": 7}) == 7
    assert ao._cf_read_value({"resource_subtype": "date",
                              "date_value": {"date": "2026-07-15", "date_time": None}}) == "2026-07-15"
    assert ao._cf_read_value({"resource_subtype": "people",
                              "people_value": [{"gid": "u1", "name": "Ada"}]}) == [{"id": "u1", "name": "Ada"}]
    # Empty values → None.
    assert ao._cf_read_value({"resource_subtype": "enum", "enum_value": None}) is None


def test_encode_asana_custom_field_write_shapes():
    from app.connectors import tracker_meta as tm

    # enum/select → the chosen option gid (string).
    assert tm.encode_field_value("asana", {"type": "select", "editable": True},
                                 {"id": "o_hi", "name": "High"}) == "o_hi"
    # multi_enum/multiselect → list of option gids.
    assert tm.encode_field_value("asana", {"type": "multiselect", "editable": True},
                                 [{"id": "a1"}, {"id": "a2"}]) == ["a1", "a2"]
    # date → Asana's date object.
    assert tm.encode_field_value("asana", {"type": "date", "editable": True},
                                 "2026-07-15") == {"date": "2026-07-15"}
    # people/users → list of user gids.
    assert tm.encode_field_value("asana", {"type": "users", "editable": True},
                                 [{"id": "u1"}]) == ["u1"]
    # text/number pass through.
    assert tm.encode_field_value("asana", {"type": "text", "editable": True}, "x") == "x"
    assert tm.encode_field_value("asana", {"type": "number", "editable": True}, 5) == 5


def test_asana_people_field_decodes_with_name():
    """People fields keep the person's NAME through decode — Asana's user
    compact is {gid, name}, so the override round-trips and the reconcile
    loop doesn't spuriously re-import a name=None value."""
    from app.connectors import tracker_meta as tm

    fdef = {"id": "cf_people", "type": "users", "editable": True, "options": None}
    got = tm.decode_field_value("asana", fdef, [{"id": "u1", "name": "Ada"}])
    assert got == [{"id": "u1", "name": "Ada"}]


def test_cf_read_value_date_reads_date_part_even_with_time():
    from app.connectors import asana_oauth as ao

    v = ao._cf_read_value({"resource_subtype": "date", "date_value": {
        "date": "2026-07-15", "date_time": "2026-07-15T09:00:00.000Z"}})
    assert v == "2026-07-15"  # date part, not the ISO datetime


def test_encode_asana_date_rejects_garbage():
    import pytest

    from app.connectors import tracker_meta as tm

    with pytest.raises(ValueError):
        tm.encode_field_value("asana", {"type": "date", "editable": True}, "not-a-date")


def test_encode_asana_users_rejects_entries_without_ids():
    import pytest

    from app.connectors import tracker_meta as tm

    with pytest.raises(ValueError):
        tm.encode_field_value("asana", {"type": "users", "editable": True},
                              [{"name": "Ada"}])  # no id → fail loudly, not clear


def test_get_task_surfaces_custom_fields_and_due_date():
    from app.connectors import asana_oauth

    payload = {"data": {
        "name": "T", "notes": "n", "completed": False, "permalink_url": "u",
        "modified_at": "2026-07-15T10:00:00.000Z", "due_on": "2026-07-20",
        "start_on": "2026-07-10",
        "memberships": [{"project": {"gid": PROJ}, "section": {"gid": "s1", "name": "To Do"}}],
        "custom_fields": [
            {"gid": "cf_enum", "resource_subtype": "enum",
             "enum_value": {"gid": "o_hi", "name": "High"}},
            {"gid": "cf_text", "resource_subtype": "text", "text_value": "hello"},
        ],
    }}
    resp = MagicMock(status_code=200, ok=True)
    resp.json.return_value = payload
    with patch("app.connectors.asana_oauth.requests.get", return_value=resp):
        state = asana_oauth.get_task("tok", "t1", project_gid=PROJ)
    assert state["custom_fields"] == [
        {"id": "cf_enum", "value": {"id": "o_hi", "name": "High"}},
        {"id": "cf_text", "value": "hello"},
    ]
    assert state["due_date"] == "2026-07-20" and state["start_date"] == "2026-07-10"


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


_META_CF = {
    "provider": "asana", "destination_id": PROJ,
    "statuses": [], "priorities": [], "issue_types": None,
    "fields": [
        {"id": "cf_enum", "name": "Effort", "type": "select", "editable": True,
         "options": [{"id": "o_hi", "name": "High"}]},
        {"id": "builtin:due_date", "name": "Due date", "type": "date",
         "editable": True, "options": None},
    ],
}


def test_asana_tracker_reads_and_writes_custom_fields():
    """A select custom field + the built-in due date round-trip through the
    _Tracker: get_task-shaped remote decodes to normalized values, and a push
    encodes them into ONE task PUT (custom_fields map + due_on)."""
    from app.connectors import asana_oauth

    tracker = _asana_tracker(_META_CF)

    # READ side.
    remote = {
        "custom_fields": [{"id": "cf_enum", "value": {"id": "o_hi", "name": "High"}}],
        "due_date": "2026-07-20",
    }
    got = tracker.remote_custom_fields(remote)
    assert got["cf_enum"] == {"id": "o_hi", "name": "High"}
    assert got["builtin:due_date"] == "2026-07-20"

    # WRITE side: one PUT, custom field encoded to its option gid + due_on built-in.
    with patch.object(asana_oauth, "update_task") as upd:
        tracker.push_custom_fields("t1", {
            "cf_enum": {"id": "o_hi", "name": "High"},
            "builtin:due_date": "2026-07-20",
        })
    upd.assert_called_once()
    kwargs = upd.call_args.kwargs
    assert kwargs["custom_fields"] == {"cf_enum": "o_hi"}
    assert kwargs["extra_fields"] == {"due_on": "2026-07-20"}


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

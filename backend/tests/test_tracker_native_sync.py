"""Tracker-native vocabulary in the two-way sync + field validation (PR 2/3
of the tracker-meta work): with a destination's metadata cached, statuses and
priorities sync VERBATIM in the customer's own vocabulary, custom fields
reconcile field-by-field, and ticket-field writes validate (and resolve
legacy names) against the destination's real vocabulary. Without meta,
everything falls back to the legacy heuristics — covered by test_ticket_sync.
"""
from __future__ import annotations

import pytest

from app.auth import CompanyContext
from app.stories.generate import Story
from tests.test_ticket_sync import (
    CID,
    FakeTracker,
    _edit_row,
    _seed_prd_tickets,
    _sync_cfg,
    fake_tracker,  # noqa: F401 — fixture reuse
)


def _ctx(cid: str = CID) -> CompanyContext:
    return CompanyContext(company_id=cid, role="owner", user_id="u")


@pytest.fixture()
def quiet_kicks(monkeypatch):
    """Disable the instant-push background threads for tests that exercise
    the save routes but aren't about the kicks — real daemon threads racing
    the shared fake-SQLite connection make those tests flaky. Order this
    AFTER isolated_settings in the test signature (module reload)."""
    monkeypatch.setattr(
        "app.stories.sync.kick_prd_sync_from_key", lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "app.stories.sync.kick_comment_push", lambda *a, **k: False,
    )


#: A customized Jira-style destination: renamed workflow, real priority
#: scheme, one editable select, one editable text, one exotic read-only
#: field, and a number field (for invalid-value tests).
META = {
    "provider": "jira",
    "destination_id": "KAN",
    "statuses": [
        {"id": "1", "name": "Groomed", "color": None, "category": "open"},
        {"id": "2", "name": "Building", "color": "#fd0", "category": "in_progress"},
        {"id": "3", "name": "Shipped", "color": "#0f0", "category": "done"},
    ],
    "priorities": [
        {"id": "1", "name": "Highest", "color": None},
        {"id": "2", "name": "High", "color": None},
        {"id": "3", "name": "Medium", "color": None},
        {"id": "4", "name": "Low", "color": None},
    ],
    "issue_types": [
        {"id": "t1", "name": "Task", "subtask": False},
        {"id": "t2", "name": "Story", "subtask": False},
        {"id": "t3", "name": "Sub-task", "subtask": True},
    ],
    "fields": [
        {"id": "customfield_1", "name": "Team", "type": "select",
         "raw_type": "select", "required": False, "editable": True,
         "options": [
             {"id": "o1", "name": "Platform", "color": None},
             {"id": "o2", "name": "Growth", "color": None},
         ]},
        {"id": "customfield_2", "name": "Notes", "type": "text",
         "raw_type": "textfield", "required": False, "editable": True,
         "options": None},
        {"id": "customfield_3", "name": "Org", "type": "unsupported",
         "raw_type": "cascadingselect", "required": False, "editable": False,
         "options": None},
        {"id": "customfield_4", "name": "Effort", "type": "number",
         "raw_type": "float", "required": False, "editable": True,
         "options": None},
    ],
}


def _remote(title="Login", desc="Original", **over):
    return {
        "title": title, "description": desc, "status": "Groomed",
        "assignee": None, "url": "u", "priority": "Medium",
        "issue_type": "Task",
        "updated_at": "2026-07-11T12:00:00+00:00", **over,
    }


def _prev(base_hash, **over):
    return {
        "status": "Groomed", "content_hash": base_hash,
        "synced_at": "2026-07-05T00:00:00+00:00", "sprntly_status": None,
        "priority": "Medium", "issue_type": "Task", **over,
    }


# ── Status: verbatim import + category projection (meta present) ─────────────


def test_meta_status_imports_verbatim_with_category(isolated_settings, fake_tracker):  # noqa: F811
    """"Building" is NOT in Sprntly's vocabulary and the old heuristics can't
    place it — with meta it imports verbatim, plus the canonical category."""
    from app.db.client import require_client
    from app.db.ticket_sync import get_sync_config
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    FakeTracker.meta_seed = META
    fake_tracker.seed = {tid: _remote(status="Building")}
    _sync_cfg(7, statuses={tid: _prev(content_hash("Login", "Original"))})

    run_prd_sync(CID, 7)

    edit = (
        require_client().table("ticket_edits").select("status")
        .eq("company_id", CID).eq("ticket_key", f"prd-7-{tid}").execute().data[0]
    )
    assert edit["status"] == "Building"  # verbatim, not "In progress"
    entry = get_sync_config(CID, 7)["statuses"][tid]
    assert entry["status_category"] == "in_progress"


def test_meta_priority_change_imports_verbatim(isolated_settings, fake_tracker):  # noqa: F811
    from app.db.client import require_client
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    FakeTracker.meta_seed = META
    fake_tracker.seed = {tid: _remote(priority="Highest")}
    _sync_cfg(7, statuses={tid: _prev(content_hash("Login", "Original"))})

    run_prd_sync(CID, 7)

    edit = (
        require_client().table("ticket_edits").select("priority")
        .eq("company_id", CID).eq("ticket_key", f"prd-7-{tid}").execute().data[0]
    )
    assert edit["priority"] == "Highest"


def test_without_meta_priority_never_imports(isolated_settings, fake_tracker):  # noqa: F811
    """No meta → the pre-meta behavior exactly: priority changes in the
    tracker stay display-only (nothing lands in ticket_edits)."""
    from app.db.client import require_client
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    fake_tracker.seed = {tid: _remote(priority="Highest")}
    _sync_cfg(7, statuses={tid: _prev(content_hash("Login", "Original"))})

    run_prd_sync(CID, 7)

    rows = (
        require_client().table("ticket_edits").select("priority")
        .eq("company_id", CID).eq("ticket_key", f"prd-7-{tid}").execute().data
    )
    assert not rows or rows[0]["priority"] is None


# ── Issue type (Jira): import + best-effort push ─────────────────────────────


def test_issue_type_remote_change_imports(isolated_settings, fake_tracker):  # noqa: F811
    from app.db.client import require_client
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    FakeTracker.meta_seed = META
    fake_tracker.seed = {tid: _remote(issue_type="Story")}
    _sync_cfg(7, statuses={tid: _prev(content_hash("Login", "Original"))})

    run_prd_sync(CID, 7)

    edit = (
        require_client().table("ticket_edits").select("issue_type")
        .eq("company_id", CID).eq("ticket_key", f"prd-7-{tid}").execute().data[0]
    )
    assert edit["issue_type"] == "Story"


def test_issue_type_local_edit_pushes_best_effort(isolated_settings, fake_tracker):  # noqa: F811
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    FakeTracker.meta_seed = META
    fake_tracker.seed = {tid: _remote()}  # still "Task" in Jira
    _sync_cfg(7, statuses={tid: _prev(content_hash("Login", "Original"))})
    _edit_row(CID, f"prd-7-{tid}", issue_type="Story",
              updated_at="2026-07-10T00:00:00+00:00")

    run_prd_sync(CID, 7)

    tracker = fake_tracker.instances[0]
    assert tracker.type_sets == [(f"ref-{tid}", "Story")]


# ── Custom fields: field-by-field reconcile ──────────────────────────────────


def test_custom_field_remote_change_imports_and_merges(isolated_settings, fake_tracker):  # noqa: F811
    """The tracker changed ONE field → only that field imports, and the merge
    keeps the sibling local override intact (the clobber test)."""
    from app.db.client import require_client
    from app.db.ticket_sync import get_sync_config
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    FakeTracker.meta_seed = META
    fake_tracker.seed = {tid: _remote(custom_fields={
        "customfield_1": {"id": "o2", "name": "Growth"},   # changed remotely
        "customfield_2": None,
        "customfield_4": None,
    })}
    _sync_cfg(7, statuses={tid: _prev(
        content_hash("Login", "Original"),
        custom_fields={"customfield_1": {"id": "o1", "name": "Platform"},
                       "customfield_2": None, "customfield_4": None},
    )})
    # Sibling LOCAL override on another field (older than the last pass).
    _edit_row(CID, f"prd-7-{tid}",
              custom_fields={"customfield_2": "local note"},
              updated_at="2026-07-01T00:00:00+00:00")

    run_prd_sync(CID, 7)

    edit = (
        require_client().table("ticket_edits").select("custom_fields")
        .eq("company_id", CID).eq("ticket_key", f"prd-7-{tid}").execute().data[0]
    )
    assert edit["custom_fields"] == {
        "customfield_1": {"id": "o2", "name": "Growth"},  # imported
        "customfield_2": "local note",                     # survived the merge
    }
    # Snapshot reflects the pull.
    entry = get_sync_config(CID, 7)["statuses"][tid]
    assert entry["custom_fields"]["customfield_1"] == {"id": "o2", "name": "Growth"}


def test_custom_field_local_edit_pushes_out(isolated_settings, fake_tracker):  # noqa: F811
    from app.db.ticket_sync import get_sync_config
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    FakeTracker.meta_seed = META
    fake_tracker.seed = {tid: _remote(custom_fields={
        "customfield_1": {"id": "o1", "name": "Platform"},
        "customfield_2": None, "customfield_4": None,
    })}
    _sync_cfg(7, statuses={tid: _prev(
        content_hash("Login", "Original"),
        custom_fields={"customfield_1": {"id": "o1", "name": "Platform"},
                       "customfield_2": None, "customfield_4": None},
    )})
    # Local edit AFTER the last pass changes one field.
    _edit_row(CID, f"prd-7-{tid}",
              custom_fields={"customfield_1": {"id": "o2", "name": "Growth"}},
              updated_at="2026-07-10T00:00:00+00:00")

    run_prd_sync(CID, 7)

    tracker = fake_tracker.instances[0]
    assert tracker.field_pushes == [
        (f"ref-{tid}", {"customfield_1": {"id": "o2", "name": "Growth"}}),
    ]
    # The stored snapshot reflects the pushed value — the next pass must not
    # read our own write back as a remote change.
    entry = get_sync_config(CID, 7)["statuses"][tid]
    assert entry["custom_fields"]["customfield_1"] == {"id": "o2", "name": "Growth"}


def test_custom_field_stale_local_override_still_pushes(isolated_settings, fake_tracker):  # noqa: F811
    """The swallowed-edit regression: a local override RECORDED BEFORE the
    last pass (edit row older than synced_at) must still push when the remote
    value hasn't moved — remote is unchanged, so writing Sprntly's value can't
    clobber anything. Freshness gates don't apply to overrides."""
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    FakeTracker.meta_seed = META
    fake_tracker.seed = {tid: _remote(custom_fields={
        "customfield_1": {"id": "o1", "name": "Platform"},
        "customfield_2": None, "customfield_4": None,
    })}
    _sync_cfg(7, statuses={tid: _prev(
        content_hash("Login", "Original"),
        custom_fields={"customfield_1": {"id": "o1", "name": "Platform"},
                       "customfield_2": None, "customfield_4": None},
    )})
    # Local override OLDER than the last pass (the pre-baseline-swallow case).
    _edit_row(CID, f"prd-7-{tid}",
              custom_fields={"customfield_1": {"id": "o2", "name": "Growth"}},
              updated_at="2026-07-01T00:00:00+00:00")

    run_prd_sync(CID, 7)

    tracker = fake_tracker.instances[0]
    assert tracker.field_pushes == [
        (f"ref-{tid}", {"customfield_1": {"id": "o2", "name": "Growth"}}),
    ]


def test_custom_field_baseline_pass_pushes_existing_overrides(isolated_settings, fake_tracker):  # noqa: F811
    """First pass with fields (no prev snapshot): an EXISTING local override
    pushes once — Sprntly is the record when history is unknowable (mirrors
    the content baseline rule)."""
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    FakeTracker.meta_seed = META
    fake_tracker.seed = {tid: _remote(custom_fields={
        "customfield_1": {"id": "o1", "name": "Platform"},
        "customfield_2": None, "customfield_4": None,
    })}
    _sync_cfg(7, statuses={tid: _prev(content_hash("Login", "Original"))})
    _edit_row(CID, f"prd-7-{tid}",
              custom_fields={"customfield_2": "shipped note"},
              updated_at="2026-07-01T00:00:00+00:00")

    run_prd_sync(CID, 7)

    tracker = fake_tracker.instances[0]
    assert tracker.field_pushes == [(f"ref-{tid}", {"customfield_2": "shipped note"})]


def test_custom_field_first_pass_only_baselines(isolated_settings, fake_tracker):  # noqa: F811
    """A prev snapshot from BEFORE the custom-fields feature (no key) just
    baselines — history is unknowable, so nothing moves either direction."""
    from app.db.client import require_client
    from app.db.ticket_sync import get_sync_config
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    FakeTracker.meta_seed = META
    fake_tracker.seed = {tid: _remote(custom_fields={
        "customfield_1": {"id": "o2", "name": "Growth"},
        "customfield_2": "remote note", "customfield_4": None,
    })}
    _sync_cfg(7, statuses={tid: _prev(content_hash("Login", "Original"))})

    run_prd_sync(CID, 7)

    tracker = fake_tracker.instances[0]
    assert tracker.field_pushes == []
    rows = (
        require_client().table("ticket_edits").select("custom_fields")
        .eq("company_id", CID).eq("ticket_key", f"prd-7-{tid}").execute().data
    )
    assert not rows or not rows[0]["custom_fields"]
    entry = get_sync_config(CID, 7)["statuses"][tid]
    assert entry["custom_fields"]["customfield_2"] == "remote note"


# ── The real _Tracker's meta-native writes (unit, no DB/HTTP) ────────────────


def _bare_tracker(provider: str, meta: dict | None):
    from app.stories.sync import _Tracker

    t = _Tracker.__new__(_Tracker)
    t.provider, t.company_id, t.destination = provider, CID, "D"
    t.meta = meta
    t._token, t._cloud, t._site = "tok", "cloud", None
    return t


def test_priority_out_prefers_meta_then_legacy():
    clickup = _bare_tracker("clickup", {
        "priorities": [{"id": "2", "name": "High", "color": None}],
    })
    assert clickup._priority_out(Story(title="T", priority="High")) == 2
    # Legacy value not in meta → the generator's fixed map.
    assert clickup._priority_out(Story(title="T", priority="urgent")) == 1

    jira = _bare_tracker("jira", META)
    assert jira._priority_out(Story(title="T", priority="highest")) == "Highest"
    assert jira._priority_out(Story(title="T", priority="normal")) == "Medium"


def test_builtin_fields_read_and_write(monkeypatch):
    """Built-in task properties (dates, points, tags/labels) read off the
    task payload and write through the task-update APIs — not the
    custom-field endpoints."""
    from app.connectors.tracker_meta import (
        CLICKUP_BUILTIN_FIELDS,
        JIRA_BUILTIN_FIELDS,
    )
    from app.stories import sync as sync_mod

    cu = _bare_tracker("clickup", {"fields": [dict(f) for f in CLICKUP_BUILTIN_FIELDS]})
    out = cu.remote_custom_fields({
        "start_date": "1783728000000", "due_date": None, "points": 3,
        "tags": ["spike", "gate"], "custom_fields": [],
    })
    assert out["builtin:start_date"] == "2026-07-11"
    assert out["builtin:due_date"] is None
    assert out["builtin:points"] == 3
    assert out["builtin:tags"] == ["spike", "gate"]

    calls: dict = {}
    monkeypatch.setattr(
        sync_mod.clickup_oauth, "update_task",
        lambda tok, ref, extra: calls.setdefault("patch", extra),
    )
    monkeypatch.setattr(
        sync_mod.clickup_oauth, "add_task_tag",
        lambda tok, ref, name: calls.setdefault("tags", []).append(name),
    )
    cu.push_custom_fields("task1", {
        "builtin:due_date": "2026-07-11", "builtin:points": 5,
        "builtin:tags": ["infra"],
    })
    assert calls["patch"] == {"due_date": 1783728000000, "points": 5}
    assert calls["tags"] == ["infra"]

    jt = _bare_tracker("jira", {"fields": [dict(f) for f in JIRA_BUILTIN_FIELDS]})
    monkeypatch.setattr(
        sync_mod.jira_oauth, "update_issue",
        lambda tok, cloud, ref, extra_fields: calls.setdefault("jira", extra_fields),
    )
    jt.push_custom_fields("KAN-1", {
        "builtin:labels": ["infra", "q3"], "builtin:due_date": "2026-07-11",
    })
    assert calls["jira"] == {"labels": ["infra", "q3"], "duedate": "2026-07-11"}


def test_set_status_native_vs_legacy(monkeypatch):
    from app.stories import sync as sync_mod

    calls: list[tuple] = []
    monkeypatch.setattr(
        sync_mod.jira_oauth, "transition_issue",
        lambda tok, cloud, ref, status: calls.append(("jira", ref, status)) or True,
    )
    jira = _bare_tracker("jira", META)
    # A meta status pushes verbatim ("Shipped" is not in the legacy map).
    assert jira.set_status("KAN-1", "Shipped") is True
    # A legacy Sprntly value still flows through the fixed map.
    assert jira.set_status("KAN-1", "In progress") is True
    assert calls == [("jira", "KAN-1", "Shipped"), ("jira", "KAN-1", "In Progress")]
    # Unknown everywhere → refused, not guessed.
    assert jira.set_status("KAN-1", "Nonsense") is False


# ── Write validation (routes + MCP speak the tracker's vocabulary) ───────────


def _bind_with_meta(prd_id: int = 42) -> None:
    from app.db.ticket_sync import upsert_sync_config
    from app.db.tracker_meta import save_meta

    upsert_sync_config(CID, prd_id, provider="jira", destination_id="KAN")
    save_meta(CID, "jira", "KAN", META)


def test_save_fields_resolves_legacy_names_and_rejects_unknown(isolated_settings, quiet_kicks):
    from app.db.client import require_client
    from app.routes import tickets as routes

    _bind_with_meta()
    key = "prd-42-abc123"

    # Canonical/legacy names RESOLVE through the category (agents + old UI
    # flows keep working): "done" lands on this workspace's "Shipped".
    routes.save_fields(key, routes.FieldsIn(status="done", priority="high"), _ctx())
    row = (
        require_client().table("ticket_edits").select("status, priority")
        .eq("company_id", CID).eq("ticket_key", key).execute().data[0]
    )
    assert row["status"] == "Shipped" and row["priority"] == "High"

    # Exact tracker names pass verbatim (case-normalized to the meta casing).
    routes.save_fields(key, routes.FieldsIn(status="building"), _ctx())
    row = (
        require_client().table("ticket_edits").select("status")
        .eq("company_id", CID).eq("ticket_key", key).execute().data[0]
    )
    assert row["status"] == "Building"

    # Unknown status → 422 carrying the allowed names for self-correction.
    with pytest.raises(Exception) as ei:
        routes.save_fields(key, routes.FieldsIn(status="Weird"), _ctx())
    assert getattr(ei.value, "status_code", None) == 422
    assert "Shipped" in str(getattr(ei.value, "detail", ""))


def test_save_fields_unbound_stays_free_text(isolated_settings, quiet_kicks):
    """No destination (or no meta) → exactly the legacy behavior: any string
    saves unvalidated."""
    from app.db.client import require_client
    from app.routes import tickets as routes

    routes.save_fields("prd-9-xyz", routes.FieldsIn(status="Whatever"), _ctx())
    row = (
        require_client().table("ticket_edits").select("status")
        .eq("company_id", CID).eq("ticket_key", "prd-9-xyz").execute().data[0]
    )
    assert row["status"] == "Whatever"


def test_save_fields_custom_fields_validate_and_merge(isolated_settings, quiet_kicks):
    from app.db.client import require_client
    from app.routes import tickets as routes

    _bind_with_meta()
    key = "prd-42-abc123"

    # Two saves, one field each — the merge keeps both.
    routes.save_fields(key, routes.FieldsIn(
        custom_fields={"customfield_1": {"id": "o1", "name": "Platform"}},
    ), _ctx())
    routes.save_fields(key, routes.FieldsIn(
        custom_fields={"customfield_2": "a note"},
    ), _ctx())
    row = (
        require_client().table("ticket_edits").select("custom_fields")
        .eq("company_id", CID).eq("ticket_key", key).execute().data[0]
    )
    assert row["custom_fields"] == {
        "customfield_1": {"id": "o1", "name": "Platform"},
        "customfield_2": "a note",
    }

    # null clears ONE field's override, keeping the sibling.
    routes.save_fields(key, routes.FieldsIn(
        custom_fields={"customfield_1": None},
    ), _ctx())
    row = (
        require_client().table("ticket_edits").select("custom_fields")
        .eq("company_id", CID).eq("ticket_key", key).execute().data[0]
    )
    assert row["custom_fields"] == {"customfield_2": "a note"}

    # Unknown field id → 422; read-only field → 422; bad value → 422.
    for bad in (
        {"customfield_9": "x"},
        {"customfield_3": "x"},          # exotic → editable: False
        {"customfield_4": "not-a-number"},
    ):
        with pytest.raises(Exception) as ei:
            routes.save_fields(key, routes.FieldsIn(custom_fields=bad), _ctx())
        assert getattr(ei.value, "status_code", None) == 422


def test_save_fields_issue_type_validates(isolated_settings, quiet_kicks):
    from app.db.client import require_client
    from app.routes import tickets as routes

    _bind_with_meta()
    key = "prd-42-abc123"

    # Case-insensitive resolve to the real type name.
    routes.save_fields(key, routes.FieldsIn(issue_type="story"), _ctx())
    row = (
        require_client().table("ticket_edits").select("issue_type")
        .eq("company_id", CID).eq("ticket_key", key).execute().data[0]
    )
    assert row["issue_type"] == "Story"

    # Subtask types and unknown names are refused with the allowed list.
    for bad in ("Sub-task", "Epicish"):
        with pytest.raises(Exception) as ei:
            routes.save_fields(key, routes.FieldsIn(issue_type=bad), _ctx())
        assert getattr(ei.value, "status_code", None) == 422
        assert "Story" in str(getattr(ei.value, "detail", ""))


# ── Child issues → REAL Jira sub-tasks ───────────────────────────────────────


def test_push_jira_subtasks_is_idempotent_and_sets_parent(isolated_settings, monkeypatch):
    from app.stories import push as push_mod

    created: list[dict] = []

    def _fake_create(tok, cloud, **kw):
        created.append(kw)
        return {"key": f"KAN-{100 + len(created)}", "id": "x", "url": None}

    monkeypatch.setattr(push_mod.jira_oauth, "create_issue", _fake_create)

    push_mod.push_jira_subtasks(
        CID, "KAN", "KAN-7", "tid1",
        ["[P] Write migration", "Wire the route", "  "],
        access_token="tok", cloud_id="cloud", subtask_type="Sub-task",
    )
    assert [c["summary"] for c in created] == ["Write migration", "Wire the route"]
    assert all(c["issue_type"] == "Sub-task" for c in created)
    assert all(c["extra_fields"] == {"parent": {"key": "KAN-7"}} for c in created)

    # Second push: both children already mapped → nothing created.
    push_jira_count = len(created)
    push_mod.push_jira_subtasks(
        CID, "KAN", "KAN-7", "tid1",
        ["[P] Write migration", "Wire the route"],
        access_token="tok", cloud_id="cloud", subtask_type="Sub-task",
    )
    assert len(created) == push_jira_count

    # A NEW child (edited list) creates only the missing one.
    push_mod.push_jira_subtasks(
        CID, "KAN", "KAN-7", "tid1",
        ["Wire the route", "Ship the docs"],
        access_token="tok", cloud_id="cloud", subtask_type="Sub-task",
    )
    assert [c["summary"] for c in created][-1] == "Ship the docs"
    assert len(created) == 3


def test_subtask_type_gates_description_section(isolated_settings, monkeypatch):
    """With a sub-task type in meta, the Jira description drops its Child
    issues text (they exist as real sub-tasks); without one, the legacy
    section stays."""
    from app.db.tracker_meta import save_meta
    from app.stories.push import jira_subtask_type

    story = Story(title="T", body="B", subtasks=["step 1"])
    assert "Child issues" in story.to_description()
    assert "Child issues" not in story.to_description(include_subtasks=False)

    assert jira_subtask_type(CID, "KAN") is None  # no meta cached
    save_meta(CID, "jira", "KAN", META)  # META carries a Sub-task type
    assert jira_subtask_type(CID, "KAN") == "Sub-task"


# ── Comment push (one-way, Sprntly → tracker) ────────────────────────────────


def _comment_row(key: str, body: str, created_at: str, author: str = "Ada") -> int:
    from app.db.client import require_client

    resp = require_client().table("ticket_comments").insert({
        "company_id": CID, "ticket_key": key, "author": author,
        "body": body, "created_at": created_at,
    }).execute()
    return resp.data[0]["id"]


def test_sync_pass_pushes_post_binding_comments_only(isolated_settings, fake_tracker):  # noqa: F811
    """Catch-up: unpushed comments created AFTER the PRD was bound become
    real tracker comments (attributed) and are marked pushed; pre-binding
    history never floods the tracker."""
    from app.db.client import require_client
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    key = f"prd-7-{tid}"
    fake_tracker.seed = {tid: _remote()}
    _sync_cfg(7, statuses={tid: _prev(content_hash("Login", "Original"))})

    old_id = _comment_row(key, "ancient history", "2020-01-01T00:00:00+00:00")
    new_id = _comment_row(key, "watch the rate limit", "2027-01-01T00:00:00+00:00")

    run_prd_sync(CID, 7)

    tracker = fake_tracker.instances[0]
    assert tracker.comments == [(f"ref-{tid}", "Ada: watch the rate limit")]
    rows = {
        r["id"]: r["tracker_comment_id"]
        for r in require_client().table("ticket_comments").select("id, tracker_comment_id")
        .eq("company_id", CID).execute().data
    }
    assert rows[new_id] == "tc-1" and rows[old_id] is None
    # A second pass never re-pushes an already-marked comment.
    run_prd_sync(CID, 7)
    assert fake_tracker.instances[1].comments == []


def test_kick_comment_push_pushes_and_marks(isolated_settings, monkeypatch, fake_tracker):  # noqa: F811
    import time

    from app.db.client import require_client
    from app.db.ticket_sync import upsert_sync_config
    from app.stories import sync as sync_mod

    base = Story(title="Login", body="B").to_dict()
    tid = base["id"]
    key = f"prd-42-{tid}"

    # Unbound → no-op.
    cid_row = _comment_row(key, "hello", "2027-01-01T00:00:00+00:00")
    assert sync_mod.kick_comment_push(CID, key, cid_row, "Ada", "hello") is False

    upsert_sync_config(CID, 42, provider="clickup", destination_id="901")
    fake_tracker.seed = {tid: _remote()}
    assert sync_mod.kick_comment_push(CID, key, cid_row, "Ada", "hello") is True
    # The push runs on a daemon thread — wait for the mark to land.
    for _ in range(100):
        row = (
            require_client().table("ticket_comments")
            .select("tracker_comment_id").eq("id", cid_row).execute().data[0]
        )
        if row["tracker_comment_id"]:
            break
        time.sleep(0.02)
    assert row["tracker_comment_id"] == "tc-1"
    tracker = fake_tracker.instances[-1]
    assert tracker.comments == [(f"ref-{tid}", "Ada: hello")]


def test_comment_routes_kick_instant_push(isolated_settings, monkeypatch):
    from app.routes import internal_mcp as mcp
    from app.routes import tickets as routes

    kicked: list[tuple] = []
    monkeypatch.setattr(
        "app.stories.sync.kick_comment_push",
        lambda cid, key, comment_id, author, body: kicked.append(
            (cid, key, author, body)
        ) or True,
    )
    _bind_with_meta()
    key = "prd-42-abc123"
    routes.add_comment(key, routes.CommentIn(author="user", body="from web"), _ctx())
    monkeypatch.setattr(
        "app.db.companies.display_name_for_user", lambda uid: "Agent Smith",
    )
    mcp.add_ticket_comment(key, CID, "u-1", mcp.TicketCommentIn(body="from mcp"))
    assert kicked == [
        (CID, key, "user", "from web"),
        (CID, key, "Agent Smith", "from mcp"),
    ]


# ── Instant push (edit → tracker immediately, no scheduler wait) ─────────────


def test_kick_prd_sync_from_key(isolated_settings, monkeypatch):
    import threading

    from app.db.ticket_sync import get_sync_config, upsert_sync_config
    from app.stories import sync as sync_mod

    # Malformed key / unbound PRD → no-op.
    assert sync_mod.kick_prd_sync_from_key(CID, "not-a-key") is False
    assert sync_mod.kick_prd_sync_from_key(CID, "prd-99-x") is False

    ran = threading.Event()
    monkeypatch.setattr(
        sync_mod, "run_prd_sync", lambda cid, pid: ran.set(),
    )
    upsert_sync_config(CID, 42, provider="jira", destination_id="KAN")
    assert sync_mod.kick_prd_sync_from_key(CID, "prd-42-abc") is True
    assert ran.wait(2), "the background pass never ran"
    # The kick marked the row syncing BEFORE spawning, so an immediate second
    # save is single-flighted rather than stampeding the tracker API.
    assert get_sync_config(CID, 42)["sync_status"] == "syncing"
    assert sync_mod.kick_prd_sync_from_key(CID, "prd-42-abc") is False


def test_saves_kick_an_instant_sync(isolated_settings, monkeypatch):
    """Every Sprntly-side edit — fields (web + MCP) and description — pushes
    to the tracker immediately via the kick, not at the next scheduler tick."""
    from app.routes import internal_mcp as mcp
    from app.routes import tickets as routes

    kicked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.stories.sync.kick_prd_sync_from_key",
        lambda cid, key: kicked.append((cid, key)) or True,
    )
    _bind_with_meta()
    key = "prd-42-abc123"

    routes.save_fields(key, routes.FieldsIn(status="Building"), _ctx())
    routes.save_description(
        key, routes.DescriptionIn(description="new text"), _ctx(),
    )
    mcp.save_ticket_fields(key, CID, mcp.TicketFieldsIn(priority="High"))
    mcp.save_ticket_description(key, CID, mcp.TicketDescriptionIn(description="d"))
    assert kicked == [(CID, key)] * 4


def test_mcp_save_fields_resolves_canonical_status(isolated_settings, quiet_kicks):
    """An MCP agent following the server instructions ("move to Done") works
    against a workspace whose done-status is named "Shipped"."""
    from app.db.client import require_client
    from app.routes import internal_mcp as mcp

    _bind_with_meta()
    key = "prd-42-abc123"
    mcp.save_ticket_fields(key, CID, mcp.TicketFieldsIn(status="Done"))
    row = (
        require_client().table("ticket_edits").select("status")
        .eq("company_id", CID).eq("ticket_key", key).execute().data[0]
    )
    assert row["status"] == "Shipped"

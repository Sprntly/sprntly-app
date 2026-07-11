"""Two-way ticket tracker sync: the engine (app/stories/sync.py), its state
store (app/db/ticket_sync.py), the /v1/stories/sync routes, and the identity
guarantees that make edited tickets update (not duplicate) tracker tasks.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.auth import CompanyContext
from app.stories.generate import Story

CID = "11111111-2222-3333-4444-555555555555"


def _ctx(cid: str = CID) -> CompanyContext:
    return CompanyContext(company_id=cid, role="owner", user_id="u")


def _seed_prd_tickets(company_id: str, prd_id: int, stories: list[dict]) -> None:
    from app.db.client import require_client

    require_client().table("prd_tickets").insert(
        {
            "company_id": company_id,
            "prd_id": prd_id,
            "content_hash": "h",
            "stories": stories,
            "status": "ready",
        }
    ).execute()


# ── Identity: edits must not change a story's stable id ─────────────────────


def test_rehydrated_story_keeps_id_across_edits():
    """A stored story rehydrated via from_dict pins its generation-time id, so
    an edited title/body still maps to the SAME tracker task (no duplicates)."""
    stored = Story(title="Login flow", body="As a user…").to_dict()
    rehydrated = Story.from_dict(stored)
    rehydrated.title = "Login flow v2"
    rehydrated.body = "Completely rewritten"
    assert rehydrated.stable_id() == stored["id"]


def test_story_without_stored_id_hashes_as_before():
    """Client-sent stories (StoryIn has no id) keep the legacy content hash."""
    assert (
        Story.from_dict({"title": "A", "body": "B"}).stable_id()
        == Story(title="A", body="B").stable_id()
    )


# ── Merge: ticket_edits (web + MCP writes) reach the pushed story ────────────


def test_merged_stories_apply_edit_overrides(isolated_settings):
    from app.db.client import require_client
    from app.stories.sync import merged_stories_for_prd

    base = Story(title="Login", body="Original", what="W", scope=["s1"]).to_dict()
    _seed_prd_tickets(CID, 7, [base])
    require_client().table("ticket_edits").insert(
        {
            "company_id": CID,
            "ticket_key": f"prd-7-{base['id']}",
            "title": "Login v2",
            "description": "Edited description",
            "acceptance_criteria": ["Given X, Then Y"],
            "priority": "high",
            "subtasks": ["Write migration"],
        }
    ).execute()

    [merged] = merged_stories_for_prd(CID, 7)
    assert merged.title == "Login v2"
    # The description override replaces the structured sections wholesale.
    assert merged.body == "Edited description"
    assert merged.what == "" and merged.scope == []
    assert merged.acceptance_criteria == ["Given X, Then Y"]
    assert merged.priority == "high"
    assert merged.subtasks == ["Write migration"]
    # Identity survives every one of those edits.
    assert merged.stable_id() == base["id"]


def test_merged_stories_without_edits_pass_through(isolated_settings):
    from app.stories.sync import merged_stories_for_prd

    base = Story(title="Untouched", body="B", acceptance_criteria=["AC"]).to_dict()
    _seed_prd_tickets(CID, 8, [base])
    [merged] = merged_stories_for_prd(CID, 8)
    assert merged.title == "Untouched"
    assert merged.acceptance_criteria == ["AC"]


# ── Direction decisions + import normalization (pure) ────────────────────────


def test_decide_direction_matrix():
    from app.stories.sync import decide_direction

    assert decide_direction(local_changed=False, remote_changed=False) == "none"
    assert decide_direction(local_changed=True, remote_changed=False) == "push"
    assert decide_direction(local_changed=False, remote_changed=True) == "import"
    # Both changed → last writer wins.
    assert decide_direction(
        local_changed=True, remote_changed=True,
        local_time="2026-07-10T10:00:00+00:00", remote_time="2026-07-10T11:00:00+00:00",
    ) == "import"
    assert decide_direction(
        local_changed=True, remote_changed=True,
        local_time="2026-07-10T12:00:00+00:00", remote_time="2026-07-10T11:00:00+00:00",
    ) == "push"
    # Uncomparable timestamps → Sprntly wins.
    assert decide_direction(
        local_changed=True, remote_changed=True, local_time=None, remote_time="garbage",
    ) == "push"


def test_normalize_imported_description_strips_tail_and_unbolds_labels():
    from app.stories.sync import normalize_imported_description

    pushed_render = (
        "**What**\nCreate the battle card.\n\n"
        "**Scope**\n- Who to target\n- Pain hooks\n\n"
        "**Acceptance criteria**\n- Given X, Then Y\n\n"
        "_Provenance: Part A §5 R2_"
    )
    out = normalize_imported_description(pushed_render)
    # Bold headers → labeled-text form; generated tail sections cut.
    assert out == (
        "What\nCreate the battle card.\n\n"
        "The ticket must cover\n- Who to target\n- Pain hooks"
    )
    # Freeform tracker text passes through untouched.
    assert normalize_imported_description("Just a plain rewrite.") == "Just a plain rewrite."


def test_tracker_status_mapping():
    from app.stories.sync import tracker_status_to_sprntly

    assert tracker_status_to_sprntly("IN PROGRESS") == "In progress"
    assert tracker_status_to_sprntly("In Review") == "Review"
    assert tracker_status_to_sprntly("Complete") == "Done"
    assert tracker_status_to_sprntly("Closed") == "Done"
    assert tracker_status_to_sprntly("to do") == "To do"
    assert tracker_status_to_sprntly("Some Custom Column") is None  # never imported


# ── The two-way pass (FakeTracker drives the engine) ─────────────────────────


class FakeTracker:
    """In-memory tracker double: `remotes` maps ticket_id → remote state; a
    missing entry = never created (bulk_create then registers it)."""

    instances: list["FakeTracker"] = []

    def __init__(self, provider, company_id, destination):
        self.provider, self.company_id, self.destination = provider, company_id, destination
        self.remotes = dict(FakeTracker.seed)
        self.pushed: list[tuple[str, str]] = []       # (ref, title)
        self.created: list[str] = []                   # titles
        self.status_sets: list[tuple[str, str]] = []   # (ref, status)
        FakeTracker.instances.append(self)

    seed: dict = {}

    def task_ref(self, tid):
        return f"ref-{tid}" if tid in self.remotes else None

    def remote(self, ref):
        tid = ref.removeprefix("ref-")
        return self.remotes.get(tid)

    def push(self, ref, story):
        tid = ref.removeprefix("ref-")
        self.pushed.append((ref, story.title))
        self.remotes[tid] = {
            **(self.remotes.get(tid) or {}),
            "title": story.title, "description": story.to_description(),
            "updated_at": "2026-07-11T12:00:00+00:00",
        }

    def set_status(self, ref, status):
        self.status_sets.append((ref, status))
        return True

    def bulk_create(self, stories):
        for s in stories:
            self.created.append(s.title)
            self.remotes[s.stable_id()] = {
                "title": s.title, "description": s.to_description(),
                "status": "to do", "assignee": None, "url": "u",
                "updated_at": "2026-07-11T12:00:00+00:00",
            }
        return {"created": [{"story": s.title} for s in stories], "errors": []}


@pytest.fixture()
def fake_tracker(monkeypatch):
    FakeTracker.instances = []
    FakeTracker.seed = {}
    from app.stories import sync as sync_mod

    monkeypatch.setattr(sync_mod, "_Tracker", FakeTracker)
    return FakeTracker


def _sync_cfg(prd_id: int, statuses: dict | None = None) -> None:
    from app.db.client import require_client
    from app.db.ticket_sync import upsert_sync_config

    upsert_sync_config(CID, prd_id, provider="clickup", destination_id="L1")
    if statuses is not None:
        require_client().table("prd_ticket_sync").update(
            {"statuses": statuses}
        ).eq("company_id", CID).eq("prd_id", prd_id).execute()


def _edit_row(company_id, key, **fields):
    from app.db.client import require_client

    require_client().table("ticket_edits").upsert(
        {"company_id": company_id, "ticket_key": key, **fields},
        on_conflict="company_id,ticket_key",
    ).execute()


def test_first_sync_bulk_creates_and_baselines(isolated_settings, fake_tracker):
    from app.db.ticket_sync import get_sync_config
    from app.stories.sync import run_prd_sync

    base = Story(title="Login", body="B").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    _sync_cfg(7)

    result = run_prd_sync(CID, 7)

    tracker = fake_tracker.instances[0]
    assert tracker.created == ["Login"]
    assert result["pushed"] == 1 and result["imported"] == 0
    cfg = get_sync_config(CID, 7)
    entry = cfg["statuses"][base["id"]]
    # Baselined: tracker content fingerprint + pass timestamp recorded.
    assert entry["content_hash"] and entry["synced_at"]
    assert entry["status"] == "to do" and entry["url"] == "u"
    assert cfg["sync_status"] == "idle" and cfg["last_synced_at"]


def test_tracker_edit_imports_back_into_ticket_edits(isolated_settings, fake_tracker):
    """A title/description rewritten IN the tracker lands in ticket_edits —
    visible to the web and MCP — instead of being overwritten by the push."""
    from app.db.client import require_client
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    fake_tracker.seed = {tid: {
        "title": "Login (edited in ClickUp)", "description": "Rewritten there.",
        "status": "to do", "assignee": "Sam", "url": "u",
        "updated_at": "2026-07-11T12:00:00+00:00",
    }}
    # Previous pass baselined DIFFERENT content → remote has changed since.
    _sync_cfg(7, statuses={tid: {
        "status": "to do", "content_hash": content_hash("Login", "Original"),
        "synced_at": "2026-07-01T00:00:00+00:00", "sprntly_status": None,
    }})

    result = run_prd_sync(CID, 7)

    assert result["imported"] == 1
    tracker = fake_tracker.instances[0]
    assert tracker.pushed == []  # import direction — nothing pushed out
    edit = (
        require_client().table("ticket_edits").select("*")
        .eq("company_id", CID).eq("ticket_key", f"prd-7-{tid}").execute().data[0]
    )
    assert edit["title"] == "Login (edited in ClickUp)"
    assert edit["description"] == "Rewritten there."


def test_local_edit_pushes_out_when_remote_unchanged(isolated_settings, fake_tracker):
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    remote = {"title": "Login", "description": "Original", "status": "to do",
              "assignee": None, "url": "u", "updated_at": "2026-07-01T00:00:00+00:00"}
    fake_tracker.seed = {tid: remote}
    _sync_cfg(7, statuses={tid: {
        "status": "to do",
        "content_hash": content_hash("Login", "Original"),
        "synced_at": "2026-07-02T00:00:00+00:00", "sprntly_status": None,
    }})
    # Local edit AFTER the last pass.
    _edit_row(CID, f"prd-7-{tid}", title="Login v2",
              updated_at="2026-07-10T00:00:00+00:00")

    result = run_prd_sync(CID, 7)

    tracker = fake_tracker.instances[0]
    assert [t for _, t in tracker.pushed] == ["Login v2"]
    assert result["pushed"] == 1 and result["imported"] == 0


def test_both_changed_newer_side_wins(isolated_settings, fake_tracker):
    from app.db.client import require_client
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    # Remote edited at 12:00, local at 10:00 → remote (tracker) wins.
    fake_tracker.seed = {tid: {
        "title": "Tracker title", "description": "Tracker text", "status": "to do",
        "assignee": None, "url": "u", "updated_at": "2026-07-10T12:00:00+00:00",
    }}
    _sync_cfg(7, statuses={tid: {
        "status": "to do", "content_hash": content_hash("Login", "Original"),
        "synced_at": "2026-07-09T00:00:00+00:00", "sprntly_status": None,
    }})
    _edit_row(CID, f"prd-7-{tid}", title="Local title",
              updated_at="2026-07-10T10:00:00+00:00")

    result = run_prd_sync(CID, 7)

    assert result["imported"] == 1
    tracker = fake_tracker.instances[0]
    assert tracker.pushed == []
    edit = (
        require_client().table("ticket_edits").select("title")
        .eq("company_id", CID).eq("ticket_key", f"prd-7-{tid}").execute().data[0]
    )
    assert edit["title"] == "Tracker title"


def test_no_changes_means_no_writes(isolated_settings, fake_tracker):
    """The steady state (nothing changed on either side) costs zero tracker
    writes — the 15-minute cadence stays cheap."""
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    fake_tracker.seed = {tid: {
        "title": "Login", "description": "Original", "status": "to do",
        "assignee": None, "url": "u", "updated_at": "2026-07-01T00:00:00+00:00",
    }}
    _sync_cfg(7, statuses={tid: {
        "status": "to do", "content_hash": content_hash("Login", "Original"),
        "synced_at": "2026-07-02T00:00:00+00:00", "sprntly_status": None,
    }})

    result = run_prd_sync(CID, 7)

    tracker = fake_tracker.instances[0]
    assert tracker.pushed == [] and tracker.created == [] and tracker.status_sets == []
    assert result == {"pushed": 0, "imported": 0, "push_errors": 0,
                      "statuses": result["statuses"]}


def test_tracker_status_change_imports_into_internal_status(isolated_settings, fake_tracker):
    from app.db.client import require_client
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    fake_tracker.seed = {tid: {
        "title": "Login", "description": "Original", "status": "in progress",
        "assignee": "Sam", "url": "u", "updated_at": "2026-07-01T00:00:00+00:00",
    }}
    # Last pass saw "to do" → the tracker moved it since.
    _sync_cfg(7, statuses={tid: {
        "status": "to do", "content_hash": content_hash("Login", "Original"),
        "synced_at": "2026-07-02T00:00:00+00:00", "sprntly_status": None,
    }})

    run_prd_sync(CID, 7)

    edit = (
        require_client().table("ticket_edits").select("status")
        .eq("company_id", CID).eq("ticket_key", f"prd-7-{tid}").execute().data[0]
    )
    assert edit["status"] == "In progress"


def test_local_status_change_pushes_out_best_effort(isolated_settings, fake_tracker):
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    fake_tracker.seed = {tid: {
        "title": "Login", "description": "Original", "status": "to do",
        "assignee": None, "url": "u", "updated_at": "2026-07-01T00:00:00+00:00",
    }}
    _sync_cfg(7, statuses={tid: {
        "status": "to do", "content_hash": content_hash("Login", "Original"),
        "synced_at": "2026-07-02T00:00:00+00:00", "sprntly_status": None,
    }})
    # The PM moved it in Sprntly (status-only edit, updated_at older than
    # synced_at → not a content push, but the status still flows out).
    _edit_row(CID, f"prd-7-{tid}", status="Done",
              updated_at="2026-07-01T00:00:00+00:00")

    run_prd_sync(CID, 7)

    tracker = fake_tracker.instances[0]
    assert tracker.status_sets == [(f"ref-{tid}", "Done")]
    assert tracker.pushed == []


def test_run_prd_sync_records_failure_and_reraises(isolated_settings, monkeypatch):
    from app.db.ticket_sync import get_sync_config, upsert_sync_config
    from app.stories import sync as sync_mod
    from app.stories.push import ClickUpNotConnectedError

    _seed_prd_tickets(CID, 7, [Story(title="T", body="B").to_dict()])
    upsert_sync_config(CID, 7, provider="clickup", destination_id="L1")

    def _boom(*a, **k):
        raise ClickUpNotConnectedError("ClickUp is not connected")

    monkeypatch.setattr(sync_mod, "_Tracker", _boom)
    with pytest.raises(ClickUpNotConnectedError):
        sync_mod.run_prd_sync(CID, 7)

    cfg = get_sync_config(CID, 7)
    assert cfg["sync_status"] == "idle"  # never wedged in 'syncing'
    assert "not connected" in cfg["last_error"]
    assert cfg["last_synced_at"] is None


def test_run_prd_sync_requires_config(isolated_settings):
    from app.stories.sync import TicketSyncNotConfiguredError, run_prd_sync

    with pytest.raises(TicketSyncNotConfiguredError):
        run_prd_sync(CID, 404)


def test_sync_in_flight_staleness_window():
    from app.stories.sync import sync_in_flight

    now = datetime.now(timezone.utc)
    assert sync_in_flight(
        {"sync_status": "syncing", "sync_started_at": now.isoformat()}
    )
    # A crashed run (old started_at) may be taken over.
    assert not sync_in_flight(
        {
            "sync_status": "syncing",
            "sync_started_at": (now - timedelta(minutes=30)).isoformat(),
        }
    )
    assert not sync_in_flight({"sync_status": "idle"})
    assert not sync_in_flight({"sync_status": "syncing", "sync_started_at": None})


# ── Jira pull-status parity ──────────────────────────────────────────────────


def test_pull_jira_status_maps_by_ticket_id():
    from app.stories import push as push_mod

    def _issue_key(cid, project, ticket_id):
        return "SPR-1" if ticket_id == "tk1" else None

    with patch.object(push_mod, "_jira_creds", return_value=("tok", "cloud")), \
         patch.object(push_mod, "get_jira_issue_key", side_effect=_issue_key), \
         patch.object(
             push_mod.jira_oauth, "_site_url_for_cloud",
             return_value="https://acme.atlassian.net",
         ), patch.object(
             push_mod.jira_oauth, "get_issue",
             return_value={"status": "Done", "assignee": "Ada", "url": "u"},
         ) as get_issue:
        out = push_mod.pull_jira_status(CID, "SPR", ["tk1", "tk2"])

    # Only the mapped ticket comes back; the never-pushed one is absent.
    assert out == {"tk1": {"status": "Done", "assignee": "Ada", "url": "u"}}
    # The site url is resolved once and passed through (no per-issue lookups).
    assert get_issue.call_args.kwargs["site_url"] == "https://acme.atlassian.net"


# ── Routes: GET state / POST trigger ─────────────────────────────────────────


def test_sync_state_unconfigured(isolated_settings):
    from app.routes import stories as routes

    assert routes.sync_state(7, _ctx()) == {"configured": False}


def test_trigger_sync_registers_destination_and_runs(isolated_settings, monkeypatch):
    from app.routes import stories as routes

    ran: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "app.stories.sync.run_prd_sync",
        lambda cid, prd_id: ran.append((cid, prd_id)) or {"pushed": 0},
    )

    async def _flow():
        resp = await routes.trigger_sync(
            7,
            routes.SyncTriggerIn(
                provider="clickup", destination_id="L1", destination_name="Sprint"
            ),
            _ctx(),
        )
        assert resp == {"status": "syncing"}
        # Let the background task drain.
        for _ in range(100):
            if ran:
                break
            await asyncio.sleep(0.01)

    asyncio.run(_flow())
    assert ran == [(CID, 7)]

    state = routes.sync_state(7, _ctx())
    assert state["configured"] is True
    assert state["provider"] == "clickup"
    assert state["destination_id"] == "L1"
    assert state["destination_name"] == "Sprint"


def test_trigger_sync_404s_when_never_configured(isolated_settings):
    from app.routes import stories as routes

    async def _flow():
        with pytest.raises(Exception) as ei:
            await routes.trigger_sync(99, routes.SyncTriggerIn(), _ctx())
        assert getattr(ei.value, "status_code", None) == 404

    asyncio.run(_flow())


def test_trigger_sync_rejects_half_destinations_and_unknown_providers(isolated_settings):
    from app.routes import stories as routes

    async def _flow():
        with pytest.raises(Exception) as ei:
            await routes.trigger_sync(
                7, routes.SyncTriggerIn(provider="clickup"), _ctx()
            )
        assert getattr(ei.value, "status_code", None) == 400
        # Typed task-tracking but not engine-implemented → still rejected.
        with pytest.raises(Exception) as ei2:
            await routes.trigger_sync(
                7,
                routes.SyncTriggerIn(provider="linear", destination_id="X"),
                _ctx(),
            )
        assert getattr(ei2.value, "status_code", None) == 400
        # Connected-but-wrong-TYPE (communication, not task-tracking) → rejected.
        with pytest.raises(Exception) as ei3:
            await routes.trigger_sync(
                7,
                routes.SyncTriggerIn(provider="slack", destination_id="C042"),
                _ctx(),
            )
        assert getattr(ei3.value, "status_code", None) == 400
        assert "task-tracking" in str(getattr(ei3.value, "detail", ""))

    asyncio.run(_flow())


def test_trigger_sync_is_idempotent_while_in_flight(isolated_settings, monkeypatch):
    """A second trigger while a recent sync is running doesn't double-run."""
    from app.db.ticket_sync import get_sync_config, mark_syncing, upsert_sync_config
    from app.routes import stories as routes

    upsert_sync_config(CID, 7, provider="clickup", destination_id="L1")
    mark_syncing(CID, 7)

    called = []
    monkeypatch.setattr(
        "app.stories.sync.run_prd_sync", lambda *a: called.append(a)
    )

    async def _flow():
        resp = await routes.trigger_sync(7, routes.SyncTriggerIn(), _ctx())
        assert resp == {"status": "syncing"}

    asyncio.run(_flow())
    assert called == []
    assert get_sync_config(CID, 7)["sync_status"] == "syncing"


# ── Scheduler cycle ──────────────────────────────────────────────────────────


def test_scheduler_cycle_syncs_each_auto_row_isolated(isolated_settings, monkeypatch):
    """Every auto_sync row runs; one failing row never stops the rest; rows
    with a recent in-flight sync are skipped."""
    from app.db.ticket_sync import mark_syncing, upsert_sync_config
    from app import scheduler as sched

    upsert_sync_config(CID, 1, provider="clickup", destination_id="L1")
    upsert_sync_config(CID, 2, provider="jira", destination_id="SPR")
    upsert_sync_config(CID, 3, provider="clickup", destination_id="L3")
    mark_syncing(CID, 3)  # in flight → skipped

    ran: list[int] = []

    def _run(cid, prd_id):
        if prd_id == 1:
            raise RuntimeError("boom")
        ran.append(prd_id)
        return {"pushed": 0, "push_errors": 0}

    monkeypatch.setattr("app.stories.sync.run_prd_sync", _run)
    asyncio.run(sched._run_ticket_sync_cycle())
    assert ran == [2]  # prd 1 failed (isolated), prd 3 skipped, prd 2 ran

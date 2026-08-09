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
from app.stories.scope import prd_scope

# Pin every test that touches this shared CID / FakeTracker / prd_id fixture
# family to the SAME xdist worker (requires `--dist=loadgroup`), run as one
# contiguous scheduling unit. Several unrelated test files independently
# reuse this exact CID (and small, easily-colliding prd_ids like 7/30/31/42)
# with the SAME FakeTracker/isolated_settings machinery; under `-n auto`
# these can otherwise be scheduled interleaved with unrelated tests
# elsewhere in the suite, widening the window for any as-yet-unknown
# cross-test leak to land here. Defense in depth alongside the structural
# executor-drain fix in conftest.py (`_drain_orphaned_executor_work`) — see
# that fixture's docstring for the actual mechanism this was compensating
# for. Every file sharing this exact CID literal carries the same mark:
# test_ticket_sync.py (source), test_ticket_lifecycle.py,
# test_tracker_native_sync.py, test_asana_sync.py, test_tracker_meta.py.
pytestmark = pytest.mark.xdist_group(name="ticket-sync-shared-cid")

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
    missing entry = never created (bulk_create then registers it). `meta_seed`
    (a normalized TrackerMeta dict) turns on the tracker-native paths, exactly
    like a cached tracker_meta row does on the real _Tracker."""

    instances: list["FakeTracker"] = []

    def __init__(self, provider, company_id, destination):
        self.provider, self.company_id, self.destination = provider, company_id, destination
        self.remotes = dict(FakeTracker.seed)
        # tids the tracker 404s (deleted there) → remote() reports "__gone__".
        self.gone = set(FakeTracker.gone_seed)
        self.meta = FakeTracker.meta_seed
        self.pushed: list[tuple[str, str]] = []       # (ref, title)
        self.created: list[str] = []                   # titles
        self.cleared: list[str] = []                   # tids clear_ref'd
        self.removed: list[str] = []                   # refs remove_task'd
        self.status_sets: list[tuple[str, str]] = []   # (ref, status)
        self.field_pushes: list[tuple[str, dict]] = [] # (ref, {fid: value})
        self.field_push_current: list[dict] = []       # remote values at push time
        self.type_sets: list[tuple[str, str]] = []     # (ref, issue_type)
        self.comments: list[tuple[str, str]] = []      # (ref, text)
        self.assignee_sets: list[tuple[str, str]] = [] # (ref, account_id)
        self.assignee_map = dict(FakeTracker.assignee_seed)
        FakeTracker.instances.append(self)

    seed: dict = {}
    gone_seed: set = set()
    meta_seed: dict | None = None
    assignee_seed: dict = {}  # lower-cased email → accountId (assignable users)
    removal_fails: bool = False  # tracker refuses remove_task (delete + close)

    def assignee_ref(self, assignee):
        # Mirrors the real _Tracker: resolve the Sprntly assignee's email to a
        # tracker accountId; None when absent / no match.
        if not isinstance(assignee, dict):
            return None
        email = (assignee.get("email") or "").strip().lower()
        if not email:
            return None
        return self.assignee_map.get(email)

    def set_assignee(self, ref, account_id):
        self.assignee_sets.append((ref, account_id))
        return True

    # Mirrors of the real _Tracker's meta surface (same semantics).
    def meta_status(self, name):
        if not self.meta or not name:
            return None
        want = name.strip().lower()
        for s in self.meta.get("statuses") or []:
            if (s.get("name") or "").strip().lower() == want:
                return s
        return None

    def editable_fields(self):
        return [
            f for f in (self.meta or {}).get("fields") or [] if f.get("editable")
        ]

    def meta_issue_type(self, name):
        if not self.meta or not name:
            return None
        want = name.strip().lower()
        for t in self.meta.get("issue_types") or []:
            if not t.get("subtask") and (t.get("name") or "").strip().lower() == want:
                return t.get("name")
        return None

    def set_issue_type(self, ref, issue_type):
        self.type_sets.append((ref, issue_type))
        return True

    def add_comment(self, ref, text):
        self.comments.append((ref, text))
        return f"tc-{len(self.comments)}"

    def remote_custom_fields(self, remote):
        # Tests seed already-normalized values directly on the remote state.
        return {
            f["id"]: (remote.get("custom_fields") or {}).get(f["id"])
            for f in self.editable_fields()
        }

    def push_custom_fields(self, ref, values, current=None):
        # `current` = the tracker's values right now; the real ClickUp branch
        # needs it to know which tags to REMOVE (its tag API has no whole-list
        # write). Recorded so tests can assert it was threaded through.
        self.field_pushes.append((ref, dict(values)))
        self.field_push_current.append(dict(current or {}))
        tid = ref.removeprefix("ref-")
        cf = dict((self.remotes.get(tid) or {}).get("custom_fields") or {})
        cf.update(values)
        self.remotes[tid] = {**(self.remotes.get(tid) or {}), "custom_fields": cf}

    def task_ref(self, tid):
        return f"ref-{tid}" if tid in self.remotes else None

    def clear_ref(self, tid):
        self.cleared.append(tid)
        self.remotes.pop(tid, None)
        # Also drop it from the SEED, which is what a later pass's tracker is
        # built from. The real clear_ref deletes a mapping ROW, so the next
        # pass genuinely sees the ticket as never-pushed; without this the fake
        # would resurrect it and hide restore/re-create bugs.
        FakeTracker.seed.pop(tid, None)
        self.gone.discard(tid)

    def remove_task(self, ref):
        # Whole-ticket removal (deleted / excluded). `removal_fails` makes the
        # tracker refuse, so a test can check the mapping is KEPT for a retry.
        self.removed.append(ref)
        if FakeTracker.removal_fails:
            return False
        self.remotes.pop(ref.removeprefix("ref-"), None)
        return True

    def remote(self, ref):
        tid = ref.removeprefix("ref-")
        if tid in self.gone:
            return {"__gone__": True}
        return self.remotes.get(tid)

    def push(self, ref, story, remote=None):
        # `remote` = the state the pass already read; the real ClickUp branch
        # reuses its checklists for the child-issue reconcile.
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
    FakeTracker.gone_seed = set()
    FakeTracker.meta_seed = None
    FakeTracker.assignee_seed = {}
    FakeTracker.removal_fails = False
    from app.stories import sync as sync_mod

    monkeypatch.setattr(sync_mod, "_Tracker", FakeTracker)
    return FakeTracker


def _sync_cfg(prd_id: int, statuses: dict | None = None) -> None:
    from app.db.client import require_client
    from app.db.ticket_sync import upsert_sync_config

    upsert_sync_config(CID, prd_scope(prd_id), provider="clickup", destination_id="L1")
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
    cfg = get_sync_config(CID, prd_scope(7))
    entry = cfg["statuses"][base["id"]]
    # Baselined: tracker content fingerprint + pass timestamp recorded.
    assert entry["content_hash"] and entry["synced_at"]
    assert entry["status"] == "to do" and entry["url"] == "u"
    assert cfg["sync_status"] == "idle" and cfg["last_synced_at"]


def test_tracker_side_deletion_repushes(isolated_settings, fake_tracker):
    """A ticket DELETED in the tracker (but still present in Sprntly) is
    RE-PUSHED on the next sync: the stale mapping is cleared and the task
    re-created. We only push, so a tracker-side delete must never drop the
    Sprntly ticket."""
    from app.db.ticket_sync import get_sync_config
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="B").to_dict()
    tid = base["id"]
    _seed_prd_tickets(CID, 30, [base])
    # Previously synced (baseline present), THEN deleted in the tracker.
    prev_hash = content_hash("Login", Story.from_dict(base).to_description())
    fake_tracker.seed = {tid: {"title": "Login", "status": "to do", "url": "u"}}
    fake_tracker.gone_seed = {tid}  # the tracker now 404s this task
    _sync_cfg(30, statuses={tid: {
        "content_hash": prev_hash, "synced_at": "2026-07-10T00:00:00+00:00",
        "status": "to do", "sprntly_status": "To do", "url": "u",
    }})

    result = run_prd_sync(CID, 30)

    tracker = fake_tracker.instances[0]
    # The stale mapping was cleared and the ticket re-created (not skipped).
    assert tid in tracker.cleared
    assert "Login" in tracker.created
    assert result["pushed"] >= 1
    # It re-baselines cleanly: a fresh fingerprint is recorded again.
    cfg = get_sync_config(CID, prd_scope(30))
    assert cfg["statuses"][tid]["content_hash"] and cfg["statuses"][tid]["synced_at"]


def test_transient_fetch_failure_does_not_repush(isolated_settings, fake_tracker):
    """A TRANSIENT fetch failure (remote None, not a definite 404) must NOT
    re-create — that would duplicate the task on a network blip. The ticket is
    left untouched with its prior state kept."""
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="B").to_dict()
    tid = base["id"]
    _seed_prd_tickets(CID, 31, [base])
    prev_hash = content_hash("Login", Story.from_dict(base).to_description())
    # In remotes (so task_ref is non-None) but remote() returns None (transient).
    fake_tracker.seed = {tid: None}
    _sync_cfg(31, statuses={tid: {
        "content_hash": prev_hash, "synced_at": "2026-07-10T00:00:00+00:00",
        "status": "to do", "sprntly_status": "To do", "url": "u",
    }})

    run_prd_sync(CID, 31)

    tracker = fake_tracker.instances[0]
    assert tracker.cleared == []       # never cleared
    assert tracker.created == []       # never re-created


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
                      "removed": 0, "statuses": result["statuses"]}


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


def test_assignee_resolves_by_email_and_pushes_out(isolated_settings, fake_tracker):
    """A Sprntly ticket assigned to a member whose email matches a tracker user
    pushes that user's accountId out — the 'assign here → assign there, same
    person by email' behavior. The written accountId is snapshotted so a later
    pass with no assignee change does NOT re-push it."""
    from app.db.ticket_sync import get_sync_config
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    fake_tracker.seed = {tid: {
        "title": "Login", "description": "Original", "status": "to do",
        "assignee": None, "url": "u", "updated_at": "2026-07-01T00:00:00+00:00",
    }}
    # The tracker's assignable users, keyed by their public email.
    fake_tracker.assignee_seed = {"sam@acme.com": "acct-sam"}
    _sync_cfg(7, statuses={tid: {
        "status": "to do", "content_hash": content_hash("Login", "Original"),
        "synced_at": "2026-07-02T00:00:00+00:00", "sprntly_status": None,
    }})
    # Assigned in Sprntly (assignee dict carries the member's email).
    _edit_row(
        CID, f"prd-7-{tid}",
        assignee={"user_id": "u1", "display_name": "Sam", "email": "Sam@Acme.com"},
        updated_at="2026-07-01T00:00:00+00:00",
    )

    run_prd_sync(CID, 7)

    tracker = fake_tracker.instances[0]
    # Matched case-insensitively → the tracker issue is assigned by accountId.
    assert tracker.assignee_sets == [(f"ref-{tid}", "acct-sam")]
    cfg = get_sync_config(CID, prd_scope(7))
    assert cfg["statuses"][tid]["assignee_account_id"] == "acct-sam"

    # Second pass, nothing changed → the already-set assignee is NOT re-pushed.
    tracker.assignee_sets.clear()
    run_prd_sync(CID, 7)
    assert fake_tracker.instances[-1].assignee_sets == []


def test_assignee_unmatched_email_leaves_tracker_untouched(isolated_settings, fake_tracker):
    """A Sprntly assignee whose email matches no tracker user (e.g. their Jira
    email isn't public) must NOT touch the tracker's assignee — never force an
    unassign or guess."""
    from app.stories.sync import content_hash, run_prd_sync

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    fake_tracker.seed = {tid: {
        "title": "Login", "description": "Original", "status": "to do",
        "assignee": None, "url": "u", "updated_at": "2026-07-01T00:00:00+00:00",
    }}
    fake_tracker.assignee_seed = {"someone-else@acme.com": "acct-x"}
    _sync_cfg(7, statuses={tid: {
        "status": "to do", "content_hash": content_hash("Login", "Original"),
        "synced_at": "2026-07-02T00:00:00+00:00", "sprntly_status": None,
    }})
    _edit_row(
        CID, f"prd-7-{tid}",
        assignee={"user_id": "u1", "display_name": "Sam", "email": "sam@acme.com"},
        updated_at="2026-07-01T00:00:00+00:00",
    )

    run_prd_sync(CID, 7)

    assert fake_tracker.instances[0].assignee_sets == []


def test_run_prd_sync_records_failure_and_reraises(isolated_settings, monkeypatch):
    from app.db.ticket_sync import get_sync_config, upsert_sync_config
    from app.stories import sync as sync_mod
    from app.stories.push import ClickUpNotConnectedError

    _seed_prd_tickets(CID, 7, [Story(title="T", body="B").to_dict()])
    upsert_sync_config(CID, prd_scope(7), provider="clickup", destination_id="L1")

    def _boom(*a, **k):
        raise ClickUpNotConnectedError("ClickUp is not connected")

    monkeypatch.setattr(sync_mod, "_Tracker", _boom)
    with pytest.raises(ClickUpNotConnectedError):
        sync_mod.run_prd_sync(CID, 7)

    cfg = get_sync_config(CID, prd_scope(7))
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


def test_tracker_assignee_ref_matches_email_case_insensitively():
    """The real _Tracker resolves a Sprntly assignee's email to the Jira
    accountId of the assignable user with the same email (case-insensitive),
    from a single assignable-users lookup. No email / no match → None."""
    from app.stories import sync as sync_mod

    users = [
        {"accountId": "acct-sam", "email": "Sam@Acme.com"},
        {"accountId": "acct-noemail", "email": None},   # email not public
    ]
    with patch.object(sync_mod, "_jira_creds", return_value=("tok", "cloud")), \
         patch.object(
             sync_mod.jira_oauth, "_site_url_for_cloud", return_value=None,
         ), patch(
             "app.db.tracker_meta.get_cached_meta", return_value=None,
         ), patch.object(
             sync_mod.jira_oauth, "list_assignable_users", return_value=users,
         ) as lau:
        tracker = sync_mod._Tracker("jira", CID, "SPR")
        assert tracker.assignee_ref({"email": "sam@acme.com"}) == "acct-sam"
        assert tracker.assignee_ref({"email": "nobody@acme.com"}) is None
        assert tracker.assignee_ref({"display_name": "No Email"}) is None
        assert tracker.assignee_ref(None) is None

    # The assignable-users list is fetched once and cached for the whole pass.
    assert lau.call_count == 1


# ── Routes: GET state / POST trigger ─────────────────────────────────────────


def test_sync_state_unconfigured(isolated_settings):
    from app.routes import stories as routes

    assert routes.sync_state(7, _ctx()) == {"configured": False}


def test_trigger_sync_registers_destination_and_runs(isolated_settings, monkeypatch):
    from app.routes import stories as routes

    ran: list[tuple[str, object]] = []
    monkeypatch.setattr(
        "app.stories.sync.run_ticket_sync",
        lambda cid, scope: ran.append((cid, scope)) or {"pushed": 0},
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
    assert ran == [(CID, prd_scope(7))]

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
        # Connected-but-wrong-TYPE (communication, not task-management) → rejected.
        with pytest.raises(Exception) as ei3:
            await routes.trigger_sync(
                7,
                routes.SyncTriggerIn(provider="slack", destination_id="C042"),
                _ctx(),
            )
        assert getattr(ei3.value, "status_code", None) == 400
        assert "task-management" in str(getattr(ei3.value, "detail", ""))

    asyncio.run(_flow())


def test_trigger_sync_is_idempotent_while_in_flight(isolated_settings, monkeypatch):
    """A second trigger while a recent sync is running doesn't double-run."""
    from app.db.ticket_sync import get_sync_config, mark_syncing, upsert_sync_config
    from app.routes import stories as routes

    upsert_sync_config(CID, prd_scope(7), provider="clickup", destination_id="L1")
    mark_syncing(CID, prd_scope(7))

    called = []
    monkeypatch.setattr(
        "app.stories.sync.run_ticket_sync", lambda *a: called.append(a)
    )

    async def _flow():
        resp = await routes.trigger_sync(7, routes.SyncTriggerIn(), _ctx())
        assert resp == {"status": "syncing"}

    asyncio.run(_flow())
    assert called == []
    assert get_sync_config(CID, prd_scope(7))["sync_status"] == "syncing"


# ── Scheduler cycle ──────────────────────────────────────────────────────────


def test_scheduler_cycle_syncs_each_auto_row_isolated(isolated_settings, monkeypatch):
    """Every auto_sync row runs; one failing row never stops the rest; rows
    with a recent in-flight sync are skipped."""
    from app.db.ticket_sync import mark_syncing, upsert_sync_config
    from app import scheduler as sched

    upsert_sync_config(CID, prd_scope(1), provider="clickup", destination_id="L1")
    upsert_sync_config(CID, prd_scope(2), provider="jira", destination_id="SPR")
    upsert_sync_config(CID, prd_scope(3), provider="clickup", destination_id="L3")
    mark_syncing(CID, prd_scope(3))  # in flight → skipped

    ran: list[int] = []

    def _run(cid, scope):
        if scope.id == 1:
            raise RuntimeError("boom")
        ran.append(scope.id)
        return {"pushed": 0, "push_errors": 0}

    monkeypatch.setattr("app.stories.sync.run_ticket_sync", _run)
    asyncio.run(sched._run_ticket_sync_cycle())
    assert ran == [2]  # prd 1 failed (isolated), prd 3 skipped, prd 2 ran


# ── the four-mirror round trip (ticket description layout) ───────────────────
#
# `to_description` (push), `story_editable_text` (what the web edits and the
# sync compares), and `_IMPORT_LABELS` (tracker -> Sprntly) are three renderings
# of ONE layout. If any two disagree about a label,
# `normalize_imported_description` stops recognising what the push just wrote,
# `content_hash(title, description)` differs on every pass, and the tracker sync
# reports a phantom remote change on every ticket, forever.

_M6_CUSTOM_LAYOUT = [
    {"label": "Summary", "source": "what"},
    {"label": "Acceptance owner", "source": "custom:acceptance_owner"},
    {"label": "The ask", "source": "user_story"},
    {"label": "Covers", "source": "scope"},
]


def _m6_story(**kw):
    from app.stories.generate import Story

    base = dict(
        title="Ship it", body="As a user, I want X, so that Y.",
        what="Build the thing", why_now="Churn is up 12%",
        user_story="As a user, I want X, so that Y.",
        scope=["cover A", "cover B"], out_of_scope="Not the mobile app",
        acceptance_criteria=["Given A When B Then C"],
        subtasks=["child one"], prd_section="R3", route="agent-ready",
    )
    base.update(kw)
    return Story(**base)


def _assert_round_trip_stable(story):
    """Push it, read it back, and assert the sync sees NO change.

    This is the exact comparison `sync_prd_tickets` makes: the tracker's
    description normalised back must equal the local editable text, and the
    content hash of the two must match. A mismatch here IS the permanent
    phantom diff."""
    from app.stories.sync import (
        content_hash,
        normalize_imported_description,
        story_editable_text,
    )

    pushed = story.to_description()
    normalised = normalize_imported_description(pushed, story.description_layout)
    local = story_editable_text(story)

    assert normalised == local, (
        "the push labels and the editable labels disagree — this is the "
        "permanent phantom-diff bug"
    )
    assert content_hash(story.title, normalised) == content_hash(story.title, local)


def test_round_trip_is_stable_under_the_default_layout():
    _assert_round_trip_stable(_m6_story())


def test_round_trip_is_stable_under_a_custom_layout():
    _assert_round_trip_stable(_m6_story(
        description_layout=_M6_CUSTOM_LAYOUT,
        custom_sections={"acceptance_owner": "QA lead"},
    ))


def test_the_import_labels_are_derived_from_the_layout():
    # The default map must be exactly the one this used to hard-code, including
    # the Scope -> "The ticket must cover" rename.
    from app.stories.sync import _IMPORT_LABELS, import_labels_for

    assert _IMPORT_LABELS == {
        "**What**": "What",
        "**Why now**": "Why now",
        "**User story**": "User story",
        "**Scope**": "The ticket must cover",
        "**Out of scope**": "Out of scope",
    }
    custom = import_labels_for(_M6_CUSTOM_LAYOUT)
    # A custom layout uses ONE label for both vocabularies — there is no legacy
    # rename to preserve for a section the customer just named.
    assert custom["**Summary**"] == "Summary"
    assert custom["**Acceptance owner**"] == "Acceptance owner"


def test_a_ticket_normalises_under_its_own_layout_not_the_companys_current_one():
    """A ticket pushed under last month's format keeps round-tripping.

    The layout is carried ON THE STORY, so changing the company's active format
    cannot retroactively break the sync for tickets already in the tracker."""
    old = _m6_story(
        description_layout=_M6_CUSTOM_LAYOUT,
        custom_sections={"acceptance_owner": "QA lead"},
    )
    _assert_round_trip_stable(old)
    # Normalising the same pushed text under the DEFAULT layout would not
    # recognise "**Summary**" — which is precisely why the story carries its own.
    from app.stories.sync import normalize_imported_description, story_editable_text

    wrong = normalize_imported_description(old.to_description(), None)
    assert wrong != story_editable_text(old)


def test_the_generated_tail_is_still_cut_under_a_custom_layout():
    # Acceptance criteria / child issues / provenance live as their own fields
    # in Sprntly and must never duplicate into the description on import.
    from app.stories.sync import normalize_imported_description

    s = _m6_story(
        description_layout=_M6_CUSTOM_LAYOUT,
        custom_sections={"acceptance_owner": "QA lead"},
    )
    out = normalize_imported_description(s.to_description(), s.description_layout)
    assert "Acceptance criteria" not in out
    assert "Child issues" not in out
    assert "Provenance" not in out


def test_scheduler_cycle_also_syncs_standalone_ticket_sets(
    isolated_settings, monkeypatch
):
    """A set-owned sync row is scheduler work exactly like a PRD-owned one.

    The row identifies its artifact by which owner column is populated, so a
    set row must NOT be skipped as "no prd_id" — the bug the old
    `if prd_id is None: continue` guard would have introduced silently, leaving
    standalone sets bound to a tracker but never auto-syncing."""
    from app.db.ticket_sync import upsert_sync_config
    from app.stories.scope import set_scope
    from app import scheduler as sched

    upsert_sync_config(CID, prd_scope(4), provider="clickup", destination_id="L4")
    upsert_sync_config(CID, set_scope(9), provider="jira", destination_id="KAN")

    ran: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "app.stories.sync.run_ticket_sync",
        lambda cid, scope: ran.append((scope.kind, scope.id))
        or {"pushed": 0, "push_errors": 0},
    )
    asyncio.run(sched._run_ticket_sync_cycle())
    assert sorted(ran) == [("prd", 4), ("set", 9)]


# ── the round trip for a PRD-LESS (chat-generated) ticket ────────────────────
#
# `main` grew tickets that exist without a PRD: a chat thread with no PRD open
# can now produce a real `ticket_sets` artifact, synced through the same engine
# under a `set` scope instead of a `prd` one.
#
# Those tickets go through the SAME `generate_user_stories(company_id, ...)`
# entry point, so `generate_from_input` resolves the company's active ticket
# format for them exactly as it does for a PRD's tickets — the format is a
# COMPANY-level artifact ("how our team writes tickets"), not a PRD-level one,
# so a company that uploaded one gets it on every ticket regardless of where the
# ticket came from. A company with no active format gets the default layout,
# which is every chat ticket today.
#
# What has to hold, and what these prove: the layout survives the ticket_sets
# storage round trip (same `stories` jsonb payload, same to_dict/from_dict), and
# the push -> normalise -> compare invariant is stable for a set-scoped ticket
# under both layouts. If it were not, the sync would report a phantom remote
# change on every chat ticket on every pass.


def _seed_chat_ticket_set(stories: list[dict]) -> int:
    """A finished, PRD-less ticket set holding `stories`, via the real writers."""
    from app.db.ticket_sets import create_set, finish_set

    set_id = create_set(CID, workspace_id=None, conversation_id=None,
                        source_text="generate tickets for the retry banner")
    finish_set(set_id, title="Retry banner", stories=stories)
    return set_id


def _round_trip_from_storage(set_id: int):
    """Read the set back the way the sync engine does and assert the invariant."""
    from app.stories.generate import Story
    from app.stories.scope import set_scope
    from app.stories.sync import (
        content_hash,
        normalize_imported_description,
        story_editable_text,
    )

    raw = set_scope(set_id).stories(CID)
    assert raw, "the set stored no stories"
    story = Story.from_dict(raw[0])

    pushed = story.to_description()
    normalised = normalize_imported_description(pushed, story.description_layout)
    local = story_editable_text(story)
    assert normalised == local, (
        "a chat ticket's push labels and editable labels disagree — this is the "
        "permanent phantom-diff bug, on the PRD-less path"
    )
    assert content_hash(story.title, normalised) == content_hash(story.title, local)
    return story


def test_a_chat_ticket_round_trips_under_the_default_layout(isolated_settings):
    set_id = _seed_chat_ticket_set([_m6_story().to_dict()])
    story = _round_trip_from_storage(set_id)
    # No company format ⇒ the default layout, and nothing extra is stored.
    assert story.description_layout is None
    assert story.to_description().startswith("**What**")


def test_a_chat_ticket_round_trips_under_a_custom_layout(isolated_settings):
    set_id = _seed_chat_ticket_set([
        _m6_story(
            description_layout=_M6_CUSTOM_LAYOUT,
            custom_sections={"acceptance_owner": "QA lead"},
        ).to_dict()
    ])
    story = _round_trip_from_storage(set_id)
    # The layout survived the ticket_sets storage round trip...
    assert story.description_layout == _M6_CUSTOM_LAYOUT
    assert story.custom_sections == {"acceptance_owner": "QA lead"}
    # ...and the company's own labels are what the tracker gets.
    assert "**Summary**" in story.to_description()
    assert "**What**" not in story.to_description()


def test_a_chat_ticket_gets_the_companys_active_format(isolated_settings, monkeypatch):
    """The PRD-less path reaches `resolve_ticket_layout` through the same
    `generate_from_input` a PRD's tickets do — it is keyed on the COMPANY, so
    there is no PRD to be missing."""
    import app.stories.generate as gen

    seen: dict = {}

    def _fake_resolve(company_id):
        seen["company_id"] = company_id
        return _M6_CUSTOM_LAYOUT, "tpl-1"

    monkeypatch.setattr(gen, "resolve_ticket_layout", _fake_resolve)
    monkeypatch.setattr(
        gen, "_generate_single",
        lambda *a, **k: [gen.Story(title="T", body="b", user_story="As a user…")],
    )

    stories = gen.generate_from_input(CID, prd_input="a chat brief, no PRD")

    assert seen["company_id"] == CID
    assert stories[0].description_layout == _M6_CUSTOM_LAYOUT

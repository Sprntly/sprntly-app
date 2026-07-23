"""Tests for the periodic connector-refresh scheduler job.

Before this slice, the scheduler ran only the brief-synthesis cycle —
which reads the KG but never refreshes it from upstream connectors. The
KG would go as stale as the user's last manual sync (or as their last
`kickoff_sync` at OAuth-connect time). Per CEO direction, refreshing
the corpus is option C-A of the home-chat-on-tools roadmap: the cheap
fix that turns the schedule on and refreshes connector data every
`pipeline_interval_hours` (default 6h).

This job is independent of `BRIEF_ENGINE` — it just calls
`kickoff_sync(company_id, provider)` for every (company × active
KG-puller-provider) pair.
"""
from __future__ import annotations

from unittest.mock import patch


def test_refresh_iterates_every_company_active_kg_puller_provider():
    """For each company, fire kickoff_sync for every active connection
    whose provider has a KG puller (clickup / hubspot / fireflies / github)."""
    from app.scheduler import _refresh_all_company_connectors

    companies = [
        {"id": "co-a", "slug": "acme", "display_name": "Acme"},
        {"id": "co-b", "slug": "globex", "display_name": "Globex"},
    ]
    # Each company has a different mix of connectors; we should see all
    # active puller-backed ones get a kickoff, nothing else.
    conns_by_company = {
        "co-a": [
            {"provider": "github",   "status": "active"},
            {"provider": "hubspot",  "status": "active"},
            {"provider": "figma",    "status": "active"},   # no puller → skipped
            {"provider": "slack",    "status": "active"},   # no puller → skipped
            {"provider": "clickup",  "status": "inactive"}, # inactive → skipped
        ],
        "co-b": [
            {"provider": "fireflies", "status": "active"},
            {"provider": "github",    "status": "active"},
        ],
    }

    with patch("app.scheduler.list_companies", return_value=companies), \
         patch(
            "app.scheduler.db.list_connections",
            side_effect=lambda cid: conns_by_company.get(cid, []),
         ), \
         patch("app.scheduler.kickoff_sync") as mock_kickoff:
        _refresh_all_company_connectors()

    calls = sorted({(c.args[0], c.args[1]) for c in mock_kickoff.call_args_list})
    assert calls == sorted([
        ("co-a", "github"),
        ("co-a", "hubspot"),
        ("co-b", "fireflies"),
        ("co-b", "github"),
    ])


def test_refresh_isolates_per_company_failures():
    """A db error or kickoff_sync raise for one company must not stop
    later companies in the cycle."""
    from app.scheduler import _refresh_all_company_connectors

    companies = [
        {"id": "co-bad", "slug": "broken"},
        {"id": "co-ok",  "slug": "good"},
    ]

    def conns(cid: str):
        if cid == "co-bad":
            raise RuntimeError("db down for this tenant")
        return [{"provider": "github", "status": "active"}]

    with patch("app.scheduler.list_companies", return_value=companies), \
         patch("app.scheduler.db.list_connections", side_effect=conns), \
         patch("app.scheduler.kickoff_sync") as mock_kickoff:
        _refresh_all_company_connectors()

    # co-ok still got its kickoff despite co-bad blowing up.
    assert mock_kickoff.call_args_list == [((("co-ok", "github")), {})] \
        or [(c.args[0], c.args[1]) for c in mock_kickoff.call_args_list] == [("co-ok", "github")]


def test_refresh_no_companies_is_a_clean_no_op():
    """Fresh deploys / empty databases shouldn't crash the scheduler."""
    from app.scheduler import _refresh_all_company_connectors

    with patch("app.scheduler.list_companies", return_value=[]), \
         patch("app.scheduler.kickoff_sync") as mock_kickoff:
        _refresh_all_company_connectors()

    mock_kickoff.assert_not_called()


def test_refresh_skips_providers_without_kg_pullers():
    """figma / slack have their own corpus-sync routes (or are per-user) —
    never fire kickoff_sync for them even if they're the company's only
    active connection."""
    from app.scheduler import _refresh_all_company_connectors

    companies = [{"id": "co-a", "slug": "acme"}]
    conns = [
        {"provider": "figma", "status": "active"},
        {"provider": "slack", "status": "active"},
    ]

    with patch("app.scheduler.list_companies", return_value=companies), \
         patch("app.scheduler.db.list_connections", return_value=conns), \
         patch("app.scheduler.kickoff_sync") as mock_kickoff:
        _refresh_all_company_connectors()

    mock_kickoff.assert_not_called()


def test_refresh_includes_google_drive():
    """google_drive has no token puller but IS wired for periodic refresh —
    kickoff_sync special-cases it (picked Drive files that change get
    re-pulled into corpus + KG)."""
    from app.scheduler import _refresh_all_company_connectors

    companies = [{"id": "co-a", "slug": "acme"}]
    conns = [
        {"provider": "google_drive", "status": "active"},
        {"provider": "figma",        "status": "active"},  # still skipped
    ]

    with patch("app.scheduler.list_companies", return_value=companies), \
         patch("app.scheduler.db.list_connections", return_value=conns), \
         patch("app.scheduler.kickoff_sync") as mock_kickoff:
        _refresh_all_company_connectors()

    assert [(c.args[0], c.args[1]) for c in mock_kickoff.call_args_list] == [
        ("co-a", "google_drive")
    ]


def test_start_scheduler_registers_refresh_job_when_enabled(monkeypatch):
    """When SCHEDULER_ENABLED=true, the connector-refresh job must be
    wired alongside the weekly-brief tick — distinct IDs so they show up
    separately in logs."""
    from app import scheduler as sched_mod

    monkeypatch.setattr(sched_mod.settings, "scheduler_enabled", True)
    monkeypatch.setattr(sched_mod.settings, "pipeline_interval_hours", 6)
    monkeypatch.setattr(sched_mod.settings, "weekly_brief_tick_minutes", 15)

    started: list = []

    class _FakeScheduler:
        def __init__(self):
            self.jobs: list[dict] = []

        def add_job(self, func, *, trigger=None, id=None, name=None, replace_existing=False):
            self.jobs.append({"func": func, "id": id, "name": name})

        def start(self):
            started.append(True)

        def shutdown(self, wait=False):
            pass

    fake = _FakeScheduler()
    monkeypatch.setattr(sched_mod, "AsyncIOScheduler", lambda: fake)

    sched_mod.start_scheduler()

    # Both jobs registered: weekly brief tick + connector refresh
    ids = sorted(j["id"] for j in fake.jobs)
    assert "weekly_brief_tick" in ids
    assert "refresh_connectors" in ids
    assert started == [True]

    sched_mod.shutdown_scheduler()

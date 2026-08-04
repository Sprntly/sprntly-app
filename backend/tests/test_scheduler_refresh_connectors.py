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
            {"provider": "slack",    "status": "active"},   # own company-level kick
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
         patch("app.scheduler.kickoff_slack_corpus_sync") as mock_slack, \
         patch("app.scheduler.kickoff_sync") as mock_kickoff:
        _refresh_all_company_connectors()

    calls = sorted({(c.args[0], c.args[1]) for c in mock_kickoff.call_args_list})
    assert calls == sorted([
        ("co-a", "github"),
        ("co-a", "hubspot"),
        ("co-b", "fireflies"),
        ("co-b", "github"),
    ])
    # Slack refreshes through its own company-level corpus kick, not
    # kickoff_sync — once for the company that has it.
    assert [c.args[0] for c in mock_slack.call_args_list] == ["co-a"]


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
    """figma has its own corpus-sync route — never fire kickoff_sync for it.
    slack refreshes through kickoff_slack_corpus_sync (company-level), never
    kickoff_sync."""
    from app.scheduler import _refresh_all_company_connectors

    companies = [{"id": "co-a", "slug": "acme"}]
    conns = [
        {"provider": "figma", "status": "active"},
        {"provider": "slack", "status": "active"},
    ]

    with patch("app.scheduler.list_companies", return_value=companies), \
         patch("app.scheduler.db.list_connections", return_value=conns), \
         patch("app.scheduler.kickoff_slack_corpus_sync") as mock_slack, \
         patch("app.scheduler.kickoff_sync") as mock_kickoff:
        _refresh_all_company_connectors()

    mock_kickoff.assert_not_called()
    assert [c.args[0] for c in mock_slack.call_args_list] == ["co-a"]


def test_refresh_kicks_slack_once_per_company_despite_many_installs():
    """Slack is per-user for delivery, so a company can hold several rows —
    but the voice-of-customer corpus sync is company-level: exactly ONE
    kick per company per cycle, however many members installed the bot."""
    from app.scheduler import _refresh_all_company_connectors

    companies = [{"id": "co-a", "slug": "acme"}]
    conns = [
        {"provider": "slack", "status": "active", "user_id": "u-pm"},
        {"provider": "slack", "status": "active", "user_id": "u-eng"},
        {"provider": "slack", "status": "revoked", "user_id": "u-old"},
    ]

    with patch("app.scheduler.list_companies", return_value=companies), \
         patch("app.scheduler.db.list_connections", return_value=conns), \
         patch("app.scheduler.kickoff_slack_corpus_sync") as mock_slack, \
         patch("app.scheduler.kickoff_sync") as mock_kickoff:
        _refresh_all_company_connectors()

    mock_kickoff.assert_not_called()
    assert [c.args[0] for c in mock_slack.call_args_list] == ["co-a"]


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
    wired alongside the top-insights tick — distinct IDs so they show up
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

    # Both jobs registered: Top Insights brief tick + connector refresh
    ids = sorted(j["id"] for j in fake.jobs)
    assert "brief_tick" in ids
    assert "refresh_connectors" in ids
    assert started == [True]

    sched_mod.shutdown_scheduler()


def test_refresh_also_tops_up_the_call_index_for_fireflies():
    """kickoff_sync fills the KG (distilled summaries); the call INDEX holds the
    per-call metadata chat answers listings from, and only the index can answer
    "which calls last week" without a ~168s corpus pass.

    Fireflies must get BOTH — the index kickoff is not a `continue`, because
    fireflies IS in PULLERS and still needs its KG pull.
    """
    from app.scheduler import _refresh_all_company_connectors

    companies = [{"id": "co-a", "slug": "acme", "display_name": "Acme"}]
    conns = {
        "co-a": [
            {"provider": "fireflies", "status": "active"},
            {"provider": "github", "status": "active"},
        ]
    }

    with patch("app.scheduler.list_companies", return_value=companies), \
         patch("app.scheduler.db.list_connections",
               side_effect=lambda cid: conns.get(cid, [])), \
         patch("app.scheduler.kickoff_slack_corpus_sync"), \
         patch("app.scheduler.kickoff_call_index_sync") as mock_index, \
         patch("app.scheduler.kickoff_sync") as mock_kickoff:
        _refresh_all_company_connectors()

    assert [c.args[0] for c in mock_index.call_args_list] == ["co-a"]
    # …and the KG pull still happened for fireflies alongside it.
    assert ("co-a", "fireflies") in {
        (c.args[0], c.args[1]) for c in mock_kickoff.call_args_list
    }


def test_a_company_without_fireflies_gets_no_index_kickoff():
    from app.scheduler import _refresh_all_company_connectors

    companies = [{"id": "co-a", "slug": "acme", "display_name": "Acme"}]
    conns = {"co-a": [{"provider": "github", "status": "active"}]}

    with patch("app.scheduler.list_companies", return_value=companies), \
         patch("app.scheduler.db.list_connections",
               side_effect=lambda cid: conns.get(cid, [])), \
         patch("app.scheduler.kickoff_slack_corpus_sync"), \
         patch("app.scheduler.kickoff_call_index_sync") as mock_index, \
         patch("app.scheduler.kickoff_sync"):
        _refresh_all_company_connectors()

    mock_index.assert_not_called()


def test_a_raising_index_kickoff_does_not_kill_the_cycle():
    """One tenant's failure must not stop the rest of the refresh."""
    from app.scheduler import _refresh_all_company_connectors

    companies = [
        {"id": "co-a", "slug": "acme", "display_name": "Acme"},
        {"id": "co-b", "slug": "globex", "display_name": "Globex"},
    ]
    conns = {
        "co-a": [{"provider": "fireflies", "status": "active"}],
        "co-b": [{"provider": "github", "status": "active"}],
    }

    with patch("app.scheduler.list_companies", return_value=companies), \
         patch("app.scheduler.db.list_connections",
               side_effect=lambda cid: conns.get(cid, [])), \
         patch("app.scheduler.kickoff_slack_corpus_sync"), \
         patch("app.scheduler.kickoff_call_index_sync",
               side_effect=RuntimeError("boom")), \
         patch("app.scheduler.kickoff_sync") as mock_kickoff:
        _refresh_all_company_connectors()

    assert ("co-b", "github") in {
        (c.args[0], c.args[1]) for c in mock_kickoff.call_args_list
    }

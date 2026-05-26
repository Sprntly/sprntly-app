"""Per-source monitors that turn external feeds into CompetitorSignal rows.

Each monitor is a subclass of `SourceMonitor` (see base.py) with a
single `check_for_new_signals(profile)` method. Callers iterate over a
list of monitors per profile; persistence is delegated to
`app.research.profile_service.record_signal`, which handles dedup.

P1.5 ships two working monitors (app store + changelog) and two
stubbed (jobs + social). The stubs return `[]` and log a TODO so the
digest pipeline can wire them in without code changes once the real
integrations land in P2.
"""
from app.research.monitors.app_store_monitor import AppStoreIOSMonitor
from app.research.monitors.base import SourceMonitor
from app.research.monitors.changelog_monitor import ChangelogMonitor
from app.research.monitors.jobs_monitor import JobsMonitor
from app.research.monitors.social_monitor import SocialMonitor


def default_monitors() -> list[SourceMonitor]:
    """The set of monitors run by `POST /v1/research/competitors/{id}/refresh`.

    Order matters only for log readability — each monitor is independent
    and dedup'd at persistence time.
    """
    return [
        AppStoreIOSMonitor(),
        ChangelogMonitor(),
        JobsMonitor(),
        SocialMonitor(),
    ]


__all__ = [
    "SourceMonitor",
    "AppStoreIOSMonitor",
    "ChangelogMonitor",
    "JobsMonitor",
    "SocialMonitor",
    "default_monitors",
]

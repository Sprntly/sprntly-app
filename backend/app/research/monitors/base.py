"""Abstract base for per-source monitors.

A monitor is a small object that knows how to fetch fresh data for a
single source (iTunes RSS, a changelog page, etc) and emit
`CompetitorSignalCreate` rows. The base class doesn't touch the DB —
persistence is the caller's job (typically `profile_service.record_signal`)
so monitors stay easy to unit-test against fixtures.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.research.profile import (
    CompetitorProfile,
    CompetitorSignalCreate,
)


class SourceMonitor(ABC):
    """Override `check_for_new_signals` to fetch + parse fresh signals."""

    #: Short identifier used in logs ("app_store_ios", "changelog", …).
    name: str = "base"

    @abstractmethod
    def check_for_new_signals(
        self,
        profile: CompetitorProfile,
    ) -> list[CompetitorSignalCreate]:
        """Return zero-or-more signals for `profile`.

        Implementations should be defensive: a network error, a parse
        failure, or a profile with no relevant URL configured should
        all return `[]` and log a warning rather than raise. The
        caller iterates over many monitors per profile; one failure
        shouldn't poison the whole refresh.
        """
        raise NotImplementedError

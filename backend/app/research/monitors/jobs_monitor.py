"""STUB monitor — job posting feeds.

Real implementations need either LinkedIn's restricted API or per-company
Greenhouse/Lever ATS integrations. Both are P2. Until then this returns
an empty list so callers can iterate over `default_monitors()` without
special-casing missing sources.
"""
from __future__ import annotations

import logging

from app.research.monitors.base import SourceMonitor
from app.research.profile import (
    CompetitorProfile,
    CompetitorSignalCreate,
)

logger = logging.getLogger(__name__)


class JobsMonitor(SourceMonitor):
    name = "jobs"

    def check_for_new_signals(
        self,
        profile: CompetitorProfile,
    ) -> list[CompetitorSignalCreate]:
        logger.info(
            "Job monitoring needs LinkedIn/Greenhouse integration (P2); "
            "skipping profile %s",
            profile.id,
        )
        return []

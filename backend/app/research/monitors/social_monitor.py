"""STUB monitor — social posts (Twitter/X, LinkedIn, etc).

Social APIs are gated and expensive; we'll wire one of the third-party
aggregators (Apify, ScrapingBee, …) in P2. For now this returns an
empty list and logs.
"""
from __future__ import annotations

import logging

from app.research.monitors.base import SourceMonitor
from app.research.profile import (
    CompetitorProfile,
    CompetitorSignalCreate,
)

logger = logging.getLogger(__name__)


class SocialMonitor(SourceMonitor):
    name = "social"

    def check_for_new_signals(
        self,
        profile: CompetitorProfile,
    ) -> list[CompetitorSignalCreate]:
        logger.info(
            "Social monitoring deferred (P2); skipping profile %s",
            profile.id,
        )
        return []

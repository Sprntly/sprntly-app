"""Research Agent — weekly competitive digest.

P0-7 (Synthesis_Agent_Spec §3.2 Step 4 / Brief §4 Competitive Pulse):
gathers cheap public signals about a workspace's competitors so the
weekly Brief can flag "what shipped or moved this week" without
operator intervention.

Phase-1 sources (this PR):
- App Store reviews (iOS public RSS/JSON; no API key)
- Changelog / blog / release-notes HTML scraping
- G2 / Capterra: stubbed (deferred to a paid scraper API)

The digest is best-effort: individual source failures (timeout, 4xx,
malformed HTML) degrade to "no signals" rather than raising. The
caller (Synthesis Agent / Brief renderer) treats a missing
CompetitorPulse as "no news this week" and silently drops the
section, which matches the spec's "Only if competitive connector
active" guard.
"""
from app.research.digest import generate_weekly_digest
from app.research.models import (
    ChangelogSignal,
    CompetitorPulse,
    CompetitiveDigest,
    ReviewSignal,
)

__all__ = [
    "generate_weekly_digest",
    "ChangelogSignal",
    "ReviewSignal",
    "CompetitorPulse",
    "CompetitiveDigest",
]

"""Per-source fetchers for the competitive digest.

Each module owns one source (app store reviews, changelogs, review
sites) and exposes a single `fetch_*` function that returns a list of
Signal models. Fetchers are network-best-effort: they swallow
exceptions, log, and return [] rather than propagating, because the
digest must produce a valid CompetitiveDigest even when the wider
internet is being weird.
"""
from app.research.sources.app_store import fetch_recent_reviews
from app.research.sources.changelog import fetch_recent_changelog_items
from app.research.sources.review_sites import fetch_g2_signals

__all__ = [
    "fetch_recent_reviews",
    "fetch_recent_changelog_items",
    "fetch_g2_signals",
]

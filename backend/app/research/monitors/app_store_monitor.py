"""iOS App Store reviews via the public iTunes RSS feed.

Apple exposes a JSON-encoded RSS feed at:

    https://itunes.apple.com/<country>/rss/customerreviews/id=<APPID>/sortBy=mostRecent/json

We extract the app ID from a configured `app_store_ios_url` (e.g.
`https://apps.apple.com/us/app/linear/id1500840122`), poll the feed,
and emit one CompetitorSignal per review entry.

The sibling P0-7 digest branch has a richer fetcher. To avoid merge
conflicts, this module uses an internal `fetch_recent_reviews` helper
that's trivially mockable in tests; the digest's version can replace
this later without touching the route or service layers.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

import requests

from app.research.monitors.base import SourceMonitor
from app.research.profile import (
    CompetitorProfile,
    CompetitorSignalCreate,
)

logger = logging.getLogger(__name__)


_APP_ID_RE = re.compile(r"/id(\d+)")
_DEFAULT_COUNTRY = "us"
_REQUEST_TIMEOUT_S = 10.0


def _extract_app_id(app_store_url: str) -> Optional[str]:
    m = _APP_ID_RE.search(app_store_url or "")
    return m.group(1) if m else None


def fetch_recent_reviews(
    app_id: str,
    country: str = _DEFAULT_COUNTRY,
) -> list[dict]:
    """Hit the iTunes RSS feed and return the raw entry dicts.

    Apple's feed wraps reviews under feed.entry[1:] (entry[0] is the
    app itself). We return the slice as-is so the monitor can map
    fields without us having to model Apple's shape statically.
    """
    url = (
        f"https://itunes.apple.com/{country}/rss/customerreviews/"
        f"id={app_id}/sortBy=mostRecent/json"
    )
    try:
        resp = requests.get(url, timeout=_REQUEST_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("App Store fetch failed for id=%s: %s", app_id, e)
        return []
    entries = (data.get("feed") or {}).get("entry") or []
    # First entry describes the app, not a review. Skip it.
    return entries[1:] if len(entries) > 1 else []


def _rating_to_sentiment(rating: int) -> str:
    if rating >= 4:
        return "positive"
    if rating <= 2:
        return "negative"
    return "neutral"


def _parse_review_entry(entry: dict) -> Optional[CompetitorSignalCreate]:
    """Turn one iTunes RSS entry into a CompetitorSignalCreate.

    Apple's JSON is verbose — each field is `{"label": "..."}`. We pull
    the labels defensively because Apple has been known to drop fields
    on certain locales.
    """
    try:
        title = (entry.get("title") or {}).get("label", "").strip()
        content = (entry.get("content") or {}).get("label", "").strip()
        rating_raw = (entry.get("im:rating") or {}).get("label", "0")
        rating = int(rating_raw) if rating_raw.isdigit() else 0
        link_entries = entry.get("link") or []
        if isinstance(link_entries, dict):
            link_entries = [link_entries]
        url = None
        for link in link_entries:
            href = (link.get("attributes") or {}).get("href")
            if href:
                url = href
                break
        updated = (entry.get("updated") or {}).get("label")
        if not updated:
            return None
        # Apple uses RFC 3339; fromisoformat handles it via the +00:00 swap.
        published_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        logger.debug("Skipping malformed App Store entry: %s", e)
        return None
    if not title:
        return None
    return CompetitorSignalCreate(
        source="app_store_ios",
        signal_type="review",
        title=title,
        body=content,
        url=url,
        sentiment=_rating_to_sentiment(rating),
        published_at=published_at,
        raw_payload_json={"rating": rating, "entry": entry},
    )


class AppStoreIOSMonitor(SourceMonitor):
    name = "app_store_ios"

    def check_for_new_signals(
        self,
        profile: CompetitorProfile,
    ) -> list[CompetitorSignalCreate]:
        if not profile.app_store_ios_url:
            return []
        app_id = _extract_app_id(profile.app_store_ios_url)
        if not app_id:
            logger.warning(
                "Could not extract app id from %s (profile %s)",
                profile.app_store_ios_url,
                profile.id,
            )
            return []
        entries = fetch_recent_reviews(app_id)
        signals: list[CompetitorSignalCreate] = []
        for entry in entries:
            parsed = _parse_review_entry(entry)
            if parsed is not None:
                signals.append(parsed)
        return signals

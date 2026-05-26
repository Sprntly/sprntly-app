"""App store review fetcher.

iOS: Apple exposes a public RSS feed that also serves JSON; no API key
or auth required, lightly rate-limited per IP. We hit:

    https://itunes.apple.com/us/rss/customerreviews/page=1/id={app_id}/sortby=mostrecent/json

and parse the standard `feed.entry[]` shape. Each entry has:

    {
      "im:rating": {"label": "5"},
      "title":     {"label": "..."},
      "content":   {"label": "..."},     # the review body
      "updated":   {"label": "ISO ts"},
      "author":    {"name": {"label": "..."}},  # not used
    }

The very first entry in Apple's feed is the app metadata itself (no
rating), not a review — we skip it.

Android (Google Play): Google doesn't publish an RSS/JSON feed for
reviews. Real options are (a) Play Console API for owned apps only, or
(b) third-party scraping services (SerpAPI, Apptopia, etc.) which cost
money. Phase-1 returns []; the function shape is preserved so the
digest aggregator can stay store-agnostic.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

import requests

from app.research.models import ReviewSignal

logger = logging.getLogger(__name__)

ITUNES_RSS_TEMPLATE = (
    "https://itunes.apple.com/us/rss/customerreviews/"
    "page=1/id={app_id}/sortby=mostrecent/json"
)
USER_AGENT = "Sprntly-Research-Agent/0.1"
HTTP_TIMEOUT = 15
# Apple's feed returns up to ~50 entries; cap the slice we surface to
# the digest at 10 so one chatty competitor can't dominate the brief.
MAX_REVIEWS = 10


def fetch_recent_reviews(
    app_id: str,
    store: Literal["ios", "android"],
    *,
    competitor: str | None = None,
) -> list[ReviewSignal]:
    """Pull the most recent reviews for an app.

    Args:
        app_id: Numeric iTunes app id ("284882215" for Facebook) or
                Play Store package id ("com.example.app").
        store:  "ios" or "android".
        competitor: Friendly label to attach to each ReviewSignal. If
                    None, falls back to the app_id string.

    Returns an empty list on any failure (network, parse, schema mismatch)
    and logs a warning. Never raises.
    """
    label = (competitor or app_id).strip() or app_id

    if store == "android":
        logger.warning(
            "Google Play needs a scraper or 3rd-party API; "
            "skipping reviews for %s (%s)",
            label,
            app_id,
        )
        return []

    if store != "ios":  # defensive: Literal already narrows, but logs are nicer
        logger.warning("Unknown store %r for %s — skipping", store, label)
        return []

    if not app_id or not app_id.strip():
        logger.warning("fetch_recent_reviews: empty app_id for %s", label)
        return []

    url = ITUNES_RSS_TEMPLATE.format(app_id=app_id.strip())
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("iTunes RSS fetch failed for %s: %s", label, e)
        return []

    if resp.status_code != 200:
        logger.warning(
            "iTunes RSS returned %d for %s (app_id=%s)",
            resp.status_code,
            label,
            app_id,
        )
        return []

    try:
        payload = resp.json()
    except ValueError as e:
        logger.warning("iTunes RSS JSON decode failed for %s: %s", label, e)
        return []

    return _parse_itunes_payload(payload, label)


def _parse_itunes_payload(payload: Any, competitor: str) -> list[ReviewSignal]:
    """Pull ReviewSignals out of the iTunes RSS-as-JSON envelope.

    Tolerant of missing keys: each entry that fails to parse is skipped
    individually so one malformed review doesn't drop the whole batch.
    """
    if not isinstance(payload, dict):
        return []
    feed = payload.get("feed")
    if not isinstance(feed, dict):
        return []
    entries = feed.get("entry")
    # When there are 0 reviews, Apple omits `entry` entirely. When there
    # is exactly 1 review, some endpoints return a dict instead of a
    # list — handle both.
    if entries is None:
        return []
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return []

    signals: list[ReviewSignal] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        # The first entry in Apple's feed is the app itself (no rating).
        # We detect that by the absence of `im:rating`.
        rating_raw = _label(entry.get("im:rating"))
        if rating_raw is None:
            continue
        try:
            rating = int(rating_raw)
        except (TypeError, ValueError):
            continue
        if not 1 <= rating <= 5:
            continue

        title = _label(entry.get("title")) or ""
        body = _label(entry.get("content")) or ""
        updated = _label(entry.get("updated")) or ""

        if not title.strip() or not updated.strip():
            # Required by ReviewSignal — skip rather than fudge.
            continue

        try:
            signal = ReviewSignal(
                competitor=competitor,
                store="ios",
                rating=rating,
                title=title,
                body=body,
                published_at=updated,
            )
        except Exception as e:  # pydantic ValidationError or similar
            logger.debug("Skipping unparseable review for %s: %s", competitor, e)
            continue
        signals.append(signal)
        if len(signals) >= MAX_REVIEWS:
            break

    return signals


def _label(field: Any) -> str | None:
    """Apple wraps almost every leaf in `{"label": "..."}`. Unwrap safely."""
    if isinstance(field, dict):
        val = field.get("label")
        if val is None:
            return None
        return str(val)
    return None

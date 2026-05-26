"""Weekly competitive digest assembler.

Glue layer that, for each competitor passed in, calls the three
per-source fetchers, gathers the results into a CompetitorPulse, then
produces a single CompetitiveDigest with 3–5 pre-rendered highlight
bullets ready for the Brief renderer.

Design notes:
- Source fetchers never raise — they degrade to [] on failure. The
  digest aggregator therefore doesn't need a try/except wall.
- "notable" is set rule-based, not via LLM. Rules (see
  _compute_notable): (a) any new changelog item this week, OR (b) a
  noticeable shift in review ratings (>= 2 reviews with rating <=2,
  or any 1-star review with the word "broken"/"crash"/"refund"). This
  is intentionally simple; a future PR can swap in an LLM-based
  sentiment classifier when we have budget.
- Highlight ranking is also rule-based: changelog items rank above
  reviews (a real feature ship is more newsworthy than a 1-star rant),
  and within each bucket we sort by recency (no date -> oldest).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.research.models import (
    ChangelogSignal,
    CompetitiveDigest,
    CompetitorPulse,
    ReviewSignal,
)
from app.research.sources import (
    fetch_g2_signals,
    fetch_recent_changelog_items,
    fetch_recent_reviews,
)

logger = logging.getLogger(__name__)

# Hard cap on the number of bullets the Brief's "Competitive Pulse"
# section can hold. Tied to the Synthesis Agent spec §4 ("3–5 highlight
# items"). If we lower this, the only consumer is the brief renderer.
MAX_HIGHLIGHTS = 5
MIN_HIGHLIGHTS = 3

# Keywords used to upgrade a 1-star review into "notable". Intentionally
# narrow — we don't want every grumpy review to set the flag.
_SEVERE_REVIEW_KEYWORDS = (
    "broken",
    "crash",
    "refund",
    "lost data",
    "data loss",
    "useless",
)


def generate_weekly_digest(
    workspace_id: str,
    competitors: list[dict[str, Any]],
) -> CompetitiveDigest:
    """Build a CompetitiveDigest for the given workspace + competitors.

    Args:
        workspace_id: Opaque identifier from the caller (dataset slug,
                      Supabase workspace UUID, etc.). Echoed back in
                      the digest so the Brief renderer doesn't need
                      separate lookup state.
        competitors:  List of {name, url, ios_app_id?, g2_slug?} dicts.
                      Only `name` is required. `url` is needed for the
                      changelog scraper. `ios_app_id` and `g2_slug` are
                      optional; their absence cleanly degrades to "no
                      signals from that source for this competitor".

    Returns a valid CompetitiveDigest even when every fetcher returned
    nothing (in which case pulses is empty-ish and top_highlights is
    a single "no notable competitive activity" line so the Brief
    section doesn't render blank).
    """
    generated_at = datetime.now(timezone.utc).isoformat()

    pulses: list[CompetitorPulse] = []
    for entry in competitors or []:
        pulse = _pulse_for_competitor(entry)
        if pulse is not None:
            pulses.append(pulse)

    highlights = _rank_highlights(pulses)
    if not highlights:
        highlights = ["No notable competitive activity this week."]

    return CompetitiveDigest(
        workspace_id=workspace_id,
        generated_at=generated_at,
        pulses=pulses,
        top_highlights=highlights[:MAX_HIGHLIGHTS],
    )


# --- per-competitor ---------------------------------------------------------


def _pulse_for_competitor(entry: dict[str, Any]) -> CompetitorPulse | None:
    """Build one CompetitorPulse from a competitor config dict.

    Returns None when the entry is so malformed we can't even get a
    name out of it — the digest then silently skips that row rather
    than blowing up with a ValidationError on the whole batch.
    """
    if not isinstance(entry, dict):
        logger.warning("competitor entry must be a dict, got %s", type(entry).__name__)
        return None

    name_raw = entry.get("name") or entry.get("competitor")
    if not name_raw or not str(name_raw).strip():
        logger.warning("competitor entry missing name; skipping (%r)", entry)
        return None
    name = str(name_raw).strip()

    url = (entry.get("url") or "").strip()
    ios_app_id = (entry.get("ios_app_id") or "").strip() or None
    g2_slug = (entry.get("g2_slug") or "").strip() or None

    # Fetchers swallow their own errors; we don't need a try/except
    # wall here. But if a *pydantic* coercion explodes later, we'd
    # rather log + skip the competitor than fail the whole digest.
    try:
        changelog_signals: list[ChangelogSignal] = (
            fetch_recent_changelog_items(url, competitor=name) if url else []
        )
        app_store_signals: list[ReviewSignal] = (
            fetch_recent_reviews(ios_app_id, "ios", competitor=name)
            if ios_app_id
            else []
        )
        review_signals: list[ReviewSignal] = fetch_g2_signals(name, g2_slug=g2_slug)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("digest fetch crashed for %s: %s", name, e)
        return CompetitorPulse(competitor_name=name)

    notable = _compute_notable(changelog_signals, app_store_signals, review_signals)

    return CompetitorPulse(
        competitor_name=name,
        app_store_signals=app_store_signals,
        changelog_signals=changelog_signals,
        review_signals=review_signals,
        notable=notable,
    )


def _compute_notable(
    changelog: list[ChangelogSignal],
    app_store: list[ReviewSignal],
    review_sites: list[ReviewSignal],
) -> bool:
    """Rule-based notable flag. See module docstring."""
    if changelog:
        # Any new changelog item is, by definition, a ship this week.
        return True

    bad_reviews = [r for r in (app_store + review_sites) if r.rating <= 2]
    if len(bad_reviews) >= 2:
        return True
    for r in bad_reviews:
        body_l = r.body.lower()
        title_l = r.title.lower()
        if r.rating == 1 and any(kw in body_l or kw in title_l for kw in _SEVERE_REVIEW_KEYWORDS):
            return True

    return False


# --- highlight ranking ------------------------------------------------------


def _rank_highlights(pulses: list[CompetitorPulse]) -> list[str]:
    """Build the 3–5 bullet list the Brief's Competitive Pulse renders."""
    bullets: list[str] = []

    # Changelog/blog shippages first — they're the most direct
    # competitive signal ("X shipped feature Y this week").
    changelog_items: list[tuple[ChangelogSignal, str]] = []
    for pulse in pulses:
        for item in pulse.changelog_signals:
            changelog_items.append((item, pulse.competitor_name))
    changelog_items.sort(key=lambda pair: pair[0].published_at or "", reverse=True)

    for item, competitor in changelog_items:
        bullets.append(_format_changelog_bullet(item, competitor))
        if len(bullets) >= MAX_HIGHLIGHTS:
            return bullets

    # Then notable review signals. Only surface 1- and 2-star reviews
    # — a single 5-star review isn't competitive intel.
    review_items: list[tuple[ReviewSignal, str]] = []
    for pulse in pulses:
        for r in pulse.app_store_signals + pulse.review_signals:
            if r.rating <= 2:
                review_items.append((r, pulse.competitor_name))
    review_items.sort(key=lambda pair: pair[0].published_at, reverse=True)

    for r, competitor in review_items:
        bullets.append(_format_review_bullet(r, competitor))
        if len(bullets) >= MAX_HIGHLIGHTS:
            return bullets

    # If we ended up with fewer than MIN_HIGHLIGHTS bullets, that's
    # fine — the caller fills the gap with "no notable activity".
    return bullets


def _format_changelog_bullet(item: ChangelogSignal, competitor: str) -> str:
    summary_part = f" — {item.summary}" if item.summary else ""
    return f"{competitor} ({item.source}): {item.title}{summary_part}"


def _format_review_bullet(r: ReviewSignal, competitor: str) -> str:
    snippet = r.body or r.title
    # Trim hard for bullet form — the body validator already capped
    # at 500 chars, but bullets need to stay one line.
    if len(snippet) > 140:
        snippet = snippet[:139].rstrip() + "…"
    return f"{competitor} ({r.store} review, {r.rating}★): {snippet}"

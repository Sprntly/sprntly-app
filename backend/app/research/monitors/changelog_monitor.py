"""Generic changelog scraper.

Most product changelog pages don't expose RSS, so we fetch the HTML
and extract entries heuristically:

  1. Prefer `<article>` blocks — that's the modern convention.
  2. Fall back to `<section>` or `<li>` elements that have a heading
     immediately followed by a date and a body.

Each entry becomes a CompetitorSignal(source='changelog'). The raw
HTML of the entry is stashed under `raw_payload_json.html` so we can
re-extract fields later if the heuristics improve.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional

import requests

from app.research.monitors.base import SourceMonitor
from app.research.profile import (
    CompetitorProfile,
    CompetitorSignalCreate,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_S = 10.0
# Common changelog date formats. Ordered most-specific first so e.g.
# "2026-05-26" doesn't get partially parsed as "2026".
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
)
_DATE_RE = re.compile(
    r"\b(?:"
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{4}/\d{2}/\d{2}"
    r"|[A-Za-z]+ \d{1,2},? \d{4}"
    r"|\d{1,2} [A-Za-z]+ \d{4}"
    r")\b"
)


class _ArticleExtractor(HTMLParser):
    """Pull text + raw HTML of every <article> element.

    For each article we collect:
      - the first <h1>/<h2>/<h3> text (treated as the title)
      - the full inner text
      - the raw HTML (stashed in raw_payload_json)
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._article_depth = 0
        self._heading_tag: Optional[str] = None
        self._capture_heading = False
        self._current_title: list[str] = []
        self._current_text: list[str] = []
        self._current_raw: list[str] = []
        self.articles: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag == "article":
            self._article_depth += 1
            if self._article_depth == 1:
                self._current_title = []
                self._current_text = []
                self._current_raw = []
            self._current_raw.append(self.get_starttag_text() or f"<{tag}>")
            return
        if self._article_depth > 0:
            self._current_raw.append(self.get_starttag_text() or f"<{tag}>")
            if tag in ("h1", "h2", "h3") and not self._current_title:
                self._heading_tag = tag
                self._capture_heading = True

    def handle_endtag(self, tag):
        if self._article_depth > 0:
            self._current_raw.append(f"</{tag}>")
            if tag == self._heading_tag:
                self._heading_tag = None
                self._capture_heading = False
        if tag == "article" and self._article_depth > 0:
            self._article_depth -= 1
            if self._article_depth == 0:
                self.articles.append({
                    "title": " ".join(self._current_title).strip(),
                    "text": " ".join(self._current_text).strip(),
                    "html": "".join(self._current_raw),
                })

    def handle_data(self, data):
        if self._article_depth == 0:
            return
        self._current_raw.append(data)
        clean = data.strip()
        if not clean:
            return
        if self._capture_heading:
            self._current_title.append(clean)
        self._current_text.append(clean)


def _parse_date(text: str) -> Optional[datetime]:
    """Find the first date-shaped substring in `text` and parse it."""
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    candidate = m.group(0).replace(",", "")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def fetch_changelog_html(url: str) -> str:
    try:
        resp = requests.get(url, timeout=_REQUEST_TIMEOUT_S)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        logger.warning("Changelog fetch failed for %s: %s", url, e)
        return ""


def extract_changelog_entries(html: str) -> list[dict]:
    """Pure function — parse HTML, return list of {title, text, html}.

    Exposed at module level so tests can exercise it without mocking
    `requests`.
    """
    if not html:
        return []
    parser = _ArticleExtractor()
    try:
        parser.feed(html)
    except Exception as e:  # pragma: no cover — HTMLParser rarely raises
        logger.warning("Changelog HTML parse failed: %s", e)
        return []
    return parser.articles


def _entry_to_signal(
    profile: CompetitorProfile,
    entry: dict,
) -> Optional[CompetitorSignalCreate]:
    title = entry.get("title") or ""
    text = entry.get("text") or ""
    if not title:
        return None
    published_at = _parse_date(text) or _parse_date(title)
    if not published_at:
        # No date found — skip rather than guess at today's date. The
        # digest only cares about dated events.
        return None
    return CompetitorSignalCreate(
        source="changelog",
        signal_type="release",
        title=title,
        body=text,
        url=profile.changelog_url,
        sentiment=None,
        published_at=published_at,
        raw_payload_json={"html": entry.get("html", "")},
    )


class ChangelogMonitor(SourceMonitor):
    name = "changelog"

    def check_for_new_signals(
        self,
        profile: CompetitorProfile,
    ) -> list[CompetitorSignalCreate]:
        if not profile.changelog_url:
            return []
        html = fetch_changelog_html(profile.changelog_url)
        entries = extract_changelog_entries(html)
        signals: list[CompetitorSignalCreate] = []
        for entry in entries:
            parsed = _entry_to_signal(profile, entry)
            if parsed is not None:
                signals.append(parsed)
        return signals

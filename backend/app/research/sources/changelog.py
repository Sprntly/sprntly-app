"""Changelog / blog / release-notes scraper.

We don't know where a given competitor publishes their changelog, so
we probe a small set of conventional paths and parse the first one
that responds with HTML. Order matters: dedicated `/changelog` and
`/releases` paths are higher signal than a general `/blog` (which
often surfaces marketing posts, not feature ships).

Extraction heuristic:
- Parse with BeautifulSoup.
- Prefer `<article>` elements, then top-level `<li>` items inside
  the main content, then `<section>`s with a date-looking string.
- For each candidate, extract a title (first h1/h2/h3, falling back
  to the first non-empty text) and a summary (next paragraph or the
  surrounding text, truncated by the Pydantic validator).
- Drop items with no title.

This is pattern-recognition, not magic — it works on most static
"What's new" pages and breaks on heavy SPAs. That's acceptable for
Phase-1: SPAs need a headless browser anyway, which we'd budget
separately.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from app.research.models import ChangelogSignal

logger = logging.getLogger(__name__)

USER_AGENT = "Sprntly-Research-Agent/0.1"
HTTP_TIMEOUT = 15
MAX_ITEMS = 5

# (path, source-label) — order = probe priority.
_CANDIDATE_PATHS: tuple[tuple[str, str], ...] = (
    ("/changelog", "changelog"),
    ("/releases", "changelog"),
    ("/release-notes", "changelog"),
    ("/whats-new", "changelog"),
    ("/blog", "blog"),
    ("/news", "press"),
    ("/press", "press"),
)

# Quick-and-dirty date matchers. We don't try to parse every
# locale — we just look for the most common shapes and convert to
# ISO-8601 when possible. None survives if nothing matches.
_DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # 2026-05-26
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"), "%Y-%m-%d"),
    # 2026/05/26
    (re.compile(r"\b(\d{4}/\d{2}/\d{2})\b"), "%Y/%m/%d"),
    # May 26, 2026
    (re.compile(r"\b([A-Z][a-z]+ \d{1,2},\s+\d{4})\b"), "%B %d, %Y"),
    # 26 May 2026
    (re.compile(r"\b(\d{1,2}\s+[A-Z][a-z]+\s+\d{4})\b"), "%d %B %Y"),
)


def fetch_recent_changelog_items(
    url: str,
    *,
    competitor: str | None = None,
) -> list[ChangelogSignal]:
    """Probe a competitor's site for changelog-shaped content.

    `url` can be either the bare domain (https://example.com) or a
    direct changelog URL (https://example.com/changelog). If a path
    is already present we honour it; otherwise we try the standard
    set of candidate paths.

    Always returns a list (possibly empty). Never raises.
    """
    if not url or not url.strip():
        return []
    url = url.strip().rstrip("/")
    label = (competitor or _domain_of(url)).strip() or url

    candidates = _candidate_urls(url)
    for candidate_url, source in candidates:
        html = _fetch_html(candidate_url)
        if not html:
            continue
        items = _extract_items(html, candidate_url, source, label)
        if items:
            return items[:MAX_ITEMS]
    return []


# --- internals --------------------------------------------------------------


def _candidate_urls(url: str) -> list[tuple[str, str]]:
    """If url already has a path, hit only it; else expand to the candidate set."""
    parsed = urlparse(url if "://" in url else "https://" + url)
    base = f"{parsed.scheme or 'https'}://{parsed.netloc or parsed.path}"
    path = parsed.path if parsed.netloc else ""
    if path and path not in ("/", ""):
        # The caller passed an explicit path. Use it; classify by
        # keyword for the `source` field.
        source = "changelog"
        lowered = path.lower()
        if "blog" in lowered:
            source = "blog"
        elif "news" in lowered or "press" in lowered:
            source = "press"
        return [(url, source)]
    return [(urljoin(base + "/", p.lstrip("/")), src) for p, src in _CANDIDATE_PATHS]


def _fetch_html(url: str) -> str | None:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"},
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        logger.debug("changelog fetch failed for %s: %s", url, e)
        return None
    if resp.status_code != 200:
        # 404 is the common case for the wrong candidate path; log at
        # debug to avoid spamming the production logs.
        logger.debug("changelog fetch %s returned %d", url, resp.status_code)
        return None
    ctype = resp.headers.get("Content-Type", "")
    if "html" not in ctype.lower() and "xml" not in ctype.lower():
        # PDFs, JSON, etc. — not our target.
        return None
    return resp.text


def _extract_items(
    html: str,
    page_url: str,
    source: str,
    competitor: str,
) -> list[ChangelogSignal]:
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        logger.warning("changelog parse failed for %s: %s", page_url, e)
        return []

    # Strip noise upfront so heuristics don't trip on <script>/<style>.
    for noisy in soup(("script", "style", "noscript")):
        noisy.decompose()

    items: list[ChangelogSignal] = []
    seen_titles: set[str] = set()

    for candidate in _candidate_blocks(soup):
        title = _extract_title(candidate)
        if not title:
            continue
        # De-dup on title within a single page — the same release
        # often appears in both a feed list and an article body.
        key = title.lower().strip()
        if key in seen_titles:
            continue
        seen_titles.add(key)

        summary = _extract_summary(candidate, title)
        item_url = _extract_link(candidate, page_url) or page_url
        published_at = _extract_date(candidate)

        try:
            signal = ChangelogSignal(
                competitor=competitor,
                source=source,
                title=title,
                url=item_url,
                published_at=published_at,
                summary=summary,
            )
        except Exception as e:
            logger.debug("Skipping unparseable changelog item: %s", e)
            continue
        items.append(signal)
        if len(items) >= MAX_ITEMS:
            break

    return items


def _candidate_blocks(soup: BeautifulSoup) -> Iterable[Tag]:
    """Yield likely changelog-item containers in priority order."""
    # 1. <article> — highest signal on real CMS-driven changelog pages.
    yield from soup.find_all("article")
    # 2. <li> items inside the main content area.
    main = soup.find("main") or soup.body or soup
    if isinstance(main, Tag):
        yield from main.find_all("li", recursive=True)
    # 3. <section>s — some hand-rolled changelog pages use these.
    yield from soup.find_all("section")


def _extract_title(block: Tag) -> str | None:
    for hn in ("h1", "h2", "h3", "h4"):
        tag = block.find(hn)
        if tag and tag.get_text(strip=True):
            return tag.get_text(strip=True)
    # Fall back to <a> text when the block doesn't have headings (some
    # changelog list pages render each row as a single styled link).
    link = block.find("a")
    if link and link.get_text(strip=True):
        text = link.get_text(strip=True)
        # Filter out single-word nav links ("Next", "Previous").
        if len(text.split()) >= 2:
            return text
    return None


def _extract_summary(block: Tag, title: str) -> str:
    # First <p> inside the block, falling back to the block's own
    # text. The Pydantic validator caps to 280 chars; we don't have
    # to truncate here.
    p = block.find("p")
    if p:
        text = p.get_text(" ", strip=True)
        if text and text != title:
            return text
    full = block.get_text(" ", strip=True)
    if full.startswith(title):
        full = full[len(title):].strip()
    return full


def _extract_link(block: Tag, base_url: str) -> str | None:
    link = block.find("a", href=True)
    if not link:
        return None
    href = link["href"]
    if not href:
        return None
    if isinstance(href, list):  # defensive — some parsers return lists
        href = href[0]
    return urljoin(base_url, str(href))


def _extract_date(block: Tag) -> str | None:
    """Find a date string within the block and normalise to ISO-8601.

    Returns None when nothing matches. Brief downstream treats None as
    "no date" and weighs accordingly.
    """
    # <time datetime="..."> is the gold standard; trust it.
    time_tag = block.find("time")
    if isinstance(time_tag, Tag):
        dt_attr = time_tag.get("datetime")
        if isinstance(dt_attr, str) and dt_attr.strip():
            return dt_attr.strip()
        text_inside = time_tag.get_text(strip=True)
        normalised = _try_parse_human_date(text_inside)
        if normalised:
            return normalised

    text = block.get_text(" ", strip=True)
    return _try_parse_human_date(text)


def _try_parse_human_date(text: str) -> str | None:
    if not text:
        return None
    for pattern, fmt in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        raw = m.group(1)
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        # Stamp UTC so the downstream brief renderer can compare
        # timestamps across sources without timezone surprises.
        return dt.replace(tzinfo=timezone.utc).isoformat()
    return None


def _domain_of(url: str) -> str:
    parsed = urlparse(url if "://" in url else "https://" + url)
    return parsed.netloc or url

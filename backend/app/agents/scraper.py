"""Shared web scraping utilities for intelligence agents.

Uses httpx + BeautifulSoup for lightweight scraping. No paid APIs —
all data comes from public web pages and DuckDuckGo HTML search.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from app.config import settings

logger = logging.getLogger(__name__)

# Rate limiter: max 3 concurrent requests to avoid hammering targets
_SCRAPE_SEMA = asyncio.Semaphore(3)

# Shared httpx client config
_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_HEADERS = {
    "User-Agent": getattr(settings, "scraping_user_agent", "Sprntly/1.0"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


async def fetch_page(url: str, max_chars: int = 50_000) -> str:
    """Fetch a URL and return its text content.

    Returns the extracted text (no HTML tags). Empty string on failure.
    """
    async with _SCRAPE_SEMA:
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                headers=_HEADERS,
                follow_redirects=True,
                max_redirects=5,
            ) as client:
                resp = await client.get(url)
                if resp.status_code >= 400:
                    logger.warning("Scrape %s returned %d", url, resp.status_code)
                    return ""
                html = resp.text[:200_000]  # cap raw HTML size
                return extract_text(html)[:max_chars]
        except Exception as exc:
            logger.warning("Scrape failed for %s: %s", url, exc)
            return ""


def extract_text(html: str) -> str:
    """Extract readable text from HTML, stripping nav/footer/script."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content elements
    for tag in soup.find_all(
        ["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe"]
    ):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)

    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def search_ddg(
    query: str,
    max_results: int = 10,
) -> list[dict[str, str]]:
    """Search DuckDuckGo HTML and return results.

    Returns list of {title, url, snippet} dicts. No API key needed.
    """
    search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    results: list[dict[str, str]] = []

    async with _SCRAPE_SEMA:
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                headers=_HEADERS,
                follow_redirects=True,
            ) as client:
                resp = await client.get(search_url)
                if resp.status_code >= 400:
                    logger.warning("DuckDuckGo search failed: %d", resp.status_code)
                    return []
                soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            logger.warning("DuckDuckGo search error: %s", exc)
            return []

    for result_div in soup.find_all("div", class_="result"):
        if len(results) >= max_results:
            break

        title_tag = result_div.find("a", class_="result__a")
        snippet_tag = result_div.find("a", class_="result__snippet")

        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        href = title_tag.get("href", "")
        snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

        if title and href:
            results.append({
                "title": title,
                "url": href,
                "snippet": snippet,
            })

    return results


async def scrape_multiple(urls: list[str], max_chars_per: int = 30_000) -> dict[str, str]:
    """Scrape multiple URLs concurrently. Returns {url: text} mapping."""
    tasks = [fetch_page(url, max_chars=max_chars_per) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    output: dict[str, str] = {}
    for url, result in zip(urls, results):
        if isinstance(result, str) and result:
            output[url] = result
        else:
            output[url] = ""
    return output

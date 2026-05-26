"""Scenario B — Style inference from a public website (no Figma).

Spec source: Design_Agent_Spec.docx §3.B (no-Figma scenario), §6
(design-token extraction).

We fetch the URL, parse with BeautifulSoup, and extract a rough set of
design tokens (colors + fonts) from inline + linked styles. Anything we
can't infer falls back to neutral defaults.

The real generator inspects the rendered DOM (Playwright) — this stub
is static HTML only. Sufficient to ground the JSON skeleton + keep the
KG / route contract honest for the POC.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# Hex/rgb/named colors that show up in inline style="..." + <link> CSS.
_COLOR_RE = re.compile(
    r"(?:#[0-9a-fA-F]{3,8})|(?:rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(?:,\s*[\d.]+\s*)?\))"
)

# Quoted + unquoted font families on font-family: declarations.
# We capture everything up to `;` / `}` / newline — the cleanup step
# strips quotes and picks the first family in the list.
_FONT_FAMILY_RE = re.compile(
    r"font-family\s*:\s*([^;}\n]+)", re.IGNORECASE
)


def generate_from_website(
    url: str,
    prd_content: dict[str, Any],
    fetcher: Optional[Callable[[str], str]] = None,
) -> dict[str, Any]:
    """Build the prototype skeleton by sniffing a public website.

    Args:
        url: Fully-qualified URL of the team's site (or any reference site).
        prd_content: Parent PRD payload — summarized into meta.
        fetcher: Override the HTTP fetch (defaults to requests.get).
            Test seam — the unit tests pass a closure returning fixture
            HTML so no network round-trip is needed.

    Returns:
        JSON skeleton with the inferred style tokens. Falls back to
        defaults on fetch / parse failure so the lifecycle keeps moving.
    """
    skeleton: dict[str, Any] = {
        "pages": [{"id": "home", "name": "Home", "frames": []}],
        "components": [],
        "style": {"colors": [], "fonts": []},
        "meta": {
            "scenario": "website",
            "source": url,
            "prd_summary": _prd_summary(prd_content),
            "generator_version": "poc-0.1",
        },
    }

    try:
        html = (fetcher or _default_fetcher)(url)
    except Exception:
        logger.exception("Website fetch failed for url=%s", url)
        skeleton["meta"]["degraded"] = True
        skeleton["meta"]["error"] = "website_fetch_failed"
        return skeleton

    try:
        colors, fonts, title = _extract_tokens(html)
    except Exception:
        # BS4 failure on truly malformed HTML — we still want a skeleton.
        logger.exception("Website parse failed for url=%s", url)
        skeleton["meta"]["degraded"] = True
        skeleton["meta"]["error"] = "website_parse_failed"
        return skeleton

    skeleton["style"]["colors"] = colors
    skeleton["style"]["fonts"] = fonts
    if title:
        skeleton["pages"][0]["name"] = title
    skeleton["meta"]["site_title"] = title
    return skeleton


def _default_fetcher(url: str) -> str:
    """Live HTTP fetch. Imported lazily so unit tests that pass a fake
    fetcher don't need requests configured."""
    import requests
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


def _extract_tokens(html: str) -> tuple[list[str], list[str], str]:
    """Pull colors + fonts + <title> from the HTML.

    Deduped + capped (top 12 colors, top 6 fonts) so the skeleton stays
    small. Order is preserved — earliest-seen wins, which roughly tracks
    visual prominence on most landing pages.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.find("title")
    title = title_el.get_text(strip=True) if title_el else ""

    # Gather inline styles + <style> blocks + linked stylesheets we can see.
    raw_css_chunks: list[str] = []
    for tag in soup.find_all(style=True):
        raw_css_chunks.append(tag.get("style", ""))
    for style_block in soup.find_all("style"):
        raw_css_chunks.append(style_block.get_text())
    css_blob = "\n".join(raw_css_chunks)

    colors = _dedup_capped(_COLOR_RE.findall(css_blob), cap=12)
    fonts = _dedup_capped(
        [_clean_font(m) for m in _FONT_FAMILY_RE.findall(css_blob)],
        cap=6,
    )
    return colors, fonts, title


def _clean_font(value: str) -> str:
    """Normalize a font-family declaration to its first family.

    `font-family: "Inter", system-ui, sans-serif` → `Inter`
    """
    first = value.split(",")[0].strip()
    return first.strip("'\" ")


def _dedup_capped(values: list[str], cap: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        v = v.strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
        if len(out) >= cap:
            break
    return out


def _prd_summary(prd_content: dict[str, Any]) -> str:
    if not isinstance(prd_content, dict):
        return ""
    title = str(prd_content.get("title") or "")
    body = str(prd_content.get("payload_md") or prd_content.get("body") or "")
    return f"{title}: {body[:200]}".strip(": ").strip()


__all__ = ["generate_from_website"]

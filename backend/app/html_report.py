"""Server-side reading of our own HTML reports.

Three of the chat skills answer with a complete, self-contained HTML document
(voice-of-customer-report, public-feedback-report,
competitive-intelligence-review). The web client detects that with
`looksLikeHtmlBrief` and renders it in a sandboxed iframe — but Slack has no
iframe. Posting the document as message text there produces a wall of CSS, and
Slack truncates it, so the delivery path needs to (a) recognise an HTML answer
and (b) say something useful about it in plain text before attaching the file.

`looks_like_html_report` is the server-side port of the frontend predicate (same
tag list, same fence stripping, deliberately kept in step). `summarize_report`
reads the document's own opening — the title and the lead paragraphs the skill
already wrote for a reader with fifteen minutes — rather than asking a model to
re-summarize what was just synthesized. `report_metadata` reads the inert
`report-metadata` JSON block the renderers emit.

Everything here is defensive: these run inside a fire-and-forget Slack task, so
a malformed document must degrade to a shorter message, never raise.
"""
from __future__ import annotations

import html as html_mod
import json
import logging
import re

logger = logging.getLogger(__name__)

# Mirrors web/app/lib/htmlBrief.ts stripHtmlCodeFence + looksLikeHtmlBrief.
_FENCE = re.compile(r"^\s*```(?:html)?\s*\n(.*?)\n?```\s*$", re.S | re.I)
_HTML_START = re.compile(r"^\s*<(?:!doctype|meta|html|div|style)\b", re.I)

_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
# The lead prose, in the order the renderers use it: the CIR renderer's
# `.opener` divs, the public-feedback template's `.intro`, then any paragraph.
_LEAD_BLOCKS = (
    re.compile(r'<div class="opener"[^>]*>(.*?)</div>', re.S | re.I),
    re.compile(r'<p class="intro"[^>]*>(.*?)</p>', re.S | re.I),
    re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I),
)
_METADATA_BLOCK = re.compile(
    r'<script type="application/json" id="report-metadata">(.*?)</script>',
    re.S,
)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html_fence(payload: str | None) -> str:
    """Drop a wrapping ```html fence so a fenced document still reads as one."""
    text = payload or ""
    m = _FENCE.match(text)
    return m.group(1) if m else text


def looks_like_html_report(payload: str | None) -> bool:
    """True when an answer IS a self-contained HTML document rather than prose.

    Server-side port of the frontend's `looksLikeHtmlBrief`; keep the tag list
    in step with web/app/lib/htmlBrief.ts.
    """
    return bool(_HTML_START.match(strip_html_fence(payload)))


def _text(fragment: str) -> str:
    """Tags out, entities decoded, whitespace collapsed."""
    return _WS.sub(" ", html_mod.unescape(_TAGS.sub(" ", fragment or ""))).strip()


def report_metadata(payload: str | None) -> dict:
    """The renderers' inert `report-metadata` JSON block, or {}."""
    m = _METADATA_BLOCK.search(payload or "")
    if not m:
        return {}
    try:
        parsed = json.loads(m.group(1).replace("\\u003c", "<"))
    except (ValueError, TypeError):
        logger.debug("report-metadata block was not valid JSON", exc_info=True)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def summarize_report(payload: str | None, *, limit: int = 1200) -> str:
    """A plain-text summary of an HTML report for a text-only surface.

    Uses the document's own title and opening prose — the findings the skill
    wrote for a reader with fifteen minutes — plus the window from the metadata
    block when it carries one. Returns "" when nothing readable can be pulled,
    so the caller can fall back to a generic line rather than posting an empty
    message.
    """
    doc = strip_html_fence(payload)
    if not doc:
        return ""
    title = ""
    m = _H1.search(doc)
    if m:
        title = _text(m.group(1))
    leads: list[str] = []
    for pattern in _LEAD_BLOCKS:
        for frag in pattern.findall(doc):
            text = _text(frag)
            # Skip the template's own scaffolding lines (short labels, notes).
            if len(text) < 40:
                continue
            if text not in leads:
                leads.append(text)
            if len(leads) == 2:
                break
        if leads:
            break
    window = str(report_metadata(doc).get("window") or "").strip()

    parts: list[str] = []
    if title:
        parts.append(f"*{title}*")
    if window:
        parts.append(f"_{window}_")
    parts.extend(leads)
    summary = "\n\n".join(parts).strip()
    if len(summary) > limit:
        summary = summary[: limit - 1].rstrip() + "…"
    return summary

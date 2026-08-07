"""Normalise a model-written evidence brief to the stored render contract.

THE CONTRACT this enforces (what every consumer of `evidences.payload_md`
depends on, and what breaks when it slips):

  1. The payload's first non-whitespace character is `<`, and specifically the
     start of one of `<!doctype` / `<meta` / `<html` / `<div` / `<style`.
     `web/app/lib/htmlBrief.ts::looksLikeHtmlBrief` is an ANCHORED sniff, and
     `markdownToEvidenceState` branches on it alone — with no variant fallback.
     One sentence of preamble ("Here is the brief:") therefore does not degrade
     the brief, it routes the whole document into the legacy `:::block` markdown
     parser, which yields zero sections: a BLANK Evidence panel in the artifact
     panel, the Artifacts screen and every share link. The full-page
     EvidenceScreen survives on `variant === "v3"`; nothing else does.
  2. The document carries the canonical stylesheet, spliced in server-side
     (`app.html_style.inject_canonical_css`) over the empty `<style>` marker the
     model emits.
  3. No `<script>`. The viewer renders the brief in an iframe with
     `sandbox="allow-same-origin"` and NO `allow-scripts`, so a script could
     never run — but the payload is also read by non-rendering consumers (the
     MCP `get_prd_evidence` tool, the PRD-tab chat context, the downstream
     QA/technical-design/risk agents), and stripping it here keeps the stored
     artifact honest for all of them. The sandbox is the boundary; this is
     defence in depth on top of it, never a substitute.

Moving the brief's CONTENT from the vendored skill to the prompt (evidence-kg-v6)
makes the model freer in prose, which makes rule 1 more likely to be brushed —
so it stops being a hope pinned to a prompt line and becomes a normalisation
step with a loud failure. A document with no HTML in it at all raises, so the
row lands in `failed` (which the UI already surfaces with an explicit retry)
rather than storing prose that renders as an empty page.
"""
from __future__ import annotations

import logging
import re

from app.html_style import inject_canonical_css
from app.llm import strip_code_fence

logger = logging.getLogger(__name__)

# The opening tags `looksLikeHtmlBrief` accepts, ANCHORED exactly as the
# frontend anchors them. Kept in sync with web/app/lib/htmlBrief.ts — that sniff
# is the whole reason this module exists.
_SNIFF_RE = re.compile(r"^\s*<(?:!doctype|meta|html|div|style)\b", re.IGNORECASE)
# Any markup at all — where the document actually starts, whatever tag it is.
_ANY_TAG_RE = re.compile(r"<[A-Za-z!/]")
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.DOTALL | re.IGNORECASE)
_BARE_SCRIPT_RE = re.compile(r"<script\b[^>]*/?>", re.IGNORECASE)


class EvidenceHtmlError(RuntimeError):
    """The model's output contains no HTML document to store."""


def _strip_preamble_and_trailer(html: str) -> str:
    """Drop anything the model wrote outside the document.

    Leading prose is the dangerous half (rule 1 above). Trailing prose is
    cosmetic — it renders as stray text under the brief — and is only removed
    when it is unambiguous: plain text after the final `>` with no markup in it.
    """
    m = _ANY_TAG_RE.search(html)
    if not m:
        raise EvidenceHtmlError(
            "model output contains no HTML at all — the evidence brief must be "
            f"an HTML document (first 120 chars: {html.strip()[:120]!r})"
        )
    if m.start() > 0:
        logger.warning(
            "evidence: dropped %d chars of preamble before the document (%r)",
            m.start(), html[: m.start()].strip()[:120],
        )
        html = html[m.start():]
    tail_start = html.rfind(">")
    tail = html[tail_start + 1:]
    if tail.strip() and "<" not in tail:
        logger.warning("evidence: dropped trailing commentary (%r)", tail.strip()[:120])
        html = html[: tail_start + 1]
    return html


def normalize_evidence_html(raw: object, css: str) -> str:
    """Return the stored `payload_md` for a generated evidence brief.

    Strips a wrapping code fence, removes anything outside the document, drops
    any `<script>`, and splices in the canonical stylesheet. Raises
    `EvidenceHtmlError` when there is no document to store — the caller lets that
    fail the row rather than persisting something that renders blank.
    """
    text = raw if isinstance(raw, str) else str(raw)
    html = _strip_preamble_and_trailer(strip_code_fence(text))

    if _SCRIPT_RE.search(html) or _BARE_SCRIPT_RE.search(html):
        logger.warning("evidence: stripped <script> from a generated brief")
        html = _BARE_SCRIPT_RE.sub("", _SCRIPT_RE.sub("", html))

    # The model emits an EMPTY <style>; inject the canonical stylesheet so the
    # stored brief is self-contained and every brief shares one design system.
    # This also FIXES most sniff failures on its own: a document that opens with
    # some other tag (`<section>`, `<h1>`) and carries no <style>/</head> gets
    # the canonical block PREPENDED, so the payload then starts with `<style`.
    html = inject_canonical_css(html, css)

    if not _SNIFF_RE.match(html):
        # Whatever is left opens with a tag the frontend sniff does not accept
        # (e.g. `<section>…<style>…`). Guarantee rule 1 rather than hope for it:
        # a charset meta is inert, and it is what the contract asks for anyway.
        logger.warning(
            "evidence: document opened with an unrecognised tag (%r) — prefixing "
            "<meta> so the payload satisfies looksLikeHtmlBrief",
            html.lstrip()[:60],
        )
        html = '<meta charset="utf-8">\n' + html

    if 'class="wrap"' not in html and "class='wrap'" not in html:
        # Not fatal: the document still renders, just full-bleed and unpadded
        # (`.wrap` is what the stylesheet and the viewer's width override both
        # key on). Worth a log line because it means the model left the contract.
        logger.warning("evidence: generated brief has no .wrap container")
    return html

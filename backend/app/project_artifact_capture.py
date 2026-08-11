"""Capture a chat output as a durable `reports` artifact, on a project.

Sibling of `report_capture.py` (same target table, same `save_report`
writer), but a DIFFERENT trigger and a DIFFERENT failure contract:

  - `report_capture.capture_report` runs AFTER an ask finishes and is
    BEST-EFFORT — it swallows everything so a degraded artifacts library
    never surfaces as a failed answer the user already read.
  - `save_chat_output_as_report` runs because a USER clicked "save" on a
    chat output they are looking at right now. There is no answer to
    protect, so nothing here swallows: a DB error propagates to the
    caller (the route turns it into a 500) and a `None` return (the
    insert yielded no row) is surfaced as an explicit 502 — the button
    that triggered this needs to know the save failed.

Chat outputs are markdown, and there is no server-side markdown→HTML
renderer in this codebase (no `markdown`/`markdown-it`/`bleach` dependency).
`_wrap_as_report_html` is v1's bridge: the raw content, HTML-escaped and
preserved in a `<pre>`, inside a minimal self-contained document so it
satisfies `report_capture._HTML_DOC_RE` / `looks_like_html_report()` and
renders app-faithfully in the existing `HtmlReportView` iframe — byte
faithful, XSS-safe, zero new dependency. Rich markdown rendering (headings,
lists, ...) is a deliberately deferred follow-up, not this ticket.
"""
from __future__ import annotations

import html

_TITLE_MAX = 200  # matches report_capture._TITLE_MAX

# The skill id this capture path badges every saved chat output with —
# `humanize_label("saved-chat")` renders it as the "Saved chat" badge on
# the artifacts list, distinguishing a user-saved item from a
# pipeline-generated report (voice-of-customer-report, etc).
_SAVED_CHAT_SKILL = "saved-chat"

_SAVED_CHAT_STYLE = (
    "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
    "margin:0;padding:2rem;color:#1a1a1a;background:#fff}"
    ".saved-chat{max-width:760px;margin:0 auto}"
    ".saved-chat h1{font-size:1.5rem;margin:0 0 1rem}"
    ".saved-chat-body{white-space:pre-wrap;word-wrap:break-word;"
    "font-family:inherit;font-size:1rem;line-height:1.6}"
)


def _derive_title(content: str, explicit: str | None) -> str:
    """An explicit non-empty title wins; else the first non-empty line of
    `content` with any leading markdown heading marks stripped; else a
    fixed fallback. Always capped to `_TITLE_MAX`."""
    if explicit and explicit.strip():
        return explicit.strip()[:_TITLE_MAX]
    for line in content.splitlines():
        stripped = line.lstrip("#").strip()
        if stripped:
            return stripped[:_TITLE_MAX]
    return "Saved from chat"


def _wrap_as_report_html(title: str, body: str) -> str:
    """A self-contained HTML document wrapping `body` — starts with
    `<!doctype html` so `report_capture.looks_like_html_report()` matches
    and `HtmlReportView` renders it. `title` and `body` are both
    `html.escape`'d: `body` is preserved verbatim (byte-faithful, no
    markdown rendering) inside a `<pre>`, so a `<script>` in the saved
    content is stored and rendered as inert escaped text, never a live
    tag."""
    escaped_title = html.escape(title)
    escaped_body = html.escape(body)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{escaped_title}</title>"
        f"<style>{_SAVED_CHAT_STYLE}</style></head><body>"
        f'<article class="saved-chat"><h1>{escaped_title}</h1>'
        f'<pre class="saved-chat-body">{escaped_body}</pre></article>'
        "</body></html>"
    )


def save_chat_output_as_report(
    *,
    content: str,
    company_id: str,
    title: str | None = None,
    workspace_id: str | None = None,
    conversation_id: int | None = None,
) -> int | None:
    """Persist `content` (a chat output) as a `reports` row and return its
    id, or `None` when the insert yielded no row.

    NOT best-effort: this is a user-initiated save, so a raised DB error
    is left to propagate (the route surfaces it as a 500) rather than
    swallowed. `ask_id` is deliberately never set — this capture has no
    originating ask — so the `reports_ask_id_uniq` partial unique index
    (which excludes NULL `ask_id`) never collides across saved-chat rows.
    """
    doc_title = _derive_title(content, title)

    from app import db

    return db.save_report(
        company_id,
        skill=_SAVED_CHAT_SKILL,
        title=doc_title,
        html=_wrap_as_report_html(doc_title, content),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )

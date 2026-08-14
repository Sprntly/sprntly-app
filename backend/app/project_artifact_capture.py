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

Chat outputs are markdown, and `reports.html` is stored and read RAW —
verbatim `content`, no wrapping, no escaping. This capture writes the ONE
skill id (`_SAVED_CHAT_SKILL`) every reader routes on to render it
correctly:

  - `web/app/components/shared/ReportsTab.tsx` and
    `web/app/r/PublicReportViewer.tsx` (the in-app and the public `/r/<token>`
    viewers) both check `skill == "saved-chat"` and, when true, render the
    stored text as markdown via `SavedChatMarkdown`
    (`<ReactMarkdown remarkPlugins={[remarkGfm]}>`, no `rehype-raw`) instead
    of handing it to `HtmlReportView`'s sandboxed iframe, which expects a
    real HTML document.
  - XSS safety lives at that render boundary, not here: react-markdown
    without `rehype-raw` never executes embedded HTML — a `<script>` in the
    stored markdown prints as inert text — so storage does not need to
    escape or wrap anything (v1's `_wrap_as_report_html` HTML-escaped-`<pre>`
    bridge is retired; this module now writes exactly what the user saved).

Every OTHER report (VoC, competitive-intelligence, ...) is still a
self-contained HTML document from `report_capture.capture_report`, and
still renders through `HtmlReportView` unchanged — this module only ever
writes the one `saved-chat` skill.
"""
from __future__ import annotations

_TITLE_MAX = 200  # matches report_capture._TITLE_MAX

# The skill id this capture path badges every saved chat output with —
# `humanize_label("saved-chat")` renders it as the "Saved chat" badge on
# the artifacts list, distinguishing a user-saved item from a
# pipeline-generated report (voice-of-customer-report, etc), and is the
# discriminator every reader (`ReportsTab.tsx`, `PublicReportViewer.tsx`)
# checks to render markdown instead of the HTML-document iframe.
_SAVED_CHAT_SKILL = "saved-chat"


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

    `content` is stored VERBATIM in `reports.html` — raw markdown, not an
    HTML document (see the module docstring for why that's safe: rendering,
    not storage, is where XSS is prevented for this skill). NOT best-effort:
    this is a user-initiated save, so a raised DB error is left to propagate
    (the route surfaces it as a 500) rather than swallowed. `ask_id` is
    deliberately never set — this capture has no originating ask — so the
    `reports_ask_id_uniq` partial unique index (which excludes NULL `ask_id`)
    never collides across saved-chat rows.
    """
    doc_title = _derive_title(content, title)

    from app import db

    return db.save_report(
        company_id,
        skill=_SAVED_CHAT_SKILL,
        title=doc_title,
        html=content,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )

"""The artifacts THIS CHAT produced, as grounding — "ARTIFACTS IN THIS CHAT".

`prd_context` and `artifact_context` each ground ONE document, and only when
the client names its id: a PRD tab sends `prd_id`, an evidence tab
`evidence_id`, a ticket-set tab `ticket_set_id`. Everything else a thread makes
— a Voice-of-Customer report, a competitive-intelligence report, a document
written from chat — had no builder and no id to send, so it was never in the
prompt at all.

The reported failure is what that looks like from the outside. With a report
open in the panel, "summarize the report in just one paragraph" was answered
from `user_feedback_raw_2026_07_21_to_2026_07_26` — a corpus file covering a
different month, with different themes, from the report on screen. A follow-up
("how many themes are in the report?") then confidently counted the themes of
the wrong document. Nothing was misread; the report had never been shown.

History is not a substitute, and that is the whole reason this module exists
rather than a bigger history budget: `prompt_history.clamp_turn_text` caps
every folded turn at 4k characters so a report carrying base64 charts cannot
400 the rest of the thread. For a report that budget is spent on the run line
and the executive summary — the recommendations are at the BOTTOM and never
survive the clamp.

THREE RULES, in the order they matter:

1. THE THREAD IS THE BOUNDARY. Everything here is keyed on `conversation_id`.
   A report from another chat, however relevant it looks, is not what "the
   report" means to someone typing in this one.
2. THE PANEL WINS. When the reader has a document open, that is what "it" and
   "this report" refer to, so it is rendered FIRST and in full. Ties are broken
   by recency, which is the same thing one step weaker.
3. THE REST STILL COUNT. A thread that made three artifacts can be asked about
   any of them, so the others follow the focused one — bounded, because the
   budget is finite and the focused document is the likely subject.

Best-effort by construction, like every grounding block: a missing row, a
foreign tenant, or any read error collapses to '' and the ask answers exactly
as it did before this existed.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.prompt_history import html_to_text

logger = logging.getLogger(__name__)

#: The focused document — the one the panel is showing, or the newest. Sized to
#: hold a full VoC or competitive-intelligence report including the
#: recommendations at its end, which is the section follow-ups ask about most
#: and the one history always truncated away. ~30k chars ≈ 7.5k tokens.
_FOCUS_CAP = 30_000

#: Everything else in the thread. Enough to answer "what did the other report
#: say about pricing?" from the document rather than from a title, and small
#: enough that three of them cannot crowd out the one actually being asked
#: about.
_OTHER_CAP = 6_000

#: How many non-focused artifacts ride along. A thread with more than this has
#: a listing surface for the tail (`list_artifacts`), and a prompt is not it.
_MAX_OTHERS = 3

_HEADER = (
    "=== ARTIFACTS IN THIS CHAT ===\n"
    "These documents were produced in THIS conversation and belong to it. "
    "\"The report\", \"this document\", \"your recommendations\", a numbered "
    "point, a theme, a section, or any unqualified ask about findings refers "
    "to what is below — answer from it, quote its own wording and figures, and "
    "say plainly when it does not cover what was asked. The first one is what "
    "the reader has open; prefer it when the question does not name another. "
    "Do not go looking for tickets, calls or channels to re-derive an answer a "
    "document here already states, and never answer about a document from "
    "another conversation.\n"
)

#: How each kind reads to a person. Storage vocabulary never reaches a prompt:
#: `custom_artifact` is a "document" everywhere a human sees it.
_KIND_LABEL = {
    "report": "Report",
    "document": "Document",
}


def _cap(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[… truncated]"


def _body_of(row: dict[str, Any]) -> str:
    """The visible text of an artifact row, whichever column holds it.

    A report stores `html`; a document stores `body_html`. Both are stripped to
    text — a generated document is mostly layout, and the model needs the
    findings, not the stylesheet.
    """
    raw = row.get("html") or row.get("body_html") or row.get("body") or ""
    return html_to_text(raw)


def _render(row: dict[str, Any], kind: str, limit: int) -> str:
    """One artifact as a prompt section, or '' when it has no readable body."""
    body = _cap(_body_of(row), limit)
    if not body:
        return ""
    label = _KIND_LABEL.get(kind, kind.replace("_", " ").title())
    title = (row.get("title") or "").strip() or f"(untitled {label.lower()})"
    lines = [f"## {label}: {title}"]
    # Every field renders when present and is simply absent otherwise — the
    # shape of the section is what tells the model what it knows about a
    # document, and a silently dropped line reads as a document that has none.
    if row.get("skill"):
        lines.append(f"Generated by the '{row['skill']}' skill.")
    if row.get("question"):
        lines.append(f"Originally asked: {row['question']}")
    if row.get("created_at"):
        lines.append(f"Created: {str(row['created_at'])[:10]}")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


def _thread_rows(company_id: str, conversation_id: int) -> list[tuple[str, dict]]:
    """Every artifact bound to this conversation, newest first, as (kind, row).

    Each read is independently best-effort: a documents table that errors must
    not cost the answer the report it was going to be grounded on.
    """
    rows: list[tuple[str, dict]] = []
    try:
        from app.db.reports import reports_with_bodies_for_conversation

        rows += [
            ("report", r)
            for r in reports_with_bodies_for_conversation(conversation_id, company_id)
        ]
    except Exception:  # noqa: BLE001 — one source failing is not the answer failing
        logger.exception(
            "thread reports unavailable company=%s conversation=%s",
            company_id, conversation_id,
        )
    try:
        from app.db.custom_artifacts import documents_with_bodies_for_conversation

        rows += [
            ("document", r)
            for r in documents_with_bodies_for_conversation(company_id, conversation_id)
        ]
    except Exception:  # noqa: BLE001
        logger.exception(
            "thread documents unavailable company=%s conversation=%s",
            company_id, conversation_id,
        )
    # Newest first across both kinds. `id` is monotonic per table, so it only
    # orders WITHIN a kind; `created_at` is what makes the two comparable, and
    # a row missing it sorts last rather than crashing the sort.
    rows.sort(key=lambda kr: str(kr[1].get("created_at") or ""), reverse=True)
    return rows


def build_thread_artifact_context(
    company_id: Optional[str],
    conversation_id: Optional[int],
    focus: Optional[dict] = None,
) -> str:
    """The "ARTIFACTS IN THIS CHAT" block, or '' when the thread has none.

    `focus` is the artifact the side panel is showing — `{"kind": "report" |
    "document", "id": int}`, exactly the shape the classify call already
    receives as `open_artifact`. It only ever REORDERS what this thread
    already owns: a focus naming an artifact from another conversation is
    ignored rather than fetched, which is what keeps rule 1 true even if a
    stale client sends a pointer from the thread the reader just left.

    Returns '' when nothing in the thread has a readable body, so a chat that
    has produced no documents composes exactly the prompt it did before.
    """
    if not company_id or not conversation_id:
        return ""
    try:
        rows = _thread_rows(company_id, conversation_id)
        if not rows:
            return ""

        focus_kind = str((focus or {}).get("kind") or "").strip().lower()
        focus_id = (focus or {}).get("id")
        if focus_kind and focus_id is not None:
            rows.sort(
                key=lambda kr: not (
                    kr[0] == focus_kind and kr[1].get("id") == focus_id
                )
            )

        sections: list[str] = []
        for index, (kind, row) in enumerate(rows[: _MAX_OTHERS + 1]):
            rendered = _render(row, kind, _FOCUS_CAP if index == 0 else _OTHER_CAP)
            if rendered:
                sections.append(rendered)
        if not sections:
            return ""

        # An artifact the thread holds but that did not fit is NAMED rather
        # than dropped in silence: "there is also a Competitive Intelligence
        # report in this chat" is a true and useful thing to be able to say,
        # and a model that cannot see the tail should not imply the list it
        # can see is the whole of it.
        tail = rows[_MAX_OTHERS + 1:]
        if tail:
            named = ", ".join(
                f"{_KIND_LABEL.get(k, k)} \"{(r.get('title') or 'untitled')}\""
                for k, r in tail
            )
            sections.append(
                "## Also in this chat, not included above\n"
                f"{named}. Say these exist if asked; their contents are not in "
                "this prompt, so do not describe what they say."
            )
        return _HEADER + "\n\n".join(sections)
    except Exception:  # noqa: BLE001 — grounding must never break the answer
        logger.exception(
            "thread artifact context unavailable company=%s conversation=%s",
            company_id, conversation_id,
        )
        return ""

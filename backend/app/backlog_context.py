"""The workspace's BACKLOG — what it is, and what is currently in it.

Asked for by the planner (`ask_planner.Plan.include_backlog`) and executed on
the answer path. Before it, the chat did not know the surface existed: the word
"backlog" appears nowhere in `ASK_SYSTEM`, no block listed an idea, and the
`/v1/ideation` routes were reachable only from the Backlog screen itself. So
"what's in my backlog" was answered out of the knowledge graph — where the
nearest thing to a backlog is a synced Jira board — and the user was told about
someone else's tickets.

TWO HALVES, the same shape `projects_context` established and for the same
reason: the block opens by saying what the backlog IS in this product (the
ideas this week's Top Insights brief did NOT pick, plus anything added by hand),
then lists what is in it. A model that can list the rows but cannot explain the
surface answers "what even is this" badly, and that is the first thing anyone
asks about a screen they have just found.

THE ID IS ON EVERY LINE, deliberately. The write actions that follow (mark an
idea done, re-sequence, add) all begin by resolving a phrase the user typed —
"the export bug" — to one row, and the only place a model can learn that
mapping is here. A line without its id is a line no action can act on.

SCOPED BY COMPANY, like the library and team blocks and unlike a dataset:
`ideation_items` is keyed on `enterprise_id` alone (see `db/ideation.py`), so
the backlog is a company fact and every workspace of that company sees the same
one. Do not "improve" this into a workspace scope without changing the table.

Never raises, and returns "" for a read that failed — "your backlog is empty"
said because a query timed out is a confident lie about the user's own data,
and no block at all degrades to the answer they got before this existed. A
genuinely EMPTY backlog is a real state and does render, because "there is
nothing in it yet, here is what it is for" is a true and useful answer.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# The whole visible list reaches the prompt up to this bound. The weekly pass
# shortlists 25–30, and manual adds are counted in ones — so this is a runaway
# guard rather than a real ceiling, and a truncation is DECLARED (see below)
# rather than silently presented as the complete list.
_MAX_ITEMS = 80

_BACKLOG_SCREEN = "Backlog"

# `ideation_items.tag` → the words the product uses. The SAME mapping the
# Backlog screen renders (`IdeationScreen.tsx`'s TYPE_LABELS), so the chat and
# the screen can never call one idea two different things. Nobody says
# "something_broken" out loud, and a model shown the raw enum will echo it.
_TAG_LABELS: dict[str, str] = {
    "something_broken": "Bug",
    "something_new": "New initiative",
    "something_better": "UI",
}

# `ideation_items.status` → plain words. 'backlog' is the legacy spelling of
# 'proposed' (see `db.ideation.LEGACY_STATUS_BACKLOG`) and reads identically.
_STATUS_LABELS: dict[str, str] = {
    "proposed": "proposed",
    "backlog": "proposed",
    "in_progress": "in progress",
}


def _item_line(row: dict) -> str:
    title = (row.get("title") or "").strip() or "(untitled idea)"
    tag = _TAG_LABELS.get(row.get("tag") or "", "untyped")
    status = _STATUS_LABELS.get(row.get("status") or "", row.get("status") or "proposed")
    return (
        f"- #{row.get('rank')} {title} — {tag} — {status} — "
        f"backlog item id: {row.get('id')}"
    )


#: What the backlog IS. Its own constant because it is the half of this block
#: that does not depend on a read succeeding — an empty backlog still gets the
#: explanation, which is exactly what someone asking "what is the backlog" needs.
_WHAT_THE_BACKLOG_IS = (
    "The BACKLOG in Sprntly is this company's pool of product ideas: the "
    "themes the weekly prioritization pass ranked but the current Top Insights "
    "brief did NOT pick for its top three, plus any idea a person added by "
    "hand. Each idea carries a rank (lower is higher priority), a type (Bug, "
    "New initiative, UI) and a status. An idea can be turned into a PRD, and "
    "from there into tickets and a prototype. It is NOT a Jira backlog, a "
    "sprint board, or anything in a connected tool, and it is NOT the same as "
    "this workspace's tickets — those are generated FROM a PRD. The backlog "
    f"lives on the {_BACKLOG_SCREEN} screen."
)


def backlog_block(company_id: Optional[str]) -> str:
    """This company's visible backlog, as a context section."""
    if not company_id:
        return ""
    try:
        from app.db.ideation import list_visible_ideation_items

        items = list_visible_ideation_items(company_id) or []
    except Exception:  # noqa: BLE001 — an unreadable list degrades, never lies
        logger.exception("backlog block: read failed for %s", company_id)
        return ""

    shown, dropped = items[:_MAX_ITEMS], max(0, len(items) - _MAX_ITEMS)
    parts = [
        "=== THIS COMPANY'S BACKLOG ===",
        _WHAT_THE_BACKLOG_IS,
        "",
        "The list below is the complete set of ideas currently VISIBLE on the "
        f"{_BACKLOG_SCREEN} screen, read just now, in rank order. Ideas that "
        "have been completed or dismissed are deliberately not here. Never "
        "name a backlog idea that does not appear below, and never present a "
        "Jira issue or a ticket as one.",
        "",
        f"BACKLOG ({len(items)} item{'s' if len(items) != 1 else ''}), "
        "highest priority first.",
    ]
    parts.extend([_item_line(r) for r in shown] or [
        "(Empty — this company has no visible backlog ideas. That is a normal "
        "state before the first Top Insights brief has been generated, since "
        "the pool is the remainder of that brief's ranking: say so, say what "
        "the backlog is for, and offer to add an idea.)"
    ])
    if dropped:
        parts.append(
            f"(+{dropped} more not shown — say the list was truncated at "
            f"{_MAX_ITEMS} rather than presenting it as complete.)"
        )
    return "\n".join(parts)

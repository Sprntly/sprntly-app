"""Rewrite an existing ticket's description from a PRD — chat → preview → confirm.

The gap this fills, reported from a live thread: a user pulled up a ticket, had
a PRD generated from it, then asked "update the ticket details with the PRD" —
and nothing could serve that sentence. `chat_intent` has no "change an existing
ticket" action, so the message fell through to the ask path, where
`is_jira_lookup` claimed it (a write verb on a PM noun) and handed it to
app/jira_lookup.py — a skill whose tools are Jira-only and which has NO way to
read a PRD. It saw at most the four-sentence chat summary of the document
(app/artifact_summary.py), never the document.

This module is that missing executor. It knows both places a "ticket" can live,
because from the chat they are the same word:

  - a SPRNTLY ticket — generated from a PRD by app/stories/generate.py, an
    element of `prd_tickets.stories`, keyed "prd-{prd_id}-{story_id}", edited
    through PUT /v1/tickets/{key}/description;
  - a JIRA issue — keyed "PROJ-142", edited through POST /v1/jira/write.

Which one the user meant is resolved by LOOKING, not by parsing their sentence:
the model lists the tickets of the PRD in context and searches Jira (when it is
connected), and picks the ticket the thread is actually about.

Propose-only, exactly like app/jira_lookup.py — and for a stronger reason here.
This skill REPLACES a description rather than setting one field, so a misheard
target destroys writing that a person may have done by hand. The model produces
a proposal; it rides out on the answer as `_pending_ticket_change` and is
rendered as a confirm card. The write happens only when a person clicks Confirm,
through the ordinary tenant-scoped endpoints that already own each surface.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.llm import run_tool_loop

logger = logging.getLogger(__name__)

ANSWER_MODEL = "claude-sonnet-4-6"
_MAX_ITERS = 8
# Wider than jira_lookup's 4k: the tool loop carries a rendered PRD in, and the
# rewritten description back out.
_MAX_TOKENS = 8000
_SKILL_ACTION = "Ticket update"
_SKILL_SOURCE = "ticket-update"

# A PRD is the source material for a rewrite, not the answer — cap what rides
# into the loop so a long Part B can't crowd out the ticket itself.
_PRD_MAX_CHARS = 40_000
# Enough to pick the right ticket out of a PRD's set; the ones we generate run
# to a dozen or two.
_TICKET_LIST_CAP = 60

_SYSTEM = (
    "You update an existing ticket so it reflects a PRD. You do NOT write "
    "anything — you produce a proposal the user confirms.\n\n"
    "A 'ticket' in this workspace lives in one of two places, and the user's "
    "wording does not distinguish them:\n"
    "- a SPRNTLY ticket, generated from a PRD (key like 'prd-42-a1b2c3'), or\n"
    "- a JIRA issue (key like 'PROJ-142').\n"
    "Find out which by LOOKING, never by guessing from the phrasing.\n\n"
    "Tools:\n"
    "- get_prd: the PRD in context, rendered in full. Read it FIRST — it is the "
    "source material for everything you write.\n"
    "- list_prd_tickets: every Sprntly ticket generated from that PRD, with its "
    "key and title.\n"
    "- get_ticket: one Sprntly ticket's CURRENT title, description and "
    "acceptance criteria.\n"
    "- jira_search / jira_get_issue: the same for a connected Jira. Absent when "
    "Jira is not connected — then the ticket is a Sprntly one.\n"
    "- propose_ticket_update: hand back the rewritten description for the user "
    "to confirm.\n\n"
    "Method: read the PRD, read the ticket AS IT IS NOW, then rewrite its "
    "description so it carries the PRD's requirements for that ticket's slice "
    "of the work. Rewrite only that ticket's scope — a PRD covers more than any "
    "one ticket, and pasting the whole document into a ticket is not an "
    "update. Keep every requirement, constraint and number the PRD states "
    "verbatim; invent nothing that is not in the PRD or already in the ticket. "
    "Preserve the ticket's existing acceptance criteria unless the PRD "
    "contradicts or extends them, and write them as testable statements.\n\n"
    "Resolving WHICH ticket: the conversation above almost always names it — "
    "the issue key you or the user mentioned, or the ticket the thread has been "
    "about. Use that. If the thread names none and the PRD has several tickets, "
    "ASK which one rather than proposing against a guess.\n\n"
    "After proposing, say plainly which ticket it targets and what will change, "
    "and that it is awaiting their confirmation. NEVER say you have updated, "
    "changed or saved anything — at that point you have not. If you cannot find "
    "the ticket, or there is no PRD in context, say so plainly and stop."
)

_GET_PRD_TOOL = {
    "name": "get_prd",
    "description": (
        "The PRD in context for this conversation, rendered in full (title, "
        "body, implementation spec). This is the source material for the "
        "rewrite — read it before anything else. Takes no arguments."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

_LIST_TICKETS_TOOL = {
    "name": "list_prd_tickets",
    "description": (
        "Every Sprntly ticket generated from the PRD in context: its key "
        "(e.g. 'prd-42-a1b2c3') and title. Use it to find which ticket the "
        "conversation is about. Takes no arguments."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

_GET_TICKET_TOOL = {
    "name": "get_ticket",
    "description": (
        "One Sprntly ticket as it stands RIGHT NOW — title, description, "
        "acceptance criteria, scope — including any edits a person has made. "
        "Read this before proposing, so the rewrite builds on the real current "
        "content instead of replacing work you never saw."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ticket_key": {
                "type": "string",
                "description": "The ticket key, e.g. 'prd-42-a1b2c3'.",
            },
        },
        "required": ["ticket_key"],
    },
}

_PROPOSE_TOOL = {
    "name": "propose_ticket_update",
    "description": (
        "PROPOSE the rewritten description for the user to confirm. This does "
        "NOT write — it returns a preview the user must approve.\n"
        "`target` is 'sprntly' for a ticket generated from a PRD (pass its key "
        "as `ticket_key`) or 'jira' for a Jira issue (pass its key as "
        "`ticket_key` too — e.g. 'PROJ-142').\n"
        "`description` is the full new description text, in markdown. "
        "`acceptance_criteria` is the full new list, one testable statement per "
        "item — omit it to leave the ticket's criteria untouched (Jira has no "
        "separate criteria field, so there it is folded into the description).\n"
        "Call this ONCE, for ONE ticket."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "enum": ["sprntly", "jira"]},
            "ticket_key": {"type": "string"},
            "description": {"type": "string"},
            "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["target", "ticket_key", "description"],
    },
}


def _plain_payload(answer: str, *, confidence: float = 0.0) -> dict:
    """Ask-shaped payload for the non-LLM branches, tagged so the UI attributes
    it to this path."""
    return {
        "answer": answer, "key_points": [], "citations": [],
        "confidence": confidence, "unanswered": "",
        "_skill": None, "_skill_action": _SKILL_ACTION, "_skill_source": _SKILL_SOURCE,
    }


def _render_history(history: Optional[list[dict]]) -> str:
    """Recent turns as plain text. The ticket this request targets is named in
    an earlier turn far more often than in the request itself ("update the
    ticket with the PRD" names neither), so this block is what makes the target
    resolvable at all."""
    if not history:
        return ""
    recent = history[-10:]
    rows = [f"{t.get('role', 'user').capitalize()}: {t.get('content', '')}" for t in recent]
    return "Conversation so far:\n" + "\n".join(rows) + "\n\n"


def _render_prd(prd_id: Optional[int]) -> str:
    """The PRD in context, rendered for the model. Applied patches are folded in
    by get_prd_rendered, so this is the document as the user sees it — not the
    raw stored row."""
    if not prd_id:
        return "(no PRD is open on this tab or bound to this conversation)"
    from app.db.prds import get_prd_rendered

    prd = get_prd_rendered(prd_id)
    if prd is None:
        return f"(no PRD found with id {prd_id})"
    parts = [f"# {prd.get('title') or 'PRD'}"]
    for key in ("payload_md", "llm_part"):
        chunk = str(prd.get(key) or "").strip()
        if chunk:
            parts.append(chunk)
    text = "\n\n".join(parts)
    if len(text) > _PRD_MAX_CHARS:
        text = text[:_PRD_MAX_CHARS] + "\n\n…(PRD truncated)"
    return text


def _render_ticket_list(company_id: str, prd_id: Optional[int]) -> str:
    """Key + title for every ticket generated from the PRD in context."""
    if not prd_id:
        return "(no PRD in context, so there are no generated tickets to list)"
    from app.db.prd_tickets import get_tickets
    from app.routes.internal_mcp import _ticket_key_for

    row = get_tickets(company_id, prd_id)
    stories = (row or {}).get("stories") or []
    if not stories:
        return f"(PRD {prd_id} has no generated tickets yet)"
    rows = [
        f"- {_ticket_key_for(prd_id, s)} — {s.get('title') or '(untitled)'}"
        for s in stories[:_TICKET_LIST_CAP]
        if isinstance(s, dict)
    ]
    if len(stories) > _TICKET_LIST_CAP:
        rows.append(f"- …and {len(stories) - _TICKET_LIST_CAP} more")
    return "\n".join(rows)


def _load_ticket(company_id: str, ticket_key: str) -> dict | None:
    """One Sprntly ticket, generated content merged with per-ticket edits.

    Reuses the MCP surface's reader so the chat sees exactly what a developer's
    `get_ticket` sees — same merge, same override precedence, one definition of
    "the current ticket"."""
    from fastapi import HTTPException

    from app.routes.internal_mcp import ticket_data

    try:
        return ticket_data(ticket_key, company_id)
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001 — a lookup failure is a tool result, not a 500
        logger.exception("ticket-update: ticket read failed for %s", ticket_key)
        return None


def _render_ticket(ticket: dict) -> str:
    lines = [
        f"Key: {ticket.get('id')}",
        f"Title: {ticket.get('title') or '(untitled)'}",
        f"Status: {ticket.get('status') or 'Backlog'}",
        "",
        "Description:",
        str(ticket.get("description") or "(empty)"),
    ]
    criteria = ticket.get("acceptance_criteria") or []
    lines += ["", "Acceptance criteria:"]
    lines += [f"- {c}" for c in criteria] or ["(none)"]
    for label, key in (("Scope", "scope"), ("Out of scope", "out_of_scope")):
        values = ticket.get(key) or []
        if values:
            lines += ["", f"{label}:"] + [f"- {v}" for v in values]
    return "\n".join(lines)


def _preview_lines(
    description: str,
    criteria: list[str] | None,
    before: dict | None,
    *,
    criteria_rewritten: bool,
) -> list[str]:
    """The human-readable "what will change" lines shown on the confirm card.

    Deliberately reports SIZE rather than a full before/after diff: the card sits
    inline in a chat thread, and the new description is rendered under it in
    full. What the user cannot otherwise see is whether they are about to lose
    something — so that is what this says.
    """
    lines: list[str] = []
    old = str((before or {}).get("description") or "")
    if old.strip():
        lines.append(
            f"Description: replaces the current {len(old)}-character description "
            f"with {len(description)} characters"
        )
    else:
        lines.append(f"Description: sets a new {len(description)}-character description")
    if criteria is not None:
        old_criteria = (before or {}).get("acceptance_criteria") or []
        if criteria_rewritten:
            lines.append(
                f"Acceptance criteria: {len(old_criteria)} → {len(criteria)} item(s)"
            )
        else:
            lines.append(f"Acceptance criteria: unchanged ({len(criteria)} item(s))")
    return lines


def _make_dispatch(
    *, company_id: str, prd_id: Optional[int], jira_session: Any, proposal: dict
):
    """Build the (name, input) -> str dispatcher. Every tool returns rendered
    text; a per-call failure becomes a readable error string so the model can
    adjust rather than the whole answer erroring."""
    from app.connectors import jira_fetch

    def dispatch(name: str, inp: dict) -> str:
        inp = inp if isinstance(inp, dict) else {}

        if name == "get_prd":
            return _render_prd(prd_id)

        if name == "list_prd_tickets":
            return _render_ticket_list(company_id, prd_id)

        if name == "get_ticket":
            key = (inp.get("ticket_key") or "").strip()
            if not key:
                return "(get_ticket: 'ticket_key' is required)"
            ticket = _load_ticket(company_id, key)
            if ticket is None:
                return f"(no Sprntly ticket found with key {key})"
            return _render_ticket(ticket)

        if name == "jira_search":
            if jira_session is None:
                return "(Jira is not connected)"
            hits = jira_fetch.search(
                jira_session,
                text=inp.get("text"),
                project=inp.get("project"),
                status=inp.get("status"),
            )
            return jira_fetch.render_search(hits)

        if name == "jira_get_issue":
            if jira_session is None:
                return "(Jira is not connected)"
            key = (inp.get("issue_key") or "").strip()
            if not key:
                return "(jira_get_issue: 'issue_key' is required)"
            issue = jira_fetch.get_issue(jira_session, key)
            if issue is None:
                return f"(no Jira issue found with key {key})"
            return jira_fetch.render_issue(issue)

        if name == "propose_ticket_update":
            return _dispatch_propose(
                inp,
                company_id=company_id,
                jira_session=jira_session,
                proposal=proposal,
            )

        return f"(unknown tool {name})"

    return dispatch


def _dispatch_propose(
    inp: dict, *, company_id: str, jira_session: Any, proposal: dict
) -> str:
    """Validate a proposed rewrite and stage it for the confirm card.

    Validation is not ceremony: the target must EXIST and be readable before the
    user is shown a button that overwrites it. A proposal against a key the
    model invented dies here, as a tool result the model can recover from,
    rather than as a 404 after someone clicks Confirm.
    """
    from app.connectors import jira_fetch

    target = (inp.get("target") or "").strip().lower()
    key = (inp.get("ticket_key") or "").strip()
    description = (inp.get("description") or "").strip()
    criteria_in = inp.get("acceptance_criteria")
    criteria = (
        [str(c).strip() for c in criteria_in if str(c).strip()]
        if isinstance(criteria_in, list)
        else None
    )

    if target not in ("sprntly", "jira"):
        return "(propose_ticket_update: 'target' must be 'sprntly' or 'jira')"
    if not key:
        return "(propose_ticket_update: 'ticket_key' is required)"
    if not description:
        return "(propose_ticket_update: 'description' is required and cannot be empty)"

    criteria_rewritten = criteria is not None

    if target == "sprntly":
        before = _load_ticket(company_id, key)
        if before is None:
            return (
                f"(no Sprntly ticket found with key {key} — call list_prd_tickets "
                "and use a key from that list)"
            )
        title = str(before.get("title") or "")
        # PUT /v1/tickets/{key}/description REPLACES the criteria with whatever
        # it is sent, so "leave them alone" cannot be expressed by sending
        # nothing — an omitted list would arrive as [] and blank criteria the
        # user never asked to touch. Carry the current ones forward instead, so
        # the proposal always states the exact end state.
        if criteria is None:
            criteria = [str(c) for c in (before.get("acceptance_criteria") or [])]
    else:
        if jira_session is None:
            return "(Jira is not connected, so a Jira issue cannot be updated)"
        issue = jira_fetch.get_issue(jira_session, key)
        if issue is None:
            return f"(no Jira issue found with key {key})"
        # Jira has one description field; criteria fold into it rather than
        # being dropped on the floor.
        if criteria:
            description = description.rstrip() + "\n\nAcceptance criteria:\n" + "\n".join(
                f"- {c}" for c in criteria
            )
            criteria = None
        before = {"description": issue.get("description") or ""}
        title = str(issue.get("summary") or "")

    proposal.clear()
    proposal.update({
        "target": target,
        "ticket_key": key,
        "title": title,
        "description": description,
        # For a Sprntly ticket this is always the EXACT list to write (current
        # ones carried forward when the model didn't rewrite them). Always None
        # for Jira, which has no separate criteria field — there they were
        # folded into the description above.
        "acceptance_criteria": criteria,
        "preview": _preview_lines(
            description, criteria, before, criteria_rewritten=criteria_rewritten
        ),
    })
    return (
        f"Proposed an update to {key}. It is now waiting for the user's "
        "confirmation — nothing has been written. Tell them what will change."
    )


def answer(
    *,
    enterprise_id: str,
    question: str,
    history: Optional[list[dict]] = None,
    prd_id: Optional[int] = None,
) -> dict:
    """Propose a PRD-grounded rewrite of one ticket; return an Ask-shaped payload.

    Never raises and never writes — on any failure the chat gets a plain message.
    A successful proposal rides out as `_pending_ticket_change`; applying it
    takes a person clicking Confirm.
    """
    from app.connectors import jira_fetch

    # Best-effort: no Jira just means the Jira tools aren't offered. This path
    # must NOT bail on a missing Jira connection the way jira_lookup does — the
    # ticket is just as likely to be a Sprntly one, which needs no connector.
    try:
        jira_session = jira_fetch.open_session(enterprise_id)
    except Exception:  # noqa: BLE001
        logger.exception("ticket-update: jira session failed for %s", enterprise_id)
        jira_session = None

    if prd_id is None and jira_session is None:
        return _plain_payload(
            "I can rewrite a ticket's details from a PRD, but I need both in "
            "view: open the PRD beside this chat (or generate one here) and "
            "name the ticket you want updated."
        )

    tools = [_GET_PRD_TOOL, _LIST_TICKETS_TOOL, _GET_TICKET_TOOL, _PROPOSE_TOOL]
    if jira_session is not None:
        from app.jira_lookup import _GET_ISSUE_TOOL, _SEARCH_TOOL

        tools = [_SEARCH_TOOL, _GET_ISSUE_TOOL, *tools]

    meta: dict = {}
    proposal: dict = {}
    try:
        text = run_tool_loop(
            system=_SYSTEM,
            user=_render_history(history) + f"Request: {question}",
            tools=tools,
            dispatch=_make_dispatch(
                company_id=enterprise_id,
                prd_id=prd_id,
                jira_session=jira_session,
                proposal=proposal,
            ),
            model=ANSWER_MODEL,
            max_tokens=_MAX_TOKENS,
            max_iters=_MAX_ITERS,
            meta_out=meta,
        )
    except Exception:  # noqa: BLE001 — never break the chat
        logger.exception("ticket-update: tool loop failed for %s", enterprise_id)
        return _plain_payload(
            "I couldn't put together that ticket update just now. Please retry "
            "in a moment."
        )

    _log(enterprise_id, meta)
    if not text.strip():
        return _plain_payload(
            "I couldn't work out which ticket to update. Name it by key, or "
            "open the PRD whose ticket you mean beside this chat."
        )
    return {
        "answer": text, "key_points": [], "citations": [],
        "confidence": 0.6, "unanswered": "",
        "_skill": None, "_skill_action": _SKILL_ACTION, "_skill_source": _SKILL_SOURCE,
        # Present only when a rewrite was proposed. The UI renders it as a
        # confirm card; nothing is written until the user acts on it.
        **({"_pending_ticket_change": proposal} if proposal else {}),
    }


def _log(enterprise_id: str, meta: dict) -> None:
    """Best-effort decision-log row (the tool-loop path bypasses the gateway's
    own logging, like jira_lookup._log)."""
    try:
        from app.graph.decision_log import log_agent_decision

        log_agent_decision(
            enterprise_id=enterprise_id,
            agent="qa",
            decision_type="ticket_update",
            factors={k: meta.get(k) for k in ("input_tokens", "output_tokens") if k in meta},
            model=meta.get("model"),
            prompt_version="qa-ticket-update-v1",
        )
    except Exception:  # noqa: BLE001
        logger.exception("ticket-update decision-log write failed")

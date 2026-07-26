"""On-demand Jira lookup — chat → live Jira (REST) → grounded answer.

The Jira sibling of app/call_digest.py. When a user references a Jira ticket or
epic ("what's the status of PROJ-142", "summarize the checkout epic in Jira",
"which tickets are open on the billing board"), the generic Ask path answers
from the KG — a periodic, token-capped, comment-less snapshot of Jira. This
module answers from Jira LIVE instead.

Unlike the call digest (one deterministic pre-fetch → a fixed skill), a Jira
lookup is agentic: the model decides which issue/epic to read, or searches for
it, so we run a bounded tool-use loop (app.llm.run_tool_loop) exposing two
read-only Jira tools — jira_search and jira_get_issue — over a live session
(app/connectors/jira_fetch.py). The model calls them, reads the real issues, and
answers grounded in what it fetched.

Intent detection (is_jira_lookup) lives in skill_router; qa_agent delegates here
when it fires, BEFORE the generic router (which would otherwise answer from the
stale KG). Read-only: no create/update/transition is reachable from chat.
"""
from __future__ import annotations

import logging

from app.connectors import jira_fetch
from app.llm import run_tool_loop

logger = logging.getLogger(__name__)

ANSWER_MODEL = "claude-sonnet-4-6"
_MAX_ITERS = 6
_MAX_TOKENS = 4000
_SKILL_ACTION = "Jira lookup"
_SKILL_SOURCE = "jira-lookup"

_SYSTEM = (
    "You are a product-management assistant with LIVE access to the user's "
    "connected Jira. You can READ freely, and you can PROPOSE changes for the "
    "user to confirm. Answer by fetching the real issues the question refers to "
    "— never guess an issue's status, description, or assignee.\n\n"
    "Tools:\n"
    "- jira_get_issue: fetch one issue in full by its key (e.g. PROJ-142) — "
    "description, status, assignee, comments, and (for an epic) its child "
    "issues. Use this whenever the question names a specific key.\n"
    "- jira_search: find issues by keyword text, project key, and/or status "
    "when no key is given, then jira_get_issue the ones that matter.\n"
    "- jira_editmeta: which fields are EDITABLE on an issue, with their types "
    "and legal values. Use it when the user asks what can be changed, or what a "
    "field accepts. Never assert that a field exists, is editable, or takes a "
    "given value without checking — Jira's answer is per issue and per "
    "permission, so it is the only reliable source.\n\n"
    "- jira_propose_change: propose an edit — field values, a status move, or a "
    "comment. It does NOT write; the user confirms first.\n\n"
    "Changing an issue: call jira_editmeta FIRST to get the real field ids, "
    "types and legal values, then jira_propose_change with what the user asked "
    "for. Shape values to the field's type (a `date` takes YYYY-MM-DD, so "
    "\"august 31 2028\" becomes \"2028-08-31\"). Status is never a field — pass "
    "it as `to_status`. If the user's wording is ambiguous about WHICH issue, "
    "ask before proposing. After proposing, state plainly what will change and "
    "that it needs their confirmation — NEVER say you have updated, moved, "
    "assigned or commented on anything: at that point you have not.\n\n"
    "Rules: call a tool before answering anything factual about an issue. If a "
    "key doesn't exist or a search returns nothing, say so plainly — do not "
    "invent issues. Cite issue keys (and their browse links when present) in "
    "your answer. Be concise and concrete.\n\n"
    "Follow-ups: the question is often a continuation that never repeats the "
    "issue it means (\"can you get me all the details about it?\", \"who owns "
    "that one?\", \"and the comments\"). Resolve the reference from the "
    "conversation above — the key you or the user already named is the issue "
    "they mean — and jira_get_issue it directly rather than searching again. "
    "Only when the conversation names no issue at all should you search, or ask "
    "which one they mean."
)

_SEARCH_TOOL = {
    "name": "jira_search",
    "description": (
        "Search the user's Jira for issues. Provide any of: `text` (keyword "
        "search over summary/description), `project` (a project key like "
        "'PROJ'), `status` (e.g. 'In Progress', 'Done'). Returns a list of "
        "matching issues (key, summary, type, status, assignee), newest first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Keyword(s) to match in the issue text."},
            "project": {"type": "string", "description": "Restrict to this project key."},
            "status": {"type": "string", "description": "Restrict to this workflow status."},
        },
    },
}

_GET_ISSUE_TOOL = {
    "name": "jira_get_issue",
    "description": (
        "Fetch one Jira issue in full by its key (e.g. 'PROJ-142'): summary, "
        "description, status, priority, assignee, labels, comments, subtasks, "
        "and — when the issue is an Epic — its child issues."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "issue_key": {"type": "string", "description": "The issue key, e.g. 'PROJ-142'."},
        },
        "required": ["issue_key"],
    },
}


_EDITMETA_TOOL = {
    "name": "jira_editmeta",
    "description": (
        "List which fields can be EDITED on one Jira issue, with each field's id, "
        "type and legal values. Jira answers this per issue, so it already "
        "reflects that project's screens, the issue type and the user's own "
        "permissions. Use it before telling the user whether something can be "
        "changed, or what a field will accept — do not assume a field exists or "
        "is editable. Note status is NOT a field here: it moves via a workflow "
        "transition."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "issue_key": {"type": "string", "description": "The issue key, e.g. 'PROJ-142'."},
        },
        "required": ["issue_key"],
    },
}


def _plain_payload(answer: str, *, confidence: float = 0.0) -> dict:
    """Ask-shaped payload for the non-LLM branches (Jira not connected), tagged
    so the UI attributes it to the Jira-lookup path."""
    return {
        "answer": answer, "key_points": [], "citations": [],
        "confidence": confidence, "unanswered": "",
        "_skill": None, "_skill_action": _SKILL_ACTION, "_skill_source": _SKILL_SOURCE,
    }


def _render_history(history: list[dict] | None) -> str:
    """Recent turns as plain text. Wide enough (10 turns ≈ 5 exchanges) that the
    issue key the follow-up refers to is still in view — the model resolves
    "it"/"that one" against this block, so trimming it too hard is what turns a
    follow-up back into a blind re-search."""
    if not history:
        return ""
    recent = history[-10:]
    rows = [f"{t.get('role', 'user').capitalize()}: {t.get('content', '')}" for t in recent]
    return "Conversation so far:\n" + "\n".join(rows) + "\n\n"


_PROPOSE_TOOL = {
    "name": "jira_propose_change",
    "description": (
        "PROPOSE a change to a Jira issue for the user to confirm. This does NOT "
        "write anything — it validates the change and returns a preview which "
        "the user must approve before it is applied. Use it whenever the user "
        "asks to change, set, update, move, assign or comment on an issue.\n"
        "Provide `issue_key` and at least one of: `fields` (an object of Jira "
        "FIELD IDS to values — call jira_editmeta first to learn the ids, types "
        "and legal values; dates are 'YYYY-MM-DD'), `to_status` (a workflow "
        "status name, which moves the issue via a transition rather than a field "
        "write), or `comment` (text to post).\n"
        "Call this ONCE per request, then tell the user plainly what will change "
        "and that it is awaiting their confirmation. Never claim the change has "
        "been made — it has not, until they confirm."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "issue_key": {"type": "string", "description": "The issue key, e.g. 'PROJ-142'."},
            "fields": {
                "type": "object",
                "description": "Jira field ids → values, e.g. {\"duedate\": \"2028-08-31\"}.",
            },
            "to_status": {"type": "string", "description": "Target workflow status name."},
            "comment": {"type": "string", "description": "Comment body to post."},
        },
        "required": ["issue_key"],
    },
}


def _make_dispatch(session: jira_fetch.JiraSession, proposal: dict | None = None):
    """Build the (name, input) -> str tool dispatcher bound to a live session.
    Each tool returns a rendered text block for the model; a per-call failure is
    turned into a readable error string (run_tool_loop also guards), so the
    model can adjust rather than the whole answer erroring."""
    def dispatch(name: str, inp: dict) -> str:
        inp = inp if isinstance(inp, dict) else {}
        if name == "jira_search":
            hits = jira_fetch.search(
                session,
                text=inp.get("text"),
                project=inp.get("project"),
                status=inp.get("status"),
            )
            return jira_fetch.render_search(hits)
        if name == "jira_get_issue":
            key = (inp.get("issue_key") or "").strip()
            if not key:
                return "(jira_get_issue: 'issue_key' is required)"
            issue = jira_fetch.get_issue(session, key)
            if issue is None:
                return f"(no Jira issue found with key {key})"
            return jira_fetch.render_issue(issue)
        if name == "jira_editmeta":
            key = (inp.get("issue_key") or "").strip()
            if not key:
                return "(jira_editmeta: 'issue_key' is required)"
            meta = jira_fetch.get_editmeta(session, key)
            if meta is None:
                return f"(no Jira issue found with key {key})"
            return jira_fetch.render_editmeta(meta)
        if name == "jira_propose_change":
            key = (inp.get("issue_key") or "").strip()
            if not key:
                return "(jira_propose_change: 'issue_key' is required)"
            fields = inp.get("fields") if isinstance(inp.get("fields"), dict) else {}
            to_status = (inp.get("to_status") or "").strip()
            comment = (inp.get("comment") or "").strip()
            if not fields and not to_status and not comment:
                return ("(jira_propose_change: give at least one of fields, "
                        "to_status or comment)")
            preview = jira_fetch.preview_change(
                session, key, fields=fields, to_status=to_status, comment=comment
            )
            if proposal is not None and preview.get("ok"):
                # Handed back to answer() and out to the UI as the pending
                # action. The model cannot execute it — only the user can, by
                # confirming, which posts to routes/jira_write.py.
                proposal.clear()
                proposal.update(preview["change"])
            return preview["text"]
        return f"(unknown tool {name})"

    return dispatch


def answer(*, enterprise_id: str, question: str, history: list[dict] | None = None) -> dict:
    """Run the on-demand Jira lookup and return an Ask-shaped payload.

    Opens a live Jira session for the tenant and lets the model fetch the issues
    the question refers to via the read-only tool loop. When Jira isn't
    connected, returns a helpful connect message instead. Never raises — the
    chat answer degrades gracefully on any failure."""
    session = jira_fetch.open_session(enterprise_id)
    if session is None:
        return _plain_payload(
            "I can pull live details from your Jira — tickets, epics, comments, "
            "and their status — but Jira isn't connected yet (or its access "
            "needs refreshing). Connect **Jira** in Settings → Connectors and "
            "I'll be able to read your issues."
        )

    meta: dict = {}
    # Filled by the propose tool with the exact change the user is being asked to
    # confirm. It rides out on the payload; applying it needs a separate,
    # user-initiated POST (routes/jira_write.py). The model never writes.
    proposal: dict = {}
    try:
        text = run_tool_loop(
            system=_SYSTEM,
            user=_render_history(history) + f"Question: {question}",
            tools=[_SEARCH_TOOL, _GET_ISSUE_TOOL, _EDITMETA_TOOL, _PROPOSE_TOOL],
            dispatch=_make_dispatch(session, proposal),
            model=ANSWER_MODEL,
            max_tokens=_MAX_TOKENS,
            max_iters=_MAX_ITERS,
            meta_out=meta,
        )
    except Exception:  # noqa: BLE001 — never break the chat
        logger.exception("jira-lookup: tool loop failed for %s", enterprise_id)
        return _plain_payload(
            "I couldn't reach Jira to look that up just now. Please retry in a "
            "moment — if it keeps failing, your Jira connection may need "
            "reconnecting in Settings → Connectors."
        )

    _log(enterprise_id, meta)
    if not text.strip():
        return _plain_payload(
            "I looked in Jira but couldn't find the issue(s) your question "
            "refers to. Double-check the issue key or try naming the project."
        )
    return {
        "answer": text, "key_points": [], "citations": [],
        "confidence": 0.6, "unanswered": "",
        "_skill": None, "_skill_action": _SKILL_ACTION, "_skill_source": _SKILL_SOURCE,
        # Present only when the agent proposed a change. The UI renders it as a
        # confirm card; nothing is written until the user acts on it.
        **({"_pending_jira_change": proposal} if proposal else {}),
    }


def _log(enterprise_id: str, meta: dict) -> None:
    """Best-effort decision-log row (the tool-loop path bypasses the gateway's
    own logging, like _answer_with_script in qa_agent)."""
    try:
        from app.graph.decision_log import log_agent_decision

        log_agent_decision(
            enterprise_id=enterprise_id,
            agent="qa",
            decision_type="jira_lookup",
            factors={k: meta.get(k) for k in ("input_tokens", "output_tokens") if k in meta},
            model=meta.get("model"),
            prompt_version="qa-jira-lookup-v1",
        )
    except Exception:  # noqa: BLE001
        logger.exception("jira-lookup decision-log write failed")

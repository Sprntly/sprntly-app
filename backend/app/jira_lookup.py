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
    "You are a product-management assistant with LIVE, read-only access to the "
    "user's connected Jira. Answer the user's question by fetching the real "
    "issues it refers to — never guess an issue's status, description, or "
    "assignee.\n\n"
    "Tools:\n"
    "- jira_get_issue: fetch one issue in full by its key (e.g. PROJ-142) — "
    "description, status, assignee, comments, and (for an epic) its child "
    "issues. Use this whenever the question names a specific key.\n"
    "- jira_search: find issues by keyword text, project key, and/or status "
    "when no key is given, then jira_get_issue the ones that matter.\n\n"
    "Rules: call a tool before answering anything factual about an issue. If a "
    "key doesn't exist or a search returns nothing, say so plainly — do not "
    "invent issues. Cite issue keys (and their browse links when present) in "
    "your answer. Be concise and concrete."
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


def _plain_payload(answer: str, *, confidence: float = 0.0) -> dict:
    """Ask-shaped payload for the non-LLM branches (Jira not connected), tagged
    so the UI attributes it to the Jira-lookup path."""
    return {
        "answer": answer, "key_points": [], "citations": [],
        "confidence": confidence, "unanswered": "",
        "_skill": None, "_skill_action": _SKILL_ACTION, "_skill_source": _SKILL_SOURCE,
    }


def _render_history(history: list[dict] | None) -> str:
    if not history:
        return ""
    recent = history[-6:]
    rows = [f"{t.get('role', 'user').capitalize()}: {t.get('content', '')}" for t in recent]
    return "Conversation so far:\n" + "\n".join(rows) + "\n\n"


def _make_dispatch(session: jira_fetch.JiraSession):
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
    try:
        text = run_tool_loop(
            system=_SYSTEM,
            user=_render_history(history) + f"Question: {question}",
            tools=[_SEARCH_TOOL, _GET_ISSUE_TOOL],
            dispatch=_make_dispatch(session),
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

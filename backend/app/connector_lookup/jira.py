"""Jira adapter — the first connector-lookup provider.

This is the code that used to live in app/jira_lookup.py, moved verbatim: the
same four tools (three reads + propose-a-change), the same dispatcher, the same
system prompt. app/jira_lookup.py is now a thin shim over the framework that
keeps the module's public surface — including the `_pending_jira_change` confirm
card contract with routes/jira_write.py and the web UI — byte-identical.

Read/propose only. No code path lets a model turn a sentence into a Jira write:
`jira_propose_change` validates a change and hands back a preview which the USER
must confirm (routes/jira_write.py applies it).
"""
from __future__ import annotations

from app.connector_lookup.base import LookupSession
from app.connectors import jira_fetch

DISPLAY_NAME = "Jira"

SYSTEM = (
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

#: Verbatim pre-framework copy. Jira's connect message is user-facing text that
#: predates this package and is asserted by the lookup's own tests; the framework
#: composes its own (which also lists what IS connected) for new adapters.
NOT_CONNECTED = (
    "I can pull live details from your Jira — tickets, epics, comments, "
    "and their status — but Jira isn't connected yet (or its access "
    "needs refreshing). Connect **Jira** in Settings → Connectors and "
    "I'll be able to read your issues."
)

SEARCH_TOOL = {
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

GET_ISSUE_TOOL = {
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


EDITMETA_TOOL = {
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


PROPOSE_TOOL = {
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

TOOLS = [SEARCH_TOOL, GET_ISSUE_TOOL, EDITMETA_TOOL, PROPOSE_TOOL]

#: Key the proposed change rides out on. Consumed by the web UI's confirm card
#: and applied by routes/jira_write.py — do not rename.
PENDING_CHANGE_KEY = "_pending_jira_change"


def make_dispatch(session: jira_fetch.JiraSession, proposal: dict | None = None):
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
            # Exactly one match → return it IN FULL, not the one-line summary.
            #
            # The lean field set exists because a search can return twenty rows
            # and nobody wants twenty descriptions. With a single hit there is no
            # such trade: the user asked about one ticket, got four fields and an
            # offer to fetch the rest, and had to ask again for what they plainly
            # already wanted. One extra API call buys the whole answer up front.
            if len(hits) == 1 and hits[0].get("key"):
                issue = jira_fetch.get_issue(session, hits[0]["key"])
                if issue is not None:
                    return jira_fetch.render_issue(issue)
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


class JiraProvider:
    """LookupProvider over app/connectors/jira_fetch.py."""

    provider = "jira"
    display_name = DISPLAY_NAME
    keywords = ("jira", "atlassian")
    #: Jira's own client already bounds a result (4k description, 20 comments,
    #: 50 epic children, 300 chars per extra field), so this cap sits ABOVE those
    #: bounds: the refactor changes nothing about what a Jira tool result
    #: contains. New adapters take the framework's 10k default.
    result_char_cap = 24_000

    def open_session(self, enterprise_id: str) -> LookupSession | None:
        session = jira_fetch.open_session(enterprise_id)
        if session is None:
            return None
        # The proposal dict is created per session and mutated in place by the
        # propose tool; answer() copies it onto the payload when non-empty.
        return LookupSession(
            provider=self.provider,
            handle=session,
            extras={PENDING_CHANGE_KEY: {}},
        )

    def tools(self) -> list[dict]:
        return TOOLS

    def dispatch(self, session: LookupSession, name: str, inp: dict) -> str:
        return make_dispatch(session.handle, session.extras[PENDING_CHANGE_KEY])(
            name, inp
        )

    def system_block(self) -> str:
        return SYSTEM


PROVIDER = JiraProvider()

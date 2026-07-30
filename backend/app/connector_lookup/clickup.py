"""ClickUp adapter — live task reads for the tracker fast-path.

Read-only by design: ClickUp writes exist (connectors/clickup_oauth.py) but only
behind the story-push flow, and there is no confirm-card contract for ClickUp
yet, so chat can read tasks and nothing more. The Jira adapter's
propose-then-confirm surface has no ClickUp twin here on purpose — a model that
could write without a confirm step is exactly what that contract prevents.
"""
from __future__ import annotations

from app.connector_lookup.base import LookupSession, cap_items
from app.connectors import clickup_fetch

DISPLAY_NAME = "ClickUp"

SYSTEM = (
    "Tools:\n"
    "- clickup_search_tasks: find tasks by keyword `text`, `status` (the "
    "workspace's own status name, e.g. 'in progress'), and/or `list_name`. "
    "Returns one line per task (id, title, status, list, assignee, link).\n"
    "- clickup_get_task: one task in full by its id — body, assignees, dates, "
    "tags and recent comments. Use it once a search has told you which task "
    "matters.\n\n"
    "Honest limits you MUST respect: ClickUp's API has no full-text task "
    "search, so a keyword search scans a bounded window of the most recently "
    "updated tasks (the result says how many). If the user's task might be "
    "older than that window, say the search covered recent tasks only rather "
    "than concluding it doesn't exist. ClickUp statuses are per-list custom "
    "names, so don't assume 'To Do'/'Done' exist — read them off the results.\n"
    "This connection is READ-ONLY: you cannot create, edit, move or comment on "
    "a ClickUp task. If asked, say so plainly and offer to summarize instead."
)

SEARCH_TOOL = {
    "name": "clickup_search_tasks",
    "description": (
        "Search the user's ClickUp tasks. Provide any of: `text` (keyword "
        "matched against task title and body), `status` (a workspace status "
        "name), `list_name` (restrict to a list whose name contains this). "
        "Returns matching tasks, most recently updated first, and states how "
        "many tasks were scanned."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Keyword(s) to match in the task."},
            "status": {"type": "string", "description": "Restrict to this ClickUp status name."},
            "list_name": {"type": "string", "description": "Restrict to lists matching this name."},
        },
    },
}

GET_TASK_TOOL = {
    "name": "clickup_get_task",
    "description": (
        "Fetch one ClickUp task in full by its id (as returned by "
        "clickup_search_tasks): title, description, status, assignees, dates, "
        "tags and its most recent comments."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The ClickUp task id."},
        },
        "required": ["task_id"],
    },
}

TOOLS = [SEARCH_TOOL, GET_TASK_TOOL]

NOT_CONNECTED = (
    "I can read your ClickUp tasks live — status, assignees, comments — but "
    "ClickUp isn't connected yet (or its access needs refreshing). Connect "
    "**ClickUp** in Settings → Connectors and ask me again."
)


class ClickUpProvider:
    """LookupProvider over app/connectors/clickup_fetch.py."""

    provider = "clickup"
    display_name = DISPLAY_NAME
    keywords = ("clickup", "click up")

    def open_session(self, enterprise_id: str) -> LookupSession | None:
        session = clickup_fetch.open_session(enterprise_id)
        if session is None:
            return None
        return LookupSession(provider=self.provider, handle=session)

    def tools(self) -> list[dict]:
        return TOOLS

    def system_block(self) -> str:
        return SYSTEM

    def dispatch(self, session: LookupSession, name: str, inp: dict) -> str:
        handle = session.handle
        if name == "clickup_search_tasks":
            rows, scanned = clickup_fetch.search_tasks(
                handle,
                text=inp.get("text"),
                status=inp.get("status"),
                list_name=inp.get("list_name"),
            )
            kept, marker = cap_items(rows, clickup_fetch._SEARCH_LIMIT)
            return clickup_fetch.render_search(kept, scanned, truncation=marker)
        if name == "clickup_get_task":
            task_id = (inp.get("task_id") or "").strip()
            if not task_id:
                return "(clickup_get_task: 'task_id' is required)"
            task = clickup_fetch.get_task(handle, task_id)
            if task is None:
                return f"(no ClickUp task found with id {task_id})"
            return clickup_fetch.render_task(task)
        return f"(unknown tool {name})"


PROVIDER = ClickUpProvider()

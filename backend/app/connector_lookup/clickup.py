"""ClickUp adapter — live task reads for the tracker fast-path.

Read-only by design: ClickUp writes exist (connectors/clickup_oauth.py) but only
behind the story-push flow, and there is no confirm-card contract for ClickUp
yet, so chat can read tasks and nothing more. The Jira adapter's
propose-then-confirm surface has no ClickUp twin here on purpose — a model that
could write without a confirm step is exactly what that contract prevents.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.connector_lookup.base import LookupSession, cap_items
from app.connectors import clickup_fetch

if TYPE_CHECKING:
    from app.kg_ingest.types import RawRecord

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


def _row_to_record(row: dict) -> "RawRecord":
    """One `clickup_fetch.search_tasks` row → the CLOSEST `RawRecord` it can
    build with NO new HTTP call.

    NOT byte-identical to `kg_ingest.pullers.clickup.pull`'s record for the
    same task, and it cannot be made so from a search row alone — stated here
    because AC4 requires saying so, not papering over it:

      - `tags`: absent from `_task_row` entirely (ClickUp's search endpoint
        never returns them) — the puller's `tags` KEY is simply not produced,
        so it never appears in `render()`'s `data:` line, where the puller's
        does whenever a task has any.
      - `assignees`: the puller carries every assignee; a search row keeps
        only `_task_row`'s first one (`assignee`, singular), because that is
        all the search endpoint returns before the client-side keyword filter
        runs. Wrapped in a list here for property-name parity, but a task with
        2+ assignees renders a shorter list than the puller's.
      - `text`: empty. `_task_row` carries no description/body at all — only
        `clickup_fetch.get_task` (a second HTTP call, out of scope per the
        ticket) does.
      - `timestamp`: even where BOTH sides have data, the FORMAT differs.
        `_task_row["updated"]` is already `_ms_to_iso`-converted to a bare
        `YYYY-MM-DD` for display; the puller carries ClickUp's raw
        `date_updated` epoch-ms STRING, unconverted. Two representations of
        the same instant, never equal as strings.

    Closing every gap above needs `clickup_get_task` per hit — a new HTTP
    call this module's OWN `dispatch`/`dispatch_records` still may not make
    (sweep.py's latency contract). `enrich_record` below closes it anyway,
    from the sweep-persist background thread ONLY, where that contract does
    not apply — see connector_lookup/sweep_persist.py's module docstring.
    See the AC4 test for the assertion that THIS record and the puller's
    record for the same task do NOT collide (the lean, sweep-time shape);
    the enrichment test proves the ENRICHED one does.
    """
    from app.kg_ingest.types import RawRecord

    assignee = row.get("assignee")
    return RawRecord(
        provider="clickup",
        kind="task",
        external_id=str(row.get("id") or ""),
        title=row.get("name", "") or "",
        text="",
        properties={
            "status": row.get("status"),
            "priority": row.get("priority"),
            "list": row.get("list"),
            "assignees": [assignee] if assignee else [],
        },
        timestamp=row.get("updated"),
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

    def dispatch_records(self, session: LookupSession, name: str, inp: dict):
        """`(text, records)` for `clickup_search_tasks`, `None` for anything
        else. Calls `clickup_fetch.search_tasks` and `render_search` exactly as
        `dispatch` does above — ONE fetch, reused for both text and records —
        so `text` is byte-identical to `dispatch`'s own output by construction.
        See `_row_to_record` for why the records themselves are NOT
        byte-identical to the scheduled pull's."""
        if name != "clickup_search_tasks":
            return None
        handle = session.handle
        rows, scanned = clickup_fetch.search_tasks(
            handle,
            text=inp.get("text"),
            status=inp.get("status"),
            list_name=inp.get("list_name"),
        )
        kept, marker = cap_items(rows, clickup_fetch._SEARCH_LIMIT)
        text = clickup_fetch.render_search(kept, scanned, truncation=marker)
        records = [_row_to_record(r) for r in kept] if kept else None
        return text, records


def _task_to_puller_record(t: dict) -> "RawRecord":
    """One raw ClickUp task (`clickup_fetch.get_task_raw`) -> the SAME
    `RawRecord` `kg_ingest.pullers.clickup.pull` builds for it: same
    provider/kind, same `properties` KEYS in the SAME order, same
    external_id, same raw epoch-ms timestamp. Field-for-field copy of that
    puller's construction (kg_ingest/pullers/clickup.py) — AC4's
    byte-identity is a property of `RawRecord.render()`, so matching the
    shape exactly is the whole job (same approach jira.py's
    `_issue_to_record` takes for the case that was already byte-identical
    before this amendment).
    """
    from app.kg_ingest.types import RawRecord

    status = (t.get("status") or {}).get("status", "")
    return RawRecord(
        provider="clickup",
        kind="task",
        external_id=str(t["id"]),
        title=t.get("name", "") or "",
        text=(t.get("text_content") or t.get("description") or "")[:2000],
        properties={
            "status": status,
            "priority": ((t.get("priority") or {}) or {}).get("priority"),
            "list": ((t.get("list") or {}) or {}).get("name"),
            "tags": [g.get("name") for g in t.get("tags", [])],
            "assignees": [a.get("username") for a in t.get("assignees", [])],
        },
        timestamp=t.get("date_updated") or t.get("date_created"),
    )


def enrich_record(session: LookupSession, record: "RawRecord") -> "RawRecord":
    """PERSIST-THREAD ONLY (see connector_lookup/sweep_persist.py's module
    docstring for why a per-hit fetch is safe here and NOT in
    `dispatch`/`dispatch_records`, which stay bound by sweep.py's own
    latency contract). One `clickup_get_task`-equivalent fetch —
    `clickup_fetch.get_task_raw`, not `get_task` (that one shapes the chat
    tool's OWN result and would break byte-identity — see its docstring) —
    turns the LEAN `_row_to_record` this module built at sweep time into the
    puller-shaped record AC4 needs (AC-A1).

    Raises on any failure (404 is the one exception, handled below by
    falling back to the lean record — a task that vanished between the
    search and this fetch is not an error). The caller
    (`sweep_persist._enrich_source`) is what isolates a per-hit failure from
    the rest of the source/run (AC-A4); this function does not swallow
    anything else itself, so a caller that forgot to wrap it fails loudly
    rather than silently returning a plausible-looking record it never
    actually fetched.
    """
    raw = clickup_fetch.get_task_raw(session.handle, record.external_id)
    if raw is None:
        return record  # 404/gone since the search — keep the lean record
    return _task_to_puller_record(raw)


PROVIDER = ClickUpProvider()

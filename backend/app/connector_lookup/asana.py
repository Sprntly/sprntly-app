"""Asana adapter — live task reads from chat.

Read-only by design, exactly as the ClickUp adapter is: Asana writes exist
(connectors/asana_oauth.py, driven by the story-push and two-way-sync flows)
and there is no confirm-card contract for Asana, so chat can read tasks and
nothing more. The Jira adapter's propose-then-confirm surface has no Asana twin
here on purpose.

THE ONE HONEST LIMIT THAT SHAPES EVERYTHING BELOW. Asana's full-text search
(`/workspaces/{gid}/tasks/search`, which does match task NOTES) is a
Premium/Business-only endpoint — a free or Starter workspace answers 402. An
adapter built on it would work for whoever tested it and fail for a customer,
silently, as "Asana found nothing". So the search here is Asana's TYPEAHEAD,
which every plan has and which matches TASK TITLES ONLY. That limit is stated
in the system block, in the rendered result, and in the not-found copy, because
a title-only search reporting "nothing in Asana" about a topic discussed in a
task's description would be a false absence.

Depth is still reachable: `asana_get_task` fetches one task in full — notes,
assignee, section, custom fields — once a search says which task matters.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.connector_lookup.base import LookupSession, cap_items

if TYPE_CHECKING:
    from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

DISPLAY_NAME = "Asana"

#: Workspaces one search walks. A typeahead call is per-workspace, so this is
#: the leg's request budget: most companies have one workspace, an agency or a
#: consultancy has a handful, and past three the fan-out costs more than the
#: extra coverage is worth inside a chat turn. The rendered result says how
#: many were searched, so a capped search never reads as a complete one.
_MAX_WORKSPACES = 3

#: Hits kept from one search, across every workspace.
_MAX_HITS = 15

#: Task notes carried into a record. Same figure as
#: `kg_ingest.pullers.asana._TEXT_CHARS`, deliberately: the same task read two
#: ways must not produce two different texts.
_TEXT_CHARS = 2000

SYSTEM = (
    "Tools:\n"
    "- asana_search_tasks: find tasks by keyword `text`, matched against the "
    "task TITLE. Returns one line per task (id, title, project, section, "
    "assignee, done-state, link).\n"
    "- asana_get_task: one task in full by its id — notes, assignee, section, "
    "dates and custom fields. Use it once a search has told you which task "
    "matters.\n\n"
    "Honest limits you MUST respect: Asana's keyword search here matches TASK "
    "TITLES ONLY, never the description or comments (Asana's full-text search "
    "is a paid-plan API we cannot rely on being available). So an empty result "
    "means \"no task TITLE mentions this\", NOT \"Asana has nothing about "
    "this\" — say which it was. The search covers a bounded number of "
    "workspaces and the result says how many. Asana has no native status or "
    "priority: a task's status IS the section it sits in within its project, "
    "and `completed` is the real done signal — read section names off the "
    "results rather than assuming 'To Do'/'Done' exist.\n"
    "This connection is READ-ONLY: you cannot create, edit, move, complete or "
    "comment on an Asana task. If asked, say so plainly and offer to "
    "summarize instead."
)

SEARCH_TOOL = {
    "name": "asana_search_tasks",
    "description": (
        "Search the company's Asana tasks by keyword. `text` is matched "
        "against the task TITLE only — not its description or comments. "
        "Returns matching tasks with their project, section (Asana's status), "
        "assignee and link, and states how many workspaces were searched."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Keyword(s) to match in the task title.",
            },
        },
        "required": ["text"],
    },
}

GET_TASK_TOOL = {
    "name": "asana_get_task",
    "description": (
        "Fetch one Asana task in full by its id (as returned by "
        "asana_search_tasks): title, notes, section, assignee, dates, "
        "completion and custom fields."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The Asana task gid."},
        },
        "required": ["task_id"],
    },
}

TOOLS = [SEARCH_TOOL, GET_TASK_TOOL]

NOT_CONNECTED = (
    "I can read your Asana tasks live — sections, assignees, notes — but "
    "Asana isn't connected yet (or its access needs refreshing). Connect "
    "**Asana** in Settings → Connectors and ask me again."
)


@dataclass
class AsanaHandle:
    """One tenant's Asana access for the duration of one answer, plus the
    workspace list resolved once so a search-then-read pair doesn't pay for it
    twice."""

    # repr suppressed: this IS the bearer token.
    access_token: str = field(repr=False)
    company_id: str = ""
    _workspaces: "list[dict[str, Any]] | None" = field(default=None, repr=False)

    def workspaces(self) -> list[dict[str, Any]]:
        if self._workspaces is None:
            from app.connectors import asana_oauth

            # The chat HTTP bound, not the sync path's longer one — this is the
            # first request a chat search makes, and `base.HTTP_TIMEOUT` is the
            # framework's promise that no single upstream outlives the answer.
            self._workspaces = asana_oauth.list_workspaces(
                self.access_token, timeout=asana_oauth._READ_TIMEOUT
            )
        return self._workspaces


def _section_of(task: dict[str, Any]) -> dict[str, Any]:
    """The section a typeahead hit sits in, without knowing its project.

    `asana_oauth._membership_section` needs a project gid to disambiguate a
    task that lives in several projects; a chat read has none. A hit carries
    its memberships inline, so the FIRST one that names a section is used and
    the project it belongs to is rendered alongside it — which keeps the pair
    self-consistent (this section, in that project) even for a multi-project
    task, rather than reporting a section from one project next to another
    project's name.
    """
    for membership in task.get("memberships") or []:
        if isinstance(membership, dict) and (membership.get("section") or {}).get("name"):
            return membership
    memberships = task.get("memberships") or []
    return memberships[0] if memberships and isinstance(memberships[0], dict) else {}


def _render_hit(task: dict[str, Any]) -> str:
    membership = _section_of(task)
    project = (membership.get("project") or {}).get("name") or ""
    section = (membership.get("section") or {}).get("name") or ""
    assignee = (task.get("assignee") or {}).get("name") or (
        (task.get("assignee") or {}).get("email") or ""
    )
    bits = [f"- {task.get('gid')}: {task.get('name') or '(untitled task)'}"]
    if project:
        bits.append(f"project {project}")
    if section:
        bits.append(f"section {section}")
    if assignee:
        bits.append(assignee)
    bits.append("completed" if task.get("completed") else "open")
    if task.get("permalink_url"):
        bits.append(str(task["permalink_url"]))
    return " · ".join(bits)


def render_search(
    hits: list[dict[str, Any]], workspaces_searched: int, *, truncation: str = ""
) -> str:
    """The rendered `asana_search_tasks` result.

    One public renderer, called by BOTH `dispatch` and `dispatch_records`, so
    the byte-identity `base.RecordsCapable` requires of the two is a property
    of construction rather than of remembering to keep them in step.
    """
    scope = (
        f"{workspaces_searched} Asana workspace(s) searched by task TITLE"
        if workspaces_searched
        else "no Asana workspaces were readable"
    )
    if not hits:
        return (
            f"(no Asana task TITLE matches these terms — {scope}. This does not "
            "mean Asana has nothing on the topic: task descriptions and "
            "comments are not searched here.)"
        )
    head = f"{len(hits)} Asana task(s) whose title matches ({scope}):"
    lines = [_render_hit(t) for t in hits]
    return "\n".join(p for p in [head, "\n".join(lines), truncation] if p)


def render_task(task: dict[str, Any]) -> str:
    """One full task, as `asana_get_task` shows it."""
    from app.connectors.asana_oauth import _cf_read_value

    membership = _section_of(task)
    project = (membership.get("project") or {}).get("name") or ""
    section = (membership.get("section") or {}).get("name") or ""
    assignee = (task.get("assignee") or {}).get("name") or (
        (task.get("assignee") or {}).get("email") or ""
    )
    header = task.get("name") or "(untitled task)"
    meta = [
        f"id: {task.get('gid')}",
        f"project: {project or 'unknown'}",
        f"section (Asana's status): {section or 'none'}",
        f"completed: {'yes' if task.get('completed') else 'no'}",
    ]
    if assignee:
        meta.append(f"assignee: {assignee}")
    if task.get("due_on"):
        meta.append(f"due: {task['due_on']}")
    if task.get("start_on"):
        meta.append(f"starts: {task['start_on']}")
    if task.get("modified_at"):
        meta.append(f"updated: {task['modified_at']}")
    if task.get("permalink_url"):
        meta.append(f"link: {task['permalink_url']}")
    custom = [
        f"{cf.get('name')}: {_cf_read_value(cf)}"
        for cf in task.get("custom_fields") or []
        if isinstance(cf, dict) and cf.get("name")
        and _cf_read_value(cf) not in (None, [])
    ]
    if custom:
        meta.append("custom fields — " + "; ".join(custom))
    notes = (task.get("notes") or "").strip() or "(no description)"
    return f"{header}\n" + "\n".join(meta) + f"\n\n{notes}"


def _hit_to_record(task: dict[str, Any]) -> "RawRecord":
    """One typeahead hit → the CLOSEST `RawRecord` it can build with NO new
    HTTP call.

    NOT byte-identical to `kg_ingest.pullers.asana.pull`'s record for the same
    task, and it cannot be made so from a typeahead hit alone — stated rather
    than papered over, same as ClickUp's `_row_to_record`:

      - `text`: EMPTY. Typeahead never returns `notes`, and the puller's record
        carries the task description. This is the largest gap and the one that
        guarantees the two hashes differ.
      - `properties["project"]`: the FIRST membership's project name, where the
        puller carries the project it was pulled UNDER. Same value for a
        single-project task, different for a task in several.
      - `custom_fields`: typeahead does not return them, so the key is simply
        not produced (the puller omits it too when a task has none, so an
        Asana task with no custom fields is the one case where they can agree).

    So an Asana sweep does NOT dedupe against the scheduled 6-hourly pull's
    ledger — a task read both ways is extracted twice. What bounds that cost is
    the per-(company, provider) cooldown in `sweep_persist.py`, exactly as it
    is the only bound for Slack. Closing the gap properly means one
    `get_task_raw` per hit from the persist thread (an `enrich_record`, as
    clickup/confluence/hubspot have); that is a deliberate follow-up, not an
    oversight, and the record built here is the honest lean shape until then.
    """
    from app.kg_ingest.types import RawRecord

    membership = _section_of(task)
    assignee = task.get("assignee") or {}
    return RawRecord(
        provider="asana",
        kind="task",
        external_id=str(task.get("gid") or ""),
        title=task.get("name", "") or "",
        text="",
        properties={
            "section": (membership.get("section") or {}).get("name"),
            "completed": bool(task.get("completed")),
            "assignee": assignee.get("name") or assignee.get("email"),
            "due_date": task.get("due_on"),
            "project": (membership.get("project") or {}).get("name"),
            "permalink": task.get("permalink_url"),
        },
        timestamp=task.get("modified_at"),
    )


def load_access_token(company_id: str) -> str | None:
    """The company's Asana access token, or None when Asana isn't connected /
    the credential can't be used.

    Delegates to `app.stories.push._asana_creds` rather than re-implementing
    the read-decrypt-refresh-persist sequence. That is not tidiness: a SECOND
    copy of Asana's refresh would be a second writer racing the first for one
    rotating credential, which is the exact defect
    `connectors/token_refresh.py` exists to close. One chokepoint, one writer.

    Never raises — a live-lookup open must not, per `base.LookupProvider`.
    """
    try:
        from app.stories.push import _asana_creds

        return _asana_creds(company_id) or None
    except Exception:  # noqa: BLE001 — not connected / expired / unreadable
        logger.info(
            "asana-lookup: could not open a session for %s", company_id, exc_info=True
        )
        return None


class AsanaProvider:
    """LookupProvider over app/connectors/asana_oauth.py."""

    provider = "asana"
    display_name = DISPLAY_NAME
    keywords = ("asana",)

    def open_session(self, enterprise_id: str) -> LookupSession | None:
        token = load_access_token(enterprise_id)
        if not token:
            return None
        return LookupSession(
            provider=self.provider,
            handle=AsanaHandle(access_token=token, company_id=enterprise_id),
        )

    def tools(self) -> list[dict]:
        return TOOLS

    def system_block(self) -> str:
        return SYSTEM

    def dispatch(self, session: LookupSession, name: str, inp: dict) -> str:
        if name == "asana_search_tasks":
            text, _kept = self._search(session.handle, inp)
            return text
        if name == "asana_get_task":
            return self._get_task(session.handle, inp)
        return f"(unknown tool {name})"

    def dispatch_records(self, session: LookupSession, name: str, inp: dict):
        """`(text, records)` for `asana_search_tasks`, `None` for anything
        else. Runs `_search` — the SAME single pass `dispatch` runs for this
        tool — so `text` is byte-identical to `dispatch`'s own output by
        construction. See `_hit_to_record` for what the records can and cannot
        carry."""
        if name != "asana_search_tasks":
            return None
        text, kept = self._search(session.handle, inp)
        return text, ([_hit_to_record(t) for t in kept] if kept else None)

    def _search(
        self, handle: AsanaHandle, inp: dict
    ) -> tuple[str, list[dict[str, Any]]]:
        """`(rendered text, kept hits)` — one typeahead call per workspace.

        A workspace whose typeahead fails is SKIPPED and excluded from the
        count the rendered result reports, so "2 workspaces searched" is the
        number actually searched rather than the number attempted. An auth
        failure is not skipped: it means the whole token is bad, so it
        propagates and the leg reports the source as unread.
        """
        from app.connectors import asana_oauth

        text = (inp.get("text") or "").strip()
        if not text:
            return "(asana_search_tasks: 'text' is required)", []

        hits: list[dict[str, Any]] = []
        searched = 0
        seen: set[str] = set()
        for workspace in handle.workspaces()[:_MAX_WORKSPACES]:
            gid = workspace.get("gid")
            if not gid:
                continue
            try:
                found = asana_oauth.typeahead_tasks(handle.access_token, str(gid), text)
            except asana_oauth.AsanaAuthExpiredError:
                raise
            except Exception:  # noqa: BLE001 — one bad workspace must not end the search
                logger.info(
                    "asana-lookup: typeahead failed in workspace %s for %s",
                    gid, handle.company_id, exc_info=True,
                )
                continue
            searched += 1
            for task in found:
                key = str(task.get("gid"))
                if key in seen:
                    continue
                seen.add(key)
                hits.append(task)

        kept, marker = cap_items(hits, _MAX_HITS)
        return render_search(kept, searched, truncation=marker), kept

    def _get_task(self, handle: AsanaHandle, inp: dict) -> str:
        from app.connectors import asana_oauth

        task_id = (inp.get("task_id") or "").strip()
        if not task_id:
            return "(asana_get_task: 'task_id' is required)"
        task = asana_oauth.get_task_raw(handle.access_token, task_id)
        if task is None:
            return f"(no Asana task found with id {task_id})"
        return render_task(task)


PROVIDER = AsanaProvider()

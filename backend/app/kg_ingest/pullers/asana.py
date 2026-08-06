"""Asana puller — tasks → RawRecords.

Asana has no native status or priority (per asana_oauth's module docstring):
a task's "status" is the SECTION it sits in within a project, and the
`completed` boolean is the real done signal. Neither status nor priority is
classified here — the extractor still infers bug/feature/fix downstream from
the title/notes/section, same as ClickUp.

BOUNDED BY PROJECT COUNT, NOT DEEPER PAGINATION: `list_project_tasks` fetches
one page per project (Asana's own per-request cap), so a workspace with a
single 10k-task project is already bounded to `_TASKS_PER_PROJECT`. What is
NOT naturally bounded is the number of PROJECTS a large workspace can have —
`_PROJECT_LIMIT` caps that, mirroring clickup.py's `_PAGE_LIMIT` pilot-scale
convention.
"""
from __future__ import annotations

import logging
from typing import Iterator

from app.connectors.asana_oauth import (
    AsanaAuthExpiredError,
    _cf_read_value,
    _membership_section,
    list_project_tasks,
    list_projects,
    list_workspaces,
)
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

_TEXT_CHARS = 2000  # clamp to match clickup.py's convention
_PROJECT_LIMIT = 5  # projects scanned per workspace — pilot-scale cap; bump when needed
_TASKS_PER_PROJECT = 100  # Asana's per-request max; see list_project_tasks


def _to_record(task: dict, project_gid: str, project_name: str) -> RawRecord | None:
    gid = task.get("gid")
    if not gid:
        return None
    section = _membership_section(task, project_gid) or {}
    assignee = task.get("assignee") or {}
    custom_fields = {
        cf["name"]: _cf_read_value(cf)
        for cf in task.get("custom_fields") or []
        if isinstance(cf, dict) and cf.get("name")
        and _cf_read_value(cf) not in (None, [])
    }
    return RawRecord(
        provider="asana",
        kind="task",
        external_id=str(gid),
        title=task.get("name", "") or "",
        text=(task.get("notes") or "")[:_TEXT_CHARS],
        properties={
            "section": section.get("name"),
            "completed": bool(task.get("completed")),
            "assignee": assignee.get("name") or assignee.get("email"),
            "due_date": task.get("due_on"),
            "project": project_name,
            "permalink": task.get("permalink_url"),
            **({"custom_fields": custom_fields} if custom_fields else {}),
        },
        timestamp=task.get("modified_at"),
    )


def pull(token: str) -> Iterator[RawRecord]:
    """Yield one RawRecord per task across the token's workspaces' projects.

    Error-isolated per workspace and per project: one that the token cannot
    read (deleted, permission revoked) is logged and skipped, never raised —
    but an auth failure (401/403, AsanaAuthExpiredError) means the WHOLE
    token is bad and must never be swallowed behind that isolation; it
    propagates so the caller (kg_ingest.runner.sync_provider, then
    auto_sync._run_sync) can surface a reconnect prompt instead of a silent
    zero-record sync.
    """
    for ws in list_workspaces(token):
        ws_gid = ws.get("gid")
        if not ws_gid:
            continue
        try:
            projects = list_projects(token, ws_gid)
        except AsanaAuthExpiredError:
            raise
        except Exception as e:  # noqa: BLE001 — one bad workspace must not end the pull
            logger.warning("asana: skipping workspace %s: %s", ws_gid, e)
            continue

        for project in projects[:_PROJECT_LIMIT]:
            project_gid = project.get("gid")
            if not project_gid:
                continue
            try:
                tasks = list_project_tasks(
                    token, project_gid, limit=_TASKS_PER_PROJECT
                )
            except AsanaAuthExpiredError:
                raise
            except Exception as e:  # noqa: BLE001 — one bad project must not end the pull
                logger.warning("asana: skipping project %s: %s", project_gid, e)
                continue

            project_name = project.get("name") or ""
            for t in tasks:
                record = _to_record(t, project_gid, project_name)
                if record is not None:
                    yield record

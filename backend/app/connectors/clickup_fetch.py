"""Live ClickUp reads for the Q&A agent — on-demand task search / task fetch.

The ClickUp sibling of app/connectors/jira_fetch.py, and the reason a
ClickUp-only company can finally ask "show me my open tickets" in chat: routing
has always been tracker-agnostic (skill_router._stateless_tracker_lookup), but
execution was Jira-only, so those tenants were told to connect Jira — a tool
they don't use. This module is the read side that closes that gap.

Auth quirk (same as the puller and the push side): ClickUp wants the token RAW in
`Authorization`, with NO `Bearer ` prefix. ClickUp issues no refresh token and
its tokens don't expire under normal use, so there is nothing to refresh — a
rejected token (401/403) means the user must reconnect, which the adapter says.

Search caveat, stated honestly to the model (see connector_lookup/clickup.py):
the ClickUp v2 API has no free-text task search. We page the team task list
(bounded) and filter client-side on name/description, so a keyword search covers
the most recently updated window, not the entire workspace history.

READ-ONLY. Writes (task create/update, comments, status) live in
connectors/clickup_oauth.py behind the story-push flow; nothing here mutates.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

from app.connector_lookup.base import HTTP_TIMEOUT
from app.connectors.clickup_oauth import CLICKUP_API, CLICKUP_PROVIDER
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json

logger = logging.getLogger(__name__)

_TIMEOUT = HTTP_TIMEOUT
#: Pages of the team task list we are willing to walk per lookup (100 tasks per
#: page). Bounded because the endpoint has no text filter — this is the window a
#: keyword search actually covers.
_MAX_PAGES = 3
_PAGE_SIZE = 100
#: Rows returned to the model for one search, and comments per task.
_SEARCH_LIMIT = 20
_COMMENT_LIMIT = 15
_DESC_CHARS = 4000
_COMMENT_CHARS = 600


class ClickUpAuthError(RuntimeError):
    """ClickUp rejected the stored token (401/403). No refresh exists, so the
    only remedy is reconnecting — surfaced to the model as such."""


@dataclass
class ClickUpSession:
    """A tenant's live ClickUp access for one lookup: the raw access token plus
    the teams (workspaces) it can see, resolved once per lookup like Jira's
    cloud_id."""

    # repr suppressed: a live token must not land in a log line, an exception
    # context or a test failure dump.
    access_token: str = field(repr=False)
    team_ids: list[str] = field(default_factory=list)
    team_names: dict[str, str] = field(default_factory=dict)

    @property
    def headers(self) -> dict[str, str]:
        # RAW token — no "Bearer ". See module docstring.
        return {"Authorization": self.access_token, "Accept": "application/json"}


def _get(session: ClickUpSession, path: str, params: dict | None = None) -> dict[str, Any]:
    """Authenticated GET against the ClickUp v2 API. 401/403 → ClickUpAuthError
    (reconnect); rate limits and other failures raise requests.HTTPError so the
    adapter can render an honest per-tool message."""
    resp = requests.get(
        f"{CLICKUP_API}{path}", params=params or {},
        headers=session.headers, timeout=_TIMEOUT,
    )
    if resp.status_code in (401, 403):
        raise ClickUpAuthError(
            "ClickUp rejected the stored token — reconnect ClickUp in Settings"
        )
    resp.raise_for_status()
    return resp.json() or {}


def open_session(company_id: str) -> ClickUpSession | None:
    """Resolve a live ClickUpSession for a company, or None when ClickUp isn't
    connected / the credential can't be read / the token sees no workspace.
    Never raises — the caller degrades to a connect message.

    Tenancy: the token is read by (company_id, provider) via db.get_connection,
    the same single chokepoint every connector read goes through. Nothing here
    accepts a company or token from model input.
    """
    from app import db

    row = db.get_connection(company_id, CLICKUP_PROVIDER)
    if not row:
        return None
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, ValueError, KeyError, TypeError):
        logger.warning("clickup-lookup: could not decrypt token for %s", company_id)
        return None
    access_token = token_json.get("access_token")
    if not access_token:
        return None
    session = ClickUpSession(access_token=access_token)
    try:
        teams = _get(session, "/team").get("teams") or []
    except Exception:  # noqa: BLE001 — no workspace visible → treat as unusable
        logger.warning(
            "clickup-lookup: could not list teams for %s", company_id, exc_info=True
        )
        return None
    for team in teams:
        tid = team.get("id")
        if tid:
            session.team_ids.append(str(tid))
            session.team_names[str(tid)] = team.get("name") or str(tid)
    if not session.team_ids:
        logger.warning("clickup-lookup: token for %s sees no workspace", company_id)
        return None
    return session


# ── Reads ────────────────────────────────────────────────────────────────────


def _ms_to_iso(raw: Any) -> str | None:
    try:
        ms = int(raw or 0)
    except (TypeError, ValueError):
        return None
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _task_row(task: dict) -> dict[str, Any]:
    """The lean, one-line-per-result shape for a search hit."""
    assignees = [
        a.get("username") or a.get("email")
        for a in (task.get("assignees") or [])
        if a.get("username") or a.get("email")
    ]
    return {
        "id": str(task.get("id") or ""),
        "name": task.get("name") or "",
        "status": ((task.get("status") or {}) or {}).get("status") or "",
        "assignee": assignees[0] if assignees else None,
        "list": ((task.get("list") or {}) or {}).get("name"),
        "priority": ((task.get("priority") or {}) or {}).get("priority"),
        "updated": _ms_to_iso(task.get("date_updated")),
        "url": task.get("url"),
    }


def _matches(task: dict, text: str) -> bool:
    """Client-side keyword match — ClickUp v2 has no text search (see module
    docstring). Matched against the title and the body."""
    needle = text.lower()
    haystack = " ".join(
        str(part or "") for part in (
            task.get("name"),
            task.get("text_content") or task.get("description"),
        )
    ).lower()
    return needle in haystack


def search_tasks(
    session: ClickUpSession,
    *,
    text: str | None = None,
    status: str | None = None,
    list_name: str | None = None,
    limit: int = _SEARCH_LIMIT,
) -> tuple[list[dict], int]:
    """Search the tenant's ClickUp tasks. Returns `(rows, scanned)`.

    `status` is pushed down to the API (`statuses[]`); `text` and `list_name` are
    filtered client-side over a bounded window of the most recently updated
    tasks. `scanned` is how many tasks that window held, so the adapter can be
    honest about coverage.
    """
    rows: list[dict] = []
    scanned = 0
    for team_id in session.team_ids:
        for page in range(_MAX_PAGES):
            params: dict[str, Any] = {
                "page": page, "subtasks": "true", "include_closed": "true",
                "order_by": "updated",
            }
            if status and status.strip():
                params["statuses[]"] = status.strip()
            data = _get(session, f"/team/{team_id}/task", params=params)
            tasks = data.get("tasks") or []
            scanned += len(tasks)
            for task in tasks:
                if text and text.strip() and not _matches(task, text.strip()):
                    continue
                if list_name and list_name.strip():
                    name = ((task.get("list") or {}) or {}).get("name") or ""
                    if list_name.strip().lower() not in name.lower():
                        continue
                rows.append(_task_row(task))
                if len(rows) >= limit:
                    return rows, scanned
            if data.get("last_page") or len(tasks) < _PAGE_SIZE:
                break
    return rows, scanned


def get_task(session: ClickUpSession, task_id: str) -> dict | None:
    """One task in full — body, status, assignees, dates, tags, and its most
    recent comments. None when ClickUp has no such task."""
    try:
        data = _get(
            session, f"/task/{task_id}",
            params={"include_markdown_description": "true"},
        )
    except requests.HTTPError as e:
        resp = getattr(e, "response", None)
        if resp is not None and resp.status_code in (404, 410):
            return None
        raise
    if not data.get("id"):
        return None
    row = _task_row(data)
    row.update({
        "description": (
            data.get("markdown_description") or data.get("text_content")
            or data.get("description") or ""
        )[:_DESC_CHARS],
        "assignees": [
            a.get("username") or a.get("email")
            for a in (data.get("assignees") or [])
            if a.get("username") or a.get("email")
        ],
        "tags": [t.get("name") for t in (data.get("tags") or []) if t.get("name")],
        "due_date": _ms_to_iso(data.get("due_date")),
        "start_date": _ms_to_iso(data.get("start_date")),
        "space": ((data.get("space") or {}) or {}).get("name"),
        "comments": _get_comments(session, task_id),
    })
    return row


def _get_comments(session: ClickUpSession, task_id: str) -> list[dict]:
    """Newest comments on a task, bounded. Best-effort: comments are extra
    context, never the reason a lookup fails."""
    try:
        data = _get(session, f"/task/{task_id}/comment")
    except Exception:  # noqa: BLE001
        logger.warning("clickup-lookup: comment fetch failed for %s", task_id)
        return []
    out: list[dict] = []
    for c in (data.get("comments") or [])[:_COMMENT_LIMIT]:
        text = (c.get("comment_text") or "").strip()
        if not text:
            continue
        out.append({
            "author": ((c.get("user") or {}) or {}).get("username") or "?",
            "date": _ms_to_iso(c.get("date")),
            "text": text[:_COMMENT_CHARS],
        })
    return out


# ── Rendering (what the model reads) ─────────────────────────────────────────


def render_search(rows: list[dict], scanned: int, *, truncation: str = "") -> str:
    """One line per hit, plus an honest coverage line."""
    if not rows:
        return (
            "No matching ClickUp tasks in the "
            f"{scanned} most recently updated tasks searched."
        )
    lines = [
        f"- {r['id']}: {r['name']} [{r['status'] or 'no status'}]"
        + (f" · {r['list']}" if r.get("list") else "")
        + (f" · {r['assignee']}" if r.get("assignee") else "")
        + (f" · updated {r['updated']}" if r.get("updated") else "")
        + (f" · {r['url']}" if r.get("url") else "")
        for r in rows
    ]
    body = "\n".join(lines)
    footer = f"\n(searched the {scanned} most recently updated ClickUp tasks)"
    if truncation:
        footer = f"\n{truncation}{footer}"
    return body + footer


def render_task(task: dict) -> str:
    """One task in full, in the same shape as jira_fetch.render_issue."""
    parts = [f"{task['id']}: {task.get('name') or '(untitled)'}"]
    meta = [
        f"status: {task.get('status') or 'unknown'}",
        f"list: {task.get('list') or 'unknown'}",
    ]
    if task.get("space"):
        meta.append(f"space: {task['space']}")
    if task.get("priority"):
        meta.append(f"priority: {task['priority']}")
    if task.get("assignees"):
        meta.append("assignees: " + ", ".join(task["assignees"]))
    if task.get("due_date"):
        meta.append(f"due: {task['due_date']}")
    if task.get("start_date"):
        meta.append(f"start: {task['start_date']}")
    if task.get("updated"):
        meta.append(f"updated: {task['updated']}")
    if task.get("tags"):
        meta.append("tags: " + ", ".join(task["tags"]))
    if task.get("url"):
        meta.append(f"url: {task['url']}")
    parts.append(" · ".join(meta))
    if task.get("description"):
        parts.append(f"description: {task['description']}")
    comments = task.get("comments") or []
    if comments:
        parts.append("comments (newest first):")
        for c in comments:
            when = f" on {c['date']}" if c.get("date") else ""
            parts.append(f"  - {c['author']}{when}: {c['text']}")
    return "\n".join(parts)

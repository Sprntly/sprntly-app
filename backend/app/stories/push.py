"""Push generated user stories into ClickUp as tasks.

This is the OUTWARD-FACING write step: it creates tasks in the user's ClickUp
workspace, so it is an explicit action, kept SEPARATE from generation (the user
reviews the stories first). It decrypts the company's ClickUp token, then
creates one task per story, error-isolated — one task failing never stops the
rest, and every failure is reported back.

Auth: ClickUp tokens are unscoped (they carry the user's full ClickUp perms),
so no extra scope is needed to create tasks; the raw-token auth quirk is handled
by clickup_oauth.create_task.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from app import db
from app.connectors import clickup_oauth, jira_oauth
from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
    encrypt_token_json,
)
from app.db.clickup_sync import get_clickup_task_id, save_clickup_task_id
from app.db.jira_sync import get_jira_issue_key, save_jira_issue_key
from app.stories.generate import Story

logger = logging.getLogger(__name__)

CLICKUP_PROVIDER = "clickup"
JIRA_PROVIDER = "jira"


class ClickUpNotConnectedError(LookupError):
    """Raised when the company has no active ClickUp connection."""


class JiraNotConnectedError(LookupError):
    """Raised when the company has no active Jira connection."""


def _clickup_fields(story: Story) -> dict[str, Any]:
    """Map the canonical ticket's fields onto the ClickUp task-create body
    (beyond name/description/priority, which the caller sets directly).

    Only fields ClickUp accepts universally on task creation are mapped here —
    `tags` (from labels) and `points` (story points, ClickUp's native Sprint
    Points field). Fields that need list-specific config (custom statuses,
    per-site custom fields) or extra API calls (checklists from subtasks,
    dependency links) are intentionally left to the sync follow-up so a push
    never fails on a workspace that lacks them."""
    fields: dict[str, Any] = {}
    if story.labels:
        fields["tags"] = list(story.labels)
    if story.story_points is not None:
        fields["points"] = story.story_points
    return fields


def _clickup_access_token(company_id: str) -> str:
    """Decrypt the company's stored ClickUp access token. Raises
    ClickUpNotConnectedError if not connected or the token is unusable."""
    row = db.get_connection(company_id, CLICKUP_PROVIDER)
    if not row or not row.get("token_json_encrypted"):
        raise ClickUpNotConnectedError("ClickUp is not connected for this company")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, ValueError) as e:
        raise ClickUpNotConnectedError("ClickUp token is unreadable") from e
    token = token_json.get("access_token") or ""
    if not token:
        raise ClickUpNotConnectedError("ClickUp connection has no access_token")
    return token


def push_stories_to_clickup(
    company_id: str,
    list_id: str,
    stories: Iterable[Story],
) -> dict[str, Any]:
    """Create one ClickUp task per story in `list_id`.

    Returns `{"created": [{story, task_id, url}], "errors": [{story, error}]}`.
    Error-isolated: a single task failure is captured in `errors` and the rest
    continue. Raises ClickUpNotConnectedError up-front if ClickUp isn't
    connected (nothing to push to).
    """
    access_token = _clickup_access_token(company_id)

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    # story title → ClickUp task id, for resolving in-batch dependency links.
    title_to_task: dict[str, str] = {}
    stories = list(stories)

    # ── Pass 1: create/update each task (+ child-issue checklist on new tasks) ──
    for story in stories:
        try:
            ticket_id = story.stable_id()
            existing = get_clickup_task_id(company_id, list_id, ticket_id)
            common = dict(
                name=story.title,
                # Rich body so the five-section description + acceptance criteria
                # render as headings/bullets in ClickUp rather than raw markdown.
                markdown_description=story.to_description(),
                priority=story.clickup_priority(),
                extra=_clickup_fields(story),
            )
            if existing:
                # Idempotent re-push: update the task we created before rather
                # than creating a duplicate.
                task = clickup_oauth.update_task(access_token, existing, **common)
                task_id = task.get("id") or existing
            else:
                task = clickup_oauth.create_task(access_token, list_id, **common)
                task_id = task.get("id")
                if task_id:
                    save_clickup_task_id(company_id, list_id, ticket_id, task_id)
                    # Child issues → a ClickUp checklist. Only on CREATE — a
                    # re-push would otherwise stack duplicate checklists (ClickUp
                    # has no upsert here); a full checklist reconcile is future work.
                    _sync_subtasks_checklist(access_token, task_id, story)
            if task_id:
                title_to_task[story.title] = task_id
            created.append({
                "story": story.title,
                "task_id": task_id,
                "url": task.get("url"),
                "updated": bool(existing),
            })
        except Exception as e:  # noqa: BLE001 — isolate per-story failures
            logger.warning("ClickUp push failed for story %r: %s", story.title, e)
            errors.append({"story": story.title, "error": str(e)})

    # ── Pass 2: link dependencies now that every task in the batch has an id ──
    for story in stories:
        src = title_to_task.get(story.title)
        if not src or not story.blocked_by:
            continue
        for dep in story.blocked_by:
            target = _resolve_dep(dep, title_to_task)
            if not target or target == src:
                continue
            try:
                # blocked_by ⇒ this task waits on the target (depends_on).
                clickup_oauth.add_dependency(access_token, src, depends_on=target)
            except Exception as e:  # noqa: BLE001 — a link failure never fails the push
                logger.warning("ClickUp dependency link failed %s→%s: %s", story.title, dep, e)

    return {"created": created, "errors": errors}


def _sync_subtasks_checklist(access_token: str, task_id: str, story: Story) -> None:
    """Create a 'Child issues' checklist on a freshly-created task, one item per
    subtask (the '[P]' parallel marker stripped). Best-effort — never fails the
    push."""
    if not story.subtasks:
        return
    try:
        checklist_id = clickup_oauth.create_checklist(access_token, task_id, "Child issues")
        if not checklist_id:
            return
        for sub in story.subtasks:
            label = sub.replace("[P]", "").strip()
            if label:
                clickup_oauth.create_checklist_item(access_token, checklist_id, label)
    except Exception as e:  # noqa: BLE001
        logger.warning("ClickUp checklist sync failed for %r: %s", story.title, e)


def _resolve_dep(dep: str, title_to_task: dict[str, str]) -> str | None:
    """Resolve a dependency reference (e.g. 'T-1 — Competitive Positioning
    One-Pager' or a bare title) to a ClickUp task id in this batch, by matching
    a known ticket title as a substring. In-batch only — cross-batch links need
    the reconcile pass (future work)."""
    d = dep.lower()
    for title, task_id in title_to_task.items():
        if title and title.lower() in d:
            return task_id
    return None


def pull_clickup_status(
    company_id: str, list_id: str, ticket_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Bidirectional read: for each ticket already synced to `list_id`, fetch its
    current ClickUp state (status, assignee, url) so Sprntly reflects work done
    in the tracker. Keyed by the ticket's stable_id; tickets never pushed (no
    mapping row) are simply absent from the result. Best-effort per ticket."""
    access_token = _clickup_access_token(company_id)
    out: dict[str, dict[str, Any]] = {}
    for ticket_id in ticket_ids:
        task_id = get_clickup_task_id(company_id, list_id, ticket_id)
        if not task_id:
            continue
        state = clickup_oauth.get_task(access_token, task_id)
        if state.get("status") or state.get("assignee"):
            out[ticket_id] = state
    return out


# ── Jira push ────────────────────────────────────────────────────────────────
#
# Mirrors the ClickUp push but targets a Jira PROJECT (not a list) and creates
# issues. Two Jira-specific concerns: access tokens expire in ~1h (so we refresh
# + persist before pushing), and every REST call needs the site's cloud_id
# (cached on the connection's config_json at connect time).


def _jira_creds(company_id: str) -> tuple[str, str]:
    """Return `(access_token, cloud_id)` for the company's Jira connection,
    refreshing + persisting an expired access token first.

    Raises JiraNotConnectedError if not connected, the token is unreadable, or
    the connection has no cloud_id / no way to refresh an expired token."""
    import time

    row = db.get_connection(company_id, JIRA_PROVIDER)
    if not row or not row.get("token_json_encrypted"):
        raise JiraNotConnectedError("Jira is not connected for this company")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, ValueError) as e:
        raise JiraNotConnectedError("Jira token is unreadable") from e

    # Refresh an expired/near-expiry access token and persist the rotated payload.
    obtained_at = token_json.get("obtained_at", 0)
    expires_in = token_json.get("expires_in", 3600)
    refresh_token = token_json.get("refresh_token")
    if refresh_token and time.time() > obtained_at + expires_in - 120:
        try:
            token_json = json.loads(
                jira_oauth.token_payload_to_store(
                    jira_oauth.refresh_access_token(refresh_token)
                )
            )
            db.update_connection_tokens(
                company_id, JIRA_PROVIDER, encrypt_token_json(json.dumps(token_json))
            )
        except jira_oauth.JiraAuthExpiredError as e:
            raise JiraNotConnectedError(str(e)) from e

    access_token = token_json.get("access_token") or ""
    if not access_token:
        raise JiraNotConnectedError("Jira connection has no access_token")
    cloud_id = (json.loads(row.get("config_json") or "{}")).get("cloud_id") \
        or jira_oauth.first_cloud_id(access_token)
    if not cloud_id:
        raise JiraNotConnectedError("Jira connection has no accessible site")
    return access_token, cloud_id


def jira_subtask_type(company_id: str, project_key: str) -> str | None:
    """The project's REAL sub-task issue type name (from cached tracker
    metadata), or None when the project has none / meta was never pulled —
    the gate for creating Sprntly child issues as real Jira sub-tasks."""
    try:
        from app.db.tracker_meta import get_cached_meta

        meta = get_cached_meta(company_id, "jira", project_key)
    except Exception:  # noqa: BLE001 — meta is an enhancement, never a gate
        return None
    for t in (meta or {}).get("issue_types") or []:
        if t.get("subtask") and t.get("name"):
            return t["name"]
    return None


def push_jira_subtasks(
    company_id: str,
    project_key: str,
    parent_key: str,
    ticket_id: str,
    subtasks: Iterable[str],
    *,
    access_token: str,
    cloud_id: str,
    subtask_type: str,
) -> None:
    """Create the MISSING child issues as real Jira sub-tasks under
    `parent_key`. Idempotent by content: each subtask maps to a
    `{ticket_id}#sub#{hash}` row in jira_issue_map, so a re-push/sync skips
    ones that exist; a renamed item creates a new sub-task (the old one stays
    — add-only, like tags). Per-item failures are isolated + logged."""
    import hashlib
    import re as _re

    for raw in subtasks or []:
        label = _re.sub(r"^\s*\[P\]\s*", "", str(raw)).strip()
        if not label:
            continue
        sub_id = (
            f"{ticket_id}#sub#"
            f"{hashlib.sha256(label.encode('utf-8')).hexdigest()[:12]}"
        )
        try:
            if get_jira_issue_key(company_id, project_key, sub_id):
                continue
            issue = jira_oauth.create_issue(
                access_token, cloud_id,
                project_key=project_key,
                summary=label[:255],
                issue_type=subtask_type,
                extra_fields={"parent": {"key": parent_key}},
            )
            if issue.get("key"):
                save_jira_issue_key(company_id, project_key, sub_id, issue["key"])
        except Exception as e:  # noqa: BLE001 — one child failing is isolated
            logger.warning(
                "Jira sub-task push failed under %s (%r): %s", parent_key, label, e
            )


def push_stories_to_jira(
    company_id: str,
    project_key: str,
    stories: Iterable[Story],
    *,
    issue_type: str = "Task",
) -> dict[str, Any]:
    """Create (or idempotently update) one Jira issue per story in `project_key`.

    Returns `{"created": [{story, task_id, url, updated}], "errors": [...]}`.
    Error-isolated per story. Raises JiraNotConnectedError up-front if Jira
    isn't connected. Mirrors push_stories_to_clickup's create-or-update-by-map
    behavior so a re-push doesn't duplicate issues.

    Child issues: when the project has a sub-task type (tracker metadata),
    each story's subtasks become REAL Jira sub-tasks under the issue — and
    the description drops its Child issues text section (no duplication).
    Without a sub-task type the legacy description section stays.
    """
    access_token, cloud_id = _jira_creds(company_id)
    subtask_type = jira_subtask_type(company_id, project_key)

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for story in stories:
        try:
            ticket_id = story.stable_id()
            existing = get_jira_issue_key(company_id, project_key, ticket_id)
            assignee = getattr(story, "assignee_account_id", None) or None
            description = story.to_description(include_subtasks=subtask_type is None)
            if existing:
                issue = jira_oauth.update_issue(
                    access_token, cloud_id, existing,
                    summary=story.title,
                    description=description,
                    priority_name=story.jira_priority(),
                    assignee_account_id=assignee,
                )
                issue_key = issue.get("key") or existing
            else:
                issue = jira_oauth.create_issue(
                    access_token, cloud_id,
                    project_key=project_key,
                    summary=story.title,
                    description=description,
                    # A per-ticket issue-type edit (ticket_edits.issue_type,
                    # set from tracker metadata's real types) wins over the
                    # batch default.
                    issue_type=getattr(story, "jira_issue_type", None) or issue_type,
                    priority_name=story.jira_priority(),
                    assignee_account_id=assignee,
                )
                issue_key = issue.get("key")
                if issue_key:
                    save_jira_issue_key(company_id, project_key, ticket_id, issue_key)
            if issue_key and subtask_type and story.subtasks:
                push_jira_subtasks(
                    company_id, project_key, issue_key, ticket_id,
                    story.subtasks,
                    access_token=access_token, cloud_id=cloud_id,
                    subtask_type=subtask_type,
                )
            created.append({
                "story": story.title,
                "task_id": issue_key,
                "url": issue.get("url"),
                "updated": bool(existing),
            })
        except Exception as e:  # noqa: BLE001 — isolate per-story failures
            logger.warning("Jira push failed for story %r: %s", story.title, e)
            errors.append({"story": story.title, "error": str(e)})

    return {"created": created, "errors": errors}


def pull_jira_status(
    company_id: str, project_key: str, ticket_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Bidirectional read, Jira flavor of pull_clickup_status: for each ticket
    already synced to `project_key`, fetch its current Jira state (status,
    assignee, url). Keyed by the ticket's stable_id; tickets never pushed (no
    mapping row) are simply absent. Best-effort per ticket."""
    access_token, cloud_id = _jira_creds(company_id)
    # Resolve the browse base URL once for the whole batch.
    site_url = jira_oauth._site_url_for_cloud(access_token, cloud_id)
    out: dict[str, dict[str, Any]] = {}
    for ticket_id in ticket_ids:
        issue_key = get_jira_issue_key(company_id, project_key, ticket_id)
        if not issue_key:
            continue
        state = jira_oauth.get_issue(
            access_token, cloud_id, issue_key, site_url=site_url
        )
        if state.get("status") or state.get("assignee"):
            out[ticket_id] = state
    return out

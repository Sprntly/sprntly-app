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
from app.connectors import clickup_oauth
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json
from app.db.clickup_sync import get_clickup_task_id, save_clickup_task_id
from app.stories.generate import Story

logger = logging.getLogger(__name__)

CLICKUP_PROVIDER = "clickup"


class ClickUpNotConnectedError(LookupError):
    """Raised when the company has no active ClickUp connection."""


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

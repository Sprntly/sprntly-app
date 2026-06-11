"""Push generated user stories into a tracker (ClickUp tasks or Jira issues).

This is the OUTWARD-FACING write step: it creates tickets in the user's
tracker, so it is an explicit action, kept SEPARATE from generation (the user
reviews the stories first). One ticket per story, error-isolated — one ticket
failing never stops the rest, and every failure is reported back.

ClickUp auth: tokens are unscoped (they carry the user's full ClickUp perms),
so no extra scope is needed to create tasks; the raw-token auth quirk is
handled by clickup_oauth.create_task.

Jira auth: tokens rotate hourly — jira_oauth.get_valid_access_token refreshes
and re-persists before the push. Descriptions go up as ADF (API v3 requirement)
with the same dual-layer content the ClickUp markdown carries.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from fastapi import HTTPException

from app import db
from app.connectors import clickup_oauth, jira_oauth
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json
from app.stories.generate import Story

logger = logging.getLogger(__name__)

CLICKUP_PROVIDER = "clickup"


class ClickUpNotConnectedError(LookupError):
    """Raised when the company has no active ClickUp connection."""


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
    for story in stories:
        try:
            task = clickup_oauth.create_task(
                access_token,
                list_id,
                name=story.title,
                description=story.to_description(),
                priority=story.clickup_priority(),
            )
            created.append({
                "story": story.title,
                "task_id": task.get("id"),
                "url": task.get("url"),
            })
        except Exception as e:  # noqa: BLE001 — isolate per-story failures
            logger.warning("ClickUp push failed for story %r: %s", story.title, e)
            errors.append({"story": story.title, "error": str(e)})

    return {"created": created, "errors": errors}


# ── Jira ──────────────────────────────────────────────────────────────────────


def _story_to_adf(story: Story) -> dict[str, Any]:
    """Render the dual-layer ticket as an ADF document: the human story,
    an Acceptance criteria section, and the machine/meta footer (trace,
    route, owner/blocks, dependencies, [P], walking skeleton)."""
    blocks: list[dict[str, Any]] = [jira_oauth.adf_paragraph(story.body.strip())]
    if story.acceptance_criteria:
        blocks.append(jira_oauth.adf_heading("Acceptance criteria"))
        blocks.append(jira_oauth.adf_bullet_list(story.acceptance_criteria))
    meta = story.meta_lines()
    if story.priority:
        # Jira's priority field is screen-dependent (400s where unconfigured),
        # so the suggestion travels in the meta block instead.
        meta = [f"Suggested priority: {story.priority}"] + meta
    if meta:
        blocks.append(jira_oauth.adf_heading("Ticket metadata"))
        blocks.append(jira_oauth.adf_bullet_list(meta))
    return jira_oauth.adf_document(blocks)


def _story_labels(story: Story) -> list[str]:
    """Jira labels carrying the skill's routing so boards can filter on it.
    Jira labels reject spaces — keep these kebab-case."""
    labels = ["sprntly"]
    if story.route:
        labels.append(story.route)
    if story.kind == "decision":
        labels.append("decision-ticket")
    if story.walking_skeleton:
        labels.append("walking-skeleton")
    return labels


def push_stories_to_jira(
    company_id: str,
    project_id: str,
    issue_type_id: str,
    stories: Iterable[Story],
) -> dict[str, Any]:
    """Create one Jira issue per story in the given project/issue type.

    Returns `{"created": [{story, issue_key, url}], "errors": [{story, error}]}`.
    Error-isolated like the ClickUp path. Raises HTTPException(404) up-front
    if Jira isn't connected.
    """
    access_token, token_json = jira_oauth.get_valid_access_token(company_id)
    cloud_id = token_json.get("cloud_id") or ""
    if not cloud_id:
        raise HTTPException(
            500, "Jira connection has no cloud_id — disconnect and reconnect"
        )
    site_url = token_json.get("site_url")

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for story in stories:
        try:
            issue = jira_oauth.create_issue(
                access_token,
                cloud_id,
                project_id=project_id,
                issue_type_id=issue_type_id,
                summary=story.title,
                description_adf=_story_to_adf(story),
                labels=_story_labels(story),
                site_url=site_url,
            )
            created.append({
                "story": story.title,
                "issue_key": issue.get("key"),
                "url": issue.get("url"),
            })
        except Exception as e:  # noqa: BLE001 — isolate per-story failures
            logger.warning("Jira push failed for story %r: %s", story.title, e)
            errors.append({"story": story.title, "error": str(e)})

    return {"created": created, "errors": errors}

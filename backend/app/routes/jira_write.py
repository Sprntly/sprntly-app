"""Apply a Jira change the user confirmed in chat.

  POST /v1/jira/write   -> apply field updates / a status transition / a comment

This is the ONLY path from the chat to a Jira mutation, and it is deliberately
not reachable by the agent. The lookup skill can propose a change
(`jira_propose_change`), which comes back on the answer as `_pending_jira_change`
and is rendered as a confirmation card; applying it takes an explicit request
from the browser, i.e. a person clicking Confirm.

That split is the whole safety model. A chat command is a sentence, and a
sentence can be misheard — the wrong issue, a misread date, a field the user
never meant. Jira has no undo, and a ticket a whole team is looking at is a bad
place to discover a parsing bug. So the model gets to say what it WOULD do, and
the user decides whether it happens.

Every change is re-validated here against the issue's own editmeta and available
transitions rather than trusted from the request: the client is the user's, but
the payload still crosses a network boundary, and validating at write time is
what makes "not editable" a clean error instead of a raw Jira 400.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import WorkspaceContext, require_workspace
from app.connectors import jira_fetch
from app.design_agent.csrf import require_same_origin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/jira", tags=["jira"])


class JiraWriteIn(BaseModel):
    issue_key: str = Field(..., min_length=2, max_length=64)
    # Jira FIELD IDS → values, already shaped by the agent against editmeta
    # (e.g. {"duedate": "2028-08-31"}). Re-checked here before anything is sent.
    fields: dict[str, Any] | None = None
    # Target workflow status NAME. Separate from `fields` because Jira does not
    # edit status as a field — it walks a transition.
    to_status: str | None = None
    comment: str | None = None


@router.post("/write", dependencies=[Depends(require_same_origin)])
def apply_change(
    body: JiraWriteIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Apply a confirmed change to one Jira issue.

    Partial success is reported honestly rather than collapsed into one boolean:
    a request can set two fields, move status and post a comment, and any part
    can fail on its own (a field the user may not set, a transition that is not
    legal from the current status). The response says exactly what landed, so the
    chat can tell the user rather than implying everything worked.
    """
    session = jira_fetch.open_session(company.company_id)
    if session is None:
        raise HTTPException(
            409, "Jira isn't connected (or its access needs refreshing)."
        )

    key = body.issue_key.strip()
    results: dict[str, Any] = {"issue_key": key}
    applied: list[str] = []
    failed: list[str] = []

    if body.fields:
        try:
            r = jira_fetch.apply_field_updates(session, key, body.fields)
        except Exception as e:  # noqa: BLE001 — surface as a chat-readable error
            logger.warning("jira write: fields failed on %s: %s", key, e)
            r = {"ok": False, "error": "Jira rejected the field update."}
        results["fields"] = r
        (applied if r.get("ok") else failed).append("fields")

    if body.to_status:
        try:
            r = jira_fetch.apply_transition(session, key, body.to_status)
        except Exception as e:  # noqa: BLE001
            logger.warning("jira write: transition failed on %s: %s", key, e)
            r = {"ok": False, "error": "Jira rejected the status change."}
        results["status"] = r
        (applied if r.get("ok") else failed).append("status")

    if body.comment:
        try:
            r = jira_fetch.add_comment(session, key, body.comment)
        except Exception as e:  # noqa: BLE001
            logger.warning("jira write: comment failed on %s: %s", key, e)
            r = {"ok": False, "error": "Jira rejected the comment."}
        results["comment"] = r
        (applied if r.get("ok") else failed).append("comment")

    if not applied and not failed:
        raise HTTPException(400, "Nothing to change: no fields, status or comment.")

    results["ok"] = not failed
    results["applied"] = applied
    results["failed"] = failed
    return results

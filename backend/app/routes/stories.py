"""User-story endpoints — generate from a PRD, then push into a tracker.

  POST /v1/stories/generate     {prd_id}                      -> generated stories (no write)
  POST /v1/stories/lists                                       -> ClickUp lists to pick a target
  POST /v1/stories/jira/projects                               -> Jira projects to pick a target
  POST /v1/stories/jira/issue-types {project_id}               -> issue types for that project
  POST /v1/stories/push         {tracker, <target>, stories}  -> create the stories in the tracker

Generation and push are kept SEPARATE on purpose: generation never touches the
user's tracker, so the user reviews the stories before any are written. Push is
the explicit, outward-facing write. All routes require_company (tenant scoped).

Push targets by tracker:
  clickup (default) -> list_id
  jira              -> project_id + issue_type_id
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.connectors import clickup_oauth, jira_oauth
from app.stories.generate import (
    PRDNotFoundError,
    Story,
    generate_user_stories,
)
from app.stories.push import (
    ClickUpNotConnectedError,
    push_stories_to_clickup,
    push_stories_to_jira,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/stories", tags=["stories"])


class GenerateIn(BaseModel):
    prd_id: int | None = Field(default=None, ge=1)
    insight: str | None = None


class StoryIn(BaseModel):
    title: str
    body: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: str | None = None
    route: str | None = None
    kind: str = "build"
    trace: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    parallel: bool = False
    walking_skeleton: bool = False
    owner: str | None = None
    blocks: list[str] = Field(default_factory=list)
    criteria_generated: bool = False

    def to_story(self) -> Story:
        # Story.from_dict re-applies skill invariants (e.g. decision tickets
        # are always needs-human) even on user-edited payloads.
        return Story.from_dict(self.model_dump())


class PushIn(BaseModel):
    tracker: str = "clickup"
    # ClickUp target
    list_id: str | None = None
    # Jira target
    project_id: str | None = None
    issue_type_id: str | None = None
    stories: list[StoryIn] = Field(..., min_length=1)


class JiraIssueTypesIn(BaseModel):
    project_id: str = Field(..., min_length=1)


@router.post("/generate")
def generate(
    body: GenerateIn,
    company: CompanyContext = Depends(require_company),
):
    """Generate user stories from a PRD (or a free-form insight).

    Returns the stories for the user to review. Does NOT write anything to
    a tracker — call /v1/stories/push separately once reviewed.
    """
    if (body.prd_id is None) == (body.insight is None):
        raise HTTPException(400, "provide exactly one of prd_id or insight")
    try:
        stories = generate_user_stories(
            company.company_id, prd_id=body.prd_id, insight=body.insight,
        )
    except PRDNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    return {"stories": [s.to_dict() for s in stories]}


@router.post("/lists")
def clickup_lists(company: CompanyContext = Depends(require_company)):
    """List the ClickUp lists this company can push into (target picker).

    404 if ClickUp isn't connected.
    """
    from app.stories.push import _clickup_access_token

    try:
        token = _clickup_access_token(company.company_id)
    except ClickUpNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    return {"lists": clickup_oauth.list_lists(token)}


@router.post("/jira/projects")
def jira_projects(company: CompanyContext = Depends(require_company)):
    """List the Jira projects this company can push into (target picker).

    404 if Jira isn't connected.
    """
    token, token_json = jira_oauth.get_valid_access_token(company.company_id)
    cloud_id = token_json.get("cloud_id") or ""
    if not cloud_id:
        raise HTTPException(
            500, "Jira connection has no cloud_id — disconnect and reconnect"
        )
    return {
        "projects": jira_oauth.list_projects(token, cloud_id),
        "site_url": token_json.get("site_url"),
    }


@router.post("/jira/issue-types")
def jira_issue_types(
    body: JiraIssueTypesIn,
    company: CompanyContext = Depends(require_company),
):
    """List the non-subtask issue types for one Jira project (target picker,
    second step). 404 if Jira isn't connected.
    """
    token, token_json = jira_oauth.get_valid_access_token(company.company_id)
    cloud_id = token_json.get("cloud_id") or ""
    if not cloud_id:
        raise HTTPException(
            500, "Jira connection has no cloud_id — disconnect and reconnect"
        )
    return {
        "issue_types": jira_oauth.list_issue_types(
            token, cloud_id, body.project_id
        )
    }


@router.post("/push")
def push(
    body: PushIn,
    company: CompanyContext = Depends(require_company),
):
    """Create the given stories in the chosen tracker (explicit write).

    tracker="clickup" (default) needs list_id; tracker="jira" needs
    project_id + issue_type_id. 404 if the tracker isn't connected.
    Per-story failures are isolated and reported in `errors` rather than
    failing the whole batch.
    """
    stories = [s.to_story() for s in body.stories]

    if body.tracker == "clickup":
        if not body.list_id:
            raise HTTPException(400, "list_id is required for tracker='clickup'")
        try:
            return push_stories_to_clickup(
                company.company_id, body.list_id, stories
            )
        except ClickUpNotConnectedError as e:
            raise HTTPException(404, str(e)) from e

    if body.tracker == "jira":
        if not body.project_id or not body.issue_type_id:
            raise HTTPException(
                400,
                "project_id and issue_type_id are required for tracker='jira'",
            )
        return push_stories_to_jira(
            company.company_id, body.project_id, body.issue_type_id, stories
        )

    raise HTTPException(400, f"Unknown tracker {body.tracker!r}")

"""User-story endpoints — generate from a PRD, then push into ClickUp.

  POST /v1/stories/generate  {prd_id}            -> generated stories (no write)
  POST /v1/stories/lists                          -> ClickUp lists to pick a target
  POST /v1/stories/push      {list_id, stories}  -> create the stories in ClickUp

Generation and push are kept SEPARATE on purpose: generation never touches the
user's tracker, so the user reviews the stories before any are written. Push is
the explicit, outward-facing write. All routes require_company (tenant scoped).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.connectors import clickup_oauth
from app.stories.generate import (
    PRDNotFoundError,
    Story,
    generate_user_stories,
)
from app.stories.push import ClickUpNotConnectedError, push_stories_to_clickup

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


class PushIn(BaseModel):
    list_id: str = Field(..., min_length=1)
    stories: list[StoryIn] = Field(..., min_length=1)


@router.post("/generate")
def generate(
    body: GenerateIn,
    company: CompanyContext = Depends(require_company),
):
    """Generate user stories from a PRD (or a free-form insight).

    Returns the stories for the user to review. Does NOT write anything to
    ClickUp — call /v1/stories/push separately once reviewed.
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


@router.post("/push")
def push(
    body: PushIn,
    company: CompanyContext = Depends(require_company),
):
    """Create the given stories as tasks in a ClickUp list (explicit write).

    404 if ClickUp isn't connected. Per-story failures are isolated and
    reported in `errors` rather than failing the whole batch.
    """
    stories = [
        Story(
            title=s.title,
            body=s.body,
            acceptance_criteria=s.acceptance_criteria,
            priority=s.priority,
            route=s.route,
        )
        for s in body.stories
    ]
    try:
        result = push_stories_to_clickup(company.company_id, body.list_id, stories)
    except ClickUpNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    return result

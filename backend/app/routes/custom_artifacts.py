"""Custom artifacts — team documents of any kind (the "Others" library).

  POST   /v1/custom-artifacts                  -> create a document
  GET    /v1/custom-artifacts                  -> this company's documents
  GET    /v1/custom-artifacts/by-conversation/{cid} -> the ones born in a chat
  GET    /v1/custom-artifacts/{id}             -> one document, with its body
  PATCH  /v1/custom-artifacts/{id}             -> save a title / kind / body
  DELETE /v1/custom-artifacts/{id}             -> remove it

Generation is NOT here — that is the chat's job and lands in a later slice.
These routes are the storage surface underneath it, and the one the editor
talks to.

TENANT GATE on every route: `require_company` resolves the caller's company
from the JWT and `db.custom_artifacts` filters `company_id` IN THE QUERY, so a
document belonging to another company reads as absent. Both cases raise 404,
never 403 — a foreign tenant must not be able to tell "exists but not yours"
from "doesn't exist". RLS is bypassed (service-role key), so this is the ONLY
tenant boundary these routes have.

NO PER-USER GATE, deliberately. Any member of the company can read and write
any document in it: that is what "shared within the team" means, and it is the
same posture reports and ticket sets already have. `created_by`/`updated_by`
are attribution only. Explicit per-person sharing is a later slice; when it
arrives it adds a check here, and until then a route that filtered on
`created_by` would silently make the library private — the exact bug #1061
fixed on the share-link path.

THE BODY IS SANITIZED ON EVERY WRITE (app/custom_artifact_html.py), not on
read. Sanitizing on write means the stored document is the safe one, so every
consumer — this API, the PDF renderer, a future export, a future share link —
is covered without each having to remember. Read paths return what is stored.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.custom_artifact_html import sanitize_artifact_html
from app.db.conversations import conversation_belongs_to_company
from app.db.custom_artifacts import (
    VersionConflict,
    create_artifact,
    delete_artifact,
    get_artifact,
    list_artifacts_for_company,
    list_artifacts_for_conversation,
    update_artifact,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/custom-artifacts", tags=["custom-artifacts"])

# Same ceiling the db module enforces on the stored column. Checked here too so
# an oversized body is a 413 with a reason, rather than a silent truncation the
# user only discovers when the end of their document is missing.
MAX_BODY_CHARS = 400_000


def _public(row: dict, *, with_body: bool = True) -> dict:
    """One document as the web reads it.

    Empty strings are returned rather than omitted: the editor renders every
    field and decides its own placeholder copy, so the API never decides that a
    blank title should disappear.
    """
    out = {
        "id": row["id"],
        "kind": row.get("kind") or "",
        "title": row.get("title") or "",
        "status": row.get("status") or "ready",
        "version": int(row.get("version") or 1),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "conversation_id": row.get("conversation_id"),
        "created_by": row.get("created_by"),
        "updated_by": row.get("updated_by"),
    }
    if with_body:
        out["body_html"] = row.get("body_html") or ""
    return out


def _require_owned(artifact_id: int, company_id: str) -> dict:
    """The document, or 404. The company filter lives in the query."""
    row = get_artifact(company_id, artifact_id)
    if row is None:
        raise HTTPException(404, "Artifact not found")
    return row


class CreateIn(BaseModel):
    # Every field is optional: "New document" from the library creates an empty
    # one and the user names it by typing, exactly as a new Google Doc behaves.
    kind: str = ""
    title: str = ""
    body_html: str = ""
    conversation_id: int | None = None


@router.post("")
def create(
    body: CreateIn,
    company: CompanyContext = Depends(require_company),
):
    """Create a document. Returns the full row, including its id and version."""
    if len(body.body_html or "") > MAX_BODY_CHARS:
        raise HTTPException(413, "Document is too large")
    # `conversation_id` is the ONE id on this surface the CLIENT chooses, which
    # makes it the one that has to be checked. Conversation ids are sequential
    # integers, and the artifacts listing resolves a document's conversation
    # into a TITLE — so an unchecked id lets a caller attach their own document
    # to another tenant's chat and read that chat's title back out of their own
    # library. Storing only ids the caller owns closes it at the source, which
    # also covers every future reader of the column.
    if body.conversation_id is not None and not conversation_belongs_to_company(
        body.conversation_id, company.company_id
    ):
        raise HTTPException(404, "Conversation not found")
    row = create_artifact(
        company.company_id,
        kind=body.kind,
        title=body.title,
        body_html=sanitize_artifact_html(body.body_html),
        conversation_id=body.conversation_id,
        created_by=company.user_id,
    )
    return _public(row)


@router.get("")
def list_all(company: CompanyContext = Depends(require_company)):
    """This company's documents, newest first, WITHOUT their bodies."""
    rows = list_artifacts_for_company(company.company_id)
    return {"artifacts": [_public(r, with_body=False) for r in rows]}


@router.get("/by-conversation/{conversation_id}")
def list_for_conversation(
    conversation_id: int,
    company: CompanyContext = Depends(require_company),
):
    """The documents born in one chat, newest first (the thread-resume read)."""
    rows = list_artifacts_for_conversation(company.company_id, conversation_id)
    return {"artifacts": [_public(r, with_body=False) for r in rows]}


@router.get("/{artifact_id}")
def get_one(
    artifact_id: int,
    company: CompanyContext = Depends(require_company),
):
    """One document with its body."""
    return _public(_require_owned(artifact_id, company.company_id))


class UpdateIn(BaseModel):
    # None means "don't touch this field", so a body autosave never clobbers a
    # title someone renamed in another tab, and vice versa.
    title: str | None = None
    kind: str | None = None
    body_html: str | None = None
    # The version the editor started from. Optional: omitting it accepts
    # last-write-wins, which is what a rename from the library row does.
    base_version: int | None = Field(default=None, ge=1)


@router.patch("/{artifact_id}")
def update(
    artifact_id: int,
    body: UpdateIn,
    company: CompanyContext = Depends(require_company),
):
    """Save an edit.

    409 when `base_version` no longer matches — someone else saved first. The
    response carries THEIR version of the document so the editor can say who
    moved it and offer the current text, rather than dropping the user's work
    on the floor with a bare error.
    """
    if body.body_html is not None and len(body.body_html) > MAX_BODY_CHARS:
        raise HTTPException(413, "Document is too large")
    # No ownership pre-read here: `update_artifact` resolves the row
    # company-filtered and returns None for one that is absent OR foreign,
    # which becomes the same 404 below. A pre-read would be a second round trip
    # buying a boundary the writer already enforces.
    try:
        row = update_artifact(
            company.company_id,
            artifact_id,
            title=body.title,
            kind=body.kind,
            body_html=(
                sanitize_artifact_html(body.body_html)
                if body.body_html is not None
                else None
            ),
            base_version=body.base_version,
            updated_by=company.user_id,
        )
    except VersionConflict as exc:
        current = exc.current
        raise HTTPException(
            status_code=409,
            detail={
                "error": "version_conflict",
                "current": _public(current) if current else None,
            },
        )
    if row is None:
        # Deleted between the ownership read and the write.
        raise HTTPException(404, "Artifact not found")
    return _public(row)


@router.delete("/{artifact_id}")
def remove(
    artifact_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Delete a document. 404 when absent or foreign."""
    if not delete_artifact(company.company_id, artifact_id):
        raise HTTPException(404, "Artifact not found")
    return {"deleted": True}

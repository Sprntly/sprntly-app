"""Conversation history endpoints — persist chat threads to Supabase.

  GET    /v1/conversations               -> list the CALLER'S conversations
  POST   /v1/conversations               -> create a new conversation (stamped with the caller)
  PATCH  /v1/conversations/{id}          -> update title/reply/pinned/prd_id
  DELETE /v1/conversations/{id}          -> delete a conversation

Chats are PER-USER: every row is stamped with the creating member's user_id
and only that member can list/read/update/delete it — teammates in the same
workspace never see each other's chats (PRD chats included). Only artifacts
(PRDs, prototypes, evidence) are workspace-shared.

Legacy rows created before stamping (user_id IS NULL) cannot be attributed to
an owner, so they are hidden from everyone — strict per-user privacy beats
resurfacing chats whose author is unknown.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app import attachments_storage
from app.auth import (
    CompanyContext,
    WorkspaceContext,
    require_company,
    require_workspace,
)
from app.db.client import require_client, utc_now
from app.design_agent.csrf import require_same_origin  # server-side CSRF/Origin gate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


class ConversationIn(BaseModel):
    title: str = Field(..., min_length=1)
    preview: str = ""
    agent_type: str = "ask"
    query: str = ""
    reply: str = ""
    pinned: bool = False
    # The PRD this conversation is about, when opened from a PRD tab. Lets a
    # reopened PRD rehydrate its earlier chat turns via GET /by-prd/{prd_id}.
    prd_id: int | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    preview: str | None = None
    query: str | None = None
    reply: str | None = None
    pinned: bool | None = None
    # Back-patched once known: command flows (import a doc / "generate a PRD for
    # X") create the conversation from the seed turn BEFORE the async generate
    # returns the prd_id, so it's first stored as null. Setting it here lets a
    # reopened-from-history chat rebind to its PRD (by-prd lookup + panel reopen).
    prd_id: int | None = None


def _get_owned_conversation(
    c: Any, conversation_id: int, company: CompanyContext
) -> dict[str, Any] | None:
    """The conversation iff it belongs to this company AND the caller owns it.
    None otherwise (including legacy user_id-NULL rows) — callers 404."""
    resp = (
        c.table("conversations")
        .select("*")
        .eq("id", conversation_id)
        .eq("company_id", company.company_id)
        .eq("user_id", company.user_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


@router.get("")
def list_conversations(
    company: WorkspaceContext = Depends(require_workspace),
):
    """List the CALLER'S conversations in the ACTIVE WORKSPACE, newest first."""
    c = require_client()
    resp = (
        c.table("conversations")
        .select("*")
        .eq("company_id", company.company_id)
        .eq("workspace_id", company.workspace_id)
        .eq("user_id", company.user_id)
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )
    return {"conversations": resp.data or []}


@router.post("")
def create_conversation(
    body: ConversationIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Create a new conversation, owned by the calling user, in the active
    workspace."""
    c = require_client()
    row: dict[str, Any] = {
        "company_id": company.company_id,
        "workspace_id": company.workspace_id,
        # Chats are per-user: stamp the creator so list/read stay private.
        "user_id": company.user_id,
        "title": body.title,
        "preview": body.preview,
        "agent_type": body.agent_type,
        "query": body.query,
        "reply": body.reply,
        "pinned": body.pinned,
    }
    if body.prd_id is not None:
        row["prd_id"] = body.prd_id
    resp = c.table("conversations").insert(row).execute()
    return resp.data[0] if resp.data else {}


# ── Attachment files (the ORIGINAL uploaded document, not just extracted text) ──
# Declared BEFORE the /{conversation_id} routes: the file is staged on SEND, before
# its turn (and often its conversation) exists, so these are workspace-scoped, not
# conversation-scoped. Storing the raw file lets a reopened chat render the real
# document — PDF/image inline, everything downloadable — via a short-lived signed
# URL (routes/attachments_storage), the same Bearer-authed-endpoint→public-URL
# pattern the OAuth start + bundle share use.


@router.post(
    "/attachments",
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
async def upload_attachment(
    file: UploadFile = File(...),
    company: WorkspaceContext = Depends(require_workspace),
):
    """Stage an uploaded chat file; return its storage key + sniffed metadata.

    Empty → 400, oversize → 413, unsupported extension → 422 (mirrors the
    screenshot/PRD-import upload guards)."""
    ext = attachments_storage.ext_of(file.filename or "")
    if not attachments_storage.is_supported_ext(ext):
        raise HTTPException(422, "Unsupported file type.")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")
    if len(data) > attachments_storage.MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, "File too large (max 25 MB).")
    key = await attachments_storage.stage_attachment(
        workspace_id=company.workspace_id, data=data, ext=ext
    )
    return {
        "key": key,
        "name": file.filename or f"file.{ext}",
        "mime": attachments_storage.media_type_for_key(key),
        "size": len(data),
    }


@router.get("/attachments/sign")
def sign_attachment(
    key: str,
    name: str = "",
    company: WorkspaceContext = Depends(require_workspace),
):
    """Mint fresh signed (view + download) URLs for a stored attachment key.

    The key embeds the workspace prefix; a key outside the caller's workspace is
    refused (404 — never leak that it exists). Re-signed on every viewer open so a
    permanent chat always resolves a live URL after the short TTL elapses."""
    try:
        urls = attachments_storage.attachment_urls(
            workspace_id=company.workspace_id, key=key, filename=name,
        )
    except ValueError:
        raise HTTPException(404, "Attachment not found")
    return {**urls, "mime": attachments_storage.media_type_for_key(key)}


@router.get("/by-prd/{prd_id}")
def get_conversation_by_prd(
    prd_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Return the CALLER'S most recent conversation for a PRD (plus its turns),
    so a reopened PRD tab can rehydrate their prior chat. PRD chats are
    per-user — a teammate reopening the same PRD gets their own (or no)
    conversation, never someone else's. Empty (not 404) when the caller has no
    saved conversation for the PRD yet. (Company-scoped: the PRD id itself is
    already workspace-gated where it's read, and chats stay per-user.)"""
    c = require_client()
    conv = (
        c.table("conversations")
        .select("*")
        .eq("company_id", company.company_id)
        .eq("prd_id", prd_id)
        .eq("user_id", company.user_id)
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    conversation = conv.data[0] if conv.data else None
    if conversation is None:
        return {"conversation": None, "turns": []}
    turns = (
        c.table("conversation_turns")
        .select("*")
        .eq("conversation_id", conversation["id"])
        .order("created_at")
        .execute()
    )
    return {"conversation": conversation, "turns": turns.data or []}


@router.patch("/{conversation_id}")
def update_conversation(
    conversation_id: int,
    body: ConversationUpdate,
    company: CompanyContext = Depends(require_company),
):
    """Update a conversation (title, reply, pinned, etc.) — owner only."""
    c = require_client()
    if _get_owned_conversation(c, conversation_id, company) is None:
        raise HTTPException(404, "Conversation not found")
    patch: dict[str, Any] = {"updated_at": utc_now()}
    if body.title is not None:
        patch["title"] = body.title
    if body.preview is not None:
        patch["preview"] = body.preview
    if body.query is not None:
        patch["query"] = body.query
    if body.reply is not None:
        patch["reply"] = body.reply
    if body.pinned is not None:
        patch["pinned"] = body.pinned
    if body.prd_id is not None:
        patch["prd_id"] = body.prd_id
    resp = (
        c.table("conversations")
        .update(patch)
        .eq("id", conversation_id)
        .eq("company_id", company.company_id)
        .execute()
    )
    if not resp.data:
        raise HTTPException(404, "Conversation not found")
    return resp.data[0]


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Delete a conversation (turns cascade-delete via FK) — owner only."""
    c = require_client()
    if _get_owned_conversation(c, conversation_id, company) is None:
        raise HTTPException(404, "Conversation not found")
    c.table("conversations").delete().eq(
        "id", conversation_id
    ).eq("company_id", company.company_id).execute()
    return {"ok": True}


# ── Turns (messages within a conversation) ──


class TurnAttachment(BaseModel):
    """Extracted text of a file the user attached to this turn. Persisted so a
    reloaded thread (and the chat→PRD flow) can still see documents attached
    earlier in the conversation — content caps mirror the ask path's clamps.

    `content` may be EMPTY: a document imported straight to a PRD (the "generate a
    PRD" command over a file) has no in-chat extracted text — the file BECOMES the
    PRD — but its name is still persisted as a name-only chip so the reopened
    thread shows what the user attached beside their command. Empty-content
    attachments are skipped by the chat→PRD grounding (frontend conversationPrdDocs).

    `key`/`mime` point at the ORIGINAL file stashed in storage (POST
    /v1/conversations/attachments) so a reopened chat can render the real document
    (PDF/image inline, everything downloadable) — not just the extracted text.
    Null on legacy turns and on text pasted without an upload."""
    name: str = Field(..., min_length=1, max_length=300)
    content: str = Field(..., max_length=60_000)
    key: str | None = Field(default=None, max_length=400)
    mime: str | None = Field(default=None, max_length=200)
    size: int | None = Field(default=None, ge=0)


class TurnIn(BaseModel):
    role: str = "user"  # "user" or "assistant"
    content: str = Field(..., min_length=1)
    attachments: list[TurnAttachment] | None = Field(default=None, max_length=8)


@router.get("/{conversation_id}/turns")
def list_turns(
    conversation_id: int,
    company: CompanyContext = Depends(require_company),
):
    """List all turns in a conversation, oldest first — owner only."""
    c = require_client()
    if _get_owned_conversation(c, conversation_id, company) is None:
        raise HTTPException(404, "Conversation not found")
    resp = (
        c.table("conversation_turns")
        .select("*")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
    )
    return {"turns": resp.data or []}


@router.post("/{conversation_id}/turns")
def add_turn(
    conversation_id: int,
    body: TurnIn,
    company: CompanyContext = Depends(require_company),
):
    """Add a turn (message) to a conversation — owner only."""
    c = require_client()
    if _get_owned_conversation(c, conversation_id, company) is None:
        raise HTTPException(404, "Conversation not found")
    row: dict[str, Any] = {
        "conversation_id": conversation_id,
        "role": body.role,
        "content": body.content,
    }
    if body.attachments:
        # exclude_none keeps the stored shape minimal — a text-only attachment
        # stays {name, content}; key/mime/size appear only when a file was stored.
        row["attachments"] = [a.model_dump(exclude_none=True) for a in body.attachments]
    resp = c.table("conversation_turns").insert(row).execute()
    # Update conversation preview + timestamp. Only overwrite preview on user
    # turns — assistant turns should NOT blank out the last user message shown
    # in the chat-history list (ChatsScreen).
    patch: dict[str, Any] = {"updated_at": utc_now()}
    if body.role == "user":
        patch["preview"] = body.content[:200]
    c.table("conversations").update(patch).eq("id", conversation_id).execute()
    return resp.data[0] if resp.data else {}

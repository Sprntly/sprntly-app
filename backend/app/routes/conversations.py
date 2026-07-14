"""Conversation history endpoints — persist chat threads to Supabase.

  GET    /v1/conversations               -> list the CALLER'S conversations
  POST   /v1/conversations               -> create a new conversation (stamped with the caller)
  PATCH  /v1/conversations/{id}          -> update title/reply/pinned
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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.db.client import require_client, utc_now

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
    company: CompanyContext = Depends(require_company),
):
    """List the CALLER'S conversations, newest first."""
    c = require_client()
    resp = (
        c.table("conversations")
        .select("*")
        .eq("company_id", company.company_id)
        .eq("user_id", company.user_id)
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )
    return {"conversations": resp.data or []}


@router.post("")
def create_conversation(
    body: ConversationIn,
    company: CompanyContext = Depends(require_company),
):
    """Create a new conversation, owned by the calling user."""
    c = require_client()
    row: dict[str, Any] = {
        "company_id": company.company_id,
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


@router.get("/by-prd/{prd_id}")
def get_conversation_by_prd(
    prd_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Return the CALLER'S most recent conversation for a PRD (plus its turns),
    so a reopened PRD tab can rehydrate their prior chat. PRD chats are
    per-user — a teammate reopening the same PRD gets their own (or no)
    conversation, never someone else's. Empty (not 404) when the caller has no
    saved conversation for the PRD yet."""
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


class TurnIn(BaseModel):
    role: str = "user"  # "user" or "assistant"
    content: str = Field(..., min_length=1)


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
    resp = c.table("conversation_turns").insert({
        "conversation_id": conversation_id,
        "role": body.role,
        "content": body.content,
    }).execute()
    # Update conversation preview + timestamp. Only overwrite preview on user
    # turns — assistant turns should NOT blank out the last user message shown
    # in the chat-history list (ChatsScreen).
    patch: dict[str, Any] = {"updated_at": utc_now()}
    if body.role == "user":
        patch["preview"] = body.content[:200]
    c.table("conversations").update(patch).eq("id", conversation_id).execute()
    return resp.data[0] if resp.data else {}

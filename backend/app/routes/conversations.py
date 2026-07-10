"""Conversation history endpoints — persist chat threads to Supabase.

  GET    /v1/conversations               -> list all for this company
  POST   /v1/conversations               -> create a new conversation
  PATCH  /v1/conversations/{id}          -> update title/reply/pinned
  DELETE /v1/conversations/{id}          -> delete a conversation
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


@router.get("")
def list_conversations(
    company: CompanyContext = Depends(require_company),
):
    """List all conversations for this company, newest first."""
    c = require_client()
    resp = (
        c.table("conversations")
        .select("*")
        .eq("company_id", company.company_id)
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
    """Create a new conversation."""
    c = require_client()
    row: dict[str, Any] = {
        "company_id": company.company_id,
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
    """Return the most recent conversation for a PRD (plus its turns), so a
    reopened PRD tab can rehydrate its prior chat. Empty (not 404) when the PRD
    has no saved conversation yet."""
    c = require_client()
    conv = (
        c.table("conversations")
        .select("*")
        .eq("company_id", company.company_id)
        .eq("prd_id", prd_id)
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    if not conv.data:
        return {"conversation": None, "turns": []}
    conversation = conv.data[0]
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
    """Update a conversation (title, reply, pinned, etc.)."""
    c = require_client()
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
    """Delete a conversation (turns cascade-delete via FK)."""
    c = require_client()
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
    """List all turns in a conversation, oldest first."""
    c = require_client()
    # Verify ownership
    conv = c.table("conversations").select("id").eq(
        "id", conversation_id
    ).eq("company_id", company.company_id).limit(1).execute()
    if not conv.data:
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
    """Add a turn (message) to a conversation."""
    c = require_client()
    # Verify ownership
    conv = c.table("conversations").select("id").eq(
        "id", conversation_id
    ).eq("company_id", company.company_id).limit(1).execute()
    if not conv.data:
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

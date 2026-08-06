"""Conversation ↔ PRD binding.

The chat surface opens a PRD command tab (import a document, "generate a PRD
for X") and persists the seed turn as a conversation BEFORE the PRD exists —
the prd_id simply isn't known until the generate/import call returns. The
client used to back-patch the link afterwards from a React effect, which meant
navigating away (or reloading) mid-generation left the conversation with
prd_id=NULL forever: reopening it from history came back as a plain, PRD-less
chat with no way to reach the document it had just produced.

Binding server-side at PRD-creation time closes that window: once the route has
both ids it writes the link itself, so the browser is free to leave.
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.client import require_client

logger = logging.getLogger(__name__)


def conversation_belongs_to_company(conversation_id: int, company_id: str) -> bool:
    """Whether this conversation exists AND belongs to this company.

    Conversation ids are sequential integers, so any route that accepts one from
    the client has to prove ownership before storing it — otherwise a caller can
    stamp their own artifact with a foreign tenant's conversation id and read
    that chat's title back out of the artifacts listing. Callers turn False into
    404 (never 403): "exists but not yours" and "doesn't exist" must be
    indistinguishable.
    """
    rows = (
        require_client().table("conversations")
        .select("id")
        .eq("id", conversation_id)
        .eq("company_id", company_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return bool(rows)


def bind_conversation_to_prd(
    conversation_id: int,
    prd_id: int,
    company_id: str,
    user_id: str | None,
) -> bool:
    """Point `conversation_id` at `prd_id`. True if a row was updated.

    Ownership-scoped exactly like the conversations routes (per-user chats
    within a company), so a caller can never bind someone else's conversation
    by guessing an id. Only fills a NULL prd_id — a conversation already bound
    to a PRD is left alone, so a re-issued command can't silently repoint an
    existing chat at a different document.

    Best-effort by design: this runs inside the fire-and-forget generate/import
    routes, where the PRD itself is what the caller is waiting on. A failure
    here is logged and swallowed rather than failing the generation — the
    client's own back-patch remains as a fallback.
    """
    try:
        c = require_client()
        q = (
            c.table("conversations")
            .update({"prd_id": prd_id})
            .eq("id", conversation_id)
            .eq("company_id", company_id)
            .is_("prd_id", "null")
        )
        # Legacy rows predate user stamping (user_id IS NULL) and are hidden from
        # everyone, so there is nothing to bind when the caller has no user id.
        if user_id:
            q = q.eq("user_id", user_id)
        resp: Any = q.execute()
        return bool(resp.data)
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "Failed to bind conversation %s to PRD %s", conversation_id, prd_id,
            exc_info=True,
        )
        return False


def get_conversation_prd_id(
    conversation_id: int,
    company_id: str,
    user_id: str | None,
) -> int | None:
    """The PRD this conversation is bound to, or None.

    The read half of bind_conversation_to_prd, with the same ownership scoping
    (per-user chats within a company). Used by the chat intent dispatcher to
    resolve "make it shorter" to the PRD this thread produced when the client
    didn't send an explicit prd_id (e.g. a resumed chat whose tab lost its
    local state). Best-effort: any error → None."""
    try:
        c = require_client()
        q = (
            c.table("conversations")
            .select("prd_id")
            .eq("id", conversation_id)
            .eq("company_id", company_id)
            .limit(1)
        )
        if user_id:
            q = q.eq("user_id", user_id)
        rows: Any = q.execute()
        if rows.data:
            return rows.data[0].get("prd_id")
        return None
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "Failed to read PRD binding for conversation %s", conversation_id,
            exc_info=True,
        )
        return None


# ── Evidence half of the binding (mirrors the PRD pair above exactly) ───────
#
# Extends the SAME mechanism to Evidence rather than inventing a parallel one
# (conversations.evidence_id, added alongside conversations.prd_id's existing
# column — see the 20260731 migration): the chat-task command's Evidence
# artifact (generate_task_evidence) gets the identical "bind the commanding
# chat to the artifact server-side, before the caller could navigate away"
# treatment PRDs already had.


def bind_conversation_to_evidence(
    conversation_id: int,
    evidence_id: int,
    company_id: str,
    user_id: str | None,
) -> bool:
    """Point `conversation_id` at `evidence_id`. True if a row was updated.

    Same ownership scoping and "only fills a NULL" semantics as
    bind_conversation_to_prd — see its docstring."""
    try:
        c = require_client()
        q = (
            c.table("conversations")
            .update({"evidence_id": evidence_id})
            .eq("id", conversation_id)
            .eq("company_id", company_id)
            .is_("evidence_id", "null")
        )
        if user_id:
            q = q.eq("user_id", user_id)
        resp: Any = q.execute()
        return bool(resp.data)
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "Failed to bind conversation %s to evidence %s", conversation_id, evidence_id,
            exc_info=True,
        )
        return False


def get_conversation_evidence_id(
    conversation_id: int,
    company_id: str,
    user_id: str | None,
) -> int | None:
    """The Evidence doc this conversation is bound to, or None. Read half of
    bind_conversation_to_evidence — see get_conversation_prd_id."""
    try:
        c = require_client()
        q = (
            c.table("conversations")
            .select("evidence_id")
            .eq("id", conversation_id)
            .eq("company_id", company_id)
            .limit(1)
        )
        if user_id:
            q = q.eq("user_id", user_id)
        rows: Any = q.execute()
        if rows.data:
            return rows.data[0].get("evidence_id")
        return None
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "Failed to read evidence binding for conversation %s", conversation_id,
            exc_info=True,
        )
        return None

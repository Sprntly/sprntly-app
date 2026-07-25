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

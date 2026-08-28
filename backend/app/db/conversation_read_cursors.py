"""Per-(conversation, user) read cursor for an individual project chat
(AD-P3/AD-P20). `conversation_read_cursors` stores ONLY the caller's own
last-read turn id for one conversation.

`set_cursor` is advance-only: `max(existing, new)`, so a stale/out-of-order
write can never move a cursor backward. Used by
`db.conversations._advance_own_cursor` so writing your own turn and leaving
does not flip your own chat to unread.

Mirrors `db/project_delegations.py`'s client/`@retry_on_disconnect` style."""
from __future__ import annotations

from app.db.client import require_client, retry_on_disconnect, utc_now


def get_cursor(conversation_id: int, user_id: str) -> int:
    """The caller's last-read turn id for this conversation, or 0 if no
    cursor row exists yet (never having opened the chat is 'unread from the
    start', not an error)."""
    rows = (
        require_client()
        .table("conversation_read_cursors")
        .select("last_read_turn_id")
        .eq("conversation_id", conversation_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    )
    return rows[0]["last_read_turn_id"] if rows else 0


@retry_on_disconnect
def set_cursor(conversation_id: int, user_id: str, last_read_turn_id: int) -> dict:
    """Upsert the caller's cursor to `last_read_turn_id` — advance-only
    (AC5): clamps to `max(existing, new)` so a stale/out-of-order client
    POST can never move the cursor backward and re-mark already-read turns
    unread. Touches `updated_at`."""
    client = require_client()
    existing = get_cursor(conversation_id, user_id)
    new_value = max(existing, last_read_turn_id)
    return (
        client.table("conversation_read_cursors")
        .upsert(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "last_read_turn_id": new_value,
                "updated_at": utc_now(),
            },
            on_conflict="conversation_id,user_id",
        )
        .execute()
        .data[0]
    )

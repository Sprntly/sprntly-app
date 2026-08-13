"""Per-(conversation, user) read cursor — inputs-only + derive-at-read
(AD-P3/AD-P20). `conversation_read_cursors` stores ONLY the caller's own
last-read turn id for one conversation; "unread" is never stored as a
boolean/count — `unread_for` derives it at read time by comparing against
`latest_individual_turn_id` ([[feedback_prefer-inference-over-stored-derived-state]]).

`latest_individual_turn_id` reuses `db.conversations.list_individual_turns`
(the existing individual-chat reader) rather than a raw `conversation_turns`
select, so the two individual-turn reads can never diverge (DRY) — it is
the caller's OWN individual conversation either way, since every route in
`routes/projects.py` resolves `conv = get_individual_project_chat(project_id,
ctx.user_id)` before calling into this module.

`set_cursor` is advance-only: `max(existing, new)`, so a stale/out-of-order
client POST can never move a cursor backward and re-mark already-read turns
as unread (AC5).

Mirrors `db/project_delegations.py`'s client/`@retry_on_disconnect` style."""
from __future__ import annotations

from app.db.client import require_client, retry_on_disconnect, utc_now
from app.db.conversations import list_individual_turns


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


def latest_individual_turn_id(conversation_id: int) -> int | None:
    """max(conversation_turns.id) for this individual conversation, or None
    if it has no turns yet. Reuses `list_individual_turns` (the existing
    own-conversation-gated reader) rather than reading `conversation_turns`
    directly — the two individual-turn reads must never diverge. Every
    caller here resolves `conversation_id` via the caller's OWN
    `get_individual_project_chat(project_id, ctx.user_id)` first, so
    `user_id` below is always the conversation's own owner."""
    rows = (
        require_client()
        .table("conversations")
        .select("user_id")
        .eq("id", conversation_id)
        .eq("kind", "individual")
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return None
    owner_user_id = rows[0]["user_id"]
    turns = list_individual_turns(conversation_id, owner_user_id)
    return turns[-1]["id"] if turns else None


def unread_for(conversation_id: int, user_id: str) -> bool:
    """Derived, never stored: True iff the conversation has at least one
    turn beyond the caller's cursor. An empty conversation (no turns yet)
    is always False, regardless of cursor state."""
    latest = latest_individual_turn_id(conversation_id)
    if latest is None:
        return False
    return latest > get_cursor(conversation_id, user_id)


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

"""Publish-on-write for a group project chat turn (AD-P21).

One neutral home for the group `turn.created` broadcast so BOTH the group
turns route (`routes/projects.py`) and the ask-completion sink
(`ask_job_runner.py`, where the agent reply is persisted) reach the same
shaping + whitelist without a route<->runner import cycle. The read-DTO
whitelist keeps an internal `conversation_turns` column (e.g.
`attachments`/`client_message_id`) off the wire even if the table gains one.

Best-effort throughout (AD-P22): the turn has ALREADY persisted by the time
this is called, so a re-read or publish hiccup is swallowed here and never
masks the successful write — the client's next `since`-cursor reconcile
(`GET .../group/turns`) closes any dropped-broadcast gap.
"""
from __future__ import annotations

import logging

from app.db import conversations as conversations_db
from app.realtime import publish_broadcast

logger = logging.getLogger(__name__)

# The exact `list_group_turns` read-DTO key set — the hard whitelist applied
# before every `turn.created` broadcast (AD-P21 no-schema-coupling). Mirrors
# the shape `list_group_turns` returns for the poll read, so a realtime turn
# and a reloaded turn render identically.
GROUP_TURN_DTO_KEYS = (
    "id",
    "role",
    "content",
    "author_user_id",
    "author_name",
    "author_job_role",
    "created_at",
    # The FULL structured reply on an assistant turn (answer/key_points/
    # citations + any classify-envelope card data) — on the broadcast too, so
    # a realtime-delivered agent turn renders the same cards a reload does.
    "reply",
    # Execution-run status, attached by `list_group_turns` onto the human turn
    # whose id == the run's source_turn_id (already FE-vocabulary at the DTO
    # edge). On the broadcast too, so the realtime shape matches the poll read.
    "run_status",
    "error_class",
)


def publish_group_turn_created(
    project_id: int, conversation_id: int, turn: dict | None
) -> None:
    """Best-effort publish of one just-persisted group turn (human or agent)
    to the member-authorized `project:{id}` channel as `turn.created`.

    Re-reads the turn through `list_group_turns` so the broadcast carries the
    SAME author-enriched DTO the poll read returns (never a raw DB row), then
    whitelists it. The re-read, the shaping, AND `publish_broadcast` are all
    swallowed here (AD-P22) — `turn` has already persisted, so nothing here
    may raise back into the write path."""
    if not turn:
        return
    try:
        shaped = conversations_db.list_group_turns(
            conversation_id, since=turn["id"] - 1
        )
        dto = next((t for t in shaped if t["id"] == turn["id"]), None)
        if dto is not None:
            publish_broadcast(
                f"project:{project_id}",
                "turn.created",
                {k: dto[k] for k in GROUP_TURN_DTO_KEYS},
            )
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P22: never break the write
        logger.warning(
            "realtime_publish_prep_failed topic=project:%s event=turn.created error_class=%s",
            project_id,
            type(exc).__name__,
        )

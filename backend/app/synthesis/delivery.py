"""Brief delivery — push a freshly generated brief to each recipient's Slack.

Slack is PER-USER: every member who connected their own Slack gets the brief
in their OWN workspace, at their chosen target (a channel, or a DM to
themselves — config.target_type / config.channel_id, set via the Settings
picker / POST /v1/connectors/slack/config).

The message itself is ALWAYS drafted by the brief-nudge skill (the Day-0
announcement) — there is no static fallback. The skill is composed once per
company (one LLM call) and the same copy is fanned out to every recipient.
A draft failure means no Slack post (logged), never a static stand-in.

Delivery is a SIDE EFFECT of brief generation: it must never break or block
the brief itself. Any failure is logged + reported in the return value, not
raised. A user with no Slack connected / no target configured is skipped;
zero connected users ⇒ clean no-op.
"""
from __future__ import annotations

import json
import logging

from app import db
from app.brief_nudge import brief_deep_link, generate_nudge, nudge_slack_blocks
from app.connectors import slack_oauth
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json

logger = logging.getLogger(__name__)


def _deliver_to_one(row: dict, text: str, blocks: list[dict]) -> dict:
    """Deliver the (already skill-drafted) brief message to a single per-user
    Slack connection row. Returns a per-recipient result dict; never raises."""
    user_id = row.get("user_id")
    if row.get("status") != "active":
        return {"user_id": user_id, "delivered": False,
                "reason": "slack_not_connected"}
    config = row.get("config") or {}
    target_type = (config.get("target_type") or slack_oauth.TARGET_CHANNEL).strip()
    # A channel target needs a channel picked; a DM target needs nothing here
    # (it resolves to the installing user's own DM at send time).
    if target_type == slack_oauth.TARGET_CHANNEL and not (
        config.get("channel_id") or "").strip():
        return {"user_id": user_id, "delivered": False,
                "reason": "no_channel_configured"}
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, json.JSONDecodeError) as e:
        logger.error("slack token unreadable for user %s: %s", user_id, e)
        return {"user_id": user_id, "delivered": False,
                "reason": "token_unreadable"}
    bot_token = token_json.get("access_token") or ""
    if not bot_token:
        return {"user_id": user_id, "delivered": False, "reason": "no_bot_token"}
    try:
        # Route to the user's chosen target — their own DM or a channel
        # (self-joining a public channel so not_in_channel can't drop it).
        res = slack_oauth.post_to_target(
            bot_token, config=config,
            authed_user_id=token_json.get("authed_user_id"),
            text=text, blocks=blocks)
        return {"user_id": user_id, "delivered": True,
                "channel": res.get("channel") or config.get("channel_id")}
    except Exception as e:  # noqa: BLE001 — one recipient never breaks the rest
        logger.exception("brief slack delivery failed for user %s", user_id)
        return {"user_id": user_id, "delivered": False, "reason": f"error: {e}"}


def deliver_brief_to_slack(enterprise_id: str, brief: dict) -> dict:
    """Best-effort, PER-USER delivery: draft the brief's Slack announcement
    with the brief-nudge skill (once for the company), then fan it out to
    every member who connected their own Slack and picked a target. Each
    recipient gets it in THEIR own workspace — never a company-shared bot.

    Returns an aggregate {delivered, recipients, reason?}. `delivered` is
    True if at least one recipient received it. Never raises."""
    try:
        rows = db.list_slack_connections(enterprise_id)
        if not rows:
            return {"delivered": False, "reason": "slack_not_connected",
                    "recipients": []}
        # Compose the message ONCE via the skill — no static fallback. A draft
        # failure aborts Slack delivery for this brief (logged, brief intact).
        try:
            deep_link = brief_deep_link()
            nudge = generate_nudge(enterprise_id, brief, 0, deep_link)
            text, blocks = nudge_slack_blocks(nudge, deep_link)
        except Exception as e:  # noqa: BLE001 — generation must not raise
            logger.exception("brief slack draft (skill) failed for %s", enterprise_id)
            return {"delivered": False, "reason": f"generation_error: {e}",
                    "recipients": []}
        recipients = [_deliver_to_one(row, text, blocks) for row in rows]
        any_delivered = any(r.get("delivered") for r in recipients)
        out: dict = {"delivered": any_delivered, "recipients": recipients}
        if not any_delivered:
            # Surface a single representative reason when nobody got it.
            out["reason"] = recipients[0].get("reason", "not_delivered")
        return out
    except Exception as e:  # noqa: BLE001 — delivery never breaks generation
        logger.exception("brief slack delivery failed for %s", enterprise_id)
        return {"delivered": False, "reason": f"error: {e}", "recipients": []}

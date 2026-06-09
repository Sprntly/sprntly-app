"""Brief delivery — push a freshly generated brief to each recipient's Slack.

Slack is PER-USER: every member who connected their own Slack gets the
brief in their OWN workspace + chosen channel. The bot token lives on that
user's connection row; the target channel lives in `config.channel_id` (set
via the Settings channel picker / POST /v1/connectors/slack/config).

Delivery is a SIDE EFFECT of brief generation: it must never break or block
the brief itself. Any failure is logged + reported in the return value, not
raised. A user with no Slack connected / no channel configured is skipped;
zero connected users ⇒ clean no-op.
"""
from __future__ import annotations

import json
import logging

from app import db
from app.config import settings
from app.connectors import slack_oauth
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json

logger = logging.getLogger(__name__)

MAX_INSIGHTS_IN_MESSAGE = 5

_TAG_LABEL = {
    "something_broken": ":wrench: FIX",
    "something_new": ":hammer_and_pick: BUILD",
    "something_better": ":chart_with_upwards_trend: OPTIMIZE",
}


def _brief_blocks(brief: dict) -> tuple[str, list[dict]]:
    """(plain-text fallback, Slack Block Kit blocks) for a brief payload."""
    headline = brief.get("summary_headline") or "Your weekly brief is ready"
    week = brief.get("week_label", "")
    insights = (brief.get("insights") or [])[:MAX_INSIGHTS_IN_MESSAGE]

    lines = []
    for i, ins in enumerate(insights):
        tag = _TAG_LABEL.get(ins.get("tag", ""), ins.get("tag", ""))
        lines.append(f"*{i + 1}. {tag}* — {ins.get('title', '')}")

    app_url = (settings.frontend_url or "https://app.sprntly.ai").rstrip("/")
    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"Weekly Brief — {week}"[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{headline}*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines) or "_No insights._"}},
        {"type": "actions", "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "Open in Sprntly"},
            "url": f"{app_url}/brief",
        }]},
    ]
    fallback = f"Weekly Brief — {week}: {headline}"
    return fallback, blocks


def _deliver_to_one(row: dict, brief: dict) -> dict:
    """Deliver the brief to a single per-user Slack connection row.
    Returns a per-recipient result dict; never raises."""
    user_id = row.get("user_id")
    if row.get("status") != "active":
        return {"user_id": user_id, "delivered": False,
                "reason": "slack_not_connected"}
    config = row.get("config") or {}
    channel = (config.get("channel_id") or "").strip()
    if not channel:
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
        fallback, blocks = _brief_blocks(brief)
        slack_oauth.post_message(bot_token, channel=channel,
                                 text=fallback, blocks=blocks)
        return {"user_id": user_id, "delivered": True, "channel": channel}
    except Exception as e:  # noqa: BLE001 — one recipient never breaks the rest
        logger.exception("brief slack delivery failed for user %s", user_id)
        return {"user_id": user_id, "delivered": False, "reason": f"error: {e}"}


def deliver_brief_to_slack(enterprise_id: str, brief: dict) -> dict:
    """Best-effort, PER-USER delivery: fan the brief out to every member of
    the company who connected their own Slack and picked a channel. Each
    recipient gets it in THEIR own workspace — never a company-shared bot.

    Returns an aggregate {delivered, recipients, reason?}. `delivered` is
    True if at least one recipient received it. Never raises."""
    try:
        rows = db.list_slack_connections(enterprise_id)
        if not rows:
            return {"delivered": False, "reason": "slack_not_connected",
                    "recipients": []}
        recipients = [_deliver_to_one(row, brief) for row in rows]
        any_delivered = any(r.get("delivered") for r in recipients)
        out: dict = {"delivered": any_delivered, "recipients": recipients}
        if not any_delivered:
            # Surface a single representative reason when nobody got it.
            out["reason"] = recipients[0].get("reason", "not_delivered")
        return out
    except Exception as e:  # noqa: BLE001 — delivery never breaks generation
        logger.exception("brief slack delivery failed for %s", enterprise_id)
        return {"delivered": False, "reason": f"error: {e}", "recipients": []}

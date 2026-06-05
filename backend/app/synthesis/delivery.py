"""Brief delivery — push a freshly generated brief to the company's Slack.

Uses Martin's Slack connector (#136-era): bot token on the company's
connection row, target channel in `config.channel_id` (set via the Settings
channel picker / POST /v1/connectors/slack/config).

Delivery is a SIDE EFFECT of brief generation: it must never break or block
the brief itself. Any failure is logged + reported in the return value, not
raised. No Slack connection / no channel configured ⇒ clean no-op.
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


def deliver_brief_to_slack(enterprise_id: str, brief: dict) -> dict:
    """Best-effort delivery. Returns {delivered, reason?}. Never raises."""
    try:
        row = db.get_connection(enterprise_id, slack_oauth.SLACK_PROVIDER)
        if not row or row.get("status") != "active":
            return {"delivered": False, "reason": "slack_not_connected"}
        config = row.get("config") or {}
        channel = (config.get("channel_id") or "").strip()
        if not channel:
            return {"delivered": False, "reason": "no_channel_configured"}
        try:
            token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
        except (TokenEncryptionError, json.JSONDecodeError) as e:
            logger.error("slack token unreadable for %s: %s", enterprise_id, e)
            return {"delivered": False, "reason": "token_unreadable"}
        bot_token = token_json.get("access_token") or ""
        if not bot_token:
            return {"delivered": False, "reason": "no_bot_token"}

        fallback, blocks = _brief_blocks(brief)
        slack_oauth.post_message(bot_token, channel=channel,
                                 text=fallback, blocks=blocks)
        return {"delivered": True, "channel": channel}
    except Exception as e:  # noqa: BLE001 — delivery never breaks generation
        logger.exception("brief slack delivery failed for %s", enterprise_id)
        return {"delivered": False, "reason": f"error: {e}"}

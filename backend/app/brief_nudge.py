"""Brief nudge — generate + send the Slack messages that drive a user to OPEN
their weekly brief, so they engage with the platform.

This is the wiring behind the `brief-nudge` skill. The skill is the METHOD
(what each message says, the Day 0→3 cadence, the honesty rules); this module
is the runtime: it pulls the real figures from a generated brief, binds the
skill through the gateway to compose the Slack/email copy, and fans the Slack
message out to every company member who connected their own Slack — exactly
the same per-user delivery path the brief itself uses (app/synthesis/delivery).

Cadence:
  Day 0 — announcement, sent right after the brief is generated.
  Day 1/2/3 — escalating reminders, sent by the scheduler cycle, ONLY while
              the brief is still unopened (app.db.nudge.is_brief_unopened).

Two guards keep it safe:
  - The whole feature is OFF unless `settings.brief_nudge_enabled` — no real
    user is messaged until it's explicitly turned on.
  - Idempotency: a cadence step that's already in `brief_nudge_sends` is never
    re-sent (app.db.nudge.has_nudge_been_sent).

Like brief delivery, sending is a SIDE EFFECT — every failure is logged and
returned, never raised, so a nudge can't break brief generation.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app import db
from app.config import settings
from app.connectors import slack_oauth
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json
from app.db import nudge as nudge_db
from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

MAX_ITEMS = 3
SLACK = "slack"

# Day-specific intent, handed to the skill so the model escalates honestly.
_DAY_GUIDE = {
    0: "Day 0 — announcement (brief just published): greet, roll up the total "
       "upside, name the top 3 plays.",
    1: "Day 1 — reminder, impact-led (still unopened): lead with the figure "
       "still on the table, the top 2 plays, note both are already drafted.",
    2: "Day 2 — reminder, focused (still unopened): the single biggest play + "
       "its figure, plus the concrete cost of waiting.",
    3: "Day 3 — final reminder (still unopened): one play + figure + the close "
       "date, and an honest promise to pause reminders after this.",
}

_NUDGE_SYSTEM = (
    "You are Sprntly's brief-nudge composer. The bound brief-nudge skill is your "
    "METHOD — follow it exactly. Compose the Slack message AND the email for the "
    "given DAY from the brief data provided. Lead every headline/subject with the "
    "concrete business-impact figure. Use ONE dominant CTA that deep-links to the "
    "provided brief URL. Every number must come from the brief data below — never "
    "invent a figure or manufacture urgency. Item count shrinks across days "
    "(3→2→1→1) and tone escalates (announce→impact-led→cost-of-waiting→final)."
)

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "detail": {"type": "string"},
        "impact": {"type": "string", "description": "the figure, traced to the brief"},
    },
    "required": ["label", "detail"],
}

NUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "slack": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "intro": {"type": "string"},
                "items": {"type": "array", "items": _ITEM_SCHEMA},
                "cta_label": {"type": "string"},
                "cta_url": {"type": "string"},
                "pause_note": {"type": "string", "description": "Day 3 only"},
            },
            "required": ["headline", "intro", "items", "cta_label", "cta_url"],
        },
        "email": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "preheader": {"type": "string"},
                "eyebrow": {"type": "string"},
                "title": {"type": "string"},
                "intro": {"type": "string"},
                "items": {"type": "array", "items": _ITEM_SCHEMA},
                "cta_label": {"type": "string"},
                "cta_url": {"type": "string"},
                "pause_note": {"type": "string", "description": "Day 3 only"},
            },
            "required": ["subject", "title", "intro", "cta_label", "cta_url"],
        },
    },
    "required": ["slack", "email"],
}


def brief_deep_link() -> str:
    """The one CTA target — the brief page in the app (mirrors brief delivery)."""
    return f"{(settings.frontend_url or 'https://app.sprntly.ai').rstrip('/')}/brief"


def _nudge_input(brief: dict, day: int, deep_link: str) -> str:
    """Render the brief's real figures into the skill input. Only data that's
    actually in the brief is passed — the skill is told never to invent."""
    insights = (brief.get("insights") or [])[:MAX_ITEMS]
    lines = []
    for i, ins in enumerate(insights):
        metrics = ", ".join(
            f"{m.get('label')}: {m.get('value')}" for m in (ins.get("metrics") or [])
        )
        impact = "; ".join(ins.get("impact_math") or [])
        bits = [f"{i + 1}. [{ins.get('tag', '')}] {ins.get('title', '')}"]
        if ins.get("subtitle"):
            bits.append(f"— {ins['subtitle']}")
        if metrics:
            bits.append(f"(metrics: {metrics})")
        if impact:
            bits.append(f"(impact: {impact})")
        lines.append(" ".join(bits))
    return "\n".join(
        [
            f"DAY: {day} — {_DAY_GUIDE.get(day, '')}",
            f"DEEP LINK (the single CTA target): {deep_link}",
            f"WEEK: {brief.get('week_label', '')}",
            f"BRIEF HEADLINE: {brief.get('summary_headline', '')}",
            "ROLLUP / GREETING (the source of the total-upside figure — quote its "
            f"numbers, don't invent): {brief.get('greeting', '')}",
            "TOP ITEMS (use these real figures only):",
            *(lines or ["(no ranked items — keep it qualitative, no figures)"]),
        ]
    )


def generate_nudge(enterprise_id: str, brief: dict, day: int, deep_link: str) -> dict:
    """Compose the Day-`day` Slack + email copy by binding the brief-nudge skill.
    Returns the validated NUDGE_SCHEMA dict. Raises on LLM failure (callers
    isolate)."""
    result = llm_call(
        enterprise_id=enterprise_id,
        agent="brief_nudge",
        purpose=f"compose_nudge_day_{day}",
        prompt_version="brief-nudge-v1",
        system=_NUDGE_SYSTEM,
        input=_nudge_input(brief, day, deep_link),
        json_schema=NUDGE_SCHEMA,
        skill="brief-nudge",
        max_tokens=1500,
    )
    out = result.output
    if not isinstance(out, dict):
        raise ValueError("brief-nudge composer returned non-dict output")
    return out


def nudge_slack_blocks(nudge: dict, deep_link: str) -> tuple[str, list[dict]]:
    """(plain-text fallback, Slack Block Kit blocks) for a generated nudge.
    Compact: bold headline, intro, the items, one CTA button."""
    slack = nudge.get("slack") or {}
    headline = slack.get("headline") or "Your weekly brief is ready"
    intro = slack.get("intro") or ""
    items = slack.get("items") or []
    cta_label = slack.get("cta_label") or "Open this week's brief"
    cta_url = slack.get("cta_url") or deep_link

    item_lines = []
    for it in items:
        impact = f" — *{it['impact']}*" if it.get("impact") else ""
        item_lines.append(f"• *{it.get('label', '')}*{impact} — {it.get('detail', '')}")

    body_parts = [p for p in (intro, "\n".join(item_lines)) if p]
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{headline}*"[:3000]}},
    ]
    if body_parts:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(body_parts)[:3000]}}
        )
    if slack.get("pause_note"):
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": slack["pause_note"]}]}
        )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": cta_label[:75]},
                    "url": cta_url,
                    "style": "primary",
                }
            ],
        }
    )
    return headline, blocks


def _eligible(row: dict, enterprise_id: str, brief_id: int, day: int) -> bool:
    """Cheap pre-checks (no token decode, no LLM): active connection, a target
    (channel picked, or a DM-to-self), not already sent, and — for reminders —
    the brief is still unopened."""
    if row.get("status") != "active":
        return False
    config = row.get("config") or {}
    target_type = (config.get("target_type") or slack_oauth.TARGET_CHANNEL).strip()
    # DM target resolves to the installing user's own DM at send time, so it
    # needs no channel here; a channel target requires a picked channel.
    if target_type == slack_oauth.TARGET_CHANNEL and not config.get("channel_id"):
        return False
    user_id = row.get("user_id")
    if not user_id:
        return False
    if nudge_db.has_nudge_been_sent(enterprise_id, user_id, brief_id, day, SLACK):
        return False
    if day >= 1 and not nudge_db.is_brief_unopened(enterprise_id, user_id, brief_id):
        return False
    return True


def _deliver_to_one(
    row: dict, enterprise_id: str, brief_id: int, day: int, text: str, blocks: list[dict]
) -> dict:
    """Send the rendered nudge to one per-user Slack connection + record it.
    Never raises."""
    user_id = row.get("user_id")
    config = row.get("config") or {}
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, json.JSONDecodeError, KeyError) as e:
        logger.error("nudge slack token unreadable for user %s: %s", user_id, e)
        return {"user_id": user_id, "delivered": False, "reason": "token_unreadable"}
    bot_token = token_json.get("access_token") or ""
    if not bot_token:
        return {"user_id": user_id, "delivered": False, "reason": "no_bot_token"}
    try:
        # Same target routing as brief delivery — DM the user or post to their
        # channel — so the skill-drafted nudge honors the user's chosen target.
        res = slack_oauth.post_to_target(
            bot_token, config=config,
            authed_user_id=token_json.get("authed_user_id"),
            text=text, blocks=blocks)
    except Exception as e:  # noqa: BLE001 — one recipient never breaks the rest
        logger.exception("nudge slack delivery failed for user %s", user_id)
        return {"user_id": user_id, "delivered": False, "reason": f"error: {e}"}
    nudge_db.record_nudge_sent(enterprise_id, user_id, brief_id, day, SLACK)
    return {"user_id": user_id, "delivered": True,
            "channel": res.get("channel") or config.get("channel_id")}


def deliver_brief_nudge_to_slack(
    enterprise_id: str, brief: dict, *, day: int, brief_id: int
) -> dict:
    """Generate the Day-`day` nudge and fan it out to every eligible member's
    own Slack. Best-effort; never raises. No-op (and no LLM call) when the
    feature is off or nobody is eligible."""
    if not settings.brief_nudge_enabled:
        return {"delivered": False, "reason": "brief_nudge_disabled", "recipients": []}
    try:
        rows = db.list_slack_connections(enterprise_id)
        if not rows:
            return {"delivered": False, "reason": "slack_not_connected", "recipients": []}
        eligible = [r for r in rows if _eligible(r, enterprise_id, brief_id, day)]
        if not eligible:
            return {"delivered": False, "reason": "no_eligible_recipients", "recipients": []}
        deep_link = brief_deep_link()
        try:
            nudge = generate_nudge(enterprise_id, brief, day, deep_link)
        except Exception as e:  # noqa: BLE001 — generation failure must not raise
            logger.exception("nudge generation failed for %s day %s", enterprise_id, day)
            return {"delivered": False, "reason": f"generation_error: {e}", "recipients": []}
        text, blocks = nudge_slack_blocks(nudge, deep_link)
        recipients = [
            _deliver_to_one(r, enterprise_id, brief_id, day, text, blocks) for r in eligible
        ]
        any_delivered = any(r.get("delivered") for r in recipients)
        out: dict = {"delivered": any_delivered, "recipients": recipients, "day": day}
        if not any_delivered:
            out["reason"] = recipients[0].get("reason", "not_delivered")
        return out
    except Exception as e:  # noqa: BLE001 — delivery never breaks generation
        logger.exception("brief nudge delivery failed for %s", enterprise_id)
        return {"delivered": False, "reason": f"error: {e}", "recipients": []}


def _days_since(generated_at: str, now: datetime | None = None) -> int | None:
    """Whole days between a brief's `generated_at` (ISO/timestamptz) and now."""
    if not generated_at:
        return None
    try:
        ts = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return (ref - ts).days


def run_nudge_cycle(now: datetime | None = None) -> dict:
    """Scheduler job: send the due Day 1/2/3 reminder for each company's current
    brief while it's unopened. Day 0 is sent inline at generation time, not here.
    Error-isolated per company. No-op when the feature is off."""
    if not settings.brief_nudge_enabled:
        return {"enabled": False}
    from app.db.briefs import get_current_brief
    from app.db.companies import list_companies

    checked = 0
    delivered = 0
    try:
        companies = list_companies() or []
    except Exception:  # noqa: BLE001
        logger.exception("brief nudge cycle: list_companies failed")
        return {"enabled": True, "error": "list_companies_failed"}

    for co in companies:
        slug = co.get("slug")
        company_id = co.get("id")
        if not slug or not company_id:
            continue
        try:
            brief = get_current_brief(slug)
            if not brief:
                continue
            brief_id = brief.get("id")
            day = _days_since(brief.get("generated_at"), now)
            if brief_id is None or day not in (1, 2, 3):
                continue
            checked += 1
            res = deliver_brief_nudge_to_slack(company_id, brief, day=day, brief_id=brief_id)
            if res.get("delivered"):
                delivered += 1
        except Exception:  # noqa: BLE001 — one company never breaks the cycle
            logger.exception("brief nudge cycle failed for company %s", company_id)

    return {"enabled": True, "companies_checked": checked, "delivered": delivered}

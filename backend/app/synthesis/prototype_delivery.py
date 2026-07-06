"""Prototype-ready delivery — notify a company when a background prototype
generation finishes, over the SAME channels the weekly brief uses.

Generation runs in the background (routes/design_agent.py `_run_generation_bg`),
so the user is not kept on a loading screen. When the prototype reaches `ready`
we reach the user exactly like the weekly brief does:

  * Slack — PER-USER: every member who connected their own Slack gets a DM /
    channel post in their OWN workspace at their chosen target. Reuses the
    brief's `_deliver_to_one` fan-out verbatim.
  * Email — company-wide, gated on `notification_settings.email_enabled`
    (default OFF), to the same recipient set the brief resolves. Reuses the
    brief's `_send_via_resend` + `_resolve_recipients`.

Unlike the brief, the message is a fixed, deterministic notification (no LLM
skill draft) — "your prototype is ready" + one CTA to the in-app canvas.

Delivery is a SIDE EFFECT of generation completing: it must NEVER raise or
block the generation. Every failure is logged + returned, never raised. A user
with no Slack connected / email disabled is a clean no-op.
"""
from __future__ import annotations

import html
import logging

from app import db
from app.config import settings
from app.db import companies as companies_db
from app.synthesis.delivery import _deliver_to_one
from app.synthesis.email_delivery import _resolve_recipients, _send_via_resend

logger = logging.getLogger(__name__)


def _prototype_url(prd_id: int) -> str:
    """The one CTA target — the in-app prototype canvas for this PRD (mirrors
    the brief's `/brief` deep link). Requires the user to be signed in; this is
    an internal authoring surface, not a public share link."""
    base = (settings.frontend_url or "https://app.sprntly.ai").rstrip("/")
    return f"{base}/prototype?prd={prd_id}"


def _prototype_slack_blocks(prd_title: str, url: str) -> tuple[str, list[dict]]:
    """(plain-text fallback, Slack Block Kit blocks) for the ready notification.
    Deterministic — a bold headline, a one-line body naming the PRD, and one
    "View prototype" CTA button. No LLM."""
    headline = "Your prototype is ready"
    title = prd_title.strip() or "your PRD"
    body = f"The interactive prototype for *{title}* has finished generating."
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{headline}*"[:3000]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": body[:3000]}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View prototype"},
                    "url": url,
                    "style": "primary",
                }
            ],
        },
    ]
    return headline, blocks


def _render_prototype_email(prd_title: str, url: str) -> tuple[str, str, str]:
    """(subject, html_body, text_body) for the ready email. Plain, self-contained
    HTML in the brief email's visual key — no external assets required to read
    it."""
    title = prd_title.strip() or "your PRD"
    esc_title = html.escape(title)
    esc_url = html.escape(url, quote=True)
    subject = f"Your prototype is ready — {title}"
    text_body = (
        "Your prototype is ready.\n\n"
        f"The interactive prototype for \"{title}\" has finished generating.\n\n"
        f"View it here: {url}\n"
    )
    html_body = (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
        'Roboto,Helvetica,Arial,sans-serif;max-width:520px;margin:0 auto;'
        'padding:32px 24px;color:#15171c;">'
        '<h1 style="font-size:20px;margin:0 0 12px;">Your prototype is ready</h1>'
        f'<p style="font-size:15px;line-height:1.5;color:#41444f;margin:0 0 24px;">'
        f'The interactive prototype for <strong>{esc_title}</strong> has finished '
        'generating.</p>'
        f'<a href="{esc_url}" style="display:inline-block;background:#1a8a52;'
        'color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;'
        'padding:11px 20px;border-radius:8px;">View prototype</a>'
        '</div>'
    )
    return subject, html_body, text_body


def _deliver_prototype_to_slack(company_id: str, prd_title: str, url: str) -> dict:
    """Best-effort PER-USER Slack delivery of the ready notification. Fans the
    fixed message out to every member who connected their own Slack + picked a
    target (reuses the brief's `_deliver_to_one`). Returns an aggregate
    {delivered, recipients, reason?}. Never raises."""
    try:
        rows = db.list_slack_connections(company_id)
        if not rows:
            return {"delivered": False, "reason": "slack_not_connected",
                    "recipients": []}
        text, blocks = _prototype_slack_blocks(prd_title, url)
        recipients = [_deliver_to_one(row, text, blocks) for row in rows]
        any_delivered = any(r.get("delivered") for r in recipients)
        out: dict = {"delivered": any_delivered, "recipients": recipients}
        if not any_delivered:
            out["reason"] = recipients[0].get("reason", "not_delivered")
        return out
    except Exception as e:  # noqa: BLE001 — delivery never breaks generation
        logger.exception("prototype slack delivery failed for %s", company_id)
        return {"delivered": False, "reason": f"error: {e}", "recipients": []}


def _deliver_prototype_to_email(company_id: str, prd_title: str, url: str) -> dict:
    """Best-effort email delivery of the ready notification. Honors
    `notification_settings.email_enabled` (default OFF) — the SAME gate the
    weekly brief uses — and the same recipient resolution. Per-recipient failure
    isolation. Never raises."""
    try:
        api_key = settings.resend_api_key
        if not api_key:
            return {"delivered": False, "reason": "resend_not_configured",
                    "recipients": []}

        notif = companies_db.get_notification_settings(company_id)
        if not notif.get("email_enabled"):
            return {"delivered": False, "reason": "email_disabled",
                    "recipients": []}

        recipients = _resolve_recipients(company_id, notif)
        if not recipients:
            return {"delivered": False, "reason": "no_recipients",
                    "recipients": []}

        subject, html_body, text_body = _render_prototype_email(prd_title, url)

        results: list[dict] = []
        for addr in recipients:
            try:
                _send_via_resend(api_key, to=addr, subject=subject,
                                 html_body=html_body, text_body=text_body)
                results.append({"email": addr, "delivered": True})
            except Exception as e:  # noqa: BLE001 — one address never breaks the rest
                logger.exception("prototype email delivery failed for %s", addr)
                results.append({"email": addr, "delivered": False,
                                "reason": f"error: {e}"})

        any_delivered = any(r["delivered"] for r in results)
        out: dict = {"delivered": any_delivered, "recipients": results}
        if not any_delivered:
            out["reason"] = results[0].get("reason", "not_delivered")
        return out
    except Exception as e:  # noqa: BLE001 — delivery never breaks generation
        logger.exception("prototype email delivery failed for %s", company_id)
        return {"delivered": False, "reason": f"error: {e}", "recipients": []}


def deliver_prototype_ready(company_id: str, *, prd_id: int, prd_title: str) -> dict:
    """Notify a company that a background prototype generation finished, over
    the SAME channels as the weekly brief — per-user Slack + company email — each
    self-gated exactly as the brief is. Best-effort; never raises.

    Called from `_run_generation_bg` once the prototype row reaches `ready`. The
    CTA points at the in-app canvas (`/prototype?prd=<id>`)."""
    url = _prototype_url(prd_id)

    slack = _deliver_prototype_to_slack(company_id, prd_title, url)
    if not slack.get("delivered") and slack.get("reason") not in (
        "slack_not_connected", "no_channel_configured"
    ):
        logger.warning("prototype slack delivery: %s", slack)

    email = _deliver_prototype_to_email(company_id, prd_title, url)
    if not email.get("delivered") and email.get("reason") not in (
        "email_disabled", "no_recipients", "resend_not_configured"
    ):
        logger.warning("prototype email delivery: %s", email)

    return {"slack": slack, "email": email}

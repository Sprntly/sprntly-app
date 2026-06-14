"""Brief delivery — email a freshly generated brief to a company's recipients.

The email path mirrors the Slack one (app/synthesis/delivery.py) but routes
through Resend's transactional-email API (https://api.resend.com/emails) FROM
the verified `mail.sprntly.ai` sender. It is the second delivery channel for
the Weekly Brief (v0 checklist 2.4); Slack remains independent.

Per-company config lives in `companies.notification_settings` (JSONB):
    {
      "email_enabled": true,                 # master toggle (default OFF)
      "email_recipients": ["a@co.com", ...]  # optional explicit list
    }
When `email_enabled` is falsy ⇒ clean no-op. When `email_recipients` is absent
⇒ default to the company members' emails (app.db.team.list_company_members).

Delivery is a SIDE EFFECT of brief generation: it must NEVER raise or block the
brief itself. Each recipient is sent independently — one failed send never
stops the others (per-recipient failure isolation). A missing RESEND_API_KEY,
a disabled toggle, or zero resolvable recipients ⇒ clean no-op with a reason.
"""
from __future__ import annotations

import html
import logging

import httpx

from app.config import settings
from app.db import companies as companies_db
from app.db import team as team_db

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
MAX_INSIGHTS_IN_EMAIL = 5
_SEND_TIMEOUT_SECONDS = 10.0

# Plain-text tag labels (no Slack emoji shortcodes — this is real email).
_TAG_LABEL = {
    "something_broken": "FIX",
    "something_new": "BUILD",
    "something_better": "OPTIMIZE",
    # Forward-compat aliases for the checklist's FIX/BUILD/RESEARCH/WATCH set.
    "research": "RESEARCH",
    "watch": "WATCH",
}


def _app_brief_url() -> str:
    base = (settings.frontend_url or "https://app.sprntly.ai").rstrip("/")
    return f"{base}/brief"


def _tag_label(tag: str) -> str:
    return _TAG_LABEL.get(tag, (tag or "").replace("_", " ").upper() or "INSIGHT")


def render_brief_email(brief: dict) -> tuple[str, str, str]:
    """Render (subject, html_body, text_body) for a brief payload.

    Mirrors the Slack content: headline, the week label, the ranked insights
    each prefixed with its FIX/BUILD/OPTIMIZE/RESEARCH/WATCH tag, and a link to
    the brief in the app. Pure + deterministic — unit-tested directly.
    """
    headline = brief.get("summary_headline") or "Your weekly brief is ready"
    week = brief.get("week_label", "")
    insights = (brief.get("insights") or [])[:MAX_INSIGHTS_IN_EMAIL]
    url = _app_brief_url()

    subject = f"Weekly Brief — {week}: {headline}" if week else f"Weekly Brief: {headline}"

    # ── plain-text body ──────────────────────────────────────────────────────
    text_lines = [f"Weekly Brief — {week}".rstrip(" —"), "", headline, ""]
    if insights:
        for i, ins in enumerate(insights):
            tag = _tag_label(ins.get("tag", ""))
            text_lines.append(f"{i + 1}. [{tag}] {ins.get('title', '')}")
            subtitle = (ins.get("subtitle") or "").strip()
            if subtitle:
                text_lines.append(f"   {subtitle}")
    else:
        text_lines.append("No insights this week.")
    text_lines += ["", f"Open in Sprntly: {url}"]
    text_body = "\n".join(text_lines)

    # ── HTML body ────────────────────────────────────────────────────────────
    def esc(s: str) -> str:
        return html.escape(s or "")

    items_html = []
    for ins in insights:
        tag = esc(_tag_label(ins.get("tag", "")))
        title = esc(ins.get("title", ""))
        subtitle = esc((ins.get("subtitle") or "").strip())
        sub_html = (
            f'<div style="color:#555;font-size:14px;margin-top:2px;">{subtitle}</div>'
            if subtitle else ""
        )
        items_html.append(
            '<li style="margin-bottom:14px;">'
            f'<span style="display:inline-block;font-size:11px;font-weight:700;'
            f'letter-spacing:.5px;color:#fff;background:#111;border-radius:4px;'
            f'padding:2px 7px;margin-right:8px;">{tag}</span>'
            f'<span style="font-weight:600;">{title}</span>{sub_html}</li>'
        )
    if not items_html:
        items_html.append(
            '<li style="color:#888;">No insights this week.</li>'
        )

    html_body = (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,'
        'sans-serif;max-width:560px;margin:0 auto;color:#111;">'
        f'<p style="font-size:13px;color:#888;text-transform:uppercase;'
        f'letter-spacing:1px;margin:0 0 4px;">Weekly Brief{" — " + esc(week) if week else ""}</p>'
        f'<h1 style="font-size:22px;line-height:1.3;margin:0 0 20px;">{esc(headline)}</h1>'
        f'<ul style="list-style:none;padding:0;margin:0 0 24px;">{"".join(items_html)}</ul>'
        f'<a href="{esc(_app_brief_url())}" '
        'style="display:inline-block;background:#111;color:#fff;text-decoration:none;'
        'font-weight:600;padding:10px 18px;border-radius:6px;">Open in Sprntly</a>'
        '<p style="font-size:12px;color:#aaa;margin-top:28px;">'
        'You are receiving this because brief notifications are enabled for your '
        'company in Sprntly.</p>'
        '</div>'
    )
    return subject, html_body, text_body


def _send_via_resend(
    api_key: str, *, to: str, subject: str, html_body: str, text_body: str
) -> None:
    """POST one email to Resend. Raises on transport / non-2xx so the caller
    can record a per-recipient failure. Isolated so tests can patch it."""
    resp = httpx.post(
        RESEND_API_URL,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={
            "from": settings.brief_email_from,
            "to": [to],
            "subject": subject,
            "html": html_body,
            "text": text_body,
        },
        timeout=_SEND_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()


def _resolve_recipients(company_id: str, notif: dict) -> list[str]:
    """Resolve the recipient email list. Explicit `email_recipients` wins;
    otherwise fall back to the company members' emails. De-duplicated, order
    preserved, empties dropped."""
    explicit = notif.get("email_recipients")
    if isinstance(explicit, list) and explicit:
        raw = explicit
    else:
        members = team_db.list_company_members(company_id)
        raw = [m.get("email") for m in members]

    seen: set[str] = set()
    out: list[str] = []
    for addr in raw:
        if not isinstance(addr, str):
            continue
        addr = addr.strip()
        if not addr or addr.lower() in seen:
            continue
        seen.add(addr.lower())
        out.append(addr)
    return out


def deliver_brief_to_email(company_id: str, brief: dict) -> dict:
    """Best-effort email delivery of a brief to a company's recipients.

    Honors `notification_settings.email_enabled` (default OFF). Sends one email
    per recipient with per-recipient failure isolation — one bad address never
    stops the rest. Returns an aggregate
        {delivered: bool, recipients: [{email, delivered, reason?}], reason?}
    `delivered` is True iff at least one recipient was sent. Never raises."""
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

        subject, html_body, text_body = render_brief_email(brief)

        results: list[dict] = []
        for addr in recipients:
            try:
                _send_via_resend(api_key, to=addr, subject=subject,
                                 html_body=html_body, text_body=text_body)
                results.append({"email": addr, "delivered": True})
            except Exception as e:  # noqa: BLE001 — one address never breaks the rest
                logger.exception("brief email delivery failed for %s", addr)
                results.append({"email": addr, "delivered": False,
                                "reason": f"error: {e}"})

        any_delivered = any(r["delivered"] for r in results)
        out: dict = {"delivered": any_delivered, "recipients": results}
        if not any_delivered:
            out["reason"] = results[0].get("reason", "not_delivered")
        return out
    except Exception as e:  # noqa: BLE001 — delivery never breaks generation
        logger.exception("brief email delivery failed for %s", company_id)
        return {"delivered": False, "reason": f"error: {e}", "recipients": []}

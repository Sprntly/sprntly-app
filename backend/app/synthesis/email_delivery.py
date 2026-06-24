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
from app.synthesis.weekly_brief_skill import accent_for_skill_type

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

# Default green used for the primary CTA / brand accent when a card carries no
# type accent. Mirrors the skill template's --green token.
_GREEN = "#1a8a52"
_GREEN_DARK = "#157045"
_GREEN_SOFT = "#e6f3ec"
_INK = "#15171c"
_INK_2 = "#41444f"
_INK_SOFT = "#80838d"
_LINE = "#e9e8e4"
_PAPER = "#f6f5f1"
_CHIP = "#f4f3ef"
_CHIP_LINE = "#e7e6e1"

_SANS = "'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
_SERIF = "'Spectral',Georgia,'Times New Roman',serif"
_FONTS_HREF = (
    "https://fonts.googleapis.com/css2?"
    "family=Spectral:wght@400;500;600;700&family=Inter:wght@400;500;600;700&display=swap"
)


def _app_brief_url() -> str:
    base = (settings.frontend_url or "https://app.sprntly.ai").rstrip("/")
    return f"{base}/brief"


def _tag_label(tag: str) -> str:
    return _TAG_LABEL.get(tag, (tag or "").replace("_", " ").upper() or "INSIGHT")


def _accent_soft(accent: str) -> str:
    """A faint tint of the accent for pill backgrounds. Falls back to a neutral
    chip when the accent is not a parseable #rrggbb hex."""
    h = (accent or "").strip().lstrip("#")
    if len(h) != 6:
        return _CHIP
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return _CHIP
    # Blend ~12% accent over white for a soft tinted background.
    mix = lambda c: round(c * 0.12 + 255 * 0.88)  # noqa: E731
    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"


def _type_label(skill_type: str) -> str:
    """Capitalized type name for the pill (Reliability, Competitive, …)."""
    t = (skill_type or "").strip()
    return t[:1].upper() + t[1:] if t else "Insight"


def render_brief_email(brief: dict) -> tuple[str, str, str]:
    """Render (subject, html_body, text_body) for a brief payload.

    Rebuilt to mirror the weekly-brief skill's card design: a PM-coworker
    header, a greeting, then one card per insight with a type pill (in the
    type's accent), a serif headline, body, source chips, and two CTA buttons.
    The accent for every card is DERIVED FROM THE TYPE via
    `accent_for_skill_type` — never trusted from `_card.accent`, which the model
    can mismatch. Insights without a `_card` degrade to the legacy
    tag/title/subtitle rendering so old briefs still render. Pure +
    deterministic — unit-tested directly.
    """
    greeting = (brief.get("greeting") or "").strip()
    headline = brief.get("summary_headline") or "Your weekly brief is ready"
    intro = greeting or headline
    week = brief.get("week_label", "")
    insights = (brief.get("insights") or [])[:MAX_INSIGHTS_IN_EMAIL]
    url = _app_brief_url()

    subject = f"Weekly Brief — {week}: {headline}" if week else f"Weekly Brief: {headline}"

    # ── plain-text body ──────────────────────────────────────────────────────
    text_lines = [f"Weekly Brief — {week}".rstrip(" —"), "", intro, ""]
    if insights:
        for i, ins in enumerate(insights):
            card = ins.get("_card") if isinstance(ins.get("_card"), dict) else None
            if card:
                label = _type_label(card.get("type") or "")
                title = (card.get("title") or ins.get("title") or "").strip()
                sources = [str(s).strip() for s in (card.get("sources") or []) if str(s).strip()]
            else:
                label = _tag_label(ins.get("tag", ""))
                title = (ins.get("title") or "").strip()
                sources = []
            text_lines.append(f"{i + 1}. [{label}] {title}")
            subtitle = ""
            if card:
                subtitle = (card.get("body") or "").strip()
            if not subtitle:
                subtitle = (ins.get("subtitle") or "").strip()
            if subtitle:
                text_lines.append(f"   {subtitle}")
            if sources:
                text_lines.append(f"   From: {', '.join(sources)}")
    else:
        text_lines.append("No insights this week.")
    text_lines += ["", f"Open in Sprntly: {url}"]
    text_body = "\n".join(text_lines)

    # ── HTML body ────────────────────────────────────────────────────────────
    def esc(s: str) -> str:
        return html.escape(s if isinstance(s, str) else (str(s) if s is not None else ""))

    cards_html = [_render_card_html(ins, esc) for ins in insights]
    if not cards_html:
        cards_html.append(
            f'<div style="color:{_INK_SOFT};font-size:15px;'
            f'font-family:{_SANS};padding:8px 0;">No insights this week.</div>'
        )

    meta = f' &middot; {esc(week)}' if week else ""
    open_btn = (
        f'<a href="{esc(url)}" style="display:inline-block;background:{_GREEN};'
        f'color:#ffffff;text-decoration:none;font-family:{_SANS};font-weight:600;'
        f'font-size:14px;padding:11px 20px;border-radius:9px;">Open in Sprntly</a>'
    )

    html_body = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<link href="{_FONTS_HREF}" rel="stylesheet">'
        f'<style>body{{margin:0;padding:0;background:{_PAPER};}}</style></head>'
        f'<body style="margin:0;padding:0;background:{_PAPER};">'
        f'<div style="background:{_PAPER};padding:32px 16px;font-family:{_SANS};'
        f'color:{_INK};-webkit-font-smoothing:antialiased;">'
        '<div style="max-width:600px;margin:0 auto;">'
        # ── header: PM coworker + meta ───────────────────────────────────────
        '<div style="margin-bottom:14px;">'
        f'<span style="display:inline-block;font-family:{_SANS};font-size:10.5px;'
        f'font-weight:600;letter-spacing:.05em;color:{_GREEN_DARK};'
        f'background:{_GREEN_SOFT};padding:4px 10px;border-radius:999px;">'
        '&#10022; PM COWORKER</span>'
        f'<span style="font-family:{_SANS};font-size:13px;color:{_INK_SOFT};'
        f'margin-left:10px;">Weekly brief{meta}</span>'
        '</div>'
        # ── greeting ─────────────────────────────────────────────────────────
        f'<div style="font-family:{_SANS};font-size:16px;line-height:1.6;'
        f'color:{_INK_2};margin-bottom:26px;">{esc(intro)}</div>'
        # ── cards ────────────────────────────────────────────────────────────
        f'{"".join(cards_html)}'
        # ── footer CTA + note ────────────────────────────────────────────────
        f'<div style="margin-top:24px;">{open_btn}</div>'
        f'<div style="font-family:{_SANS};font-size:12px;color:{_INK_SOFT};'
        f'margin-top:26px;line-height:1.5;">You are receiving this because brief '
        'notifications are enabled for your company in Sprntly.</div>'
        '</div></div></body></html>'
    )
    return subject, html_body, text_body


def _render_card_html(ins: dict, esc) -> str:
    """Render one insight as a skill-style card. Falls back to the legacy
    tag/title/subtitle layout when the insight carries no `_card`. Never
    raises on missing fields."""
    card = ins.get("_card") if isinstance(ins.get("_card"), dict) else None

    if card:
        skill_type = (card.get("type") or "").strip()
        # DERIVE the accent from the type — never trust card["accent"], which the
        # model can mismatch. Unknown types fall back to the brand green.
        accent = accent_for_skill_type(skill_type) or _GREEN
        label = _type_label(skill_type)
        title = (card.get("title") or ins.get("title") or "").strip()
        body = (card.get("body") or "").strip()
        sources = [str(s).strip() for s in (card.get("sources") or []) if str(s).strip()]
        ctas = [c for c in (card.get("ctas") or []) if isinstance(c, dict)]
    else:
        # Legacy fallback: derive a pseudo-card from tag/title/subtitle.
        accent = _GREEN
        label = _tag_label(ins.get("tag", ""))
        title = (ins.get("title") or "").strip()
        body = (ins.get("subtitle") or "").strip()
        sources = []
        ctas = []

    if not ctas:
        ctas = [
            {"label": "View PRD", "style": "primary"},
            {"label": "View prototype", "style": "ghost"},
        ]

    soft = _accent_soft(accent)
    url = _app_brief_url()

    # type pill
    pill = (
        f'<span style="display:inline-block;font-family:{_SANS};font-size:11px;'
        f'font-weight:600;letter-spacing:.07em;text-transform:uppercase;'
        f'color:{accent};background:{soft};padding:5px 11px;border-radius:999px;">'
        f'{esc(label)}</span>'
    )

    headline = (
        f'<div style="font-family:{_SERIF};font-weight:600;font-size:22px;'
        f'line-height:1.25;color:{_INK};margin:12px 0;">{esc(title)}</div>'
    )

    body_html = (
        f'<div style="font-family:{_SANS};font-size:15px;line-height:1.6;'
        f'color:{_INK_2};">{esc(body)}</div>' if body else ""
    )

    # source chips ("From" row)
    if sources:
        chips = (
            f'<span style="font-family:{_SANS};font-size:10.5px;'
            f'letter-spacing:.04em;text-transform:uppercase;color:{_INK_SOFT};'
            f'margin-right:6px;">From</span>'
        )
        for s in sources:
            chips += (
                f'<span style="display:inline-block;font-family:{_SANS};'
                f'font-size:10.5px;color:{_INK_2};background:{_CHIP};'
                f'border:1px solid {_CHIP_LINE};padding:3px 8px;border-radius:6px;'
                f'margin-right:6px;">{esc(s)}</span>'
            )
        sources_html = f'<div style="margin-top:14px;">{chips}</div>'
    else:
        sources_html = ""

    # CTA buttons (first primary-filled, rest ghost-outlined)
    btns = ""
    for i, c in enumerate(ctas[:2]):
        clabel = (c.get("label") or "").strip() or ("View PRD" if i == 0 else "View prototype")
        style = (c.get("style") or "").strip().lower()
        is_primary = style == "primary" or (not style and i == 0)
        if is_primary:
            btns += (
                f'<a href="{esc(url)}" style="display:inline-block;'
                f'font-family:{_SANS};font-size:14px;font-weight:600;'
                f'text-decoration:none;background:{accent};color:#ffffff;'
                f'padding:10px 16px;border-radius:9px;margin-right:10px;">'
                f'{esc(clabel)}</a>'
            )
        else:
            btns += (
                f'<a href="{esc(url)}" style="display:inline-block;'
                f'font-family:{_SANS};font-size:14px;font-weight:600;'
                f'text-decoration:none;background:#ffffff;color:{accent};'
                f'border:1px solid {_LINE};padding:9px 15px;border-radius:9px;'
                f'margin-right:10px;">{esc(clabel)}</a>'
            )
    actions_html = f'<div style="margin-top:18px;">{btns}</div>' if btns else ""

    # Card shell: white card with a left accent bar (border-left).
    return (
        f'<div style="background:#ffffff;border:1px solid {_LINE};'
        f'border-left:3px solid {accent};border-radius:14px;'
        f'padding:22px 26px;margin-bottom:16px;">'
        f'{pill}{headline}{body_html}{sources_html}{actions_html}'
        '</div>'
    )


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

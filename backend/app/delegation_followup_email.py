"""One-way transactional email nudge for the autonomous task follow-up
sweep — the escalation-to-assignee channel (spec §2 channel step 2, only
reached after >=2 unanswered DM cycles).

Copies `invite_reminders.send_reminder_email`'s transport shape verbatim
(`httpx.post(RESEND_API_URL, ...)`, `resend_api_key` from settings,
`_from_address()` falling back to `brief_email_from`, best-effort —
returns `bool`, never raises, logs `skipped` when no key). There is no
single shared Resend-send primitive in this codebase today (each drip
module — `invite_reminders.py`, `drip_email.py` — carries its own
`httpx.post`); matching that existing pattern is the DRY-consistent
choice rather than introducing a third shape.

One-way: no inbound parsing, no reply-to expectation. The CTA deep-links
to the project's private (individual) chat — the real, stable route
(`web/app/lib/routes.ts::projectPath(id, {chat:"individual"})` renders
`/projects?id=<id>&chat=individual`), not a placeholder.

NAME/identifier only in the body — never the task_summary text
(observability/PII rule; the task content lives in-app, never in an
email a third-party transport handles)."""
from __future__ import annotations

import html as html_mod
import logging

import httpx

from app import config as config_mod

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
_HTTP_TIMEOUT_SECONDS = 10.0

_DEFAULT_FIRST = "there"

_SERIF = "'Spectral',Georgia,'Times New Roman',serif"
_SANS = "'Inter',-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _from_address() -> str:
    """From: header. Overridable via INVITE_FROM_EMAIL (the shared drip
    sender override); falls back to brief_email_from — the same verified
    `mail.sprntly.ai` sender every other drip module uses."""
    return (
        getattr(config_mod.settings, "invite_from_email", "")
        or getattr(config_mod.settings, "brief_email_from", "")
        or "Sprntly <briefs@mail.sprntly.ai>"
    )


def project_chat_link(project_id: int) -> str:
    """The CTA deep-link: the project's private individual chat. Mirrors
    `projectPath(id, {chat: "individual"})` (`web/app/lib/routes.ts`) —
    `?id=<id>&chat=individual`, resolved against `frontend_url`."""
    base = (config_mod.settings.frontend_url or "").rstrip("/") or (
        "http://localhost:3000"
    )
    return f"{base}/projects?id={project_id}&chat=individual"


def render_followup_email(*, first_name: str, project_id: int) -> tuple[str, str, str]:
    """Fill the nudge template. Returns (subject, body_text, body_html).
    NAME only — never task_summary or any chat content."""
    name = (first_name or "").strip() or _DEFAULT_FIRST
    link = project_chat_link(project_id)
    subject = "You have an update waiting in Sprntly"
    body_text = (
        f"Hi {name},\n\n"
        "You have an update waiting for you in Sprntly.\n\n"
        f"Open it here: {link}\n\n"
        "Best,\nThe Sprntly Team"
    )
    body_html = _render_html(name=name, link=link)
    return subject, body_text, body_html


def _render_html(*, name: str, link: str) -> str:
    name_esc = html_mod.escape(name)
    link_esc = html_mod.escape(link, quote=True)
    return f"""\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f6f5f1;margin:0;padding:0">
  <tr>
    <td align="center" style="padding:44px 16px 36px">
      <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;max-width:520px">
        <tr>
          <td align="center" style="padding:0 0 20px;font-family:{_SERIF};font-size:25px;font-weight:600;color:#15171c;letter-spacing:-0.02em">
            Sprntly<span style="color:#1a8a52">.</span>
          </td>
        </tr>
        <tr>
          <td style="background-color:#ffffff;border:1px solid #e9e8e4;border-radius:14px;padding:40px 40px 34px">
            <h1 style="margin:0 0 18px;font-family:{_SERIF};font-size:23px;line-height:1.3;font-weight:600;color:#15171c">You have an update waiting</h1>
            <p style="margin:0 0 16px;font-family:{_SANS};font-size:15px;line-height:1.65;color:#41444f">Hi {name_esc}, you have an update waiting for you in Sprntly.</p>
            <table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:24px">
              <tr>
                <td align="center" style="border-radius:10px;background-color:#1a8a52">
                  <a href="{link_esc}" style="display:inline-block;padding:13px 28px;font-family:{_SANS};font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:10px">Open Sprntly</a>
                </td>
              </tr>
            </table>
            <p style="margin:24px 0 0;font-family:{_SANS};font-size:13px;line-height:1.6;color:#80838d">
              Or paste this link into your browser:<br>
              <a href="{link_esc}" style="color:#1a8a52;word-break:break-all">{link_esc}</a>
            </p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


def send_followup_email(*, to_email: str, first_name: str, project_id: int) -> bool:
    """Send one follow-up nudge via Resend. Returns True iff Resend accepted
    it. Best-effort: every failure (missing key, network, non-2xx) is
    caught and returned as False, never raised — mirrors
    `invite_reminders.send_reminder_email`."""
    api_key = getattr(config_mod.settings, "resend_api_key", "") or ""
    if not api_key:
        logger.info(
            "send_followup_email skipped: RESEND_API_KEY not configured (to=%s)",
            to_email,
        )
        return False

    subject, body_text, body_html = render_followup_email(
        first_name=first_name, project_id=project_id
    )
    try:
        resp = httpx.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": _from_address(),
                "to": [to_email],
                "subject": subject,
                "text": body_text,
                "html": body_html,
            },
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Resend task-followup email failed for %s: %s %s",
                to_email, resp.status_code, resp.text[:200],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("Resend task-followup email raised for %s: %s", to_email, exc)
        return False

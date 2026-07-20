"""Onboarding welcome email — the "your workspace is ready" send.

Fired ONCE, the moment a user completes onboarding and their first workspace
(company) exists (see routes/onboarding.py:post_onboarding_complete). Distinct
from the drip cadence in app/drip_email.py — that is a scheduler-driven day-1/
3/7 nudge sequence; this is an instant, completion-triggered transactional
email signed by the founder.

Reuses the same transport and branding as the drip / weekly-brief emails:
  - Resend HTTPS API over httpx (env RESEND_API_KEY; no SMTP, no SDK).
  - Config resolved at CALL TIME (config_mod.settings.*) so the test suite's
    config reload + monkeypatched client win.
  - Best-effort: every failure (missing key, network, non-2xx) is caught and
    surfaced as a bool; a send failure never raises to the caller, so it can
    never block a user from entering the app.

Copy is the founder note verbatim, with the "one-page guide" surfaced as a
LINK (welcome_guide_url) rather than a file attachment. `{first_name}` and
`{workspace_name}` are filled from the caller's profile + company.

User-facing copy says "workspace" (the user's word for their company), never
"dataset" / "company", per the product naming rule.
"""
from __future__ import annotations

import html as html_mod
import logging

import httpx

from app import config as config_mod

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
_HTTP_TIMEOUT_SECONDS = 10.0

# Support line printed in the email body + rendered as a tel: link in HTML.
SUPPORT_PHONE = "(201) 852-5211"

_SUBJECT = "Welcome to Sprntly, {first_name} — your workspace is ready"

# Plain-text body (the Resend `text` fallback). Paragraphs are separated by
# blank lines; the numbered steps render as a list in HTML (see below).
_BODY_TEXT = (
    "Hi {first_name}, welcome to Sprntly. Your workspace, {workspace_name}, is "
    "ready.\n\n"
    "Quick picture of what you just unlocked: Sprntly is a product intelligence "
    "platform that helps you identify the most important thing to build, then "
    "drafts the PRD, spins up the prototype, and creates the tickets you can "
    "hand straight to your team. The whole loop, in one place.\n\n"
    "Two quick setup steps to get the best out of it:\n\n"
    "1. Invite your team: Settings → Team & Roles. Sprntly is a collaboration "
    "workspace and works best when everyone is building off the same context.\n"
    "2. Connect your data sources: Settings → Connectors. This is how we give "
    "you the right insights on what to build, grounded in your product and not "
    "generic advice.\n\n"
    "Here's a one-page guide that walks you from your first insight to shipping "
    "your first feature: {guide_url}\n\n"
    "If you're ever blocked, click Help in the navigation bar, or call "
    f"{SUPPORT_PHONE}. A real human picks up, not a bot.\n\n"
    "Good teams ship fast. The iconic companies of tomorrow will ship the right "
    "thing, fast. That's what we built Sprntly for.\n\n"
    "Can't wait to see what your team ships.\n\n"
    "Best,\nDavid\nCo-founder, Sprntly"
)

# Branded shell tokens — mirror app/drip_email.py + supabase/templates/*.html:
# paper background, white card, serif headline, green CTA.
_SERIF = "'Spectral',Georgia,'Times New Roman',serif"
_SANS = "'Inter',-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _from_address() -> str:
    """The From: header. Overridable via WELCOME_EMAIL_FROM; defaults to the
    generic onboarding sender on the verified mail.sprntly.ai domain. Resolved
    at call time so test config reloads apply."""
    return getattr(config_mod.settings, "welcome_email_from", "") or (
        "Sprntly <onboarding@mail.sprntly.ai>"
    )


def _app_base() -> str:
    """The app base URL for the "Open Sprntly" CTA."""
    return (
        getattr(config_mod.settings, "frontend_url", "") or "https://app.sprntly.ai"
    ).rstrip("/")


def _guide_url() -> str:
    """The one-page onboarding guide link. Overridable via WELCOME_GUIDE_URL;
    defaults to `<app_base>/guide` so the email always carries a working link
    even before a dedicated guide URL is configured."""
    return getattr(config_mod.settings, "welcome_guide_url", "") or (
        f"{_app_base()}/guide"
    )


def render_welcome_email(
    *, first_name: str, workspace_name: str
) -> tuple[str, str, str]:
    """Render the welcome email. Returns (subject, body_text, body_html).

    Pure + deterministic given the config resolved at call time — safe to unit
    test. Empty inputs degrade to friendly fallbacks ("there" / "your
    workspace") so a missing profile field never yields "Hi , ...".
    """
    safe_first = (first_name or "").strip() or "there"
    safe_ws = (workspace_name or "").strip() or "your workspace"
    guide_url = _guide_url()

    subject = _SUBJECT.format(first_name=safe_first)
    body_text = _BODY_TEXT.format(
        first_name=safe_first, workspace_name=safe_ws, guide_url=guide_url
    )
    body_html = _render_welcome_html(
        first_name=safe_first, workspace_name=safe_ws, guide_url=guide_url
    )
    return subject, body_text, body_html


def _p(text: str) -> str:
    """A body paragraph in the branded sans style."""
    return (
        f'<p style="margin:0 0 16px;font-family:{_SANS};font-size:15px;'
        f'line-height:1.65;color:#41444f">{text}</p>'
    )


def _render_welcome_html(
    *, first_name: str, workspace_name: str, guide_url: str
) -> str:
    """The branded HTML body: paper background, white card, serif headline, an
    ordered setup list, the guide link, and a green 'Open Sprntly' CTA. The
    plain-text body stays in the Resend payload as the fallback."""
    base = _app_base()
    fn = html_mod.escape(first_name)
    ws = html_mod.escape(workspace_name)
    guide = html_mod.escape(guide_url, quote=True)
    phone_digits = "".join(ch for ch in SUPPORT_PHONE if ch.isdigit())

    link_style = "color:#1a8a52;text-decoration:none;font-weight:600"
    step_style = (
        f"margin:0 0 12px;font-family:{_SANS};font-size:15px;line-height:1.6;"
        "color:#41444f"
    )

    intro = _p(
        f"Hi {fn}, welcome to Sprntly. Your workspace, "
        f'<strong style="color:#15171c">{ws}</strong>, is ready.'
    )
    pitch = _p(
        "Quick picture of what you just unlocked: Sprntly is a product "
        "intelligence platform that helps you identify the most important "
        "thing to build, then drafts the PRD, spins up the prototype, and "
        "creates the tickets you can hand straight to your team. The whole "
        "loop, in one place."
    )
    steps_lead = _p(
        '<strong style="color:#15171c">Two quick setup steps to get the best '
        "out of it:</strong>"
    )
    steps = (
        f'<ol style="margin:0 0 16px;padding-left:20px">'
        f'<li style="{step_style}"><strong style="color:#15171c">Invite your '
        "team:</strong> Settings → Team &amp; Roles. Sprntly is a collaboration "
        "workspace and works best when everyone is building off the same "
        "context.</li>"
        f'<li style="{step_style}"><strong style="color:#15171c">Connect your '
        "data sources:</strong> Settings → Connectors. This is how we give you "
        "the right insights on what to build, grounded in your product and not "
        "generic advice.</li>"
        "</ol>"
    )
    guide_p = _p(
        f'<a href="{guide}" style="{link_style}">Here\'s a one-page guide</a> '
        "that walks you from your first insight to shipping your first feature."
    )
    help_p = _p(
        "If you're ever blocked, click Help in the navigation bar, or call "
        f'<a href="tel:+1{phone_digits}" style="{link_style}">{SUPPORT_PHONE}</a>. '
        "A real human picks up, not a bot."
    )
    closing = _p(
        "Good teams ship fast. The iconic companies of tomorrow will ship the "
        "right thing, fast. That's what we built Sprntly for."
    )
    signoff_p = _p("Can't wait to see what your team ships.")
    signature = (
        f'<p style="margin:24px 0 0;font-family:{_SANS};font-size:14px;'
        f'line-height:1.6;color:#41444f">Best,<br>David<br>'
        f'<span style="color:#80838d">Co-founder, Sprntly</span></p>'
    )

    body_html = (
        intro + pitch + steps_lead + steps + guide_p + help_p + closing
        + signoff_p + signature
    )

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
            <h1 style="margin:0 0 18px;font-family:{_SERIF};font-size:23px;line-height:1.3;font-weight:600;color:#15171c">Your workspace is ready</h1>
            {body_html}
            <table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:28px">
              <tr>
                <td align="center" style="border-radius:10px;background-color:#1a8a52">
                  <a href="{base}" style="display:inline-block;padding:13px 28px;font-family:{_SANS};font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:10px">Open Sprntly</a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding:20px 8px 0;font-family:{_SANS};font-size:12px;line-height:1.7;color:#a9aab1">
            Sprntly — product intelligence for product teams<br>
            <a href="{base}" style="color:#80838d;text-decoration:none">sprntly.ai</a>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


def send_welcome_email(
    *, to_email: str, first_name: str, workspace_name: str
) -> bool:
    """Send the onboarding welcome email via Resend. Returns True iff Resend
    accepted it.

    Best-effort: every failure (missing key, network, non-2xx) is caught and
    returned as False so the caller can record/skip and move on. Mirrors the
    drip_email.send_drip_email contract."""
    api_key = getattr(config_mod.settings, "resend_api_key", "") or ""
    if not api_key:
        logger.info(
            "send_welcome_email skipped: RESEND_API_KEY not configured (to=%s)",
            to_email,
        )
        return False

    subject, body_text, body_html = render_welcome_email(
        first_name=first_name, workspace_name=workspace_name
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
                "Resend welcome send failed for %s: %s %s",
                to_email, resp.status_code, resp.text[:200],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("Resend welcome send raised for %s: %s", to_email, exc)
        return False

"""One place that puts an email on the wire.

`team_email`, `drip_email`, `welcome_email` and `delegation_followup_email` each
carry their own copy of the same twenty lines: the Resend URL, the bearer
header, the timeout, the swallow-everything error handling, the "no API key
means no-op" branch. Four copies of a thing that talks to the outside world is
four places to fix a timeout and three places to forget.

This is that code, once. It is NOT a refactor of those four — they predate it
and rewriting them belongs in its own change, not smuggled into a billing PR.
New senders should use this.

CONTRACT, matching what those modules already promise their callers:

  - Returns True on a 2xx, False on anything else. Never raises. An email is
    never the reason a webhook 500s or a scheduler tick dies.
  - No API key configured is a no-op that returns False and logs at INFO. That
    is the normal state in CI and local dev, not an error.
  - Config is read at CALL time, so the test suite's config reload and any
    monkeypatched settings win.
"""
from __future__ import annotations

import html as html_mod
import logging

import httpx

from app import config as config_mod

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
_HTTP_TIMEOUT_SECONDS = 10.0

#: The Resend API key is scoped to the verified `mail.sprntly.ai` domain, so a
#: bare `@sprntly.ai` sender is rejected with a 403. Learned the hard way; see
#: drip_email._from_address, which says the same thing.
_DEFAULT_FROM = "Sprntly <billing@mail.sprntly.ai>"

#: How billing mail signs off. Stripe's receipts are signed by nobody, which is
#: part of why a $0 trial receipt reads like a machine fault. A name at the
#: bottom is the cheapest way to say a person is behind this.
DEFAULT_SIGNOFF = "The Sprntly billing team"

# Branded shell tokens — the SAME shell as app/welcome_email.py and
# app/drip_email.py: paper background, white card, serif headline, green CTA.
# Deliberately not a new look. Somebody who got the welcome email should read
# the billing one as the same product, and a second house style is a second
# thing to keep in sync.
_SERIF = "'Spectral',Georgia,'Times New Roman',serif"
_SANS = "'Inter',-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

_INK = "#15171c"
_BODY = "#41444f"
_MUTED = "#80838d"
_FAINT = "#a9aab1"
_GREEN = "#1a8a52"
_PAPER = "#f6f5f1"
_HAIRLINE = "#e9e8e4"

#: The mark, served by the web static export. A PNG rather than the SVG beside
#: it because Gmail strips <svg> outright. The wordmark under it is TEXT, so a
#: client that blocks images — most of them, by default, for a sender you have
#: never replied to — still shows branding instead of a broken-image box.
LOGO_URL = "https://app.sprntly.ai/brand/sprntly-mark-512.png"


def default_from() -> str:
    """The From: header, overridable per deployment."""
    return getattr(config_mod.settings, "billing_from_email", "") or _DEFAULT_FROM


def render_html(
    *,
    subject: str,
    body_text: str,
    cta_label: str = "",
    cta_url: str = "",
    facts: list[tuple[str, str]] | None = None,
    signoff: str = DEFAULT_SIGNOFF,
) -> str:
    """The branded HTML for one billing email.

    Tables and inline styles, because that is all Gmail, Outlook and Apple Mail
    agree on — no flexbox, no <style> block, no external CSS.

    `facts` is the panel of numbers: the plan, the credits, the date something
    happens. Billing mail is read for exactly those, and a reader should not
    have to parse a sentence to find one. Two columns, so a long value wraps
    under its own label instead of shoving the layout sideways.

    EVERYTHING INTERPOLATED IS ESCAPED. The copy is ours; a plan label or an
    amount that reached us from Stripe is not.
    """
    esc = html_mod.escape
    paragraphs = "".join(
        f'<p style="margin:0 0 16px;font-family:{_SANS};font-size:15px;'
        f'line-height:1.65;color:{_BODY}">{esc(p)}</p>'
        for p in body_text.strip().split("\n\n")
        if p.strip()
    )

    facts_html = ""
    if facts:
        rows = "".join(
            f'<tr>'
            f'<td style="padding:9px 16px 9px 0;font-family:{_SANS};font-size:13px;'
            f'line-height:1.5;color:{_MUTED};vertical-align:top">{esc(k)}</td>'
            f'<td align="right" style="padding:9px 0;font-family:{_SANS};font-size:14px;'
            f'line-height:1.5;font-weight:600;color:{_INK};vertical-align:top">{esc(v)}</td>'
            f'</tr>'
            for k, v in facts
        )
        facts_html = (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="margin:2px 0 24px;background-color:{_PAPER};'
            f'border-radius:10px"><tr><td style="padding:4px 18px">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f'{rows}</table></td></tr></table>'
        )

    button = ""
    if cta_label and cta_url:
        button = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:2px 0 0">'
            f'<tr><td align="center" style="border-radius:10px;background-color:{_GREEN}">'
            f'<a href="{esc(cta_url, quote=True)}" style="display:inline-block;'
            f'padding:13px 28px;font-family:{_SANS};font-size:15px;font-weight:600;'
            f'color:#ffffff;text-decoration:none;border-radius:10px">{esc(cta_label)}</a>'
            f'</td></tr></table>'
        )

    signoff_html = ""
    if signoff:
        signoff_html = (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="margin:30px 0 0"><tr><td style="border-top:1px solid {_HAIRLINE};'
            f'padding-top:18px;font-family:{_SANS};font-size:14px;line-height:1.6;'
            f'color:{_BODY}">Thanks,<br>'
            f'<span style="color:{_MUTED}">{esc(signoff)}</span></td></tr></table>'
        )

    return f"""\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{_PAPER};margin:0;padding:0">
  <tr>
    <td align="center" style="padding:44px 16px 36px">
      <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;max-width:520px">
        <tr>
          <td align="center" style="padding:0 0 20px">
            <img src="{LOGO_URL}" width="34" height="34" alt="" style="display:block;margin:0 auto 10px;border:0;border-radius:9px">
            <div style="font-family:{_SERIF};font-size:25px;font-weight:600;color:{_INK};letter-spacing:-0.02em">Sprntly<span style="color:{_GREEN}">.</span></div>
          </td>
        </tr>
        <tr>
          <td style="background-color:#ffffff;border:1px solid {_HAIRLINE};border-radius:14px;padding:40px 40px 34px">
            <h1 style="margin:0 0 18px;font-family:{_SERIF};font-size:23px;line-height:1.3;font-weight:600;color:{_INK}">{esc(subject)}</h1>
            {paragraphs}{facts_html}{button}{signoff_html}
          </td>
        </tr>
        <tr>
          <td align="center" style="padding:20px 8px 0;font-family:{_SANS};font-size:12px;line-height:1.7;color:{_FAINT}">
            You are getting this because you are an owner or admin of a Sprntly workspace.<br>
            <a href="https://sprntly.ai" style="color:{_MUTED};text-decoration:none">sprntly.ai</a>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


def _as_text(
    body_text: str, facts: list[tuple[str, str]] | None, signoff: str
) -> str:
    """The plain-text alternative, carrying the same content as the HTML.

    A multipart email whose two halves disagree is a worse read on a text-only
    client AND a spam signal, so the facts and the sign-off appear here too —
    just without the table.
    """
    parts = [body_text.strip()]
    if facts:
        parts.append("\n".join(f"{k}: {v}" for k, v in facts))
    if signoff:
        parts.append(f"Thanks,\n{signoff}")
    return "\n\n".join(parts)


def send(
    *,
    to_email: str,
    subject: str,
    body_text: str,
    cta_label: str = "",
    cta_url: str = "",
    facts: list[tuple[str, str]] | None = None,
    signoff: str = DEFAULT_SIGNOFF,
    from_address: str | None = None,
) -> bool:
    """Send one email. True on success; never raises."""
    api_key = getattr(config_mod.settings, "resend_api_key", "") or ""
    if not api_key:
        logger.info("mailer: skipped, RESEND_API_KEY not configured (to=%s)", to_email)
        return False
    if not to_email or "@" not in to_email:
        logger.warning("mailer: refusing to send to %r", to_email)
        return False

    try:
        resp = httpx.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": from_address or default_from(),
                "to": [to_email],
                "subject": subject,
                "text": _as_text(body_text, facts, signoff),
                "html": render_html(
                    subject=subject,
                    body_text=body_text,
                    cta_label=cta_label,
                    cta_url=cta_url,
                    facts=facts,
                    signoff=signoff,
                ),
            },
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        if resp.status_code >= 400:
            logger.warning(
                "mailer: send failed to=%s status=%s body=%s",
                to_email, resp.status_code, resp.text[:200],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 — an email must never break a caller
        logger.warning("mailer: send raised to=%s: %s", to_email, exc)
        return False

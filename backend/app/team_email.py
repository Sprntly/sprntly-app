"""Workspace invite emails (C7 of the team-roles slice; password rules 2026-07-17).

Two paths by whether the email already has a Supabase auth account:

  - NEW user → `generate_link(type=invite)` creates the `auth.users` row
    (status: invited) and returns the link WITHOUT Supabase sending an email;
    we email the invitee ourselves. The emailed URL is `<FRONTEND_URL>
    /auth/confirm?token_hash=…&type=invite` — our own page, which consumes the
    token via `verifyOtp` only on an explicit button click. Corporate mail
    scanners (SafeLinks etc.) prefetch every link in an email with a GET; the
    raw Supabase `/auth/v1/verify` action link is consumed by that GET, so it
    must never appear in an email (2026-07-22 Freezing Point incident — all
    their invite links were dead before a human ever clicked). After verifyOtp
    the confirm page routes to `/set-password` so the invitee MUST create a
    password before entering, then `postLoginPath`'s `tryAutoAcceptInvite`
    hook converts the pending workspace_invites row into a company_members row.

  - EXISTING user → a plain notification email (Resend, same sender as the
    weekly brief) linking to `<FRONTEND_URL>/sign-in`. Deliberately NOT a
    magic link: an existing account must never be logged in by an email
    click — with a live session the sign-in page forwards them straight in
    (postLoginPath, which auto-accepts the invite); without one they sign in
    with their password first.

Best-effort: failures are caught and reported via the returned status —
the team route persists the workspace_invites row regardless. Caller
surfaces the status to the UI so the inviter can copy the accept URL
manually when the send fails.
"""
from __future__ import annotations

import html
import logging
from urllib.parse import quote

import httpx

from app import config as config_mod
from app.db import client as db_client_mod

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
_SEND_TIMEOUT_SECONDS = 15.0


def _invite_redirect_url() -> str:
    """Where Supabase should redirect the invitee after they click the
    magic link. Lands on the existing /auth/callback page which then
    runs our `postLoginPath` flow (including the team-invite
    auto-accept hook)."""
    # Resolve at call time — the test suite reloads app.config so this
    # cannot be captured at import time.
    base = (config_mod.settings.frontend_url or "").rstrip("/")
    if not base:
        # Defensive default — should never happen in deployed envs (the
        # OAuth flow already 500s without FRONTEND_URL).
        base = "http://localhost:3000"
    return f"{base}/auth/callback"


# send_invite_email outcomes.
SENT = "sent"  # new user — Supabase invite (set-password flow) sent
SENT_EXISTING = "sent_existing"  # existing user — sign-in notification email sent
FAILED = "failed"  # nothing could be sent (see logs)


def _is_already_registered(exc: Exception) -> bool:
    """True when invite_user_by_email failed because the email already has a
    Supabase auth account. Supabase 422s these with "A user with this email
    address has already been registered" — invite_user_by_email only creates
    *new* users, so existing ones need a different path (a magic-link sign-in)."""
    msg = str(exc).lower()
    return "already" in msg and "registered" in msg


def _sign_in_url() -> str:
    base = (config_mod.settings.frontend_url or "").rstrip("/")
    if not base:
        base = "http://localhost:3000"
    return f"{base}/sign-in"


# Fallbacks when a name isn't resolvable — shared with the invite reminders.
_DEFAULT_FIRST = "there"
_DEFAULT_INVITER = "a teammate"
_DEFAULT_WORKSPACE = "your workspace"

_SERIF = "'Spectral',Georgia,'Times New Roman',serif"
_SANS = "'Inter',-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _render_invite_html(
    *,
    heading: str,
    paragraphs: list[str],
    cta_label: str,
    cta_url: str,
    footnote: str = "",
) -> str:
    """Branded invite HTML (paper background, white card, serif headline, green
    CTA) matching the drip / weekly-brief shell. `heading` + `paragraphs` are
    assumed already HTML-escaped by the caller (they interpolate names)."""
    base = (config_mod.settings.frontend_url or "").rstrip("/") or (
        "https://app.sprntly.ai"
    )
    paras_html = ""
    for p in paragraphs:
        paras_html += (
            f'<p style="margin:0 0 16px;font-family:{_SANS};font-size:15px;'
            f'line-height:1.65;color:#41444f">{p}</p>'
        )
    foot_html = (
        f'<p style="margin:22px 0 0;font-family:{_SANS};font-size:13px;'
        f'line-height:1.6;color:#80838d">{html.escape(footnote)}</p>'
        if footnote
        else ""
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
            <h1 style="margin:0 0 18px;font-family:{_SERIF};font-size:23px;line-height:1.3;font-weight:600;color:#15171c">{heading}</h1>
            {paras_html}
            <table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:24px">
              <tr>
                <td align="center" style="border-radius:10px;background-color:#1a8a52">
                  <a href="{cta_url}" style="display:inline-block;padding:13px 28px;font-family:{_SANS};font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:10px">{html.escape(cta_label)}</a>
                </td>
              </tr>
            </table>
            {foot_html}
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


def _send_day0_email(
    *,
    email: str,
    accept_link: str,
    inviter_first_name: str,
    workspace_name: str,
    first_name: str,
) -> bool:
    """Send the branded Day-0 invite email via Resend. Returns True iff Resend
    accepted it. Shared by BOTH the new-user path (accept_link = the generated
    magic link) and the existing-user path (accept_link = /sign-in) — identical
    copy, only the link differs. Best-effort: a missing key or a send error
    returns False (never raises)."""
    api_key = getattr(config_mod.settings, "resend_api_key", None)
    if not api_key:
        logger.warning(
            "Day-0 invite email for %s skipped: RESEND_API_KEY not configured "
            "— inviter should share the link manually.",
            email,
        )
        return False
    inviter = (inviter_first_name or "").strip() or _DEFAULT_INVITER
    workspace = (workspace_name or "").strip() or _DEFAULT_WORKSPACE
    greet = (first_name or "").strip() or _DEFAULT_FIRST
    safe_url = html.escape(accept_link, quote=True)
    subject = f"{inviter} has invited you to Sprntly to collaborate"
    text = (
        f"Hi {greet}, {inviter} added you to the {workspace} workspace on "
        "Sprntly. It's where the team writes PRDs, reviews tickets, and puts "
        "prototypes in front of each other before anything gets built.\n\n"
        f"Set up your account here: {accept_link}\n\n"
        "It takes under 60 seconds.\n\n"
        "Best,\nThe Sprntly Team"
    )
    body_html = _render_invite_html(
        heading=f"{html.escape(inviter)} invited you to Sprntly",
        paragraphs=[
            f"Hi {html.escape(greet)}, {html.escape(inviter)} added you to the "
            f"<strong style=\"color:#15171c\">{html.escape(workspace)}</strong> "
            "workspace on Sprntly. It's where the team writes PRDs, reviews "
            "tickets, and puts prototypes in front of each other before "
            "anything gets built.",
        ],
        cta_label="Set up your account",
        cta_url=safe_url,
        footnote="It takes under 60 seconds.",
    )
    try:
        resp = httpx.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": config_mod.settings.brief_email_from,
                "to": [email],
                "subject": subject,
                "html": body_html,
                "text": text,
            },
            timeout=_SEND_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("Day-0 invite email failed for %s: %s (Resend).", email, exc)
        return False


def _notify_existing_user(
    email: str, *, inviter_first_name: str, workspace_name: str, first_name: str
) -> str:
    """Day-0 email for an already-registered invitee — same copy, but the link
    is /sign-in (NEVER a magic link: an email click must not log an existing
    account in). postLoginPath's auto-accept hook converts their pending
    workspace_invites row into a membership on their next sign-in."""
    ok = _send_day0_email(
        email=email,
        accept_link=_sign_in_url(),
        inviter_first_name=inviter_first_name,
        workspace_name=workspace_name,
        first_name=first_name,
    )
    if ok:
        logger.info(
            "Invite for existing user %s: sent Day-0 sign-in email "
            "(auto-accepts on next sign-in).",
            email,
        )
        return SENT_EXISTING
    return FAILED


def _extract_link_property(resp, name: str) -> str | None:
    """Pull a property (`action_link`, `hashed_token`, …) out of a
    generate_link response, tolerant of the supabase-py object shape
    (resp.properties.<name>) and dict shapes."""
    props = getattr(resp, "properties", None)
    if props is not None:
        value = getattr(props, name, None)
        if isinstance(value, str) and value:
            return value
        if isinstance(props, dict) and isinstance(props.get(name), str) and props[name]:
            return props[name]
    if isinstance(resp, dict):
        p = resp.get("properties")
        if isinstance(p, dict) and isinstance(p.get(name), str) and p[name]:
            return p[name]
    return None


def _extract_action_link(resp) -> str | None:
    return _extract_link_property(resp, "action_link")


def _confirm_page_link(hashed_token: str) -> str:
    """Scanner-proof accept URL: our own /auth/confirm page carrying the
    token_hash. A mail scanner's GET just loads the page; the token is only
    consumed by `verifyOtp` when the invitee clicks the accept button."""
    base = (config_mod.settings.frontend_url or "").rstrip("/")
    if not base:
        base = "http://localhost:3000"
    return f"{base}/auth/confirm?token_hash={quote(hashed_token, safe='')}&type=invite"


def _generate_invite_link(client, email: str, redirect: str, invite_data: dict) -> str | None:
    """Create the invited auth user AND return their accept link WITHOUT
    Supabase sending any email — so our own branded Day-0 email is the only one
    that goes out. Prefers the scanner-proof /auth/confirm URL built from the
    response's `hashed_token`; falls back to the raw action_link only when the
    response carries no hashed_token (old GoTrue shapes) — that link dies to
    mail-scanner prefetch, but a maybe-working link beats none. Returns None if
    neither could be read. Raises the underlying error (the caller handles
    already-registered)."""
    resp = client.auth.admin.generate_link(
        {
            "type": "invite",
            "email": email,
            "options": {"redirect_to": redirect, "data": invite_data},
        }
    )
    hashed_token = _extract_link_property(resp, "hashed_token")
    if hashed_token:
        return _confirm_page_link(hashed_token)
    logger.warning(
        "generate_link for %s returned no hashed_token — emailing the raw "
        "action link (vulnerable to mail-scanner prefetch).",
        email,
    )
    return _extract_action_link(resp)


def send_invite_email(
    email: str,
    *,
    inviter_first_name: str = "",
    workspace_name: str = "",
    first_name: str = "",
) -> str:
    """Email an invitee so they can join the workspace. Returns one of
    SENT (a new-user Day-0 email went out), SENT_EXISTING (the email already had
    an account, so the sign-in Day-0 email went out), or FAILED (see logs).
    Catches every exception so the caller never has to wrap it — the
    workspace_invites row is the source of truth either way.

    Preferred path (RESEND configured): generate_link creates the user + returns
    the accept link WITHOUT Supabase sending its templated email, then we send
    our OWN branded Day-0 email — so the copy lives entirely in code, never in
    the Supabase Dashboard template. Fallback (no RESEND, or generate_link errors
    for a non-registered reason): the classic Supabase-sent invite email, so
    invites still go out. The inviter/workspace/first names personalise the copy;
    missing names degrade to friendly fallbacks."""
    # Resolve at call time so test monkeypatches on `app.db.client` win.
    client = db_client_mod.supabase_client()
    if client is None:
        logger.warning(
            "send_invite_email skipped: supabase client not configured"
        )
        return FAILED

    redirect = _invite_redirect_url()
    invite_data = {
        "inviter_first_name": (inviter_first_name or "").strip(),
        "workspace_name": (workspace_name or "").strip(),
        "first_name": (first_name or "").strip(),
    }
    api_key = getattr(config_mod.settings, "resend_api_key", None)

    # Preferred: code-owned Day-0 email (generate the link, send it ourselves).
    if api_key:
        try:
            action_link = _generate_invite_link(client, email, redirect, invite_data)
        except Exception as exc:  # noqa: BLE001 — best-effort
            if _is_already_registered(exc):
                return _notify_existing_user(
                    email,
                    inviter_first_name=inviter_first_name,
                    workspace_name=workspace_name,
                    first_name=first_name,
                )
            logger.warning(
                "generate_link failed for %s: %s — falling back to the "
                "Supabase-sent invite email.",
                email, exc,
            )
        else:
            if action_link:
                ok = _send_day0_email(
                    email=email,
                    accept_link=action_link,
                    inviter_first_name=inviter_first_name,
                    workspace_name=workspace_name,
                    first_name=first_name,
                )
                if ok:
                    return SENT
                logger.warning(
                    "Day-0 send failed for new user %s after generate_link; "
                    "the invite row persists (resend to retry).",
                    email,
                )
                return FAILED
            logger.warning(
                "generate_link returned no action_link for %s — falling back "
                "to the Supabase-sent invite email.",
                email,
            )

    # Fallback: let Supabase send its templated invite (RESEND unset, or
    # generate_link failed for a non-registered reason). Keeps invites working.
    try:
        client.auth.admin.invite_user_by_email(
            email,
            {"redirect_to": redirect, "data": invite_data},
        )
        return SENT
    except Exception as exc:  # noqa: BLE001 — best-effort
        if _is_already_registered(exc):
            return _notify_existing_user(
                email,
                inviter_first_name=inviter_first_name,
                workspace_name=workspace_name,
                first_name=first_name,
            )
        logger.warning(
            "Supabase invite_user_by_email failed for %s: %s "
            "(redirect_to=%s). Check Supabase Auth settings: "
            "email rate limits, redirect URLs allow-list, SMTP config.",
            email, exc, redirect,
        )
        return FAILED

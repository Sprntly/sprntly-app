"""Workspace invite emails (C7 of the team-roles slice; password rules 2026-07-17).

Two paths by whether the email already has a Supabase auth account:

  - NEW user → Supabase `auth/v1/admin/invite_user_by_email`: creates the
    `auth.users` row (status: invited) and sends the invite email. Clicking it
    lands on `<FRONTEND_URL>/auth/callback` with `type=invite`; the callback
    routes them to `/set-password` so they MUST create a password before
    entering, then `postLoginPath`'s `tryAutoAcceptInvite` hook converts the
    pending workspace_invites row into a company_members row.

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


def _send_existing_user_notification(email: str) -> str:
    """Notify an already-registered user of their workspace invite with a plain
    link to the sign-in page — NEVER a magic link (an email click must not log
    an existing account in). With a live session the sign-in page forwards them
    straight in; otherwise they enter their password. Either way postLoginPath's
    auto-accept hook converts their pending workspace_invites row into a
    company_members row — same end state as the new-user invite flow, without
    creating a duplicate account.

    Sent via Resend (same sender the weekly brief uses); without a configured
    RESEND_API_KEY nothing can be sent → FAILED, and the UI already tells the
    inviter to share the link manually."""
    api_key = getattr(config_mod.settings, "resend_api_key", None)
    if not api_key:
        logger.warning(
            "Invite notification for existing user %s skipped: RESEND_API_KEY "
            "not configured — inviter should share the sign-in link manually.",
            email,
        )
        return FAILED
    url = _sign_in_url()
    safe_url = html.escape(url, quote=True)
    subject = "You've been invited to a Sprntly workspace"
    text = (
        "You've been added to a Sprntly workspace.\n\n"
        f"Open Sprntly and sign in with your existing account to join: {url}\n\n"
        "If you weren't expecting this, you can ignore this email."
    )
    body_html = (
        '<div style="font-family:Inter,Arial,sans-serif;font-size:14px;'
        'color:#15201b;line-height:1.6;">'
        "<p>You've been added to a <strong>Sprntly</strong> workspace.</p>"
        "<p>Sign in with your existing account to join your team:</p>"
        f'<p><a href="{safe_url}" style="display:inline-block;background:#179463;'
        'color:#ffffff;text-decoration:none;padding:10px 18px;border-radius:8px;">'
        "Open Sprntly</a></p>"
        "<p style=\"color:#6b7570;font-size:12px;\">If you weren't expecting "
        "this, you can ignore this email.</p>"
        "</div>"
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
        logger.info(
            "Invite for existing user %s: sent sign-in notification email "
            "(invite auto-accepts on their next sign-in).",
            email,
        )
        return SENT_EXISTING
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "Invite notification email failed for existing user %s: %s "
            "(via Resend).",
            email, exc,
        )
        return FAILED


def send_invite_email(email: str) -> str:
    """Email an invitee so they can join the workspace. Returns one of
    SENT (new user, Supabase invite sent — they set a password on landing),
    SENT_EXISTING (the email already had an account, so a plain sign-in
    notification was sent instead), or FAILED (see logs). Catches every
    exception so the caller never has to wrap it — the workspace_invites row
    is the source of truth either way."""
    # Resolve at call time so test monkeypatches on `app.db.client` win.
    client = db_client_mod.supabase_client()
    if client is None:
        logger.warning(
            "send_invite_email skipped: supabase client not configured"
        )
        return FAILED

    redirect = _invite_redirect_url()
    try:
        client.auth.admin.invite_user_by_email(
            email,
            {"redirect_to": redirect},
        )
        return SENT
    except Exception as exc:  # noqa: BLE001 — best-effort
        # invite_user_by_email only works for NEW emails; an already-registered
        # user 422s. That's not a delivery failure — send a plain sign-in
        # notification instead, which reaches the same auto-accept end state.
        if _is_already_registered(exc):
            return _send_existing_user_notification(email)
        # Genuine failure. Surface the exact Supabase error so operators can
        # fix config. Common causes:
        #   - Free-tier rate limit (3 emails/hour)
        #   - redirect_to not in Supabase "Redirect URLs" (Auth > URL Config)
        #   - SMTP not configured in Supabase project
        logger.warning(
            "Supabase invite_user_by_email failed for %s: %s "
            "(redirect_to=%s). Check Supabase Auth settings: "
            "email rate limits, redirect URLs allow-list, SMTP config.",
            email, exc, redirect,
        )
        return FAILED

"""Supabase Admin invite-email helper (C7 of the team-roles slice).

`send_invite_email(email)` calls Supabase's
`auth/v1/admin/invite_user_by_email`, which:
  - Creates an `auth.users` row if one doesn't exist (status: invited).
  - Sends the magic-link email from Supabase's default sender (or your
    configured SMTP).
  - When the invitee clicks the link they land on `<FRONTEND_URL>/auth/callback`
    already authenticated; the post-sign-in `tryAutoAcceptInvite` hook
    (web/app/lib/supabase/client.ts) then converts the pending
    workspace_invites row into a company_members row.

Best-effort: failures are caught and reported via the returned bool —
the team route persists the workspace_invites row regardless. Caller
surfaces the bool to the UI so the inviter can copy the accept URL
manually when the send fails.
"""
from __future__ import annotations

import logging

from app import config as config_mod
from app.db import client as db_client_mod

logger = logging.getLogger(__name__)


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
SENT = "sent"  # new user — Supabase invite (magic link) sent
SENT_EXISTING = "sent_existing"  # existing user — magic-link sign-in email sent
FAILED = "failed"  # nothing could be sent (see logs)


def _is_already_registered(exc: Exception) -> bool:
    """True when invite_user_by_email failed because the email already has a
    Supabase auth account. Supabase 422s these with "A user with this email
    address has already been registered" — invite_user_by_email only creates
    *new* users, so existing ones need a different path (a magic-link sign-in)."""
    msg = str(exc).lower()
    return "already" in msg and "registered" in msg


def _send_existing_user_magic_link(client, email: str, redirect: str) -> str:
    """Send a magic-link sign-in email to an already-registered user so they
    can accept a workspace invite. Signing in lands them on /auth/callback,
    where the post-login auto-accept hook converts their pending
    workspace_invites row into a company_members row — same end state as the
    new-user invite flow, just without creating a duplicate account."""
    try:
        client.auth.sign_in_with_otp(
            {
                "email": email,
                "options": {
                    "email_redirect_to": redirect,
                    # They already exist; don't (re)create an auth user.
                    "should_create_user": False,
                },
            }
        )
        logger.info(
            "Invite for existing user %s: sent magic-link sign-in email "
            "(invite auto-accepts on login).",
            email,
        )
        return SENT_EXISTING
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "Magic-link sign-in email failed for existing user %s: %s "
            "(redirect_to=%s). Check Supabase Auth settings: email rate "
            "limits, redirect URLs allow-list, SMTP config.",
            email, exc, redirect,
        )
        return FAILED


def send_invite_email(email: str) -> str:
    """Email an invitee so they can join the workspace. Returns one of
    SENT (new user, Supabase invite sent), SENT_EXISTING (the email already
    had an account, so a magic-link sign-in email was sent instead), or
    FAILED (see logs). Catches every exception so the caller never has to
    wrap it — the workspace_invites row is the source of truth either way."""
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
        # user 422s. That's not a delivery failure — fall back to a magic-link
        # sign-in email, which reaches the same auto-accept end state.
        if _is_already_registered(exc):
            return _send_existing_user_magic_link(client, email, redirect)
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

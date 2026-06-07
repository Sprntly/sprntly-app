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


def send_invite_email(email: str) -> bool:
    """Send a Supabase magic-link invite email. Returns True iff the
    Admin API call succeeded. Catches every exception so the caller
    never has to wrap it — the workspace_invites row is the source of
    truth either way."""
    # Resolve at call time so test monkeypatches on `app.db.client` win.
    client = db_client_mod.supabase_client()
    if client is None:
        logger.warning(
            "send_invite_email skipped: supabase client not configured"
        )
        return False

    try:
        client.auth.admin.invite_user_by_email(
            email,
            {"redirect_to": _invite_redirect_url()},
        )
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "Supabase invite_user_by_email failed for %s: %s", email, exc
        )
        return False

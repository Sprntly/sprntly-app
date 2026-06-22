"""Synthetic sign-in health monitor.

Catches the 2026-06-22 failure class: the Google OAuth client secret shared by
the Drive connector AND Supabase Auth's Google login was rotated/deleted without
updating every consumer, silently breaking "Sign in with Google". No test caught
it — the secret lives in external (Supabase) config and CI mocks Supabase /
never performs a real external sign-in.

This probe authenticates the configured Google OAuth client against Google's
token endpoint using a throwaway authorization code:
  - Google rejects the dummy code with ``invalid_grant``  => client_id + secret
    are VALID (the sign-in token exchange would succeed)        -> HEALTHY
  - Google rejects the CLIENT with ``invalid_client``     => secret is wrong /
    deleted (the sign-in token exchange would fail)             -> UNHEALTHY

Supabase Auth's Google login uses the SAME client + secret, so a healthy probe
means that secret is accepted by Google for both the connector and login. (It
cannot detect Supabase's stored copy diverging from ours — that needs a true
end-to-end sign-in synthetic; tracked as a follow-up.)

On UNHEALTHY: logs CRITICAL and, if Resend + an alert address are configured,
emails an alert. Fail-OPEN on transport errors so a network blip never pages.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_PROBE_TIMEOUT_S = 15
_DUMMY_CODE = "synthetic-signin-monitor-probe"


def probe_google_oauth_secret() -> tuple[bool, str]:
    """Return ``(healthy, detail)``.

    ``healthy`` is True when Google accepts the client credentials (rejecting
    only the dummy code with ``invalid_grant``), and False ONLY when Google
    explicitly rejects the client with ``invalid_client``. Any transport error
    or unexpected shape is treated as healthy-but-noted (fail-open) so a blip or
    a Google-side change can't false-page; we only assert BROKEN on a definitive
    ``invalid_client``.
    """
    client_id = settings.google_client_id
    client_secret = settings.google_client_secret
    if not client_id or not client_secret:
        return True, "google_oauth_not_configured"
    try:
        resp = httpx.post(
            _GOOGLE_TOKEN_URL,
            data={
                "code": _DUMMY_CODE,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": settings.google_oauth_redirect_uri or "",
                "grant_type": "authorization_code",
            },
            timeout=_PROBE_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 — a transport blip must not page
        logger.warning("signin_monitor: probe transport error: %s", type(exc).__name__)
        return True, f"probe_error:{type(exc).__name__}"
    try:
        err = (resp.json() or {}).get("error", "")
    except ValueError:
        err = ""
    if err == "invalid_client":
        return False, "invalid_client"   # secret wrong/deleted -> sign-in broken
    if err == "invalid_grant":
        return True, "invalid_grant"     # creds valid, dummy code rejected -> OK
    return True, f"unexpected:{resp.status_code}:{err or 'no_error'}"


def _send_alert(detail: str) -> None:
    api_key = settings.resend_api_key
    to = settings.signin_monitor_alert_email
    if not api_key or not to:
        logger.warning(
            "signin_monitor: alert email not configured "
            "(needs RESEND_API_KEY + SIGNIN_MONITOR_ALERT_EMAIL); logged only"
        )
        return
    from app.synthesis.email_delivery import _send_via_resend

    subject = "🔴 Sprntly: Google sign-in is BROKEN (OAuth client secret rejected)"
    body = (
        "The synthetic sign-in monitor found the Google OAuth client secret is "
        f"rejected by Google ({detail}). 'Sign in with Google' and the Google "
        "Drive connector will fail until the secret is corrected in BOTH places "
        "that hold it: the backend .env (GOOGLE_CLIENT_SECRET) AND Supabase "
        "Auth -> Providers -> Google. They share one Google OAuth client."
    )
    try:
        _send_via_resend(
            api_key, to=to, subject=subject,
            html_body=f"<p>{body}</p>", text_body=body,
        )
        logger.info("signin_monitor: alert email sent to %s", to)
    except Exception as exc:  # noqa: BLE001
        logger.error("signin_monitor: failed to send alert email: %s", type(exc).__name__)


async def run_google_signin_health_check() -> tuple[bool, str]:
    """Scheduler entrypoint: run the (blocking) probe off the event loop; log +
    alert on UNHEALTHY. Returns the ``(healthy, detail)`` result."""
    healthy, detail = await asyncio.to_thread(probe_google_oauth_secret)
    if healthy:
        logger.info("signin_monitor: Google OAuth secret OK (%s)", detail)
    else:
        logger.critical("signin_monitor: GOOGLE SIGN-IN BROKEN — %s", detail)
        _send_alert(detail)
    return healthy, detail

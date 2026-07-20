"""Tests for workspace invite-email delivery (C7 + code-owned Day-0 rewrite).

Delivery model (app/team_email.send_invite_email):

  - NEW user, RESEND configured (the prod path): `generate_link(type=invite)`
    creates the auth user AND returns the accept link WITHOUT Supabase sending
    its templated email; we then send our OWN branded Day-0 email via Resend.
    The copy lives entirely in code — the Supabase Dashboard template no longer
    matters.
  - NEW user, no RESEND key: fall back to `invite_user_by_email` so Supabase
    sends its templated invite (invites still go out).
  - EXISTING user (generate_link / invite 422s "already registered"): send the
    Day-0 email linking to /sign-in — NEVER a magic link (an email click must
    not log an existing account in).

Best-effort: if sending fails the workspace_invites row STILL persists and the
route returns 201 with email_sent=false so the UI can prompt a manual share.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import app.auth  # noqa: F401

from tests._company_helpers import company_client

_ALREADY_REGISTERED = "A user with this email address has already been registered"

# A representative Supabase magic link generate_link returns.
_ACTION_LINK = (
    "https://proj.supabase.co/auth/v1/verify?token=abc123&type=invite"
    "&redirect_to=http://localhost:3000/auth/callback"
)


def _install_fake_admin(monkeypatch, *, action_link: str = _ACTION_LINK):
    """Point `supabase_client().auth.admin` at mocks for BOTH generate_link
    (default: returns `action_link`) and invite_user_by_email (fallback path),
    plus a sign_in_with_otp mock we assert is never used. Returns the admin
    mock for per-test assertions/side-effects."""
    admin_mock = MagicMock()
    admin_mock.generate_link = MagicMock(
        return_value=SimpleNamespace(
            properties=SimpleNamespace(action_link=action_link),
            user=SimpleNamespace(id="auth-user-id"),
        )
    )
    admin_mock.invite_user_by_email = MagicMock(
        return_value=SimpleNamespace(user=SimpleNamespace(id="auth-user-id"))
    )
    otp_mock = MagicMock(return_value=SimpleNamespace())

    from app.db import client as db_client_mod

    fake_db_client = db_client_mod.supabase_client()
    fake_db_client.auth = SimpleNamespace(admin=admin_mock, sign_in_with_otp=otp_mock)
    return admin_mock


def _otp_mock():
    from app.db import client as db_client_mod

    return db_client_mod.supabase_client().auth.sign_in_with_otp


def _set_resend(monkeypatch, key: str = "rk-test"):
    """Configure RESEND + patch team_email's httpx.post. Returns the post mock.
    Call AFTER company_client (which reloads app.config)."""
    import app.config as config_mod
    from app import team_email

    monkeypatch.setattr(config_mod.settings, "resend_api_key", key, raising=False)
    post_mock = MagicMock(return_value=SimpleNamespace(raise_for_status=lambda: None))
    monkeypatch.setattr(team_email.httpx, "post", post_mock)
    return post_mock


def _clear_resend(monkeypatch):
    import app.config as config_mod

    monkeypatch.setattr(config_mod.settings, "resend_api_key", "", raising=False)


# ─────────────── NEW user: code-owned Day-0 email (generate_link) ───────────


def test_new_user_uses_generate_link_and_our_email(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    admin_mock = _install_fake_admin(monkeypatch)
    post_mock = _set_resend(monkeypatch)

    r = ctx.client.post(
        "/v1/team/invites", json={"email": "fresh@co.com", "role": "member"}
    )
    assert r.status_code == 201, r.text
    assert r.json().get("email_sent") is True

    # We generated the link ourselves and did NOT let Supabase send its template.
    admin_mock.generate_link.assert_called_once()
    admin_mock.invite_user_by_email.assert_not_called()
    args, kwargs = admin_mock.generate_link.call_args
    params = args[0] if args else kwargs
    assert params["type"] == "invite"
    assert params["email"] == "fresh@co.com"
    assert params["options"]["redirect_to"].startswith("http")

    # Our Resend email carries the Day-0 copy + the generated accept link.
    post_mock.assert_called_once()
    payload = post_mock.call_args.kwargs["json"]
    assert payload["to"] == ["fresh@co.com"]
    assert "has invited you to Sprntly to collaborate" in payload["subject"]
    assert _ACTION_LINK in payload["text"]


def test_new_user_falls_back_to_supabase_when_no_resend(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    admin_mock = _install_fake_admin(monkeypatch)
    _clear_resend(monkeypatch)

    r = ctx.client.post(
        "/v1/team/invites", json={"email": "fresh2@co.com", "role": "member"}
    )
    assert r.status_code == 201, r.text
    assert r.json().get("email_sent") is True
    # Without RESEND we don't generate our own link; Supabase sends its template.
    admin_mock.generate_link.assert_not_called()
    admin_mock.invite_user_by_email.assert_called_once()


def test_invite_persists_even_if_email_send_fails(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _install_fake_admin(monkeypatch)
    post_mock = _set_resend(monkeypatch)
    post_mock.side_effect = Exception("resend down")

    r = ctx.client.post(
        "/v1/team/invites", json={"email": "x@co.com", "role": "member"}
    )
    assert r.status_code == 201, r.text
    assert r.json().get("email_sent") is False
    from app.db.client import require_client

    rows = (
        require_client()
        .table("workspace_invites")
        .select("id")
        .eq("company_id", ctx.company_id)
        .eq("email", "x@co.com")
        .execute()
        .data
    )
    assert len(rows) == 1


def test_redirect_to_uses_frontend_url(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    import app.config as config_mod

    monkeypatch.setattr(
        config_mod.settings, "frontend_url", "https://app.example", raising=False
    )
    admin_mock = _install_fake_admin(monkeypatch)
    _set_resend(monkeypatch)

    ctx.client.post(
        "/v1/team/invites", json={"email": "r@co.com", "role": "member"}
    )
    params = admin_mock.generate_link.call_args.args[0]
    assert params["options"]["redirect_to"].startswith("https://app.example")


# ─────────────────────── resend triggers a re-send ───────────────────────


def test_resend_sends_again(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _install_fake_admin(monkeypatch)
    post_mock = _set_resend(monkeypatch)

    iid = ctx.client.post(
        "/v1/team/invites", json={"email": "rs@co.com", "role": "member"}
    ).json()["id"]
    post_mock.reset_mock()

    r = ctx.client.post(f"/v1/team/invites/{iid}/resend")
    assert r.status_code == 200
    post_mock.assert_called_once()
    assert post_mock.call_args.kwargs["json"]["to"] == ["rs@co.com"]


def test_resend_email_sent_false_when_send_fails(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _install_fake_admin(monkeypatch)
    post_mock = _set_resend(monkeypatch)
    iid = ctx.client.post(
        "/v1/team/invites", json={"email": "rsf@co.com", "role": "member"}
    ).json()["id"]
    post_mock.reset_mock()
    post_mock.side_effect = Exception("provider down")

    r = ctx.client.post(f"/v1/team/invites/{iid}/resend")
    assert r.status_code == 200
    assert r.json().get("email_sent") is False


# ──────────────── existing-user invitee (already registered) ────────────────


def test_existing_user_sends_signin_email(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    admin_mock = _install_fake_admin(monkeypatch)
    admin_mock.generate_link.side_effect = Exception(_ALREADY_REGISTERED)
    post_mock = _set_resend(monkeypatch)

    r = ctx.client.post(
        "/v1/team/invites", json={"email": "existing@co.com", "role": "member"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body.get("email_sent") is True
    assert body.get("existing_user") is True

    _otp_mock().assert_not_called()  # never a magic-link login for existing accounts
    post_mock.assert_called_once()
    payload = post_mock.call_args.kwargs["json"]
    assert payload["to"] == ["existing@co.com"]
    assert "/sign-in" in payload["text"]
    assert "/sign-in" in payload["html"]


def test_existing_user_send_failure_is_email_sent_false(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    admin_mock = _install_fake_admin(monkeypatch)
    admin_mock.generate_link.side_effect = Exception(_ALREADY_REGISTERED)
    post_mock = _set_resend(monkeypatch)
    post_mock.side_effect = Exception("resend rate limit")

    r = ctx.client.post(
        "/v1/team/invites", json={"email": "existing2@co.com", "role": "member"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body.get("email_sent") is False
    assert body.get("existing_user") is not True


def test_existing_user_no_resend_key_is_email_sent_false(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    admin_mock = _install_fake_admin(monkeypatch)
    # No RESEND → fall through to Supabase invite, which 422s "already
    # registered"; the existing-user email then can't send (no key).
    admin_mock.invite_user_by_email.side_effect = Exception(_ALREADY_REGISTERED)
    _clear_resend(monkeypatch)

    r = ctx.client.post(
        "/v1/team/invites", json={"email": "existing3@co.com", "role": "member"}
    )
    assert r.status_code == 201, r.text
    assert r.json().get("email_sent") is False
    _otp_mock().assert_not_called()


def test_existing_user_unit_returns_sent_existing(isolated_settings, monkeypatch):
    company_client(monkeypatch)
    admin_mock = _install_fake_admin(monkeypatch)
    admin_mock.generate_link.side_effect = Exception(_ALREADY_REGISTERED)
    _set_resend(monkeypatch)

    from app import team_email

    assert team_email.send_invite_email("dup@co.com") == team_email.SENT_EXISTING

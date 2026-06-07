"""Tests for Supabase invite-email delivery (C7).

When the Settings → Team page creates or resends an invite, we now also
call `supabase.auth.admin.invite_user_by_email(email, {redirect_to})`
so the invitee gets a magic-link email. The DB row (workspace_invites)
is still the source of truth for *pending* invites — the email is the
delivery channel that takes the invitee back to our app, where the
post-sign-in auto-accept hook (C4) turns the pending row into a
membership.

Best-effort semantics: if the Supabase admin call raises, the invite
row STILL persists. The route response carries an `email_sent: bool`
flag so the UI can render a "saved but email failed — share the link
manually" warning instead of a hard error.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import app.auth  # noqa: F401

import pytest

from tests._company_helpers import company_client


def _install_fake_admin(monkeypatch):
    """Replace `supabase_client().auth.admin.invite_user_by_email` with a
    MagicMock that records calls. Returns the mock for assertions."""
    admin_mock = MagicMock()
    admin_mock.invite_user_by_email = MagicMock(
        return_value=SimpleNamespace(user=SimpleNamespace(id="auth-user-id"))
    )

    # `app.db.client.supabase_client` is patched by the isolated_settings
    # fixture to return a FakeSupabaseClient. Wrap it so its `.auth.admin`
    # attribute points at our mock without disturbing the DB-row table()
    # calls. The two clients (DB rows vs. auth admin) share one instance
    # in prod, so this mirrors reality closely enough.
    from app.db import client as db_client_mod

    fake_db_client = db_client_mod.supabase_client()
    fake_db_client.auth = SimpleNamespace(admin=admin_mock)
    return admin_mock


# ─────────────────────── invite POST sends email ───────────────────────


def test_invite_post_calls_supabase_admin_invite(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    admin_mock = _install_fake_admin(monkeypatch)

    r = ctx.client.post(
        "/v1/team/invites", json={"email": "fresh@co.com", "role": "member"}
    )
    assert r.status_code == 201, r.text

    body = r.json()
    assert body["email"] == "fresh@co.com"
    # New response field: did the email actually go out?
    assert body.get("email_sent") is True

    # Verify the admin call shape: email + redirect_to to our frontend.
    admin_mock.invite_user_by_email.assert_called_once()
    args, kwargs = admin_mock.invite_user_by_email.call_args
    # Either positional (email, options) or kwargs — accept both shapes.
    sent_email = args[0] if args else kwargs.get("email")
    options = args[1] if len(args) > 1 else kwargs.get("options") or {}
    assert sent_email == "fresh@co.com"
    redirect = (
        options.get("redirect_to") if isinstance(options, dict) else None
    )
    assert redirect and redirect.startswith("http"), (
        f"redirect_to should point at our frontend; got {redirect!r}"
    )


def test_invite_persists_even_if_email_send_fails(isolated_settings, monkeypatch):
    """If Supabase 4xx's the admin call, we KEEP the workspace_invites
    row and return 201 with email_sent=false. The inviter can then share
    the link manually or retry via the Resend button."""
    ctx = company_client(monkeypatch)
    admin_mock = _install_fake_admin(monkeypatch)
    admin_mock.invite_user_by_email.side_effect = Exception(
        "supabase rate limit"
    )

    r = ctx.client.post(
        "/v1/team/invites", json={"email": "x@co.com", "role": "member"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "x@co.com"
    assert body.get("email_sent") is False
    # Row should still be in the DB so the UI shows it as pending.
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


def test_invite_redirect_to_uses_frontend_url(isolated_settings, monkeypatch):
    """The magic link should land users on our app's auth callback, not
    on Supabase's default. Whatever FRONTEND_URL is wired to (localhost
    in tests, app.sprntly.ai in prod) is what we pass."""
    # company_client reloads app.config inside setup_supabase_auth, so the
    # frontend_url override has to happen AFTER that reload.
    ctx = company_client(monkeypatch)
    import app.config as config_mod

    monkeypatch.setattr(
        config_mod.settings, "frontend_url", "https://app.example", raising=False
    )
    admin_mock = _install_fake_admin(monkeypatch)

    ctx.client.post(
        "/v1/team/invites", json={"email": "r@co.com", "role": "member"}
    )
    args, kwargs = admin_mock.invite_user_by_email.call_args
    options = args[1] if len(args) > 1 else kwargs.get("options") or {}
    assert options.get("redirect_to", "").startswith("https://app.example")


# ─────────────────────── resend triggers a re-send ───────────────────────


def test_resend_calls_supabase_admin_invite_again(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    admin_mock = _install_fake_admin(monkeypatch)

    iid = ctx.client.post(
        "/v1/team/invites", json={"email": "rs@co.com", "role": "member"}
    ).json()["id"]
    # First call was during create; reset the mock so we only assert on resend.
    admin_mock.invite_user_by_email.reset_mock()

    r = ctx.client.post(f"/v1/team/invites/{iid}/resend")
    assert r.status_code == 200

    admin_mock.invite_user_by_email.assert_called_once()
    args, kwargs = admin_mock.invite_user_by_email.call_args
    sent_email = args[0] if args else kwargs.get("email")
    assert sent_email == "rs@co.com"


def test_resend_email_sent_false_when_admin_fails(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    admin_mock = _install_fake_admin(monkeypatch)
    iid = ctx.client.post(
        "/v1/team/invites", json={"email": "rsf@co.com", "role": "member"}
    ).json()["id"]
    admin_mock.invite_user_by_email.reset_mock()
    admin_mock.invite_user_by_email.side_effect = Exception("provider down")

    r = ctx.client.post(f"/v1/team/invites/{iid}/resend")
    assert r.status_code == 200
    assert r.json().get("email_sent") is False

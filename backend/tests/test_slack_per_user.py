"""Slack connector is PER-USER, not company-shared.

Every other connector (github / clickup / hubspot / figma) is company-scoped
and shared by all members. Slack is the exception: each user installs their
own bot, picks their own channel, and gets their own notifications. The bug
these tests lock down: with a single company-shared Slack row, member B could
read member A's Slack token + channels, post as A's bot, and disconnect A.

Coverage:
  - cross-user denial: member B cannot read/disconnect member A's Slack
  - each user resolves THEIR OWN Slack on channels / config / test
  - callback persists the user_id carried in the signed state
  - legacy NULL-user rows are excluded from per-user reads (→ reconnect)
  - two users in one company can both connect Slack (partial-unique allows it)
  - per-user db accessors round-trip + stay isolated
  - notification/brief delivery fans out per-user
  - company-scoped providers are untouched (regression)
"""
from __future__ import annotations

import importlib
import json
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tests._company_helpers import (
    seed_connection,
    setup_supabase_auth,
    supabase_bearer,
)


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.slack_oauth",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def slack_env(isolated_settings, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SLACK_CLIENT_ID", "test-slack-client-id")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "test-slack-client-secret")
    monkeypatch.setenv(
        "SLACK_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/slack/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    yield


def _seed_member(company_id: str, user_id: str, role: str = "member") -> None:
    """Add another member to an existing company (the company + first member
    already seeded). Lets two users share one company."""
    from app.db.client import require_client

    c = require_client()
    c.table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": company_id,
            "user_id": user_id,
            "role": role,
        }
    ).execute()


def _two_user_company(monkeypatch) -> SimpleNamespace:
    """One company with TWO members (A = owner, B = member). Returns a client
    per user, both pointed at the same in-memory app."""
    setup_supabase_auth(monkeypatch)
    import app.main as main_mod

    importlib.reload(sys.modules["app.main"])

    from app.db.client import require_client

    company_id = uuid.uuid4().hex
    user_a = "user-a-" + uuid.uuid4().hex[:6]
    user_b = "user-b-" + uuid.uuid4().hex[:6]

    c = require_client()
    c.table("companies").insert(
        {"id": company_id, "slug": f"co-{company_id[:6]}", "display_name": "Co"}
    ).execute()
    _seed_member(company_id, user_a, "owner")
    _seed_member(company_id, user_b, "member")

    client_a = TestClient(main_mod.app, headers=supabase_bearer(user_a))
    client_b = TestClient(main_mod.app, headers=supabase_bearer(user_b))
    return SimpleNamespace(
        company_id=company_id,
        user_a=user_a,
        user_b=user_b,
        client_a=client_a,
        client_b=client_b,
    )


# ─────────────────────────── slack_oauth state ───────────────────────────


def test_state_carries_and_verifies_user_id(slack_env):
    from app.connectors import slack_oauth

    state = slack_oauth.sign_oauth_state(company_id="co-1", user_id="u-7")
    payload = slack_oauth.verify_oauth_state(state)
    assert payload["company_id"] == "co-1"
    assert payload["user_id"] == "u-7"


def test_verify_state_rejects_missing_user_id(slack_env):
    import time

    import jwt
    from fastapi import HTTPException

    from app.config import settings
    from app.connectors import slack_oauth

    now = int(time.time())
    # Old-shape state with no user_id must be rejected outright.
    legacy = jwt.encode(
        {
            "provider": "slack",
            "company_id": "co-1",
            "nonce": "x",
            "iat": now,
            "exp": now + 600,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        slack_oauth.verify_oauth_state(legacy)
    assert exc.value.status_code == 400


# ─────────────────────────── db accessors ───────────────────────────


def test_db_slack_accessors_roundtrip_and_isolate(slack_env):
    from app import db
    from app.connectors.tokens import encrypt_token_json

    company = uuid.uuid4().hex
    # Seed the company (FK target).
    from app.db.client import require_client
    require_client().table("companies").insert(
        {"id": company, "slug": "co", "display_name": "Co"}
    ).execute()

    enc_a = encrypt_token_json(json.dumps({"access_token": "xoxb-A"}))
    enc_b = encrypt_token_json(json.dumps({"access_token": "xoxb-B"}))
    db.upsert_slack_connection(
        company_id=company, user_id="A", token_encrypted=enc_a,
        scopes="", account_label="A-team")
    db.upsert_slack_connection(
        company_id=company, user_id="B", token_encrypted=enc_b,
        scopes="", account_label="B-team")

    # Each user sees only their own row.
    row_a = db.get_slack_connection(company, "A")
    row_b = db.get_slack_connection(company, "B")
    assert row_a["account_label"] == "A-team"
    assert row_b["account_label"] == "B-team"
    assert row_a["user_id"] == "A"

    # list returns both per-user rows.
    listed = db.list_slack_connections(company)
    assert {r["user_id"] for r in listed} == {"A", "B"}

    # delete is scoped to one user.
    db.delete_slack_connection(company, "A")
    assert db.get_slack_connection(company, "A") is None
    assert db.get_slack_connection(company, "B") is not None


def test_two_users_one_company_both_connect(slack_env):
    """Partial-unique (company_id, user_id, provider) allows two Slack rows
    in one company — the old unique(company_id, provider) would have rejected
    the second."""
    from app import db
    from app.connectors.tokens import encrypt_token_json
    from app.db.client import require_client

    company = uuid.uuid4().hex
    require_client().table("companies").insert(
        {"id": company, "slug": "co", "display_name": "Co"}
    ).execute()
    enc = encrypt_token_json(json.dumps({"access_token": "t"}))
    db.upsert_slack_connection(company_id=company, user_id="A",
                               token_encrypted=enc, scopes="")
    db.upsert_slack_connection(company_id=company, user_id="B",
                               token_encrypted=enc, scopes="")
    assert len(db.list_slack_connections(company)) == 2


def test_list_excludes_legacy_null_user_rows(slack_env):
    """Legacy company-shared Slack rows (user_id IS NULL) are orphaned: never
    returned by per-user reads, so those users reconnect."""
    from app import db
    from app.connectors.tokens import encrypt_token_json
    from app.db.client import require_client

    company = uuid.uuid4().hex
    c = require_client()
    c.table("companies").insert(
        {"id": company, "slug": "co", "display_name": "Co"}
    ).execute()
    # Insert a legacy NULL-user slack row directly.
    enc = encrypt_token_json(json.dumps({"access_token": "legacy"}))
    c.table("connections").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": company,
            "user_id": None,
            "provider": "slack",
            "status": "active",
            "scopes": "",
            "token_json_encrypted": enc,
            "config": "{}",
        }
    ).execute()

    assert db.list_slack_connections(company) == []
    assert db.get_slack_connection(company, "anyone") is None


# ─────────────────────────── routes: cross-user denial ───────────────────────────


def test_member_b_cannot_read_member_a_slack(slack_env, monkeypatch):
    ctx = _two_user_company(monkeypatch)
    # Only A connects Slack.
    seed_connection(company_id=ctx.company_id, user_id=ctx.user_a,
                    provider="slack", token_blob={"access_token": "xoxb-A"},
                    label="A-team")

    # B asks for channels — must 404 (B has no Slack), never see A's.
    r = ctx.client_b.get("/v1/connectors/slack/channels")
    assert r.status_code == 404

    # B's connectors listing must NOT surface A's Slack at all.
    listed_b = ctx.client_b.get("/v1/connectors").json()
    assert not any(c["provider"] == "slack" for c in listed_b["connections"])
    # A's listing shows A's own Slack.
    listed_a = ctx.client_a.get("/v1/connectors").json()
    assert any(c["provider"] == "slack" for c in listed_a["connections"])

    # A asks for channels — resolves A's own token.
    with patch(
        "app.routes.connectors.slack_oauth.list_channels",
        return_value=[{"id": "C1", "name": "a-only", "is_private": False,
                       "is_member": True, "is_archived": False}],
    ) as mock_list:
        r = ctx.client_a.get("/v1/connectors/slack/channels")
    assert r.status_code == 200
    mock_list.assert_called_once_with("xoxb-A")


def test_member_b_disconnect_does_not_kill_member_a_slack(slack_env, monkeypatch):
    ctx = _two_user_company(monkeypatch)
    seed_connection(company_id=ctx.company_id, user_id=ctx.user_a,
                    provider="slack", token_blob={"access_token": "xoxb-A"})

    # B disconnect → 404, A untouched.
    r = ctx.client_b.delete("/v1/connectors/slack")
    assert r.status_code == 404

    from app import db
    assert db.get_slack_connection(ctx.company_id, ctx.user_a) is not None

    # A disconnect → 200, only A's row removed.
    r = ctx.client_a.delete("/v1/connectors/slack")
    assert r.status_code == 200
    assert db.get_slack_connection(ctx.company_id, ctx.user_a) is None


def test_each_user_gets_their_own_slack_on_channels(slack_env, monkeypatch):
    ctx = _two_user_company(monkeypatch)
    seed_connection(company_id=ctx.company_id, user_id=ctx.user_a,
                    provider="slack", token_blob={"access_token": "xoxb-A"})
    seed_connection(company_id=ctx.company_id, user_id=ctx.user_b,
                    provider="slack", token_blob={"access_token": "xoxb-B"})

    with patch(
        "app.routes.connectors.slack_oauth.list_channels",
        return_value=[],
    ) as mock_list:
        ctx.client_a.get("/v1/connectors/slack/channels")
        ctx.client_b.get("/v1/connectors/slack/channels")

    tokens_used = {call.args[0] for call in mock_list.call_args_list}
    assert tokens_used == {"xoxb-A", "xoxb-B"}


def test_config_saves_to_current_users_slack_only(slack_env, monkeypatch):
    ctx = _two_user_company(monkeypatch)
    seed_connection(company_id=ctx.company_id, user_id=ctx.user_a,
                    provider="slack", token_blob={"access_token": "xoxb-A"})
    seed_connection(company_id=ctx.company_id, user_id=ctx.user_b,
                    provider="slack", token_blob={"access_token": "xoxb-B"})

    ra = ctx.client_a.post("/v1/connectors/slack/config",
                           json={"channel_id": "C-A", "channel_name": "a"})
    rb = ctx.client_b.post("/v1/connectors/slack/config",
                           json={"channel_id": "C-B", "channel_name": "b"})
    assert ra.status_code == 200 and rb.status_code == 200

    from app import db
    row_a = db.get_slack_connection(ctx.company_id, ctx.user_a)
    row_b = db.get_slack_connection(ctx.company_id, ctx.user_b)
    assert json.loads(row_a["config_json"])["channel_id"] == "C-A"
    assert json.loads(row_b["config_json"])["channel_id"] == "C-B"


# ─────────────────────────── routes: callback persists user_id ───────────────────────────


def test_callback_persists_user_id_from_state(slack_env, monkeypatch):
    ctx = _two_user_company(monkeypatch)
    from app.connectors import slack_oauth

    state = slack_oauth.sign_oauth_state(
        company_id=ctx.company_id, user_id=ctx.user_b)

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "ok": True,
        "access_token": "xoxb-from-callback",
        "bot_user_id": "U99",
        "team": {"id": "T1", "name": "Meridian"},
        "scope": "chat:write",
    }
    with patch("app.connectors.slack_oauth.requests.post", return_value=mock_resp):
        r = ctx.client_b.get(
            "/v1/connectors/slack/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 307

    from app import db
    # Stored under user B (from state), not A.
    assert db.get_slack_connection(ctx.company_id, ctx.user_b) is not None
    assert db.get_slack_connection(ctx.company_id, ctx.user_a) is None


# ─────────────────────────── delivery fans out per-user ───────────────────────────


def test_delivery_targets_each_user_own_slack(slack_env, monkeypatch):
    from app.synthesis import delivery

    rows = [
        {"user_id": "A", "status": "active", "config": {"channel_id": "C-A"},
         "token_json_encrypted": "encA"},
        {"user_id": "B", "status": "active", "config": {"channel_id": "C-B"},
         "token_json_encrypted": "encB"},
    ]
    tokens = {"encA": {"access_token": "xoxb-A"},
              "encB": {"access_token": "xoxb-B"}}
    posts: list[tuple[str, str]] = []

    monkeypatch.setattr(delivery.db, "list_slack_connections", lambda cid: rows)
    monkeypatch.setattr(delivery, "decrypt_token_json",
                        lambda enc: json.dumps(tokens[enc]))
    monkeypatch.setattr(
        delivery.slack_oauth, "post_message",
        lambda tok, *, channel, text, blocks, **k: posts.append((tok, channel))
        or {"ok": True})

    out = delivery.deliver_brief_to_slack(
        "ent-A", {"summary_headline": "h", "week_label": "w", "insights": []})
    assert out["delivered"] is True
    # Each recipient got the brief on their OWN token + channel.
    assert ("xoxb-A", "C-A") in posts
    assert ("xoxb-B", "C-B") in posts
    assert len(posts) == 2


def test_delivery_skips_user_without_channel(slack_env, monkeypatch):
    from app.synthesis import delivery

    rows = [
        {"user_id": "A", "status": "active", "config": {"channel_id": "C-A"},
         "token_json_encrypted": "encA"},
        {"user_id": "B", "status": "active", "config": {},  # no channel
         "token_json_encrypted": "encB"},
    ]
    monkeypatch.setattr(delivery.db, "list_slack_connections", lambda cid: rows)
    monkeypatch.setattr(delivery, "decrypt_token_json",
                        lambda enc: json.dumps({"access_token": "t"}))
    monkeypatch.setattr(delivery.slack_oauth, "post_message",
                        lambda *a, **k: {"ok": True})

    out = delivery.deliver_brief_to_slack(
        "ent-A", {"summary_headline": "h", "week_label": "w", "insights": []})
    by_user = {r["user_id"]: r for r in out["recipients"]}
    assert by_user["A"]["delivered"] is True
    assert by_user["B"]["delivered"] is False
    assert by_user["B"]["reason"] == "no_channel_configured"


def test_delivery_dm_target_routes_to_user_dm(slack_env, monkeypatch):
    """A user whose target_type is 'dm' gets the brief in their own DM — no
    channel needed; the installing user's authed_user_id is the recipient."""
    from app.synthesis import delivery

    rows = [
        {"user_id": "A", "status": "active",
         "config": {"target_type": "dm"},  # no channel_id
         "token_json_encrypted": "encA"},
    ]
    calls: list[dict] = []
    monkeypatch.setattr(delivery.db, "list_slack_connections", lambda cid: rows)
    monkeypatch.setattr(
        delivery, "decrypt_token_json",
        lambda enc: json.dumps({"access_token": "xoxb-A", "authed_user_id": "U-A"}))
    monkeypatch.setattr(
        delivery.slack_oauth, "post_to_target",
        lambda tok, *, config, authed_user_id, text, blocks: calls.append(
            {"tok": tok, "config": config, "authed_user_id": authed_user_id})
        or {"ok": True, "channel": "D-A"})

    out = delivery.deliver_brief_to_slack(
        "ent-A", {"summary_headline": "h", "week_label": "w", "insights": []})
    assert out["delivered"] is True
    # The DM config + the resolved installing user flow through to the router.
    assert calls[0]["config"]["target_type"] == "dm"
    assert calls[0]["authed_user_id"] == "U-A"


# ─────────────────────────── regression: other providers untouched ───────────────────────────


@pytest.mark.parametrize("provider", ["github", "clickup", "hubspot", "figma"])
def test_company_scoped_providers_still_shared(slack_env, monkeypatch, provider):
    """Non-Slack providers stay company-scoped + member-shared: both members
    see the same single connection, keyed by (company_id, provider) only."""
    ctx = _two_user_company(monkeypatch)
    seed_connection(company_id=ctx.company_id, provider=provider,
                    token_blob={"access_token": "shared"}, label="shared-acct")

    from app import db
    # One company-scoped row, reachable by company_id alone (no user_id).
    row = db.get_connection(ctx.company_id, provider)
    assert row is not None
    assert row.get("user_id") is None
    assert row["account_label"] == "shared-acct"

    # Both members' /v1/connectors listing shows it (shared).
    for client in (ctx.client_a, ctx.client_b):
        listed = client.get("/v1/connectors").json()
        assert any(c["provider"] == provider for c in listed["connections"])

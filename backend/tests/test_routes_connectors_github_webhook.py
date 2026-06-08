"""Tests for the GitHub webhook + installation-token surface.

We cover:
  - HMAC-SHA256 signature verification (good, bad, missing secret/header)
  - installation token caching (cache hit, cache miss → API call, cache
    invalidation on expiry)
  - webhook event dispatch:
      ping → 200 no-op
      installation (created/deleted/suspend) → db upsert/delete + cache clear
      installation_repositories → repository_selection update
      pull_request (opened/closed/merged) → db upsert with right state
  - the GET /github/installations and /github/pull-requests list endpoints
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import sys
import time
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient


WEBHOOK_SECRET = "test-webhook-secret-32-bytes-long"


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.figma_oauth",
        "app.connectors.github_app",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def github_app_env(isolated_settings, monkeypatch):
    """Full GitHub App config including private key + webhook secret."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "gh-client-id")
    monkeypatch.setenv("GITHUB_APP_CLIENT_SECRET", "gh-client-secret")
    monkeypatch.setenv(
        "GITHUB_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/github/callback",
    )
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", WEBHOOK_SECRET)

    # Generate an RSA key for the App so make_app_jwt/get_installation_token can sign.
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", pem)
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")

    _reload_app_modules()
    import app.db as db_mod
    db_mod.init_db()
    # Clear in-process token cache between tests.
    from app.connectors import github_app
    github_app.clear_installation_token_cache()
    yield {"private_key": private_key}


@pytest.fixture
def client(github_app_env):
    import app.main as main_mod
    c = TestClient(main_mod.app)
    r = c.post("/v1/auth/login", json={"password": "test-pw"})
    assert r.status_code == 200
    return c


# ─────────────────────── signature verification ───────────────────────


def test_verify_webhook_signature_accepts_valid_signature(github_app_env):
    from app.connectors import github_app
    body = b'{"hello":"world"}'
    sig = _sign(body)
    assert github_app.verify_webhook_signature(body, sig) is True


def test_verify_webhook_signature_rejects_bad_signature(github_app_env):
    from app.connectors import github_app
    body = b'{"hello":"world"}'
    assert github_app.verify_webhook_signature(body, "sha256=deadbeef") is False


def test_verify_webhook_signature_rejects_missing_header(github_app_env):
    from app.connectors import github_app
    body = b'{}'
    assert github_app.verify_webhook_signature(body, None) is False


def test_verify_webhook_signature_rejects_wrong_prefix(github_app_env):
    from app.connectors import github_app
    body = b'{}'
    digest = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert github_app.verify_webhook_signature(body, f"sha1={digest}") is False


def test_verify_webhook_signature_rejects_without_secret(github_app_env, monkeypatch):
    """If GITHUB_WEBHOOK_SECRET is empty, every request must be rejected."""
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    _reload_app_modules()
    from app.connectors import github_app
    assert github_app.verify_webhook_signature(b'{}', "sha256=anything") is False


# ─────────────────────── installation token cache ───────────────────────


def test_get_installation_token_caches_across_calls(github_app_env):
    from app.connectors import github_app

    fake_resp = MagicMock(ok=True)
    fake_resp.json.return_value = {
        "token": "ghs_abc",
        # Expiry 1h from now in GitHub's ISO format.
        "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600)),
    }
    with patch("app.connectors.github_app.requests.post", return_value=fake_resp) as mock_post:
        token1 = github_app.get_installation_token(42)
        token2 = github_app.get_installation_token(42)

    assert token1 == "ghs_abc" == token2
    # Two calls, one network hit.
    assert mock_post.call_count == 1


def test_get_installation_token_refreshes_when_cache_expired(github_app_env):
    from app.connectors import github_app

    # Seed cache with a token that's about to expire.
    near_expiry = int(time.time()) + 60  # inside the 5-min safety window
    github_app._install_token_cache[7] = ("stale", near_expiry)  # type: ignore[attr-defined]

    fake_resp = MagicMock(ok=True)
    fake_resp.json.return_value = {
        "token": "ghs_fresh",
        "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600)),
    }
    with patch("app.connectors.github_app.requests.post", return_value=fake_resp):
        token = github_app.get_installation_token(7)
    assert token == "ghs_fresh"


def test_clear_installation_token_cache_targeted(github_app_env):
    from app.connectors import github_app
    github_app._install_token_cache[1] = ("a", int(time.time()) + 3600)  # type: ignore[attr-defined]
    github_app._install_token_cache[2] = ("b", int(time.time()) + 3600)  # type: ignore[attr-defined]
    github_app.clear_installation_token_cache(1)
    assert 1 not in github_app._install_token_cache  # type: ignore[attr-defined]
    assert 2 in github_app._install_token_cache  # type: ignore[attr-defined]
    github_app.clear_installation_token_cache()
    assert github_app._install_token_cache == {}  # type: ignore[attr-defined]


# ─────────────────────── webhook signature gate ───────────────────────


def test_webhook_rejects_bad_signature(client):
    body = b'{"action": "created"}'
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": "sha256=deadbeef",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401


def test_webhook_ping_returns_ok(client):
    body = b'{"zen":"hi"}'
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "event": "ping"}


def test_webhook_rejects_bad_json(client):
    body = b"not-json"
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 400


# ─────────────────────── installation event ───────────────────────


def _install_payload(action: str, install_id: int = 99, login: str = "octocat") -> bytes:
    return json.dumps({
        "action": action,
        "installation": {
            "id": install_id,
            "account": {"id": 1, "login": login, "type": "User"},
            "repository_selection": "selected",
            "permissions": {"contents": "write", "pull_requests": "write"},
            "events": ["installation", "pull_request"],
        },
    }).encode("utf-8")


def test_webhook_installation_created_upserts_row(client):
    import app.db as db
    body = _install_payload("created")
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["handled"] is True
    row = db.get_github_installation(99)
    assert row is not None
    assert row["account_login"] == "octocat"
    assert row["suspended"] == 0


def test_webhook_installation_suspend_marks_suspended(client):
    import app.db as db
    # Create first.
    body = _install_payload("created")
    client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "installation", "X-Hub-Signature-256": _sign(body)},
    )
    # Then suspend.
    body = _install_payload("suspend")
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "installation", "X-Hub-Signature-256": _sign(body)},
    )
    assert r.status_code == 200
    row = db.get_github_installation(99)
    assert row["suspended"] == 1


def test_webhook_installation_deleted_clears_row_and_cache(client):
    import app.db as db
    from app.connectors import github_app
    # Seed cache + db.
    body = _install_payload("created")
    client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "installation", "X-Hub-Signature-256": _sign(body)},
    )
    github_app._install_token_cache[99] = ("ghs_x", int(time.time()) + 3600)  # type: ignore[attr-defined]

    body = _install_payload("deleted")
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "installation", "X-Hub-Signature-256": _sign(body)},
    )
    assert r.status_code == 200
    assert db.get_github_installation(99) is None
    assert 99 not in github_app._install_token_cache  # type: ignore[attr-defined]


# ─────────────────────── installation_repositories event ───────────────────────


def test_webhook_installation_repositories_updates_selection(client):
    import app.db as db
    # Seed an install with repository_selection=selected.
    body = _install_payload("created")
    client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "installation", "X-Hub-Signature-256": _sign(body)},
    )

    # Now flip to "all".
    body = json.dumps({
        "action": "added",
        "installation": {
            "id": 99,
            "account": {"id": 1, "login": "octocat", "type": "User"},
            "repository_selection": "all",
        },
        "repositories_added": [{"full_name": "octocat/repo1"}],
    }).encode("utf-8")
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "installation_repositories",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert r.status_code == 200
    row = db.get_github_installation(99)
    assert row["repository_selection"] == "all"


def test_webhook_installation_repositories_no_row_no_crash(client):
    """If we never saw the install (e.g. backend was down), gracefully ignore."""
    import app.db as db
    body = json.dumps({
        "action": "added",
        "installation": {"id": 12345, "account": {"id": 1, "login": "x", "type": "User"}},
    }).encode("utf-8")
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "installation_repositories",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert r.status_code == 200
    assert db.get_github_installation(12345) is None


# ─────────────────────── pull_request event ───────────────────────


def _pr_payload(action: str, *, number: int = 7, state: str = "open", merged: bool = False) -> bytes:
    return json.dumps({
        "action": action,
        "installation": {"id": 99},
        "repository": {"full_name": "octocat/hello"},
        "pull_request": {
            "number": number,
            "title": "Add feature",
            "state": state,
            "draft": False,
            "merged": merged,
            "user": {"login": "octocat"},
            "head": {"ref": "feature/x"},
            "base": {"ref": "main"},
            "html_url": f"https://github.com/octocat/hello/pull/{number}",
            "body": "Fixes #42",
            "created_at": "2026-05-25T10:00:00Z",
            "updated_at": "2026-05-25T11:00:00Z",
        },
    }).encode("utf-8")


def test_webhook_pull_request_opened_upserts(client):
    import app.db as db
    body = _pr_payload("opened")
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body)},
    )
    assert r.status_code == 200
    prs = db.list_open_pull_requests(99)
    assert len(prs) == 1
    assert prs[0]["repo_full_name"] == "octocat/hello"
    assert prs[0]["pr_number"] == 7
    assert prs[0]["state"] == "open"
    assert prs[0]["author_login"] == "octocat"


def test_webhook_pull_request_closed_marks_closed(client):
    import app.db as db
    client.post(
        "/v1/connectors/github/webhook",
        content=_pr_payload("opened"),
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(_pr_payload("opened"))},
    )
    closed = _pr_payload("closed", state="closed", merged=False)
    client.post(
        "/v1/connectors/github/webhook",
        content=closed,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(closed)},
    )
    assert db.list_open_pull_requests(99) == []


def test_webhook_pull_request_merged_marks_merged(client):
    import app.db as db
    merged = _pr_payload("closed", state="closed", merged=True)
    client.post(
        "/v1/connectors/github/webhook",
        content=merged,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(merged)},
    )
    # No open PRs.
    assert db.list_open_pull_requests(99) == []


def test_webhook_unknown_event_returns_ok_unhandled(client):
    body = b'{"action":"created"}'
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "star", "X-Hub-Signature-256": _sign(body)},
    )
    assert r.status_code == 200
    assert r.json()["handled"] is False


# ─────────────────────── list endpoints ───────────────────────


def test_list_installations_endpoint(github_app_env, monkeypatch):
    """Webhook → list end-to-end. The list endpoint is now tenant-isolated
    (security hotfix), so the test seeds a company with a github
    connection labelled @octocat to match the webhook payload's
    account_login."""
    from tests._company_helpers import company_client, seed_connection

    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="github",
        token_blob={"access_token": "tok"},
        label="@octocat",
    )
    body = _install_payload("created")
    ctx.client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "installation", "X-Hub-Signature-256": _sign(body)},
    )
    r = ctx.client.get("/v1/connectors/github/installations")
    assert r.status_code == 200
    out = r.json()["installations"]
    assert len(out) == 1
    assert out[0]["account_login"] == "octocat"


def test_list_open_prs_endpoint_filters_by_installation(github_app_env, monkeypatch):
    """Install 99 owned by @octocat has one open PR; install 100 (not
    even seeded, would be cross-tenant) returns 403 under the new guard.
    Was 200/empty under the global pre-hotfix shape."""
    from tests._company_helpers import company_client, seed_connection

    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="github",
        token_blob={"access_token": "tok"},
        label="@octocat",
    )
    body = _install_payload("created", install_id=99)
    ctx.client.post(
        "/v1/connectors/github/webhook", content=body,
        headers={"X-GitHub-Event": "installation", "X-Hub-Signature-256": _sign(body)},
    )

    pr = _pr_payload("opened")
    ctx.client.post(
        "/v1/connectors/github/webhook", content=pr,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(pr)},
    )

    r = ctx.client.get(
        "/v1/connectors/github/pull-requests", params={"installation_id": 99}
    )
    assert r.status_code == 200
    assert len(r.json()["pull_requests"]) == 1

    # Install 100 is not owned by this company → 403, not 200/empty.
    # Don't leak whether the install exists.
    r2 = ctx.client.get(
        "/v1/connectors/github/pull-requests", params={"installation_id": 100}
    )
    assert r2.status_code == 403


def test_list_endpoints_require_auth(github_app_env):
    """Unauthenticated callers should get 401 on the listing endpoints."""
    import app.main as main_mod
    c = TestClient(main_mod.app)
    assert c.get("/v1/connectors/github/installations").status_code == 401
    assert c.get("/v1/connectors/github/pull-requests").status_code == 401

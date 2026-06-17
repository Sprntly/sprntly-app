"""Route integration: codebase-map pre-warm on connect + push webhook.

Mirrors test_routes_connectors_github_webhook.py's fixtures. We mock the pre-warm
primitive (prewarm_installation / prewarm_map) so NO thread, network, or build runs
— these tests assert ONLY the route wiring + the feature gate:

* the push webhook fires prewarm_map for a default-branch push when the Design
  Agent flag is ON, with ref=None (build_map resolves the latest default sha)
* it does NOT fire for a feature-branch push, nor when the flag is OFF
* a prewarm failure NEVER fails the webhook response (best-effort)
* the webhook signature gate is unchanged (bad signature → 401, no pre-warm)
* the connect bind path fires prewarm_installation when bound + flag ON
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import sys

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
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", pem)
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    # Design Agent ON by default for these tests; individual tests flip it off.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    _reload_app_modules()
    import app.db as db_mod
    db_mod.init_db()
    from app.connectors import github_app
    github_app.clear_installation_token_cache()
    yield


@pytest.fixture
def client(github_app_env):
    import app.main as main_mod
    c = TestClient(main_mod.app)
    r = c.post("/v1/auth/login", json={"password": "test-pw"})
    assert r.status_code == 200
    return c


def _push_payload(
    *, repo="owner/repo", default_branch="main", ref="refs/heads/main", install_id=99
) -> bytes:
    payload: dict = {
        "repository": {"full_name": repo, "default_branch": default_branch},
        "ref": ref,
        "after": "newsha123",
    }
    if install_id is not None:
        payload["installation"] = {"id": install_id}
    return json.dumps(payload).encode("utf-8")


# ─────────────────────── push webhook pre-warm ───────────────────────


def test_push_default_branch_prewarms_new_sha(client, monkeypatch):
    """A default-branch push fires a single prewarm_map(install, repo, None) and
    still returns handled:true."""
    import app.routes.connectors as connectors_mod

    monkeypatch.setattr(connectors_mod.db, "mark_github_design_systems_stale", lambda r: 1)
    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.design_agent.codebase_map.prewarm.prewarm_map",
        lambda installation_id, repo, ref=None: calls.append((installation_id, repo, ref)),
    )

    body = _push_payload()
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": _sign(body)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["handled"] is True
    # Exactly one warm, ref=None (build_map resolves the latest default-branch sha).
    assert calls == [(99, "owner/repo", None)]


def test_push_non_default_branch_does_not_prewarm(client, monkeypatch):
    """A push to a feature branch warms nothing — /locate only ever targets the
    default branch, so a feature-branch warm would be wasted cold-build load."""
    import app.routes.connectors as connectors_mod

    monkeypatch.setattr(connectors_mod.db, "mark_github_design_systems_stale", lambda r: 1)
    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.design_agent.codebase_map.prewarm.prewarm_map",
        lambda *a, **k: calls.append((a, k)),
    )

    body = _push_payload(ref="refs/heads/feature/x")
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": _sign(body)},
    )
    assert r.status_code == 200
    assert r.json()["handled"] is True
    assert calls == []


def test_push_does_not_prewarm_when_flag_off(client, monkeypatch):
    """With DESIGN_AGENT_ENABLED off, the push webhook still works but pre-warm is
    a clean no-op (the feature is dark)."""
    import app.routes.connectors as connectors_mod

    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "0")
    monkeypatch.setattr(connectors_mod.db, "mark_github_design_systems_stale", lambda r: 1)
    monkeypatch.setattr(
        "app.design_agent.codebase_map.prewarm.prewarm_map",
        lambda *a, **k: pytest.fail("pre-warm must not run when the flag is off"),
    )

    body = _push_payload()
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": _sign(body)},
    )
    assert r.status_code == 200
    assert r.json()["handled"] is True


def test_push_prewarm_failure_does_not_fail_webhook(client, monkeypatch):
    """A pre-warm scheduling error is swallowed: the webhook still returns 200."""
    import app.routes.connectors as connectors_mod

    monkeypatch.setattr(connectors_mod.db, "mark_github_design_systems_stale", lambda r: 1)

    def _boom(*a, **k):
        raise RuntimeError("scheduler exploded")

    monkeypatch.setattr(
        "app.design_agent.codebase_map.prewarm.prewarm_map", _boom
    )

    body = _push_payload()
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": _sign(body)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["handled"] is True


def test_push_bad_signature_still_401_no_prewarm(client, monkeypatch):
    """The signature gate is unchanged: a bad signature is rejected 401 BEFORE any
    pre-warm could run."""
    monkeypatch.setattr(
        "app.design_agent.codebase_map.prewarm.prewarm_map",
        lambda *a, **k: pytest.fail("pre-warm must never run on a rejected webhook"),
    )
    body = _push_payload()
    r = client.post(
        "/v1/connectors/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert r.status_code == 401


# ─────────────────────── connect bind pre-warm ───────────────────────


def test_bind_installation_company_prewarms_when_flag_on(github_app_env, monkeypatch):
    """Binding a GitHub installation to a company (the connection-established path)
    fires prewarm_installation when the Design Agent flag is on."""
    import app.routes.connectors as connectors_mod
    from tests._company_helpers import company_client

    calls: list[int] = []
    monkeypatch.setattr(
        "app.design_agent.codebase_map.prewarm.prewarm_installation",
        lambda installation_id, *a, **k: calls.append(installation_id),
    )

    mp = pytest.MonkeyPatch()
    try:
        ctx = company_client(mp)
        connectors_mod._bind_installation_company(4242, ctx.company_id)
    finally:
        mp.undo()

    assert calls == [4242]


def test_bind_installation_company_no_prewarm_when_flag_off(github_app_env, monkeypatch):
    """With the flag off, binding still succeeds but pre-warm is a clean no-op."""
    import app.routes.connectors as connectors_mod
    from tests._company_helpers import company_client

    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "0")
    monkeypatch.setattr(
        "app.design_agent.codebase_map.prewarm.prewarm_installation",
        lambda *a, **k: pytest.fail("pre-warm must not run when the flag is off"),
    )

    mp = pytest.MonkeyPatch()
    try:
        ctx = company_client(mp)
        connectors_mod._bind_installation_company(4243, ctx.company_id)
    finally:
        mp.undo()

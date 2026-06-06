"""Tests for the `return_to` field in signed OAuth state (commit 1 of
the onboarding-connect-modals slice).

The OAuth callback runs without a user session, so anything it knows
about where to redirect the user *after* the round-trip has to ride
inside the signed state JWT. The original code hardcoded
`/settings?section=connectors` — fine when connectors were only
managed from Settings, but the onboarding flow needs callbacks to
bounce back to `/onboarding/4` instead.

`return_to` is signed into the state at `/start-oauth` time, validated
as a safe relative path (defends open-redirect), and the callback
appends `?connected=<provider>` and redirects there.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import jwt
import pytest

from app.connectors import (
    clickup_oauth,
    figma_oauth,
    github_app,
    google_oauth,
    hubspot_oauth,
    slack_oauth,
)
from app.routes.connectors import _is_safe_return_to
from tests._company_helpers import company_client


PROVIDERS = [
    ("figma", figma_oauth, figma_oauth.FIGMA_PROVIDER),
    ("github", github_app, github_app.GITHUB_PROVIDER),
    ("clickup", clickup_oauth, clickup_oauth.CLICKUP_PROVIDER),
    ("hubspot", hubspot_oauth, hubspot_oauth.HUBSPOT_PROVIDER),
    ("google", google_oauth, google_oauth.GOOGLE_DRIVE_PROVIDER),
    ("slack", slack_oauth, slack_oauth.SLACK_PROVIDER),
]


# ───────────────────────── validation helper ─────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        None,
        "/",
        "/onboarding/4",
        "/settings?section=connectors",
        "/settings?section=connectors&foo=bar",
        "/a/b/c/d",
    ],
)
def test_safe_return_to_accepts_relative_paths(value):
    assert _is_safe_return_to(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",                              # empty string is not a relative path
        "//evil.com",                    # protocol-relative — would navigate off-site
        "//evil.com/onboarding/4",
        "https://evil.com",              # absolute URL
        "http://evil.com/foo",
        "javascript:alert(1)",           # scheme injection
        "data:text/html,<script>",
        "evil.com",                      # bare host
        "onboarding/4",                  # relative without leading slash
        "\\\\evil.com",                  # backslash protocol confusion (browsers normalize \\ to /)
        "/\\evil.com",                   # mixed slash trick
        "x" * 1500,                      # absurdly long
        "/" + "a" * 2000,                # absurdly long path
    ],
)
def test_safe_return_to_rejects_unsafe_values(value):
    assert _is_safe_return_to(value) is False


# ───────────────────────── per-provider state roundtrip ─────────────────────────


@pytest.mark.parametrize("name,mod,expected_provider", PROVIDERS)
def test_state_carries_return_to_when_provided(name, mod, expected_provider, isolated_settings):
    state = mod.sign_oauth_state(company_id="co-acme", return_to="/onboarding/4")
    payload = mod.verify_oauth_state(state)
    assert payload["return_to"] == "/onboarding/4"
    assert payload["company_id"] == "co-acme"
    assert payload["provider"] == expected_provider


@pytest.mark.parametrize("name,mod,_", PROVIDERS)
def test_state_omits_return_to_when_not_provided(name, mod, _, isolated_settings):
    """Back-compat: when return_to isn't passed, state doesn't carry one
    and verify is happy. Callbacks fall back to /settings in that case."""
    state = mod.sign_oauth_state(company_id="co-acme")
    payload = mod.verify_oauth_state(state)
    # Either key is missing, or key exists but is None — both signal "no return_to".
    assert payload.get("return_to") in (None,)


# ───────────────────────── start-oauth route accepts/rejects ─────────────────────────


def _env_for_provider(name: str, monkeypatch):
    """Set the env vars each provider checks via `*_configured()`."""
    # Callback paths encrypt the token before storing — a Fernet key must be
    # present (every other connector test file sets one; relying on leakage
    # from earlier tests breaks under reordering).
    from cryptography.fernet import Fernet
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    common = {
        "CLICKUP_CLIENT_ID": "x",
        "CLICKUP_CLIENT_SECRET": "x",
        "CLICKUP_OAUTH_REDIRECT_URI": "http://t/cb",
        "FIGMA_CLIENT_ID": "x",
        "FIGMA_CLIENT_SECRET": "x",
        "FIGMA_OAUTH_REDIRECT_URI": "http://t/cb",
        "HUBSPOT_CLIENT_ID": "x",
        "HUBSPOT_CLIENT_SECRET": "x",
        "HUBSPOT_OAUTH_REDIRECT_URI": "http://t/cb",
        "GITHUB_APP_CLIENT_ID": "x",
        "GITHUB_APP_CLIENT_SECRET": "x",
        "GITHUB_OAUTH_REDIRECT_URI": "http://t/cb",
        "GOOGLE_CLIENT_ID": "x",
        "GOOGLE_CLIENT_SECRET": "x",
        "GOOGLE_OAUTH_REDIRECT_URI": "http://t/cb",
        "SLACK_CLIENT_ID": "x",
        "SLACK_CLIENT_SECRET": "x",
        "SLACK_OAUTH_REDIRECT_URI": "http://t/cb",
        "TOKEN_ENCRYPTION_KEY": "Y2FfZJj7Bug3PldyzhB4j5d4mLrZQk_RspnvJgC_yYg=",
        "FRONTEND_URL": "http://localhost:3000",
    }
    for k, v in common.items():
        monkeypatch.setenv(k, v)


def test_start_oauth_accepts_safe_return_to_and_signs_into_state(
    isolated_settings, monkeypatch,
):
    _env_for_provider("figma", monkeypatch)
    import importlib, sys
    for name in ("app.config", "app.connectors.tokens", "app.connectors.figma_oauth", "app.routes.connectors", "app.main"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])

    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/figma/start-oauth",
        json={"return_to": "/onboarding/4"},
    )
    assert r.status_code == 200, r.text
    url = r.json()["authorize_url"]
    # Extract the state= parameter from the authorize URL
    import urllib.parse as up
    parsed = up.urlparse(url)
    state = up.parse_qs(parsed.query)["state"][0]
    from app.connectors import figma_oauth as _figma
    payload = _figma.verify_oauth_state(state)
    assert payload["return_to"] == "/onboarding/4"


def test_start_oauth_rejects_unsafe_return_to(isolated_settings, monkeypatch):
    _env_for_provider("figma", monkeypatch)
    import importlib, sys
    for name in ("app.config", "app.connectors.tokens", "app.connectors.figma_oauth", "app.routes.connectors", "app.main"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])

    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/figma/start-oauth",
        json={"return_to": "https://evil.com"},
    )
    assert r.status_code == 422


def test_start_oauth_works_without_return_to(isolated_settings, monkeypatch):
    """Back-compat — Settings page calls don't pass return_to, must still work."""
    _env_for_provider("figma", monkeypatch)
    import importlib, sys
    for name in ("app.config", "app.connectors.tokens", "app.connectors.figma_oauth", "app.routes.connectors", "app.main"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])

    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/figma/start-oauth", json={})
    assert r.status_code == 200


# ───────────────────────── callback uses return_to ─────────────────────────


def test_callback_redirects_to_return_to_when_state_carries_it(
    isolated_settings, monkeypatch,
):
    """The pivotal behaviour — after OAuth completes, the user lands on
    the page they came from (not always /settings)."""
    _env_for_provider("figma", monkeypatch)
    import importlib, sys
    for name in ("app.config", "app.connectors.tokens", "app.connectors.figma_oauth", "app.routes.connectors", "app.main"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])

    ctx = company_client(monkeypatch)
    state = figma_oauth.sign_oauth_state(
        company_id=ctx.company_id, return_to="/onboarding/4",
    )
    with (
        patch(
            "app.routes.connectors.figma_oauth.exchange_code_for_token",
            return_value={"access_token": "tok", "user_id": "u1"},
        ),
        patch(
            "app.routes.connectors.figma_oauth.fetch_me",
            return_value={"email": "alice@co.test", "handle": "alice"},
        ),
    ):
        r = ctx.client.get(
            "/v1/connectors/figma/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 307, r.text
    location = r.headers["location"]
    assert "/onboarding/4" in location
    assert "connected=figma" in location


def test_callback_falls_back_to_settings_when_state_has_no_return_to(
    isolated_settings, monkeypatch,
):
    """Existing Settings page flow still works — state without
    return_to means "go to /settings"."""
    _env_for_provider("figma", monkeypatch)
    import importlib, sys
    for name in ("app.config", "app.connectors.tokens", "app.connectors.figma_oauth", "app.routes.connectors", "app.main"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])

    ctx = company_client(monkeypatch)
    state = figma_oauth.sign_oauth_state(company_id=ctx.company_id)
    with (
        patch(
            "app.routes.connectors.figma_oauth.exchange_code_for_token",
            return_value={"access_token": "tok", "user_id": "u1"},
        ),
        patch(
            "app.routes.connectors.figma_oauth.fetch_me",
            return_value={"email": "alice@co.test", "handle": "alice"},
        ),
    ):
        r = ctx.client.get(
            "/v1/connectors/figma/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 307, r.text
    location = r.headers["location"]
    assert "section=connectors" in location
    assert "connected=figma" in location

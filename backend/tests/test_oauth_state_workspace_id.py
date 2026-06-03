"""Tests proving each provider's signed OAuth state now carries the
workspace_id of the workspace that initiated the flow (commit 3).

The callback path has no Bearer token — the user is arriving back from
the provider, not from our app — so the signed state is the trust
boundary that tells the callback which workspace gets the new
connection token. Without it, a callback can't write to the right
workspace at all.
"""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi import HTTPException

from app.connectors import (
    clickup_oauth,
    figma_oauth,
    github_app,
    google_oauth,
    hubspot_oauth,
)


PROVIDERS = [
    ("figma", figma_oauth, figma_oauth.FIGMA_PROVIDER),
    ("github", github_app, github_app.GITHUB_PROVIDER),
    ("clickup", clickup_oauth, clickup_oauth.CLICKUP_PROVIDER),
    ("hubspot", hubspot_oauth, hubspot_oauth.HUBSPOT_PROVIDER),
    ("google", google_oauth, google_oauth.GOOGLE_DRIVE_PROVIDER),
]


@pytest.mark.parametrize("name,mod,expected_provider", PROVIDERS)
def test_state_roundtrip_carries_workspace_id(name, mod, expected_provider, isolated_settings):
    state = mod.sign_oauth_state(workspace_id="ws-123")
    payload = mod.verify_oauth_state(state)
    assert payload["workspace_id"] == "ws-123"
    assert payload["provider"] == expected_provider


@pytest.mark.parametrize("name,mod,_", PROVIDERS)
def test_sign_state_requires_workspace_id(name, mod, _, isolated_settings):
    """Silent defaults are how the original cross-tenant bug came back —
    the type system catches a missing workspace_id at the call site."""
    with pytest.raises(TypeError):
        mod.sign_oauth_state()  # type: ignore[call-arg]


@pytest.mark.parametrize("name,mod,expected_provider", PROVIDERS)
def test_verify_rejects_state_without_workspace_id(
    name, mod, expected_provider, isolated_settings
):
    """A state JWT correctly signed but missing workspace_id (e.g. from an
    old client or a forged payload) must be rejected, not silently
    accepted as a workspace-less callback."""
    from app.config import settings

    now = int(time.time())
    payload = {
        "provider": expected_provider,
        "nonce": "x",
        "iat": now,
        "exp": now + 600,
        # workspace_id deliberately omitted
    }
    # Match each provider's chosen alg.
    alg = getattr(mod, "JWT_ALG_STATE", None) or getattr(mod, "JWT_ALG", "HS256")
    state = jwt.encode(payload, settings.jwt_secret, algorithm=alg)
    with pytest.raises(HTTPException) as exc:
        mod.verify_oauth_state(state)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("name,mod,_", PROVIDERS)
def test_verify_rejects_expired_state(name, mod, _, isolated_settings):
    """iat/exp on the state JWT enforce age. Replay > 10 min after mint is
    rejected by PyJWT's `exp` check during verify."""
    from app.config import settings

    state = mod.sign_oauth_state(workspace_id="ws-x")
    # Decode without verification, set exp to the past, re-sign.
    payload = jwt.decode(state, options={"verify_signature": False})
    payload["exp"] = int(time.time()) - 1
    alg = getattr(mod, "JWT_ALG_STATE", None) or getattr(mod, "JWT_ALG", "HS256")
    expired = jwt.encode(payload, settings.jwt_secret, algorithm=alg)
    with pytest.raises(HTTPException) as exc:
        mod.verify_oauth_state(expired)
    assert exc.value.status_code == 400

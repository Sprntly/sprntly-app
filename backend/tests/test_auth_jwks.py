"""Tests for _decode_supabase_token under both HS256 and JWKS paths (commit G).

The Sprntly backend originally only verified Supabase session tokens via a
shared HS256 secret (`SUPABASE_JWT_SECRET`). Supabase has been migrating
projects to asymmetric signing keys (ES256/ECC) — the secret for those is
intentionally unrevealable in the dashboard, so the only verification path
is the project's public JWKS endpoint:

    GET https://<project>.supabase.co/auth/v1/.well-known/jwks.json

These tests assert the function dispatches correctly on the JWT's `alg`
header: HS256 keeps the legacy shared-secret path, ES256 fetches the
public key from JWKS. Adding new ECC-signed tokens does not regress any
existing HS256 behavior.
"""
from __future__ import annotations

import importlib
import sys
import time
from unittest.mock import MagicMock, patch

import jwt
import pytest


def _reload_app_modules():
    for name in (
        "app.config",
        "app.auth",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


def _ec_keypair():
    """Generate an ES256 keypair for tests (no network, no Supabase needed)."""
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    return private_key, public_key


# ─────────────────────── HS256 (legacy) path ───────────────────────


def test_hs256_token_still_verifies_with_shared_secret(isolated_settings, monkeypatch):
    """Regression — HS256 path (the only path before commit G) keeps working."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "shared-hs256-test-secret")
    _reload_app_modules()

    from app.auth import _decode_supabase_token

    token = jwt.encode(
        {
            "sub": "user-hs",
            "aud": "authenticated",
            "exp": int(time.time()) + 300,
        },
        "shared-hs256-test-secret",
        algorithm="HS256",
    )
    payload = _decode_supabase_token(token)
    assert payload["sub"] == "user-hs"


def test_hs256_token_with_wrong_secret_is_rejected(isolated_settings, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "wrong-secret")
    _reload_app_modules()

    from app.auth import _decode_supabase_token

    token = jwt.encode(
        {"sub": "u", "aud": "authenticated", "exp": int(time.time()) + 300},
        "actual-secret",
        algorithm="HS256",
    )
    with pytest.raises(jwt.PyJWTError):
        _decode_supabase_token(token)


def test_hs256_path_raises_when_secret_missing(isolated_settings, monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    _reload_app_modules()

    from app.auth import _decode_supabase_token

    token = jwt.encode(
        {"sub": "u", "aud": "authenticated", "exp": int(time.time()) + 300},
        "anything",
        algorithm="HS256",
    )
    with pytest.raises(jwt.PyJWTError):
        _decode_supabase_token(token)


# ─────────────────────── ES256 (modern, JWKS) path ───────────────────────


def test_es256_token_verifies_via_jwks(isolated_settings, monkeypatch):
    """ES256-signed token gets verified by fetching the public key from JWKS."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    _reload_app_modules()

    private_key, public_key = _ec_keypair()
    token = jwt.encode(
        {
            "sub": "user-es",
            "aud": "authenticated",
            "exp": int(time.time()) + 300,
        },
        private_key,
        algorithm="ES256",
        headers={"kid": "test-kid"},
    )

    fake_signing_key = MagicMock()
    fake_signing_key.key = public_key

    fake_client = MagicMock()
    fake_client.get_signing_key_from_jwt.return_value = fake_signing_key

    import app.auth as auth_mod
    monkeypatch.setattr(auth_mod, "_get_jwks_client", lambda: fake_client)

    payload = auth_mod._decode_supabase_token(token)
    assert payload["sub"] == "user-es"
    fake_client.get_signing_key_from_jwt.assert_called_once_with(token)


def test_es256_token_with_wrong_key_is_rejected(isolated_settings, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    _reload_app_modules()

    real_private, _ = _ec_keypair()
    _, wrong_public = _ec_keypair()  # a DIFFERENT keypair

    token = jwt.encode(
        {"sub": "u", "aud": "authenticated", "exp": int(time.time()) + 300},
        real_private,
        algorithm="ES256",
    )

    fake_signing_key = MagicMock()
    fake_signing_key.key = wrong_public

    fake_client = MagicMock()
    fake_client.get_signing_key_from_jwt.return_value = fake_signing_key

    import app.auth as auth_mod
    monkeypatch.setattr(auth_mod, "_get_jwks_client", lambda: fake_client)

    with pytest.raises(jwt.PyJWTError):
        auth_mod._decode_supabase_token(token)


def test_es256_path_raises_when_supabase_url_missing(isolated_settings, monkeypatch):
    """Without SUPABASE_URL the JWKS endpoint can't be built — surface as PyJWTError."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "")
    _reload_app_modules()

    private_key, _ = _ec_keypair()
    token = jwt.encode(
        {"sub": "u", "aud": "authenticated", "exp": int(time.time()) + 300},
        private_key,
        algorithm="ES256",
    )

    from app.auth import _decode_supabase_token

    with pytest.raises(jwt.PyJWTError):
        _decode_supabase_token(token)


# ─────────────────────── Unsupported / malformed ───────────────────────


def test_unsupported_algorithm_is_rejected(isolated_settings, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "x")
    _reload_app_modules()

    from app.auth import _decode_supabase_token

    # HS512 is a valid JWT algorithm but we don't accept it (only HS256 and
    # the asymmetric ES256/RS256 family).
    token = jwt.encode(
        {"sub": "u", "aud": "authenticated", "exp": int(time.time()) + 300},
        "secret-bytes-long-enough-for-hs512-xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        algorithm="HS512",
    )
    with pytest.raises(jwt.PyJWTError):
        _decode_supabase_token(token)


def test_malformed_token_is_rejected(isolated_settings, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "x")
    _reload_app_modules()

    from app.auth import _decode_supabase_token

    with pytest.raises(jwt.PyJWTError):
        _decode_supabase_token("not-actually-a-jwt-at-all")


# ─────────────────────── Bearer round-trip via require_session ───────────────────────


def test_require_session_accepts_es256_bearer_via_jwks(
    isolated_settings, monkeypatch
):
    """End-to-end: ES256 Bearer token → require_session → returns payload."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    _reload_app_modules()

    private_key, public_key = _ec_keypair()
    token = jwt.encode(
        {
            "sub": "user-e2e",
            "aud": "authenticated",
            "exp": int(time.time()) + 300,
        },
        private_key,
        algorithm="ES256",
    )

    fake_signing_key = MagicMock()
    fake_signing_key.key = public_key
    fake_client = MagicMock()
    fake_client.get_signing_key_from_jwt.return_value = fake_signing_key

    import app.auth as auth_mod
    monkeypatch.setattr(auth_mod, "_get_jwks_client", lambda: fake_client)

    payload = auth_mod.require_session(
        authorization=f"Bearer {token}",
        sprntly_app_session=None,
        sprntly_demo_session=None,
    )
    assert payload["sub"] == "user-e2e"
    assert payload["aud"] == "supabase"

"""Defensive tests for Settings.github_app_private_key_pem.

Background: systemd's EnvironmentFile= parser handles unquoted values
DIFFERENTLY than pydantic-settings. In particular, systemd in unquoted
mode can strip backslashes from values like `\\n` — leaving the running
process with a PEM that has `n` characters where newlines should be.
Pydantic then has nothing to replace, the PEM stays invalid, and PyJWT
raises "Could not parse the provided public key."

To make the property tolerant of how the .env value is formatted,
github_app_private_key_pem should:
  - Strip surrounding quotes (both single and double)
  - Normalise literal `\\n` to real newlines (idempotent if already real)
  - Return whatever the caller gave us if it's already a valid PEM
"""
from __future__ import annotations

import importlib
import sys

import pytest


@pytest.fixture
def fresh_settings(monkeypatch):
    """Return a freshly-loaded Settings each time so env-var changes
    are picked up by pydantic-settings."""
    def _make(value: str):
        monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", value)
        if "app.config" in sys.modules:
            importlib.reload(sys.modules["app.config"])
        else:
            importlib.import_module("app.config")
        from app.config import settings
        return settings

    return _make


# ─────────────────────── unquoted + literal \n ───────────────────────


def test_unquoted_literal_escaped_newlines_normalize(fresh_settings):
    """The legacy shape — value with no quotes, `\\n` written literally."""
    s = fresh_settings("-----BEGIN PRIVATE KEY-----\\nBODY\\n-----END PRIVATE KEY-----\\n")
    pem = s.github_app_private_key_pem
    assert "\\n" not in pem
    assert pem.startswith("-----BEGIN PRIVATE KEY-----\n")
    assert "\nBODY\n" in pem


# ─────────────────────── quoted variants ───────────────────────


def test_double_quoted_value_strips_outer_quotes(fresh_settings):
    """Pydantic-settings + python-dotenv usually strip outer double
    quotes, but if a .env loader leaves them in, the property should
    still produce a clean PEM."""
    s = fresh_settings('"-----BEGIN PRIVATE KEY-----\\nBODY\\n-----END PRIVATE KEY-----\\n"')
    pem = s.github_app_private_key_pem
    assert not pem.startswith('"')
    assert not pem.endswith('"')
    assert pem.startswith("-----BEGIN PRIVATE KEY-----\n")


def test_single_quoted_value_strips_outer_quotes(fresh_settings):
    s = fresh_settings("'-----BEGIN PRIVATE KEY-----\\nBODY\\n-----END PRIVATE KEY-----\\n'")
    pem = s.github_app_private_key_pem
    assert not pem.startswith("'")
    assert not pem.endswith("'")
    assert pem.startswith("-----BEGIN PRIVATE KEY-----\n")


# ─────────────────────── already-valid PEM (real newlines) ───────────────────────


def test_real_newlines_passthrough_idempotent(fresh_settings):
    """When the env supplies a PEM with actual newlines (no `\\n` escapes
    to normalise), the property should pass it through unchanged."""
    raw = "-----BEGIN PRIVATE KEY-----\nBODY\n-----END PRIVATE KEY-----\n"
    s = fresh_settings(raw)
    pem = s.github_app_private_key_pem
    assert pem == raw


# ─────────────────────── mismatched / lone quote (do NOT strip) ───────────────────────


def test_mismatched_outer_quotes_left_intact(fresh_settings):
    """If first/last chars differ (e.g. only a leading quote), don't
    strip — that's not a balanced quote and stripping would mangle
    real content."""
    s = fresh_settings('"-----BEGIN PRIVATE KEY-----\\nBODY')
    pem = s.github_app_private_key_pem
    # The leading quote stays because there's no matching trailing one.
    assert pem.startswith('"')


# ─────────────────────── empty / unset ───────────────────────


def test_empty_value_returns_empty_string(fresh_settings):
    s = fresh_settings("")
    assert s.github_app_private_key_pem == ""


def test_empty_string_returns_empty_string(fresh_settings):
    """Explicit empty value short-circuits cleanly (no AttributeError on
    .strip / slicing)."""
    s = fresh_settings("")
    assert s.github_app_private_key_pem == ""


# ─────────────────────── whitespace tolerance ───────────────────────


def test_leading_trailing_whitespace_stripped(fresh_settings):
    """Trim before quote-detection so `  "key"  ` still has its quotes
    found and stripped."""
    s = fresh_settings('  "-----BEGIN PRIVATE KEY-----\\nBODY\\n-----END PRIVATE KEY-----\\n"  ')
    pem = s.github_app_private_key_pem
    assert pem.startswith("-----BEGIN PRIVATE KEY-----")
    assert not pem.startswith('"')


# ─────────────────────── end-to-end: signs a JWT ───────────────────────


def test_normalized_pem_signs_with_pyjwt(fresh_settings):
    """The whole point — verify a normalized PEM is actually usable by
    PyJWT. Generate a fresh RSA key, write it into the env in the
    systemd-stripped shape (literal `\\n`), confirm the property
    produces a PEM that PyJWT can sign with."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import jwt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pem_str = pem_bytes.decode()
    # Escape into the .env shape (literal `\\n`), as systemd would
    # supply it after the quote-wrapped fix.
    s = fresh_settings(pem_str.replace("\n", "\\n"))

    normalized = s.github_app_private_key_pem
    token = jwt.encode({"iss": "test"}, normalized, algorithm="RS256")
    assert isinstance(token, str)
    assert token.count(".") == 2


def test_normalized_pem_signs_when_env_is_quoted(fresh_settings):
    """Same as above but the env value is wrapped in quotes — must still
    produce a signable PEM after quote stripping + normalisation."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import jwt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_str = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    quoted_escaped = '"' + pem_str.replace("\n", "\\n") + '"'
    s = fresh_settings(quoted_escaped)

    normalized = s.github_app_private_key_pem
    token = jwt.encode({"iss": "test"}, normalized, algorithm="RS256")
    assert isinstance(token, str)
    assert token.count(".") == 2

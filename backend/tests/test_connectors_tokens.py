"""Tests for encrypted OAuth token storage."""
import importlib
import sys

import pytest

from app.connectors.tokens import TokenEncryptionError


def test_encrypt_decrypt_roundtrip(isolated_settings, monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    importlib.reload(sys.modules["app.config"])
    importlib.reload(sys.modules["app.connectors.tokens"])
    from app.connectors import tokens as tokens_mod

    plain = '{"refresh_token":"abc","token":"xyz"}'
    cipher = tokens_mod.encrypt_token_json(plain)
    assert tokens_mod.decrypt_token_json(cipher) == plain


def test_encrypt_requires_key(isolated_settings, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "")
    importlib.reload(sys.modules["app.config"])
    importlib.reload(sys.modules["app.connectors.tokens"])
    from app.connectors import tokens as tokens_mod

    with pytest.raises(tokens_mod.TokenEncryptionError):
        tokens_mod.encrypt_token_json('{"a":1}')

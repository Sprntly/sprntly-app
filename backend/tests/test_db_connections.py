"""Tests for connections table helpers."""
import importlib
import sys

from cryptography.fernet import Fernet

from app import db
from app.connectors.tokens import decrypt_token_json, encrypt_token_json


def test_upsert_and_get_connection(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    importlib.reload(sys.modules["app.config"])
    importlib.reload(sys.modules["app.connectors.tokens"])

    cipher = encrypt_token_json('{"token":"t"}')
    row = db.upsert_connection(
        provider="google_drive",
        token_encrypted=cipher,
        scopes="drive.readonly",
        google_email="user@example.com",
        config_json='{"dataset":"acme"}',
    )
    assert row["provider"] == "google_drive"
    assert row["google_email"] == "user@example.com"

    loaded = db.get_connection("google_drive")
    assert loaded is not None
    assert decrypt_token_json(loaded["token_json_encrypted"]) == '{"token":"t"}'

    assert len(db.list_connections()) == 1

    db.update_connection_sync("google_drive", last_sync_error=None)
    again = db.get_connection("google_drive")
    assert again["last_sync_at"] is not None

    assert db.delete_connection("google_drive")
    assert db.get_connection("google_drive") is None

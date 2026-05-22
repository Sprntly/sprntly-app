"""Tests for Google Drive folder sync (mocked Drive API)."""
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from app import db
from app.connectors import google_oauth
from app.connectors.google_drive_sync import (
    SyncConfigError,
    drive_http_error_message,
    parse_folder_id,
    sync_google_drive,
)
from app.connectors.tokens import encrypt_token_json


@pytest.fixture
def drive_connected(isolated_settings, monkeypatch):
    import importlib
    import sys

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    importlib.reload(sys.modules["app.config"])
    importlib.reload(sys.modules["app.connectors.tokens"])
    from app.connectors.tokens import encrypt_token_json as enc

    token = enc(
        '{"token":"t","refresh_token":"r","token_uri":"https://oauth2.googleapis.com/token",'
        '"client_id":"c","client_secret":"s"}'
    )
    db.insert_dataset(slug="acme", display_name="Acme")
    db.upsert_connection(
        provider=google_oauth.GOOGLE_DRIVE_PROVIDER,
        token_encrypted=token,
        scopes=google_oauth.DRIVE_READONLY_SCOPE,
        config_json='{"dataset":"acme","folder_id":"folder123"}',
    )
    from app import datasets as datasets_mod

    datasets_mod.create_dataset  # ensure module loaded
    (isolated_settings["data_dir"] / "acme" / "raw").mkdir(parents=True, exist_ok=True)


def test_drive_http_error_access_not_configured():
    err = MagicMock()
    err.resp = MagicMock(status=403)
    err.content = (
        b'{"error":{"message":"Drive API disabled","errors":[{"reason":"accessNotConfigured"}]}}'
    )
    msg = drive_http_error_message(err)
    assert "not enabled" in msg.lower()


def test_parse_folder_id_accepts_url():
    fid = "abcXYZ_12folder99"
    assert (
        parse_folder_id(f"https://drive.google.com/drive/folders/{fid}") == fid
    )


def test_browse_folders_mock(drive_connected):
    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {
        "files": [{"id": "sub1", "name": "Product"}]
    }
    with (
        patch(
            "app.connectors.google_drive_sync.build_drive_service",
            return_value=mock_service,
        ),
        patch(
            "app.connectors.google_drive_sync._refresh_credentials",
            return_value=MagicMock(),
        ),
    ):
        out = __import__(
            "app.connectors.google_drive_sync", fromlist=["browse_folders"]
        ).browse_folders("root")
    assert out["current"]["id"] == "root"
    assert out["folders"][0]["name"] == "Product"


def test_sync_requires_connection(isolated_settings):
    with pytest.raises(SyncConfigError, match="not connected"):
        sync_google_drive(dataset="acme", folder_id="folder123")


def test_sync_ingests_and_skips_unchanged(drive_connected):
    file_meta = {
        "id": "file1",
        "name": "notes.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-05-20T12:00:00.000Z",
        "size": "12",
    }
    patches = (
        patch(
            "app.connectors.google_drive_sync.build_drive_service",
            return_value=MagicMock(),
        ),
        patch(
            "app.connectors.google_drive_sync.list_folder_files",
            return_value=[file_meta],
        ),
        patch(
            "app.connectors.google_drive_sync.download_file_content",
            return_value=("notes.txt", b"hello from drive"),
        ),
        patch(
            "app.connectors.google_drive_sync._refresh_credentials",
            return_value=MagicMock(),
        ),
    )
    for p in patches:
        p.start()
    try:
        result = sync_google_drive()
        assert result.dataset == "acme"
        assert len(result.synced) == 1
        assert result.synced[0]["md_chars"] > 0

        result2 = sync_google_drive()
        assert len(result2.synced) == 0
        assert result2.skipped[0]["reason"] == "unchanged"
    finally:
        for p in patches:
            p.stop()

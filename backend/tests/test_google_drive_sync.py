"""Tests for Google Drive picked-file sync (mocked Drive API).

Under the drive.file scope there is no folder browsing — the Picker frontend
hands us explicit file IDs which we store in config["files"] and sync.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from app import db
from app.connectors import google_oauth
from app.connectors.google_drive_sync import (
    SyncConfigError,
    drive_http_error_message,
    normalize_picked_files,
    sync_google_drive,
)
from app.db.client import require_client


def _seed_company(slug: str) -> str:
    wsid = uuid.uuid4().hex
    require_client().table("companies").insert(
        {"id": wsid, "slug": slug, "display_name": slug.title()}
    ).execute()
    return wsid


@pytest.fixture
def drive_connected(isolated_settings, monkeypatch):
    """Set up a workspace + connected Drive row + dataset for the sync flow.
    Returns the company_id so tests can scope calls correctly. The connection
    config seeds two picked files (the Picker's output)."""
    import importlib
    import sys

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    importlib.reload(sys.modules["app.config"])
    importlib.reload(sys.modules["app.connectors.tokens"])
    from app.connectors.tokens import encrypt_token_json as enc

    company_id = _seed_company("acme")
    token = enc(
        '{"token":"t","refresh_token":"r","token_uri":"https://oauth2.googleapis.com/token",'
        '"client_id":"c","client_secret":"s"}'
    )
    db.insert_dataset(slug="acme", display_name="Acme")
    db.upsert_connection(
        company_id=company_id,
        provider=google_oauth.GOOGLE_DRIVE_PROVIDER,
        token_encrypted=token,
        scopes=google_oauth.DRIVE_FILE_SCOPE,
        config_json='{"dataset":"acme","files":[{"id":"file0001aa","name":"notes.txt"}]}',
    )
    (isolated_settings["data_dir"] / "acme" / "raw").mkdir(parents=True, exist_ok=True)
    return company_id


@pytest.fixture(autouse=True)
def kg_kickoff(monkeypatch):
    """Stub the async KG extraction kick so sync tests never spawn a real
    extraction thread (LLM calls). Records the docs each call received."""
    calls: list[list] = []

    def _fake_kickoff(company_id, docs):
        calls.append(list(docs))
        return True

    monkeypatch.setattr(
        "app.kg_ingest.drive_extract.kickoff_drive_extract", _fake_kickoff
    )
    return calls


def test_drive_http_error_access_not_configured():
    err = MagicMock()
    err.resp = MagicMock(status=403)
    err.content = (
        b'{"error":{"message":"Drive API disabled","errors":[{"reason":"accessNotConfigured"}]}}'
    )
    msg = drive_http_error_message(err)
    assert "not enabled" in msg.lower()


def test_normalize_picked_files_validates_and_dedupes():
    out = normalize_picked_files(
        [
            {"id": "abcdEFGH12", "name": "Plan"},
            {"id": "abcdEFGH12", "name": "Plan v2"},  # dupe -> last wins
            {"id": "zzzz9999xx"},  # no name
        ]
    )
    assert out == [
        {"id": "abcdEFGH12", "name": "Plan v2"},
        {"id": "zzzz9999xx", "name": None},
    ]


def test_normalize_picked_files_empty_is_empty_list():
    assert normalize_picked_files(None) == []
    assert normalize_picked_files([]) == []


def test_normalize_picked_files_rejects_bad_id():
    with pytest.raises(SyncConfigError, match="invalid Drive file id"):
        normalize_picked_files([{"id": "bad id!"}])
    with pytest.raises(SyncConfigError, match="must have an id"):
        normalize_picked_files([{"name": "no id"}])


def test_sync_requires_connection(isolated_settings):
    company_id = _seed_company("acme")
    with pytest.raises(SyncConfigError, match="not connected"):
        sync_google_drive(company_id=company_id, dataset="acme")


def test_sync_no_op_on_empty_picked_files(drive_connected):
    """An empty picked-file list is a graceful no-op, not an error."""
    company_id = drive_connected
    with (
        patch(
            "app.connectors.google_drive_sync.build_drive_service",
            return_value=MagicMock(),
        ) as mock_build,
        patch(
            "app.connectors.google_drive_sync._refresh_credentials",
            return_value=MagicMock(),
        ),
    ):
        result = sync_google_drive(company_id=company_id, files=[])
    assert result.dataset == "acme"
    assert result.synced == []
    assert result.skipped == []
    assert result.errors == []
    # Never even built the Drive service for an empty pick.
    mock_build.assert_not_called()


def test_sync_downloads_and_ingests_each_picked_file(drive_connected, kg_kickoff):
    company_id = drive_connected
    file_meta = {
        "id": "file0001aa",
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
            "app.connectors.google_drive_sync.get_file_metadata",
            return_value=file_meta,
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
        result = sync_google_drive(company_id=company_id)
        assert result.dataset == "acme"
        assert len(result.synced) == 1
        assert result.synced[0]["md_chars"] > 0
        # The changed file was handed to the KG extractor (async).
        assert result.kg_queued == ["notes"]
        assert len(kg_kickoff) == 1
        assert kg_kickoff[0][0].file_id == "file0001aa"
        assert "hello from drive" in kg_kickoff[0][0].text

        # Second run: corpus copy is fresh, but extraction (stubbed) never
        # advanced kg_file_mtime — the file is re-queued for the KG without a
        # duplicate corpus write.
        result2 = sync_google_drive(company_id=company_id)
        assert len(result2.synced) == 0
        assert result2.kg_queued == ["notes"]
        assert len(kg_kickoff) == 2

        # Simulate a completed extraction (what _record_kg_result does): with
        # both ledgers fresh, the third run skips the file entirely.
        import json as _json

        row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
        cfg = _json.loads(row["config_json"])
        cfg["kg_file_mtime"] = dict(cfg["file_mtime"])
        db.patch_connection_config(
            company_id, google_oauth.GOOGLE_DRIVE_PROVIDER, cfg
        )
        result3 = sync_google_drive(company_id=company_id)
        assert len(result3.synced) == 0
        assert result3.kg_queued == []
        assert result3.skipped[0]["reason"] == "unchanged"
        assert len(kg_kickoff) == 2
    finally:
        for p in patches:
            p.stop()


def test_sync_stores_picked_files_passed_in(drive_connected):
    """Passing files= overwrites the stored picked-file list, then syncs them."""
    company_id = drive_connected
    metas = {
        "newfile01": {
            "id": "newfile01",
            "name": "spec.txt",
            "mimeType": "text/plain",
            "modifiedTime": "2026-06-01T00:00:00.000Z",
            "size": "5",
        },
    }
    patches = (
        patch(
            "app.connectors.google_drive_sync.build_drive_service",
            return_value=MagicMock(),
        ),
        patch(
            "app.connectors.google_drive_sync.get_file_metadata",
            side_effect=lambda service, fid: metas[fid],
        ),
        patch(
            "app.connectors.google_drive_sync.download_file_content",
            return_value=("spec.txt", b"hello"),
        ),
        patch(
            "app.connectors.google_drive_sync._refresh_credentials",
            return_value=MagicMock(),
        ),
    )
    for p in patches:
        p.start()
    try:
        result = sync_google_drive(
            company_id=company_id,
            files=[{"id": "newfile01", "name": "spec.txt"}],
        )
        assert len(result.synced) == 1
    finally:
        for p in patches:
            p.stop()

    import json

    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    cfg = json.loads(row["config_json"])
    assert cfg["files"] == [{"id": "newfile01", "name": "spec.txt"}]

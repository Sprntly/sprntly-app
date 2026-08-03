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
    MAX_SYNC_BYTES,
    SyncConfigError,
    SyncResult,
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


def test_first_kg_sync_grandfathers_preexisting_files(drive_connected, kg_kickoff):
    """A connection that synced before KG ingest shipped (corpus file_mtime
    exists, no kg_file_mtime ledger) adopts the corpus mtimes on its first
    KG-aware sync instead of re-extracting every file into near-duplicate
    signals. Files edited AFTER that still reach the KG."""
    import json as _json

    company_id = drive_connected
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    cfg = _json.loads(row["config_json"])
    cfg["file_mtime"] = {"file0001aa": "2026-05-20T12:00:00.000Z"}
    db.patch_connection_config(
        company_id, google_oauth.GOOGLE_DRIVE_PROVIDER, cfg
    )

    meta = {
        "id": "file0001aa",
        "name": "notes.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-05-20T12:00:00.000Z",
        "size": "12",
    }
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              side_effect=lambda service, fid: dict(meta)),
        patch("app.connectors.google_drive_sync.download_file_content",
              return_value=("notes.txt", b"edited content")),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        # First KG-aware sync: unchanged file is grandfathered, NOT extracted.
        result = sync_google_drive(company_id=company_id)
        assert result.skipped[0]["reason"] == "unchanged"
        assert result.kg_queued == []
        assert kg_kickoff == []
        row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
        cfg = _json.loads(row["config_json"])
        assert cfg["kg_file_mtime"] == {"file0001aa": "2026-05-20T12:00:00.000Z"}

        # The file is edited later: both corpus + KG pick up the new version.
        meta["modifiedTime"] = "2026-06-01T00:00:00.000Z"
        result2 = sync_google_drive(company_id=company_id)
        assert len(result2.synced) == 1
        assert result2.kg_queued == ["notes"]
        assert len(kg_kickoff) == 1
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


# ─── Fail-loud: a picked item that can't be ingested is never a silent skip ──


def _folder_meta(name: str = "Xometry", modified: str = "2026-05-20T12:00:00.000Z") -> dict:
    return {
        "id": "folder0001",
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "modifiedTime": modified,
    }


def test_picked_folder_reports_error_not_silent_success(drive_connected, kg_kickoff):
    """RED on unfixed code: the folder lands in `skipped` with `errors == []`."""
    company_id = drive_connected
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=_folder_meta()),
        patch("app.connectors.google_drive_sync.download_file_content",
              new=MagicMock()),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        result = sync_google_drive(company_id=company_id)
    finally:
        for p in patches:
            p.stop()
    assert len(result.errors) == 1
    assert "is a folder" in result.errors[0]["error"]
    assert "Xometry" in result.errors[0]["error"]
    assert result.synced == []
    assert result.skipped == []


def test_picked_folder_sets_last_sync_error(drive_connected, kg_kickoff):
    """RED on unfixed code: last_sync_error stays None for a picked folder."""
    company_id = drive_connected
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=_folder_meta()),
        patch("app.connectors.google_drive_sync.download_file_content",
              new=MagicMock()),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        sync_google_drive(company_id=company_id)
    finally:
        for p in patches:
            p.stop()
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    err = row.get("last_sync_error")
    assert err is not None
    assert "Xometry" in err
    assert "is a folder" in err
    assert err != "1 file(s) failed"


def test_picked_folder_never_attempts_download(drive_connected, kg_kickoff):
    company_id = drive_connected
    download_mock = MagicMock()
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=_folder_meta()),
        patch("app.connectors.google_drive_sync.download_file_content",
              new=download_mock),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        sync_google_drive(company_id=company_id)
    finally:
        for p in patches:
            p.stop()
    download_mock.assert_not_called()


def test_unsupported_type_is_error_not_skip(drive_connected, kg_kickoff):
    company_id = drive_connected
    meta = {
        "id": "file0001aa",
        "name": "archive.zip",
        "mimeType": "application/zip",
        "modifiedTime": "2026-05-20T12:00:00.000Z",
        "size": "5",
    }
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=meta),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        result = sync_google_drive(company_id=company_id)
    finally:
        for p in patches:
            p.stop()
    assert result.skipped == []
    assert len(result.errors) == 1
    assert "Unsupported file type" in result.errors[0]["error"]
    assert "application/zip" in result.errors[0]["error"]


def test_oversize_file_is_error_not_skip(drive_connected, kg_kickoff):
    company_id = drive_connected
    meta = {
        "id": "file0001aa",
        "name": "huge.pdf",
        "mimeType": "application/pdf",
        "modifiedTime": "2026-05-20T12:00:00.000Z",
        "size": str(MAX_SYNC_BYTES + 1),
    }
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=meta),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        result = sync_google_drive(company_id=company_id)
    finally:
        for p in patches:
            p.stop()
    assert len(result.errors) == 1
    assert "20MB" in result.errors[0]["error"]


def test_last_sync_error_names_first_failure_and_counts_rest(drive_connected, kg_kickoff):
    company_id = drive_connected
    metas = {
        "badtype01": {
            "id": "badtype01", "name": "archive.zip", "mimeType": "application/zip",
            "modifiedTime": "2026-05-20T12:00:00.000Z", "size": "5",
        },
        "badtype02": {
            "id": "badtype02", "name": "video.mov", "mimeType": "video/quicktime",
            "modifiedTime": "2026-05-20T12:00:00.000Z", "size": "5",
        },
    }
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              side_effect=lambda service, fid: metas[fid]),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        result = sync_google_drive(
            company_id=company_id,
            files=[
                {"id": "badtype01", "name": "archive.zip"},
                {"id": "badtype02", "name": "video.mov"},
            ],
        )
    finally:
        for p in patches:
            p.stop()
    assert len(result.errors) == 2
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    first = result.errors[0]
    expected = f"{first['name']}: {first['error']} (+1 more)"[:500]
    assert row["last_sync_error"] == expected
    assert len(row["last_sync_error"]) <= 500


def test_kg_kickoff_false_reports_error(drive_connected, monkeypatch):
    company_id = drive_connected
    monkeypatch.setattr(
        "app.kg_ingest.drive_extract.kickoff_drive_extract",
        lambda company_id, docs: False,
    )
    file_meta = {
        "id": "file0001aa",
        "name": "notes.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-05-20T12:00:00.000Z",
        "size": "12",
    }
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=file_meta),
        patch("app.connectors.google_drive_sync.download_file_content",
              return_value=("notes.txt", b"hello from drive")),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        result = sync_google_drive(company_id=company_id)
    finally:
        for p in patches:
            p.stop()
    kg_errors = [e for e in result.errors if e["name"] == "knowledge graph"]
    assert len(kg_errors) == 1
    assert "extraction didn't start" in kg_errors[0]["error"]
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    assert row["last_sync_error"] is not None
    assert "extraction didn't start" in row["last_sync_error"]


def test_kg_kickoff_raise_does_not_break_sync(drive_connected, monkeypatch):
    company_id = drive_connected

    def _raise(company_id, docs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.kg_ingest.drive_extract.kickoff_drive_extract", _raise
    )
    file_meta = {
        "id": "file0001aa",
        "name": "notes.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-05-20T12:00:00.000Z",
        "size": "12",
    }
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=file_meta),
        patch("app.connectors.google_drive_sync.download_file_content",
              return_value=("notes.txt", b"hello from drive")),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        result = sync_google_drive(company_id=company_id)  # must not raise
    finally:
        for p in patches:
            p.stop()
    assert isinstance(result, SyncResult)
    kg_errors = [e for e in result.errors if e["name"] == "knowledge graph"]
    assert len(kg_errors) == 1
    assert "extraction didn't start" in kg_errors[0]["error"]


def test_folder_with_matching_mtime_still_errors(drive_connected, kg_kickoff):
    """RED on unfixed code: proves the folder guard precedes the freshness
    short-circuit — a folder with a matching cached mtime must still error."""
    company_id = drive_connected
    import json as _json

    modified = "2026-05-20T12:00:00.000Z"
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    cfg = _json.loads(row["config_json"])
    cfg["file_mtime"] = {"folder0001": modified}
    cfg["kg_file_mtime"] = {"folder0001": modified}
    db.patch_connection_config(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER, cfg)

    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=_folder_meta(modified=modified)),
        patch("app.connectors.google_drive_sync.download_file_content",
              new=MagicMock()),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        result = sync_google_drive(
            company_id=company_id,
            files=[{"id": "folder0001", "name": "Xometry"}],
        )
    finally:
        for p in patches:
            p.stop()
    assert len(result.errors) == 1
    assert "is a folder" in result.errors[0]["error"]
    assert result.skipped == []


def test_skipped_only_ever_carries_unchanged(drive_connected, kg_kickoff):
    company_id = drive_connected
    import json as _json

    modified = "2026-05-20T12:00:00.000Z"
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    cfg = _json.loads(row["config_json"])
    cfg["file_mtime"] = {"file0001aa": modified}
    cfg["kg_file_mtime"] = {"file0001aa": modified}
    cfg["files"] = [
        {"id": "file0001aa", "name": "notes.txt"},
        {"id": "badtype01", "name": "archive.zip"},
    ]
    db.patch_connection_config(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER, cfg)

    metas = {
        "file0001aa": {
            "id": "file0001aa", "name": "notes.txt", "mimeType": "text/plain",
            "modifiedTime": modified, "size": "12",
        },
        "badtype01": {
            "id": "badtype01", "name": "archive.zip", "mimeType": "application/zip",
            "modifiedTime": "2026-06-01T00:00:00.000Z", "size": "5",
        },
    }
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              side_effect=lambda service, fid: metas[fid]),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        result = sync_google_drive(company_id=company_id)
    finally:
        for p in patches:
            p.stop()
    assert {s["reason"] for s in result.skipped} == {"unchanged"}
    assert any(
        e["name"] == "archive.zip" and "Unsupported file type" in e["error"]
        for e in result.errors
    )


def test_all_success_leaves_last_sync_error_none(drive_connected, kg_kickoff):
    company_id = drive_connected
    file_meta = {
        "id": "file0001aa",
        "name": "notes.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-05-20T12:00:00.000Z",
        "size": "12",
    }
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=file_meta),
        patch("app.connectors.google_drive_sync.download_file_content",
              return_value=("notes.txt", b"hello from drive")),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        result = sync_google_drive(company_id=company_id)
    finally:
        for p in patches:
            p.stop()
    assert result.errors == []
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    assert row["last_sync_error"] is None


def test_sync_result_to_dict_keys_unchanged():
    keys = set(SyncResult(dataset="acme").to_dict())
    assert keys == {"dataset", "synced", "skipped", "errors", "kg_queued", "kg_signals"}

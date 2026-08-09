"""Tests for Google Drive picked-file sync (mocked Drive API).

Under the drive.file scope there is still no Drive-wide listing — the Picker
frontend hands us explicit IDs which we store in config["files"] and sync.

A picked ID may be a FILE or a FOLDER, and only Drive metadata says which. A
folder is expanded to the files beneath it on every sync (so files added later
arrive on their own), bounded by depth and count, with what it expanded to
recorded in config["folder_contents"] for the UI.
"""
import uuid
from pathlib import Path
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
        # The converted markdown is really on disk in the dataset corpus.
        # This is where a Drive file's TEXT lives, and it is why the document
        # catalog deliberately stores no body of its own for Drive — doing so
        # would be a second copy of the same customer file.
        md_path = Path(result.synced[0]["md_path"])
        assert md_path.exists()
        assert "hello from drive" in md_path.read_text(encoding="utf-8")
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




# ── Folders ──────────────────────────────────────────────────────────────────
#
# The Picker shows folders so a user can browse INTO one and pick the files
# inside, but does not let a folder itself be selected. Verified against a live
# Drive on 2026-08-03: under drive.file the Picker grants the folder OBJECT and
# nothing beneath it — the folder's metadata reads fine while files.list on it
# returns zero children, not a 403. A connected folder is undetectably inert.
#
# These cover the entries that predate that change and are still in config.


def test_a_connected_folder_is_skipped_with_copy_that_helps(
    drive_connected, kg_kickoff
):
    company_id = drive_connected
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=_folder_meta()),
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

    # Not an error — nothing went wrong, the grant simply does not reach inside.
    assert result.errors == []
    assert result.synced == []
    assert len(result.skipped) == 1
    reason = result.skipped[0]["reason"]
    assert "picked directly" in reason
    # The copy has to say what WORKS, not just what didn't.
    assert "select the files inside" in reason


def test_a_folder_is_never_downloaded(drive_connected, kg_kickoff):
    """A folder has no bytes; asking Drive to export one is an error the user
    would see for an action they did not take."""
    company_id = drive_connected
    download_mock = MagicMock(return_value=("x.txt", b"x"))
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
        sync_google_drive(
            company_id=company_id,
            files=[{"id": "folder0001", "name": "Xometry"}],
        )
    finally:
        for p in patches:
            p.stop()
    download_mock.assert_not_called()


def test_a_folder_is_marked_as_one_so_the_ui_can_say_so(
    drive_connected, kg_kickoff
):
    """`folder_contents` is how the UI tells a folder from a file. Without the
    key a stale folder row renders as an ordinary file that mysteriously never
    syncs."""
    import json as _json

    company_id = drive_connected
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=_folder_meta()),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        sync_google_drive(
            company_id=company_id,
            files=[{"id": "folder0001", "name": "Xometry"}],
        )
    finally:
        for p in patches:
            p.stop()

    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    assert _json.loads(row["config_json"])["folder_contents"] == {
        "folder0001": []
    }


def test_folder_marks_are_replaced_not_merged(drive_connected, kg_kickoff):
    """A folder the user disconnects must take its marker with it."""
    import json as _json

    company_id = drive_connected
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    cfg = _json.loads(row["config_json"])
    cfg["folder_contents"] = {"goneforever": []}
    db.patch_connection_config(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER, cfg)

    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=_folder_meta()),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        sync_google_drive(
            company_id=company_id,
            files=[{"id": "folder0001", "name": "Xometry"}],
        )
    finally:
        for p in patches:
            p.stop()

    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    contents = _json.loads(row["config_json"])["folder_contents"]
    assert "goneforever" not in contents
    assert contents == {"folder0001": []}


def test_sync_tells_the_extractor_where_it_wrote_the_markdown(
    drive_connected, kg_kickoff
):
    """The converted name is normalised and collision-suffixed, so the moment
    `ingest_file` returns is the ONLY moment it is knowable. The sync carries
    it to the extractor, which records it against the file's provenance row —
    without that, a Drive document can be catalogued, summarised and ranked
    and still have no readable body.

    The KG-only refresh pass (corpus already fresh) reports NO location, which
    is correct rather than sloppy: it never wrote a file, so it has nothing to
    report, and the extractor keeps whatever the earlier pass recorded instead
    of being handed a blank to overwrite it with.
    """
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

        doc = kg_kickoff[0][0]
        assert doc.dataset == "acme"
        assert doc.md_file == result.synced[0]["md_path"]
        assert Path(doc.md_file).exists()

        # Second pass: corpus fresh, extraction retrying. No location claimed.
        sync_google_drive(company_id=company_id)
        assert kg_kickoff[1][0].md_file == ""
    finally:
        for p in patches:
            p.stop()

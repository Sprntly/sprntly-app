"""Tests for Google Drive picked-file sync (mocked Drive API).

Under the drive.file scope there is still no Drive-wide listing — the Picker
frontend hands us explicit IDs which we store in config["files"] and sync.

A picked ID may be a FILE or a FOLDER, and only Drive metadata says which. A
folder is recursively expanded to every descendant file on every sync (so
files added later arrive on their own without re-opening the Picker), bounded
by depth and count, with the SUBTREE SHAPE (not a flat list) recorded in
config["folder_contents"] for the UI.
"""
import re
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from app import db
from app.connectors import google_oauth
from app.connectors import google_drive_sync
from app.connectors.google_drive_sync import (
    GOOGLE_FOLDER,
    MAX_SYNC_BYTES,
    SyncConfigError,
    SyncResult,
    _list_folder_children,
    drive_http_error_message,
    expand_folder,
    get_file_metadata,
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


# ─── Injected service + entries (service-account mode's own path) ───────────
#
# service_account mode injects an already-authenticated Drive client
# (`service`) and its own enumerated item set (`entries`) instead of letting
# sync_google_drive resolve OAuth credentials off the connection row and
# read config["files"]. Both default to None, and the pre-existing tests
# above (which never pass either) are the byte-identical-default coverage.


def test_default_params_still_build_drive_service_and_use_config_files(
    drive_connected, kg_kickoff
):
    """AC2, mutation-proof: with service=None, entries=None (the default —
    every call site above this ticket), build_drive_service IS called and
    config["files"] IS the item source, unchanged by the new parameters."""
    company_id = drive_connected
    file_meta = {
        "id": "file0001aa",
        "name": "notes.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-05-20T12:00:00.000Z",
        "size": "12",
    }
    with (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=MagicMock()) as mock_build,
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=file_meta) as mock_get_meta,
        patch("app.connectors.google_drive_sync.download_file_content",
              return_value=("notes.txt", b"hello from drive")),
    ):
        result = sync_google_drive(company_id=company_id)

    mock_build.assert_called_once()
    # The item resolved is config["files"]'s file0001aa (the drive_connected
    # fixture's picked file) — not anything injected.
    mock_get_meta.assert_called_once()
    assert mock_get_meta.call_args.args[1] == "file0001aa"
    assert len(result.synced) == 1
    assert result.synced[0]["filename"] == "notes.txt"


def test_injected_service_is_used_and_build_drive_service_is_skipped(
    drive_connected, kg_kickoff
):
    """AC1/AC2: when `service` is injected, sync_google_drive uses it as-is
    and NEVER calls build_drive_service — proven with a REAL call into
    sync_google_drive (not a mock of sync_google_drive itself)."""
    company_id = drive_connected
    file_id = "injfile01"
    fake_service = MagicMock()
    fake_service.files.return_value.get.return_value.execute.return_value = {
        "id": file_id,
        "name": "shared.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-05-20T12:00:00.000Z",
        "size": "5",
    }

    with (
        patch("app.connectors.google_drive_sync.build_drive_service") as mock_build,
        patch("app.connectors.google_drive_sync.download_file_content",
              return_value=("shared.txt", b"hi from the injected service")),
    ):
        result = sync_google_drive(
            company_id=company_id,
            service=fake_service,
            entries=[{"id": file_id, "name": "shared.txt"}],
        )

    mock_build.assert_not_called()
    assert result.errors == []
    assert len(result.synced) == 1
    assert result.synced[0]["filename"] == "shared.txt"
    # The metadata call landed on the INJECTED service, not any other client.
    fake_service.files.return_value.get.assert_called()

    # config["files"] (the fixture's file0001aa pick) is untouched — entries
    # replaced the item set for this call without overwriting stored config.
    import json as _json

    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    cfg = _json.loads(row["config_json"])
    assert cfg["files"] == [{"id": "file0001aa", "name": "notes.txt"}]


def test_injected_entries_folder_expands_recursively_via_injected_service(
    drive_connected, kg_kickoff
):
    """AC1: a folder entry passed via `entries` (what SA's enumerate_shared
    returns for a shared folder) is recursively expanded via the real
    `expand_folder`, using the INJECTED service — the same recursion the
    picked-folder OAuth path uses — so the whole subtree ingests, not just
    the folder object."""
    company_id = drive_connected
    root_id, child_id = "safolder01", "sadescfile1"
    children = {
        root_id: [{
            "id": child_id, "name": "descendant.txt", "mimeType": "text/plain",
            "modifiedTime": "2026-05-20T12:00:00.000Z", "size": "5",
        }],
    }
    fake_service = _drive_service_with_children(children)
    fake_service.files.return_value.get.return_value.execute.return_value = (
        _folder_meta(name="SA Shared Folder", folder_id=root_id)
    )

    with (
        patch("app.connectors.google_drive_sync.build_drive_service") as mock_build,
        patch("app.connectors.google_drive_sync.download_file_content",
              return_value=("descendant.txt", b"from the SA subtree")),
    ):
        result = sync_google_drive(
            company_id=company_id,
            service=fake_service,
            entries=[{"id": root_id, "name": "SA Shared Folder"}],
        )

    mock_build.assert_not_called()
    assert result.errors == []
    assert len(result.synced) == 1
    assert result.synced[0]["filename"] == "descendant.txt"

    import json as _json

    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    contents = _json.loads(row["config_json"])["folder_contents"][root_id]
    assert {n["id"] for n in contents} == {child_id}


# ─── Fail-loud: a picked item that can't be ingested is never a silent skip ──


def _folder_meta(
    name: str = "Contoso",
    modified: str = "2026-05-20T12:00:00.000Z",
    folder_id: str = "folder0001",
) -> dict:
    return {
        "id": folder_id,
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "modifiedTime": modified,
    }


def _drive_service_with_children(children_by_folder: dict[str, list[dict]]) -> MagicMock:
    """A mocked Drive service whose ``files().list(q="'<id>' in parents...")``
    returns ``children_by_folder[<id>]`` — the shape `_list_folder_children` /
    `expand_folder` walk against. Every ``list(...)`` call's kwargs are
    recorded on ``service._list_calls`` so a test can assert the shared-drive
    params were passed."""
    service = MagicMock()
    calls: list[dict] = []

    def _list(**kwargs):
        calls.append(kwargs)
        q = kwargs.get("q", "")
        m = re.match(r"'([^']+)' in parents", q)
        fid = m.group(1) if m else ""
        resp = MagicMock()
        resp.execute.return_value = {
            "files": children_by_folder.get(fid, []),
            "nextPageToken": None,
        }
        return resp

    service.files.return_value.list.side_effect = _list
    service._list_calls = calls
    return service


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
# A picked folder is stored exactly like a picked file. Every sync recursively
# re-walks its whole subtree (expand_folder), and every descendant FILE found
# flows through the same download + dedup + KG-ingest path as a directly
# picked file. Whether the walk finds anything depends on the connection's
# granted OAuth scope: under drive.file it legitimately comes back empty (a
# reportable, non-error outcome), which is why the frontend gates folder
# SELECTION on the connection actually holding drive.readonly.


# ── expand_folder / _list_folder_children: pure walk logic, mocked at the
# service.files().list() boundary ──────────────────────────────────────────


def test_list_folder_children_passes_shared_drive_params():
    """Without supportsAllDrives/includeItemsFromAllDrives, files.list only
    searches My Drive — a folder living in a Shared Drive would silently
    return 0 children regardless of OAuth scope."""
    service = _drive_service_with_children({"folder0001": []})
    _list_folder_children(service, "folder0001")
    assert len(service._list_calls) == 1
    call = service._list_calls[0]
    assert call["supportsAllDrives"] is True
    assert call["includeItemsFromAllDrives"] is True
    assert "'folder0001' in parents" in call["q"]


def test_get_file_metadata_passes_shared_drive_params():
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = {"id": "f1"}
    get_file_metadata(service, "f1")
    _, kwargs = service.files.return_value.get.call_args
    assert kwargs["supportsAllDrives"] is True


def test_expand_folder_walks_multi_level_nesting():
    root, sub = "folder-root", "folder-sub"
    children = {
        root: [
            {"id": sub, "name": "Sub", "mimeType": GOOGLE_FOLDER},
            {"id": "file-root-1", "name": "root.txt", "mimeType": "text/plain"},
        ],
        sub: [
            {"id": "file-sub-1", "name": "sub.txt", "mimeType": "text/plain"},
        ],
    }
    service = _drive_service_with_children(children)
    files, tree_nodes = expand_folder(service, root, "Root")

    assert {f["id"] for f in files} == {"file-root-1", "file-sub-1"}
    # The folder object itself is never in `files` — only its descendants.
    assert sub not in {f["id"] for f in files}


def test_expand_folder_preserves_subtree_shape_not_flattened():
    """The flatten-bug regression: a subfolder's files stay parented to the
    SUBFOLDER in the returned tree_nodes, not hoisted to the picked root."""
    root, sub = "folder-root", "folder-sub"
    children = {
        root: [{"id": sub, "name": "Sub", "mimeType": GOOGLE_FOLDER}],
        sub: [{"id": "file-sub-1", "name": "sub.txt", "mimeType": "text/plain"}],
    }
    service = _drive_service_with_children(children)
    _, tree_nodes = expand_folder(service, root, "Root")

    by_id = {n["id"]: n for n in tree_nodes}
    assert by_id[sub]["parentId"] == root
    # This is the assertion the old flat-list storage got wrong: the
    # sub-folder's file must be parented to the SUB-folder, not the root.
    assert by_id["file-sub-1"]["parentId"] == sub
    assert by_id["file-sub-1"]["parentId"] != root


def test_expand_folder_enforces_max_depth(monkeypatch):
    monkeypatch.setattr(google_drive_sync, "_MAX_FOLDER_DEPTH", 2)
    # root(depth0) -> f1(depth1) -> f2(depth2) -> f3(depth3, must NOT be
    # descended into — its child file is unreachable).
    children = {
        "root": [{"id": "f1", "name": "f1", "mimeType": GOOGLE_FOLDER}],
        "f1": [{"id": "f2", "name": "f2", "mimeType": GOOGLE_FOLDER}],
        "f2": [{"id": "f3", "name": "f3", "mimeType": GOOGLE_FOLDER}],
        "f3": [{"id": "toodeep.txt", "name": "toodeep.txt", "mimeType": "text/plain"}],
    }
    service = _drive_service_with_children(children)
    files, tree_nodes = expand_folder(service, "root", "Root")

    # f3 is SEEN (listed as a child of f2, so it shows in the tree)...
    assert any(n["id"] == "f3" for n in tree_nodes)
    # ...but never walked INTO, so its child never appears anywhere.
    assert not any(n["id"] == "toodeep.txt" for n in tree_nodes)
    assert files == []


def test_expand_folder_enforces_max_files(monkeypatch):
    monkeypatch.setattr(google_drive_sync, "_MAX_FOLDER_FILES", 3)
    kids = [
        {"id": f"file{i}", "name": f"file{i}.txt", "mimeType": "text/plain"}
        for i in range(10)
    ]
    service = _drive_service_with_children({"root": kids})
    files, _ = expand_folder(service, "root", "Root")
    assert len(files) == 3


# ── sync_google_drive: the folder branch, end to end ───────────────────────


def test_a_folder_that_expands_to_nothing_is_skipped_with_the_honest_message(
    drive_connected, kg_kickoff
):
    """The drive.file no-cascade case: the walk legitimately finds nothing.
    Not an error — nothing went wrong, the grant simply does not reach
    inside — but reported, not silently connected-and-inert."""
    company_id = drive_connected
    service = _drive_service_with_children({"folder0001": []})
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=service),
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
            files=[{"id": "folder0001", "name": "Contoso"}],
        )
    finally:
        for p in patches:
            p.stop()

    assert result.errors == []
    assert result.synced == []
    assert len(result.skipped) == 1
    reason = result.skipped[0]["reason"]
    assert "returned no files" in reason
    assert "pick the files inside" in reason


def test_a_folder_object_is_never_downloaded(drive_connected, kg_kickoff):
    """A folder has no bytes; asking Drive to export one is an error the user
    would see for an action they did not take. (Its descendants, if any, DO
    get downloaded — covered by the parity test below.)"""
    company_id = drive_connected
    service = _drive_service_with_children({"folder0001": []})
    download_mock = MagicMock(return_value=("x.txt", b"x"))
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=service),
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
            files=[{"id": "folder0001", "name": "Contoso"}],
        )
    finally:
        for p in patches:
            p.stop()
    download_mock.assert_not_called()


def test_folder_contents_stores_subtree_shape_not_flat_list(
    drive_connected, kg_kickoff
):
    """`folder_contents` is how the UI tells a folder from a file, and how it
    renders the nested tree. A subfolder's files must stay parented to the
    subfolder — the flatten-bug regression, exercised through the full sync
    path this time (not just expand_folder in isolation)."""
    import json as _json

    company_id = drive_connected
    root_id, sub_id, subfile_id = "folder0001", "subfolder01", "subfile001"
    children = {
        root_id: [{"id": sub_id, "name": "Sub", "mimeType": GOOGLE_FOLDER}],
        sub_id: [{
            "id": subfile_id, "name": "in-sub.txt", "mimeType": "text/plain",
            "modifiedTime": "2026-05-20T12:00:00.000Z", "size": "5",
        }],
    }
    service = _drive_service_with_children(children)
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=service),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=_folder_meta(folder_id=root_id)),
        patch("app.connectors.google_drive_sync.download_file_content",
              return_value=("in-sub.txt", b"hello")),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        sync_google_drive(
            company_id=company_id,
            files=[{"id": root_id, "name": "Contoso"}],
        )
    finally:
        for p in patches:
            p.stop()

    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    contents = _json.loads(row["config_json"])["folder_contents"][root_id]
    by_id = {n["id"]: n for n in contents}
    assert by_id[sub_id]["parentId"] == root_id
    assert by_id[subfile_id]["parentId"] == sub_id  # NOT root_id


def test_folder_marks_are_replaced_not_merged(drive_connected, kg_kickoff):
    """A folder the user disconnects must take its marker with it."""
    import json as _json

    company_id = drive_connected
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    cfg = _json.loads(row["config_json"])
    cfg["folder_contents"] = {"goneforever": []}
    db.patch_connection_config(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER, cfg)

    service = _drive_service_with_children({"folder0001": []})
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=service),
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
            files=[{"id": "folder0001", "name": "Contoso"}],
        )
    finally:
        for p in patches:
            p.stop()

    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    contents = _json.loads(row["config_json"])["folder_contents"]
    assert "goneforever" not in contents
    assert contents == {"folder0001": []}


def test_folder_descendant_hits_same_target_processing_as_a_picked_file(
    drive_connected, kg_kickoff
):
    """Parity: a file discovered by walking a picked folder must flow through
    the IDENTICAL corpus-ingest + KG-queue path as a directly-picked file —
    no separate, lesser handling for a walked descendant."""
    company_id = drive_connected
    root_id, child_id = "folder0001", "descfile01"
    children = {
        root_id: [{
            "id": child_id, "name": "descendant.txt", "mimeType": "text/plain",
            "modifiedTime": "2026-05-20T12:00:00.000Z", "size": "5",
        }],
    }
    service = _drive_service_with_children(children)
    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=service),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=_folder_meta(folder_id=root_id)),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.download_file_content",
              return_value=("descendant.txt", b"hello from subtree")),
    )
    for p in patches:
        p.start()
    try:
        result = sync_google_drive(
            company_id=company_id,
            files=[{"id": root_id, "name": "Xometry"}],
        )
    finally:
        for p in patches:
            p.stop()

    # Same outcome shape a directly-picked file's success takes: corpus-synced
    # and queued for KG extraction.
    assert len(result.synced) == 1
    assert result.kg_queued == ["descendant"]
    assert len(kg_kickoff) == 1
    assert kg_kickoff[0][0].file_id == child_id


def test_folder_change_detection_add_update_remove(drive_connected, kg_kickoff):
    """Across successive syncs of the same picked folder: a file Drive adds to
    it is ingested on the next sync; a file whose mtime changes is
    re-ingested; a file removed from the folder is simply no longer a walk
    target (never re-downloaded). Its prior KG rows are NOT purged by this —
    they decay, same as every other connector's remove behaviour; not
    re-fixed here."""
    company_id = drive_connected
    root_id = "folder0001"
    file_a = {
        "id": "filea01", "name": "a.txt", "mimeType": "text/plain",
        "modifiedTime": "2026-05-20T12:00:00.000Z", "size": "5",
    }
    file_b = {
        "id": "fileb01", "name": "b.txt", "mimeType": "text/plain",
        "modifiedTime": "2026-05-20T12:00:00.000Z", "size": "5",
    }
    children: dict[str, list[dict]] = {root_id: [dict(file_a)]}
    service = _drive_service_with_children(children)

    downloaded: list[str] = []

    def _download(service, meta):
        downloaded.append(meta["name"])
        return meta["name"], f"content of {meta['name']}".encode()

    patches = (
        patch("app.connectors.google_drive_sync.build_drive_service",
              return_value=service),
        patch("app.connectors.google_drive_sync.get_file_metadata",
              return_value=_folder_meta(folder_id=root_id)),
        patch("app.connectors.google_drive_sync._refresh_credentials",
              return_value=MagicMock()),
        patch("app.connectors.google_drive_sync.download_file_content",
              side_effect=_download),
    )
    for p in patches:
        p.start()
    try:
        # 1) Only file A is in the folder.
        r1 = sync_google_drive(
            company_id=company_id, files=[{"id": root_id, "name": "Xometry"}]
        )
        assert {s["filename"] for s in r1.synced} == {"a.txt"}

        # 2) ADD: Drive gains file B — the next walk sees it, unprompted. Only
        # the NEW file is corpus-synced; A's unchanged corpus copy is not
        # rewritten (it may still be re-queued for KG — see the grandfathering
        # test above; that is a KG-freshness nuance, not a corpus re-sync).
        children[root_id] = [dict(file_a), dict(file_b)]
        r2 = sync_google_drive(company_id=company_id)
        assert {s["filename"] for s in r2.synced} == {"b.txt"}

        # 3) UPDATE: file A's mtime changes — re-ingested; B untouched.
        children[root_id][0] = {**file_a, "modifiedTime": "2026-06-01T00:00:00.000Z"}
        r3 = sync_google_drive(company_id=company_id)
        assert {s["filename"] for s in r3.synced} == {"a.txt"}

        # 4) REMOVE: file B is gone from Drive — the next walk never lists it,
        # so it's not downloaded and not a sync target at all.
        children[root_id] = [children[root_id][0]]  # only (updated) A remains
        downloaded.clear()
        r4 = sync_google_drive(company_id=company_id)
        assert "b.txt" not in downloaded
        assert all(s["filename"] != "b.txt" for s in r4.synced)
    finally:
        for p in patches:
            p.stop()


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


# ───────────── what a picked selection actually covers ─────────────────────
#
# `reachable_file_ids` is the read side of the two-part Drive selection:
# `config["files"]` is what the Picker returned, `config["folder_contents"]`
# is what a picked folder expanded to at the last sync. Anything asking "which
# documents does this selection cover" needs both, and it must answer from
# STORED state — a live walk would make a transient Drive failure look like a
# folder that shrank.


def test_reachable_ids_include_a_picked_folders_stored_subtree():
    from app.connectors.google_drive_sync import reachable_file_ids

    contents = {"folderA": [
        {"id": "f1", "parentId": "folderA"},
        {"id": "sub1", "mimeType": GOOGLE_FOLDER, "parentId": "folderA"},
        # Nested descendants live in the SAME flat list under the picked root
        # (expand_folder stores one node list per root), so no recursion is
        # needed here and none is written.
        {"id": "f2", "parentId": "sub1"},
    ]}

    assert reachable_file_ids(["folderA"], contents) == {
        "folderA", "sub1", "f1", "f2"
    }


def test_reachable_ids_of_a_plain_file_are_just_itself():
    from app.connectors.google_drive_sync import reachable_file_ids

    assert reachable_file_ids(["file1"], {"folderA": [{"id": "f1"}]}) == {"file1"}


def test_reachable_ids_tolerate_a_missing_or_junk_expansion():
    """`folder_contents` is whatever the last sync wrote, including `{}` for a
    folder whose walk failed. A cleanup keyed on this must degrade to "just
    the picked ids" rather than raise inside a settings save."""
    from app.connectors.google_drive_sync import reachable_file_ids

    assert reachable_file_ids(["folderA"], None) == {"folderA"}
    assert reachable_file_ids(["folderA"], {"folderA": []}) == {"folderA"}
    assert reachable_file_ids(
        ["folderA"], {"folderA": ["not-a-dict", {"name": "no id"}, {"id": ""}]}
    ) == {"folderA"}
    assert reachable_file_ids(["", "  ", None], {}) == set()

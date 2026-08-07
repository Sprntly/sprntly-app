"""Tests for the service-account Drive access mode (mocked IAM + Drive API).

Covers provisioning (`mint_company_service_account`) — connection-level
idempotency, the random account-id uniquifier that sidesteps GCP's ~30-day
tombstone on a deleted SA name, and the bounded propagation-retry on
`keys.create` — plus enumeration (`enumerate_shared`) and the sync entry
point (`sync_service_account`) that feeds the same walk/download/ingest path
the OAuth route uses.
"""
import base64
import importlib
import json
import sys

import pytest
from cryptography.fernet import Fernet
from googleapiclient.errors import HttpError
from unittest.mock import MagicMock, patch

from app import db
from app.connectors import google_drive_sync, google_oauth, google_service_account
from app.connectors.tokens import decrypt_token_json
from app.db.client import require_client
from tests._company_helpers import seed_company


@pytest.fixture
def sa_env(isolated_settings, monkeypatch):
    """A service_account-mode environment: bootstrap credential configured,
    encryption key set, and every module that cached `settings` at import
    time reloaded so it sees these values (same pattern as the OAuth route
    tests' `google_env` fixture)."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GOOGLE_DRIVE_ACCESS_MODE", "service_account")
    monkeypatch.setenv("GCP_SA_BOOTSTRAP_PROJECT", "test-project")
    monkeypatch.setenv(
        "GCP_SA_BOOTSTRAP_KEY_JSON",
        json.dumps({"type": "service_account", "project_id": "test-project"}),
    )
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.google_service_account",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
        else:
            importlib.import_module(name)
    yield


def _fake_iam(key_side_effect=None) -> MagicMock:
    """A MagicMock standing in for the `iam` discovery Resource, wired so
    `create()` succeeds and `keys().create()` returns a decodable key (or
    raises/side-effects the caller supplies)."""
    iam = MagicMock()
    iam.projects().serviceAccounts().create().execute.return_value = {}
    key_call = iam.projects().serviceAccounts().keys().create().execute
    if key_side_effect is not None:
        key_call.side_effect = key_side_effect
    else:
        key_call.return_value = {
            "privateKeyData": base64.b64encode(
                b'{"type":"service_account","key":"1"}'
            ).decode(),
        }
    return iam


def _http_error(status: int, message: str) -> HttpError:
    resp = MagicMock(status=status)
    content = json.dumps({"error": {"message": message}}).encode()
    return HttpError(resp=resp, content=content)


# ─────────────────────── mode gating ───────────────────────


def test_service_account_mode_enabled_reflects_env(sa_env):
    assert google_service_account.service_account_mode_enabled() is True


def test_service_account_mode_disabled_by_default(isolated_settings, monkeypatch):
    monkeypatch.delenv("GOOGLE_DRIVE_ACCESS_MODE", raising=False)
    for name in ("app.config", "app.connectors.google_service_account"):
        importlib.reload(sys.modules[name])
    assert google_service_account.service_account_mode_enabled() is False


def test_service_account_mode_configured_requires_both_env_vars(
    isolated_settings, monkeypatch
):
    monkeypatch.setenv("GCP_SA_BOOTSTRAP_PROJECT", "")
    monkeypatch.setenv("GCP_SA_BOOTSTRAP_KEY_JSON", "")
    for name in ("app.config", "app.connectors.google_service_account"):
        importlib.reload(sys.modules[name])
    assert google_service_account.service_account_mode_configured() is False


# ─────────────────────── mint: provisioning ───────────────────────


def test_mint_creates_sa_and_stores_key_in_dedicated_column(sa_env):
    company_id = seed_company()
    iam = _fake_iam()

    with patch.object(
        google_service_account, "_bootstrap_iam_service", return_value=iam
    ):
        email = google_service_account.mint_company_service_account(company_id)

    assert email.startswith("sprntly-")
    assert email.endswith("@test-project.iam.gserviceaccount.com")

    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    assert row is not None
    # The key lives in its OWN column — never the OAuth token column, which is
    # left empty (no OAuth connect happened) so the two credentials coexist.
    assert row["sa_key_encrypted"]
    assert row["token_json_encrypted"] == ""
    decrypted = json.loads(decrypt_token_json(row["sa_key_encrypted"]))
    assert decrypted == {"type": "service_account", "key": "1"}
    # Email + tree state are in the client-visible config, not the key.
    cfg = json.loads(row["config_json"])
    assert cfg["service_account_email"] == email


def test_mint_is_idempotent_reuses_existing_key(sa_env):
    company_id = seed_company()
    iam = _fake_iam()

    with patch.object(
        google_service_account, "_bootstrap_iam_service", return_value=iam
    ) as bootstrap:
        email1 = google_service_account.mint_company_service_account(company_id)
        email2 = google_service_account.mint_company_service_account(company_id)

    assert email1 == email2
    # The second call reused the stored SA — it never even asked for an IAM
    # client, let alone called create()/keys().create() again.
    assert bootstrap.call_count == 1
    assert iam.projects().serviceAccounts().create().execute.call_count == 1
    assert (
        iam.projects().serviceAccounts().keys().create().execute.call_count == 1
    )


def test_mint_uniquifier_differs_across_calls_for_the_same_company(sa_env):
    company_id = seed_company()
    ids = {google_service_account._account_id_for(company_id) for _ in range(8)}
    # Same company prefix every time, but the random suffix must differ, or a
    # re-mint after a delete would collide with GCP's ~30-day tombstone on the
    # old account-id.
    assert len(ids) == 8
    assert all(i.startswith("sprntly-") for i in ids)


def test_mint_after_simulated_delete_uses_a_fresh_account_id_and_reprovisions(
    sa_env,
):
    company_id = seed_company()
    iam = _fake_iam()

    with patch.object(
        google_service_account, "_bootstrap_iam_service", return_value=iam
    ):
        email1 = google_service_account.mint_company_service_account(company_id)

    # Simulate the SA having been deleted out from under us (e.g. an admin
    # cleaned it up in GCP): clear the stored email + key so a re-mint is
    # forced, exactly like a customer disconnecting and reconnecting.
    require_client().table("connections").update(
        {"sa_key_encrypted": None, "config": {}}
    ).eq("company_id", company_id).eq(
        "provider", google_oauth.GOOGLE_DRIVE_PROVIDER
    ).execute()

    with patch.object(
        google_service_account, "_bootstrap_iam_service", return_value=iam
    ):
        email2 = google_service_account.mint_company_service_account(company_id)

    # A fresh random uniquifier — never the tombstoned name from the first mint.
    assert email1 != email2
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    assert row["sa_key_encrypted"]


def test_mint_key_create_retries_transient_404_then_succeeds(sa_env, monkeypatch):
    company_id = seed_company()
    err = _http_error(404, "does not exist")
    ok = {
        "privateKeyData": base64.b64encode(b'{"type":"service_account"}').decode(),
    }
    iam = _fake_iam(key_side_effect=[err, err, ok])
    monkeypatch.setattr(google_service_account.time, "sleep", lambda *_: None)

    with patch.object(
        google_service_account, "_bootstrap_iam_service", return_value=iam
    ):
        email = google_service_account.mint_company_service_account(company_id)

    assert email
    assert (
        iam.projects().serviceAccounts().keys().create().execute.call_count == 3
    )


def test_mint_key_create_403_policy_denial_is_fatal_not_retried(sa_env, monkeypatch):
    company_id = seed_company()
    err = _http_error(403, "iam.disableServiceAccountKeyCreation")
    iam = _fake_iam(key_side_effect=err)
    monkeypatch.setattr(google_service_account.time, "sleep", lambda *_: None)

    with patch.object(
        google_service_account, "_bootstrap_iam_service", return_value=iam
    ):
        with pytest.raises(google_service_account.ServiceAccountModeError) as exc:
            google_service_account.mint_company_service_account(company_id)

    assert "disableServiceAccountKeyCreation" in str(exc.value)
    # A 403 is a policy denial, not a propagation race — retrying it would
    # never succeed, so it fails on the FIRST attempt.
    assert (
        iam.projects().serviceAccounts().keys().create().execute.call_count == 1
    )


def test_mint_requires_configuration(isolated_settings, monkeypatch):
    monkeypatch.setenv("GCP_SA_BOOTSTRAP_PROJECT", "")
    monkeypatch.setenv("GCP_SA_BOOTSTRAP_KEY_JSON", "")
    for name in ("app.config", "app.connectors.google_service_account"):
        importlib.reload(sys.modules[name])
    company_id = seed_company()
    with pytest.raises(google_service_account.ServiceAccountModeError):
        google_service_account.mint_company_service_account(company_id)


# ─────────────────────── enumerate_shared ───────────────────────


def test_enumerate_shared_returns_files_and_folders(sa_env):
    service = MagicMock()
    service.files().list().execute.return_value = {
        "files": [
            {
                "id": "file1",
                "name": "Q3 plan.docx",
                "mimeType": "application/vnd.google-apps.document",
            },
            {
                "id": "folder1",
                "name": "Shared team folder",
                "mimeType": google_drive_sync.GOOGLE_FOLDER,
            },
        ],
        "nextPageToken": None,
    }

    entries = google_service_account.enumerate_shared(service)

    ids = {e["id"] for e in entries}
    assert ids == {"file1", "folder1"}
    folder_entry = next(e for e in entries if e["id"] == "folder1")
    assert folder_entry["mimeType"] == google_drive_sync.GOOGLE_FOLDER

    # sharedWithMe, over ALL drives (a shared folder can live in a Shared
    # Drive, not just My Drive).
    call_kwargs = service.files().list.call_args.kwargs
    assert "sharedWithMe" in call_kwargs["q"]
    assert call_kwargs["supportsAllDrives"] is True
    assert call_kwargs["includeItemsFromAllDrives"] is True


def test_enumerate_shared_paginates(sa_env):
    service = MagicMock()
    service.files().list().execute.side_effect = [
        {"files": [{"id": "a", "name": "A", "mimeType": "text/plain"}],
         "nextPageToken": "page2"},
        {"files": [{"id": "b", "name": "B", "mimeType": "text/plain"}],
         "nextPageToken": None},
    ]
    entries = google_service_account.enumerate_shared(service)
    assert {e["id"] for e in entries} == {"a", "b"}


# ─────────────────────── sync_service_account: parity ───────────────────────


def test_sync_service_account_feeds_the_same_ingest_loop_as_oauth(sa_env):
    company_id = seed_company()
    db.upsert_connection(
        company_id=company_id,
        provider=google_oauth.GOOGLE_DRIVE_PROVIDER,
        token_encrypted="",
        scopes=google_service_account.SA_DRIVE_SCOPE,
        config_json="{}",
    )
    fake_service = MagicMock()
    entries = [{"id": "folder1", "name": "Shared team folder"}]
    captured: dict = {}

    def fake_sync_google_drive(**kwargs):
        captured.update(kwargs)
        result = MagicMock()
        result.to_dict.return_value = {"ingested": [], "skipped": [], "errors": []}
        return result

    with (
        patch.object(
            google_service_account,
            "google_drive_service_for_company",
            return_value=fake_service,
        ),
        patch.object(
            google_service_account, "enumerate_shared", return_value=entries
        ),
        patch.object(
            google_drive_sync, "sync_google_drive", side_effect=fake_sync_google_drive
        ),
    ):
        google_service_account.sync_service_account(company_id, dataset="acme")

    # The SA path hands the EXACT same walk/download/ingest function the
    # OAuth Picker route calls, just with an injected service + entries
    # instead of resolving them from stored OAuth credentials + config[files].
    assert captured["company_id"] == company_id
    assert captured["dataset"] == "acme"
    assert captured["service"] is fake_service
    assert captured["entries"] == entries


def test_sync_service_account_persists_shared_roots_for_the_tree_ui(sa_env):
    company_id = seed_company()
    db.upsert_connection(
        company_id=company_id,
        provider=google_oauth.GOOGLE_DRIVE_PROVIDER,
        token_encrypted="",
        scopes=google_service_account.SA_DRIVE_SCOPE,
        config_json="{}",
    )
    entries = [{"id": "folder1", "name": "Shared team folder"}]

    def fake_sync_google_drive(**kwargs):
        result = MagicMock()
        result.to_dict.return_value = {"ingested": []}
        return result

    with (
        patch.object(
            google_service_account,
            "google_drive_service_for_company",
            return_value=MagicMock(),
        ),
        patch.object(
            google_service_account, "enumerate_shared", return_value=entries
        ),
        patch.object(
            google_drive_sync, "sync_google_drive", side_effect=fake_sync_google_drive
        ),
    ):
        google_service_account.sync_service_account(company_id)

    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    cfg = json.loads(row["config_json"])
    assert cfg["sa_shared_roots"] == entries


# ─────────────────────── db: dedicated column ───────────────────────


def test_update_connection_sa_key_writes_dedicated_column_leaves_oauth_token(
    sa_env,
):
    company_id = seed_company()
    db.upsert_connection(
        company_id=company_id,
        provider=google_oauth.GOOGLE_DRIVE_PROVIDER,
        token_encrypted="oauth-token-blob",
        scopes=google_oauth.DRIVE_FILE_SCOPE,
        config_json="{}",
    )
    db.update_connection_sa_key(
        company_id, google_oauth.GOOGLE_DRIVE_PROVIDER, "encrypted-sa-key-blob"
    )
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    assert row["sa_key_encrypted"] == "encrypted-sa-key-blob"
    # The OAuth user token, written first, is completely untouched.
    assert row["token_json_encrypted"] == "oauth-token-blob"

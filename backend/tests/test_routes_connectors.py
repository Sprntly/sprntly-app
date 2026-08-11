"""Tests for /v1/connectors Google Drive OAuth routes."""
import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials

from app.connectors import google_oauth
from tests._company_helpers import company_client


@pytest.fixture
def google_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/google-drive/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.google_oauth",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    import app.db as db_mod

    db_mod.init_db()
    yield


def test_list_requires_auth(unauth_client, google_env):
    # company_id is required even on the listing endpoint, but a
    # missing Authorization header still 401s first.
    r = unauth_client.get("/v1/connectors")
    assert r.status_code == 401


def test_list_empty(google_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/connectors")
    assert r.status_code == 200
    assert r.json() == {"connections": []}


def test_authorize_redirects(google_env, monkeypatch):
    ctx = company_client(monkeypatch)
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = (
        "https://accounts.google.com/o/oauth2/auth?test=1",
        None,
    )
    with patch("app.routes.connectors.google_oauth.build_flow", return_value=mock_flow):
        r = ctx.client.get(
            "/v1/connectors/google-drive/authorize",
            params={"dataset": "acme"},
            follow_redirects=False,
        )
    assert r.status_code == 307
    assert "accounts.google.com" in r.headers["location"]


def test_callback_stores_connection(google_env, monkeypatch):
    ctx = company_client(monkeypatch)
    state = google_oauth.sign_oauth_state(company_id=ctx.company_id, dataset="acme")
    creds = Credentials(
        token="access",
        refresh_token="refresh",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="test-client-id",
        client_secret="test-client-secret",
        scopes=[google_oauth.DRIVE_FILE_SCOPE],
    )
    mock_flow = MagicMock()
    mock_flow.credentials = creds
    with (
        patch("app.routes.connectors.google_oauth.build_flow", return_value=mock_flow),
        patch(
            "app.routes.connectors.google_oauth.fetch_google_account_email",
            return_value="pm@company.com",
        ),
    ):
        r = ctx.client.get(
            "/v1/connectors/google-drive/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 307
    assert "connected=google_drive" in r.headers["location"]

    listed = ctx.client.get(
        "/v1/connectors"
    ).json()
    assert len(listed["connections"]) == 1
    conn = listed["connections"][0]
    assert conn["provider"] == "google_drive"
    assert conn["google_email"] == "pm@company.com"
    assert conn["config"]["dataset"] == "acme"
    assert "token_json_encrypted" not in conn


def test_disconnect(google_env, monkeypatch):
    ctx = company_client(monkeypatch)
    state = google_oauth.sign_oauth_state(company_id=ctx.company_id, dataset=None)
    creds = Credentials(
        token="access",
        refresh_token="refresh",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="c",
        client_secret="s",
        scopes=[google_oauth.DRIVE_FILE_SCOPE],
    )
    mock_flow = MagicMock()
    mock_flow.credentials = creds
    with (
        patch("app.routes.connectors.google_oauth.build_flow", return_value=mock_flow),
        patch(
            "app.routes.connectors.google_oauth.fetch_google_account_email",
            return_value=None,
        ),
        patch("app.routes.connectors.google_oauth.try_revoke_credentials"),
    ):
        ctx.client.get(
            "/v1/connectors/google-drive/callback",
            params={"code": "x", "state": state},
        )
    r = ctx.client.delete(
        "/v1/connectors/google-drive"
    )
    assert r.status_code == 200
    assert ctx.client.get(
        "/v1/connectors"
    ).json() == {"connections": []}


# ─── POST /google-drive/sync — auto-enable branch (no-dataset path) ──────────
#
# The dataset-less branch resolves the dataset from the stored connection's
# config_json. It used to call db.get_connection(provider) with ONE positional
# arg, but the signature is get_connection(company_id, provider) — a TypeError
# that crashed every no-dataset sync. These tests pin the two-arg call.


def _seed_drive_connection(company_id: str, *, config_json: str) -> None:
    from app import db
    from app.connectors.tokens import encrypt_token_json

    db.upsert_connection(
        company_id=company_id,
        provider=google_oauth.GOOGLE_DRIVE_PROVIDER,
        token_encrypted=encrypt_token_json('{"token":"x","refresh_token":"y"}'),
        scopes="",
        account_label="pm@company.com",
        config_json=config_json,
    )


def test_sync_no_dataset_auto_enable_uses_two_arg_get_connection(google_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_drive_connection(ctx.company_id, config_json='{"dataset":"acme","files":[{"id":"file0001","name":"a.txt"}]}')

    fake_result = MagicMock()
    fake_result.to_dict.return_value = {"dataset": "acme", "ingested": 0, "skipped": 0}

    seen: dict = {}
    import app.routes.connectors as routes_mod
    real_get_connection = routes_mod.db.get_connection

    def spy_get_connection(company_id, provider):
        seen["args"] = (company_id, provider)
        return real_get_connection(company_id, provider)

    with (
        patch.object(routes_mod, "sync_google_drive", return_value=fake_result),
        patch.object(routes_mod.db, "get_connection", side_effect=spy_get_connection),
    ):
        r = ctx.client.post("/v1/connectors/google-drive/sync", json={})

    # No TypeError → the no-dataset branch resolved the dataset and returned 200.
    assert r.status_code == 200, r.text
    # The auto-enable lookup passed BOTH company_id and provider (the bug fix).
    assert seen["args"] == (ctx.company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)


def test_sync_no_dataset_resolves_dataset_and_auto_enables_input_source(google_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_drive_connection(ctx.company_id, config_json='{"dataset":"acme","files":[{"id":"file0001","name":"a.txt"}]}')

    fake_result = MagicMock()
    fake_result.to_dict.return_value = {"dataset": "acme"}

    import app.routes.connectors as routes_mod
    upserts: list = []

    def spy_upsert(dataset, source_type, **kw):
        upserts.append((dataset, source_type, kw))
        return {"dataset": dataset, "source_type": source_type}

    with (
        patch.object(routes_mod, "sync_google_drive", return_value=fake_result),
        patch.object(routes_mod.db, "upsert_input_source", side_effect=spy_upsert),
    ):
        r = ctx.client.post("/v1/connectors/google-drive/sync", json={})

    assert r.status_code == 200, r.text
    # The dataset resolved from the connection's config_json drove the auto-enable.
    assert len(upserts) == 1
    assert upserts[0][0] == "acme"
    assert upserts[0][1] == "google_drive"
    assert upserts[0][2]["enabled"] is True


# ─── POST /google-drive/files — save Picker-picked files + sync ──────────────


def test_save_files_requires_connection(google_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/google-drive/files",
        json={"files": [{"id": "abcdEFGH12", "name": "Plan"}]},
    )
    assert r.status_code == 404


def test_save_files_stores_picked_files_in_config_and_syncs(google_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_drive_connection(ctx.company_id, config_json='{"dataset":"acme"}')

    import json as _json

    import app.routes.connectors as routes_mod

    captured: dict = {}

    def fake_sync(*, company_id, dataset, files):
        # Mirror the real sync's persistence so we can assert config storage.
        from app import db as _db
        from app.connectors.google_drive_sync import (
            merge_config,
            normalize_picked_files,
        )

        row = _db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
        merge_config(
            row,
            {"files": normalize_picked_files(files), "dataset": dataset or "acme"},
        )
        captured["files"] = files
        captured["dataset"] = dataset
        result = MagicMock()
        result.to_dict.return_value = {
            "dataset": "acme",
            "synced": [],
            "skipped": [],
            "errors": [],
        }
        return result

    with (
        patch.object(routes_mod, "sync_google_drive", side_effect=fake_sync),
        patch.object(routes_mod.db, "upsert_input_source", return_value={}),
    ):
        r = ctx.client.post(
            "/v1/connectors/google-drive/files",
            json={
                "files": [
                    {"id": "abcdEFGH12", "name": "Plan"},
                    {"id": "zzzz9999xx"},
                ],
                "dataset": "acme",
            },
        )

    assert r.status_code == 200, r.text
    assert captured["files"] == [
        {"id": "abcdEFGH12", "name": "Plan"},
        {"id": "zzzz9999xx", "name": None},
    ]

    from app import db as _db

    row = _db.get_connection(ctx.company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    cfg = _json.loads(row["config_json"])
    assert cfg["files"] == [
        {"id": "abcdEFGH12", "name": "Plan"},
        {"id": "zzzz9999xx", "name": None},
    ]


def test_save_files_rejects_bad_file_id(google_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_drive_connection(ctx.company_id, config_json='{"dataset":"acme"}')
    r = ctx.client.post(
        "/v1/connectors/google-drive/files",
        json={"files": [{"id": "bad id!"}]},
    )
    assert r.status_code == 400
    assert "invalid Drive file id" in r.text


# ─── GET /google-drive/picker-token — browser-side Picker access token ────────


def test_picker_token_returns_refreshed_access_token(google_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_drive_connection(ctx.company_id, config_json='{"dataset":"acme"}')

    from datetime import datetime, timedelta, timezone

    import app.routes.connectors as routes_mod

    # Monkeypatch the refresh helper (the creds layer) so no network/Google
    # token exchange happens — return creds with a token + a future expiry.
    # google-auth stores expiry as a naive UTC datetime, so mirror that here.
    fake_creds = MagicMock()
    fake_creds.token = "ya29.fresh-access-token"
    fake_creds.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=1800
    )

    with patch.object(routes_mod, "_refresh_credentials", return_value=fake_creds):
        r = ctx.client.get("/v1/connectors/google-drive/picker-token")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"] == "ya29.fresh-access-token"
    # ~1800s remaining (allow a small clock-drift window), never the fallback.
    assert 1700 <= body["expires_in"] <= 1800


def test_picker_token_falls_back_when_no_expiry(google_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_drive_connection(ctx.company_id, config_json='{"dataset":"acme"}')

    import app.routes.connectors as routes_mod

    fake_creds = MagicMock()
    fake_creds.token = "ya29.fresh-access-token"
    fake_creds.expiry = None

    with patch.object(routes_mod, "_refresh_credentials", return_value=fake_creds):
        r = ctx.client.get("/v1/connectors/google-drive/picker-token")

    assert r.status_code == 200, r.text
    assert r.json()["expires_in"] == 3000


def test_picker_token_not_connected_returns_404(google_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/connectors/google-drive/picker-token")
    assert r.status_code == 404


def test_picker_token_app_id_derived_from_client_id_project_prefix(
    google_env, monkeypatch
):
    # google_env defaults GOOGLE_CLIENT_ID to "test-client-id", which isn't
    # shaped like a real OAuth client id and wouldn't exercise the
    # project-number-prefix split. Override it here with a realistically
    # shaped id and reload the same chain the fixture itself reloads.
    monkeypatch.setenv(
        "GOOGLE_CLIENT_ID",
        "393002598266-abc.apps.googleusercontent.com",
    )
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.google_oauth",
        "app.routes.connectors",
    ):
        importlib.reload(sys.modules[name])

    ctx = company_client(monkeypatch)
    _seed_drive_connection(ctx.company_id, config_json='{"dataset":"acme"}')

    from datetime import datetime, timedelta, timezone

    import app.routes.connectors as routes_mod

    fake_creds = MagicMock()
    fake_creds.token = "ya29.fresh-access-token"
    fake_creds.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=1800
    )

    with patch.object(routes_mod, "_refresh_credentials", return_value=fake_creds):
        r = ctx.client.get("/v1/connectors/google-drive/picker-token")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["app_id"] == "393002598266"
    assert body["access_token"] == "ya29.fresh-access-token"


def test_picker_token_app_id_empty_when_client_id_unset(google_env, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.google_oauth",
        "app.routes.connectors",
    ):
        importlib.reload(sys.modules[name])

    ctx = company_client(monkeypatch)
    _seed_drive_connection(ctx.company_id, config_json='{"dataset":"acme"}')

    from datetime import datetime, timedelta, timezone

    import app.routes.connectors as routes_mod

    fake_creds = MagicMock()
    fake_creds.token = "ya29.fresh-access-token"
    fake_creds.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=1800
    )

    with patch.object(routes_mod, "_refresh_credentials", return_value=fake_creds):
        r = ctx.client.get("/v1/connectors/google-drive/picker-token")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["app_id"] == ""
    assert body["access_token"] == "ya29.fresh-access-token"


def test_picker_token_app_id_empty_when_client_id_malformed(google_env, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "noHyphenClientId")
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.google_oauth",
        "app.routes.connectors",
    ):
        importlib.reload(sys.modules[name])

    ctx = company_client(monkeypatch)
    _seed_drive_connection(ctx.company_id, config_json='{"dataset":"acme"}')

    from datetime import datetime, timedelta, timezone

    import app.routes.connectors as routes_mod

    fake_creds = MagicMock()
    fake_creds.token = "ya29.fresh-access-token"
    fake_creds.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=1800
    )

    with patch.object(routes_mod, "_refresh_credentials", return_value=fake_creds):
        r = ctx.client.get("/v1/connectors/google-drive/picker-token")

    assert r.status_code == 200, r.text
    assert r.json()["app_id"] == ""


# ─── unpicking a Drive file drops it from the document catalog ───────────────
#
# `drive_extract` upserts a catalog row per synced file and nothing ever
# removed one, so a file the user unpicked stayed catalogued forever: still
# listed to the model as a document this workspace has, still rankable, and —
# since document resolution shipped — still eligible to be asserted as the
# subject of a question, whereupon the body fetch fails and the user is told
# the contents "could not be loaded".
#
# The reachable set is BOTH halves of the stored selection: `config["files"]`
# (what the Picker returned) UNION `config["folder_contents"]` (what a picked
# folder expanded to at the last sync). Reading only the first would leave
# every folder-sourced document behind.


def _seed_drive_docs(company_id: str, file_ids: list[str]) -> None:
    from app.db.client import require_client

    for fid in file_ids:
        require_client().table("document_catalog").insert({
            "company_id": company_id,
            "provider": "google_drive",
            "external_id": fid,
            "title": fid,
            "source_name": "Google Drive",
            "content_hash": f"h-{fid}",
            "summary": "s",
            "topics": [],
        }).execute()


def _drive_catalogued(company_id: str) -> set[str]:
    from app.db.client import require_client

    rows = (
        require_client().table("document_catalog").select("external_id")
        .eq("company_id", company_id).eq("provider", "google_drive")
        .execute().data
    )
    return {r["external_id"] for r in rows}


def _fake_drive_sync(folder_contents=None):
    """Stand-in for sync_google_drive that persists the new picked list the
    way the real one does. `folder_contents` is what the walk WOULD have
    found — passed so a test can make the walk return less than the stored
    expansion and prove that cannot widen a deletion."""
    def _sync(*, company_id, dataset, files=None, **kw):
        from app import db as _db
        from app.connectors.google_drive_sync import (
            merge_config,
            normalize_picked_files,
        )

        row = _db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
        patch = {"dataset": dataset or "acme"}
        if files is not None:
            patch["files"] = normalize_picked_files(files)
        if folder_contents is not None:
            patch["folder_contents"] = folder_contents
        merge_config(row, patch)
        result = MagicMock()
        result.to_dict.return_value = {"dataset": "acme", "synced": [],
                                       "skipped": [], "errors": []}
        return result

    return _sync


def _post_files(ctx, ids, monkeypatch_target, sync=None):
    import app.routes.connectors as routes_mod

    with (
        patch.object(routes_mod, "sync_google_drive",
                     side_effect=sync or _fake_drive_sync()),
        patch.object(routes_mod.db, "upsert_input_source", return_value={}),
        patch.object(routes_mod, "_seed_corpus_after_sync", return_value=None),
    ):
        return ctx.client.post(
            "/v1/connectors/google-drive/files",
            json={"files": [{"id": i} for i in ids], "dataset": "acme"},
        )


def test_unpicking_a_file_drops_it_from_the_catalog(google_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_drive_connection(
        ctx.company_id,
        config_json='{"dataset":"acme","files":['
                    '{"id":"file0001","name":"a"},{"id":"file0002","name":"b"}]}',
    )
    _seed_drive_docs(ctx.company_id, ["file0001", "file0002"])

    r = _post_files(ctx, ["file0002"], monkeypatch)

    assert r.status_code == 200, r.text
    assert _drive_catalogued(ctx.company_id) == {"file0002"}, (
        "the unpicked file is still catalogued — it will keep being offered "
        "as a document this workspace has"
    )


def test_unpicking_a_folder_drops_the_files_it_had_expanded_to(
    google_env, monkeypatch
):
    """The half a naive fix misses. A file inside a picked FOLDER has a
    catalog row and is named nowhere in `config["files"]` — only in the
    folder's stored expansion. For anyone who connected a folder rather than
    individual files, that is every document they have."""
    ctx = company_client(monkeypatch)
    _seed_drive_connection(
        ctx.company_id,
        config_json='{"dataset":"acme",'
                    '"files":[{"id":"folder0001","name":"Specs"},'
                    '{"id":"file0009","name":"keep"}],'
                    '"folder_contents":{"folder0001":['
                    '{"id":"file0001","name":"a","parentId":"folder0001"},'
                    '{"id":"file0002","name":"b","parentId":"folder0001"}]}}',
    )
    _seed_drive_docs(ctx.company_id, ["file0001", "file0002", "file0009"])

    r = _post_files(ctx, ["file0009"], monkeypatch)

    assert r.status_code == 200, r.text
    assert _drive_catalogued(ctx.company_id) == {"file0009"}, (
        "unpicking the folder left its descendants catalogued — the picked "
        "list never named them, so only the stored expansion can"
    )


def test_a_file_still_reachable_from_a_kept_folder_survives(
    google_env, monkeypatch
):
    """Drive lets one file sit in two folders. Removing one of them is not
    removing the file, so the retained selection is subtracted from the
    removed one rather than the removals being taken at face value."""
    ctx = company_client(monkeypatch)
    _seed_drive_connection(
        ctx.company_id,
        config_json='{"dataset":"acme",'
                    '"files":[{"id":"folder0001"},{"id":"folder0002"}],'
                    '"folder_contents":{'
                    '"folder0001":[{"id":"file0001","parentId":"folder0001"},'
                    '{"id":"file0002","parentId":"folder0001"}],'
                    '"folder0002":[{"id":"file0001","parentId":"folder0002"}]}}',
    )
    _seed_drive_docs(ctx.company_id, ["file0001", "file0002"])

    r = _post_files(ctx, ["folder0002"], monkeypatch)

    assert r.status_code == 200, r.text
    assert _drive_catalogued(ctx.company_id) == {"file0001"}, (
        "a file reachable from a folder the user KEPT was deleted because "
        "another folder holding it was removed"
    )


def test_re_saving_the_same_selection_deletes_nothing(google_env, monkeypatch):
    """The common save — the Picker re-posts what is already stored. Nothing
    left the selection, so nothing may leave the catalog."""
    ctx = company_client(monkeypatch)
    _seed_drive_connection(
        ctx.company_id,
        config_json='{"dataset":"acme","files":[{"id":"file0001"},'
                    '{"id":"file0002"}]}',
    )
    _seed_drive_docs(ctx.company_id, ["file0001", "file0002"])

    r = _post_files(ctx, ["file0001", "file0002"], monkeypatch)

    assert r.status_code == 200, r.text
    assert _drive_catalogued(ctx.company_id) == {"file0001", "file0002"}


def test_a_short_folder_walk_cannot_widen_the_deletion(google_env, monkeypatch):
    """THE SAFETY PROPERTY, at the Drive call site.

    A folder walk that comes back with two of a folder's three files is what a
    `files.list` 403, a rate limit or an expired token mid-pagination looks
    like — and it is indistinguishable from a folder that genuinely shrank.
    So the removal set is computed BEFORE the sync, from the stored selection
    against the posted one, and the sync's own walk never contributes to it:
    here the selection did not change at all, so a walk that returned almost
    nothing must delete nothing.

    Get this wrong and one transient Drive error deletes a tenant's catalog.
    """
    ctx = company_client(monkeypatch)
    _seed_drive_connection(
        ctx.company_id,
        config_json='{"dataset":"acme","files":[{"id":"folder0001"}],'
                    '"folder_contents":{"folder0001":['
                    '{"id":"file0001","parentId":"folder0001"},'
                    '{"id":"file0002","parentId":"folder0001"},'
                    '{"id":"file0003","parentId":"folder0001"}]}}',
    )
    _seed_drive_docs(ctx.company_id, ["file0001", "file0002", "file0003"])

    # Same selection; the walk this time reaches one file out of three.
    r = _post_files(
        ctx, ["folder0001"], monkeypatch,
        sync=_fake_drive_sync(folder_contents={
            "folder0001": [{"id": "file0001", "parentId": "folder0001"}]
        }),
    )

    assert r.status_code == 200, r.text

    # PROVE THE SCENARIO WAS BUILT. Everything below is only meaningful if the
    # stored expansion really did shrink — that IS the partial walk. If the
    # fake sync stopped persisting `folder_contents`, the config would keep
    # its full three-file expansion, nothing would look removed under ANY
    # implementation, and this test would pass while asserting nothing. It
    # would also stop killing the reconcile-against-the-walk mutation, and
    # nothing would report that it had gone quiet.
    import json as _json

    from app import db as _db

    cfg = _json.loads(
        _db.get_connection(
            ctx.company_id, google_oauth.GOOGLE_DRIVE_PROVIDER
        )["config_json"]
    )
    assert [n["id"] for n in cfg["folder_contents"]["folder0001"]] == [
        "file0001"
    ], (
        "the stored expansion did not shrink, so no partial walk happened — "
        "this test is not exercising what its name claims"
    )

    assert _drive_catalogued(ctx.company_id) == {
        "file0001", "file0002", "file0003"
    }, (
        "a partial folder walk deleted catalog rows — the deletion is reading "
        "a sync result, which makes a transient Drive failure destructive"
    )


def test_a_rejected_save_deletes_nothing(google_env, monkeypatch):
    """The cleanup runs only after the save actually happened. A malformed id
    400s before anything persists, so the stored selection is unchanged — and
    a user whose selection did not change must not lose documents from it."""
    ctx = company_client(monkeypatch)
    _seed_drive_connection(
        ctx.company_id,
        config_json='{"dataset":"acme","files":[{"id":"file0001"},'
                    '{"id":"file0002"}]}',
    )
    _seed_drive_docs(ctx.company_id, ["file0001", "file0002"])

    r = ctx.client.post(
        "/v1/connectors/google-drive/files",
        json={"files": [{"id": "bad id!"}], "dataset": "acme"},
    )

    assert r.status_code == 400, r.text
    assert _drive_catalogued(ctx.company_id) == {"file0001", "file0002"}


def test_a_drive_catalog_failure_never_fails_the_save(google_env, monkeypatch):
    """Cleanup behind a save that has already committed. If the delete throws,
    the user's picked files must still persist and the response must still be
    a success."""
    import json as _json

    from app import db as _db, document_catalog

    ctx = company_client(monkeypatch)
    _seed_drive_connection(
        ctx.company_id,
        config_json='{"dataset":"acme","files":[{"id":"file0001"},'
                    '{"id":"file0002"}]}',
    )

    def _boom(*a, **kw):
        raise RuntimeError("postgrest down")

    monkeypatch.setattr(document_catalog, "deregister_documents", _boom)

    r = _post_files(ctx, ["file0002"], monkeypatch)

    assert r.status_code == 200, r.text
    cfg = _json.loads(
        _db.get_connection(
            ctx.company_id, google_oauth.GOOGLE_DRIVE_PROVIDER
        )["config_json"]
    )
    assert [f["id"] for f in cfg["files"]] == ["file0002"]

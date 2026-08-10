"""Tests for the /v1/connectors/google-drive service-account mode routes.

`GOOGLE_DRIVE_ACCESS_MODE` gates this whole surface: default "oauth" leaves
the mode-report endpoint reporting "oauth" and the SA routes inert (400, no
IAM/Drive call ever made); "service_account" (plus a configured bootstrap
credential) activates provisioning + scan.

Also proves the security property required of this mode: the SA private key
never reaches the client — `GET /v1/connectors` is serialized through an
explicit allowlist that excludes `sa_key_encrypted`.
"""
import importlib
import json
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from app.connectors import google_oauth
from tests._company_helpers import company_client


def _reload_for(monkeypatch, *, mode: str, project: str = "", key_json: str = ""):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GOOGLE_DRIVE_ACCESS_MODE", mode)
    monkeypatch.setenv("GCP_SA_BOOTSTRAP_PROJECT", project)
    monkeypatch.setenv("GCP_SA_BOOTSTRAP_KEY_JSON", key_json)
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.google_service_account",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


def _reload_env(monkeypatch, *, mode: str, project: str = "", key_json: str = ""):
    """Set env + reload, yield, then reload back to the oauth default.

    `monkeypatch.setenv` only reverts the environment VARIABLE at fixture
    teardown — it never re-runs the `importlib.reload` these tests use to
    make the `app.config.settings` singleton pick the value up. Without an
    explicit reload-back, the singleton stays pinned to whatever mode the
    last test here selected, leaking into every later test in the same
    pytest worker that reads `app.config.settings` without reloading it
    itself (e.g. `app.kg_ingest.auto_sync._run_drive_sync`). Restoring here,
    before `monkeypatch`'s own teardown reverts the env vars, closes that
    leak regardless of what the next test does.
    """
    _reload_for(monkeypatch, mode=mode, project=project, key_json=key_json)
    yield
    _reload_for(monkeypatch, mode="oauth")


@pytest.fixture
def oauth_mode_env(isolated_settings, monkeypatch):
    """The default posture: SA mode off, nothing configured. Every commit of
    this feature must be safe to merge and deploy while still landing here."""
    yield from _reload_env(monkeypatch, mode="oauth")


@pytest.fixture
def sa_mode_env(isolated_settings, monkeypatch):
    yield from _reload_env(
        monkeypatch,
        mode="service_account",
        project="test-project",
        key_json=json.dumps({"type": "service_account", "project_id": "test-project"}),
    )


@pytest.fixture
def sa_mode_unconfigured_env(isolated_settings, monkeypatch):
    """service_account is REQUESTED but the bootstrap credential is missing —
    a distinct, reportable state from "oauth mode"."""
    yield from _reload_env(monkeypatch, mode="service_account")


# ─────────────────────── mode toggle ───────────────────────


def test_mode_defaults_to_oauth(oauth_mode_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/connectors/google-drive/mode")
    assert r.status_code == 200
    assert r.json() == {"mode": "oauth", "service_account_configured": False}


def test_mode_reports_service_account_when_active_and_configured(
    sa_mode_env, monkeypatch
):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/connectors/google-drive/mode")
    assert r.status_code == 200
    assert r.json() == {
        "mode": "service_account",
        "service_account_configured": True,
    }


def test_mode_service_account_requested_but_not_configured(
    sa_mode_unconfigured_env, monkeypatch
):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/connectors/google-drive/mode")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "service_account"
    assert body["service_account_configured"] is False


# ─────────────────────── SA routes inert in oauth mode ───────────────────────


def test_provision_route_400_when_mode_is_oauth(oauth_mode_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/connectors/google-drive/service-account")
    assert r.status_code == 400
    # Never touched IAM: the endpoint 400s before any GCP call is attempted.


def test_scan_route_400_when_mode_is_oauth(oauth_mode_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/google-drive/service-account/scan")
    assert r.status_code == 400


def test_provision_route_400_when_service_account_mode_not_configured(
    sa_mode_unconfigured_env, monkeypatch
):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/connectors/google-drive/service-account")
    assert r.status_code == 400
    assert "GCP_SA_BOOTSTRAP" in r.text


# ─────────────────────── SA routes active in service_account mode ────────────


def test_provision_route_mints_and_returns_state(sa_mode_env, monkeypatch):
    ctx = company_client(monkeypatch)
    import app.routes.connectors as routes_mod

    with patch.object(
        routes_mod.google_service_account,
        "mint_company_service_account",
        return_value="sprntly-abc123-def456@test-project.iam.gserviceaccount.com",
    ) as mint:
        r = ctx.client.get("/v1/connectors/google-drive/service-account")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["service_account_email"] == (
        "sprntly-abc123-def456@test-project.iam.gserviceaccount.com"
    )
    assert body["folder_contents"] == {}
    assert body["shared_roots"] == []
    mint.assert_called_once_with(ctx.company_id)


def test_provision_route_requires_admin(sa_mode_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.db.authcache import invalidate_user
    from app.db.client import require_client

    require_client().table("company_members").update({"role": "member"}).eq(
        "company_id", ctx.company_id
    ).eq("user_id", ctx.user_id).execute()
    invalidate_user(ctx.user_id)

    r = ctx.client.get("/v1/connectors/google-drive/service-account")
    assert r.status_code == 403


def test_scan_route_calls_sync_and_returns_merged_state(sa_mode_env, monkeypatch):
    ctx = company_client(monkeypatch)
    import app.routes.connectors as routes_mod
    from app import db

    db.upsert_connection(
        company_id=ctx.company_id,
        provider=google_oauth.GOOGLE_DRIVE_PROVIDER,
        token_encrypted="",
        scopes="",
        config_json=json.dumps(
            {
                "service_account_email": "sa@test-project.iam.gserviceaccount.com",
                "sa_shared_roots": [{"id": "folder1", "name": "Shared"}],
                "folder_contents": {"folder1": [{"id": "f1", "name": "a.txt"}]},
            }
        ),
    )
    fake_result = MagicMock()
    fake_result.to_dict.return_value = {"ingested": [], "skipped": [], "errors": []}

    with (
        patch.object(
            routes_mod.google_service_account,
            "sync_service_account",
            return_value=fake_result,
        ) as scan,
        patch.object(routes_mod, "_auto_enable_drive_input_source"),
        patch.object(routes_mod, "_seed_corpus_after_sync"),
    ):
        r = ctx.client.post("/v1/connectors/google-drive/service-account/scan")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["service_account_email"] == "sa@test-project.iam.gserviceaccount.com"
    assert body["shared_roots"] == [{"id": "folder1", "name": "Shared"}]
    assert body["folder_contents"] == {"folder1": [{"id": "f1", "name": "a.txt"}]}
    scan.assert_called_once()
    assert scan.call_args.args[0] == ctx.company_id


def test_scan_route_requires_admin(sa_mode_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.db.authcache import invalidate_user
    from app.db.client import require_client

    require_client().table("company_members").update({"role": "member"}).eq(
        "company_id", ctx.company_id
    ).eq("user_id", ctx.user_id).execute()
    invalidate_user(ctx.user_id)

    r = ctx.client.post("/v1/connectors/google-drive/service-account/scan")
    assert r.status_code == 403


# ─────────────────────── security: SA key never reaches the client ───────────


def test_sa_key_absent_from_client_connections_response(sa_mode_env, monkeypatch):
    ctx = company_client(monkeypatch)
    import base64

    import app.routes.connectors as routes_mod

    # Mint through the REAL code path (mint_company_service_account, the IAM
    # create + keys().create flow, encryption, storage) with only the network
    # boundary (the IAM Resource) stubbed, so the key that lands in
    # sa_key_encrypted is a real secret string we can assert is absent from
    # the client response.
    iam = MagicMock()
    iam.projects().serviceAccounts().create().execute.return_value = {}
    iam.projects().serviceAccounts().keys().create().execute.return_value = {
        "privateKeyData": base64.b64encode(
            b'{"THE-SECRET-SA-PRIVATE-KEY-MATERIAL": true}'
        ).decode(),
    }
    with patch.object(
        routes_mod.google_service_account,
        "_bootstrap_iam_service",
        return_value=iam,
    ):
        r = ctx.client.get("/v1/connectors/google-drive/service-account")
        assert r.status_code == 200, r.text

    # The mint really did store a key, so this test would be vacuous otherwise.
    from app import db

    row = db.get_connection(ctx.company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    assert row["sa_key_encrypted"]

    listed = ctx.client.get("/v1/connectors")
    assert listed.status_code == 200
    body_text = listed.text
    conn = listed.json()["connections"][0]

    # Neither the field name nor the (secret, base64-decodable) key VALUE
    # appears anywhere in the serialized response.
    assert "sa_key_encrypted" not in conn
    assert "sa_key_encrypted" not in body_text
    assert "THE-SECRET-SA-PRIVATE-KEY-MATERIAL" not in body_text

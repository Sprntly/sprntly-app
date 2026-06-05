"""Tests for the multitenant connections helpers (commit 2).

Schema-level invariants (FK, composite unique, NOT NULL) live in
test_db_connections_schema.py. This file proves the Python helper
contract:
  * every helper requires company_id (signature)
  * one provider can exist independently in two workspaces
  * reads/writes never cross a workspace boundary
"""
import importlib
import sys
import uuid

import pytest
from cryptography.fernet import Fernet

from app import db
from app.connectors.tokens import decrypt_token_json, encrypt_token_json
from app.db.client import require_client


def _seed_company(slug: str = "acme") -> str:
    cid = uuid.uuid4().hex
    require_client().table("companies").insert(
        {"id": cid, "slug": slug, "display_name": slug.title()}
    ).execute()
    return cid


def _setup_fernet(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    importlib.reload(sys.modules["app.config"])
    importlib.reload(sys.modules["app.connectors.tokens"])


def test_upsert_and_get_roundtrip_for_one_workspace(isolated_settings, monkeypatch):
    _setup_fernet(monkeypatch)
    ws = _seed_company("acme")

    cipher = encrypt_token_json('{"token":"t"}')
    row = db.upsert_connection(
        company_id=ws,
        provider="google_drive",
        token_encrypted=cipher,
        scopes="drive.readonly",
        google_email="user@example.com",
        config_json='{"dataset":"acme"}',
    )
    assert row["company_id"] == ws
    assert row["provider"] == "google_drive"
    assert row["google_email"] == "user@example.com"

    loaded = db.get_connection(ws, "google_drive")
    assert loaded is not None
    assert decrypt_token_json(loaded["token_json_encrypted"]) == '{"token":"t"}'

    assert len(db.list_connections(ws)) == 1

    db.update_connection_sync(ws, "google_drive", last_sync_error=None)
    again = db.get_connection(ws, "google_drive")
    assert again["last_sync_at"] is not None

    assert db.delete_connection(ws, "google_drive")
    assert db.get_connection(ws, "google_drive") is None


def test_same_provider_in_two_workspaces_does_not_collide(
    isolated_settings, monkeypatch
):
    _setup_fernet(monkeypatch)
    ws1 = _seed_company("acme")
    ws2 = _seed_company("globex")
    cipher = encrypt_token_json('{"token":"t"}')

    r1 = db.upsert_connection(
        company_id=ws1,
        provider="figma",
        token_encrypted=cipher,
        scopes="files:read",
        account_label="alice@acme.test",
    )
    r2 = db.upsert_connection(
        company_id=ws2,
        provider="figma",
        token_encrypted=cipher,
        scopes="files:read",
        account_label="bob@globex.test",
    )

    assert r1["id"] != r2["id"]
    assert r1["account_label"] == "alice@acme.test"
    assert r2["account_label"] == "bob@globex.test"


def test_get_is_scoped_to_workspace(isolated_settings, monkeypatch):
    """ws1 must not be able to see ws2's connection, even with the same
    provider name. This is the cross-tenant leak we're closing."""
    _setup_fernet(monkeypatch)
    ws1 = _seed_company("acme")
    ws2 = _seed_company("globex")
    cipher = encrypt_token_json('{"token":"t"}')

    db.upsert_connection(
        company_id=ws2,
        provider="figma",
        token_encrypted=cipher,
        scopes="",
        account_label="bob@globex.test",
    )

    assert db.get_connection(ws1, "figma") is None
    found = db.get_connection(ws2, "figma")
    assert found is not None
    assert found["account_label"] == "bob@globex.test"


def test_list_is_scoped_to_workspace(isolated_settings, monkeypatch):
    _setup_fernet(monkeypatch)
    ws1 = _seed_company("acme")
    ws2 = _seed_company("globex")
    cipher = encrypt_token_json('{"token":"t"}')
    db.upsert_connection(
        company_id=ws1, provider="figma", token_encrypted=cipher, scopes=""
    )
    db.upsert_connection(
        company_id=ws1, provider="github", token_encrypted=cipher, scopes=""
    )
    db.upsert_connection(
        company_id=ws2, provider="figma", token_encrypted=cipher, scopes=""
    )

    assert sorted(c["provider"] for c in db.list_connections(ws1)) == [
        "figma",
        "github",
    ]
    assert sorted(c["provider"] for c in db.list_connections(ws2)) == ["figma"]


def test_delete_does_not_touch_other_workspaces(isolated_settings, monkeypatch):
    _setup_fernet(monkeypatch)
    ws1 = _seed_company("acme")
    ws2 = _seed_company("globex")
    cipher = encrypt_token_json('{"token":"t"}')
    db.upsert_connection(
        company_id=ws1, provider="figma", token_encrypted=cipher, scopes=""
    )
    db.upsert_connection(
        company_id=ws2, provider="figma", token_encrypted=cipher, scopes=""
    )

    db.delete_connection(ws1, "figma")

    assert db.get_connection(ws1, "figma") is None
    assert db.get_connection(ws2, "figma") is not None


def test_patch_config_is_scoped_to_workspace(isolated_settings, monkeypatch):
    _setup_fernet(monkeypatch)
    ws1 = _seed_company("acme")
    ws2 = _seed_company("globex")
    cipher = encrypt_token_json('{"token":"t"}')
    db.upsert_connection(
        company_id=ws1,
        provider="google_drive",
        token_encrypted=cipher,
        scopes="",
        config_json='{"folder_id":"a"}',
    )
    db.upsert_connection(
        company_id=ws2,
        provider="google_drive",
        token_encrypted=cipher,
        scopes="",
        config_json='{"folder_id":"b"}',
    )

    db.patch_connection_config(ws1, "google_drive", {"folder_id": "A2"})

    assert db.get_connection(ws1, "google_drive")["config_json"] == '{"folder_id": "A2"}'
    # ws2 untouched
    assert db.get_connection(ws2, "google_drive")["config_json"] == '{"folder_id": "b"}'


def test_helpers_reject_missing_company_id(isolated_settings):
    """Type-system guard rail — silent defaults are how this bug came back."""
    with pytest.raises(TypeError):
        db.get_connection("figma")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        db.list_connections()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        db.delete_connection("figma")  # type: ignore[call-arg]

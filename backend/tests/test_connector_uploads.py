"""Tests for the uploaded-documents connector (`uploads`).

The user's own business documents as a first-class connector: they name a
source, optionally describe what the documents are, and attach files of any
type. The point of the design is that NOTHING downstream special-cases it —
so these tests assert it behaves like every other provider at each seam:

  * catalog        — classified, and evidence-bearing (unlike Notion/Drive)
  * store          — any file type accepted, extraction degrades gracefully
  * puller         — RawRecords carrying the user's name + description
  * runner         — registered in PULLERS with the (fn, key, hint) shape
  * routes         — create / add / list / delete, admin-gated, tenant-scoped
  * brief gate     — an uploads source satisfies has_brief_data_source through
                     the ORDINARY evidence-connection path
"""
from __future__ import annotations

import importlib
import io
import sys
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from tests._company_helpers import company_client


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.uploads",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def uploads_env(isolated_settings, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    yield


def _file(name: str, body: bytes = b"hello world") -> tuple:
    return ("files", (name, io.BytesIO(body), "application/octet-stream"))


# ─────────────────────────── Catalog ───────────────────────────


def test_uploads_is_classified_and_evidence_bearing():
    from app.connectors.catalog import is_evidence_provider, types_for

    assert types_for("uploads") == ["documents"]
    # Typed `documents` like Notion/Drive, but the user deliberately handing us
    # a named corpus IS evidence — so it can drive a brief and they can't.
    assert is_evidence_provider("uploads")
    assert not is_evidence_provider("notion")
    assert not is_evidence_provider("google_drive")


def test_uploads_registered_in_pullers_with_the_standard_tuple_shape():
    from app.kg_ingest.runner import PULLERS

    assert "uploads" in PULLERS
    fn, credential_field, hint = PULLERS["uploads"]
    assert callable(fn)
    # The "credential" is the owning company id — no secret, but stored and
    # decrypted through the same path as every other provider's token.
    assert credential_field == "company_id"
    assert hint


def test_token_for_extracts_the_company_id():
    from app.kg_ingest.runner import token_for

    assert token_for("uploads", {"company_id": "co-1"}) == "co-1"
    with pytest.raises(ValueError):
        token_for("uploads", {})


# ─────────────────────────── Store ───────────────────────────


def test_store_extracts_text_and_keeps_any_file_type(isolated_settings):
    from app.document_sources import (
        add_document_file,
        list_document_sources,
        list_source_files,
    )

    src = _seed_source(isolated_settings, name="Churn research",
                       description="12 enterprise churn interviews")
    add_document_file(_CID, src.id, filename="notes.md",
                      data=b"# Findings\nUsers churn on SSO.")
    # A binary type has no rich converter: it must be STORED and stubbed, never
    # rejected (app.ingest.fallback_to_md).
    add_document_file(_CID, src.id, filename="scan.png", data=b"\x89PNG\x00\x01")

    files = list_source_files(_CID, src.id)
    assert [f.filename for f in files] == ["notes.md", "scan.png"]
    assert "Users churn on SSO" in files[0].extracted_text
    assert "not yet parsed" in files[1].extracted_text

    sources = list_document_sources(_CID)
    assert len(sources) == 1
    assert sources[0].name == "Churn research"
    assert sources[0].file_count == 2


def test_store_extraction_failure_degrades_to_empty_text(isolated_settings):
    from app.document_sources import add_document_file, list_source_files

    src = _seed_source(isolated_settings)
    with patch("app.document_sources.convert", side_effect=RuntimeError("boom")):
        saved = add_document_file(_CID, src.id, filename="broken.pdf", data=b"%PDF-x")
    assert saved.extracted_text == ""
    # The upload still landed — the original bytes are never lost.
    assert [f.filename for f in list_source_files(_CID, src.id)] == ["broken.pdf"]


def test_reads_are_tenant_scoped(isolated_settings):
    from app.document_sources import (
        delete_document_source,
        get_document_source,
        list_document_sources,
    )

    src = _seed_source(isolated_settings)
    assert get_document_source("some-other-company", src.id) is None
    assert list_document_sources("some-other-company") == []
    assert delete_document_source("some-other-company", src.id) is False
    assert len(list_document_sources(_CID)) == 1


# ─────────────────────────── Puller ───────────────────────────


def test_puller_carries_the_user_supplied_name_and_description(isolated_settings):
    from app.document_sources import add_document_file
    from app.kg_ingest.pullers import uploads as uploads_puller

    src = _seed_source(
        isolated_settings,
        name="Q3 NPS verbatims",
        description="Free-text answers from churned enterprise accounts.",
    )
    add_document_file(_CID, src.id, filename="nps.md", data=b"Pricing is opaque.")

    recs = list(uploads_puller.pull(_CID))
    assert len(recs) == 1
    r = recs[0]
    assert (r.provider, r.kind) == ("uploads", "document")
    assert r.title == "Q3 NPS verbatims — nps.md"
    assert r.properties["source_name"] == "Q3 NPS verbatims"
    assert "churned enterprise" in r.properties["source_description"]
    assert "Pricing is opaque" in r.text
    # The rendering the extractor actually sees carries the context too.
    assert "source_name=Q3 NPS verbatims" in r.render()


def test_puller_chunks_long_documents_into_batchable_records(isolated_settings):
    from app.document_sources import add_document_file
    from app.kg_ingest.pullers import uploads as uploads_puller

    src = _seed_source(isolated_settings)
    add_document_file(_CID, src.id, filename="big.txt", data=b"x" * 10_000)

    recs = list(uploads_puller.pull(_CID))
    assert len(recs) == 3  # 10_000 chars / 4000-char chunks
    assert [r.external_id.rsplit(":", 1)[1] for r in recs] == ["0", "1", "2"]
    assert "part 1/3" in recs[0].title


def test_puller_skips_files_with_no_extractable_text(isolated_settings):
    from app.document_sources import add_document_file
    from app.kg_ingest.pullers import uploads as uploads_puller

    src = _seed_source(isolated_settings)
    with patch("app.document_sources.convert", return_value=""):
        add_document_file(_CID, src.id, filename="empty.bin", data=b"\x00\x01")
    assert list(uploads_puller.pull(_CID)) == []


def test_puller_returns_nothing_for_a_company_with_no_sources(isolated_settings):
    from app.kg_ingest.pullers import uploads as uploads_puller

    assert list(uploads_puller.pull("co-with-nothing")) == []


# ─────────────────────────── Routes ───────────────────────────


def test_create_source_requires_auth(unauth_client, uploads_env):
    r = unauth_client.post(
        "/v1/connectors/uploads/sources",
        data={"name": "Research"},
        files=[_file("a.md")],
    )
    assert r.status_code == 401


def test_create_source_stores_files_and_connects_the_provider(uploads_env, monkeypatch):
    ctx = company_client(monkeypatch)

    r = ctx.client.post(
        "/v1/connectors/uploads/sources",
        data={"name": "Churn research", "description": "Why enterprise left."},
        files=[_file("notes.md", b"# SSO gaps"), _file("data.csv", b"a,b\n1,2\n")],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["source"]["name"] == "Churn research"
    assert body["source"]["description"] == "Why enterprise left."
    assert body["source"]["file_count"] == 2
    assert {f["filename"] for f in body["source"]["files"]} == {"notes.md", "data.csv"}
    # Extracted chars are surfaced; the text itself never is.
    assert all(f["extracted_chars"] > 0 for f in body["source"]["files"])
    assert all("extracted_text" not in f for f in body["source"]["files"])

    # The connector is now Active, like any other connect flow.
    listed = ctx.client.get("/v1/connectors").json()["connections"]
    rows = [c for c in listed if c["provider"] == "uploads"]
    assert len(rows) == 1
    assert rows[0]["status"] == "active"
    assert rows[0]["types"] == ["documents"]


def test_create_source_requires_a_name(uploads_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/uploads/sources",
        data={"name": "   "},
        files=[_file("a.md")],
    )
    assert r.status_code == 422
    assert not ctx.client.get("/v1/connectors/uploads/sources").json()["sources"]


def test_description_is_optional(uploads_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/uploads/sources",
        data={"name": "Pricing deck"},
        files=[_file("deck.txt", b"tiering")],
    )
    assert r.status_code == 200, r.text
    assert r.json()["source"]["description"] == ""


def test_oversized_file_is_reported_not_fatal(uploads_env, monkeypatch):
    from app.routes import connectors as connectors_routes

    ctx = company_client(monkeypatch)
    monkeypatch.setattr(connectors_routes, "UPLOAD_MAX_FILE_BYTES", 16)

    r = ctx.client.post(
        "/v1/connectors/uploads/sources",
        data={"name": "Mixed"},
        files=[_file("small.md", b"ok"), _file("huge.md", b"y" * 64)],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [f["filename"] for f in body["source"]["files"]] == ["small.md"]
    assert body["errors"][0]["filename"] == "huge.md"
    assert "limit" in body["errors"][0]["error"]


def test_source_is_not_created_when_no_file_lands(uploads_env, monkeypatch):
    from app.routes import connectors as connectors_routes

    ctx = company_client(monkeypatch)
    monkeypatch.setattr(connectors_routes, "UPLOAD_MAX_FILE_BYTES", 1)

    r = ctx.client.post(
        "/v1/connectors/uploads/sources",
        data={"name": "All too big"},
        files=[_file("huge.md", b"y" * 64)],
    )
    assert r.status_code == 400
    assert ctx.client.get("/v1/connectors/uploads/sources").json()["sources"] == []
    listed = ctx.client.get("/v1/connectors").json()["connections"]
    assert not any(c["provider"] == "uploads" for c in listed)


def test_add_files_to_an_existing_source(uploads_env, monkeypatch):
    ctx = company_client(monkeypatch)
    created = ctx.client.post(
        "/v1/connectors/uploads/sources",
        data={"name": "Research"},
        files=[_file("one.md", b"one")],
    ).json()["source"]

    r = ctx.client.post(
        f"/v1/connectors/uploads/sources/{created['id']}/files",
        files=[_file("two.md", b"two")],
    )
    assert r.status_code == 200, r.text
    assert r.json()["source"]["file_count"] == 2


def test_add_files_404s_for_another_tenants_source(uploads_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/uploads/sources/00000000-0000-0000-0000-000000000000/files",
        files=[_file("x.md")],
    )
    assert r.status_code == 404


def test_delete_last_source_drops_the_connection(uploads_env, monkeypatch):
    ctx = company_client(monkeypatch)
    created = ctx.client.post(
        "/v1/connectors/uploads/sources",
        data={"name": "Research"},
        files=[_file("one.md", b"one")],
    ).json()["source"]

    r = ctx.client.delete(f"/v1/connectors/uploads/sources/{created['id']}")
    assert r.status_code == 200
    assert ctx.client.get("/v1/connectors/uploads/sources").json()["sources"] == []
    listed = ctx.client.get("/v1/connectors").json()["connections"]
    assert not any(c["provider"] == "uploads" for c in listed)


def test_delete_source_404_when_unknown(uploads_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.delete("/v1/connectors/uploads/sources/nope")
    assert r.status_code == 404


def test_disconnect_keeps_the_documents(uploads_env, monkeypatch):
    ctx = company_client(monkeypatch)
    ctx.client.post(
        "/v1/connectors/uploads/sources",
        data={"name": "Research"},
        files=[_file("one.md", b"one")],
    )

    r = ctx.client.delete("/v1/connectors/uploads")
    assert r.status_code == 200
    listed = ctx.client.get("/v1/connectors").json()["connections"]
    assert not any(c["provider"] == "uploads" for c in listed)
    # Documents survive a disconnect, like every other connector's ingested data.
    assert len(ctx.client.get("/v1/connectors/uploads/sources").json()["sources"]) == 1


def test_disconnect_404_when_not_connected(uploads_env, monkeypatch):
    ctx = company_client(monkeypatch)
    assert ctx.client.delete("/v1/connectors/uploads").status_code == 404


def test_uploads_is_not_an_oauth_provider(uploads_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/uploads/start-oauth")
    assert r.status_code == 404


def test_connector_status_reports_uploads_as_ingestable(uploads_env, monkeypatch):
    ctx = company_client(monkeypatch)
    ctx.client.post(
        "/v1/connectors/uploads/sources",
        data={"name": "Research"},
        files=[_file("one.md", b"one")],
    )
    statuses = ctx.client.get("/v1/connectors/status").json()["statuses"]
    row = next(s for s in statuses if s["provider"] == "uploads")
    assert row["ingestable"] is True


# ─────────────────────── Brief data-source gate ───────────────────────


def test_uploads_source_satisfies_the_brief_data_source_gate(
    isolated_settings, monkeypatch
):
    """The whole point: an uploaded-documents source is a data source, so brief
    generation is allowed — through the ordinary evidence-connection path, with
    no special case in app.brief_gate."""
    from app.brief_gate import has_brief_data_source

    # Non-evidence connectors only → gate is closed.
    monkeypatch.setattr(
        isolated_settings["db"], "list_connections",
        lambda _cid: [{"provider": "jira", "status": "active"},
                      {"provider": "notion", "status": "active"}],
    )
    assert not has_brief_data_source("co-1", "acme")

    # Add the uploads connection → gate opens.
    monkeypatch.setattr(
        isolated_settings["db"], "list_connections",
        lambda _cid: [{"provider": "jira", "status": "active"},
                      {"provider": "uploads", "status": "active"}],
    )
    assert has_brief_data_source("co-1", "acme")


def test_inactive_uploads_connection_does_not_satisfy_the_gate(
    isolated_settings, monkeypatch
):
    from app.brief_gate import has_brief_data_source

    monkeypatch.setattr(
        isolated_settings["db"], "list_connections",
        lambda _cid: [{"provider": "uploads", "status": "revoked"}],
    )
    assert not has_brief_data_source("co-1", "acme")


# ─────────────────────────── Helpers ───────────────────────────

_CID = "co-uploads-test"


def _seed_source(isolated_settings, *, name="Research", description=""):
    """Insert a company row (FK target) + one document source, service-level."""
    from app.db.client import require_client
    from app.document_sources import create_document_source

    c = require_client()
    existing = c.table("companies").select("id").eq("id", _CID).execute()
    if not (existing.data or []):
        c.table("companies").insert(
            {"id": _CID, "slug": "uploads-test", "display_name": "Uploads Test"}
        ).execute()
    return create_document_source(_CID, name=name, description=description)

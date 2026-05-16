"""Tests for the /v1/datasets HTTP routes."""
from __future__ import annotations

import io


def test_auth_required(unauth_client):
    r = unauth_client.get("/v1/datasets")
    assert r.status_code == 401


def test_list_empty(app_client):
    r = app_client.get("/v1/datasets")
    assert r.status_code == 200
    assert r.json() == {"datasets": []}


def test_create_dataset(app_client):
    r = app_client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme Corp"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "acme"
    assert body["display_name"] == "Acme Corp"


def test_create_dataset_duplicate(app_client):
    app_client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    r = app_client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme 2"})
    assert r.status_code == 409


def test_create_dataset_invalid_slug(app_client):
    r = app_client.post("/v1/datasets", json={"slug": "Has Space", "display_name": "x"})
    assert r.status_code == 422


def test_upload_files_happy_path(app_client):
    app_client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    files = [
        ("files", ("a.txt", io.BytesIO(b"first file"), "text/plain")),
        ("files", ("b.txt", io.BytesIO(b"second file"), "text/plain")),
    ]
    r = app_client.post("/v1/datasets/acme/files", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "acme"
    assert len(body["ingested"]) == 2
    assert body["errors"] == []
    assert all(item["md_chars"] > 0 for item in body["ingested"])


def test_upload_unsupported_returns_per_file_error(app_client):
    app_client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    files = [
        ("files", ("good.txt", io.BytesIO(b"ok"), "text/plain")),
        ("files", ("bad.exe", io.BytesIO(b"\x00"), "application/octet-stream")),
    ]
    r = app_client.post("/v1/datasets/acme/files", files=files)
    assert r.status_code == 200
    body = r.json()
    assert len(body["ingested"]) == 1
    assert len(body["errors"]) == 1
    assert body["errors"][0]["filename"] == "bad.exe"


def test_upload_too_large_rejected(app_client):
    app_client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    # 21 MB
    big = b"x" * (21 * 1024 * 1024)
    files = [("files", ("huge.txt", io.BytesIO(big), "text/plain"))]
    r = app_client.post("/v1/datasets/acme/files", files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["ingested"] == []
    assert "exceeds" in body["errors"][0]["error"]


def test_upload_to_missing_dataset(app_client):
    files = [("files", ("a.txt", io.BytesIO(b"x"), "text/plain"))]
    r = app_client.post("/v1/datasets/ghost/files", files=files)
    assert r.status_code == 404


def test_generate_kicks_off_async(app_client):
    app_client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    # Drop a corpus doc so brief_runner has something to load.
    from app.datasets import dataset_path
    (dataset_path("acme") / "ctx.md").write_text("hi")
    r = app_client.post("/v1/datasets/acme/generate")
    assert r.status_code == 200
    assert r.json()["started"] is True


def test_generate_missing_dataset(app_client):
    r = app_client.post("/v1/datasets/ghost/generate")
    assert r.status_code == 404


def test_delete_dataset(app_client):
    app_client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    r = app_client.delete("/v1/datasets/acme")
    assert r.status_code == 200
    # Now listing is empty again.
    r2 = app_client.get("/v1/datasets")
    assert r2.json() == {"datasets": []}


def test_delete_missing_dataset(app_client):
    r = app_client.delete("/v1/datasets/ghost")
    assert r.status_code == 404


def test_list_after_create_and_upload(app_client):
    app_client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    files = [("files", ("a.txt", io.BytesIO(b"hi"), "text/plain"))]
    app_client.post("/v1/datasets/acme/files", files=files)
    r = app_client.get("/v1/datasets")
    listing = r.json()["datasets"]
    assert len(listing) == 1
    assert listing[0]["raw_file_count"] == 1
    assert listing[0]["md_file_count"] == 1
    assert listing[0]["has_brief"] is False

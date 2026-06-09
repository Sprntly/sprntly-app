"""Tests for the /v1/datasets HTTP routes (incl. tenant isolation).

After the tenant-isolation fix these routes sit behind `require_company`. A
dataset slug IS a company slug, so each test seeds a company whose slug equals
the dataset slug it operates on. GET /v1/datasets lists ONLY the caller's
company's dataset; the per-slug routes 404 a slug that isn't the caller's.
"""
from __future__ import annotations

import io


def test_auth_required(unauth_client):
    r = unauth_client.get("/v1/datasets")
    assert r.status_code == 401


def test_list_empty(tenant_client):
    t = tenant_client.make(slug="acme")
    r = t.client.get("/v1/datasets")
    assert r.status_code == 200
    assert r.json() == {"datasets": []}


def test_create_dataset(tenant_client):
    t = tenant_client.make(slug="acme")
    r = t.client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme Corp"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "acme"
    assert body["display_name"] == "Acme Corp"


def test_create_dataset_duplicate(tenant_client):
    t = tenant_client.make(slug="acme")
    t.client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    r = t.client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme 2"})
    assert r.status_code == 409


def test_create_dataset_invalid_slug(tenant_client):
    t = tenant_client.make(slug="acme")
    r = t.client.post("/v1/datasets", json={"slug": "Has Space", "display_name": "x"})
    assert r.status_code == 422


def test_upload_files_happy_path(tenant_client):
    t = tenant_client.make(slug="acme")
    t.client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    files = [
        ("files", ("a.txt", io.BytesIO(b"first file"), "text/plain")),
        ("files", ("b.txt", io.BytesIO(b"second file"), "text/plain")),
    ]
    r = t.client.post("/v1/datasets/acme/files", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "acme"
    assert len(body["ingested"]) == 2
    assert body["errors"] == []
    assert all(item["md_chars"] > 0 for item in body["ingested"])


def test_upload_unsupported_returns_per_file_error(tenant_client):
    t = tenant_client.make(slug="acme")
    t.client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    files = [
        ("files", ("good.txt", io.BytesIO(b"ok"), "text/plain")),
        ("files", ("bad.exe", io.BytesIO(b"\x00"), "application/octet-stream")),
    ]
    r = t.client.post("/v1/datasets/acme/files", files=files)
    assert r.status_code == 200
    body = r.json()
    assert len(body["ingested"]) == 1
    assert len(body["errors"]) == 1
    assert body["errors"][0]["filename"] == "bad.exe"


def test_upload_too_large_rejected(tenant_client):
    t = tenant_client.make(slug="acme")
    t.client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    big = b"x" * (21 * 1024 * 1024)
    files = [("files", ("huge.txt", io.BytesIO(big), "text/plain"))]
    r = t.client.post("/v1/datasets/acme/files", files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["ingested"] == []
    assert "exceeds" in body["errors"][0]["error"]


def test_upload_to_unowned_dataset_404(tenant_client):
    """A slug that maps to no company → 404 before any filesystem work."""
    t = tenant_client.make(slug="acme")
    files = [("files", ("a.txt", io.BytesIO(b"x"), "text/plain"))]
    r = t.client.post("/v1/datasets/ghost/files", files=files)
    assert r.status_code == 404


def test_generate_kicks_off_async(tenant_client):
    t = tenant_client.make(slug="acme")
    t.client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    from app.datasets import dataset_path
    (dataset_path("acme") / "ctx.md").write_text("hi")
    r = t.client.post("/v1/datasets/acme/generate")
    assert r.status_code == 200
    assert r.json()["started"] is True


def test_generate_unowned_dataset_404(tenant_client):
    t = tenant_client.make(slug="acme")
    r = t.client.post("/v1/datasets/ghost/generate")
    assert r.status_code == 404


def test_delete_dataset(tenant_client):
    t = tenant_client.make(slug="acme")
    t.client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    r = t.client.delete("/v1/datasets/acme")
    assert r.status_code == 200
    r2 = t.client.get("/v1/datasets")
    assert r2.json() == {"datasets": []}


def test_delete_unowned_dataset_404(tenant_client):
    t = tenant_client.make(slug="acme")
    r = t.client.delete("/v1/datasets/ghost")
    assert r.status_code == 404


def test_list_after_create_and_upload(tenant_client):
    t = tenant_client.make(slug="acme")
    t.client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    files = [("files", ("a.txt", io.BytesIO(b"hi"), "text/plain"))]
    t.client.post("/v1/datasets/acme/files", files=files)
    r = t.client.get("/v1/datasets")
    listing = r.json()["datasets"]
    assert len(listing) == 1
    assert listing[0]["raw_file_count"] == 1
    assert listing[0]["md_file_count"] == 1
    assert listing[0]["has_brief"] is False


# ---- cross-tenant isolation -------------------------------------------------

def test_list_returns_only_callers_dataset(tenant_client):
    """GET /v1/datasets must not leak another tenant's datasets."""
    a = tenant_client.make(slug="company-a")
    a.client.post(
        "/v1/datasets", json={"slug": "company-a", "display_name": "A"}
    )
    b = tenant_client.make(slug="company-b")
    b.client.post(
        "/v1/datasets", json={"slug": "company-b", "display_name": "B"}
    )
    # B sees only B's dataset, never A's.
    b_list = b.client.get("/v1/datasets").json()["datasets"]
    assert [d["slug"] for d in b_list] == ["company-b"]
    a_list = a.client.get("/v1/datasets").json()["datasets"]
    assert [d["slug"] for d in a_list] == ["company-a"]


def test_files_and_delete_cross_tenant_404(tenant_client):
    """Company B cannot list files of / delete company A's dataset slug."""
    a = tenant_client.make(slug="company-a")
    a.client.post(
        "/v1/datasets", json={"slug": "company-a", "display_name": "A"}
    )
    a.client.post(
        "/v1/datasets/company-a/files",
        files=[("files", ("a.txt", io.BytesIO(b"hi"), "text/plain"))],
    )
    b = tenant_client.make(slug="company-b")
    assert b.client.get("/v1/datasets/company-a/files").status_code == 404
    assert b.client.post(
        "/v1/datasets/company-a/files",
        files=[("files", ("x.txt", io.BytesIO(b"x"), "text/plain"))],
    ).status_code == 404
    assert b.client.delete("/v1/datasets/company-a/files/a.txt").status_code == 404
    assert b.client.delete("/v1/datasets/company-a").status_code == 404
    # A's dataset is untouched.
    assert a.client.delete("/v1/datasets/company-a").status_code == 200

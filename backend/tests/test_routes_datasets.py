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


def test_create_dataset_foreign_slug_forbidden(tenant_client):
    """A signed-in user must not mint dataset rows for slugs that aren't
    their company's — that would squat slugs other tenants would claim."""
    t = tenant_client.make(slug="acme")
    r = t.client.post("/v1/datasets", json={"slug": "not-mine", "display_name": "X"})
    assert r.status_code == 403
    # And it must not have registered anything.
    assert t.client.get("/v1/datasets").json() == {"datasets": []}


def test_create_dataset_other_tenants_slug_forbidden(tenant_client):
    """Specifically: the slug of an EXISTING other company is rejected too."""
    tenant_client.make(slug="company-a")
    b = tenant_client.make(slug="company-b")
    r = b.client.post("/v1/datasets", json={"slug": "company-a", "display_name": "A"})
    assert r.status_code == 403


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


def test_upload_unknown_type_is_stored_not_rejected(tenant_client):
    t = tenant_client.make(slug="acme")
    t.client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    files = [
        ("files", ("good.txt", io.BytesIO(b"ok"), "text/plain")),
        ("files", ("bad.exe", io.BytesIO(b"\x00\x01binary"), "application/octet-stream")),
    ]
    r = t.client.post("/v1/datasets/acme/files", files=files)
    assert r.status_code == 200
    body = r.json()
    # Unknown binary type is now stored as a stub, not rejected.
    assert sorted(f["filename"] for f in body["ingested"]) == ["bad.exe", "good.txt"]
    assert body["errors"] == []


def test_upload_zip_is_expanded(tenant_client):
    import zipfile

    t = tenant_client.make(slug="acme")
    t.client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("one.md", b"# One")
        zf.writestr("two.txt", b"two")
        zf.writestr("extra.bin", b"\x00\x01binary")  # unknown member → stored, not skipped
    files = [("files", ("bundle.zip", io.BytesIO(buf.getvalue()), "application/zip"))]

    r = t.client.post("/v1/datasets/acme/files", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    # every member is expanded and ingested, each tagged with from_zip
    assert sorted(f["filename"] for f in body["ingested"]) == ["extra.bin", "one.md", "two.txt"]
    assert all(f["from_zip"] == "bundle.zip" for f in body["ingested"])
    assert body["errors"] == []


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
    from app.datasets import dataset_path, raw_path
    (dataset_path("acme") / "ctx.md").write_text("hi")
    # A user-uploaded raw file satisfies the data-source gate.
    raw_path("acme").mkdir(parents=True, exist_ok=True)
    (raw_path("acme") / "voice-notes.txt").write_text("hi")
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

"""Tests for GET/DELETE /v1/datasets/{slug}/files endpoints."""
from __future__ import annotations

import time


def _seed_dataset(app_client, slug: str = "acme", display: str = "Acme"):
    r = app_client.post("/v1/datasets", json={"slug": slug, "display_name": display})
    assert r.status_code == 200, r.text
    return slug


def _seed_file(slug: str, filename: str, data: bytes):
    """Use the service layer so both the raw + .md sides land on disk."""
    from app import datasets
    return datasets.ingest_file(slug, filename, data)


# ---------------- LIST ----------------------------------------------------


def test_list_files_requires_auth(unauth_client):
    r = unauth_client.get("/v1/datasets/acme/files")
    assert r.status_code == 401


def test_list_files_404_for_missing_dataset(app_client):
    r = app_client.get("/v1/datasets/ghost/files")
    assert r.status_code == 404


def test_list_files_empty_for_new_dataset(app_client):
    _seed_dataset(app_client)
    r = app_client.get("/v1/datasets/acme/files")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"slug": "acme", "files": []}


def test_list_files_returns_fields_after_seed(app_client):
    _seed_dataset(app_client)
    _seed_file("acme", "first.txt", b"hello world")
    _seed_file("acme", "second.txt", b"another body of text here")

    r = app_client.get("/v1/datasets/acme/files")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "acme"
    assert len(body["files"]) == 2

    by_name = {f["filename"]: f for f in body["files"]}
    assert set(by_name) == {"first.txt", "second.txt"}
    for name, f in by_name.items():
        assert f["kind"] == "txt"
        assert f["size_bytes"] > 0
        assert f["md_chars"] > 0
        assert "added_at" in f and f["added_at"]


def test_list_files_sorted_newest_first(app_client):
    _seed_dataset(app_client)
    _seed_file("acme", "older.txt", b"older")
    # Ensure mtime differs by at least a second on coarse filesystems.
    time.sleep(1.1)
    _seed_file("acme", "newer.txt", b"newer body")

    r = app_client.get("/v1/datasets/acme/files")
    body = r.json()
    names = [f["filename"] for f in body["files"]]
    assert names == ["newer.txt", "older.txt"], names


def test_list_files_handles_orphan_raw(app_client):
    """A raw file with no .md sibling should still list, with md_chars == 0."""
    _seed_dataset(app_client)
    from app import datasets
    raw = datasets.raw_path("acme")
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "orphan.txt").write_bytes(b"abc")

    r = app_client.get("/v1/datasets/acme/files")
    body = r.json()
    assert len(body["files"]) == 1
    f = body["files"][0]
    assert f["filename"] == "orphan.txt"
    assert f["md_chars"] == 0
    assert f["size_bytes"] == 3


# ---------------- DELETE --------------------------------------------------


def test_delete_file_requires_auth(unauth_client):
    r = unauth_client.delete("/v1/datasets/acme/files/whatever.txt")
    assert r.status_code == 401


def test_delete_file_404_for_missing_dataset(app_client):
    r = app_client.delete("/v1/datasets/ghost/files/x.txt")
    assert r.status_code == 404


def test_delete_file_404_for_missing_file(app_client):
    _seed_dataset(app_client)
    r = app_client.delete("/v1/datasets/acme/files/nope.txt")
    assert r.status_code == 404


def test_delete_file_422_for_dotfile(app_client):
    """A leading-dot filename must be rejected by the handler with 422."""
    _seed_dataset(app_client)
    r = app_client.delete("/v1/datasets/acme/files/.hidden")
    assert r.status_code == 422


def test_delete_file_rejects_slash_in_name(app_client):
    """URL-encoded slash should never let a caller traverse out of raw/."""
    _seed_dataset(app_client)
    # %2F encodes a slash. Starlette decodes path parameters, so the handler
    # will see "..%2Fetc%2Fpasswd" decoded to "../etc/passwd" — which the
    # basename check must reject with 422.
    r = app_client.delete("/v1/datasets/acme/files/..%2Fetc%2Fpasswd")
    # If the client/server normalizes away the segment, we get 404; if it
    # reaches the handler, the basename validation triggers 422. Either way
    # the request must NOT succeed, and must NOT delete an unintended file.
    assert r.status_code in (404, 422)


def test_delete_file_bare_dotdot_does_not_delete_anything(app_client):
    """`..` in the URL gets normalized by the HTTP client to the parent path.
    The handler is never reached, but we want to confirm no file vanished.
    """
    _seed_dataset(app_client)
    _seed_file("acme", "keepme.txt", b"safe")
    app_client.delete("/v1/datasets/acme/files/..")
    from app import datasets
    assert (datasets.raw_path("acme") / "keepme.txt").exists()


def test_delete_file_happy_path_removes_raw_and_md(app_client):
    _seed_dataset(app_client)
    _seed_file("acme", "report.txt", b"some content")
    from app import datasets
    raw_p = datasets.raw_path("acme") / "report.txt"
    md_p = datasets.dataset_path("acme") / "report.md"
    assert raw_p.exists() and md_p.exists()

    r = app_client.delete("/v1/datasets/acme/files/report.txt")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "slug": "acme",
        "filename": "report.txt",
        "removed": {"raw": True, "md": True},
    }
    assert not raw_p.exists()
    assert not md_p.exists()


def test_delete_file_orphan_raw_returns_md_false(app_client):
    _seed_dataset(app_client)
    from app import datasets
    raw = datasets.raw_path("acme")
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "lone.txt").write_bytes(b"x")

    r = app_client.delete("/v1/datasets/acme/files/lone.txt")
    assert r.status_code == 200
    body = r.json()
    assert body["removed"] == {"raw": True, "md": False}
    assert not (raw / "lone.txt").exists()


def test_delete_file_then_list_reflects_removal(app_client):
    _seed_dataset(app_client)
    _seed_file("acme", "a.txt", b"aaa")
    _seed_file("acme", "b.txt", b"bbb")

    r = app_client.delete("/v1/datasets/acme/files/a.txt")
    assert r.status_code == 200

    r2 = app_client.get("/v1/datasets/acme/files")
    body = r2.json()
    assert [f["filename"] for f in body["files"]] == ["b.txt"]

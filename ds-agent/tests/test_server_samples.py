"""Sample-dataset listing + load + reset endpoints.

The chat endpoint itself is mocked here; Anthropic is not called.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_PASSWORD", "letmein")
    monkeypatch.setenv("AGENT_COOKIE_SECRET", "test-cookie-secret-min-32-chars-long")
    monkeypatch.setenv("AGENT_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-used")

    from ds_agent.server import tools as _tools

    # Default stub: each staged file gets a deterministic fake file_id.
    # Tests that need a richer stub override this with their own monkeypatch.
    def _fake_upload(staged):
        return [
            _tools.UploadedFile(
                local_path=s.local_path,
                label=s.label,
                size_bytes=s.size_bytes,
                anthropic_file_id=f"file_{i}",
            )
            for i, s in enumerate(staged)
        ]

    monkeypatch.setattr(_tools, "upload_staged", _fake_upload)
    monkeypatch.setattr(_tools, "delete_file", lambda *a, **kw: None)

    from ds_agent.server.app import create_app
    return TestClient(create_app())


def _login(client) -> dict[str, str]:
    r = client.post("/api/login", json={"password": "letmein"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_samples_listed(client):
    headers = _login(client)
    r = client.get("/api/samples", headers=headers)
    assert r.status_code == 200
    samples = r.json()["samples"]
    assert any(s["id"] == "saas_retention" for s in samples)


def test_load_sample_sets_dataset(client):
    headers = _login(client)
    r = client.post("/api/load-sample", json={"sample_id": "saas_retention"}, headers=headers)
    assert r.status_code == 200
    state = client.get("/api/state", headers=headers).json()
    assert state["has_dataset"] is True
    assert "saas" in (state["dataset_label"] or "").lower()


def test_unknown_sample_404s(client):
    headers = _login(client)
    r = client.post("/api/load-sample", json={"sample_id": "bogus"}, headers=headers)
    assert r.status_code == 404


def test_reset_clears_dataset(client):
    headers = _login(client)
    client.post("/api/load-sample", json={"sample_id": "saas_retention"}, headers=headers)
    client.post("/api/reset", headers=headers)
    state = client.get("/api/state", headers=headers).json()
    assert state["has_dataset"] is False


def test_upload_rejects_unsupported_type(client):
    headers = _login(client)
    r = client.post(
        "/api/upload",
        headers=headers,
        files=[("files", ("script.exe", b"\x00\x01", "application/octet-stream"))],
    )
    assert r.status_code == 400
    assert r.json()["detail"].startswith("unsupported_type:")


def test_upload_single_csv_sets_dataset(client, monkeypatch):
    headers = _login(client)
    # Stub upload_staged so we don't hit the Files API but still mirror its shape.
    from ds_agent.server import tools as _tools
    monkeypatch.setattr(
        _tools,
        "upload_staged",
        lambda staged: [
            _tools.UploadedFile(
                local_path=s.local_path,
                label=s.label,
                size_bytes=s.size_bytes,
                anthropic_file_id=f"file_{i}",
            )
            for i, s in enumerate(staged)
        ],
    )
    r = client.post(
        "/api/upload",
        headers=headers,
        files=[("files", ("data.csv", b"a,b\n1,2\n", "text/csv"))],
    )
    assert r.status_code == 200, r.text
    state = client.get("/api/state", headers=headers).json()
    assert state["has_dataset"] is True
    assert state["dataset_label"] == "data.csv"
    assert len(state["files"]) == 1


def test_upload_multiple_files_summarizes_label(client, monkeypatch):
    headers = _login(client)
    from ds_agent.server import tools as _tools
    monkeypatch.setattr(
        _tools,
        "upload_staged",
        lambda staged: [
            _tools.UploadedFile(
                local_path=s.local_path,
                label=s.label,
                size_bytes=s.size_bytes,
                anthropic_file_id=f"file_{i}",
            )
            for i, s in enumerate(staged)
        ],
    )
    r = client.post(
        "/api/upload",
        headers=headers,
        files=[
            ("files", ("users.csv", b"id\n1\n", "text/csv")),
            ("files", ("orders.csv", b"id,amount\n1,10\n", "text/csv")),
            ("files", ("notes.md", b"# Notes\n", "text/markdown")),
        ],
    )
    assert r.status_code == 200, r.text
    state = client.get("/api/state", headers=headers).json()
    assert len(state["files"]) == 3
    # Label summarizes when more than one file is present.
    assert state["dataset_label"].startswith("3 files:")


def test_upload_zip_extracts_inner_files(client, monkeypatch, tmp_path):
    import zipfile

    headers = _login(client)
    from ds_agent.server import tools as _tools
    monkeypatch.setattr(
        _tools,
        "upload_staged",
        lambda staged: [
            _tools.UploadedFile(
                local_path=s.local_path,
                label=s.label,
                size_bytes=s.size_bytes,
                anthropic_file_id=f"file_{i}",
            )
            for i, s in enumerate(staged)
        ],
    )
    # Build a zip with two CSVs (one nested), plus junk to ignore.
    zip_path = tmp_path / "archive.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("users.csv", "id,name\n1,alice\n")
        zf.writestr("data/orders.csv", "id,amount\n10,99\n")
        zf.writestr("__MACOSX/garbage", b"junk")  # silently skipped

    with zip_path.open("rb") as fh:
        r = client.post(
            "/api/upload",
            headers=headers,
            files=[("files", ("archive.zip", fh.read(), "application/zip"))],
        )
    assert r.status_code == 200, r.text
    labels = {f["label"] for f in r.json()["files"]}
    # Both files should be present, with nested paths flattened via "__".
    assert "archive__users.csv" in labels
    assert "archive__data__orders.csv" in labels


def test_upload_zip_rejects_path_traversal(client):
    import zipfile
    import io

    headers = _login(client)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../etc/passwd", b"hacked")
    r = client.post(
        "/api/upload",
        headers=headers,
        files=[("files", ("evil.zip", buf.getvalue(), "application/zip"))],
    )
    assert r.status_code == 400
    assert "traversal" in r.json()["detail"]

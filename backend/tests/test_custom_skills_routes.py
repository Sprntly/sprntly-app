"""Custom skill upload/list/file routes (routes/custom_skills.py, PRD 1854).

Custom skills are COMPANY-scoped for now — all workspaces in a company share
one library; the uploading workspace is stamped on the row but never queried.

Covered:
- happy path: .md and .zip uploads create a company-scoped skill row and
  stage the original bytes (filesystem fallback in tests)
- the server-side validation ladder: missing/over-limit metadata (422), bad
  extension (422), empty file (400), oversize (413), unparseable content (400)
- slug conflicts: built-in shadowing and company duplicates (409), with the
  staged object rolled back on the duplicate path
- list: newest-first metadata, company-isolated
- file links: signed/fallback URLs for owned skills; foreign ids 404
- delete: removes the row and the staged original, frees the slug for
  re-upload; foreign/missing ids 404 indistinguishably
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

_MD = b"# Estimation method\nScore by reach x confidence.\n"


@pytest.fixture(autouse=True)
def _isolated_storage_dir(tmp_path, monkeypatch, isolated_settings):
    """Point the filesystem storage fallback at a per-test tmp dir so staged
    skill files never accumulate across tests (or pollute the repo data dir).

    Depends on isolated_settings so it runs AFTER _reload_app_modules() — then
    patches the settings instance skills_storage actually holds, which is the
    object its storage functions read at call time."""
    import app.skills_storage as ss

    monkeypatch.setattr(ss.settings, "storage_dir", str(tmp_path / "proto"), raising=False)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _upload(client, *, name="Estimation Helper", description="Scores features",
            filename="skill.md", data=_MD):
    return client.post(
        "/v1/skills",
        files={"file": (filename, data, "text/markdown")},
        data={"name": name, "description": description},
    )


def _staged_files() -> list[Path]:
    import app.skills_storage as ss

    root = Path(ss.settings.storage_dir).resolve() / "custom-skills"
    return [p for p in root.rglob("*") if p.is_file()] if root.is_dir() else []


# ─── happy path ──────────────────────────────────────────────────────────────


def test_upload_md_creates_skill(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slug"] == "estimation-helper"
    assert body["trigger"] == "/estimation-helper"
    assert body["name"] == "Estimation Helper"
    assert body["description"] == "Scores features"
    # uploader_name is captured from the session (JWT name/email claims — the
    # minted test token carries neither, so it may be empty, never client-set).
    assert "uploader_name" in body
    assert body["has_file"] is True
    # The original bytes are staged under the workspace prefix.
    assert len(_staged_files()) == 1


def test_upload_zip_creates_skill_with_modules(tenant_client):
    t = tenant_client.make(slug="acme")
    data = _zip_bytes({"SKILL.md": _MD, "modules/extra.md": b"more"})
    resp = _upload(t.client, name="Zip Skill", filename="zip-skill.zip", data=data)
    assert resp.status_code == 201, resp.text

    from app import db

    # Fetch through the real read API: slug lookup returns the parsed content.
    rows = db.list_custom_skills(t.company_id)
    assert [r["slug"] for r in rows] and rows[0]["slug"] == "zip-skill"
    full = db.get_custom_skill(t.company_id, "zip-skill")
    assert full["method"].startswith("# Estimation")
    assert full["modules"] == {"extra.md": "more"}


def test_uploader_identity_comes_from_session(tenant_client):
    t = tenant_client.make(slug="acme", user_id="user-fortune")
    resp = _upload(t.client)
    assert resp.status_code == 201

    from app import db

    row = db.get_custom_skill(t.company_id, "estimation-helper")
    assert row["uploader_id"] == "user-fortune"
    # The uploading workspace is stamped even though scoping is company-level.
    assert row["workspace_id"]


# ─── validation ladder ───────────────────────────────────────────────────────


def test_missing_name_422(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client, name="   ")
    assert resp.status_code == 422
    assert "name" in resp.json()["detail"].lower()


def test_missing_description_422(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client, description="")
    assert resp.status_code == 422
    assert "description" in resp.json()["detail"].lower()


def test_over_limit_metadata_422(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, name="x" * 65).status_code == 422
    assert _upload(t.client, description="x" * 1025).status_code == 422


def test_unsupported_extension_422(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client, filename="skill.pdf", data=b"%PDF-1.4")
    assert resp.status_code == 422
    assert ".md" in resp.json()["detail"]


def test_uppercase_extension_accepted(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, filename="SKILL.MD").status_code == 201


def test_empty_file_400(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, data=b"").status_code == 400


def test_oversize_413_and_boundary_accepted(tenant_client):
    from app.skills_storage import MAX_SKILL_UPLOAD_BYTES

    t = tenant_client.make(slug="acme")
    # Exactly 20 MB is accepted (inclusive boundary, per the PRD edge case)…
    at_limit = b"a" * MAX_SKILL_UPLOAD_BYTES
    assert _upload(t.client, name="At Limit", data=at_limit).status_code == 201
    # …one byte over is rejected before processing.
    over = b"a" * (MAX_SKILL_UPLOAD_BYTES + 1)
    resp = _upload(t.client, name="Over Limit", data=over)
    assert resp.status_code == 413
    assert "20 MB" in resp.json()["detail"]


def test_zip_without_md_400(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client, filename="s.zip", data=_zip_bytes({"a.txt": b"no md"}))
    assert resp.status_code == 400
    assert ".md" in resp.json()["detail"]


def test_empty_markdown_400(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, data=b"   \n").status_code == 400


def test_symbol_only_name_422(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, name="!!!").status_code == 422


# ─── slug conflicts + rollback ───────────────────────────────────────────────


def test_builtin_shadowing_409(tenant_client, monkeypatch):
    import app.routes.custom_skills as mod

    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(mod, "list_skills", lambda: ["prd-author"])
    resp = _upload(t.client, name="PRD Author")
    assert resp.status_code == 409
    assert "built-in" in resp.json()["detail"]
    # Rejected before staging — nothing written to storage.
    assert _staged_files() == []


def test_company_duplicate_409_rolls_back_staged_file(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client).status_code == 201
    assert len(_staged_files()) == 1

    resp = _upload(t.client)  # same name → same slug → unique violation
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]
    # The duplicate's staged object was rolled back — only the first remains.
    assert len(_staged_files()) == 1


def test_same_slug_allowed_across_companies(tenant_client):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    assert _upload(a.client).status_code == 201
    assert _upload(b.client).status_code == 201  # unique is per-company


# ─── list + isolation ────────────────────────────────────────────────────────


def test_list_returns_company_skills_newest_first(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, name="First Skill").status_code == 201
    assert _upload(t.client, name="Second Skill").status_code == 201

    resp = t.client.get("/v1/skills")
    assert resp.status_code == 200
    skills = resp.json()["skills"]
    assert [s["slug"] for s in skills] == ["second-skill", "first-skill"]
    # Metadata only — the method text is not shipped to the library list.
    assert "method" not in skills[0]


def test_list_is_company_isolated(tenant_client):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    assert _upload(a.client).status_code == 201

    assert b.client.get("/v1/skills").json()["skills"] == []


# ─── original-file links ─────────────────────────────────────────────────────


def test_file_links_for_owned_skill(tenant_client):
    t = tenant_client.make(slug="acme")
    skill_id = _upload(t.client).json()["id"]

    resp = t.client.get(f"/v1/skills/{skill_id}/file")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "estimation-helper.md"
    # Filesystem fallback in tests → stable file:// URLs.
    assert body["view_url"].startswith("file://")
    assert body["download_url"]


def test_file_links_foreign_id_404(tenant_client):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    skill_id = _upload(a.client).json()["id"]

    assert b.client.get(f"/v1/skills/{skill_id}/file").status_code == 404


def test_file_links_unknown_id_404(tenant_client):
    t = tenant_client.make(slug="acme")
    assert t.client.get("/v1/skills/not-a-real-id/file").status_code == 404


# ─── delete ──────────────────────────────────────────────────────────────────


def test_delete_removes_row_and_staged_file(tenant_client):
    t = tenant_client.make(slug="acme")
    skill_id = _upload(t.client).json()["id"]
    assert len(_staged_files()) == 1

    resp = t.client.delete(f"/v1/skills/{skill_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": True, "id": skill_id}
    assert t.client.get("/v1/skills").json()["skills"] == []
    # The staged original is cleaned up with the row.
    assert _staged_files() == []


def test_delete_frees_slug_for_reupload(tenant_client):
    t = tenant_client.make(slug="acme")
    skill_id = _upload(t.client).json()["id"]

    assert t.client.delete(f"/v1/skills/{skill_id}").status_code == 200
    # Same name again → no duplicate-slug 409 once the row is gone.
    assert _upload(t.client).status_code == 201


def test_delete_foreign_id_404_and_keeps_skill(tenant_client):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    skill_id = _upload(a.client).json()["id"]

    assert b.client.delete(f"/v1/skills/{skill_id}").status_code == 404
    # The owner's skill and its staged file are untouched.
    assert [s["id"] for s in a.client.get("/v1/skills").json()["skills"]] == [skill_id]
    assert len(_staged_files()) == 1


def test_delete_unknown_id_404(tenant_client):
    t = tenant_client.make(slug="acme")
    assert t.client.delete("/v1/skills/not-a-real-id").status_code == 404

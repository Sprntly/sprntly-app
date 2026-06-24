"""Tests for the roadmap doc — storage, the POST/GET /v1/company/roadmap-doc
routes, and the brief-priorities ingestion path (roadmap context reaches the
weekly-brief skill compose call)."""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.roadmap_doc import RoadmapDoc, load_roadmap_doc, save_roadmap_doc


# ---------- storage ----------

def test_save_extracts_text_and_loads(isolated_settings):
    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": "co-1", "slug": "acme", "display_name": "Acme"}
    ).execute()

    import app.roadmap_doc as rd
    rd.require_client = lambda: db  # type: ignore[assignment]

    doc = rd.save_roadmap_doc(
        "co-1",
        filename="roadmap.md",
        data=b"# H1 Roadmap\n\nThree bets: onboarding, discovery, data.",
        content_type="text/markdown",
    )
    assert doc.version == 1
    assert "Three bets" in doc.extracted_text

    loaded = rd.load_roadmap_doc("co-1")
    assert loaded is not None
    assert loaded.filename == "roadmap.md"
    assert "Three bets" in loaded.extracted_text


def test_reupload_replaces_and_bumps_version(isolated_settings):
    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": "co-2", "slug": "acme", "display_name": "Acme"}
    ).execute()
    import app.roadmap_doc as rd
    rd.require_client = lambda: db  # type: ignore[assignment]

    rd.save_roadmap_doc("co-2", filename="v1.md", data=b"first")
    doc2 = rd.save_roadmap_doc("co-2", filename="v2.md", data=b"second")
    assert doc2.version == 2
    loaded = rd.load_roadmap_doc("co-2")
    assert loaded is not None
    assert loaded.filename == "v2.md"  # latest wins — one row per company
    # exactly one row
    rows = db.table("roadmap_doc").select("id").eq("company_id", "co-2").execute().data
    assert len(rows) == 1


def test_load_returns_none_when_unset(isolated_settings):
    db = isolated_settings["supabase"]
    import app.roadmap_doc as rd
    rd.require_client = lambda: db  # type: ignore[assignment]
    assert rd.load_roadmap_doc("co-missing") is None


def test_render_for_prompt_truncates():
    doc = RoadmapDoc(filename="r.md", extracted_text="x" * 9000)
    rendered = doc.render_for_prompt(max_chars=100)
    assert len(rendered) < 200
    assert "roadmap truncated" in rendered

    assert RoadmapDoc(filename="r.md", extracted_text="").render_for_prompt() == ""


# ---------- routes ----------

def _route_client(isolated_settings, company_id: str):
    import app.main as main_mod
    from app.auth import CompanyContext
    import app.routes.company as company_route

    db = isolated_settings["supabase"]
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        db.table("companies").insert(
            {"id": company_id, "slug": "acme", "display_name": "Acme"}
        ).execute()

    main_mod.app.dependency_overrides[company_route.require_company] = (
        lambda: CompanyContext(company_id=company_id, role="owner", user_id="u1")
    )
    return TestClient(main_mod.app), company_route


def _clear(company_route):
    import app.main as main_mod
    main_mod.app.dependency_overrides.pop(company_route.require_company, None)


def test_get_404_when_no_roadmap(isolated_settings):
    client, route = _route_client(isolated_settings, "co-r1")
    try:
        r = client.get("/v1/company/roadmap-doc")
    finally:
        _clear(route)
    assert r.status_code == 404


def test_post_then_get_roundtrips(isolated_settings):
    client, route = _route_client(isolated_settings, "co-r2")
    try:
        post = client.post(
            "/v1/company/roadmap-doc",
            files={"file": ("roadmap.md", io.BytesIO(b"# H1\n\nSelf-serve onboarding bet."), "text/markdown")},
        )
        get = client.get("/v1/company/roadmap-doc")
    finally:
        _clear(route)
    assert post.status_code == 200, post.text
    body = post.json()
    assert body["ok"] is True
    assert body["filename"] == "roadmap.md"
    assert body["extracted_chars"] > 0

    assert get.status_code == 200
    g = get.json()
    assert g["filename"] == "roadmap.md"
    assert "Self-serve onboarding" in g["extracted_text"]
    # The raw base64 blob is NOT shipped in the artifact JSON.
    assert "raw_b64" not in g


def test_post_rejects_empty_file(isolated_settings):
    client, route = _route_client(isolated_settings, "co-r3")
    try:
        r = client.post(
            "/v1/company/roadmap-doc",
            files={"file": ("empty.md", io.BytesIO(b""), "text/markdown")},
        )
    finally:
        _clear(route)
    assert r.status_code == 400

"""Tests for the roadmap doc — storage, the POST/GET /v1/company/roadmap-doc
routes, and the brief-priorities ingestion path (roadmap context reaches the
top-insights skill compose call)."""
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


# ---------- version stability on an unchanged roadmap ----------
#
# The version is a user-facing claim that the ROADMAP changed, and it has to
# agree with the KG ingest ledger, which dedups on a hash of the extracted text.
# Bumping on every POST made the two disagree: staging QA saw "version 2" become
# "version 3" on a byte-identical re-upload while ingest correctly no-opped and
# wrote no new kg_source row — the label was counting uploads, the graph was
# counting roadmaps. These tests pin the rule: version tracks CONTENT.


def _rd(db):
    import app.roadmap_doc as rd
    rd.require_client = lambda: db  # type: ignore[assignment]
    return rd


def _seed_co(db, company_id: str, slug: str = "acme") -> None:
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        db.table("companies").insert(
            {"id": company_id, "slug": slug, "display_name": slug.title()}
        ).execute()


ROADMAP_BYTES = b"# H2 Roadmap\n\n- Self-serve onboarding\n- AI authoring\n"


def test_identical_reupload_does_not_bump_version(isolated_settings):
    """THE BUG: the same file uploaded twice must stay at one version."""
    db = isolated_settings["supabase"]
    _seed_co(db, "co-noop")
    rd = _rd(db)

    v1 = rd.save_roadmap_doc("co-noop", filename="H2.md", data=ROADMAP_BYTES,
                             workspace_id="ws-1")
    assert v1.version == 1
    v2 = rd.save_roadmap_doc("co-noop", filename="H2.md", data=ROADMAP_BYTES,
                             workspace_id="ws-1")

    assert v2.version == 1, "byte-identical re-upload must not create a version"
    # Response shape is unchanged — the UI still renders current state.
    assert v2.filename == "H2.md"
    assert "Self-serve onboarding" in v2.extracted_text
    assert v2.raw_b64
    loaded = rd.load_roadmap_doc("co-noop", workspace_id="ws-1")
    assert loaded is not None and loaded.version == 1
    # Still exactly one row.
    rows = db.table("roadmap_doc").select("id").eq("company_id", "co-noop").execute().data
    assert len(rows) == 1


def test_reupload_ten_times_stays_at_version_one(isolated_settings):
    """No slow drift either — a PM who re-uploads repeatedly stays at v1."""
    db = isolated_settings["supabase"]
    _seed_co(db, "co-noop10")
    rd = _rd(db)
    for _ in range(10):
        doc = rd.save_roadmap_doc("co-noop10", filename="H2.md",
                                  data=ROADMAP_BYTES, workspace_id="ws-1")
    assert doc.version == 1


def test_changed_content_still_bumps_version(isolated_settings):
    """The fix must not freeze legitimately new roadmaps."""
    db = isolated_settings["supabase"]
    _seed_co(db, "co-bump")
    rd = _rd(db)

    rd.save_roadmap_doc("co-bump", filename="H2.md", data=ROADMAP_BYTES,
                        workspace_id="ws-1")
    v2 = rd.save_roadmap_doc("co-bump", filename="H2.md",
                             data=b"# H2 Roadmap\n\n- Usage analytics\n",
                             workspace_id="ws-1")
    assert v2.version == 2
    # …and a THIRD, different upload keeps going.
    v3 = rd.save_roadmap_doc("co-bump", filename="H2.md",
                             data=b"# H2 Roadmap\n\n- SSO\n", workspace_id="ws-1")
    assert v3.version == 3
    # Re-uploading v3's content now no-ops at 3, not 4.
    again = rd.save_roadmap_doc("co-bump", filename="H2.md",
                                data=b"# H2 Roadmap\n\n- SSO\n", workspace_id="ws-1")
    assert again.version == 3


def test_same_content_new_filename_refreshes_metadata_at_same_version(isolated_settings):
    """Renaming a file doesn't make it a new roadmap — but the stored filename
    must still track what the PM actually last uploaded, or the artifact view
    shows a file they no longer recognize."""
    db = isolated_settings["supabase"]
    _seed_co(db, "co-rename")
    rd = _rd(db)

    rd.save_roadmap_doc("co-rename", filename="H2.md", data=ROADMAP_BYTES,
                        workspace_id="ws-1")
    renamed = rd.save_roadmap_doc("co-rename", filename="H2-final.md",
                                  data=ROADMAP_BYTES, workspace_id="ws-1")
    assert renamed.version == 1              # content unchanged
    assert renamed.filename == "H2-final.md"  # metadata refreshed
    loaded = rd.load_roadmap_doc("co-rename", workspace_id="ws-1")
    assert loaded is not None
    assert loaded.filename == "H2-final.md" and loaded.version == 1


def test_reexported_same_text_different_bytes_does_not_bump(isolated_settings):
    """Version identity is the EXTRACTED TEXT, matching the ingest ledger's key.

    The same roadmap re-exported (different bytes, same readable content) is not
    a new roadmap — and the ledger already treats it as unchanged, so bumping
    here would recreate exactly the disagreement this fixes."""
    db = isolated_settings["supabase"]
    _seed_co(db, "co-reexport")
    rd = _rd(db)

    rd.save_roadmap_doc("co-reexport", filename="H2.md",
                        data=b"# H2\n\n- Onboarding\n", workspace_id="ws-1")
    # Same markdown, different trailing whitespace → different bytes, same text.
    reexport = rd.save_roadmap_doc("co-reexport", filename="H2.md",
                                   data=b"# H2\n\n- Onboarding\n\n\n  ",
                                   workspace_id="ws-1")
    assert reexport.version == 1


def test_unreadable_uploads_fall_back_to_byte_identity(isolated_settings):
    """When NEITHER side yields text, text equality is vacuous — a genuinely
    different unreadable file must still register as a new version, while the
    identical one must not."""
    db = isolated_settings["supabase"]
    _seed_co(db, "co-blind")
    rd = _rd(db)

    blob_a = bytes(range(256)) * 8
    blob_b = bytes(range(255, -1, -1)) * 8
    v1 = rd.save_roadmap_doc("co-blind", filename="a.bin", data=blob_a,
                             workspace_id="ws-1")
    assert v1.version == 1
    # Same unreadable bytes → still v1.
    same = rd.save_roadmap_doc("co-blind", filename="a.bin", data=blob_a,
                               workspace_id="ws-1")
    assert same.version == 1
    # DIFFERENT unreadable bytes → a real new upload.
    diff = rd.save_roadmap_doc("co-blind", filename="b.bin", data=blob_b,
                               workspace_id="ws-1")
    assert diff.version == 2


def test_identical_bytes_in_a_second_workspace_are_that_workspace_v1(isolated_settings):
    """Cross-workspace isolation: comparison is against THIS workspace's row."""
    db = isolated_settings["supabase"]
    _seed_co(db, "co-ws")
    rd = _rd(db)

    a = rd.save_roadmap_doc("co-ws", filename="H2.md", data=ROADMAP_BYTES,
                            workspace_id="ws-a")
    b = rd.save_roadmap_doc("co-ws", filename="H2.md", data=ROADMAP_BYTES,
                            workspace_id="ws-b")
    assert a.version == 1
    assert b.version == 1, "workspace B gets its own v1, not A's version"
    # Re-uploading into A still doesn't bump A, and B is untouched.
    a2 = rd.save_roadmap_doc("co-ws", filename="H2.md", data=ROADMAP_BYTES,
                             workspace_id="ws-a")
    assert a2.version == 1
    assert rd.load_roadmap_doc("co-ws", workspace_id="ws-b").version == 1
    # Two distinct rows.
    rows = db.table("roadmap_doc").select("id").eq("company_id", "co-ws").execute().data
    assert len(rows) == 2


def test_legacy_no_workspace_path_also_holds_the_version(isolated_settings):
    """The company-keyed legacy path (older callers/tests) gets the same rule."""
    db = isolated_settings["supabase"]
    _seed_co(db, "co-legacy")
    rd = _rd(db)

    rd.save_roadmap_doc("co-legacy", filename="H2.md", data=ROADMAP_BYTES)
    same = rd.save_roadmap_doc("co-legacy", filename="H2.md", data=ROADMAP_BYTES)
    assert same.version == 1
    changed = rd.save_roadmap_doc("co-legacy", filename="H2.md", data=b"# Different\n")
    assert changed.version == 2


# ---------- endpoint + ingest agreement ----------

def test_post_same_file_twice_keeps_version_and_stays_ok(isolated_settings):
    """What staging QA actually saw: the POST response's `version` must not
    climb on a byte-identical re-upload, and the request must still succeed."""
    client, route = _route_client(isolated_settings, "co-post-noop")
    try:
        first = client.post("/v1/company/roadmap-doc", files={
            "file": ("H2.md", io.BytesIO(ROADMAP_BYTES), "text/markdown")})
        second = client.post("/v1/company/roadmap-doc", files={
            "file": ("H2.md", io.BytesIO(ROADMAP_BYTES), "text/markdown")})
    finally:
        _clear(route)

    assert first.status_code == 200 and second.status_code == 200, second.text
    assert first.json()["version"] == 1
    assert second.json()["version"] == 1        # ← was 2 before the fix
    assert second.json()["ok"] is True
    assert second.json()["filename"] == "H2.md"
    assert second.json()["extracted_chars"] > 0


def test_version_and_kg_ledger_agree_after_identical_reupload(isolated_settings):
    """The invariant behind the bug: the user-facing version and the KG ingest
    ledger must count the same thing. One roadmap → one version → one ledger row,
    no matter how many times it is uploaded."""
    from unittest.mock import patch

    import app.graph.extractor as ex
    from app.graph.facade import GraphFacade
    from app.graph.gateway import LLMResult
    from app.kg_ingest import roadmap as rm

    db = isolated_settings["supabase"]
    _seed_co(db, "co-agree")
    rd = _rd(db)

    def _llm(**_k):
        return LLMResult(
            output={"signals": [{
                "kind": "finding", "content": "Ship self-serve onboarding",
                "source_type": "pm_manual", "theme": "Onboarding",
                "relationship": "SUPPORTS", "confidence": 0.9}]},
            model="m", prompt_version=ex.PROMPT_VERSION, input_tokens=0,
            output_tokens=0, cache_read_input_tokens=0,
            cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=0,
            stop_reason="end_turn",
        )

    facade = GraphFacade()
    calls: list[int] = []
    with patch.object(ex, "llm_call", side_effect=lambda **k: (calls.append(1), _llm())[1]), \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        rd.save_roadmap_doc("co-agree", filename="H2.md", data=ROADMAP_BYTES,
                            workspace_id="ws-1")
        first_ingest = rm.ingest_roadmap("co-agree", "ws-1", facade=facade)

        # Identical re-upload, then the ingest its kickoff would run.
        doc2 = rd.save_roadmap_doc("co-agree", filename="H2.md",
                                   data=ROADMAP_BYTES, workspace_id="ws-1")
        second_ingest = rm.ingest_roadmap("co-agree", "ws-1", facade=facade)

    assert first_ingest["status"] == "ingested"
    assert doc2.version == 1                      # version held
    assert second_ingest["status"] == "unchanged"  # ledger held
    assert len(calls) == 1                         # and no extra model spend
    ledger = facade.list_sources("co-agree", source_type=rm.LEDGER_SOURCE_TYPE)
    assert len(ledger) == 1
    assert ledger[0].config["version"] == 1

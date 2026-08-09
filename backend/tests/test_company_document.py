"""Tests for company documents — the strategy/context files a PM uploads during
the onboarding strategy step (design scene onbstrat): storage (save/list, per
doc_type) + the POST/GET /v1/company/documents routes.

Sibling of test_company_template.py. The doc_types are the onbstrat upload
cards (ceo_memo | team_priorities | research | company_strategy) plus the v6
steps-6/7 upload-or-type blocks (team_strategy | team_roadmap |
decision_process | additional_context). These docs are
STORED only for now (feeding them into agent context is a follow-up), so there is
no render_for_prompt / synthesis coverage here."""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.company_document import DOC_TYPES, is_valid_doc_type


def _wire(db):
    import app.company_document as cd
    cd.require_client = lambda: db  # type: ignore[assignment]
    return cd


def _seed_company(db, company_id="co-1"):
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        # companies.slug is UNIQUE — derive a unique slug per company_id.
        db.table("companies").insert(
            {"id": company_id, "slug": f"acme-{company_id}", "display_name": "Acme"}
        ).execute()


# ---------- doc_type validation ----------

def test_doc_types_are_the_onbstrat_cards():
    assert set(DOC_TYPES) == {
        # base four (v5 onbstrat cards)
        "ceo_memo",
        "team_priorities",
        "research",
        "company_strategy",
        # upload-or-type blocks now merged into the v7 workspace step
        "team_strategy",
        "team_roadmap",
        "decision_process",
        "additional_context",
        # the v7 workspace step's "attach a previous sizing doc" affordance
        "sizing_doc",
    }
    assert is_valid_doc_type("ceo_memo")
    assert not is_valid_doc_type("not_a_type")


# ---------- storage ----------

def test_save_extracts_text_and_lists(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-1")
    cd = _wire(db)

    d = cd.save_company_document(
        "co-1",
        doc_type="ceo_memo",
        filename="memo.md",
        data=b"# H2 priorities\n\nShip self-serve onboarding.",
        content_type="text/markdown",
    )
    assert d.id
    assert d.doc_type == "ceo_memo"
    assert "self-serve onboarding" in d.extracted_text

    rows = cd.list_company_documents("co-1")
    assert len(rows) == 1
    assert rows[0].filename == "memo.md"
    assert "priorities" in rows[0].extracted_text


def test_multiple_documents_per_company_and_per_type(isolated_settings):
    """MANY documents per company (like templates), and several per doc_type."""
    db = isolated_settings["supabase"]
    _seed_company(db, "co-2")
    cd = _wire(db)

    cd.save_company_document("co-2", doc_type="ceo_memo", filename="memo.md", data=b"memo")
    cd.save_company_document("co-2", doc_type="research", filename="study.md", data=b"study")
    cd.save_company_document("co-2", doc_type="research", filename="market.md", data=b"market")

    rows = cd.list_company_documents("co-2")
    assert len(rows) == 3
    assert {r.filename for r in rows} == {"memo.md", "study.md", "market.md"}


def test_list_filters_by_doc_type(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-t")
    cd = _wire(db)
    cd.save_company_document("co-t", doc_type="ceo_memo", filename="memo.md", data=b"m")
    cd.save_company_document("co-t", doc_type="company_strategy", filename="plan.md", data=b"p")

    memos = cd.list_company_documents("co-t", doc_type="ceo_memo")
    assert [r.filename for r in memos] == ["memo.md"]
    strat = cd.list_company_documents("co-t", doc_type="company_strategy")
    assert [r.filename for r in strat] == ["plan.md"]


def test_list_empty_when_none(isolated_settings):
    db = isolated_settings["supabase"]
    cd = _wire(db)
    assert cd.list_company_documents("co-missing") == []


def test_list_fails_open_when_table_missing(isolated_settings):
    """The company_document migration deploys independently of this code, so on
    an environment where the table does not yet exist the read RAISES. The fetch
    path must fail open — return [] — so onboarding never breaks."""

    class _MissingTableDB:
        def table(self, _name):
            return self

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def execute(self):
            raise RuntimeError('relation "company_document" does not exist')

    cd = _wire(_MissingTableDB())
    assert cd.list_company_documents("co-x") == []


# ---------- routes ----------

def _route_client(isolated_settings, company_id: str):
    import app.main as main_mod
    from app.auth import CompanyContext
    import app.routes.company as company_route

    db = isolated_settings["supabase"]
    _seed_company(db, company_id)
    # The route layer calls app.company_document.* — point it at the fake db.
    _wire(db)

    main_mod.app.dependency_overrides[company_route.require_company] = (
        lambda: CompanyContext(company_id=company_id, role="owner", user_id="u1")
    )
    return TestClient(main_mod.app), company_route


def _clear(company_route):
    import app.main as main_mod
    main_mod.app.dependency_overrides.pop(company_route.require_company, None)


def test_post_list_roundtrip_per_doc_type(isolated_settings):
    client, route = _route_client(isolated_settings, "co-rt")
    try:
        post = client.post(
            "/v1/company/documents",
            files={"file": ("memo.md", io.BytesIO(b"# Memo\n\nLeadership direction."), "text/markdown")},
            data={"doc_type": "ceo_memo"},
        )
        client.post(
            "/v1/company/documents",
            files={"file": ("study.md", io.BytesIO(b"User study findings."), "text/markdown")},
            data={"doc_type": "research"},
        )
        listed = client.get("/v1/company/documents")
        only_memos = client.get("/v1/company/documents?doc_type=ceo_memo")
    finally:
        _clear(route)

    assert post.status_code == 200, post.text
    body = post.json()
    assert body["ok"] is True
    assert body["doc_type"] == "ceo_memo"
    assert body["extracted_chars"] > 0
    # raw bytes are never shipped
    assert "raw_b64" not in body

    assert listed.status_code == 200
    items = listed.json()["documents"]
    assert len(items) == 2
    assert {i["filename"] for i in items} == {"memo.md", "study.md"}

    assert only_memos.status_code == 200
    memos = only_memos.json()["documents"]
    assert [m["filename"] for m in memos] == ["memo.md"]


def test_post_accepts_workspace_step_doc_types(isolated_settings):
    """The v6 steps-6/7 upload-or-type blocks (+ step 1's strategy upload)
    upload under the four NEW doc_types — the route + storage constraint must
    accept every one of them end-to-end."""
    client, route = _route_client(isolated_settings, "co-v6")
    try:
        for doc_type in (
            "team_strategy", "team_roadmap", "decision_process", "additional_context",
            "sizing_doc",
        ):
            r = client.post(
                "/v1/company/documents",
                files={"file": (f"{doc_type}.md", io.BytesIO(b"content"), "text/markdown")},
                data={"doc_type": doc_type},
            )
            assert r.status_code == 200, f"{doc_type}: {r.text}"
            assert r.json()["doc_type"] == doc_type
        listed = client.get("/v1/company/documents")
    finally:
        _clear(route)
    assert len(listed.json()["documents"]) == 5


def test_post_rejects_invalid_doc_type(isolated_settings):
    client, route = _route_client(isolated_settings, "co-bad")
    try:
        r = client.post(
            "/v1/company/documents",
            files={"file": ("x.md", io.BytesIO(b"x"), "text/markdown")},
            data={"doc_type": "not_a_real_type"},
        )
    finally:
        _clear(route)
    assert r.status_code == 422


def test_post_rejects_empty_file(isolated_settings):
    client, route = _route_client(isolated_settings, "co-empty")
    try:
        r = client.post(
            "/v1/company/documents",
            files={"file": ("empty.md", io.BytesIO(b""), "text/markdown")},
            data={"doc_type": "ceo_memo"},
        )
    finally:
        _clear(route)
    assert r.status_code == 400

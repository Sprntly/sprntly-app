"""Tests for company templates — the company's gold-standard PRD examples
('what good looks like'): storage (save/list/delete), the
POST/GET/DELETE /v1/company/templates routes, and render_for_prompt.

Sibling of test_roadmap_doc.py; the prd-author ingestion path (templates reach
the prd-author compose call) is covered in test_prd_runner.py."""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.company_template import (
    CompanyTemplate,
    delete_company_template,
    list_company_templates,
    render_templates_for_prompt,
    save_company_template,
)


def _wire(db):
    import app.company_template as ct
    ct.require_client = lambda: db  # type: ignore[assignment]
    return ct


def _seed_company(db, company_id="co-1"):
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        # companies.slug is UNIQUE — derive a unique slug per company_id so a
        # test that seeds two companies doesn't collide.
        db.table("companies").insert(
            {"id": company_id, "slug": f"acme-{company_id}", "display_name": "Acme"}
        ).execute()


# ---------- storage ----------

def test_save_extracts_text_and_lists(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-1")
    ct = _wire(db)

    t = ct.save_company_template(
        "co-1",
        filename="gold.md",
        data=b"# Gold PRD\n\n## Problem\n## Solution\n## Metrics",
        label="Gold standard",
        content_type="text/markdown",
    )
    assert t.id
    assert t.type == "prd"
    assert t.label == "Gold standard"
    assert "Problem" in t.extracted_text

    rows = ct.list_company_templates("co-1")
    assert len(rows) == 1
    assert rows[0].filename == "gold.md"
    assert "Solution" in rows[0].extracted_text


def test_multiple_templates_per_company(isolated_settings):
    """Unlike roadmap (one per company), many templates accumulate."""
    db = isolated_settings["supabase"]
    _seed_company(db, "co-2")
    ct = _wire(db)

    ct.save_company_template("co-2", filename="a.md", data=b"alpha exemplar")
    ct.save_company_template("co-2", filename="b.md", data=b"beta exemplar")

    rows = ct.list_company_templates("co-2")
    assert len(rows) == 2
    assert {r.filename for r in rows} == {"a.md", "b.md"}


def test_list_filters_by_type(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-t")
    ct = _wire(db)
    ct.save_company_template("co-t", filename="prd.md", data=b"prd", type="prd")
    ct.save_company_template("co-t", filename="spec.md", data=b"spec", type="spec")

    prds = ct.list_company_templates("co-t", type="prd")
    assert [r.filename for r in prds] == ["prd.md"]


def test_delete_scoped_to_company(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-3")
    _seed_company(db, "co-other")
    ct = _wire(db)
    t = ct.save_company_template("co-3", filename="x.md", data=b"x")

    # Wrong company can't delete it.
    assert ct.delete_company_template("co-other", t.id) is False
    assert len(ct.list_company_templates("co-3")) == 1
    # Owner can.
    assert ct.delete_company_template("co-3", t.id) is True
    assert ct.list_company_templates("co-3") == []


def test_list_empty_when_none(isolated_settings):
    db = isolated_settings["supabase"]
    ct = _wire(db)
    assert ct.list_company_templates("co-missing") == []


def test_list_fails_open_when_table_missing(isolated_settings):
    """The company_template migration deploys independently of this code, so on
    an environment where the table does not yet exist the read RAISES. The
    fetch path must fail open — return [] — so PRD generation never breaks."""

    class _MissingTableDB:
        def table(self, _name):
            return self

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def execute(self):
            raise RuntimeError(
                'relation "company_template" does not exist'
            )

    ct = _wire(_MissingTableDB())
    # Both the low-level fetch and the prompt renderer must no-op cleanly.
    assert ct.list_company_templates("co-x") == []
    assert ct.render_templates_for_prompt("co-x") == ""


# ---------- render_for_prompt ----------

def test_render_for_prompt_truncates():
    t = CompanyTemplate(id="1", filename="r.md", extracted_text="x" * 9000)
    rendered = t.render_for_prompt(max_chars=100)
    assert len(rendered) < 200
    assert "exemplar truncated" in rendered
    assert CompanyTemplate(id="2", filename="r.md", extracted_text="").render_for_prompt() == ""


def test_render_templates_for_prompt_block(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-r")
    ct = _wire(db)
    ct.save_company_template("co-r", filename="g.md", data=b"GOLD_BODY_MARK", label="House style")

    block = ct.render_templates_for_prompt("co-r")
    assert "FORMAT/STYLE EXEMPLARS" in block
    assert "GOLD_BODY_MARK" in block
    assert "House style" in block
    assert "MATCH their structure" in block


def test_render_templates_for_prompt_empty_when_none(isolated_settings):
    db = isolated_settings["supabase"]
    ct = _wire(db)
    assert ct.render_templates_for_prompt("co-none") == ""


# ---------- routes ----------

def _route_client(isolated_settings, company_id: str):
    import app.main as main_mod
    from app.auth import CompanyContext
    import app.routes.company as company_route

    db = isolated_settings["supabase"]
    _seed_company(db, company_id)
    # The route layer calls app.company_template.* — point it at the fake db.
    _wire(db)

    main_mod.app.dependency_overrides[company_route.require_company] = (
        lambda: CompanyContext(company_id=company_id, role="owner", user_id="u1")
    )
    return TestClient(main_mod.app), company_route


def _clear(company_route):
    import app.main as main_mod
    main_mod.app.dependency_overrides.pop(company_route.require_company, None)


def test_post_list_delete_roundtrip(isolated_settings):
    client, route = _route_client(isolated_settings, "co-rt")
    try:
        post = client.post(
            "/v1/company/templates",
            files={"file": ("gold.md", io.BytesIO(b"# Gold\n\nGreat PRD shape."), "text/markdown")},
            data={"label": "Our gold standard"},
        )
        listed = client.get("/v1/company/templates")
        body = post.json()
        tid = body["id"]
        deleted = client.delete(f"/v1/company/templates/{tid}")
        after = client.get("/v1/company/templates")
    finally:
        _clear(route)

    assert post.status_code == 200, post.text
    assert body["ok"] is True
    assert body["label"] == "Our gold standard"
    assert body["extracted_chars"] > 0
    # raw bytes are never shipped
    assert "raw_b64" not in body

    assert listed.status_code == 200
    items = listed.json()["templates"]
    assert len(items) == 1
    assert items[0]["filename"] == "gold.md"

    assert deleted.status_code == 200
    assert after.json()["templates"] == []


def test_delete_missing_is_404(isolated_settings):
    client, route = _route_client(isolated_settings, "co-404")
    try:
        r = client.delete("/v1/company/templates/does-not-exist")
    finally:
        _clear(route)
    assert r.status_code == 404


def test_post_rejects_empty_file(isolated_settings):
    client, route = _route_client(isolated_settings, "co-empty")
    try:
        r = client.post(
            "/v1/company/templates",
            files={"file": ("empty.md", io.BytesIO(b""), "text/markdown")},
        )
    finally:
        _clear(route)
    assert r.status_code == 400

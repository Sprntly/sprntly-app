"""Artifact format template routes (routes/artifact_templates.py).

Templates are COMPANY-scoped: all workspaces in a company share one library and
one active format per artifact type. The uploading workspace is stamped on the
row and never queried.

Covered:
- create from BOTH shapes the one route accepts — pasted JSON and a multipart
  `.md` upload — and the server-side validation ladder for each: missing or
  over-limit name (422), missing/unknown artifact type (422), non-`.md`
  extension (422), empty source (400), over 2 MB (413), over the character cap
  (413)
- list: newest-first metadata, company-isolated, `?type=` filtered, and the
  TOP-LEVEL `generation_enabled` map that tells a screen which generators
  actually honour a custom format yet
- the list payload reads nothing the list SELECT doesn't fetch (the SQLite fake
  can't catch that, so it is asserted directly)
- detail + preview: full row, and the explicit `format` discriminator so a
  client never sniffs HTML from a leading `<`
- **404-not-403 on a foreign id for GET, PATCH, DELETE, compile, preview,
  activate and deactivate** — a foreign tenant must not be able to tell "exists
  but not yours" from "doesn't exist"
- every mutating route 403s without an `Origin` header (the CSRF backstop)
- activation: 409 until the format has compiled clean, admin-only, deactivates
  the outgoing sibling, and never crosses a tenant
- delete: open to any member for a non-active format, admin-only (403) for the
  ACTIVE one, and reports the fallback to the built-in
"""
from __future__ import annotations

import uuid

_SOURCE = "# Acme PRD\n\n## Context\n\n## Requirements\n"
_URL = "/v1/artifact-templates"


def _create(client, *, name="Acme PRD v3", artifact_type="prd", source_md=_SOURCE,
            **kw):
    return client.post(
        _URL,
        json={"name": name, "artifact_type": artifact_type, "source_md": source_md},
        **kw,
    )


def _upload(client, *, filename="acme-prd.md", data=_SOURCE.encode(),
            artifact_type="prd", name=None):
    form = {"artifact_type": artifact_type}
    if name is not None:
        form["name"] = name
    return client.post(
        _URL,
        files={"file": (filename, data, "text/markdown")},
        data=form,
    )


def _make_ready(company_id: str, template_id: str) -> None:
    """Bring a template to the one state activation accepts.

    Written through the real DB API rather than a route, because NOTHING in
    milestone 1 compiles — the compiler is a later milestone, so `ready` is not
    reachable through the API yet and the activation path would otherwise be
    untestable."""
    from app import db

    db.set_compile_result(
        company_id=company_id,
        template_id=template_id,
        compile_status="ready",
        compiled="<html><style></style><h1>Acme PRD</h1></html>",
        section_map={
            "sections": [
                {"id": "s1", "house": "Context", "customer": "Background",
                 "order": 1, "form": "prose"}
            ],
            "unmapped_house": ["Riskiest assumption"],
            "extra_sections": ["Launch checklist"],
        },
        compile_notes=[],
    )


def _member(tenant_client, t) -> dict:
    """Auth header for a SECOND user in the same company with the plain
    `member` role.

    Two rows are needed, not one: company_members carries the role the admin
    gate reads, and workspace_members is what require_workspace demands of
    anybody who is not a company owner/admin (auth.py::_resolve_workspace)."""
    from app.db.client import require_client
    from app.db.workspaces import ensure_default_workspace

    uid = "member-" + uuid.uuid4().hex[:8]
    c = require_client()
    c.table("company_members").insert(
        {"id": f"cm-{t.company_id}-{uid}", "company_id": t.company_id,
         "user_id": uid, "role": "member"}
    ).execute()
    if not c.table("profiles").select("id").eq("id", uid).execute().data:
        c.table("profiles").insert({"id": uid}).execute()
    ws = ensure_default_workspace(t.company_id)
    c.table("workspace_members").insert(
        {"id": f"wm-{ws['id']}-{uid}", "workspace_id": ws["id"],
         "user_id": uid, "role": "member"}
    ).execute()
    return tenant_client.bearer(uid)


# ─── create ──────────────────────────────────────────────────────────────────


def test_create_from_pasted_markdown(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _create(t.client)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["name"] == "Acme PRD v3"
    assert body["artifact_type"] == "prd"
    # A new format is listed and governs nothing: queued, not checked, not in use.
    assert body["compile_status"] == "pending"
    assert body["is_active"] is False
    assert body["compile_notes"] == []
    assert body["compile_summary"] is None
    assert body["compile_note_count"] == 0
    assert body["source_chars"] == len(_SOURCE)
    assert body["source_md"] == _SOURCE
    # Every metadata line renders even when blank — uploader_name comes from the
    # session's JWT claims, which the minted test token doesn't carry.
    assert "uploader_name" in body
    assert body["created_at"]
    # The three mapping blocks are always present, so the preview panel never
    # silently omits one (an omitted block reads as "nothing to report").
    assert body["section_map"] == {
        "sections": [], "unmapped_house": [], "extra_sections": []
    }


def test_create_from_an_uploaded_md_file(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client, name="Acme PRD v3")
    assert resp.status_code == 201, resp.text
    assert resp.json()["source_md"] == _SOURCE


def test_upload_without_a_name_falls_back_to_the_filename(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client, filename="Acme PRD v3.md", name=None)
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "Acme PRD v3"


def test_two_formats_may_share_a_name(tenant_client):
    # Names are free text, not invocation triggers — none of the custom-skills
    # slug deconfliction applies, and neither upload replaces the other.
    t = tenant_client.make(slug="acme")
    first = _create(t.client).json()
    second = _create(t.client).json()
    assert first["id"] != second["id"]
    assert [r["name"] for r in t.client.get(_URL).json()["templates"]] == [
        "Acme PRD v3", "Acme PRD v3"
    ]


# ─── the validation ladder ───────────────────────────────────────────────────


def test_missing_name_is_422(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _create(t.client, name="  ").status_code == 422


def test_over_limit_name_is_422(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _create(t.client, name="x" * 121).status_code == 422


def test_missing_or_unknown_artifact_type_is_422(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _create(t.client, artifact_type="").status_code == 422
    assert _create(t.client, artifact_type="roadmap").status_code == 422


def test_empty_source_is_400(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _create(t.client, source_md="   \n  ").status_code == 400


def test_source_over_the_character_cap_is_413(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    import app.routes.artifact_templates as routes_mod

    # Patched on the ROUTE module, which is where the cap is resolved and handed
    # to the store — the same shape routes/custom_skills.py uses.
    monkeypatch.setattr(routes_mod, "MAX_TEMPLATE_SOURCE_CHARS", 20)
    resp = _create(t.client, source_md="#" * 21)
    assert resp.status_code == 413, resp.text
    assert "20" in resp.json()["detail"]


def test_non_md_upload_is_422(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client, filename="format.docx", data=b"binary-ish")
    assert resp.status_code == 422
    assert ".md" in resp.json()["detail"]


def test_empty_upload_is_400(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, data=b"").status_code == 400


def test_upload_over_two_megabytes_is_413(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    import app.routes.artifact_templates as routes_mod

    monkeypatch.setattr(routes_mod, "MAX_TEMPLATE_UPLOAD_BYTES", 32)
    resp = _upload(t.client, data=b"#" * 64)
    assert resp.status_code == 413


def test_the_upload_read_is_bounded_by_the_cap(tenant_client, monkeypatch):
    """Read ONE byte past the cap and no further.

    nginx bounds the request at 50 MB, so reading the whole body before checking
    a 2 MB cap enforced that cap 25x looser than it read — an oversize upload
    was fully resident in memory before being rejected."""
    from starlette.datastructures import UploadFile

    from app.artifact_templates.store import MAX_TEMPLATE_UPLOAD_BYTES

    t = tenant_client.make(slug="acme")
    sizes: list[int] = []
    real_read = UploadFile.read

    async def _spy(self, size=-1):
        sizes.append(size)
        return await real_read(self, size)

    monkeypatch.setattr(UploadFile, "read", _spy)
    assert _upload(t.client).status_code == 201
    assert MAX_TEMPLATE_UPLOAD_BYTES + 1 in sizes
    # Never an unbounded read of the request body.
    assert -1 not in sizes


def test_a_nul_byte_in_an_upload_is_400_not_a_500(tenant_client):
    """A UTF-16LE file saved WITHOUT a BOM decodes as valid UTF-8 — U+0000 is a
    legal code point — so every character-level check passes and the NUL lands
    in `source_md`. Postgres `text` cannot hold one (SQLSTATE 22P05), so the
    user would get a 500 rather than the readable 400 this route already has
    for the undecodable case.

    NOTE this can only ever be asserted at the ROUTE: the SQLite fake stores
    NUL happily, so nothing downstream of the guard can fail in this suite."""
    t = tenant_client.make(slug="acme")
    utf16_no_bom = "# Acme PRD\n".encode("utf-16-le")
    assert b"\x00" in utf16_no_bom

    resp = _upload(t.client, data=utf16_no_bom)
    assert resp.status_code == 400, resp.text
    assert "text" in resp.json()["detail"].lower()


def test_a_nul_byte_in_pasted_or_patched_markdown_is_400(tenant_client):
    # The paste path never touches the upload route's byte-level guard, and
    # PATCH doesn't either — so the check lives in the store as well, where
    # both reach it.
    t = tenant_client.make(slug="acme")
    assert _create(t.client, source_md="# Acme\x00PRD\n").status_code == 400

    tid = _create(t.client).json()["id"]
    resp = t.client.patch(f"{_URL}/{tid}", json={"source_md": "# Acme\x00PRD\n"})
    assert resp.status_code == 400
    # And the row keeps the good source it already had.
    from app import db

    assert "\x00" not in db.get_template_by_id(t.company_id, tid)["source_md"]


def test_an_undecodable_upload_is_still_400(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, data=b"\xff\xfe\xfd bad bytes").status_code == 400


def test_a_malformed_json_body_is_400_not_a_500(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = t.client.post(
        _URL, content=b"{not json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 400


# ─── list ────────────────────────────────────────────────────────────────────


def test_list_is_newest_first_and_type_filtered(tenant_client):
    t = tenant_client.make(slug="acme")
    _create(t.client, name="Old PRD")
    _create(t.client, name="New PRD")
    _create(t.client, name="Ticket form", artifact_type="tickets")

    rows = t.client.get(_URL).json()["templates"]
    assert [r["name"] for r in rows] == ["Ticket form", "New PRD", "Old PRD"]
    assert [r["name"] for r in t.client.get(f"{_URL}?type=prd").json()["templates"]] == [
        "New PRD", "Old PRD"
    ]


def test_list_rejects_an_unknown_type_filter(tenant_client):
    t = tenant_client.make(slug="acme")
    assert t.client.get(f"{_URL}?type=roadmap").status_code == 422


def test_generation_enabled_is_top_level_and_present_on_an_empty_library(
    tenant_client,
):
    """The state most companies are in is zero rows, and the screen still
    renders all three group headers — a per-row flag would have nothing to hang
    off, which is why this is top-level.

    `prd` is now TRUE: `prd_runner.resolve_prd_template` genuinely reads this
    table, and an active PRD format governs every PRD the company generates. The
    other two are still FALSE because nothing reads them — the
    implementation-spec skeleton and the ticket description layout are later
    milestones. The map is served from ONE backend constant precisely so a
    screen can never tell a user their tickets changed when nothing did."""
    t = tenant_client.make(slug="acme")
    body = t.client.get(_URL).json()

    assert body["templates"] == []
    assert body["generation_enabled"] == {
        "prd": True, "tickets": False, "impl_spec": False
    }


def test_list_row_carries_the_note_summary_and_count(tenant_client):
    # The row copy is "…we couldn't find where your format lists evidence. See
    # all 3", so the summary alone is not enough — the count cannot be derived
    # from it, and without it the client either hides that more problems exist
    # or opens the preview to count them.
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    from app import db

    db.set_compile_result(
        company_id=t.company_id, template_id=tid, compile_status="needs_review",
        compile_notes=[
            {"code": "missing_evidence_list", "message": "No evidence list."},
            {"code": "missing_hypothesis", "message": "No hypothesis."},
            {"code": "missing_input_questions", "message": "No open questions."},
        ],
    )
    row = t.client.get(_URL).json()["templates"][0]
    assert row["compile_status"] == "needs_review"
    assert row["compile_summary"] == "No evidence list."
    assert row["compile_note_count"] == 3


def test_the_list_payload_reads_nothing_the_list_select_omits(isolated_settings):
    """Guard for a bug the fake Supabase structurally cannot catch.

    `list_templates` selects a narrow column set (no `source_md`, no
    `compiled`, no `section_map`), but the SQLite fake ignores `.select(cols)`
    and always returns every column — so a list payload that reached for
    `source_md` would pass every route test here and KeyError against real
    Postgres. Build a row containing ONLY the selected columns and put it
    through the real payload builder."""
    from app.db.artifact_templates import _LIST_COLUMNS
    from app.routes.artifact_templates import _list_item

    cols = [c.strip() for c in _LIST_COLUMNS.split(",")]
    row = {c: None for c in cols}
    row["id"] = "t-1"
    row["compile_notes"] = []
    item = _list_item(row)

    assert item["id"] == "t-1"
    # Blank values still produce their line — never dropped (house rule).
    assert item["name"] == ""
    assert item["uploader_name"] == ""
    assert item["created_at"] is None
    assert item["source_chars"] == 0
    assert item["compile_status"] == "pending"
    assert item["is_active"] is False
    assert item["compile_summary"] is None
    assert item["compile_note_count"] == 0


# ─── tenant isolation: 404, never 403 ────────────────────────────────────────


def test_another_companys_template_is_absent_from_the_library(tenant_client):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="beta")
    _create(a.client, name="Acme PRD v3")
    _create(b.client, name="Acme PRD v3")

    a_rows = a.client.get(_URL).json()["templates"]
    b_rows = b.client.get(_URL).json()["templates"]
    assert len(a_rows) == 1 and len(b_rows) == 1
    assert a_rows[0]["id"] != b_rows[0]["id"]


def test_a_foreign_id_is_404_on_every_route(tenant_client):
    """A foreign id and a missing id must be indistinguishable. Both callers
    here are company OWNERS, so nothing is masked by a role gate — a 403 on any
    of these would leak that the id names somebody else's real row."""
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="beta")
    theirs = _create(b.client).json()["id"]
    _make_ready(b.company_id, theirs)
    missing = str(uuid.uuid4())

    for tid in (theirs, missing):
        assert a.client.get(f"{_URL}/{tid}").status_code == 404
        assert a.client.get(f"{_URL}/{tid}/preview").status_code == 404
        assert a.client.patch(f"{_URL}/{tid}", json={"name": "x"}).status_code == 404
        assert a.client.post(f"{_URL}/{tid}/compile").status_code == 404
        assert a.client.post(f"{_URL}/{tid}/activate").status_code == 404
        assert a.client.post(f"{_URL}/{tid}/deactivate").status_code == 404
        assert a.client.delete(f"{_URL}/{tid}").status_code == 404

    # And nothing was written to the other tenant's row along the way.
    still = b.client.get(f"{_URL}/{theirs}").json()
    assert still["name"] == "Acme PRD v3"
    assert still["compile_status"] == "ready"
    assert still["is_active"] is False

    # A NON-ADMIN hitting a foreign id gets the 404 too, not the role gate's
    # 403. Nothing leaks either way — the 403 is uniform across every id for a
    # member — but the client maps the two to different outcomes: a 404 drops
    # the row from the list, a 403 leaves it there with a denial line. Answering
    # 403 for a row a teammate just deleted strands a phantom nobody can
    # dismiss, so ownership is checked first on every route that has both gates.
    member = _member(tenant_client, a)
    for tid in (theirs, missing):
        assert a.client.post(
            f"{_URL}/{tid}/activate", headers=member
        ).status_code == 404
        assert a.client.post(
            f"{_URL}/{tid}/deactivate", headers=member
        ).status_code == 404
        assert a.client.delete(f"{_URL}/{tid}", headers=member).status_code == 404


def test_activating_your_own_format_leaves_the_other_tenants_alone(tenant_client):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="beta")
    mine = _create(a.client).json()["id"]
    theirs = _create(b.client).json()["id"]
    _make_ready(a.company_id, mine)
    _make_ready(b.company_id, theirs)

    assert a.client.post(f"{_URL}/{mine}/activate").status_code == 200
    assert b.client.get(f"{_URL}/{theirs}").json()["is_active"] is False
    # Both companies may hold an active PRD format at the same time.
    assert b.client.post(f"{_URL}/{theirs}/activate").status_code == 200
    assert a.client.get(f"{_URL}/{mine}").json()["is_active"] is True


# ─── CSRF backstop ───────────────────────────────────────────────────────────


def test_every_mutating_route_403s_without_an_origin_header(tenant_client):
    """The test clients default a same-origin `Origin` (conftest.py:36-60), so
    a route missing `Depends(require_same_origin)` would pass every other test
    in this file and still 403 a real browser. Sending Origin: null is the only
    way to see it."""
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    bad = {"origin": "https://evil.example"}

    assert t.client.post(_URL, json={}, headers=bad).status_code == 403
    assert t.client.patch(f"{_URL}/{tid}", json={"name": "x"}, headers=bad).status_code == 403
    assert t.client.post(f"{_URL}/{tid}/compile", headers=bad).status_code == 403
    assert t.client.post(f"{_URL}/{tid}/activate", headers=bad).status_code == 403
    assert t.client.post(f"{_URL}/{tid}/deactivate", headers=bad).status_code == 403
    assert t.client.delete(f"{_URL}/{tid}", headers=bad).status_code == 403
    # The read routes are unaffected — they mutate nothing.
    assert t.client.get(_URL, headers=bad).status_code == 200
    assert t.client.get(f"{_URL}/{tid}", headers=bad).status_code == 200


# ─── detail + preview ────────────────────────────────────────────────────────


def test_preview_names_its_format_explicitly(tenant_client):
    """`format` is an explicit discriminator, never sniffed from a leading `<`:
    a markdown format that happens to open with a `<br>` would otherwise render
    as raw HTML."""
    t = tenant_client.make(slug="acme")
    prd = _create(t.client, name="P").json()["id"]
    tickets = _create(t.client, name="T", artifact_type="tickets").json()["id"]
    spec = _create(t.client, name="S", artifact_type="impl_spec").json()["id"]

    assert t.client.get(f"{_URL}/{prd}/preview").json()["format"] == "html"
    assert t.client.get(f"{_URL}/{tickets}/preview").json()["format"] == "markdown"
    assert t.client.get(f"{_URL}/{spec}/preview").json()["format"] == "markdown"


def test_preview_of_an_unchecked_format_is_an_empty_body_not_an_error(tenant_client):
    # The preview is the primary diagnostic for a format that hasn't mapped
    # cleanly, so refusing it for a non-ready row would remove the diagnosis
    # exactly when it is needed. All three mapping blocks still render.
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    body = t.client.get(f"{_URL}/{tid}/preview").json()

    assert body["compile_status"] == "pending"
    assert body["body"] == ""
    assert body["section_map"] == {
        "sections": [], "unmapped_house": [], "extra_sections": []
    }


def test_preview_carries_the_compiled_skeleton_and_the_map(tenant_client):
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    _make_ready(t.company_id, tid)
    body = t.client.get(f"{_URL}/{tid}/preview").json()

    assert body["compile_status"] == "ready"
    assert "<h1>Acme PRD</h1>" in body["body"]
    assert body["section_map"]["sections"][0]["customer"] == "Background"
    assert body["section_map"]["unmapped_house"] == ["Riskiest assumption"]
    assert body["section_map"]["extra_sections"] == ["Launch checklist"]


# ─── edit ────────────────────────────────────────────────────────────────────


def test_rename_leaves_the_source_and_the_status_alone(tenant_client):
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    _make_ready(t.company_id, tid)

    body = t.client.patch(f"{_URL}/{tid}", json={"name": "Acme PRD v4"}).json()
    assert body["name"] == "Acme PRD v4"
    assert body["source_md"] == _SOURCE
    # A rename is not a re-upload — it must not send a checked format back to
    # the queue and out of use.
    assert body["compile_status"] == "ready"


def test_a_rename_by_someone_else_does_not_rewrite_who_uploaded_it(tenant_client):
    """Provenance moves with the CONTENT, never with a rename.

    The route sends workspace_id and the uploader fields on every PATCH; the
    store now forwards them only when the source actually changed. Without that
    gate any member could take over the attribution of any format in the
    company just by renaming it — the row's "Uploaded by Ada" line silently
    became "Uploaded by whoever last fixed a typo" — and workspace_id started
    claiming a format originated in whichever workspace last renamed it, which
    is exactly what the migration header promises it does not do."""
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    from app import db

    before = db.get_template_by_id(t.company_id, tid)
    member = _member(tenant_client, t)

    resp = t.client.patch(f"{_URL}/{tid}", json={"name": "Renamed"}, headers=member)
    assert resp.status_code == 200, resp.text

    after = db.get_template_by_id(t.company_id, tid)
    assert after["name"] == "Renamed"
    assert after["uploader_id"] == before["uploader_id"]
    assert after["uploader_name"] == before["uploader_name"]
    assert after["workspace_id"] == before["workspace_id"]


def test_replacing_the_source_does_move_the_provenance(tenant_client):
    # The other half of the same rule: a re-upload IS a new version of the
    # format, so the row records who supplied it and from where.
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    from app import db

    before = db.get_template_by_id(t.company_id, tid)
    member = _member(tenant_client, t)

    resp = t.client.patch(
        f"{_URL}/{tid}", json={"source_md": "# A different form\n"}, headers=member
    )
    assert resp.status_code == 200, resp.text

    after = db.get_template_by_id(t.company_id, tid)
    assert after["uploader_id"] != before["uploader_id"]


def test_replacing_the_source_requeues_but_keeps_the_last_good_skeleton(
    tenant_client,
):
    """The recompile invariant, through the route.

    An ACTIVE format whose source is replaced stays active and keeps its
    compiled skeleton, so generation goes on using the last good version while
    the new one is checked. Blanking it would silently reformat every document
    the company generated for the duration, with nothing to connect the two."""
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    _make_ready(t.company_id, tid)
    t.client.post(f"{_URL}/{tid}/activate")

    body = t.client.patch(f"{_URL}/{tid}", json={"source_md": "# New form\n"}).json()
    assert body["source_md"] == "# New form\n"
    assert body["compile_status"] == "pending"
    assert body["is_active"] is True
    assert "<h1>Acme PRD</h1>" in t.client.get(f"{_URL}/{tid}/preview").json()["body"]


def test_an_empty_patch_is_422(tenant_client):
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    assert t.client.patch(f"{_URL}/{tid}", json={}).status_code == 422


def test_patch_runs_the_same_validation_ladder_as_create(tenant_client):
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    assert t.client.patch(f"{_URL}/{tid}", json={"name": "  "}).status_code == 422
    assert t.client.patch(f"{_URL}/{tid}", json={"name": "x" * 121}).status_code == 422
    assert t.client.patch(f"{_URL}/{tid}", json={"source_md": " "}).status_code == 400


# ─── compile ─────────────────────────────────────────────────────────────────


def test_compile_asks_for_a_check_and_answers_the_preview_shape(
    tenant_client, monkeypatch
):
    """The "Check again" button: it starts a check and answers the preview
    shape, so the caller restarts polling from the response rather than
    guessing.

    The check itself is stubbed out here by conftest's
    `_no_background_template_compile` — what a REAL run does to the row is
    test_artifact_template_compile.py's job. What this file owns is that the
    route asks for one, with this template's id, and that asking never disturbs
    the skeleton the company may be generating with right now."""
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    from app import db

    db.set_compile_result(
        company_id=t.company_id, template_id=tid, compile_status="needs_review",
        compiled="<html><style></style><h1>Old</h1></html>",
        compile_notes=[{"code": "missing_evidence_list", "message": "No evidence list."}],
    )

    import app.routes.artifact_templates as routes_mod

    asked: list = []
    monkeypatch.setattr(
        routes_mod, "schedule_compile",
        lambda company_id, template_id: (asked.append(template_id), False)[1],
    )

    resp = t.client.post(f"{_URL}/{tid}/compile")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert asked == [tid]
    # The preview shape, so the client can poll from it.
    assert body["format"] == "html"
    assert set(body) >= {"compile_status", "compile_notes", "body", "section_map"}
    # Asking for a re-check NEVER blanks the last good skeleton — an active
    # format keeps generating with it until a new one validates.
    assert "<h1>Old</h1>" in body["body"]


# ─── activation ──────────────────────────────────────────────────────────────


def test_activating_an_unchecked_format_is_409_with_its_notes(tenant_client):
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    from app import db

    db.set_compile_result(
        company_id=t.company_id, template_id=tid, compile_status="needs_review",
        compile_notes=[{"code": "missing_evidence_list", "message": "No evidence list."}],
    )

    resp = t.client.post(f"{_URL}/{tid}/activate")
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    # Same {code, message} vocabulary as compile_notes, so the client translates
    # one set of codes rather than two.
    assert detail["code"] == "not_ready"
    assert detail["notes"] == [
        {"code": "missing_evidence_list", "message": "No evidence list."}
    ]
    assert t.client.get(f"{_URL}/{tid}").json()["is_active"] is False


def test_activate_switches_the_active_format_over(tenant_client):
    t = tenant_client.make(slug="acme")
    v2 = _create(t.client, name="Acme PRD v2").json()["id"]
    v3 = _create(t.client, name="Acme PRD v3").json()["id"]
    _make_ready(t.company_id, v2)
    _make_ready(t.company_id, v3)

    assert t.client.post(f"{_URL}/{v2}/activate").json()["is_active"] is True
    assert t.client.post(f"{_URL}/{v3}/activate").json()["is_active"] is True

    rows = {r["id"]: r for r in t.client.get(_URL).json()["templates"]}
    assert rows[v3]["is_active"] is True
    # The outgoing format stays in the library — switching back is one click.
    assert rows[v2]["is_active"] is False


def test_the_raced_409_never_prints_the_raw_enum(tenant_client, monkeypatch):
    """`impl_spec` is a column value, not a word anybody typed. The refusal
    reads "your team's engineering spec format" — ARTIFACT_TYPE_LABELS exists
    for exactly this and assert_activatable's refusal already uses it."""
    t = tenant_client.make(slug="acme")
    tid = _create(t.client, artifact_type="impl_spec").json()["id"]
    _make_ready(t.company_id, tid)

    import app.routes.artifact_templates as routes_mod

    def _raced(*a, **k):
        raise routes_mod.db.ActiveTemplateConflict("impl_spec")

    monkeypatch.setattr(routes_mod.db, "activate_template", _raced)

    resp = t.client.post(f"{_URL}/{tid}/activate")
    assert resp.status_code == 409
    message = resp.json()["detail"]["message"]
    assert "engineering spec" in message
    assert "impl_spec" not in message


def test_deactivate_returns_the_type_to_the_builtin(tenant_client):
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    _make_ready(t.company_id, tid)
    t.client.post(f"{_URL}/{tid}/activate")

    assert t.client.post(f"{_URL}/{tid}/deactivate").json()["is_active"] is False
    # Idempotent — a double-click is not an error.
    assert t.client.post(f"{_URL}/{tid}/deactivate").status_code == 200
    from app import db

    assert db.get_active_template(t.company_id, "prd") is None


def test_a_plain_member_cannot_change_the_teams_format(tenant_client):
    """403, not 404. `_owned_or_404` has already run, so the caller can see
    this row in their own library — there is no existence left to protect, and
    a 404 would tell a member their own company's format had vanished. This is
    a ROLE check; ownership mismatches on the same routes still 404."""
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    _make_ready(t.company_id, tid)
    member = _member(tenant_client, t)

    denied = t.client.post(f"{_URL}/{tid}/activate", headers=member)
    assert denied.status_code == 403
    # Assert the REASON, not just the status: require_workspace answers 403 too
    # ("Not a member of this workspace"), so a broken fixture would otherwise
    # make this test pass for entirely the wrong reason.
    assert denied.json()["detail"] == "Only an admin can change your team's format."
    denied = t.client.post(f"{_URL}/{tid}/deactivate", headers=member)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Only an admin can change your team's format."
    # Reading is never role-gated — a member still sees what's in use.
    assert t.client.get(_URL, headers=member).status_code == 200
    assert t.client.get(f"{_URL}/{tid}/preview", headers=member).status_code == 200
    assert t.client.get(f"{_URL}/{tid}").json()["is_active"] is False


def test_a_member_may_still_add_rename_and_recheck_a_format(tenant_client):
    t = tenant_client.make(slug="acme")
    member = _member(tenant_client, t)

    created = _create(t.client, headers=member)
    assert created.status_code == 201, created.text
    tid = created.json()["id"]
    assert t.client.patch(f"{_URL}/{tid}", json={"name": "Renamed"},
                          headers=member).status_code == 200
    assert t.client.post(f"{_URL}/{tid}/compile", headers=member).status_code == 200


# ─── delete ──────────────────────────────────────────────────────────────────


def test_a_member_may_delete_a_format_nobody_is_using(tenant_client):
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    member = _member(tenant_client, t)

    body = t.client.delete(f"{_URL}/{tid}", headers=member).json()
    assert body["deleted"] is True
    assert body["fell_back_to_builtin"] is False
    assert t.client.get(_URL).json()["templates"] == []


def test_a_member_cannot_delete_the_format_the_team_is_using(tenant_client):
    """Falling back to the built-in is byte-for-byte the effect of
    deactivating, which is admin-gated — leaving delete open would let a plain
    member reset company-wide formatting through the side door."""
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    _make_ready(t.company_id, tid)
    t.client.post(f"{_URL}/{tid}/activate")
    member = _member(tenant_client, t)

    denied = t.client.delete(f"{_URL}/{tid}", headers=member)
    assert denied.status_code == 403
    assert denied.json()["detail"] == (
        "Only an admin can delete the format your team is using."
    )
    assert t.client.get(f"{_URL}/{tid}").json()["is_active"] is True


def test_an_admin_deleting_the_active_format_reports_the_fallback(tenant_client):
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    _make_ready(t.company_id, tid)
    t.client.post(f"{_URL}/{tid}/activate")

    body = t.client.delete(f"{_URL}/{tid}").json()
    assert body["fell_back_to_builtin"] is True
    assert body["artifact_type"] == "prd"
    from app import db

    assert db.get_active_template(t.company_id, "prd") is None
    assert t.client.get(_URL).json()["templates"] == []


# ─── what generation actually honours ────────────────────────────────────────


def test_activating_a_prd_format_really_changes_what_generation_uses(tenant_client):
    """This library stopped being inert.

    Activating a PRD format now genuinely changes the skeleton every PRD in the
    company is written into — `prd_runner.resolve_prd_template` resolves it, and
    the prompt-level proof lives in test_prd_runner.py. What this asserts is the
    contract between the two: the row the route activated is the row the runner
    resolves, and `generation_enabled` tells the truth about it.

    The vendored template is untouched and remains the fallback for every
    company without a format — see
    test_prd_runner.py::test_no_active_format_leaves_the_part_a_prompt_byte_identical."""
    t = tenant_client.make(slug="acme")
    tid = _create(t.client).json()["id"]
    _make_ready(t.company_id, tid)
    t.client.post(f"{_URL}/{tid}/activate")

    import app.prd_runner as prd_runner

    template, template_id = prd_runner.resolve_prd_template(t.company_id)
    assert template_id == tid
    assert "<h1>Acme PRD</h1>" in template
    assert template != prd_runner._load_part_a_template()
    assert t.client.get(_URL).json()["generation_enabled"]["prd"] is True


def test_tickets_and_impl_spec_are_still_inert(tenant_client):
    """The other two types have a full library, checks and activation — and no
    reader. `generation_enabled` is what stops a screen implying otherwise, so
    it has to stay false until the milestone that makes each true."""
    t = tenant_client.make(slug="acme")
    for artifact_type in ("tickets", "impl_spec"):
        tid = _create(t.client, name=f"{artifact_type} form",
                      artifact_type=artifact_type).json()["id"]
        _make_ready(t.company_id, tid)
        assert t.client.post(f"{_URL}/{tid}/activate").status_code == 200

    enabled = t.client.get(_URL).json()["generation_enabled"]
    assert enabled["tickets"] is False
    assert enabled["impl_spec"] is False

    # The impl-spec generator still loads the vendored B0-B9 skeleton.
    import app.prd_runner as prd_runner

    assert "B0" in prd_runner._load_part_b_template()

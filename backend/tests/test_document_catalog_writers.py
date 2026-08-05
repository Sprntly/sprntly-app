"""Catalog registration from the upload and chat-attachment writers.

Two of the four writers live here (Drive and Confluence have their own files,
because each carries a rule the others don't):

  uploads           `document_sources.add_document_file`, plus BOTH delete
                    paths. The bulk delete is the interesting one — see
                    `test_deleting_a_source_deregisters_every_file`.
  chat attachments  the turn-persist route, session-scoped.

Both are on a USER-FACING path, so both share the same two obligations:
registration never breaks the host operation (AC12), and the model call is
never made synchronously inside the request.
"""
from __future__ import annotations

import pytest

_CID = "co-writers"


@pytest.fixture
def catalog_calls(monkeypatch):
    """Record register/deregister without touching the LLM or embeddings."""
    from app import document_catalog

    calls = {"register": [], "deregister": []}
    real_register = document_catalog.register_document

    def _register(company_id, **kw):
        calls["register"].append({"company_id": company_id, **kw})
        return real_register(company_id, **kw)

    def _deregister(company_id, provider, external_id):
        calls["deregister"].append((company_id, provider, external_id))

    monkeypatch.setattr(document_catalog, "register_document", _register)
    monkeypatch.setattr(document_catalog, "deregister_document", _deregister)
    monkeypatch.setattr(document_catalog, "llm_call", lambda **k: type(
        "R", (), {"output": {"summary": "Seat pricing becomes usage-based in Q3.",
                             "topics": ["pricing"]}})())
    monkeypatch.setattr(document_catalog, "embed_texts",
                        lambda texts, **k: [[0.1] * 1536])
    return calls


def _seed_company(db, company_id=_CID):
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        db.table("companies").insert(
            {"id": company_id, "slug": f"s-{company_id}", "display_name": "C"}
        ).execute()


# ═════════════════════════ uploads — T6 ════════════════════════════════════


def test_an_uploaded_file_is_registered(isolated_settings, catalog_calls):
    """T6/AC11: provider `uploads`, keyed on the document_source_file id, and
    company-scoped (never session-scoped)."""
    from app import document_catalog, document_sources

    db = isolated_settings["supabase"]
    _seed_company(db)
    src = document_sources.create_document_source(
        _CID, name="Pricing research", description="Everything pricing"
    )
    saved = document_sources.add_document_file(
        _CID, src.id, filename="Q3_pricing.txt", data=b"Seat pricing becomes usage-based.",
    )

    assert len(catalog_calls["register"]) == 1
    call = catalog_calls["register"][0]
    assert call["company_id"] == _CID
    assert call["provider"] == "uploads"
    assert call["external_id"] == saved.id
    assert call["title"] == "Q3_pricing.txt"
    assert call["source_name"] == "Pricing research"
    assert call["description"] == "Everything pricing"
    assert call.get("conversation_id") is None
    # The body already lives in document_source_file and resolves through
    # get_file_text — a document's text is never stored twice.
    assert call.get("body_text") is None

    doc = document_catalog.fetch_document(_CID, "uploads", saved.id)
    assert doc is not None and doc.title == "Q3_pricing.txt"


def test_upload_summarisation_is_backgrounded(isolated_settings, catalog_calls):
    """AC16a, load-bearing. The uploads route stores up to
    UPLOAD_MAX_FILES_PER_REQUEST files SEQUENTIALLY inside the request
    coroutine. The row insert is a cheap upsert and stays synchronous; the
    model call must not be, or a 20-file upload gains twenty serial
    round-trips on a response that is instant today."""
    from app import document_sources

    db = isolated_settings["supabase"]
    _seed_company(db)
    src = document_sources.create_document_source(_CID, name="S")
    document_sources.add_document_file(
        _CID, src.id, filename="a.txt", data=b"text"
    )
    assert catalog_calls["register"][0]["background"] is True


def test_a_catalog_failure_never_fails_the_upload(isolated_settings, monkeypatch):
    """T7/AC12: an upload that succeeds today must still succeed if
    cataloguing is broken — the file is stored either way."""
    from app import document_catalog, document_sources

    db = isolated_settings["supabase"]
    _seed_company(db)

    def _boom(company_id, **kw):
        raise RuntimeError("catalog down")

    monkeypatch.setattr(document_catalog, "register_document", _boom)
    src = document_sources.create_document_source(_CID, name="S")
    saved = document_sources.add_document_file(
        _CID, src.id, filename="a.txt", data=b"still stored"
    )

    assert saved.id
    assert document_sources.get_file_text(_CID, saved.id) == "still stored"


# ═════════════════════════ uploads — T7b, deletes ══════════════════════════


def test_deleting_a_source_deregisters_every_file(isolated_settings, catalog_calls):
    """T7b/AC11, the bulk path.

    `delete_document_source` removes every file under a source in ONE query
    without ever naming their ids, and the catalog has no `source_id` column
    to delete by. So the ids must be ENUMERATED BEFORE the delete. Doing it
    after leaves nothing to enumerate and strands one summarised, still
    discoverable catalog row per deleted file."""
    from app import document_sources

    db = isolated_settings["supabase"]
    _seed_company(db)
    src = document_sources.create_document_source(_CID, name="S")
    ids = [
        document_sources.add_document_file(
            _CID, src.id, filename=f"f{i}.txt", data=f"body {i}".encode()
        ).id
        for i in range(3)
    ]

    catalog_calls["deregister"].clear()
    assert document_sources.delete_document_source(_CID, src.id) is True

    assert sorted(e for _, _, e in catalog_calls["deregister"]) == sorted(ids)
    assert {c for c, _, _ in catalog_calls["deregister"]} == {_CID}
    assert {p for _, p, _ in catalog_calls["deregister"]} == {"uploads"}


def test_deleting_a_source_really_removes_the_rows(isolated_settings, monkeypatch):
    """T7b end to end, with the real deregister running: no catalog row
    survives the source that owned it."""
    from app import document_catalog, document_sources

    db = isolated_settings["supabase"]
    _seed_company(db)
    monkeypatch.setattr(document_catalog, "llm_call", lambda **k: type(
        "R", (), {"output": {"summary": "Body.", "topics": ["t"]}})())
    monkeypatch.setattr(document_catalog, "embed_texts",
                        lambda texts, **k: [[0.1] * 1536])

    src = document_sources.create_document_source(_CID, name="S")
    for i in range(3):
        document_sources.add_document_file(
            _CID, src.id, filename=f"f{i}.txt", data=f"body {i}".encode()
        )
    assert len(document_catalog.list_documents(_CID)) == 3

    document_sources.delete_document_source(_CID, src.id)
    assert document_catalog.list_documents(_CID) == []


def test_deleting_one_file_deregisters_only_that_file(isolated_settings, catalog_calls):
    """The single-id path needs no enumeration — and must not take its
    siblings with it."""
    from app import document_sources

    db = isolated_settings["supabase"]
    _seed_company(db)
    src = document_sources.create_document_source(_CID, name="S")
    keep = document_sources.add_document_file(
        _CID, src.id, filename="keep.txt", data=b"keep"
    )
    drop = document_sources.add_document_file(
        _CID, src.id, filename="drop.txt", data=b"drop"
    )

    catalog_calls["deregister"].clear()
    assert document_sources.delete_document_file(_CID, src.id, drop.id) is True
    assert catalog_calls["deregister"] == [(_CID, "uploads", drop.id)]
    assert keep.id not in [e for _, _, e in catalog_calls["deregister"]]


def test_a_foreign_companys_delete_deregisters_nothing(isolated_settings, catalog_calls):
    """The delete is refused before it reaches the catalog, so a guessed
    source id can never strip another tenant's rows."""
    from app import document_sources

    db = isolated_settings["supabase"]
    _seed_company(db)
    _seed_company(db, "co-other")
    src = document_sources.create_document_source(_CID, name="S")
    document_sources.add_document_file(_CID, src.id, filename="a.txt", data=b"a")

    catalog_calls["deregister"].clear()
    assert document_sources.delete_document_source("co-other", src.id) is False
    assert catalog_calls["deregister"] == []


# ═════════════════════ chat attachments — T6, T7 ═══════════════════════════


def _conversation(client, title="Chat"):
    resp = client.post("/v1/conversations", json={"title": title})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_chat_attachment_is_registered_session_scoped(
    tenant_client, catalog_calls
):
    """T6/AC11: `conversation_id` AND `user_id` both set (from the verified
    context, never the request body), and the synthetic external id matching
    the shape the ask path's document manifest already uses."""
    t = tenant_client.make(slug="acme")
    conv = _conversation(t.client)

    resp = t.client.post(f"/v1/conversations/{conv['id']}/turns", json={
        "role": "user",
        "content": "here's the doc",
        "attachments": [
            {"name": "requirements.pdf", "content": "MUST prefill the cart."},
            {"name": "notes.md", "content": "Brand locked."},
        ],
    })
    assert resp.status_code == 200, resp.text
    turn_id = resp.json()["id"]

    assert len(catalog_calls["register"]) == 2
    first, second = catalog_calls["register"]
    assert first["provider"] == "chat_attachment"
    assert first["external_id"] == f"turn:{turn_id}:attachment:0"
    assert second["external_id"] == f"turn:{turn_id}:attachment:1"
    assert first["title"] == "requirements.pdf"
    assert first["conversation_id"] == conv["id"]
    assert first["user_id"] == t.user_id
    assert first["company_id"] == t.company_id
    # The text is already on the turn row and reaches the model via folded
    # history — never stored twice.
    assert first.get("body_text") is None
    # Turn-save is a request path too: the model call is backgrounded.
    assert first["background"] is True


def test_a_turn_without_attachments_registers_nothing(tenant_client, catalog_calls):
    t = tenant_client.make(slug="acme")
    conv = _conversation(t.client)
    resp = t.client.post(
        f"/v1/conversations/{conv['id']}/turns",
        json={"role": "user", "content": "plain question"},
    )
    assert resp.status_code == 200
    assert catalog_calls["register"] == []


def test_a_catalog_failure_never_fails_the_turn_save(tenant_client, monkeypatch):
    """T7/AC12: a turn that saves today must still save if cataloguing fails.
    A dropped user turn is how a reopened chat ends up showing "No response
    was generated"."""
    from app import document_catalog

    def _boom(company_id, **kw):
        raise RuntimeError("catalog down")

    monkeypatch.setattr(document_catalog, "register_document", _boom)
    t = tenant_client.make(slug="acme")
    conv = _conversation(t.client)

    resp = t.client.post(f"/v1/conversations/{conv['id']}/turns", json={
        "role": "user", "content": "here's the doc",
        "attachments": [{"name": "r.pdf", "content": "MUST prefill the cart."}],
    })
    assert resp.status_code == 200, resp.text

    turns = t.client.get(f"/v1/conversations/{conv['id']}/turns").json()["turns"]
    assert turns[0]["attachments"] == [
        {"name": "r.pdf", "content": "MUST prefill the cart."}
    ]


def test_no_writer_stores_a_document_body(isolated_settings, monkeypatch):
    """The catalog is a POINTER, not a COPY — asserted across ALL FOUR writers
    in one place, reading the stored rows rather than the call arguments.

    Every source keeps its own text where it already lived: uploads in
    `document_source_file`, chat attachments on the turn row, Drive in the
    dataset corpus markdown, Confluence in the wiki. Storing it again here
    would make this table a second copy of a customer's documents at rest, and
    would make connecting a wiki a data-retention decision it should not be.

    The column is kept for a future source with genuinely nowhere else to put
    its body, so this test is what forces that to be a deliberate act rather
    than a quiet one."""
    from app import document_catalog, document_sources
    from app.kg_ingest import drive_extract
    from app.kg_ingest.drive_extract import DriveDoc
    from app.kg_ingest.pullers import confluence

    db = isolated_settings["supabase"]
    _seed_company(db)
    monkeypatch.setattr(document_catalog, "llm_call", lambda **k: type(
        "R", (), {"output": {"summary": "Body.", "topics": ["t"]}})())
    monkeypatch.setattr(document_catalog, "embed_texts",
                        lambda texts, **k: [[0.1] * 1536])
    monkeypatch.setattr(
        drive_extract, "extract_document",
        lambda *a, **k: {"signals": 1, "themes": 0, "skipped": 0},
    )

    # 1. uploads
    src = document_sources.create_document_source(_CID, name="S")
    document_sources.add_document_file(
        _CID, src.id, filename="a.txt", data=b"upload body text"
    )

    # 2. Google Drive
    class _Facade:
        def create_source(self, *a, **k):
            return None

        def get_source(self, *a, **k):
            # The extractor reads the file's existing provenance row when the
            # doc it was handed carries no corpus location, so that a pass
            # which knows less than its predecessor cannot blank the stored
            # one. No prior row here.
            return None

    drive_extract.extract_drive_docs(_Facade(), _CID, [DriveDoc(
        file_id="drive-1", name="D", modified="2026-07-01T00:00:00Z",
        text="drive body text " * 500,
    )])

    # 3. Confluence
    class _Ctx:
        company_id = _CID
        site_url = "https://acme.atlassian.net/wiki"

    confluence._to_record(
        _Ctx(), {"id": "s1", "key": "ENG", "name": "Engineering"}, "page",
        {"id": "page-1", "title": "P",
         "body": {"storage": {"value": "<p>" + "confluence body " * 500 + "</p>",
                              "representation": "storage"}},
         "version": {"number": 1, "createdAt": "2026-07-30T00:00:00Z"}},
    )

    # 4. chat attachment
    conversation_id = db.table("conversations").insert(
        {"company_id": _CID, "user_id": "user-1", "title": "t"}
    ).execute().data[0]["id"]
    document_catalog.register_document(
        _CID, provider=document_catalog.PROVIDER_CHAT_ATTACHMENT,
        external_id="turn:1:attachment:0", title="c.pdf",
        content_hash="h", conversation_id=conversation_id, user_id="user-1",
        get_text=lambda: "attachment body text",
    )

    rows = db.table("document_catalog").select("*").eq(
        "company_id", _CID
    ).execute().data
    assert {r["provider"] for r in rows} == {
        "uploads", "google_drive", "confluence", "chat_attachment"
    }, "a writer did not register — this test would pass vacuously"
    for row in rows:
        assert row["body_text"] is None, (
            f"the {row['provider']} writer stored a document body in the "
            "catalog; the catalog holds summaries and pointers, not copies"
        )
        # ...and each still produced a real summary from its full text.
        assert row["summary"], f"{row['provider']} registered without a summary"


def test_a_session_document_is_only_readable_by_its_owner(
    tenant_client, catalog_calls
):
    """The registration and the read rule meet: the row a chat attachment
    creates is invisible to a teammate holding the real conversation id."""
    from app import document_catalog

    t = tenant_client.make(slug="acme")
    conv = _conversation(t.client)
    t.client.post(f"/v1/conversations/{conv['id']}/turns", json={
        "role": "user", "content": "doc",
        "attachments": [{"name": "r.pdf", "content": "MUST prefill the cart."}],
    })

    owner_view = document_catalog.list_documents(
        t.company_id, conversation_id=conv["id"], user_id=t.user_id
    )
    assert [d.title for d in owner_view] == ["r.pdf"]

    assert document_catalog.list_documents(
        t.company_id, conversation_id=conv["id"], user_id="someone-else"
    ) == []

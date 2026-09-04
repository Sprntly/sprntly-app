"""Backend tests for the project document-upload slice:
`POST /v1/projects/{id}/documents` (routes/projects.py) plus the read wire it
depends on (`project_group_context._artifact_content_for` +
`_TYPE_LABELS`).

A member uploads a file; it is converted to text (no LLM, no OCR), rendered to
sanitized HTML, stored as a `custom_artifacts` row (kind "document"), and
attached to the project through the idempotent `add_artifact` choke-point. The
agent then reads its body through `_artifact_content_for`.

Covers:
  - happy path: 200, a `custom_artifacts` row + a `project_artifacts` ref, and
    the returned DTO matches the drawer fan-out's `custom_artifact` shape
  - membership gate (AD-P11): a same-tenant non-member is 403'd
  - validation parity with `routes/ask.py::extract_file`: empty→400,
    oversize→413, unreadable(binary/scanned/legacy)→422, and an
    over-`MAX_BODY_CHARS` body is a clean 413 (not a 500)
  - the read wire returns the stored body (clamped), not None
  - the breadth-manifest label reads "Documents"
"""
from __future__ import annotations

import pytest

from tests import _fake_supabase
from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member

# The project artifact fan-out (`list_artifacts_for_project`) reads the
# `prototypes` table, which conftest deliberately keeps OUT of the shared base
# schema (see its note). Any test that drives the fan-out — the GET
# `/artifacts` shape check and the read-wire's manifest gate — needs it present.
_PROTOTYPE_DDL = """
CREATE TABLE IF NOT EXISTS prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    variant                TEXT NOT NULL DEFAULT 'v1',
    template_version       INTEGER NOT NULL DEFAULT 1,
    instructions           TEXT,
    target_platform        TEXT NOT NULL DEFAULT 'both',
    figma_file_key         TEXT,
    website_url            TEXT,
    github_installation_id INTEGER,
    bundle_url             TEXT,
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    preview_image_url      TEXT,
    is_complete            INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT
);
"""


@pytest.fixture
def docs_env(isolated_settings):
    """conftest's reset fake DB + the `prototypes` table the fan-out reads."""
    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    yield


def _create_project(ctx, *, name: str = "Docs project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def _upload(ctx, project_id, *, filename: str, content: bytes, content_type: str = "text/markdown", headers=None):
    return ctx.client.post(
        f"/v1/projects/{project_id}/documents",
        files={"file": (filename, content, content_type)},
        headers=headers,
    )


def _custom_artifact_rows(company_id: str) -> list[dict]:
    from app.db.client import require_client

    return (
        require_client().table("custom_artifacts")
        .select("*")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )


def _project_artifact_refs(project_id: int) -> list[dict]:
    from app.db.client import require_client

    return (
        require_client().table("project_artifacts")
        .select("*")
        .eq("project_id", project_id)
        .execute()
        .data
        or []
    )


# ── Happy path ──────────────────────────────────────────────────────────────


def test_upload_creates_document_artifact_and_ref(docs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    r = _upload(
        ctx, project["id"],
        filename="Launch Plan.md",
        content=b"# Launch Plan\n\nShip on **Friday**.\n",
    )
    assert r.status_code == 200, r.text
    dto = r.json()

    # A custom_artifacts row exists, kind="document", non-empty body_html.
    rows = _custom_artifact_rows(ctx.company_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "document"
    assert row["title"] == "Launch Plan"
    assert row["status"] == "ready"
    assert row["body_html"].strip() != ""
    # Markdown was rendered to HTML (not stored as raw markdown).
    assert "<h1" in row["body_html"] and "<strong>" in row["body_html"]

    # A project_artifacts ref was written for this document.
    refs = _project_artifact_refs(project["id"])
    assert [(x["artifact_type"], x["artifact_id"]) for x in refs] == [
        ("custom_artifact", row["id"])
    ]

    # DTO carries the document's identity.
    assert dto["type"] == "custom_artifact"
    assert dto["id"] == row["id"]
    assert dto["title"] == "Launch Plan"
    assert dto["kind"] == "document"


def test_upload_dto_matches_drawer_fanout_shape(docs_env, monkeypatch):
    """The returned item has the SAME key set the drawer's fan-out publishes
    for a custom_artifact, so the FE can optimistically insert it."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    r = _upload(ctx, project["id"], filename="notes.txt", content=b"just some notes")
    assert r.status_code == 200
    dto = r.json()

    fanout = ctx.client.get(f"/v1/projects/{project['id']}/artifacts").json()["artifacts"]
    doc_item = next(a for a in fanout if a["type"] == "custom_artifact")

    assert set(dto.keys()) == set(doc_item.keys())
    assert doc_item["id"] == dto["id"]


# ── Membership gate ─────────────────────────────────────────────────────────


def test_non_member_cannot_upload(docs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _user_id, non_member_headers = seed_same_tenant_non_member(ctx)

    r = _upload(
        ctx, project["id"], filename="x.md", content=b"hello",
        headers=non_member_headers,
    )
    assert r.status_code == 403
    # No document, no ref written on the refused path.
    assert _custom_artifact_rows(ctx.company_id) == []
    assert _project_artifact_refs(project["id"]) == []


# ── Validation parity with extract_file ─────────────────────────────────────


def test_empty_file_is_400(docs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    r = _upload(ctx, project["id"], filename="empty.md", content=b"")
    assert r.status_code == 400
    assert _custom_artifact_rows(ctx.company_id) == []


def test_oversize_file_is_413(docs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    r = _upload(
        ctx, project["id"], filename="big.md",
        content=b"a" * (25 * 1024 * 1024 + 1),
    )
    assert r.status_code == 413
    assert _custom_artifact_rows(ctx.company_id) == []


def test_unreadable_binary_is_422(docs_env, monkeypatch):
    """A binary/unsupported type extracts to the non-empty unparsed stub, which
    must still be refused as unreadable — same 422 the chat extractor gives."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    # PNG magic + a NUL byte → `_looks_textual` False → `is_unparsed_stub` True.
    r = _upload(
        ctx, project["id"], filename="scan.png",
        content=b"\x89PNG\r\n\x1a\n\x00\x00binarynoise",
        content_type="image/png",
    )
    assert r.status_code == 422
    assert "Scanned/image-only PDFs" in r.json()["detail"]
    assert _custom_artifact_rows(ctx.company_id) == []
    assert _project_artifact_refs(project["id"]) == []


def test_over_max_body_chars_is_clean_413_not_500(docs_env, monkeypatch):
    """A body that survives conversion but exceeds `MAX_BODY_CHARS` (400k) is a
    clean 413, never an unhandled `BodyTooLarge` → 500."""
    from app.db.custom_artifacts import MAX_BODY_CHARS

    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    # Plain text passes through conversion ~1:1; wrap-in-<p> keeps it well over
    # the ceiling while staying far under the 25 MB byte cap.
    big_text = ("word " * ((MAX_BODY_CHARS // 5) + 5000)).encode("utf-8")
    r = _upload(ctx, project["id"], filename="huge.txt", content=big_text)
    assert r.status_code == 413
    assert _custom_artifact_rows(ctx.company_id) == []


# ── Read wire ───────────────────────────────────────────────────────────────


def test_artifact_content_for_returns_document_body(docs_env, monkeypatch):
    from app.project_group_context import _ARTIFACT_CONTENT_CHARS, _artifact_content_for

    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    r = _upload(
        ctx, project["id"], filename="brief.md",
        content=b"# Brief\n\nThe answer is 42.\n",
    )
    artifact_id = r.json()["id"]

    content = _artifact_content_for("custom_artifact", artifact_id, ctx.company_id)
    assert content is not None
    assert "42" in content
    assert len(content) <= _ARTIFACT_CONTENT_CHARS  # caller clamps; body fits here


def test_artifact_content_for_clamps_long_document(docs_env, monkeypatch):
    from app.project_group_context import (
        _ARTIFACT_CONTENT_CHARS,
        _handle_get_artifact_content,
    )

    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    r = _upload(
        ctx, project["id"], filename="long.txt",
        content=("sentence. " * 2000).encode("utf-8"),  # ~20k chars > 8000 clamp
    )
    artifact_id = r.json()["id"]

    out = _handle_get_artifact_content(
        project["id"], "acme", ctx.company_id,
        {"artifact_type": "custom_artifact", "artifact_id": artifact_id},
    )
    assert out != "I couldn't read that artifact's content."
    # Clamped: the body was ~20k chars; the caller trims to the cap and appends
    # a short truncation marker (so length is cap + the marker, never the full
    # body).
    assert "…(truncated)" in out
    assert len(out) <= _ARTIFACT_CONTENT_CHARS + 32


# ── Label ───────────────────────────────────────────────────────────────────


def test_documents_label():
    from app.project_group_context import _TYPE_LABELS

    assert _TYPE_LABELS["custom_artifact"] == "Documents"


# ── Tool-schema ↔ handler sync (guards against half-wiring a readable type) ──


def _handler_supported_types() -> set[str]:
    """The artifact types `_artifact_content_for` actually reads, derived from
    its source (`atype == "..."` branches). Deriving this rather than
    hardcoding is the whole point: a future branch added to the handler without
    a matching enum entry (or vice versa) is exactly the half-wiring that
    shipped documents un-readable, and this set makes that state a red test."""
    import inspect
    import re

    from app import project_group_context as pgc

    src = inspect.getsource(pgc._artifact_content_for)
    return set(re.findall(r'atype == "([a-z_]+)"', src))


def test_get_artifact_content_enum_matches_handler():
    from app.project_group_context import GET_ARTIFACT_CONTENT_TOOL

    enum = set(
        GET_ARTIFACT_CONTENT_TOOL["input_schema"]["properties"]["artifact_type"]["enum"]
    )
    handled = _handler_supported_types()

    # Every readable type the model may name is one the handler can serve, and
    # every type the handler serves is offered to the model — no half-wiring in
    # either direction.
    assert enum == handled, (
        f"tool enum {sorted(enum)} out of sync with handler {sorted(handled)}"
    )
    # The regression that prompted this guard: documents must be reachable.
    assert "custom_artifact" in enum


def test_tool_descriptions_mention_documents():
    from app.project_group_context import (
        GET_ARTIFACT_CONTENT_TOOL,
        LIST_PROJECT_ARTIFACTS_TOOL,
    )

    # The listing tool advertises documents as a discoverable type...
    assert "document" in LIST_PROJECT_ARTIFACTS_TOOL["description"].lower()
    # ...and the read tool advertises them as a readable type.
    assert "document" in GET_ARTIFACT_CONTENT_TOOL["description"].lower()


def test_tool_descriptions_map_documents_to_custom_artifact():
    """#3 disambiguation hint — both descriptions tell the model an uploaded
    document is artifact_type "custom_artifact" (shown as "Documents"), so it
    can pick the right type when several artifacts exist."""
    from app.project_group_context import (
        GET_ARTIFACT_CONTENT_TOOL,
        LIST_PROJECT_ARTIFACTS_TOOL,
    )

    for tool in (GET_ARTIFACT_CONTENT_TOOL, LIST_PROJECT_ARTIFACTS_TOOL):
        assert "custom_artifact" in tool["description"]
        # The exact word `_TYPE_LABELS` renders.
        assert "Documents" in tool["description"]


# ── Routing: document-aware admission + connector-skip (slice 2) ─────────────


def _project_scope(*, has_docs: bool, with_tools: bool = False, has_context: bool | None = None):
    from app.surface_scope import Surface, SurfaceScope

    # A project with an uploaded document also HAS readable context (a
    # `custom_artifact` is one of the readable-context types), so `has_context`
    # defaults to `has_docs` — the substantive-question admission disjunct now
    # keys on `has_project_context`. Callers exercising a context-only project
    # (a PRD/memory but no uploaded doc) pass `has_context=True, has_docs=False`.
    if has_context is None:
        has_context = has_docs
    return SurfaceScope(
        surface=Surface.project_private,
        project_id=1,
        has_project_documents=has_docs,
        has_project_context=has_context,
        extra_tools=({"name": "get_project_memory"},) if with_tools else (),
    )


# #1a — document nouns admit a content request through the noun-anchored gate.
@pytest.mark.parametrize(
    "q",
    [
        "read the document",
        "what does the doc say",
        "what does the spec say about auth",
        "summarize the uploaded document",
    ],
)
def test_document_nouns_admit_content_request(q):
    from app.skill_router import is_project_content_request

    assert is_project_content_request(q) is True


# #1b — the substantive-question predicate (noun-anchor dropped, intent kept).
@pytest.mark.parametrize(
    "q",
    [
        "what were the Q3 revenue numbers?",
        "who is the target persona for this",
        "how does the onboarding flow work",
    ],
)
def test_substantive_questions_admitted(q):
    from app.skill_router import is_substantive_project_question

    assert is_substantive_project_question(q) is True


@pytest.mark.parametrize(
    "q",
    [
        "hey thanks!",          # greeting/pleasantry — no intent lead
        "delegate the deck to David",  # a command — owned by the delegate gate
        "what?",                # bare backchannel — below the substance floor
        "",                     # empty
    ],
)
def test_non_substantive_declined(q):
    from app.skill_router import is_substantive_project_question

    assert is_substantive_project_question(q) is False


# #2 — a document-phrased ask stays in the loop ONLY when the project has docs.
def test_skip_connectors_keeps_document_question_in_loop_for_doc_project():
    import app.qa_agent as qa

    # "what does the document say" → document_lookup_candidates returns a
    # named-source candidate; in a DOC project that no longer bails to the
    # workspace connector search — it stays in the project tool loop.
    assert qa._skip_project_connectors(
        _project_scope(has_docs=True), "what does the document say", None
    ) is True


def test_skip_connectors_bails_document_question_when_no_docs():
    import app.qa_agent as qa

    # Same phrasing, project with NO uploaded docs → byte-identical old
    # behavior: a named document defers to the connector interceptors.
    assert qa._skip_project_connectors(
        _project_scope(has_docs=False), "what does the document say", None
    ) is False


def test_named_connector_still_wins_inside_doc_project():
    import app.qa_agent as qa

    # An explicitly named Confluence ask routes to the connector even in a doc
    # project (named-connector-wins is preserved — only bare document phrasing
    # is redirected).
    assert qa._skip_project_connectors(
        _project_scope(has_docs=True), "what does confluence say about pricing", None
    ) is False


# #1b end-to-end — a bare factual question reaches the project tool loop in a
# doc-project, and does NOT in a no-doc project.
def test_bare_factual_question_reaches_loop_in_doc_project(monkeypatch):
    import app.qa_agent as qa

    monkeypatch.setattr(
        qa, "_try_scoped_tool_answer",
        lambda **kw: {"answer": "from-the-document", "_skill_source": "project-tools"},
    )
    out = qa.answer(
        enterprise_id="ent",
        question="who is the target persona for this",
        dataset="acme",
        scope=_project_scope(has_docs=True, with_tools=True),
        history=None,
    )
    assert out.get("_skill_source") == "project-tools", out


def test_bare_factual_question_does_not_reach_loop_without_docs(monkeypatch):
    import app.qa_agent as qa
    from types import SimpleNamespace

    def _must_not_run(**kw):
        raise AssertionError("no-doc project must not enter the project tool loop")

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _must_not_run)
    monkeypatch.setattr(
        qa, "llm_call",
        lambda **k: SimpleNamespace(output={"skill_id": None, "confidence": 0.0, "action": None}),
    )

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        return {"answer": "composed", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)

    out = qa.answer(
        enterprise_id="ent",
        question="who is the target persona for this",
        dataset="acme",
        scope=_project_scope(has_docs=False, with_tools=True),
        history=None,
    )
    # Fell through to the composer (the loop's `_try_scoped_tool_answer` above
    # would have raised had the gate admitted).
    assert out.get("answer") == "composed", out


# ── Fix B2: substantive/context admission generalizes to has_project_context ──


def test_context_question_reaches_loop_in_prd_only_project(monkeypatch):
    """A SUBSTANTIVE context ask that names NO project noun (so it can ONLY be
    admitted via the `has_project_context` disjunct, not `is_project_content_
    request`) reaches the read-tool loop in a project with a PRD (or thin
    memory) but NO uploaded document — the generalized gate, not the doc-only
    `has_project_documents` one.

    Mutation proof: reverting the disjunct to `scope.has_project_documents`
    makes THIS test go red (a PRD-only project has has_project_documents False,
    so a no-noun substantive ask is no longer admitted)."""
    import app.qa_agent as qa

    monkeypatch.setattr(
        qa, "_try_scoped_tool_answer",
        lambda **kw: {"answer": "from-the-prd", "_skill_source": "project-tools"},
    )
    out = qa.answer(
        enterprise_id="ent",
        # No project noun → declined by is_project_content_request; substantive →
        # admission rides solely on has_project_context.
        question="catch me up on where everything stands here",
        dataset="acme",
        # PRD-only project: no uploaded doc, but it HAS readable context.
        scope=_project_scope(has_docs=False, has_context=True, with_tools=True),
        history=None,
    )
    assert out.get("_skill_source") == "project-tools", out


def test_explain_this_project_reaches_loop_via_content_gate(monkeypatch):
    """Failure B, literal phrasing: 'explain this project' carries the `this
    project` noun, so Fix B1 makes `is_project_content_request` admit it into
    the read-tool loop (where the model reads the PRD/memory) instead of
    answering from workspace breadth — admitted via the content gate, so it
    does not even depend on the has_project_context disjunct."""
    import app.qa_agent as qa

    monkeypatch.setattr(
        qa, "_try_scoped_tool_answer",
        lambda **kw: {"answer": "from-the-prd", "_skill_source": "project-tools"},
    )
    out = qa.answer(
        enterprise_id="ent",
        question="explain this project",
        dataset="acme",
        scope=_project_scope(has_docs=False, has_context=True, with_tools=True),
        history=None,
    )
    assert out.get("_skill_source") == "project-tools", out


def test_context_question_does_not_reach_loop_in_empty_project(monkeypatch):
    """The empty-project guard: no memory, no readable artifact →
    `has_project_context` False → the substantive/context disjunct admits
    NOTHING extra, and a context ask that names no project noun falls through
    to the composer byte-identically."""
    import app.qa_agent as qa
    from types import SimpleNamespace

    def _must_not_run(**kw):
        raise AssertionError("empty project must not enter the project tool loop")

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _must_not_run)
    monkeypatch.setattr(
        qa, "llm_call",
        lambda **k: SimpleNamespace(output={"skill_id": None, "confidence": 0.0, "action": None}),
    )

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        return {"answer": "composed", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)

    out = qa.answer(
        enterprise_id="ent",
        # Substantive, but NAMES no project noun — only the has_project_context
        # disjunct could admit it, and on an empty project it must not.
        question="catch me up on where everything stands here",
        dataset="acme",
        scope=_project_scope(has_docs=False, has_context=False, with_tools=True),
        history=None,
    )
    assert out.get("answer") == "composed", out


def test_has_project_context_field_defaults_false():
    """Main/workspace scopes and pre-existing callers carry False — the
    generalized admission disjunct is inert unless a project surface sets it."""
    from app.surface_scope import Surface, SurfaceScope

    assert SurfaceScope(surface=Surface.main).has_project_context is False
    assert (
        SurfaceScope(surface=Surface.project_private, project_id=1).has_project_context
        is False
    )


def test_has_project_documents_field_defaults_false():
    """Main/workspace scopes and pre-existing callers carry False — the routing
    additions are inert unless a project surface sets it."""
    from app.surface_scope import Surface, SurfaceScope

    assert SurfaceScope(surface=Surface.main).has_project_documents is False
    assert (
        SurfaceScope(surface=Surface.project_private, project_id=1).has_project_documents
        is False
    )


def test_has_project_documents_helper_reflects_uploads(docs_env, monkeypatch):
    """`has_project_documents` derives the flag from the SAME fan-out the
    manifest reads — True once a document is attached, False for an empty
    project."""
    from app.project_group_context import has_project_documents

    ctx = company_client(monkeypatch)
    empty = _create_project(ctx, name="No docs")
    with_doc = _create_project(ctx, name="Has docs")

    dataset = "acme"
    assert has_project_documents(empty["id"], dataset, ctx.company_id) is False
    assert has_project_documents(with_doc["id"], dataset, ctx.company_id) is False

    _upload(ctx, with_doc["id"], filename="brief.md", content=b"# Brief\n\nhello")
    assert has_project_documents(with_doc["id"], dataset, ctx.company_id) is True
    # The other project is unaffected.
    assert has_project_documents(empty["id"], dataset, ctx.company_id) is False

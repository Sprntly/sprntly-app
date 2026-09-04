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

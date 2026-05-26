"""Tests for app.design — prototype lifecycle + state machine + KG events.

Coverage:
  - FSM transitions: GENERATING → ITERATING → COMPLETE → EXPORTED
  - Invalid scenarios rejected (figma without figma_file_key, etc.)
  - PRD Artifact existence enforced (cross-tenant prevented)
  - KG: Artifact + EXPRESSED_AS + VISUALIZES edges written on create
  - Comment classification stub returns one of three classes
  - Codebase generator raises NotImplementedError
  - Route contract: 401 / 200 / 404

Tests use the SqliteBackend with a per-test tmp_path so the KG is
isolated. The route tests reuse the conftest fixtures (app_client +
isolated_settings) and wire the same SqliteBackend in via
`set_graph_facade_for_tests`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.design import (
    InvalidScenarioError,
    PRDArtifactNotFoundError,
    PrototypeComment,
    PrototypeInputs,
    PrototypeStatus,
    add_comment,
    complete_prototype,
    create_prototype,
    export_prototype,
    get_prototype,
    iterate_prototype,
)
from app.design.comment_classifier import classify_comment
from app.design.generators import (
    generate_from_codebase,
    generate_from_figma,
    generate_from_website,
)
from app.design.lifecycle import InvalidStateTransitionError
from app.graph import (
    Artifact,
    ArtifactType,
    EdgeType,
    GraphFacade,
    Workspace,
    WorkspaceStage,
)
from app.graph.backends.sqlite_backend import SqliteBackend


# ─────────────────────── fixtures ───────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def graph(tmp_path) -> GraphFacade:
    backend = SqliteBackend(db_path=str(tmp_path / "graph.db"))
    backend.initialize_schema()
    return GraphFacade(backend)


@pytest.fixture
def seeded_workspace(graph) -> str:
    now = _now()
    ws = Workspace(
        workspace_id="ws-1",
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        company_name="Acme",
        industry="SaaS",
        stage=WorkspaceStage.GROWTH,
        business_model="B2B SaaS",
        created_at=now - timedelta(days=1),
        updated_at=now,
    )
    graph.write_workspace("ws-1", ws)
    return "ws-1"


@pytest.fixture
def seeded_prd_artifact(graph, seeded_workspace) -> Artifact:
    """Write a PRD Artifact the prototype can link to."""
    now = _now()
    art = Artifact(
        workspace_id=seeded_workspace,
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        artifact_id="art-prd-1",
        artifact_type=ArtifactType.PRD,
        agent_output_snapshot={
            "title": "Onboarding nudge",
            "body": "PRD body content",
        },
        source_decision_id="dec-1",
    )
    graph.write_artifact(seeded_workspace, art)
    return art


def _figma_inputs(workspace_id: str = "ws-1") -> PrototypeInputs:
    return PrototypeInputs(
        workspace_id=workspace_id,
        prd_artifact_id="art-prd-1",
        decision_id="dec-1",
        scenario="figma",
        figma_file_key="ABC123",
        instructions="Make it pop",
    )


def _website_inputs(workspace_id: str = "ws-1") -> PrototypeInputs:
    return PrototypeInputs(
        workspace_id=workspace_id,
        prd_artifact_id="art-prd-1",
        decision_id="dec-1",
        scenario="website",
        website_url="https://example.com",
    )


# ─────────────────────── isolated DB fixture (no FastAPI) ───────────────────────


@pytest.fixture
def lifecycle_db(isolated_settings):
    """The fake Supabase already wired by isolated_settings. Returned for clarity."""
    return isolated_settings["db"]


# ─────────────────────── create_prototype ───────────────────────


def test_create_prototype_writes_kg_artifact_and_edges(
    graph, seeded_prd_artifact, lifecycle_db, monkeypatch
):
    """Spec §7 — prototype_created emits:
      - Artifact node (type=PROTOTYPE)
      - EXPRESSED_AS edge (Decision → Artifact)
      - VISUALIZES edge (Artifact prototype → Artifact PRD)
    """
    inputs = _website_inputs()  # website scenario avoids the live Figma call
    # Stub the website fetcher so we don't hit the network.
    from app.design.generators import website_generator
    monkeypatch.setattr(
        website_generator,
        "_default_fetcher",
        lambda url: "<html><head><title>Example</title></head><body></body></html>",
    )
    proto = create_prototype("ws-1", inputs, graph)

    assert proto.status == PrototypeStatus.GENERATING
    assert proto.artifact_id.startswith("art-proto-")
    assert proto.workspace_id == "ws-1"
    # Artifact written to KG
    art = graph.get_artifact("ws-1", proto.artifact_id)
    assert art is not None
    assert art.artifact_type == ArtifactType.PROTOTYPE
    assert art.source_decision_id == "dec-1"
    assert art.visualizes_artifact_id == "art-prd-1"

    # EXPRESSED_AS edge: Decision → Artifact (prototype)
    expressed = graph.edges_from("ws-1", "dec-1", EdgeType.EXPRESSED_AS)
    assert len(expressed) == 1
    assert expressed[0].target_entity_id == proto.artifact_id
    assert expressed[0].source == "prototype_created"

    # VISUALIZES edge: Artifact (prototype) → Artifact (PRD)
    visualizes = graph.edges_from("ws-1", proto.artifact_id, EdgeType.VISUALIZES)
    assert len(visualizes) == 1
    assert visualizes[0].target_entity_id == "art-prd-1"
    assert visualizes[0].source == "prototype_created"


def test_create_prototype_persists_row(graph, seeded_prd_artifact, lifecycle_db, monkeypatch):
    from app.design.generators import website_generator
    monkeypatch.setattr(
        website_generator,
        "_default_fetcher",
        lambda url: "<html></html>",
    )
    proto = create_prototype("ws-1", _website_inputs(), graph)
    fetched = get_prototype(proto.id)
    assert fetched is not None
    assert fetched.id == proto.id
    assert fetched.artifact_id == proto.artifact_id
    assert fetched.status == PrototypeStatus.GENERATING


def test_create_prototype_rejects_figma_without_file_key(graph, seeded_prd_artifact, lifecycle_db):
    inputs = PrototypeInputs(
        workspace_id="ws-1",
        prd_artifact_id="art-prd-1",
        decision_id="dec-1",
        scenario="figma",
        # No figma_file_key
    )
    with pytest.raises(InvalidScenarioError, match="figma_file_key"):
        create_prototype("ws-1", inputs, graph)


def test_create_prototype_rejects_website_without_url(graph, seeded_prd_artifact, lifecycle_db):
    inputs = PrototypeInputs(
        workspace_id="ws-1",
        prd_artifact_id="art-prd-1",
        decision_id="dec-1",
        scenario="website",
        # No website_url
    )
    with pytest.raises(InvalidScenarioError, match="website_url"):
        create_prototype("ws-1", inputs, graph)


def test_create_prototype_rejects_workspace_mismatch(graph, seeded_prd_artifact, lifecycle_db):
    """Route says workspace X, body says workspace Y → reject before DB."""
    inputs = _website_inputs(workspace_id="ws-1")
    with pytest.raises(InvalidScenarioError, match="workspace_id mismatch"):
        create_prototype("ws-OTHER", inputs, graph)


def test_create_prototype_404_if_prd_artifact_missing(graph, seeded_workspace, lifecycle_db):
    """No PRD Artifact in the workspace → PRDArtifactNotFoundError."""
    with pytest.raises(PRDArtifactNotFoundError, match="not found"):
        create_prototype("ws-1", _website_inputs(), graph)


def test_create_prototype_cross_tenant_prd_blocked(graph, lifecycle_db):
    """PRD exists in workspace A; create_prototype called for workspace B.
    The facade get_artifact returns None for B → PRDArtifactNotFoundError.
    """
    now = _now()
    # Workspace A + PRD in A
    ws_a = Workspace(
        workspace_id="ws-A",
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        company_name="A",
        industry="SaaS",
        stage=WorkspaceStage.GROWTH,
        business_model="B2B",
        created_at=now - timedelta(days=1),
        updated_at=now,
    )
    graph.write_workspace("ws-A", ws_a)
    prd_a = Artifact(
        workspace_id="ws-A",
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        artifact_id="art-prd-A",
        artifact_type=ArtifactType.PRD,
        agent_output_snapshot={"title": "A PRD"},
    )
    graph.write_artifact("ws-A", prd_a)
    # Workspace B exists too
    ws_b = ws_a.model_copy(update={"workspace_id": "ws-B"})
    graph.write_workspace("ws-B", ws_b)

    inputs_b = PrototypeInputs(
        workspace_id="ws-B",
        prd_artifact_id="art-prd-A",  # CROSS-TENANT — should fail
        decision_id="dec-1",
        scenario="website",
        website_url="https://example.com",
    )
    with pytest.raises(PRDArtifactNotFoundError):
        create_prototype("ws-B", inputs_b, graph)


def test_create_prototype_rejects_non_prd_artifact(graph, seeded_workspace, lifecycle_db):
    """Linking against a non-PRD Artifact (e.g. another prototype) fails."""
    now = _now()
    other = Artifact(
        workspace_id="ws-1",
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        artifact_id="art-other",
        artifact_type=ArtifactType.SPRINT_PLAN,
        agent_output_snapshot={},
    )
    graph.write_artifact("ws-1", other)
    inputs = PrototypeInputs(
        workspace_id="ws-1",
        prd_artifact_id="art-other",
        decision_id="dec-1",
        scenario="website",
        website_url="https://example.com",
    )
    with pytest.raises(PRDArtifactNotFoundError, match="expected PRD"):
        create_prototype("ws-1", inputs, graph)


# ─────────────────────── state machine transitions ───────────────────────


def test_full_lifecycle_generating_iterating_complete_exported(
    graph, seeded_prd_artifact, lifecycle_db, monkeypatch
):
    """End-to-end FSM walk:
        GENERATING (on create) →
        ITERATING (transient inside iterate_prototype) →
        COMPLETE (after iterate finishes) →
        COMPLETE (after explicit complete_prototype — no-op-ish) →
        EXPORTED (after export_prototype)
    """
    from app.design.generators import website_generator
    monkeypatch.setattr(
        website_generator,
        "_default_fetcher",
        lambda url: "<html></html>",
    )
    proto = create_prototype("ws-1", _website_inputs(), graph)
    assert proto.status == PrototypeStatus.GENERATING

    # Add a comment so iterate has something to consume.
    cmt = PrototypeComment(
        id="cmt-1",
        author_user_id="user-1",
        section_id="home",
        text="Please add a hero section — it's missing.",
        created_at=_now(),
    )
    add_comment(proto.id, cmt, graph)

    # Iterate (consumes the comment, ends COMPLETE).
    iterated = iterate_prototype(proto.id, graph)
    assert iterated.status == PrototypeStatus.COMPLETE
    # Comment should be resolved now
    assert all(c.resolved for c in iterated.comments)

    # Complete (idempotent-ish — already COMPLETE; sets completed_at).
    completed = complete_prototype(proto.id, graph)
    assert completed.status == PrototypeStatus.COMPLETE
    assert completed.completed_at is not None

    # Export.
    result = export_prototype(proto.id, "url")
    assert result["format"] == "url"
    fetched = get_prototype(proto.id)
    assert fetched.status == PrototypeStatus.EXPORTED
    assert fetched.exported_at is not None


def test_iterate_rejects_from_exported(graph, seeded_prd_artifact, lifecycle_db, monkeypatch):
    from app.design.generators import website_generator
    monkeypatch.setattr(website_generator, "_default_fetcher", lambda url: "<html></html>")
    proto = create_prototype("ws-1", _website_inputs(), graph)
    complete_prototype(proto.id, graph)
    export_prototype(proto.id, "url")
    with pytest.raises(InvalidStateTransitionError):
        iterate_prototype(proto.id, graph)


def test_complete_rejects_from_exported(graph, seeded_prd_artifact, lifecycle_db, monkeypatch):
    from app.design.generators import website_generator
    monkeypatch.setattr(website_generator, "_default_fetcher", lambda url: "<html></html>")
    proto = create_prototype("ws-1", _website_inputs(), graph)
    complete_prototype(proto.id, graph)
    export_prototype(proto.id, "url")
    with pytest.raises(InvalidStateTransitionError):
        complete_prototype(proto.id, graph)


def test_export_rejects_from_generating(graph, seeded_prd_artifact, lifecycle_db, monkeypatch):
    """Can't export before completion."""
    from app.design.generators import website_generator
    monkeypatch.setattr(website_generator, "_default_fetcher", lambda url: "<html></html>")
    proto = create_prototype("ws-1", _website_inputs(), graph)
    # status=GENERATING
    with pytest.raises(InvalidStateTransitionError):
        export_prototype(proto.id, "url")


def test_iterate_no_pending_comments_is_noop(graph, seeded_prd_artifact, lifecycle_db, monkeypatch):
    """Iterating with no unresolved comments still succeeds — it's the
    'force regen' path. Should end COMPLETE."""
    from app.design.generators import website_generator
    monkeypatch.setattr(website_generator, "_default_fetcher", lambda url: "<html></html>")
    proto = create_prototype("ws-1", _website_inputs(), graph)
    out = iterate_prototype(proto.id, graph)
    assert out.status == PrototypeStatus.COMPLETE


# ─────────────────────── export formats ───────────────────────


def test_export_zip_returns_placeholder(graph, seeded_prd_artifact, lifecycle_db, monkeypatch):
    from app.design.generators import website_generator
    monkeypatch.setattr(website_generator, "_default_fetcher", lambda url: "<html></html>")
    proto = create_prototype("ws-1", _website_inputs(), graph)
    complete_prototype(proto.id, graph)
    result = export_prototype(proto.id, "zip")
    assert result["format"] == "zip"
    assert result["placeholder"] is True


def test_export_claude_code_handoff_includes_payload(graph, seeded_prd_artifact, lifecycle_db, monkeypatch):
    from app.design.generators import website_generator
    monkeypatch.setattr(website_generator, "_default_fetcher", lambda url: "<html></html>")
    proto = create_prototype("ws-1", _website_inputs(), graph)
    complete_prototype(proto.id, graph)
    result = export_prototype(proto.id, "claude_code_handoff")
    assert result["format"] == "claude_code_handoff"
    assert result["handoff"]["prototype_id"] == proto.id
    assert result["handoff"]["artifact_id"] == proto.artifact_id
    assert "output_payload" in result["handoff"]


# ─────────────────────── comment classifier stub ───────────────────────


def test_classify_comment_style_keywords():
    from app.design.models import Prototype, PrototypeStatus
    # Minimal proto stub — classify_comment doesn't read it today.
    fake = Prototype.model_construct(
        id="p", workspace_id="w", artifact_id="a",
        status=PrototypeStatus.GENERATING, inputs=None,
        output_payload={}, comments=[],
        created_at=_now(), updated_at=_now(),
    )
    assert classify_comment(fake, "make the color softer") == "style"
    assert classify_comment(fake, "the font size is too big") == "style"


def test_classify_comment_context_gap_keywords():
    from app.design.models import Prototype, PrototypeStatus
    fake = Prototype.model_construct(
        id="p", workspace_id="w", artifact_id="a",
        status=PrototypeStatus.GENERATING, inputs=None,
        output_payload={}, comments=[],
        created_at=_now(), updated_at=_now(),
    )
    assert classify_comment(fake, "we're missing a settings page") == "context_gap"
    assert classify_comment(fake, "need an empty state here") == "context_gap"


def test_classify_comment_preference_keywords():
    from app.design.models import Prototype, PrototypeStatus
    fake = Prototype.model_construct(
        id="p", workspace_id="w", artifact_id="a",
        status=PrototypeStatus.GENERATING, inputs=None,
        output_payload={}, comments=[],
        created_at=_now(), updated_at=_now(),
    )
    assert classify_comment(fake, "I'd rather see a sidebar") == "preference"
    assert classify_comment(fake, "I prefer a darker theme") == "preference"


def test_classify_comment_defaults_to_context_gap():
    """No keyword hit → safe default is context_gap (needs a regen pass)."""
    from app.design.models import Prototype, PrototypeStatus
    fake = Prototype.model_construct(
        id="p", workspace_id="w", artifact_id="a",
        status=PrototypeStatus.GENERATING, inputs=None,
        output_payload={}, comments=[],
        created_at=_now(), updated_at=_now(),
    )
    assert classify_comment(fake, "hmm") == "context_gap"


def test_classify_comment_always_one_of_three_classes():
    """Property-style: regardless of input, the result is one of the
    three classes. Belt-and-braces for the type contract."""
    from app.design.models import Prototype, PrototypeStatus
    fake = Prototype.model_construct(
        id="p", workspace_id="w", artifact_id="a",
        status=PrototypeStatus.GENERATING, inputs=None,
        output_payload={}, comments=[],
        created_at=_now(), updated_at=_now(),
    )
    for text in ["", "color", "missing", "prefer", "asdfqwerty", "a" * 500]:
        assert classify_comment(fake, text) in ("context_gap", "preference", "style")


# ─────────────────────── add_comment ───────────────────────


def test_add_comment_persists_and_classifies(graph, seeded_prd_artifact, lifecycle_db, monkeypatch):
    from app.design.generators import website_generator
    monkeypatch.setattr(website_generator, "_default_fetcher", lambda url: "<html></html>")
    proto = create_prototype("ws-1", _website_inputs(), graph)
    cmt = PrototypeComment(
        id="cmt-x",
        author_user_id="user-1",
        section_id="home",
        text="the color is too bright",
        created_at=_now(),
    )
    updated = add_comment(proto.id, cmt, graph)
    assert len(updated.comments) == 1
    assert updated.comments[0].classification == "style"
    assert updated.comments[0].id == "cmt-x"


def test_add_comment_404_if_prototype_missing(graph, lifecycle_db):
    from app.design import PrototypeNotFoundError
    cmt = PrototypeComment(
        id="cmt-x",
        author_user_id="user-1",
        section_id="home",
        text="x",
        created_at=_now(),
    )
    with pytest.raises(PrototypeNotFoundError):
        add_comment("nope", cmt, graph)


# ─────────────────────── codebase generator stub ───────────────────────


def test_codebase_generator_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="Post-V1"):
        generate_from_codebase("file-key", "org/repo@main", {})


def test_create_prototype_scenario_c_raises_not_implemented(graph, seeded_prd_artifact, lifecycle_db):
    """Routing scenario=figma_codebase through create_prototype propagates
    the NotImplementedError. The route layer turns this into a 501."""
    inputs = PrototypeInputs(
        workspace_id="ws-1",
        prd_artifact_id="art-prd-1",
        decision_id="dec-1",
        scenario="figma_codebase",
        figma_file_key="ABC123",
    )
    with pytest.raises(NotImplementedError):
        create_prototype("ws-1", inputs, graph)


# ─────────────────────── figma generator (POC mode) ───────────────────────


def test_figma_generator_no_token_returns_placeholder():
    """When no access_token_provider is supplied (POC test mode), the
    figma generator returns a labelled placeholder skeleton — never
    crashes the lifecycle."""
    result = generate_from_figma("FILEKEY", {"title": "T"})
    assert result["meta"]["scenario"] == "figma"
    assert result["meta"]["placeholder"] is True
    assert len(result["pages"]) == 1


def test_figma_generator_with_fake_fetch_extracts_pages():
    """Live-path: fake fetch returns a Figma payload; generator extracts
    top-level pages + their frames."""
    fake_payload = {
        "name": "My File",
        "document": {
            "children": [
                {
                    "id": "0:1",
                    "name": "Page 1",
                    "type": "CANVAS",
                    "children": [
                        {"id": "1:1", "name": "Home", "type": "FRAME"},
                        {"id": "1:2", "name": "Settings", "type": "FRAME"},
                        # Non-frame node — should be skipped.
                        {"id": "1:3", "name": "Vec", "type": "VECTOR"},
                    ],
                },
                # Non-CANVAS top-level — should be skipped.
                {"id": "0:2", "name": "Junk", "type": "FRAME"},
            ],
        },
    }
    result = generate_from_figma(
        "FILEKEY",
        {"title": "T"},
        access_token_provider=lambda: "fake-token",
        fetch_file=lambda tok, key: fake_payload,
    )
    assert len(result["pages"]) == 1
    page = result["pages"][0]
    assert page["name"] == "Page 1"
    assert len(page["frames"]) == 2
    assert {f["name"] for f in page["frames"]} == {"Home", "Settings"}


def test_figma_generator_handles_fetch_failure_gracefully():
    """If fetch_file raises, we mark the skeleton as degraded but never
    crash. The lifecycle must keep moving."""
    def boom(tok, key):
        raise RuntimeError("api down")

    result = generate_from_figma(
        "FILEKEY",
        {"title": "T"},
        access_token_provider=lambda: "fake-token",
        fetch_file=boom,
    )
    assert result["meta"]["degraded"] is True
    assert result["meta"]["error"] == "figma_fetch_failed"


# ─────────────────────── website generator ───────────────────────


def test_website_generator_extracts_colors_and_fonts():
    html = """
    <html>
      <head>
        <title>Sample Co</title>
        <style>
          body { color: #112233; font-family: "Inter", sans-serif; }
          .btn { background-color: rgb(10, 20, 30); }
          .heading { font-family: 'Roboto', serif; }
        </style>
      </head>
      <body><h1 style="color: #ffffff">x</h1></body>
    </html>
    """
    result = generate_from_website(
        "https://example.com",
        {"title": "T"},
        fetcher=lambda url: html,
    )
    assert result["meta"]["scenario"] == "website"
    assert result["meta"]["site_title"] == "Sample Co"
    assert "#112233" in result["style"]["colors"]
    assert "#ffffff" in result["style"]["colors"]
    assert "Inter" in result["style"]["fonts"]
    assert "Roboto" in result["style"]["fonts"]


def test_website_generator_handles_fetch_failure_gracefully():
    def boom(url):
        raise RuntimeError("dns")

    result = generate_from_website(
        "https://example.com",
        {"title": "T"},
        fetcher=boom,
    )
    assert result["meta"]["degraded"] is True
    assert result["meta"]["error"] == "website_fetch_failed"


# ─────────────────────── route contract ───────────────────────


@pytest.fixture
def design_app_client(app_client, graph, seeded_prd_artifact, monkeypatch):
    """app_client + KG facade wired into the design routes module."""
    # Stub the website fetcher so any route-driven website creation is
    # network-free.
    from app.design.generators import website_generator
    monkeypatch.setattr(website_generator, "_default_fetcher", lambda url: "<html></html>")
    # Wire our test KG facade into the routes module.
    from app.routes import design as design_routes
    design_routes.set_graph_facade_for_tests(graph)
    yield app_client
    design_routes.set_graph_facade_for_tests(None)


def _website_body() -> dict:
    return {
        "workspace_id": "ws-1",
        "prd_artifact_id": "art-prd-1",
        "decision_id": "dec-1",
        "scenario": "website",
        "website_url": "https://example.com",
    }


def test_route_create_prototype_401_without_auth(unauth_client):
    resp = unauth_client.post("/v1/design/prototypes", json=_website_body())
    assert resp.status_code == 401


def test_route_create_prototype_201(design_app_client):
    resp = design_app_client.post("/v1/design/prototypes", json=_website_body())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "generating"
    assert body["workspace_id"] == "ws-1"
    assert body["artifact_id"].startswith("art-proto-")


def test_route_get_prototype_404_for_unknown_id(design_app_client):
    resp = design_app_client.get("/v1/design/prototypes/nope")
    assert resp.status_code == 404


def test_route_get_prototype_200(design_app_client):
    create_resp = design_app_client.post("/v1/design/prototypes", json=_website_body())
    pid = create_resp.json()["id"]
    resp = design_app_client.get(f"/v1/design/prototypes/{pid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == pid


def test_route_create_prototype_400_for_invalid_scenario(design_app_client):
    """Figma scenario without figma_file_key → 400 from lifecycle."""
    body = {
        "workspace_id": "ws-1",
        "prd_artifact_id": "art-prd-1",
        "decision_id": "dec-1",
        "scenario": "figma",
        # No figma_file_key
    }
    resp = design_app_client.post("/v1/design/prototypes", json=body)
    assert resp.status_code == 400


def test_route_create_prototype_404_for_missing_prd(design_app_client):
    body = {
        "workspace_id": "ws-1",
        "prd_artifact_id": "art-prd-DOES-NOT-EXIST",
        "decision_id": "dec-1",
        "scenario": "website",
        "website_url": "https://example.com",
    }
    resp = design_app_client.post("/v1/design/prototypes", json=body)
    assert resp.status_code == 404


def test_route_create_prototype_501_for_scenario_c(design_app_client):
    body = {
        "workspace_id": "ws-1",
        "prd_artifact_id": "art-prd-1",
        "decision_id": "dec-1",
        "scenario": "figma_codebase",
        "figma_file_key": "ABC",
    }
    resp = design_app_client.post("/v1/design/prototypes", json=body)
    assert resp.status_code == 501


def test_route_add_comment_then_iterate_then_complete_then_export(design_app_client):
    """Full route walk of the FSM."""
    pid = design_app_client.post("/v1/design/prototypes", json=_website_body()).json()["id"]

    # POST comment
    resp = design_app_client.post(
        f"/v1/design/prototypes/{pid}/comments",
        json={
            "author_user_id": "user-1",
            "section_id": "home",
            "text": "color is too bright",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["comments"]) == 1
    assert body["comments"][0]["classification"] == "style"

    # POST iterate
    resp = design_app_client.post(f"/v1/design/prototypes/{pid}/iterate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "complete"

    # POST complete (idempotent-ish; updates completed_at)
    resp = design_app_client.post(f"/v1/design/prototypes/{pid}/complete")
    assert resp.status_code == 200
    assert resp.json()["status"] == "complete"
    assert resp.json()["completed_at"] is not None

    # POST export?format=url
    resp = design_app_client.post(f"/v1/design/prototypes/{pid}/export?format=url")
    assert resp.status_code == 200
    assert resp.json()["format"] == "url"

    # GET reflects EXPORTED status
    resp = design_app_client.get(f"/v1/design/prototypes/{pid}")
    assert resp.json()["status"] == "exported"


def test_route_iterate_404_for_unknown(design_app_client):
    resp = design_app_client.post("/v1/design/prototypes/nope/iterate")
    assert resp.status_code == 404


def test_route_complete_404_for_unknown(design_app_client):
    resp = design_app_client.post("/v1/design/prototypes/nope/complete")
    assert resp.status_code == 404


def test_route_export_409_when_not_complete(design_app_client):
    """Export from GENERATING is a 409."""
    pid = design_app_client.post("/v1/design/prototypes", json=_website_body()).json()["id"]
    resp = design_app_client.post(f"/v1/design/prototypes/{pid}/export?format=url")
    assert resp.status_code == 409


def test_route_post_comment_401_without_auth(unauth_client):
    resp = unauth_client.post(
        "/v1/design/prototypes/anything/comments",
        json={"author_user_id": "u", "section_id": "s", "text": "t"},
    )
    assert resp.status_code == 401

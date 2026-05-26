"""Tests for app.synthesis.on_demand + app.routes.synthesis.

Covers:
- Pydantic model validation (workspace_id required, user_message length-capped)
- `respond_to_pm` in clarify mode → ClarifyingQuestion returned
- `respond_to_pm` in artifact mode → ArtifactResponse returned
- "I want to build X" pattern → artifact_type forced to "prd"
- Spec invariant: max 1 clarifying question per turn (extras dropped)
- Invalid artifact_type from LLM → HTTPException 502
- Route 401 without auth
- Route 200 with proper SynthesisOnDemandResponse

LLM calls are mocked via the shared `fake_llm` fixture. The GraphFacade
is instantiated against a real in-memory SqliteBackend (file under
tmp_path) so the facade's tenant assertions are exercised end-to-end —
we want to know if those break too.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError


# ───────────────────── helpers ─────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_facade(tmp_path):
    from app.graph.backends.sqlite_backend import SqliteBackend
    from app.graph.facade import GraphFacade

    db_path = tmp_path / "kg.db"
    backend = SqliteBackend(db_path=str(db_path))
    backend.initialize_schema()
    return GraphFacade(backend)


def _seed_workspace(facade, workspace_id: str = "ws-1"):
    from app.graph import (
        KpiTreeNode,
        Workspace,
        WorkspaceStage,
        WorkspaceStrategy,
    )

    now = _now()
    ws = Workspace(
        workspace_id=workspace_id,
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        company_name="Acme",
        industry="SaaS",
        stage=WorkspaceStage.GROWTH,
        business_model="B2B SaaS",
        kpi_tree=[
            KpiTreeNode(name="ARR", role="north_star", target_value=10_000_000.0),
            KpiTreeNode(
                name="Activation", role="primary", parent="ARR", target_value=0.7
            ),
        ],
        strategy=WorkspaceStrategy(
            okrs=["Grow ARR to $10M"], dead_ends=["Marketplace pivot"]
        ),
        created_at=now - timedelta(days=1),
        updated_at=now,
    )
    facade.write_workspace(workspace_id, ws)
    return ws


# ───────────────────── pydantic model tests ─────────────────────


def test_pmchatturn_requires_workspace_id(isolated_settings):
    from app.synthesis.on_demand import PmChatTurn

    with pytest.raises(ValidationError):
        PmChatTurn(user_message="hi")  # type: ignore[call-arg]


def test_pmchatturn_caps_user_message_at_2000_chars(isolated_settings):
    from app.synthesis.on_demand import PmChatTurn

    # Exactly 2000 chars is OK.
    ok = PmChatTurn(workspace_id="ws-1", user_message="x" * 2000)
    assert len(ok.user_message) == 2000

    # 2001 chars trips the validator.
    with pytest.raises(ValidationError):
        PmChatTurn(workspace_id="ws-1", user_message="x" * 2001)


def test_pmchatturn_empty_message_rejected(isolated_settings):
    from app.synthesis.on_demand import PmChatTurn

    with pytest.raises(ValidationError):
        PmChatTurn(workspace_id="ws-1", user_message="")


def test_synthesis_response_envelope_requires_conversation_id(isolated_settings):
    from app.synthesis.on_demand import SynthesisOnDemandResponse

    with pytest.raises(ValidationError):
        SynthesisOnDemandResponse(mode="artifact", conversation_id="")  # type: ignore[arg-type]


# ───────────────────── respond_to_pm — clarify path ─────────────────────


def test_respond_to_pm_returns_clarification(isolated_settings, fake_llm, tmp_path):
    from app.synthesis.on_demand import (
        ClarifyingQuestion,
        PmChatTurn,
        respond_to_pm,
    )

    facade = _make_facade(tmp_path)
    _seed_workspace(facade)

    fake_llm["payload"] = {
        "mode": "clarify",
        "clarification": {
            "question": "Which segment should we prioritize for this PRD?",
            "kg_gaps": ["target_segment"],
        },
        "artifact": None,
        "conversation_id": "conv-from-llm",
    }
    turn = PmChatTurn(
        workspace_id="ws-1",
        user_message="Draft a PRD for a new pricing page",
    )
    resp = respond_to_pm(turn, facade)

    assert resp.mode == "clarify"
    assert resp.artifact is None
    assert isinstance(resp.clarification, ClarifyingQuestion)
    assert "segment" in resp.clarification.question.lower()
    assert resp.clarification.kg_gaps == ["target_segment"]
    assert resp.conversation_id == "conv-from-llm"


def test_respond_to_pm_generates_conversation_id_when_absent(
    isolated_settings, fake_llm, tmp_path
):
    """If neither the caller nor the LLM provided a conversation_id, we
    mint one server-side so the chat surface has something to thread on."""
    from app.synthesis.on_demand import PmChatTurn, respond_to_pm

    facade = _make_facade(tmp_path)
    _seed_workspace(facade)

    fake_llm["payload"] = {
        "mode": "clarify",
        "clarification": {"question": "Which KPI?", "kg_gaps": ["kpi"]},
        "artifact": None,
        # conversation_id omitted on purpose
    }
    turn = PmChatTurn(workspace_id="ws-1", user_message="help")
    resp = respond_to_pm(turn, facade)
    assert resp.conversation_id.startswith("conv-")


# ───────────────────── respond_to_pm — artifact path ─────────────────────


def test_respond_to_pm_returns_artifact(isolated_settings, fake_llm, tmp_path):
    from app.synthesis.on_demand import (
        ArtifactResponse,
        PmChatTurn,
        respond_to_pm,
    )

    facade = _make_facade(tmp_path)
    _seed_workspace(facade)

    fake_llm["payload"] = {
        "mode": "artifact",
        "clarification": None,
        "artifact": {
            "artifact_type": "leadership_comm",
            "title": "Weekly update for the leadership team",
            "content": "## Summary\n\nWe shipped X.",
            "assumptions": [],
            "confidence": "high",
        },
        "conversation_id": "conv-leadership",
    }
    turn = PmChatTurn(
        workspace_id="ws-1",
        user_message="Write the weekly leadership update",
    )
    resp = respond_to_pm(turn, facade)

    assert resp.mode == "artifact"
    assert resp.clarification is None
    assert isinstance(resp.artifact, ArtifactResponse)
    assert resp.artifact.artifact_type == "leadership_comm"
    assert resp.artifact.title == "Weekly update for the leadership team"
    assert resp.artifact.confidence == "high"


def test_respond_to_pm_artifact_with_assumptions(
    isolated_settings, fake_llm, tmp_path
):
    """Partial-context responses carry their assumptions back to the client."""
    from app.synthesis.on_demand import PmChatTurn, respond_to_pm

    facade = _make_facade(tmp_path)
    _seed_workspace(facade)

    fake_llm["payload"] = {
        "mode": "artifact",
        "artifact": {
            "artifact_type": "strategic_analysis",
            "title": "Analysis: monetization options",
            "content": "## Hypothesis\n\n> **Assumption:** seat-based pricing.\n",
            "assumptions": ["Pricing model is seat-based"],
            "confidence": "low",
        },
        "conversation_id": "conv-1",
    }
    turn = PmChatTurn(
        workspace_id="ws-1",
        user_message="Analyze monetization options for the new SKU",
    )
    resp = respond_to_pm(turn, facade)
    assert resp.artifact is not None
    assert resp.artifact.confidence == "low"
    assert resp.artifact.assumptions == ["Pricing model is seat-based"]


# ───────────────────── "I want to build X" pattern → PRD ─────────────────────


def test_i_want_to_build_x_pattern_forces_prd_artifact_type(
    isolated_settings, fake_llm, tmp_path
):
    """The LLM picked `documentation` but the user said 'I want to build X'.
    Spec rule: prefer 'prd'. We rewrite the artifact_type."""
    from app.synthesis.on_demand import PmChatTurn, respond_to_pm

    facade = _make_facade(tmp_path)
    _seed_workspace(facade)

    fake_llm["payload"] = {
        "mode": "artifact",
        "artifact": {
            "artifact_type": "documentation",  # LLM was wrong
            "title": "Onboarding flow",
            "content": "## Problem\n\nNew users churn at activation.\n",
            "assumptions": [],
            "confidence": "high",
        },
        "conversation_id": "conv-build-x",
    }
    turn = PmChatTurn(
        workspace_id="ws-1",
        user_message="I want to build a new onboarding flow for first-time users",
    )
    resp = respond_to_pm(turn, facade)
    assert resp.artifact is not None
    assert resp.artifact.artifact_type == "prd"


def test_i_would_like_to_build_pattern_also_forces_prd(
    isolated_settings, fake_llm, tmp_path
):
    from app.synthesis.on_demand import PmChatTurn, respond_to_pm

    facade = _make_facade(tmp_path)
    _seed_workspace(facade)

    fake_llm["payload"] = {
        "mode": "artifact",
        "artifact": {
            "artifact_type": "sprint_plan",
            "title": "Pricing page",
            "content": "body",
            "assumptions": [],
            "confidence": "medium",
        },
        "conversation_id": "c-2",
    }
    turn = PmChatTurn(
        workspace_id="ws-1",
        user_message="I'd like to build a new pricing page",
    )
    resp = respond_to_pm(turn, facade)
    assert resp.artifact.artifact_type == "prd"


def test_artifact_type_unchanged_when_no_build_pattern(
    isolated_settings, fake_llm, tmp_path
):
    """No 'I want to build X' phrasing → trust the LLM's artifact_type."""
    from app.synthesis.on_demand import PmChatTurn, respond_to_pm

    facade = _make_facade(tmp_path)
    _seed_workspace(facade)

    fake_llm["payload"] = {
        "mode": "artifact",
        "artifact": {
            "artifact_type": "sprint_plan",
            "title": "Q3 sprint plan",
            "content": "## Sprint goals\n",
            "assumptions": [],
            "confidence": "medium",
        },
        "conversation_id": "c-3",
    }
    turn = PmChatTurn(workspace_id="ws-1", user_message="Plan the Q3 sprint")
    resp = respond_to_pm(turn, facade)
    assert resp.artifact.artifact_type == "sprint_plan"


# ───────────────────── spec invariant: max 1 clarifying question ─────────────────────


def test_clarification_with_question_list_truncates_to_one(
    isolated_settings, fake_llm, tmp_path
):
    """If the LLM puts a list under `question`, keep only the first.
    Logs a warning; never fails the turn."""
    from app.synthesis.on_demand import PmChatTurn, respond_to_pm

    facade = _make_facade(tmp_path)
    _seed_workspace(facade)

    fake_llm["payload"] = {
        "mode": "clarify",
        "clarification": {
            "question": [
                "Which segment?",
                "What KPI?",
                "Which OKR?",
            ],
            "kg_gaps": ["segment", "kpi", "okr"],
        },
        "artifact": None,
        "conversation_id": "c-4",
    }
    turn = PmChatTurn(workspace_id="ws-1", user_message="Draft a PRD")
    resp = respond_to_pm(turn, facade)
    assert resp.mode == "clarify"
    assert resp.clarification.question == "Which segment?"


def test_clarification_with_multiple_question_marks_truncates_to_one(
    isolated_settings, fake_llm, tmp_path
):
    """LLM glued multiple questions into one string with '?'. Same rule —
    keep the first, drop the rest."""
    from app.synthesis.on_demand import PmChatTurn, respond_to_pm

    facade = _make_facade(tmp_path)
    _seed_workspace(facade)

    fake_llm["payload"] = {
        "mode": "clarify",
        "clarification": {
            "question": "Which segment? What KPI? Which OKR?",
            "kg_gaps": [],
        },
        "artifact": None,
        "conversation_id": "c-5",
    }
    turn = PmChatTurn(workspace_id="ws-1", user_message="Draft a PRD")
    resp = respond_to_pm(turn, facade)
    assert resp.clarification.question == "Which segment?"


# ───────────────────── LLM contract violations → 502 ─────────────────────


def test_invalid_artifact_type_rejected(isolated_settings, fake_llm, tmp_path):
    from fastapi import HTTPException

    from app.synthesis.on_demand import PmChatTurn, respond_to_pm

    facade = _make_facade(tmp_path)
    _seed_workspace(facade)

    fake_llm["payload"] = {
        "mode": "artifact",
        "artifact": {
            "artifact_type": "playbook",  # not in the whitelist
            "title": "x",
            "content": "y",
            "assumptions": [],
            "confidence": "high",
        },
        "conversation_id": "c-6",
    }
    turn = PmChatTurn(workspace_id="ws-1", user_message="generate a playbook")
    with pytest.raises(HTTPException) as exc:
        respond_to_pm(turn, facade)
    assert exc.value.status_code == 502


def test_invalid_mode_rejected(isolated_settings, fake_llm, tmp_path):
    from fastapi import HTTPException

    from app.synthesis.on_demand import PmChatTurn, respond_to_pm

    facade = _make_facade(tmp_path)
    _seed_workspace(facade)

    fake_llm["payload"] = {"mode": "wat", "conversation_id": "c-7"}
    turn = PmChatTurn(workspace_id="ws-1", user_message="anything")
    with pytest.raises(HTTPException) as exc:
        respond_to_pm(turn, facade)
    assert exc.value.status_code == 502


# ───────────────────── respond_to_pm with a Mock GraphFacade ─────────────────────


def test_respond_to_pm_calls_load_session_context_with_workspace_id(
    isolated_settings, fake_llm
):
    """Sanity check: the workspace_id from the turn is what gets queried."""
    from app.synthesis.on_demand import PmChatTurn, respond_to_pm

    mock_graph = MagicMock()
    mock_graph.load_session_context.return_value = {
        "workspace": None,
        "active_hypotheses": [],
        "recent_decisions": [],
        "recent_outcomes": [],
    }
    fake_llm["payload"] = {
        "mode": "artifact",
        "artifact": {
            "artifact_type": "documentation",
            "title": "t",
            "content": "c",
            "assumptions": [],
            "confidence": "low",
        },
        "conversation_id": "c-8",
    }
    turn = PmChatTurn(workspace_id="ws-mocked", user_message="hello")
    respond_to_pm(turn, mock_graph)
    mock_graph.load_session_context.assert_called_once_with("ws-mocked")


# ───────────────────── route tests ─────────────────────


def _override_graph(client, facade):
    """Attach a GraphFacade override to the FastAPI app the TestClient
    is wired to. Cleaned up by overwriting with the default again at
    the end of the test."""
    import app.main as main_mod
    from app.routes.synthesis import _get_graph

    main_mod.app.dependency_overrides[_get_graph] = lambda: facade


def _clear_overrides():
    import app.main as main_mod

    main_mod.app.dependency_overrides.clear()


def test_route_returns_401_without_auth(unauth_client, isolated_settings, tmp_path):
    facade = _make_facade(tmp_path)
    _seed_workspace(facade)
    _override_graph(unauth_client, facade)
    try:
        resp = unauth_client.post(
            "/v1/synthesis/on-demand",
            json={"workspace_id": "ws-1", "user_message": "Draft a PRD"},
        )
        assert resp.status_code == 401
    finally:
        _clear_overrides()


def test_route_returns_200_with_artifact(
    app_client, isolated_settings, fake_llm, tmp_path
):
    facade = _make_facade(tmp_path)
    _seed_workspace(facade)
    _override_graph(app_client, facade)
    try:
        fake_llm["payload"] = {
            "mode": "artifact",
            "artifact": {
                "artifact_type": "prd",
                "title": "PRD: onboarding flow",
                "content": "## Problem\n",
                "assumptions": [],
                "confidence": "high",
            },
            "conversation_id": "c-route-1",
        }
        resp = app_client.post(
            "/v1/synthesis/on-demand",
            json={
                "workspace_id": "ws-1",
                "user_message": "I want to build a new onboarding flow",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "artifact"
        assert body["artifact"]["artifact_type"] == "prd"
        assert body["clarification"] is None
        assert body["conversation_id"] == "c-route-1"
    finally:
        _clear_overrides()


def test_route_returns_200_with_clarification(
    app_client, isolated_settings, fake_llm, tmp_path
):
    facade = _make_facade(tmp_path)
    _seed_workspace(facade)
    _override_graph(app_client, facade)
    try:
        fake_llm["payload"] = {
            "mode": "clarify",
            "clarification": {
                "question": "Which user segment is this for?",
                "kg_gaps": ["target_segment"],
            },
            "artifact": None,
            "conversation_id": "c-route-2",
        }
        resp = app_client.post(
            "/v1/synthesis/on-demand",
            json={
                "workspace_id": "ws-1",
                "user_message": "Draft a PRD for the activation experiment",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "clarify"
        assert "segment" in body["clarification"]["question"].lower()
        assert body["artifact"] is None
    finally:
        _clear_overrides()


def test_route_rejects_oversized_user_message(
    app_client, isolated_settings, tmp_path
):
    facade = _make_facade(tmp_path)
    _seed_workspace(facade)
    _override_graph(app_client, facade)
    try:
        resp = app_client.post(
            "/v1/synthesis/on-demand",
            json={"workspace_id": "ws-1", "user_message": "x" * 2001},
        )
        assert resp.status_code == 422
    finally:
        _clear_overrides()

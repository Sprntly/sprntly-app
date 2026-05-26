"""Design Agent routes — prototype lifecycle endpoints.

Spec source: Design_Agent_Spec.docx §4 (lifecycle), §5 (comments), §9
(export).

  POST   /v1/design/prototypes                    -> create
  GET    /v1/design/prototypes/{id}               -> fetch
  POST   /v1/design/prototypes/{id}/comments      -> add comment
  POST   /v1/design/prototypes/{id}/iterate       -> apply comments + regen
  POST   /v1/design/prototypes/{id}/complete      -> mark complete
  POST   /v1/design/prototypes/{id}/export        -> export (?format=...)

Every route is auth-gated via `require_session`. The KG facade is
constructed per-request (cheap; the backend is a thin wrapper) so tests
can swap in a fresh SqliteBackend without messing with module
singletons.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.auth import require_session
from app.design import (
    InvalidScenarioError,
    PRDArtifactNotFoundError,
    PrototypeComment,
    PrototypeInputs,
    PrototypeNotFoundError,
    add_comment,
    complete_prototype,
    create_prototype,
    export_prototype,
    get_prototype,
    iterate_prototype,
)
from app.design.lifecycle import InvalidStateTransitionError
from app.graph import GraphFacade

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/design/prototypes", tags=["design"])


# Module-level facade slot. Tests assign a fixture-backed facade here so
# the routes use the SqliteBackend with an in-memory DB.
_graph_facade: GraphFacade | None = None


def _get_graph() -> GraphFacade:
    """Resolve the KG facade.

    In production (env GRAPH_BACKEND set) we lazily build the facade
    from env. In tests, conftest assigns `_graph_facade` directly so a
    SqliteBackend with tmp_path is used.
    """
    global _graph_facade
    if _graph_facade is None:
        _graph_facade = GraphFacade.from_env()
        _graph_facade.initialize()
    return _graph_facade


def set_graph_facade_for_tests(facade: GraphFacade | None) -> None:
    """Test seam — set the module's KG facade. Pass None to reset."""
    global _graph_facade
    _graph_facade = facade


# ─────────────────────── request/response shapes ───────────────────────


class CreatePrototypeIn(PrototypeInputs):
    """Body for POST /v1/design/prototypes. Same shape as PrototypeInputs."""
    pass


class AddCommentIn(BaseModel):
    """Body for POST /v1/design/prototypes/{id}/comments.

    The route mints `id` + `created_at` server-side so clients can't
    forge them. Everything else is client-controlled but validated.
    """

    model_config = ConfigDict(extra="forbid")

    author_user_id: str = Field(..., min_length=1)
    section_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1, max_length=2000)


# ─────────────────────── routes ───────────────────────


@router.post("", status_code=201)
def create(
    body: CreatePrototypeIn,
    _session: dict = Depends(require_session),
):
    """Create a prototype.

    Emits KG events: prototype_created + EXPRESSED_AS + VISUALIZES edges.
    Returns the new Prototype with status=GENERATING.
    """
    graph = _get_graph()
    try:
        proto = create_prototype(body.workspace_id, body, graph)
    except InvalidScenarioError as e:
        raise HTTPException(400, str(e)) from e
    except PRDArtifactNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except NotImplementedError as e:
        # Scenario C — Post-V1.
        raise HTTPException(501, str(e)) from e
    return proto.model_dump(mode="json")


@router.get("/{prototype_id}")
def fetch(
    prototype_id: str,
    _session: dict = Depends(require_session),
):
    proto = get_prototype(prototype_id)
    if proto is None:
        raise HTTPException(404, f"Prototype {prototype_id!r} not found")
    return proto.model_dump(mode="json")


@router.post("/{prototype_id}/comments", status_code=201)
def post_comment(
    prototype_id: str,
    body: AddCommentIn,
    _session: dict = Depends(require_session),
):
    graph = _get_graph()
    # Mint server-side id + timestamp.
    comment = PrototypeComment(
        id=f"cmt-{uuid.uuid4().hex[:12]}",
        author_user_id=body.author_user_id,
        section_id=body.section_id,
        text=body.text,
        classification=None,  # filled by the classifier inside add_comment
        resolved=False,
        created_at=datetime.now(timezone.utc),
    )
    try:
        proto = add_comment(prototype_id, comment, graph)
    except PrototypeNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    return proto.model_dump(mode="json")


@router.post("/{prototype_id}/iterate")
def iterate(
    prototype_id: str,
    _session: dict = Depends(require_session),
):
    graph = _get_graph()
    try:
        proto = iterate_prototype(prototype_id, graph)
    except PrototypeNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except InvalidStateTransitionError as e:
        raise HTTPException(409, str(e)) from e
    return proto.model_dump(mode="json")


@router.post("/{prototype_id}/complete")
def complete(
    prototype_id: str,
    _session: dict = Depends(require_session),
):
    graph = _get_graph()
    try:
        proto = complete_prototype(prototype_id, graph)
    except PrototypeNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except InvalidStateTransitionError as e:
        raise HTTPException(409, str(e)) from e
    return proto.model_dump(mode="json")


@router.post("/{prototype_id}/export")
def export(
    prototype_id: str,
    format: Literal["url", "zip", "claude_code_handoff"] = "url",
    _session: dict = Depends(require_session),
):
    try:
        return export_prototype(prototype_id, format)
    except PrototypeNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except InvalidStateTransitionError as e:
        raise HTTPException(409, str(e)) from e

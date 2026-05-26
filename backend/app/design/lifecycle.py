"""Design Agent prototype lifecycle — the state machine.

Spec source: Design_Agent_Spec.docx §4 (lifecycle), §5 (commenting),
§7 (KG write events), §9 (export formats).

State transitions (see PrototypeStatus docstring):
    GENERATING → COMPLETE     (sync stub today; async later)
    GENERATING → ITERATING    (comment arrives mid-generate)
    ITERATING  → COMPLETE     (iteration finishes)
    COMPLETE   → ITERATING    (more comments)
    COMPLETE   → EXPORTED     (export endpoint hit)

KG write events emitted (per spec §7):
    prototype_created          on create_prototype
    prototype_comment_applied  on iterate_prototype (per applied comment)
    prototype_completed        on complete_prototype

Edges written:
    EXPRESSED_AS  Decision → Artifact (prototype)         on create
    VISUALIZES    Artifact (prototype) → Artifact (PRD)   on create
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional

from app import db
from app.design.comment_classifier import classify_comment
from app.design.generators import (
    generate_from_codebase,
    generate_from_figma,
    generate_from_website,
)
from app.design.models import (
    Prototype,
    PrototypeComment,
    PrototypeInputs,
    PrototypeStatus,
)
from app.graph import (
    Artifact,
    ArtifactType,
    Edge,
    EdgeType,
    GraphFacade,
)

logger = logging.getLogger(__name__)


# ─────────────────────── module errors ───────────────────────


class DesignLifecycleError(Exception):
    """Base for everything raised from this module."""


class InvalidScenarioError(DesignLifecycleError):
    """The scenario↔input contract is broken (e.g. figma scenario but no file_key)."""


class PrototypeNotFoundError(DesignLifecycleError):
    """No prototype row with the given id."""


class PRDArtifactNotFoundError(DesignLifecycleError):
    """The parent PRD Artifact wasn't found in the requested workspace —
    blocks cross-tenant escapes. Raised before any DB write."""


class InvalidStateTransitionError(DesignLifecycleError):
    """Caller asked for a transition the FSM doesn't allow from current state."""


# ─────────────────────── lifecycle: create ───────────────────────


def create_prototype(
    workspace_id: str,
    inputs: PrototypeInputs,
    graph: GraphFacade,
    *,
    prd_content_loader: Optional[Callable[[str], dict[str, Any]]] = None,
    figma_access_token_provider: Optional[Callable[[], str]] = None,
) -> Prototype:
    """Create a prototype + write the KG Artifact + EXPRESSED_AS/VISUALIZES edges.

    Args:
        workspace_id: The tenant scope. MUST match inputs.workspace_id.
        inputs: Validated payload from the route layer.
        graph: KG facade — the only KG entrypoint.
        prd_content_loader: callable(prd_artifact_id) -> dict, used by
            generators for steering context. Defaults to reading the
            Artifact's `agent_output_snapshot`. Test seam.
        figma_access_token_provider: callable() -> str. None means the
            generator runs in POC placeholder mode (no live Figma call).

    Returns:
        The newly-created Prototype with status=GENERATING that has
        already had its first synchronous generation pass. Real async
        generation lands in a follow-up.

    Raises:
        InvalidScenarioError: scenario/inputs contract broken.
        PRDArtifactNotFoundError: parent PRD doesn't exist in workspace
            (also prevents cross-tenant escapes).
    """
    if workspace_id != inputs.workspace_id:
        raise InvalidScenarioError(
            f"workspace_id mismatch: route={workspace_id} inputs={inputs.workspace_id}"
        )

    _validate_scenario_contract(inputs)

    # PRD Artifact existence — also the tenant guard. The facade scopes
    # the read by workspace_id; a PRD owned by another tenant returns
    # None and we 404. Cross-tenant escape closed.
    prd_artifact = graph.get_artifact(workspace_id, inputs.prd_artifact_id)
    if prd_artifact is None:
        raise PRDArtifactNotFoundError(
            f"PRD Artifact {inputs.prd_artifact_id!r} not found in workspace "
            f"{workspace_id!r}"
        )
    if prd_artifact.artifact_type != ArtifactType.PRD:
        raise PRDArtifactNotFoundError(
            f"Artifact {inputs.prd_artifact_id!r} is type "
            f"{prd_artifact.artifact_type}, expected PRD"
        )

    # Load PRD content for the generator. Falls back to the Artifact
    # snapshot if no loader is supplied — the snapshot is canonical
    # anyway.
    prd_content: dict[str, Any]
    if prd_content_loader is not None:
        prd_content = prd_content_loader(inputs.prd_artifact_id) or {}
    else:
        prd_content = dict(prd_artifact.agent_output_snapshot or {})

    # Stub generation — synchronous, returns a JSON skeleton.
    try:
        output_payload = _generate_prototype_payload(
            inputs,
            prd_content,
            figma_access_token_provider=figma_access_token_provider,
        )
    except NotImplementedError:
        # Scenario C — propagate so the route returns 501.
        raise

    # IDs: prototype row id + KG Artifact node id. Distinct so the KG
    # Artifact can be referenced by other graph nodes (EXPRESSED_AS,
    # VISUALIZES) without leaking the DB primary key shape.
    prototype_id = f"proto-{uuid.uuid4().hex[:12]}"
    artifact_id = f"art-proto-{uuid.uuid4().hex[:12]}"

    now = _utcnow()
    valid_at = now
    transaction_at = now.replace(microsecond=now.microsecond + 1 if now.microsecond < 999_999 else now.microsecond)
    # Bitemporal mixin requires valid_at != transaction_at; bump the
    # microsecond. Cheap, monotonic.
    if valid_at == transaction_at:
        from datetime import timedelta
        transaction_at = valid_at + timedelta(microseconds=1)

    # 1. KG: write the Artifact (type=PROTOTYPE).
    artifact = Artifact(
        workspace_id=workspace_id,
        valid_at=valid_at,
        transaction_at=transaction_at,
        artifact_id=artifact_id,
        artifact_type=ArtifactType.PROTOTYPE,
        version=1,
        agent_output_snapshot=output_payload,
        current_version=1,
        edit_distance_from_v1=0,
        source_decision_id=inputs.decision_id,
        visualizes_artifact_id=inputs.prd_artifact_id,
    )
    graph.write_artifact(workspace_id, artifact)

    # 2. KG edges: EXPRESSED_AS + VISUALIZES.
    graph.write_edge(
        workspace_id,
        Edge(
            workspace_id=workspace_id,
            valid_at=valid_at,
            transaction_at=transaction_at,
            edge_type=EdgeType.EXPRESSED_AS,
            source_entity_id=inputs.decision_id,
            source_entity_type="Decision",
            target_entity_id=artifact_id,
            target_entity_type="Artifact",
            source="prototype_created",
            confidence=1.0,
            metadata={"artifact_type": ArtifactType.PROTOTYPE.value},
        ),
    )
    graph.write_edge(
        workspace_id,
        Edge(
            workspace_id=workspace_id,
            valid_at=valid_at,
            transaction_at=transaction_at,
            edge_type=EdgeType.VISUALIZES,
            source_entity_id=artifact_id,
            source_entity_type="Artifact",
            target_entity_id=inputs.prd_artifact_id,
            target_entity_type="Artifact",
            source="prototype_created",
            confidence=1.0,
            metadata={
                "prototype_artifact_type": ArtifactType.PROTOTYPE.value,
                "target_artifact_type": ArtifactType.PRD.value,
            },
        ),
    )

    # 3. Persist the prototype row. Status=GENERATING because real
    # codegen will be async; the stub already produced a payload but the
    # FSM still walks through GENERATING for spec-correctness — the
    # route caller polls until COMPLETE.
    db.insert_prototype(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        artifact_id=artifact_id,
        status=PrototypeStatus.GENERATING.value,
        inputs=inputs.model_dump(mode="json"),
        output_payload=output_payload,
    )

    logger.info(
        "prototype_created prototype_id=%s artifact_id=%s scenario=%s",
        prototype_id,
        artifact_id,
        inputs.scenario,
    )

    # Hydrate + return.
    return Prototype(
        id=prototype_id,
        workspace_id=workspace_id,
        artifact_id=artifact_id,
        status=PrototypeStatus.GENERATING,
        inputs=inputs,
        output_url=None,
        output_payload=output_payload,
        comments=[],
        created_at=now,
        updated_at=now,
    )


# ─────────────────────── lifecycle: read ───────────────────────


def get_prototype(prototype_id: str) -> Optional[Prototype]:
    """Fetch a Prototype + its comments. Returns None if not found."""
    row = db.get_prototype(prototype_id)
    if row is None:
        return None
    comments_rows = db.list_prototype_comments(prototype_id)
    return _row_to_prototype(row, comments_rows)


# ─────────────────────── lifecycle: comments ───────────────────────


def add_comment(
    prototype_id: str,
    comment: PrototypeComment,
    graph: GraphFacade,  # noqa: ARG001 — reserved for KG write in P2
) -> Prototype:
    """Append a comment to a prototype, run the classifier stub, persist.

    Doesn't emit a KG event today — the spec calls for
    prototype_comment_applied which fires on iteration (when comments
    are *applied* to the regen), not on comment creation. Adding the
    comment in isolation has no graph effect.
    """
    proto = get_prototype(prototype_id)
    if proto is None:
        raise PrototypeNotFoundError(f"Prototype {prototype_id!r} not found")

    # Run the classifier stub up-front so the inserted row already
    # carries the chip. The real classifier will be async; the signature
    # is set up for that.
    classification = classify_comment(proto, comment.text)
    classified = comment.model_copy(update={"classification": classification})

    db.insert_prototype_comment(
        comment_id=classified.id,
        prototype_id=prototype_id,
        author_user_id=classified.author_user_id,
        section_id=classified.section_id,
        text=classified.text,
        classification=classified.classification,
        created_at=classified.created_at.isoformat(),
    )

    logger.info(
        "prototype_comment_added prototype_id=%s comment_id=%s classification=%s",
        prototype_id,
        classified.id,
        classified.classification,
    )

    # Refresh + return.
    refreshed = get_prototype(prototype_id)
    assert refreshed is not None  # we just wrote a comment to it
    return refreshed


# ─────────────────────── lifecycle: iterate ───────────────────────


def iterate_prototype(
    prototype_id: str,
    graph: GraphFacade,
    *,
    prd_content_loader: Optional[Callable[[str], dict[str, Any]]] = None,
    figma_access_token_provider: Optional[Callable[[], str]] = None,
) -> Prototype:
    """Apply pending (unresolved) comments + regenerate the payload.

    Transitions COMPLETE/GENERATING → ITERATING, runs the (stub)
    regenerator, and ends at COMPLETE again. Emits one
    prototype_comment_applied log line per consumed comment — the KG
    write event for these lands when the delta classifier ships (P2).
    """
    proto = get_prototype(prototype_id)
    if proto is None:
        raise PrototypeNotFoundError(f"Prototype {prototype_id!r} not found")

    if proto.status not in (PrototypeStatus.COMPLETE, PrototypeStatus.GENERATING):
        raise InvalidStateTransitionError(
            f"Cannot iterate from status={proto.status.value} — "
            "must be COMPLETE or GENERATING"
        )

    pending = [c for c in proto.comments if not c.resolved]
    if not pending:
        # No-op iteration is fine — the route still returns 200. Useful
        # for the "force regen" path.
        logger.info("iterate_prototype no-op prototype_id=%s (no pending comments)", prototype_id)

    # Mark ITERATING for the duration of the regen so the UI poll can
    # show a spinner.
    db.update_prototype(prototype_id, status=PrototypeStatus.ITERATING.value)

    # Regenerate. PRD content is loaded the same way as on create.
    if prd_content_loader is not None:
        prd_content = prd_content_loader(proto.inputs.prd_artifact_id) or {}
    else:
        artifact = graph.get_artifact(proto.workspace_id, proto.inputs.prd_artifact_id)
        prd_content = dict(artifact.agent_output_snapshot or {}) if artifact else {}

    new_payload = _generate_prototype_payload(
        proto.inputs,
        prd_content,
        figma_access_token_provider=figma_access_token_provider,
    )
    # Fold the consumed comments into the meta block so the next
    # generator pass has full provenance.
    meta = new_payload.setdefault("meta", {})
    meta["applied_comments"] = [
        {"id": c.id, "classification": c.classification, "section_id": c.section_id}
        for c in pending
    ]

    db.update_prototype(
        prototype_id,
        status=PrototypeStatus.COMPLETE.value,
        output_payload=new_payload,
    )
    consumed = db.mark_comments_resolved(prototype_id)

    for c in pending:
        logger.info(
            "prototype_comment_applied prototype_id=%s comment_id=%s classification=%s",
            prototype_id,
            c.id,
            c.classification,
        )

    logger.info(
        "prototype_iterated prototype_id=%s comments_consumed=%d", prototype_id, consumed
    )

    refreshed = get_prototype(prototype_id)
    assert refreshed is not None
    return refreshed


# ─────────────────────── lifecycle: complete ───────────────────────


def complete_prototype(
    prototype_id: str,
    graph: GraphFacade,  # noqa: ARG001 — reserved for KG completion edge in P2
) -> Prototype:
    """Mark a prototype COMPLETE + emit the prototype_completed event.

    Spec §7: the prototype_completed write event is a milestone — the
    PM has finalized the prototype and downstream consumers (the
    sprint-plan agent, the comms agent) key off it.

    Stub today: we log the event + set completed_at. The graph-side
    completion edge (Artifact → Outcome, when one exists) lands when
    Outcome wiring is ready.
    """
    proto = get_prototype(prototype_id)
    if proto is None:
        raise PrototypeNotFoundError(f"Prototype {prototype_id!r} not found")

    if proto.status == PrototypeStatus.EXPORTED:
        raise InvalidStateTransitionError(
            "Cannot mark EXPORTED prototype as COMPLETE"
        )

    now_iso = _utcnow().isoformat()
    db.update_prototype(
        prototype_id,
        status=PrototypeStatus.COMPLETE.value,
        completed_at=now_iso,
    )
    logger.info("prototype_completed prototype_id=%s", prototype_id)

    refreshed = get_prototype(prototype_id)
    assert refreshed is not None
    return refreshed


# ─────────────────────── lifecycle: export ───────────────────────


ExportFormat = Literal["url", "zip", "claude_code_handoff"]


def export_prototype(
    prototype_id: str,
    format: ExportFormat,
) -> dict[str, Any]:
    """Export a prototype. Returns a dict the route can return verbatim.

    Today's stub:
        url                  → returns the stored output_url or a
                               placeholder /preview/{id} path.
        zip                  → returns a placeholder download path; the
                               real zipper lands when codegen lands.
        claude_code_handoff  → returns a manifest the (existing)
                               Claude Code session can read to bootstrap
                               itself with the prototype's payload.
    """
    proto = get_prototype(prototype_id)
    if proto is None:
        raise PrototypeNotFoundError(f"Prototype {prototype_id!r} not found")

    if proto.status not in (PrototypeStatus.COMPLETE, PrototypeStatus.EXPORTED):
        raise InvalidStateTransitionError(
            f"Cannot export from status={proto.status.value} — "
            "must be COMPLETE or EXPORTED"
        )

    if format == "url":
        result = {
            "format": "url",
            "url": proto.output_url or f"/preview/{prototype_id}",
        }
    elif format == "zip":
        result = {
            "format": "zip",
            "download_url": f"/downloads/prototype-{prototype_id}.zip",
            "placeholder": True,
        }
    elif format == "claude_code_handoff":
        result = {
            "format": "claude_code_handoff",
            "handoff": {
                "prototype_id": prototype_id,
                "artifact_id": proto.artifact_id,
                "workspace_id": proto.workspace_id,
                "scenario": proto.inputs.scenario,
                "instructions": proto.inputs.instructions,
                "output_payload": proto.output_payload,
            },
        }
    else:
        # Pydantic Literal makes this unreachable from the route layer
        # but defensive against direct callers.
        raise ValueError(f"Unknown export format: {format!r}")

    now_iso = _utcnow().isoformat()
    db.update_prototype(
        prototype_id,
        status=PrototypeStatus.EXPORTED.value,
        exported_at=now_iso,
    )
    logger.info("prototype_exported prototype_id=%s format=%s", prototype_id, format)
    return result


# ─────────────────────── internals ───────────────────────


def _validate_scenario_contract(inputs: PrototypeInputs) -> None:
    """Enforce the scenario↔input contract. Lives here (not on the
    Pydantic model) so PATCH-style partial updates can still slip
    through validation."""
    if inputs.scenario == "figma":
        if not inputs.figma_file_key:
            raise InvalidScenarioError(
                "scenario='figma' requires figma_file_key"
            )
    elif inputs.scenario == "website":
        if not inputs.website_url:
            raise InvalidScenarioError(
                "scenario='website' requires website_url"
            )
    elif inputs.scenario == "figma_codebase":
        if not inputs.figma_file_key:
            raise InvalidScenarioError(
                "scenario='figma_codebase' requires figma_file_key"
            )


def _generate_prototype_payload(
    inputs: PrototypeInputs,
    prd_content: dict[str, Any],
    *,
    figma_access_token_provider: Optional[Callable[[], str]] = None,
) -> dict[str, Any]:
    """Dispatch to the right generator. Stub today — real Next.js codegen
    lands in a follow-up PR."""
    if inputs.scenario == "figma":
        assert inputs.figma_file_key is not None  # validated above
        return generate_from_figma(
            inputs.figma_file_key,
            prd_content,
            access_token_provider=figma_access_token_provider,
        )
    if inputs.scenario == "website":
        assert inputs.website_url is not None  # validated above
        return generate_from_website(inputs.website_url, prd_content)
    if inputs.scenario == "figma_codebase":
        assert inputs.figma_file_key is not None  # validated above
        # Codebase generator raises NotImplementedError — Post-V1.
        return generate_from_codebase(
            inputs.figma_file_key,
            repo_ref="HEAD",
            prd_content=prd_content,
        )
    raise InvalidScenarioError(f"Unknown scenario: {inputs.scenario!r}")


def _row_to_prototype(row: dict[str, Any], comments_rows: list[dict[str, Any]]) -> Prototype:
    """Hydrate a Prototype model from raw DB dicts."""
    inputs_raw = row["inputs"] or {}
    if isinstance(inputs_raw, str):
        # Some fakes return jsonb as a JSON string. Tolerate both.
        import json
        inputs_raw = json.loads(inputs_raw)
    inputs = PrototypeInputs.model_validate(inputs_raw)

    output_payload = row.get("output_payload") or {}
    if isinstance(output_payload, str):
        import json
        output_payload = json.loads(output_payload)

    comments = [
        PrototypeComment(
            id=c["id"],
            author_user_id=c["author_user_id"],
            section_id=c["section_id"],
            text=c["text"],
            classification=c.get("classification"),
            resolved=bool(c.get("resolved")),
            created_at=_parse_dt(c["created_at"]),
        )
        for c in comments_rows
    ]

    return Prototype(
        id=row["id"],
        workspace_id=row["workspace_id"],
        artifact_id=row["artifact_id"],
        status=PrototypeStatus(row["status"]),
        inputs=inputs,
        output_url=row.get("output_url"),
        output_payload=output_payload,
        comments=comments,
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        completed_at=_parse_dt(row["completed_at"]) if row.get("completed_at") else None,
        exported_at=_parse_dt(row["exported_at"]) if row.get("exported_at") else None,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"Cannot parse datetime from {value!r}")


__all__ = [
    "create_prototype",
    "get_prototype",
    "add_comment",
    "iterate_prototype",
    "complete_prototype",
    "export_prototype",
    "DesignLifecycleError",
    "InvalidScenarioError",
    "PrototypeNotFoundError",
    "PRDArtifactNotFoundError",
    "InvalidStateTransitionError",
]

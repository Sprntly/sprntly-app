"""Design Agent — Pydantic models for prototypes + inline comments.

Spec source: Design_Agent_Spec.docx §3 (inputs), §4 (lifecycle), §5
(inline commenting), §7 (KG write events).

Engineering decisions:
  - PrototypeStatus is the FSM state. Transitions live in lifecycle.py;
    keeping them out of the model keeps Pydantic validators from
    second-guessing route-driven changes.
  - PrototypeInputs.scenario is the spec's A/B/C selector. Validation
    that `figma_file_key` / `website_url` matches the scenario lives in
    `create_prototype` (cross-field rules play poorly with PATCH flows).
  - PrototypeComment.classification is filled by the delta classifier
    stub (comment_classifier.py). Stays Optional because comments arrive
    before classification — the route returns the comment then enqueues
    the classifier.
  - `output_payload` is a free-form dict because the real Next.js
    codegen output (route tree, component tree, design tokens, etc.)
    isn't locked in yet. We carry it as JSON; Jide replaces the stub.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PrototypeStatus(str, Enum):
    """The Design Agent FSM.

    Transitions (enforced in lifecycle.py):
        GENERATING → COMPLETE        (synchronous stub today)
        GENERATING → ITERATING        (a comment arrives mid-generation)
        ITERATING  → COMPLETE         (regen finishes)
        COMPLETE   → ITERATING        (PM adds another round of comments)
        COMPLETE   → EXPORTED         (export endpoint hit)
        any        → FAILED           (terminal — generator raised)
    """

    GENERATING = "generating"
    ITERATING = "iterating"
    COMPLETE = "complete"
    EXPORTED = "exported"
    FAILED = "failed"


PrototypeScenario = Literal["figma", "website", "figma_codebase"]


class PrototypeInputs(BaseModel):
    """User-facing payload to create a prototype.

    Validation strategy: we don't enforce the scenario↔file-key contract
    here because Pydantic cross-field rules choke on PATCH-style partial
    updates. lifecycle.create_prototype runs the contract.
    """

    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(..., min_length=1)
    prd_artifact_id: str = Field(
        ...,
        min_length=1,
        description="Parent PRD Artifact this prototype visualizes. "
        "Maps to the VISUALIZES edge (prototype → PRD).",
    )
    decision_id: str = Field(
        ...,
        min_length=1,
        description="The Decision that motivated this prototype. "
        "Maps to the EXPRESSED_AS edge (Decision → Artifact).",
    )
    scenario: PrototypeScenario
    figma_file_key: Optional[str] = Field(
        default=None,
        description="Required when scenario='figma' or 'figma_codebase'.",
    )
    website_url: Optional[str] = Field(
        default=None,
        description="Required when scenario='website'.",
    )
    instructions: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="PM-provided steering passed verbatim to the generator.",
    )


CommentClassification = Literal["context_gap", "preference", "style"]


class PrototypeComment(BaseModel):
    """One Google-Docs-style inline comment on a prototype.

    `classification` is filled by the delta classifier after the comment
    is stored. The route returns the comment with classification=None
    and a background task classifies it; this lets the UI render
    immediately and stamp the chip when classification arrives.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    author_user_id: str = Field(..., min_length=1)
    section_id: str = Field(
        ...,
        min_length=1,
        description="Which part of the prototype the comment is anchored to "
        "(page id / component id / route name — generator-defined).",
    )
    text: str = Field(..., min_length=1, max_length=2000)
    classification: Optional[CommentClassification] = Field(
        default=None,
        description="Filled by the delta classifier (comment_classifier.py). "
        "Until classified, the UI shows the comment without a chip.",
    )
    resolved: bool = False
    created_at: datetime


class Prototype(BaseModel):
    """A Design Agent prototype: the FSM record + the generator's output.

    `artifact_id` is the corresponding KG Artifact node id. The route
    keeps both — `id` is the prototype's row id in the `prototypes` DB
    table, `artifact_id` is the KG node the rest of the graph points to
    via EXPRESSED_AS / VISUALIZES.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    workspace_id: str = Field(..., min_length=1)
    artifact_id: str = Field(
        ...,
        min_length=1,
        description="The KG Artifact node id (type=PROTOTYPE) for this row.",
    )
    status: PrototypeStatus
    inputs: PrototypeInputs
    output_url: Optional[str] = Field(
        default=None,
        description="Where the rendered prototype lives (e.g. Vercel preview URL). "
        "Filled when status reaches COMPLETE; None during GENERATING/ITERATING.",
    )
    output_payload: dict[str, Any] = Field(
        default_factory=dict,
        description="The generator's output spec — Next.js routes, components, "
        "design tokens. Format isn't locked; Jide finalizes when real codegen "
        "lands.",
    )
    comments: list[PrototypeComment] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    exported_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _completed_implies_complete_or_exported(self):
        if self.completed_at is not None and self.status not in (
            PrototypeStatus.COMPLETE,
            PrototypeStatus.EXPORTED,
        ):
            raise ValueError(
                "completed_at can only be set when status is COMPLETE or EXPORTED"
            )
        if self.exported_at is not None and self.status != PrototypeStatus.EXPORTED:
            raise ValueError("exported_at can only be set when status is EXPORTED")
        return self


__all__ = [
    "Prototype",
    "PrototypeComment",
    "PrototypeInputs",
    "PrototypeScenario",
    "PrototypeStatus",
    "CommentClassification",
]

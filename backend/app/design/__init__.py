"""Design Agent — prototype lifecycle (POC scaffold).

Spec source: Design_Agent_Spec.docx §1-§9. Three scenarios:
  A. Figma connected → prototype from team's design tokens + frames
  B. No Figma → infer style from a public website (HTML scrape)
  C. Figma + codebase → baseline branch dropped into repo (Post-V1; stub)

Public API:
    from app.design import (
        Prototype,
        PrototypeInputs,
        PrototypeComment,
        PrototypeStatus,
        create_prototype,
        add_comment,
        iterate_prototype,
        complete_prototype,
        export_prototype,
    )

This package is the architectural skeleton + the state machine. Real
Next.js codegen, design-token mapping, and the LLM-driven delta
classifier are stubbed for follow-up PRs (Jide).
"""
from app.design.models import (
    Prototype,
    PrototypeComment,
    PrototypeInputs,
    PrototypeScenario,
    PrototypeStatus,
)
from app.design.lifecycle import (
    InvalidScenarioError,
    PrototypeNotFoundError,
    PRDArtifactNotFoundError,
    add_comment,
    complete_prototype,
    create_prototype,
    export_prototype,
    get_prototype,
    iterate_prototype,
)

__all__ = [
    # models
    "Prototype",
    "PrototypeComment",
    "PrototypeInputs",
    "PrototypeScenario",
    "PrototypeStatus",
    # lifecycle
    "add_comment",
    "complete_prototype",
    "create_prototype",
    "export_prototype",
    "get_prototype",
    "iterate_prototype",
    # errors
    "InvalidScenarioError",
    "PrototypeNotFoundError",
    "PRDArtifactNotFoundError",
]

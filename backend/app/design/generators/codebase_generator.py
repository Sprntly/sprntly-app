"""Scenario C — Figma + codebase prototype generation.

Spec source: Design_Agent_Spec.docx §3.C — "Figma + codebase baseline".
Post-V1: not landing in the POC. Stub raises NotImplementedError so
route + lifecycle wiring fails loudly if someone tries to use it before
the real generator lands.
"""
from __future__ import annotations

from typing import Any


def generate_from_codebase(
    figma_file_key: str,
    repo_ref: str,
    prd_content: dict[str, Any],
) -> dict[str, Any]:
    """Scenario C — Figma file + repo branch as baseline. Post-V1.

    Args:
        figma_file_key: Figma file key.
        repo_ref: GitHub repo + branch (e.g. `org/repo@main`).
        prd_content: Parent PRD payload.

    Raises:
        NotImplementedError: always — Scenario C is Post-V1.
    """
    raise NotImplementedError(
        "Scenario C (Figma + codebase) — Post-V1. "
        "Falls outside the POC scope; see Design_Agent_Spec.docx §3.C."
    )


__all__ = ["generate_from_codebase"]

"""Property/content test for `app/ask_job_runner.py::_PRIVATE_SCOPE_SYSTEM`
— the private-chat prompt extension (relocated verbatim onto the unified
answer engine's `SurfaceScope.system_addendum` seam, no longer a standalone
responder module) that makes the project's shared memory QUERYABLE on an
"entire context" ask and clarifies the PRD-edit path is applied-in-place,
not queued for approval.

A prompt-property test, not a live-model test: proves the INSTRUCTION is
present (positive markers) and the prompt explicitly PROHIBITS the
misleading advisory/needs-acceptance framing (rather than merely omitting
it) — mirrors `test_project_memory_promotion.py`'s own prompt-property
tests for the sibling `_PROMOTE_SYSTEM` constant
(`[[feedback_property-tests-on-llm-facing-description-quality]]`).
"""
from __future__ import annotations

from app import ask_job_runner


def test_individual_system_carries_context_and_edit_clarity():
    system = ask_job_runner._PRIVATE_SCOPE_SYSTEM
    lowered = system.lower()

    # Positive: the "entire context / catch me up / why & goal" synthesis
    # instruction is present.
    assert "entire context" in lowered
    assert "catch me up" in lowered
    assert "why" in lowered and "goal" in lowered
    assert "synthesize" in lowered

    # Positive: edits-apply-in-place clarity.
    assert "applied to the document in place" in lowered
    assert "undoable" in lowered
    assert "not queued for approval" in lowered

    # Positive: the prompt must explicitly PROHIBIT the advisory / needs-
    # acceptance framing — a stated instruction, not just an omission.
    assert "never describe your role as merely advisory" in lowered
    assert "claim you cannot edit the prd" in lowered
    assert "edits must be accepted before they apply" in lowered

    # The existing project-scoping tail must survive untouched.
    assert "scoped to this one project" in lowered
    assert "never assume data from another project or company" in lowered

    # Negative-space: a prompt that OMITS these instructions entirely (the
    # pre-fix shape — no context-synthesis clause, no edit clarity, and the
    # role affirmatively stated as advisory) must not vacuously pass.
    weak_prompt = (
        "You are Sprntly, the user's private project assistant. Answer "
        "questions about this project. Your role is purely advisory — PRD "
        "edits are proposed and must be accepted by a teammate before they "
        "take effect."
    )
    weak_lower = weak_prompt.lower()
    assert "entire context" not in weak_lower
    assert "never describe your role as merely advisory" not in weak_lower
    assert "purely advisory" in weak_lower  # the weak prompt DOES make the bad claim

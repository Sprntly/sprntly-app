"""Property/content test for `app/ask_job_runner.py::_GROUP_SCOPE_SYSTEM`
— the group-chat prompt extension that carries the `edit_prd` tool's
behavioral contract to the model. The in-band `_edit_prd_handler` has always
applied the edit DIRECTLY through the shared `apply_chat_edit_scoped` writer
(no pending mutation, no confirm route), but the system prompt itself was
never updated to say so when the mechanism moved to direct-apply — so the
model, following the stale instruction, told users every edit was "proposed"
and "awaiting your team's confirmation" (and never re-invoked `edit_prd` on
a follow-up, since the prompt never told it a confirmation step exists to
act on). This is a prompt-property test, not a live-model test: proves the
direct-apply framing is present (positive markers, mirroring private's own
`_PRIVATE_SCOPE_SYSTEM` — see `test_project_individual_agent_context.py`)
and that the retired propose/confirm framing is fully gone (negative
markers) — `[[feedback_property-tests-on-llm-facing-description-quality]]`.
"""
from __future__ import annotations

import re

from app import ask_job_runner


def _normalized(text: str) -> str:
    """Collapses the prompt's own line-wrap whitespace to single spaces so
    substring assertions aren't coupled to where a paragraph happens to
    wrap in the source file."""
    return re.sub(r"\s+", " ", text.lower())


def test_group_system_carries_direct_apply_edit_clarity():
    system = ask_job_runner._GROUP_SCOPE_SYSTEM
    lowered = _normalized(system)

    # Positive: edits-apply-in-place clarity, matching private's own
    # phrasing so the two surfaces never diverge again.
    assert "applied to the document in place" in lowered
    assert "undoable" in lowered
    assert "not queued for approval" in lowered
    assert "does not need a teammate to manually accept it" in lowered

    # Positive: the prompt must explicitly PROHIBIT the advisory / needs-
    # acceptance framing — a stated instruction, not just an omission. (The
    # group prompt carries the anti-"needs-acceptance" contract via "not queued
    # for approval" + "does not need a teammate to manually accept it" above;
    # unlike private it does not also add the literal "edits must be accepted
    # before they apply" clause — an intended wording divergence, same intent.)
    assert "never describe your role as merely advisory" in lowered
    assert "claim you cannot edit the prd" in lowered

    # Negative: the retired propose/confirm framing must be fully gone —
    # this is the exact language that leaked into the model's replies
    # before the fix.
    assert "is not written immediately" not in lowered
    assert "it is proposed" not in lowered
    assert "awaits the team" not in lowered
    assert "awaiting your team" not in lowered
    assert "confirms it before it takes effect" not in lowered

    # The existing multi-party / tenancy framing must survive untouched.
    assert "one more voice in the thread" in lowered
    assert "scoped to this project only" in lowered
    assert "never assume data from another project or company" in lowered


def test_group_system_edit_language_matches_private_intent():
    """Both surfaces must make the SAME direct-apply promise — a single
    source of truth in spirit, so a future edit to one prompt without the
    other regresses this test instead of shipping silently."""
    from app import ask_job_runner

    private_lower = _normalized(ask_job_runner._PRIVATE_SCOPE_SYSTEM)
    group_lower = _normalized(ask_job_runner._GROUP_SCOPE_SYSTEM)

    # The phrases BOTH surfaces carry verbatim. Private adds one more literal
    # prohibition ("edits must be accepted before they apply") that group
    # expresses differently ("does not need a teammate to manually accept it");
    # the shared set below is the direct-apply/anti-confirmation contract both
    # state identically, so a one-sided edit to either still regresses this.
    shared_phrases = (
        "applied to the document in place",
        "not queued for approval",
        "does not need a teammate to manually accept it",
        "never describe your role as merely advisory",
        "claim you cannot edit the prd",
    )
    for phrase in shared_phrases:
        assert phrase in private_lower, phrase
        assert phrase in group_lower, phrase

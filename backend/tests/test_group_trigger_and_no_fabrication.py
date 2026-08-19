"""Group edit over-fire guard (fast, deterministic).

SCOPE NOTE (post-rewrite): the group SMART-TRIGGER port this file used to cover
— the `agent_spoke_last` / `trigger_kind` derivation, the `_ADDRESSING_NOTES`
selection, the pre-classify group-edit fork, and the DRY `_classify_group_
envelope` / `_respond_as_group_agent` source-scans — was deleted when the group
answer path collapsed into the shared `qa_agent` + `/v1/ask` lifecycle. Those
tests were bound to removed seams and have been retired:

  - the interjection/should-respond gate + its trigger kinds now live on the
    `/v1/ask` mount, covered by `test_ask_lifecycle_authz.py`;
  - the no-fabrication safety property ("a completion/Done claim only follows a
    real write") is covered by `test_delegation_truthfulness.py` (the delegate/
    complete ledger writes) and `test_project_prd_edit_parity.py` (the in-band
    `edit_prd` tool ties its "Done — I've updated the PRD." narration to an
    actual `prd_versions` write).

What survives here is the surface-agnostic over-fire guard: a plain, non-edit
message never trips the edit gate, so it can never reach the in-band `edit_prd`
tool at all.
"""
from __future__ import annotations


def test_group_plain_message_never_reaches_the_edit_tool():
    """NEGATIVE regression (the over-fire guard): a PLAIN non-edit message does
    NOT pass the `is_project_edit_request` gate, so it can never reach the
    in-band `edit_prd` tool — the tool only runs when the model calls it on an
    edit-gated turn."""
    from app.skill_router import is_project_edit_request

    assert is_project_edit_request("what's the status?") is False
    assert is_project_edit_request("who is on this project?") is False
    assert is_project_edit_request("thanks team!") is False
    # An actual edit request still passes.
    assert is_project_edit_request("update the PRD to add a section") is True

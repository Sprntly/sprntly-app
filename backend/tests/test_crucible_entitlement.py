"""The `crucible` module gate — Goal Analysis, allowlist-only.

This flag FAILS CLOSED, which is the reverse of `agents` / `top_insights` /
`company_research`. Those grandfather a missing key ON so existing companies
keep a capability they already had. This one gates a capability nobody has: a
run reads a tenant's whole corpus and spends real tokens doing it, behind a
human approval gate the company has to have been told about.

So the interesting cases are the two "not an explicit true" ones — key absent,
and flags unreadable — and both must be OFF. A test suite that only checked the
happy path would pass against a fail-open implementation, which is exactly the
bug worth preventing here.

No network, no DB, no LLM.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import entitlements
from app.entitlements import (
    CRUCIBLE_DISABLED_DETAIL,
    crucible_enabled,
    require_crucible_module,
)


# ── Resolution ───────────────────────────────────────────────────────────────

def test_explicit_true_is_the_only_way_in():
    assert crucible_enabled({"crucible": True}) is True


def test_explicit_false_is_off():
    assert crucible_enabled({"crucible": False}) is False


def test_a_missing_key_is_OFF_not_grandfathered_on():
    """The whole point. Every sibling module reads a missing key as ON; this one
    must not, or every company on the platform silently gets an experimental
    feature that spends their tokens."""
    assert crucible_enabled({}) is False
    assert crucible_enabled({"agents": True, "top_insights": True}) is False


@pytest.mark.parametrize("flags", [None, "not-a-dict", 42, [], ("crucible",)])
def test_an_unreadable_flags_row_is_OFF(flags):
    """`None` means the read never reached the row. "I couldn't read your flags"
    is not a reason to start spending a company's tokens."""
    assert crucible_enabled(flags) is False


def test_a_truthy_non_boolean_still_resolves_to_a_bool():
    """Staff-panel writes are booleans, but a hand-edited row might not be."""
    assert crucible_enabled({"crucible": 1}) is True
    assert crucible_enabled({"crucible": 0}) is False
    assert crucible_enabled({"crucible": ""}) is False


def test_it_does_not_read_any_sibling_key():
    """No legacy alias, no inheritance from `agents`. The feature is new, so
    there is no grandfathered spelling to honour — and borrowing another
    module's state would enrol companies nobody enrolled."""
    for sibling in ("agents", "top_insights", "company_research",
                    "ask_planner_shadow", "chat_intent_envelope"):
        assert crucible_enabled({sibling: True}) is False


# ── The dependency ───────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self, company_id: str) -> None:
        self.company_id = company_id


def test_dependency_passes_the_context_through_when_enabled(monkeypatch):
    monkeypatch.setattr(
        entitlements, "feature_flags_for_company", lambda cid: {"crucible": True}
    )
    ctx = _Ctx("co-1")
    assert require_crucible_module(ctx) is ctx


@pytest.mark.parametrize("flags", [{}, {"crucible": False}, {"agents": True}])
def test_dependency_403s_when_not_enrolled(monkeypatch, flags):
    monkeypatch.setattr(entitlements, "feature_flags_for_company", lambda cid: flags)
    with pytest.raises(HTTPException) as exc:
        require_crucible_module(_Ctx("co-1"))
    assert exc.value.status_code == 403
    assert exc.value.detail == CRUCIBLE_DISABLED_DETAIL


def test_the_403_detail_is_user_readable_and_names_the_feature():
    """The frontend surfaces this verbatim, so it must not say "crucible" —
    users never see the engine name."""
    assert "Goal Analysis" in CRUCIBLE_DISABLED_DETAIL
    assert "rucible" not in CRUCIBLE_DISABLED_DETAIL


def test_the_route_gate_does_not_depend_on_the_ui_hiding_anything(monkeypatch):
    """PR10 hides the composer chip for a company without the flag, but a
    hidden control is a cosmetic gate: the client decides what to render and the
    server decides what runs. A direct POST from a company that was never
    enrolled must still be refused."""
    monkeypatch.setattr(entitlements, "feature_flags_for_company", lambda cid: {})
    with pytest.raises(HTTPException):
        require_crucible_module(_Ctx("co-never-enrolled"))

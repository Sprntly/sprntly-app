"""Unit tests for app.entitlements — feature_flags → module on/off resolution.

The staff admin panel writes per-company module flags into
companies.feature_flags. Resolution is fail-open for grandfathering:
existing companies carry {} or only legacy per-capability keys
(on_demand_analysis / auto_prd_generation / engineer_agent / …), and only
an explicit modern key can turn a module off. The matrix below mirrors
the frontend mapping (StaffAdminScreen.agentsEnabled) plus the
backend-only empty/no-relevant-keys → ON default.

Pure-function tests only; the route/dependency enforcement lives in
test_module_flag_enforcement.py.
"""
from __future__ import annotations

import pytest

from app.entitlements import (
    agents_enabled,
    feature_flags_for_company,
    top_insights_enabled,
)


# ---- agents_enabled matrix ---------------------------------------------------

@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        # Grandfathering: empty / missing / junk → ON.
        ({}, True),
        (None, True),
        ("not-a-dict", True),
        (["agents"], True),
        # No relevant keys at all → ON (top_insights / unknown keys are not
        # the agents module's business).
        ({"top_insights": False}, True),
        ({"engineer_agent": False, "research_agent": False}, True),
        # Legacy keys present → OR of on_demand_analysis/auto_prd_generation.
        ({"on_demand_analysis": True}, True),
        ({"auto_prd_generation": True}, True),
        ({"on_demand_analysis": False, "auto_prd_generation": True}, True),
        ({"on_demand_analysis": False}, False),
        ({"on_demand_analysis": False, "auto_prd_generation": False}, False),
        # Explicit modern key wins over everything, both directions.
        ({"agents": True}, True),
        ({"agents": False}, False),
        ({"agents": False, "on_demand_analysis": True}, False),
        ({"agents": False, "auto_prd_generation": True}, False),
        ({"agents": True, "on_demand_analysis": False}, True),
        # Explicit key is coerced to bool (JSONB can hold anything).
        ({"agents": 0}, False),
        ({"agents": 1}, True),
        ({"agents": None}, False),
    ],
)
def test_agents_enabled_matrix(flags, expected):
    assert agents_enabled(flags) is expected


# ---- top_insights_enabled matrix ----------------------------------------------

@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        # Grandfathering: empty / missing / junk → ON.
        ({}, True),
        (None, True),
        ("not-a-dict", True),
        # Irrelevant keys (including the agents module + legacy keys) → ON.
        ({"agents": False}, True),
        ({"on_demand_analysis": False, "auto_prd_generation": False}, True),
        # Explicit key decides.
        ({"top_insights": True}, True),
        ({"top_insights": False}, False),
        ({"top_insights": False, "agents": True}, False),
        ({"top_insights": 0}, False),
        ({"top_insights": None}, False),
        # `weekly_brief` is the pre-rename spelling: honored as an alias when
        # the modern key is absent (rows written before the rename migration,
        # or restored from a backup), and ignored once the modern key exists.
        ({"weekly_brief": True}, True),
        ({"weekly_brief": False}, False),
        ({"weekly_brief": False, "top_insights": True}, True),
        ({"weekly_brief": True, "top_insights": False}, False),
    ],
)
def test_top_insights_enabled_matrix(flags, expected):
    assert top_insights_enabled(flags) is expected


# ---- feature_flags_for_company (DB read, fail-open) ----------------------------

def test_feature_flags_for_company_reads_row(fake_llm):
    """Reads the stored dict back for an existing company."""
    import uuid

    from app.db.client import require_client

    cid = uuid.uuid4().hex
    require_client().table("companies").insert(
        {
            "id": cid,
            "slug": "flags-co",
            "display_name": "Flags Co",
            "feature_flags": {"agents": False, "top_insights": True},
        }
    ).execute()
    assert feature_flags_for_company(cid) == {
        "agents": False,
        "top_insights": True,
    }


def test_feature_flags_for_company_missing_row_is_empty(fake_llm):
    """Unknown company → {} → every module resolves ON (fail-open)."""
    assert feature_flags_for_company("no-such-company") == {}
    assert agents_enabled(feature_flags_for_company("no-such-company")) is True


def test_feature_flags_for_company_read_failure_is_empty(monkeypatch):
    """Any DB error (stale schema, no client) → {} → fail-open, never raises."""
    import app.db.client as client_mod

    def _boom():
        raise RuntimeError("supabase down")

    monkeypatch.setattr(client_mod, "require_client", _boom)
    assert feature_flags_for_company("whatever") == {}

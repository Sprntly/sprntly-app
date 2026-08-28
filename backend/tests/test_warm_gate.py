"""The activity gate on speculative drill-down warming (app.warm_gate).

Warming pre-generates evidence/Ask/PRD so the first click is instant. These
tests pin the two things that make that safe to switch on: an idle workspace is
skipped, and every uncertain case still warms.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import warm_gate


def _iso(days_ago: float) -> str:
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return ts.isoformat()


@pytest.fixture
def activity(monkeypatch):
    """Stub both lookups the gate can make.

    `value` is the last-conversation timestamp; `created` is the company's
    signup date, consulted only when there is no conversation history.
    """
    box: dict = {
        "value": None, "created": None, "raises": False,
        "created_raises": False, "calls": [], "created_calls": [],
    }

    def fake(company_id):
        box["calls"].append(company_id)
        if box["raises"]:
            raise RuntimeError("supabase is down")
        return box["value"]

    def fake_created(company_id):
        box["created_calls"].append(company_id)
        if box["created_raises"]:
            raise RuntimeError("supabase is down")
        return box["created"]

    monkeypatch.setattr(warm_gate, "latest_conversation_at", fake)
    monkeypatch.setattr(warm_gate, "company_created_at", fake_created)
    return box


def test_recent_activity_warms(activity, monkeypatch):
    monkeypatch.setattr(warm_gate.settings, "warm_active_within_days", 14)
    activity["value"] = _iso(3)
    assert warm_gate.should_warm_drilldowns("co-1") is True


def test_idle_workspace_is_skipped(activity, monkeypatch):
    monkeypatch.setattr(warm_gate.settings, "warm_active_within_days", 14)
    activity["value"] = _iso(30)
    assert warm_gate.should_warm_drilldowns("co-1") is False


def test_boundary_is_inclusive(activity, monkeypatch):
    """Exactly-at-the-threshold counts as active — the gate must not flap for a
    workspace someone uses on a fortnightly rhythm."""
    monkeypatch.setattr(warm_gate.settings, "warm_active_within_days", 14)
    activity["value"] = _iso(13.99)
    assert warm_gate.should_warm_drilldowns("co-1") is True


def test_brand_new_workspace_warms_despite_no_conversations(activity, monkeypatch):
    """The onboarding case. A workspace created two days ago has never had a
    conversation — the activity check alone would skip warming for exactly the
    user whose first click matters most."""
    monkeypatch.setattr(warm_gate.settings, "warm_active_within_days", 14)
    activity["value"] = None
    activity["created"] = _iso(2)
    assert warm_gate.should_warm_drilldowns("co-1") is True


def test_signed_up_but_never_used_is_skipped(activity, monkeypatch):
    """Past the grace period with still no conversation, the silence is the
    answer — this is the abandoned-signup case the gate exists for."""
    monkeypatch.setattr(warm_gate.settings, "warm_active_within_days", 14)
    activity["value"] = None
    activity["created"] = _iso(60)
    assert warm_gate.should_warm_drilldowns("co-1") is False


def test_unknown_age_and_no_conversations_is_skipped(activity, monkeypatch):
    monkeypatch.setattr(warm_gate.settings, "warm_active_within_days", 14)
    activity["value"] = None
    activity["created"] = None
    assert warm_gate.should_warm_drilldowns("co-1") is False


def test_company_lookup_failure_fails_open(activity, monkeypatch):
    monkeypatch.setattr(warm_gate.settings, "warm_active_within_days", 14)
    activity["value"] = None
    activity["created_raises"] = True
    assert warm_gate.should_warm_drilldowns("co-1") is True


def test_recent_conversation_skips_the_company_lookup(activity, monkeypatch):
    """The grace path is a fallback, not an extra query on the hot path."""
    monkeypatch.setattr(warm_gate.settings, "warm_active_within_days", 14)
    activity["value"] = _iso(1)
    assert warm_gate.should_warm_drilldowns("co-1") is True
    assert activity["created_calls"] == []


def test_zero_days_disables_the_gate(activity, monkeypatch):
    """The escape hatch: WARM_ACTIVE_WITHIN_DAYS=0 restores always-warm without
    a deploy, and must not even hit the database."""
    monkeypatch.setattr(warm_gate.settings, "warm_active_within_days", 0)
    activity["value"] = _iso(999)
    assert warm_gate.should_warm_drilldowns("co-1") is True
    assert activity["calls"] == [], "gate-off must not query for activity"


def test_no_company_warms(activity, monkeypatch):
    monkeypatch.setattr(warm_gate.settings, "warm_active_within_days", 14)
    assert warm_gate.should_warm_drilldowns(None) is True
    assert activity["calls"] == []


def test_lookup_failure_fails_open(activity, monkeypatch):
    """A read error must never cost a user their click — warm as before."""
    monkeypatch.setattr(warm_gate.settings, "warm_active_within_days", 14)
    activity["raises"] = True
    assert warm_gate.should_warm_drilldowns("co-1") is True


def test_unparseable_timestamp_fails_open(activity, monkeypatch):
    """A garbled timestamp is a fault in us, not evidence the workspace is idle,
    so it warms — the same posture as a failed read. Distinguishing this from
    "no row at all" is the whole reason the raw value is checked separately."""
    monkeypatch.setattr(warm_gate.settings, "warm_active_within_days", 14)
    activity["value"] = "not-a-timestamp"
    assert warm_gate.should_warm_drilldowns("co-1") is True


def test_naive_timestamp_is_read_as_utc(activity, monkeypatch):
    """A driver that drops the offset must not silently mark every workspace
    idle (or, worse, active) by comparing against a naive clock."""
    monkeypatch.setattr(warm_gate.settings, "warm_active_within_days", 14)
    naive = (datetime.now(timezone.utc) - timedelta(days=2)).replace(tzinfo=None)
    activity["value"] = naive.isoformat()
    assert warm_gate.should_warm_drilldowns("co-1") is True


def test_z_suffix_timestamp_parses(activity, monkeypatch):
    monkeypatch.setattr(warm_gate.settings, "warm_active_within_days", 14)
    ts = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None)
    activity["value"] = ts.isoformat() + "Z"
    assert warm_gate.should_warm_drilldowns("co-1") is True

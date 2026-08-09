"""Tests for app.graph.evals — the sampled extraction-eval harness.

A known-good sample passes, a deliberately malformed sample is caught, for
each of the vendored hubspot/jira/clickup connector-extraction skills. Also
covers the facade sampling primitive (`recent_signals_by_skill`) and the
scheduled-cycle sweep (`run_scheduled_eval_cycle`) that fans the per-skill
check out across every company, per-(company, skill) isolated."""
from __future__ import annotations

import logging

import pytest

from app.graph.evals import (
    SKILL_EXPECTED_VOCAB,
    check_signal,
    run_scheduled_eval_cycle,
    run_skill_eval,
)
from app.graph.types import Signal


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


def _hubspot_deal_blocker_signal(**overrides) -> Signal:
    """A signal shaped exactly like hubspot-extraction's own worked example
    (backend/skills/hubspot-extraction/references/expected-signal-shape.md,
    the "deal -> deal_blocker" case)."""
    kwargs = dict(
        enterprise_id="ent-evals",
        source_type="revenue",
        kind="deal_blocker",
        content="Acme Robotics ($42,000) lost to a competitor after the "
                "buyer cited missing CSV export as the deciding factor",
        properties={"amount_usd": 42000, "stage": "closedlost"},
        confidence=0.9,
        skill_id="hubspot-extraction",
    )
    kwargs.update(overrides)
    return Signal(**kwargs)


def _jira_bug_signal(**overrides) -> Signal:
    kwargs = dict(
        enterprise_id="ent-evals",
        source_type="project_mgmt",
        kind="bug",
        content="CSV export button throws a 500 error for dashboards with "
                "more than 3 widgets",
        properties={"status": "In Progress", "priority": "High"},
        confidence=0.9,
        skill_id="jira-extraction",
    )
    kwargs.update(overrides)
    return Signal(**kwargs)


def _clickup_bug_signal(**overrides) -> Signal:
    kwargs = dict(
        enterprise_id="ent-evals",
        source_type="project_mgmt",
        kind="bug",
        content="Dashboard export times out for workspaces with 50+ "
                "widgets instead of downloading",
        properties={"status": "open", "priority": "urgent"},
        confidence=0.85,
        skill_id="clickup-extraction",
    )
    kwargs.update(overrides)
    return Signal(**kwargs)


# ---------- declared vocabulary ----------

def test_expected_vocab_covers_the_three_ticket_2_skills():
    assert set(SKILL_EXPECTED_VOCAB) == {
        "hubspot-extraction", "jira-extraction", "clickup-extraction",
    }


# ---------- check_signal: known-good samples pass ----------

def test_check_signal_passes_known_good_hubspot_sample():
    sig = _hubspot_deal_blocker_signal()
    assert check_signal(sig, "hubspot-extraction") == []


def test_check_signal_passes_known_good_jira_sample():
    sig = _jira_bug_signal()
    assert check_signal(sig, "jira-extraction") == []


def test_check_signal_passes_known_good_clickup_sample():
    sig = _clickup_bug_signal()
    assert check_signal(sig, "clickup-extraction") == []


# ---------- check_signal: deliberately malformed samples are caught ----------

def test_check_signal_flags_missing_content():
    sig = _hubspot_deal_blocker_signal(content="")
    findings = check_signal(sig, "hubspot-extraction")
    assert any(f.field == "content" for f in findings)


def test_check_signal_flags_confidence_out_of_range():
    sig = _hubspot_deal_blocker_signal(confidence=1.4)
    findings = check_signal(sig, "hubspot-extraction")
    assert any(f.field == "confidence" for f in findings)


def test_check_signal_flags_negative_confidence():
    sig = _hubspot_deal_blocker_signal(confidence=-0.1)
    findings = check_signal(sig, "hubspot-extraction")
    assert any(f.field == "confidence" for f in findings)


def test_check_signal_flags_kind_not_valid_for_source_type():
    # "bug" is a customer_voice kind for hubspot, never a revenue one.
    sig = _hubspot_deal_blocker_signal(kind="bug")
    findings = check_signal(sig, "hubspot-extraction")
    assert any(f.field == "kind" for f in findings)


def test_check_signal_flags_source_type_outside_skill_contract():
    # hubspot-extraction never emits "analytics" — outside its declared
    # source_type vocabulary entirely.
    sig = _hubspot_deal_blocker_signal(source_type="analytics")
    findings = check_signal(sig, "hubspot-extraction")
    assert any(f.field == "source_type" for f in findings)


def test_check_signal_flags_raw_record_header_leak():
    """A raw source-record header leaking into `content` instead of a
    distilled statement — the "malformed content" case the ticket names."""
    sig = _hubspot_deal_blocker_signal(
        content="[hubspot/deal id=901 at=2026-07-01]\ntitle: Acme Robotics"
    )
    findings = check_signal(sig, "hubspot-extraction")
    assert any(f.field == "content" for f in findings)


def test_check_signal_flags_non_dict_properties():
    sig = _hubspot_deal_blocker_signal(properties={})
    sig.properties = "not-a-dict"  # bypass __post_init__ typing
    findings = check_signal(sig, "hubspot-extraction")
    assert any(f.field == "properties" for f in findings)


def test_check_signal_finding_carries_routing_detail():
    """AC: findings must carry enough detail to route back to the
    responsible skill — skill_id, signal_id, enterprise_id, and what was
    wrong all present on every finding."""
    sig = _hubspot_deal_blocker_signal(confidence=5.0)
    findings = check_signal(sig, "hubspot-extraction")
    assert findings
    f = findings[0]
    assert f.skill_id == "hubspot-extraction"
    assert f.signal_id == sig.id
    assert f.enterprise_id == "ent-evals"
    assert f.detail


def test_check_signal_unknown_skill_id_skips_vocab_check_only():
    """A skill_id with no declared contract (not one of the three vendored
    extraction skills) still gets the generic required-field/confidence/
    content checks — it just isn't held to a vocabulary it never declared."""
    sig = _hubspot_deal_blocker_signal(skill_id="generic", source_type="agent_inferred")
    findings = check_signal(sig, "generic")
    assert findings == []


# ---------- facade.recent_signals_by_skill ----------

def test_recent_signals_by_skill_orders_filters_and_limits(facade):
    old = _hubspot_deal_blocker_signal(
        content="older signal about CSV export blockers",
    )
    old.transaction_at = old.transaction_at.replace(year=2026, month=1, day=1)
    new = _hubspot_deal_blocker_signal(
        content="newer signal about CSV export blockers",
    )
    new.transaction_at = new.transaction_at.replace(year=2026, month=7, day=1)
    other_skill = _jira_bug_signal()
    other_enterprise = _hubspot_deal_blocker_signal(enterprise_id="ent-other")

    facade.write_signal("ent-evals", old)
    facade.write_signal("ent-evals", new)
    facade.write_signal("ent-evals", other_skill)
    facade.write_signal("ent-other", other_enterprise)

    result = facade.recent_signals_by_skill(
        "ent-evals", "hubspot-extraction", limit=10
    )
    assert [s.id for s in result] == [new.id, old.id]  # newest first

    limited = facade.recent_signals_by_skill(
        "ent-evals", "hubspot-extraction", limit=1
    )
    assert [s.id for s in limited] == [new.id]


# ---------- run_skill_eval ----------

def test_run_skill_eval_flags_malformed_and_passes_good(facade, caplog):
    good = _hubspot_deal_blocker_signal(
        content="Acme Robotics ($42,000) lost after buyer cited missing "
                "CSV export"
    )
    bad = _hubspot_deal_blocker_signal(confidence=2.0)
    facade.write_signal("ent-evals", good)
    facade.write_signal("ent-evals", bad)

    with caplog.at_level(logging.WARNING, logger="app.graph.evals"):
        result = run_skill_eval(facade, "ent-evals", "hubspot-extraction")

    assert result.sampled == 2
    assert result.flagged == 1
    assert {f.signal_id for f in result.findings} == {bad.id}
    # Enough detail to route back to the skill: the finding log line names
    # the skill and the exact signal.
    assert any(
        "hubspot-extraction" in r.getMessage() and bad.id in r.getMessage()
        for r in caplog.records
    )


def test_run_skill_eval_all_clean_no_findings(facade, caplog):
    facade.write_signal("ent-evals", _hubspot_deal_blocker_signal())
    with caplog.at_level(logging.INFO, logger="app.graph.evals"):
        result = run_skill_eval(facade, "ent-evals", "hubspot-extraction")
    assert result.sampled == 1
    assert result.flagged == 0
    assert result.findings == []


def test_run_skill_eval_empty_sample_is_a_clean_noop(facade):
    result = run_skill_eval(facade, "ent-nothing-yet", "hubspot-extraction")
    assert result.sampled == 0
    assert result.findings == []


# ---------- run_scheduled_eval_cycle ----------

def test_run_scheduled_eval_cycle_sweeps_every_company_and_skill(facade):
    facade.write_signal("co-a", _hubspot_deal_blocker_signal(enterprise_id="co-a"))
    facade.write_signal("co-a", _hubspot_deal_blocker_signal(
        enterprise_id="co-a", confidence=9.0,  # malformed
    ))
    facade.write_signal("co-b", _jira_bug_signal(enterprise_id="co-b"))

    companies = [{"id": "co-a"}, {"id": "co-b"}]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.db.companies.list_companies", lambda: companies)
        mp.setattr("app.graph.evals.GraphFacade", lambda: facade)
        totals = run_scheduled_eval_cycle()

    assert totals["companies"] == 2
    assert totals["skills"] == 3  # hubspot / jira / clickup
    assert totals["sampled"] == 3  # 2 hubspot signals + 1 jira signal
    assert totals["findings"] == 1  # the malformed hubspot signal


def test_run_scheduled_eval_cycle_isolates_per_company_skill_failures(facade):
    facade.write_signal("co-a", _hubspot_deal_blocker_signal(enterprise_id="co-a"))
    facade.write_signal("co-b", _hubspot_deal_blocker_signal(enterprise_id="co-b"))
    companies = [{"id": "co-a"}, {"id": "co-b"}]

    real_run_skill_eval = run_skill_eval

    def _flaky(facade_arg, enterprise_id, skill_id, **kw):
        if enterprise_id == "co-a" and skill_id == "hubspot-extraction":
            raise RuntimeError("boom")
        return real_run_skill_eval(facade_arg, enterprise_id, skill_id, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.db.companies.list_companies", lambda: companies)
        mp.setattr("app.graph.evals.GraphFacade", lambda: facade)
        mp.setattr("app.graph.evals.run_skill_eval", _flaky)
        totals = run_scheduled_eval_cycle()

    # co-a/hubspot raised and was skipped; co-b/hubspot still counted.
    assert totals["companies"] == 2
    assert totals["sampled"] == 1
    assert totals["findings"] == 0


def test_run_scheduled_eval_cycle_isolates_list_companies_failure():
    with pytest.MonkeyPatch.context() as mp:
        def _raise():
            raise RuntimeError("db unreachable")

        mp.setattr("app.db.companies.list_companies", _raise)
        totals = run_scheduled_eval_cycle()

    assert totals == {"companies": 0, "skills": 0, "sampled": 0, "findings": 0}

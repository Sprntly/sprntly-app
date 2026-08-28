"""Connector → extraction-skill routing (app.kg_ingest.runner.PROVIDER_SKILLS).

sync_provider must pass the right skill_id for a connector that has a
dedicated extraction skill, and None (the fully generic path, unchanged) for
every connector that doesn't — the regression case the ticket's test plan
calls out explicitly: "content from a connector without a dedicated skill
still flows through the generic extractor unchanged."
"""
from __future__ import annotations

import pytest

from app.kg_ingest import runner
from app.kg_ingest.types import RawRecord
from app.skills.loader import get_skill, list_skills


def _rec(provider: str, i: int) -> RawRecord:
    return RawRecord(
        provider=provider, kind="record", external_id=f"{provider}-{i}",
        title=f"{provider} record {i}", text=f"body of {provider} record {i}",
    )


@pytest.fixture(autouse=True)
def ledger(monkeypatch):
    """No ledger persistence across these tests — every record is unseen."""
    monkeypatch.setattr(runner, "seen_hashes", lambda *a, **k: set())
    monkeypatch.setattr(runner, "record_hashes", lambda *a, **k: None)


@pytest.fixture
def captured_skill_ids(monkeypatch):
    """Capture the skill_id kwarg every sync_provider → extract_document call
    receives, without hitting a real LLM."""
    calls: list[str | None] = []

    def fake_extract(facade, enterprise_id, *, doc_name, text, skill_id=None, **kwargs):
        calls.append(skill_id)
        return {"signals": 1, "themes": 0, "skipped": 0}

    monkeypatch.setattr(runner, "extract_document", fake_extract)
    # skill_id routing is about extract_document, not the directed-checklist
    # second pass — stub it so a fireflies (call-provider) sync in this file
    # never reaches a real LLM call.
    monkeypatch.setattr(
        runner, "run_checklist_pass",
        lambda *a, **k: {"signals": 0, "themes": 0, "skipped": 0, "signal_ids": []},
    )
    return calls


@pytest.mark.parametrize("provider,expected_skill_id", [
    ("hubspot", "hubspot-extraction"),
    ("jira", "jira-extraction"),
    ("clickup", "clickup-extraction"),
])
def test_skill_routed_provider_passes_its_skill_id(
    captured_skill_ids, provider, expected_skill_id
):
    runner.sync_provider(None, "ent-A", provider, token="t",
                         records=[_rec(provider, 1)])
    assert captured_skill_ids == [expected_skill_id]


@pytest.mark.parametrize("provider", [
    "fireflies", "github", "sprinklr", "superset", "uploads",
])
def test_unskilled_provider_passes_no_skill_id(captured_skill_ids, provider):
    """Regression: a connector without a dedicated skill keeps flowing
    through the generic extractor — skill_id must be None, not omitted or
    accidentally inherited from another provider."""
    runner.sync_provider(None, "ent-A", provider, token="t",
                         records=[_rec(provider, 1)])
    assert captured_skill_ids == [None]


def test_provider_skills_map_only_names_installed_skills():
    """Every id in the routing table must actually be a vendored skill on
    disk — a typo here would silently fall back to UnknownSkillError at
    ingest time instead of at import/test time."""
    installed = set(list_skills())
    for provider, skill_id in runner.PROVIDER_SKILLS.items():
        assert skill_id in installed, (
            f"PROVIDER_SKILLS[{provider!r}] = {skill_id!r} has no vendored "
            f"skill directory"
        )
        # Loads without raising — SKILL.md exists and parses.
        get_skill(skill_id)


def test_every_puller_provider_is_accounted_for_in_provider_skills():
    """PROVIDER_SKILLS only maps providers that are actually in PULLERS —
    a stale entry for a removed/renamed provider would be a silent no-op."""
    assert set(runner.PROVIDER_SKILLS) <= set(runner.PULLERS)

"""Tests for the brief data-source gate (app.brief_gate).

The rule (product decision, 2026-07-22): connectors in the pm / code / design /
comms / docs categories can never satisfy brief generation on their own. Every
user-triggered generation surface — the onboarding first-brief kick
(/v1/datasets/{slug}/generate), the Connectors-settings "Regenerate brief"
button (/v1/brief/regenerate-all), and the brief page's empty-state auto-kick
(/v1/brief/regenerate, also the target of chat-initiated regenerations) —
refuses with the same 409 needs-more-data message unless an evidence-bearing
connector is ACTIVE or the user has uploaded source files.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import app.routes.brief as brief_routes
from app.brief_gate import NO_DATA_SOURCE_MESSAGE, has_brief_data_source
from app.connectors.catalog import is_evidence_provider


# ── is_evidence_provider — the type-level rule ──────────────────────────────

@pytest.mark.parametrize("provider", [
    "mixpanel", "amplitude", "google_analytics", "heap", "posthog",  # analytics
    "superset",
    "zendesk", "sprinklr", "dovetail",  # customer voice
    "fireflies", "gong",  # meetings
    "hubspot", "salesforce",  # crm
    "stripe", "chartmogul",  # revenue
    "sentry", "datadog", "newrelic", "pagerduty",  # monitoring
    "intercom",  # type `communication`, but a customer-support inbox → voice
])
def test_evidence_providers(provider):
    assert is_evidence_provider(provider), f"{provider} should be evidence"


@pytest.mark.parametrize("provider", [
    "jira", "clickup", "linear", "asana",  # task management (pm)
    "github", "gitlab", "bitbucket",  # code
    "figma", "framer",  # design
    "slack", "msteams",  # communication
    "notion", "google_drive",  # documents
])
def test_non_evidence_providers(provider):
    assert not is_evidence_provider(provider), f"{provider} should NOT be evidence"


def test_unknown_provider_is_not_evidence():
    assert not is_evidence_provider("not_a_real_provider")
    assert not is_evidence_provider(None)
    assert not is_evidence_provider("")


# ── has_brief_data_source — connections + uploads ───────────────────────────

def _conn(provider: str, status: str = "active") -> dict:
    return {"provider": provider, "status": status}


def _patch_connections(monkeypatch, isolated_settings, rows):
    monkeypatch.setattr(
        isolated_settings["db"], "list_connections", lambda _cid: rows
    )


def test_gate_false_with_no_connections_and_no_uploads(
    isolated_settings, monkeypatch
):
    _patch_connections(monkeypatch, isolated_settings, [])
    assert not has_brief_data_source("co-1", "acme")


def test_gate_false_when_only_non_evidence_connectors(
    isolated_settings, monkeypatch
):
    """The exact scenario the rule targets: pm + code + design + comms + docs
    connected, nothing else → no generation."""
    _patch_connections(monkeypatch, isolated_settings, [
        _conn("jira"), _conn("github"), _conn("figma"),
        _conn("slack"), _conn("google_drive"), _conn("notion"),
    ])
    assert not has_brief_data_source("co-1", "acme")


def test_gate_true_with_one_evidence_connector_among_non_evidence(
    isolated_settings, monkeypatch
):
    _patch_connections(monkeypatch, isolated_settings, [
        _conn("slack"), _conn("jira"), _conn("hubspot"),
    ])
    assert has_brief_data_source("co-1", "acme")


def test_gate_ignores_inactive_evidence_connections(
    isolated_settings, monkeypatch
):
    _patch_connections(monkeypatch, isolated_settings, [
        _conn("hubspot", status="revoked"), _conn("fireflies", status="error"),
    ])
    assert not has_brief_data_source("co-1", "acme")


def test_gate_true_with_uploaded_sources_only(isolated_settings, monkeypatch):
    from app.datasets import raw_path

    _patch_connections(monkeypatch, isolated_settings, [_conn("slack")])
    raw_dir = raw_path("acme")
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "interview-notes.md").write_text("real user data")
    assert has_brief_data_source("co-1", "acme")


def test_gate_excludes_auto_seeded_workspace_context(
    isolated_settings, monkeypatch
):
    """The onboarding-seeded context file alone must not count as an upload —
    onboarding info alone never produces a brief."""
    from app.datasets import raw_path

    _patch_connections(monkeypatch, isolated_settings, [])
    raw_dir = raw_path("acme")
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "sprntly-workspace-context.md").write_text("# Acme")
    assert not has_brief_data_source("co-1", "acme")


def test_gate_fails_open_when_connections_lookup_errors(
    isolated_settings, monkeypatch
):
    def _boom(_cid):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(isolated_settings["db"], "list_connections", _boom)
    assert has_brief_data_source("co-1", "acme")


# ── Route enforcement — every generation surface shares the rule ────────────

@pytest.fixture
def app_client(tenant_client):
    return tenant_client.make(slug="acme", user_id="user-acme", company_id="co-1").client


def test_regenerate_409_without_data_source(
    app_client, isolated_settings, monkeypatch
):
    _patch_connections(
        monkeypatch, isolated_settings, [_conn("jira"), _conn("github")]
    )
    with patch.object(brief_routes, "_synthesis_generate_bg") as bg:
        r = app_client.post("/v1/brief/regenerate?dataset=acme")
    assert r.status_code == 409
    assert r.json()["detail"] == NO_DATA_SOURCE_MESSAGE
    bg.assert_not_called()


def test_regenerate_all_409_without_data_source(
    app_client, isolated_settings, monkeypatch
):
    _patch_connections(monkeypatch, isolated_settings, [_conn("notion")])
    with patch.object(brief_routes, "_full_pipeline_bg") as bg:
        r = app_client.post("/v1/brief/regenerate-all?dataset=acme")
    assert r.status_code == 409
    assert r.json()["detail"] == NO_DATA_SOURCE_MESSAGE
    bg.assert_not_called()


def test_datasets_generate_409_without_data_source(
    app_client, isolated_settings, monkeypatch
):
    app_client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    _patch_connections(monkeypatch, isolated_settings, [_conn("figma")])
    with patch.object(brief_routes, "_synthesis_generate_bg") as bg:
        r = app_client.post("/v1/datasets/acme/generate")
    assert r.status_code == 409
    assert r.json()["detail"] == NO_DATA_SOURCE_MESSAGE
    bg.assert_not_called()


def test_regenerate_starts_with_evidence_connector(
    app_client, isolated_settings, monkeypatch
):
    _patch_connections(
        monkeypatch, isolated_settings, [_conn("jira"), _conn("fireflies")]
    )

    async def _noop(dataset):
        return None

    with patch.object(brief_routes, "_synthesis_generate_bg", side_effect=_noop):
        r = app_client.post("/v1/brief/regenerate?dataset=acme")
    assert r.status_code == 200
    assert r.json() == {"started": True, "dataset": "acme"}

"""Tests for the brief data-source gate (app.brief_gate).

The rule (product decision, 2026-07-22): connectors in the pm / code / design /
comms / docs categories can never satisfy brief generation on their own.
Exception since 2026-07-30: Slack is dual-typed communication + customer-voice
(its synced channels are evidence), so it DOES count as a data source. Every
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
    "slack",  # dual-typed communication + customer-voice (2026-07-30):
              # user-selected channels are synced into the corpus as evidence
])
def test_evidence_providers(provider):
    assert is_evidence_provider(provider), f"{provider} should be evidence"


@pytest.mark.parametrize("provider", [
    "jira", "clickup", "linear", "asana",  # task management (pm)
    "github", "gitlab", "bitbucket",  # code
    "figma", "framer",  # design
    "msteams",  # communication (delivery target only — unlike dual-typed Slack)
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
        _conn("msteams"), _conn("google_drive"), _conn("notion"),
    ])
    assert not has_brief_data_source("co-1", "acme")


def test_gate_true_with_one_evidence_connector_among_non_evidence(
    isolated_settings, monkeypatch
):
    _patch_connections(monkeypatch, isolated_settings, [
        _conn("msteams"), _conn("jira"), _conn("hubspot"),
    ])
    assert has_brief_data_source("co-1", "acme")


def test_gate_true_with_only_slack_connected(isolated_settings, monkeypatch):
    """Slack alone satisfies the gate (2026-07-30): dual-typed communication +
    customer-voice — its synced channels bring evidence in."""
    _patch_connections(monkeypatch, isolated_settings, [_conn("slack")])
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

    _patch_connections(monkeypatch, isolated_settings, [_conn("msteams")])
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


# ── generate_brief_for — the scheduler/startup/pipeline-path gate ────────────
#
# The endpoint 409s above only cover user-triggered surfaces. The weekly
# scheduler, the startup pass, and pipeline stage 5 call
# synthesis_brief.generate_brief_for directly — which must apply the SAME rule
# itself (raising NoBriefDataSourceError), AFTER seeding so non-evidence
# connectors still reach the KG for PRDs/chat.

import app.synthesis_brief as sb
from app.brief_gate import NoBriefDataSourceError
from app.synthesis.agent import EmptyKnowledgeGraphError


def _seed_company(db, *, company_id: str, slug: str) -> None:
    existing = db.table("companies").select("id").eq("id", company_id).execute().data
    if not existing:
        db.table("companies").insert(
            {"id": company_id, "slug": slug, "display_name": slug.title()}
        ).execute()


def test_generate_brief_for_refuses_jira_only_after_seeding(
    isolated_settings, monkeypatch
):
    """The reported bug (2026-07-27): a Jira-only company got a weekly brief.
    Seeding must still run (Jira → KG), but synthesis must be refused."""
    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    _patch_connections(monkeypatch, isolated_settings, [_conn("jira")])
    with patch.object(sb, "seed_incremental", return_value={"corpus": {}}) as seed, \
         patch.object(sb, "run_synthesis") as run:
        with pytest.raises(NoBriefDataSourceError, match="No data to generate"):
            sb.generate_brief_for("acme")
    seed.assert_called_once()   # Jira still landed in the KG
    run.assert_not_called()     # but no brief came out of it


def test_generate_brief_for_refuses_cached_brief_without_source(
    isolated_settings, monkeypatch
):
    """An evidence-less company with a leftover brief (e.g. generated before
    this gate) gets a refusal, not the stale cached brief re-served."""
    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    _patch_connections(monkeypatch, isolated_settings, [_conn("jira")])
    prior = {"id": 42, "generated_at": "2026-06-10T00:00:00+00:00"}
    with patch.object(sb, "get_current_brief", return_value=prior), \
         patch.object(sb, "seed_incremental", return_value={"corpus": {}}), \
         patch.object(sb, "run_synthesis") as run:
        with pytest.raises(NoBriefDataSourceError):
            sb.generate_brief_for("acme")
    run.assert_not_called()


def test_generate_brief_for_proceeds_with_evidence_connector(
    isolated_settings, monkeypatch
):
    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    _patch_connections(
        monkeypatch, isolated_settings, [_conn("jira"), _conn("hubspot")]
    )
    with patch.object(sb, "seed_incremental", return_value={"corpus": {}}), \
         patch.object(sb, "run_synthesis",
                      return_value={"summary_headline": "ok"}) as run:
        out = sb.generate_brief_for("acme")
    assert out["summary_headline"] == "ok"
    run.assert_called_once()


def test_no_brief_data_source_error_is_benign_to_existing_handlers():
    """Every caller (pipeline `skipped` status, startup INFO skip, routes'
    needs-more-data 409) keys off EmptyKnowledgeGraphError / ValueError —
    the refusal must stay inside that hierarchy."""
    err = NoBriefDataSourceError("x")
    assert isinstance(err, EmptyKnowledgeGraphError)
    assert isinstance(err, ValueError)


def test_startup_pass_skips_no_source_company(isolated_settings, monkeypatch):
    """generate_all_synthesis_briefs treats the refusal as the benign
    empty-KG case: logged and skipped, never raised."""
    monkeypatch.setattr(
        "app.db.companies.list_companies",
        lambda: [{"id": "co-1", "slug": "acme"}],
    )
    with patch.object(sb, "generate_brief_for",
                      side_effect=NoBriefDataSourceError("no source")), \
         patch("app.brief_runner.warm_synthesis_drilldowns") as warm:
        sb.generate_all_synthesis_briefs()  # must not raise
    warm.assert_not_called()


# ── scheduler delivery — the catch-up fallback must respect the same rule ────
#
# The weekly tick's phase-2 fallback delivers the CURRENT brief even when
# generation was refused, so a leftover brief row (created before the
# generation gate) would still be pushed to a Jira-only company every week.

import app.scheduler as sched_mod


async def test_delivery_suppressed_without_data_source(
    isolated_settings, monkeypatch
):
    _patch_connections(monkeypatch, isolated_settings, [_conn("jira")])
    with patch("app.db.briefs.get_current_brief",
               return_value={"id": 1, "insights": []}), \
         patch("app.synthesis.delivery.deliver_brief") as deliver:
        delivered = await sched_mod._deliver_brief_for_company("co-1", "acme")
    assert delivered is False
    deliver.assert_not_called()


async def test_delivery_proceeds_with_data_source(
    isolated_settings, monkeypatch
):
    _patch_connections(
        monkeypatch, isolated_settings, [_conn("jira"), _conn("hubspot")]
    )
    brief = {"id": 1, "insights": []}
    with patch("app.db.briefs.get_current_brief", return_value=brief), \
         patch("app.synthesis.delivery.deliver_brief") as deliver:
        delivered = await sched_mod._deliver_brief_for_company("co-1", "acme")
    assert delivered is True
    deliver.assert_called_once_with("co-1", brief)

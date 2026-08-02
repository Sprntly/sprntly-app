"""Connector TYPE classification (app/connectors/catalog.py): what each
provider IS, surfaced on the connectors API and consumed by type-driven
features (the ticket sync's eligible-provider set).

Cardinality is a product decision (2026-07-30): connectors may carry multiple
types when the tool genuinely is more than one thing — Slack is the first
(communication + customer-voice). Every provider carries at least one type.
"""
from __future__ import annotations

from app.connectors.catalog import (
    COMMUNICATION,
    CONNECTOR_TYPES,
    CUSTOMER_VOICE,
    DOCUMENTS,
    TASK_MANAGEMENT,
    has_type,
    is_evidence_provider,
    providers_with_type,
    types_for,
)


def test_every_connectable_provider_is_classified():
    """Every provider with a real auth backend carries a type — a new
    connector must be classified before it ships."""
    connectable = [
        "jira", "clickup", "google_drive", "hubspot",
        "github", "figma", "slack", "fireflies", "confluence",
    ]
    for provider in connectable:
        assert types_for(provider), f"{provider} has no types"


def test_every_provider_has_at_least_one_type():
    """Multi-type is allowed (product decision 2026-07-30), untyped is not —
    every catalog entry must say what the tool IS."""
    for provider, types in CONNECTOR_TYPES.items():
        assert len(types) >= 1, f"{provider} has no types"


def test_slack_is_dual_typed():
    """Slack is the first multi-type connector: a communication tool (brief
    delivery) AND a customer-voice source (user-selected channels are synced
    into the corpus). Order is meaningful-ish (primary first) — pin it."""
    assert types_for("slack") == [COMMUNICATION, CUSTOMER_VOICE]
    assert has_type("slack", COMMUNICATION)
    assert has_type("slack", CUSTOMER_VOICE)
    assert "slack" in providers_with_type(CUSTOMER_VOICE)


def test_types_are_provider_specific():
    assert types_for("clickup") == [TASK_MANAGEMENT]
    assert types_for("jira") == [TASK_MANAGEMENT]
    assert types_for("hubspot") == ["crm"]
    assert types_for("fireflies") == ["meetings"]
    assert types_for("confluence") == [DOCUMENTS]
    # Unknown providers are just untyped — never an error.
    assert types_for("not-a-provider") == []
    assert types_for(None) == []


def test_confluence_is_not_evidence():
    """A wiki is internal documentation: a page asserting a customer problem
    is the author's CLAIM about it, not measured proof. So Confluence sits
    with Notion/Google Docs as `documents` and — unlike `uploads` — is
    deliberately NOT an evidence exception, meaning it cannot open the brief's
    data-source gate on its own."""
    assert is_evidence_provider("confluence") is False
    assert is_evidence_provider("google_drive") is False
    # The contrast that makes the rule legible: uploads IS an exception.
    assert is_evidence_provider("uploads") is True


def test_providers_with_type_and_has_type():
    trackers = providers_with_type(TASK_MANAGEMENT)
    assert {"jira", "clickup", "linear", "asana"} <= set(trackers)
    assert "slack" not in trackers
    assert has_type("clickup", TASK_MANAGEMENT)
    assert not has_type("slack", TASK_MANAGEMENT)


def test_every_type_value_is_kebab_case():
    for provider, types in CONNECTOR_TYPES.items():
        for t in types:
            assert t == t.lower() and " " not in t, f"{provider}: {t!r}"


def test_ticket_sync_providers_is_typed_and_implemented():
    """Sync eligibility = typed task-management ∩ engine-implemented. Linear
    is typed as a tracker but not implemented, so it must NOT be eligible."""
    from app.stories.sync import SYNC_PROVIDERS, ticket_sync_providers

    eligible = ticket_sync_providers()
    assert set(eligible) == {"clickup", "jira", "asana"}
    assert set(eligible) <= set(SYNC_PROVIDERS)
    assert "linear" not in eligible and "slack" not in eligible


def test_public_connection_carries_types():
    """GET /v1/connectors rows expose the provider's types so the web derives
    feature availability from them."""
    from app.routes.connectors import _public_connection

    row = {
        "id": 1, "provider": "clickup", "status": "active",
        "config_json": None,
    }
    assert _public_connection(row)["types"] == [TASK_MANAGEMENT]
    assert _public_connection({**row, "provider": "slack"})["types"] == [
        COMMUNICATION, CUSTOMER_VOICE,
    ]

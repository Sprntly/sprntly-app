"""Connector TYPE classification (app/connectors/catalog.py): what each
provider IS, surfaced on the connectors API and consumed by type-driven
features (the ticket sync's eligible-provider set).

Cardinality is a product decision: exactly ONE type per connector for now,
kept list-shaped so multi-type support later is a data change only.
"""
from __future__ import annotations

from app.connectors.catalog import (
    CONNECTOR_TYPES,
    TASK_MANAGEMENT,
    has_type,
    providers_with_type,
    types_for,
)


def test_every_connectable_provider_is_classified():
    """Every provider with a real auth backend carries a type — a new
    connector must be classified before it ships."""
    connectable = [
        "jira", "clickup", "google_drive", "hubspot",
        "github", "figma", "slack", "fireflies",
    ]
    for provider in connectable:
        assert types_for(provider), f"{provider} has no types"


def test_exactly_one_type_per_connector_for_now():
    """Product decision (2026-07): one type per connector. The list shape is
    future-proofing, not an invitation — this test is the guardrail."""
    for provider, types in CONNECTOR_TYPES.items():
        assert len(types) == 1, f"{provider} has {len(types)} types: {types}"


def test_types_are_provider_specific():
    assert types_for("clickup") == [TASK_MANAGEMENT]
    assert types_for("jira") == [TASK_MANAGEMENT]
    assert types_for("slack") == ["communication"]
    assert types_for("hubspot") == ["crm"]
    assert types_for("fireflies") == ["meetings"]
    # Unknown providers are just untyped — never an error.
    assert types_for("not-a-provider") == []
    assert types_for(None) == []


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
    assert set(eligible) == {"clickup", "jira"}
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
    assert _public_connection({**row, "provider": "slack"})["types"] == ["communication"]

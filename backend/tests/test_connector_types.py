"""Connector TYPE classification (app/connectors/catalog.py): what each
provider IS (multi-valued), surfaced on the connectors API and consumed by
type-driven features (the ticket sync's eligible-provider set).
"""
from __future__ import annotations

from app.connectors.catalog import (
    CONNECTOR_TYPES,
    TASK_TRACKING,
    has_type,
    providers_with_type,
    types_for,
)


def test_every_connectable_provider_is_classified():
    """Every provider with a real auth backend carries at least one type —
    a new connector must be classified before it ships."""
    connectable = [
        "jira", "clickup", "google_drive", "hubspot",
        "github", "figma", "slack", "fireflies",
    ]
    for provider in connectable:
        assert types_for(provider), f"{provider} has no types"


def test_types_are_multi_valued_and_provider_specific():
    assert types_for("clickup") == [TASK_TRACKING]
    assert types_for("jira") == [TASK_TRACKING]
    assert types_for("slack") == ["communication"]
    # Multi-valued: one tool can be several things.
    assert set(types_for("hubspot")) == {"crm", "revenue"}
    assert set(types_for("fireflies")) == {"meetings", "customer-voice"}
    # Unknown providers are just untyped — never an error.
    assert types_for("not-a-provider") == []
    assert types_for(None) == []


def test_providers_with_type_and_has_type():
    trackers = providers_with_type(TASK_TRACKING)
    assert {"jira", "clickup", "linear", "asana"} <= set(trackers)
    assert "slack" not in trackers
    assert has_type("clickup", TASK_TRACKING)
    assert not has_type("slack", TASK_TRACKING)


def test_every_type_value_is_kebab_case():
    for provider, types in CONNECTOR_TYPES.items():
        for t in types:
            assert t == t.lower() and " " not in t, f"{provider}: {t!r}"


def test_ticket_sync_providers_is_typed_and_implemented():
    """Sync eligibility = typed task-tracking ∩ engine-implemented. Linear is
    typed as a tracker but not implemented, so it must NOT be eligible."""
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
    assert _public_connection(row)["types"] == [TASK_TRACKING]
    assert _public_connection({**row, "provider": "slack"})["types"] == ["communication"]

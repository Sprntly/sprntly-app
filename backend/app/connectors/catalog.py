"""Connector type classification — what each provider IS, orthogonally to
whether/how it's connected.

Types are fixed properties of a PROVIDER (not of a customer's connection row
— no DB column). Features consume them instead of hardcoding provider names:
e.g. the ticket sync offers connected providers typed `task-management`
(intersected with the providers the sync engine actually implements — a type
declares what a tool is, the engine declares what we can do with it). The
web's connectorsCatalog.ts mirrors this map for display; this module is the
backend's authority.

Cardinality (product decision, 2026-07): every connector carries EXACTLY ONE
type for now. The shape stays list-valued so allowing multi-type connectors
later is a data change, not a schema/API change — do not add a second type to
any entry without product sign-off.
"""
from __future__ import annotations

# ── The type vocabulary ──────────────────────────────────────────────────────
TASK_MANAGEMENT = "task-management"
COMMUNICATION = "communication"
DOCUMENTS = "documents"
CUSTOMER_VOICE = "customer-voice"
MEETINGS = "meetings"
ANALYTICS = "analytics"
REVENUE = "revenue"
CRM = "crm"
CODE = "code"
MONITORING = "monitoring"
DESIGN = "design"

#: provider → its type (a one-element list; see cardinality note above).
#: Covers every provider the backend has an auth module or puller for, plus
#: catalog-only ("coming soon") providers so the web can read one map. A
#: provider absent here has no types (empty list).
CONNECTOR_TYPES: dict[str, list[str]] = {
    # Task management
    "jira": [TASK_MANAGEMENT],
    "clickup": [TASK_MANAGEMENT],
    "linear": [TASK_MANAGEMENT],
    "asana": [TASK_MANAGEMENT],
    # Communication
    "slack": [COMMUNICATION],
    "msteams": [COMMUNICATION],
    "intercom": [COMMUNICATION],
    # Documentation
    "google_drive": [DOCUMENTS],
    "notion": [DOCUMENTS],
    # Customer voice / meetings
    "zendesk": [CUSTOMER_VOICE],
    "sprinklr": [CUSTOMER_VOICE],
    "fireflies": [MEETINGS],
    "gong": [MEETINGS],
    "dovetail": [CUSTOMER_VOICE],
    "salesforce": [CRM],
    # Analytics
    "mixpanel": [ANALYTICS],
    "amplitude": [ANALYTICS],
    "google_analytics": [ANALYTICS],
    "heap": [ANALYTICS],
    "posthog": [ANALYTICS],
    # Revenue / CRM
    "stripe": [REVENUE],
    "chartmogul": [REVENUE],
    "hubspot": [CRM],
    # Code
    "github": [CODE],
    "gitlab": [CODE],
    "bitbucket": [CODE],
    # Monitoring
    "sentry": [MONITORING],
    "datadog": [MONITORING],
    "newrelic": [MONITORING],
    "pagerduty": [MONITORING],
    # Design
    "figma": [DESIGN],
    "framer": [DESIGN],
}


def types_for(provider: str | None) -> list[str]:
    """The provider's types ([] for unknown providers — never raises)."""
    return list(CONNECTOR_TYPES.get((provider or "").strip().lower(), []))


def has_type(provider: str | None, connector_type: str) -> bool:
    return connector_type in types_for(provider)


def providers_with_type(connector_type: str) -> list[str]:
    """Every provider carrying `connector_type`, catalog order."""
    return [p for p, ts in CONNECTOR_TYPES.items() if connector_type in ts]

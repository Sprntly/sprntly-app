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
    # The user's OWN documents, uploaded into a named source. A documentation
    # tool by type, but an evidence source by intent — see the exceptions below.
    "uploads": [DOCUMENTS],
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
    "superset": [ANALYTICS],
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


# ── Evidence-bearing providers (the brief data-source rule) ──────────────────
#
# The weekly brief is synthesized from connectors that BRING IN evidence about
# the product and its customers. Five kinds of tool don't do that, so they can
# never satisfy brief generation on their own (mirrors NON_EVIDENCE_CATEGORIES
# in web/app/lib/connectorsCatalog.ts — the two lists must agree):
#
#   task-management  Jira / ClickUp / Asana — where work is TRACKED once
#                    decided; the brief's output flows to them, not from them.
#   code             GitHub — what was BUILT, not what users need.
#   design           Figma / Framer — design surfaces, not customer signal.
#   communication    Slack / Teams — DELIVERY targets for the brief.
#   documents        Notion / Google Docs — internal documentation; context
#                    that shapes a brief, not customer/product evidence.
#
# Everything else (analytics, customer-voice, meetings, crm, revenue,
# monitoring) is evidence and can drive a brief.
NON_EVIDENCE_TYPES: frozenset[str] = frozenset(
    {TASK_MANAGEMENT, CODE, DESIGN, COMMUNICATION, DOCUMENTS}
)

#: Providers whose TYPE is non-evidence but which still count as a data
#: source. Intercom's type is `communication`, but as a customer-support inbox
#: it carries voice-of-customer evidence (the web catalog files it under the
#: `voice` category for the same reason). `uploads` is typed `documents` like
#: Notion/Drive, but it is the user DELIBERATELY handing us a named, described
#: corpus of their own business documents — research, support exports, strategy
#: — rather than a whole workspace of internal docs, so it does count (the web
#: catalog gives it its own evidence-bearing `uploads` category to match).
_EVIDENCE_PROVIDER_EXCEPTIONS: frozenset[str] = frozenset({"intercom", "uploads"})


def is_evidence_provider(provider: str | None) -> bool:
    """True iff `provider` can feed the brief with evidence.

    Unknown providers return False — a tool we can't classify can't be shown
    to gather anything (matches web isEvidenceConnector).
    """
    key = (provider or "").strip().lower()
    if key in _EVIDENCE_PROVIDER_EXCEPTIONS:
        return True
    ts = types_for(key)
    return bool(ts) and any(t not in NON_EVIDENCE_TYPES for t in ts)


def types_for(provider: str | None) -> list[str]:
    """The provider's types ([] for unknown providers — never raises)."""
    return list(CONNECTOR_TYPES.get((provider or "").strip().lower(), []))


def has_type(provider: str | None, connector_type: str) -> bool:
    return connector_type in types_for(provider)


def providers_with_type(connector_type: str) -> list[str]:
    """Every provider carrying `connector_type`, catalog order."""
    return [p for p, ts in CONNECTOR_TYPES.items() if connector_type in ts]

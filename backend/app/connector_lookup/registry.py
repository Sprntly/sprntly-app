"""Which connectors chat can read live — and honest copy for the ones it can't.

Three tiers, and the difference matters to the user:

- LOOKUP_PROVIDERS — there is an adapter; chat reads the tool live.
- DEFERRED — the connector exists (it syncs into the KG) but has no live-read
  adapter yet, so a question about it is answered honestly rather than from a
  half-capability.
- NO_CONNECTOR — Sprntly has no connector for it at all. Saying "I searched
  and found nothing" would be a lie; we say it isn't connectable yet.

The router (skill_router.is_connector_lookup) recognises names in all three
tiers on purpose: a user who names Zendesk deserves the honest answer here, not
a KG-flavoured guess from the generic path.
"""
from __future__ import annotations

import logging

from app.connector_lookup import answer as connector_answer
from app.connector_lookup.base import LookupProvider

logger = logging.getLogger(__name__)

#: User-facing names (mirrors web/app/lib/connectorsCatalog.ts labels).
DISPLAY_NAMES: dict[str, str] = {
    "jira": "Jira",
    "confluence": "Confluence",
    "clickup": "ClickUp",
    "asana": "Asana",
    "linear": "Linear",
    "slack": "Slack",
    "msteams": "Microsoft Teams",
    "intercom": "Intercom",
    "google_drive": "Google Drive",
    "notion": "Notion",
    "uploads": "Uploaded files",
    "zendesk": "Zendesk",
    "sprinklr": "Sprinklr",
    "fireflies": "Fireflies",
    "gong": "Gong",
    "zoom": "Zoom",
    "github": "GitHub",
    "gitlab": "GitLab",
    "hubspot": "HubSpot",
    "salesforce": "Salesforce",
    "stripe": "Stripe",
    "amplitude": "Amplitude",
    "mixpanel": "Mixpanel",
    "superset": "Superset",
    "figma": "Figma",
    "sentry": "Sentry",
}


def display_name(provider: str) -> str:
    return DISPLAY_NAMES.get(provider, provider.replace("_", " ").title())


#: provider → the adapter singleton, imported lazily so importing the registry
#: (which the router does on every intercepted question) doesn't drag in every
#: connector client.
def provider_for(name: str) -> LookupProvider | None:
    """Return the live-read adapter for a provider key, or None."""
    if name == "jira":
        from app.connector_lookup.jira import PROVIDER

        return PROVIDER
    if name == "clickup":
        from app.connector_lookup.clickup import PROVIDER

        return PROVIDER
    if name == "slack":
        from app.connector_lookup.slack import PROVIDER

        return PROVIDER
    if name == "fireflies":
        from app.connector_lookup.fireflies import PROVIDER

        return PROVIDER
    if name == "github":
        from app.connector_lookup.github import PROVIDER

        return PROVIDER
    if name == "hubspot":
        from app.connector_lookup.hubspot import PROVIDER

        return PROVIDER
    if name == "google_drive":
        from app.connector_lookup.gdrive import PROVIDER

        return PROVIDER
    if name == "confluence":
        from app.connector_lookup.confluence import PROVIDER

        return PROVIDER
    return None


#: Keys `provider_for` can resolve. Kept as data so the router and the
#: not-supported copy agree with what actually exists.
LOOKUP_PROVIDERS: tuple[str, ...] = (
    "jira", "clickup", "slack", "fireflies", "github", "hubspot", "google_drive",
    "confluence",
)

#: Connected (they sync into the KG) but no live-read adapter yet.
DEFERRED: dict[str, str] = {
    "asana": "Asana",
    "sprinklr": "Sprinklr",
    "superset": "Superset",
    "figma": "Figma",
    # Connects and (once the puller lands) syncs into the KG, but has no
    # live-read adapter — so "what was said on yesterday's Zoom call" gets the
    # honest "it syncs but I can't query it live in chat yet" rather than a
    # KG-flavoured guess dressed up as a live read.
    "zoom": "Zoom",
}

#: No Sprntly connector at all — catalog entries only (connectors/catalog.py).
NO_CONNECTOR: dict[str, str] = {
    "zendesk": "Zendesk",
    "gong": "Gong",
    "linear": "Linear",
    "amplitude": "Amplitude",
    "stripe": "Stripe",
    "notion": "Notion",
    "intercom": "Intercom",
    "mixpanel": "Mixpanel",
    "gitlab": "GitLab",
    "sentry": "Sentry",
}

#: At most this many providers' toolsets go into one loop. Two is enough for
#: "check Slack and Jira" while keeping the tool list (and the cost) small.
MAX_PROVIDERS_PER_LOOKUP = 2


def connected_providers(enterprise_id: str) -> list[str]:
    """Provider keys this company has a connection row for. Best-effort: an
    unavailable DB yields [] rather than breaking the answer.

    Slack is per-user (db/connections.py), so its rows are counted separately —
    a company-scoped list_connections may not surface them.
    """
    from app import db

    found: list[str] = []
    try:
        for row in db.list_connections(enterprise_id) or []:
            provider = row.get("provider")
            if provider and provider not in found:
                found.append(provider)
    except Exception:  # noqa: BLE001
        logger.warning("connector-lookup: list_connections failed", exc_info=True)
    if "slack" not in found:
        try:
            if db.list_slack_connections(enterprise_id):
                found.append("slack")
        except Exception:  # noqa: BLE001
            logger.warning("connector-lookup: slack row lookup failed", exc_info=True)
    return found


def connected_display_names(enterprise_id: str) -> list[str]:
    """Display names of the company's connected tools, for "connected right now:
    …" copy. `uploads` is a real source and stays in the list."""
    return [display_name(p) for p in connected_providers(enterprise_id)]


def not_supported_message(hints: list[str], enterprise_id: str) -> str:
    """Honest copy for a question about a source chat cannot read live.

    Deliberately does NOT run a tool loop or answer from the KG: the user named a
    specific source, and the truthful answer is that this source isn't readable,
    plus what is.
    """
    deferred = [h for h in hints if h in DEFERRED]
    absent = [h for h in hints if h in NO_CONNECTOR]
    parts: list[str] = []
    if absent:
        names = " and ".join(display_name(h) for h in absent)
        verb = "isn't" if len(absent) == 1 else "aren't"
        parts.append(
            f"{names} {verb} a Sprntly connector yet, so I can't read it — I'd "
            "rather say that than guess."
        )
    if deferred:
        names = " and ".join(display_name(h) for h in deferred)
        verb = "syncs" if len(deferred) == 1 else "sync"
        parts.append(
            f"{names} {verb} into your company's knowledge graph, but I can't "
            "query it live in chat yet."
        )
    if not parts:
        parts.append("I can't read that source live yet.")
    sentence = connector_answer.connected_sources_sentence(enterprise_id)
    if sentence:
        parts.append(sentence.strip() + " Ask me about any of those and I'll read them live.")
    return " ".join(parts)


def answer_for_hints(
    *,
    enterprise_id: str,
    question: str,
    history: list[dict] | None,
    hints: set[str],
    include_knowledge_graph: bool = False,
) -> dict:
    """Answer an ad-hoc connector question for the providers the router matched.

    Supported providers run the shared tool loop (at most
    MAX_PROVIDERS_PER_LOOKUP of them, connected ones first). When the question
    only named sources we cannot read, nothing is fetched and nothing is
    invented — the honest message is returned directly.

    `include_knowledge_graph` is passed through to the tool loop for questions
    that named no source at all (the document-intent path) — see
    connector_lookup/knowledge_graph.py for why those need both readers.
    """
    supported = [h for h in hints if h in LOOKUP_PROVIDERS]
    if not supported:
        return connector_answer.plain_payload(
            not_supported_message(sorted(hints), enterprise_id),
            skill_action="Connector lookup",
        )
    # Connected providers first, so a two-provider cap never drops the one we can
    # actually read.
    connected = set(connected_providers(enterprise_id))
    supported.sort(key=lambda p: (p not in connected, p))
    chosen = supported[:MAX_PROVIDERS_PER_LOOKUP]
    providers = [p for p in (provider_for(name) for name in chosen) if p is not None]
    if not providers:
        return connector_answer.plain_payload(
            not_supported_message(sorted(hints), enterprise_id),
            skill_action="Connector lookup",
        )
    # The halves we are NOT reading: sources with no adapter, and supported ones
    # dropped by the ≤2 cap. Threaded into the system block so an answer about
    # Slack never reads as an answer about "Slack and HubSpot".
    unavailable = [
        display_name(h) for h in sorted(hints)
        if h not in {p.provider for p in providers}
    ]
    return connector_answer.answer(
        enterprise_id=enterprise_id,
        question=question,
        history=history,
        providers=providers,
        unavailable_names=unavailable,
        include_knowledge_graph=include_knowledge_graph,
    )

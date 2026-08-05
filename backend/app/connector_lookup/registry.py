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
    "google_meet": "Google Meet",
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
    if name == "zoom":
        from app.connector_lookup.zoom import PROVIDER

        return PROVIDER
    return None


#: Keys `provider_for` can resolve. Kept as data so the router and the
#: not-supported copy agree with what actually exists.
LOOKUP_PROVIDERS: tuple[str, ...] = (
    "jira", "clickup", "slack", "fireflies", "github", "hubspot", "google_drive",
    "confluence", "zoom",
)

#: Connected (they sync into the KG) but no live-read adapter yet.
DEFERRED: dict[str, str] = {
    "asana": "Asana",
    # Syncs into the KG (kg_ingest/pullers/google_meet.py) but has no live-read
    # adapter yet, so "what did we say in google meet yesterday" is answered
    # honestly rather than from a half-capability. Deliberately listed rather
    # than omitted: a user who names Meet deserves the honest answer, not a
    # KG-flavoured guess from the generic path.
    "google_meet": "Google Meet",
    "sprinklr": "Sprinklr",
    "superset": "Superset",
    "figma": "Figma",
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

#: At most this many providers' TOOLSETS go into one loop.
#:
#: This used to be 2, and 2 was the literal implementation of "chat only looks
#: at one or two connectors". A question naming three sources had the third
#: silently truncated into an apology. But the fix is NOT simply a bigger
#: number, because the tool loop is SERIAL and its costs are per-iteration:
#:
#:   measured tool-schema + system-block overhead, re-sent EVERY iteration
#:     jira 732 tok · slack 761 · github 569 · confluence 393 · clickup 251 ·
#:     hubspot 227 · fireflies 217 · google_drive 158  (schema only)
#:   the widest real company (5 live-readable adapters) = 19 tools,
#:     ~4,400 tok of schema+system per iteration
#:
#: So unioning five providers' tools into one 6-iteration loop costs ~26k tokens
#: of re-sent overhead AND leaves the model one spare turn after it has spent
#: five reaching each source once. That is not breadth; it is the same coverage
#: paid for twice.
#:
#: The split below is the actual fix. Coverage comes from a PARALLEL keyword
#: sweep (connector_lookup/sweep.py) over every named source; the loop's tools
#: are for DRILL-DOWN on the few most likely to need a follow-up read. Three is
#: chosen because the loop's job is now depth, not coverage, and three toolsets
#: (~1,900 tok) keeps per-iteration overhead below where 2 used to sit relative
#: to its own iteration budget.
MAX_TOOL_PROVIDERS = 3

#: Backwards-compatible alias. Kept because it is the name every existing
#: comment, test and reader knows this concept by; deleting it would make the
#: diff look like the cap vanished rather than split in two.
MAX_PROVIDERS_PER_LOOKUP = MAX_TOOL_PROVIDERS

#: How many named providers the parallel sweep may cover. Eight is every adapter
#: that exists, so in practice this never binds — the real ceiling is what a
#: company has connected, and no company in the database has more than six
#: providers (of which `uploads` is not a live adapter at all).
MAX_SWEEP_PROVIDERS = 8

#: Wall-clock the sweep may draw for priming, CARVED OUT of answer.py's existing
#: WALL_CLOCK_BUDGET_S rather than added to it. This is the whole reason breadth
#: is affordable: the user-facing worst case for a wide lookup is unchanged from
#: what a two-provider lookup could already cost, because the sweep spends part
#: of a budget the loop was already allowed to spend.
SWEEP_PRIME_BUDGET_S = 8.0

#: Ceiling on the primed digest, TIGHTER than the sweep's own 12k default.
#:
#: The digest rides the user turn, and the user turn is re-sent on every loop
#: iteration — so unlike the chat path, where the block is paid for once, here
#: its cost is multiplied by the iteration budget. At 10 iterations a 12k-char
#: block is ~30k tokens of repetition. 6k halves that while still carrying
#: several results per swept source, and `cap_text` marks the truncation so the
#: model never reads a trimmed digest as a complete one.
PRIME_CHARS = 6_000


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


def _sweepable(provider: str) -> bool:
    """Can the parallel sweep cover this provider if it loses its tool slot?

    Imported lazily and fail-safe: if the sweep module can't be reached we
    report False, which pushes every source toward a tool slot — the
    pre-existing behaviour, and the safe direction to be wrong in.
    """
    try:
        from app.connector_lookup import sweep as connector_sweep

        return connector_sweep.can_sweep(provider)
    except Exception:  # noqa: BLE001
        logger.warning("connector-lookup: sweep capability check failed", exc_info=True)
        return False


def _tool_count(provider: str) -> int:
    """How many tools this adapter offers — the drill-down richness proxy used
    to rank tool slots. Derived from the adapter itself so it stays true as
    adapters gain tools, rather than from a hand-kept preference list that would
    silently rot. Unresolvable adapter → 0, which ranks it last."""
    try:
        adapter = provider_for(provider)
        return len(adapter.tools()) if adapter is not None else 0
    except Exception:  # noqa: BLE001
        logger.warning(
            "connector-lookup: tool count failed for %s", provider, exc_info=True
        )
        return 0


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
    # WHICH named sources get the scarce tool slots. Three keys, in order:
    #   1. connected first — a slot spent on a disconnected source reads nothing;
    #   2. then sources the SWEEP CANNOT reach (Google Drive, live Fireflies).
    #      Losing a slot makes those unreachable entirely, while a sweepable
    #      source is still covered by the parallel probe below. Without this key
    #      the split was alphabetical, so whether a source got covered at all
    #      came down to its name — Drive dropped and HubSpot kept, for no reason
    #      anyone could defend;
    #   3. then RICHEST DRILL-DOWN FIRST, measured by how many tools the adapter
    #      offers. The loop's remaining job is depth, and a slot is worth most
    #      where a follow-up read can do something the keyword probe cannot:
    #      Jira (4 tools, including the propose→confirm card) and Confluence
    #      (4, including full page bodies) earn a slot over HubSpot or ClickUp
    #      (2 each, which the probe already summarises well). Alphabetical order
    #      was quietly dropping Jira, the richest source of the four;
    #   4. name, purely so the choice is deterministic and testable.
    connected = set(connected_providers(enterprise_id))
    supported.sort(
        key=lambda p: (p not in connected, _sweepable(p), -_tool_count(p), p)
    )
    chosen = supported[:MAX_TOOL_PROVIDERS]
    providers = [p for p in (provider_for(name) for name in chosen) if p is not None]
    if not providers:
        return connector_answer.plain_payload(
            not_supported_message(sorted(hints), enterprise_id),
            skill_action="Connector lookup",
        )

    # BREADTH. Sources the question named that did not fit the tool cap are no
    # longer written off with an apology — they get the parallel keyword probe
    # and their results are primed into the loop, so the answer really does draw
    # on every named source. Only the DEPTH (a follow-up read) is rationed.
    #
    # Deliberately skipped when everything fits: a one- or two-provider lookup
    # composes byte-identically to before this change, which keeps the whole
    # common case — and the Jira shim's verbatim prompt — off the new path.
    overflow = [p for p in supported[MAX_TOOL_PROVIDERS:MAX_SWEEP_PROVIDERS]
                if p in connected]
    primed = ""
    swept_keys: set[str] = set()
    if overflow:
        try:
            from app.connector_lookup import sweep as connector_sweep

            result = connector_sweep.sweep(
                enterprise_id, question,
                budget_s=SWEEP_PRIME_BUDGET_S,
                only=set(overflow),
                # The user NAMED these sources. One topic word is enough to be
                # worth a probe here; the default floor exists to stop the
                # source-agnostic path fanning out on "make it shorter", which
                # is not a risk when three tools were spelled out.
                min_terms=1,
            )
            from app.connector_lookup.base import cap_text

            primed = cap_text(result.render(), limit=PRIME_CHARS)
            swept_keys = result.covered_providers()
        except Exception:  # noqa: BLE001 — breadth degrades, it never breaks chat
            logger.exception(
                "connector-lookup: priming sweep failed for %s", enterprise_id
            )

    # Still genuinely uncovered: sources with no adapter, plus any overflow the
    # sweep could not read. A source the sweep DID read is removed from this list
    # — it was covered, just not with a drill-down tool — because telling the
    # model it never checked something it is holding results from is how an
    # answer contradicts its own evidence.
    covered = {p.provider for p in providers} | swept_keys
    unavailable = [display_name(h) for h in sorted(hints) if h not in covered]
    return connector_answer.answer(
        enterprise_id=enterprise_id,
        question=question,
        history=history,
        providers=providers,
        unavailable_names=unavailable,
        include_knowledge_graph=include_knowledge_graph,
        primed_context=primed,
        # The loop's own budget shrinks by whatever the sweep just spent, so a
        # wide lookup's worst case equals a narrow one's instead of stacking.
        budget_penalty_s=SWEEP_PRIME_BUDGET_S if overflow else 0.0,
    )

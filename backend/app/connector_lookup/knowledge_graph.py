"""The knowledge graph as a tool inside a connector lookup.

The lookup path reads live TOOLS and nothing else. That is exactly right for
"what did Ada say in #product-eng", and wrong for a DOCUMENT question — the
shape that now routes here without naming a source at all ("what does our
onboarding spec say?"). A document question has two plausible homes and the
user neither knows nor cares which: the page itself, live in the wiki, and the
SIGNALS the extractor already lifted out of every synced source into the graph.

Answering from only one of them is how this surface failed. Reported 2026-08-03
with Confluence connected and syncing: the question never reached the wiki, was
answered entirely from KG signals belonging to an unrelated research report, and
then closed by advising the user to connect the Confluence page it should have
read. Fixing the routing alone would have bought the mirror-image failure —
quoting the wiki while staying silent about what the graph already knows — so
the model is handed both readers and told to attribute each fact to the one it
came from.

Not a connector. It opens no session, holds no OAuth, and can never be "not
connected"; it rides the tool-loop plumbing purely so the model can reach for it
the same way it reaches for confluence_search. Retrieval is tenant-scoped by
`enterprise_id` and read-only, and every failure degrades to a readable string
rather than an exception — a chat answer degrades, it does not error.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TOOL_NAME = "sprntly_knowledge_graph"

KG_TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Search Sprntly's own knowledge graph — the signals, themes, decisions, "
        "hypotheses and outcomes already extracted from EVERY source this "
        "workspace has synced (connectors, uploaded files, calls, generated "
        "reports). Complements a live source read rather than replacing it: the "
        "graph holds what the company has already concluded and where it came "
        "from, while a live read holds what a document says right now. Provide "
        "`query` describing what you are looking for, in the user's own terms."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to look for, phrased as the user phrased it.",
            },
        },
        "required": ["query"],
    },
}

TOOLS = [KG_TOOL]

SYSTEM = (
    "- sprntly_knowledge_graph: Sprntly's accumulated knowledge — signals, "
    "themes, decisions and hypotheses extracted from every source this "
    "workspace has ever synced, including sources not connected as live tools.\n"
    "\n"
    "You are holding TWO KINDS of reader and they answer different questions.\n"
    "A live source read (the tools above) tells you what a document SAYS RIGHT "
    "NOW — its actual wording, its current version. The knowledge graph tells "
    "you what has been EXTRACTED and CONCLUDED across everything, which is "
    "broader but is a paraphrase, can be stale, and never quotes a page.\n"
    "\n"
    "For a question about a specific document, read the document. Use the graph "
    "to surround that answer with what the company already decided, or when the "
    "live read finds nothing.\n"
    "A GRAPH HIT IS A LEAD, NOT AN ANSWER. Whenever this tool returns signals "
    "extracted from a source you also hold a live tool for, read that source "
    "live before you answer — the graph is a sync-time snapshot and says "
    "nothing about edits made since. Where the two disagree the live read is "
    "correct and you should say the extract was stale. Reporting a signal as "
    "the current contents of a document you did not open is the single worst "
    "thing you can do on this path.\n"
    "NEVER present a graph signal as the contents of a document. If the graph "
    "has relevant material and the live read found the page, give both and say "
    "which is which. If the live read found nothing but the graph has "
    "something, say plainly that the document was not found in the source you "
    "searched, then give what the graph holds and name it as extracted context "
    "rather than as the document.\n"
    "Do not tell the user to connect or upload a source that is already "
    "connected — the sources listed in this prompt are live and you just read "
    "them."
)

#: Signals rendered into one tool result. The bundle is already ranked and
#: token-capped by retrieve_context; this is a second, tool-shaped bound so one
#: KG call cannot crowd out the live reads in a shared context window.
MAX_CHARS = 6000

EMPTY = (
    "(the knowledge graph has nothing on this query. This is a real empty "
    "result, not an error — but it only covers what has been EXTRACTED, so a "
    "live source read may still find the document.)"
)


def search(enterprise_id: str, query: str) -> str:
    """Rendered KG context for `query`, or an honest note when there is none.

    Mirrors ask_runner._retrieve_kg_bundle: best-effort, tenant-scoped, and any
    failure (no pgvector, partial KG, unavailable DB) collapses to a message the
    model can act on instead of an exception.
    """
    if not enterprise_id:
        return EMPTY
    try:
        from app.graph.facade import GraphFacade
        from app.graph.retrieval import render_context_section, retrieve_context

        bundle = retrieve_context(GraphFacade(), enterprise_id, query)
    except Exception:  # noqa: BLE001 — the KG must never break a lookup
        logger.exception(
            "connector-lookup: KG retrieval failed for enterprise=%s", enterprise_id
        )
        return (
            "(the knowledge graph could not be read just now. This is NOT a "
            "no-results answer — do not tell the user the graph holds nothing.)"
        )
    if not bundle or bundle.get("empty"):
        return EMPTY
    try:
        rendered = render_context_section(bundle)
    except Exception:  # noqa: BLE001
        logger.exception("connector-lookup: KG render failed for %s", enterprise_id)
        return EMPTY
    text = (rendered or "").strip()
    if not text:
        return EMPTY
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n(…knowledge-graph context truncated.)"
    return (
        "Sprntly knowledge graph (extracted signals, NOT document text):\n"
        + text
        + _verify_live_directive(bundle)
    )


def _signal_sources(bundle: dict) -> list[str]:
    """Connector keys the returned signals were extracted FROM, deduped and
    ordered. Best-effort: an unfamiliar shape yields [] rather than raising."""
    seen: list[str] = []
    try:
        signals = bundle.get("signals") or []
    except AttributeError:
        return []
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        source = (sig.get("source_type") or "").strip().lower()
        if source and source not in seen:
            seen.append(source)
    return seen


def _verify_live_directive(bundle: dict) -> str:
    """Tell the model to go and check, live, whatever the graph just claimed.

    Every signal here was extracted at SYNC time. A page edited since then is
    described by this tool exactly as it was, with no marker saying so — which
    is how "brief me on the company documents" reported three populated
    Confluence pages as empty: the sync had run while they were still blank
    templates, and the graph answered faithfully from that snapshot.

    So a graph hit is a lead, never a conclusion: it names what to open. The
    sources are listed explicitly rather than left implicit, because the model
    cannot otherwise tell which of the tools above corresponds to a signal's
    provenance.
    """
    sources = _signal_sources(bundle)
    named = ", ".join(sources) if sources else "the source these came from"
    return (
        "\n\n(These signals were extracted AT SYNC TIME and may be out of date — "
        f"they came from: {named}. If any of those is available as a live tool "
        "in this conversation, you MUST read it live before answering rather "
        "than reporting the extract as the current state. Where the live read "
        "and these signals disagree, the LIVE READ WINS and you should say the "
        "graph was stale. Only fall back to reporting a signal on its own if no "
        "live tool covers its source.)"
    )


def dispatch(enterprise_id: str, name: str, inp: dict) -> str:
    query = (inp.get("query") or "").strip()
    if not query:
        return f"({TOOL_NAME}: 'query' is required)"
    return search(enterprise_id, query)

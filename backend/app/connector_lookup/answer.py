"""The connector-lookup answer loop — one bounded, tenant-bound tool loop over
whichever connectors the question is about.

Generalized from app/jira_lookup.answer (which is now a thin shim over this):
open a session per requested provider, offer the union of the CONNECTED
providers' tools, run the shared bounded tool loop, and return an Ask-shaped
payload. Every deterministic degradation the Jira path had is preserved and now
applies to every connector:

- nothing connected  → a connect message that also says what IS connected, and
  the tool loop is never called (no tokens burned, nothing hallucinated);
- upstream failure   → a retry message, no stack trace;
- empty answer       → an honest "couldn't find it there".

Caps live here rather than in the adapters (see base.py): per-tool-result
truncation with an honest marker, a wall-clock budget across the whole loop, and
the loop's own iteration bound. An adapter that forgets them still gets them.
"""
from __future__ import annotations

import logging
import time

from app.connector_lookup.base import (
    DEFAULT_RESULT_CHARS,
    LookupProvider,
    LookupSession,
    cap_text,
)
from app.llm import run_tool_loop as _default_run_loop
from app.prompt_history import clamp_turn_text

logger = logging.getLogger(__name__)

ANSWER_MODEL = "claude-sonnet-4-6"
MAX_ITERS = 6
MAX_TOKENS = 4000
SKILL_SOURCE = "connector-lookup"

#: Extra loop iterations granted per provider beyond the first. Six iterations
#: is ample for one source (search, read, done) and thin for three: the model
#: spends one turn reaching each source before it can follow up on any of them,
#: so a flat bound quietly converts breadth into shallower coverage — it looks
#: like the model chose not to dig, when it simply ran out of turns.
#:
#: Coverage itself no longer comes out of this budget (the priming sweep in
#: registry.answer_for_hints reaches the overflow sources in parallel, outside
#: the loop), so +2 per extra provider buys a search AND a read for each
#: toolset the loop actually holds, rather than paying for coverage twice.
ITERS_PER_EXTRA_PROVIDER = 2

#: Hard ceiling regardless of provider count. At MAX_TOOL_PROVIDERS=3 the
#: formula yields 10, so this binds only if that cap is ever raised — it is the
#: backstop that stops a future cap change silently buying an expensive loop.
MAX_ITERS_CEILING = 10

#: Wall-clock budget for the whole lookup, across every tool call. The iteration
#: bound alone doesn't bound time (6 slow calls × 15s each is a minute and a
#: half of a user staring at a spinner), so once the budget is spent the tools
#: stop firing and the model is told to answer from what it already has.
WALL_CLOCK_BUDGET_S = 75

#: The chart contract, shared verbatim with the plain-ask path.
#:
#: ONE SCHEMA, TWO PROMPTS, on purpose. `InlineChart.tsx` parses this block and
#: refuses anything that does not match, so a second dialect here would render
#: as a fence full of JSON in the middle of an answer. The kinds and the field
#: names belong to the RENDERER, not to this file — `ASK_SYSTEM` in
#: app/prompts.py carries the same block plus the fuller form heuristic.
_CHART_CONTRACT = (
    "\nEmbed a chart as a fenced code block with language `chart` (no other "
    "language) and a JSON body matching exactly:\n\n"
    "```chart\n"
    '{\n'
    '  "kind": "bar" | "line" | "pie" | "donut" | "stat" | "gauge",\n'
    '  "title": "Complete-sentence takeaway as the title",\n'
    '  "subtitle": "optional source line",\n'
    '  "data": [{"label": "string", "value": <number-or-string>}]\n'
    '}\n'
    "```\n\n"
    "Which kind: `bar` to compare things, `line` for change over time, `pie` "
    "(or `donut`) for a share of one whole, `stat` for 2-4 headline numbers, "
    "`gauge` for one number against a target. Every value must come from a "
    "tool result you actually read — never fill a chart out to make it look "
    "complete. Always close the fence with ``` on its own line.\n"
)


#: How to PRESENT what the tools returned — appended last by `_build_system`,
#: after every adapter block, because that is the only position where it wins.
_PRESENTATION = (
    "\n## Presenting the answer\n"
    "A COUNT PER THING IS A CHART, not a table. This path answers the most "
    "chart-shaped questions in the product — tickets per status, issues per "
    "assignee, messages per channel, commits per author — so a breakdown gets "
    "a chart and the prose says what it means. Keep a markdown table for "
    "cross-cuts with two dimensions (status BY assignee) that one chart cannot "
    "carry, and for lists that are not counts at all.\n"
    + _CHART_CONTRACT
)


_SYSTEM_HEAD = (
    "You are a product-management assistant with LIVE, read-only access to the "
    "tools the user has connected to Sprntly. Answer by FETCHING the real data "
    "the question refers to — never guess a message, ticket, file, record or "
    "commit you have not read.\n\n"
    "Rules that hold for every source:\n"
    "- Call a tool before stating anything factual about the user's data.\n"
    "- If a search returns nothing, say so plainly. Do not invent items.\n"
    "- Cite what you read (channel + date, issue key, file name, SHA, record "
    "name) so the user can check it.\n"
    "- When a tool result says it was truncated or rate-limited, say your answer "
    "covers part of the data — never imply you read all of it.\n"
    "- When several sources are available, say WHICH source each fact came "
    "from.\n"
    "- Be concise and concrete.\n"
    "Follow-ups often never repeat what they are about (\"who said that?\", "
    "\"and the rest of it\"). Resolve the reference from the conversation above "
    "rather than searching blind.\n"
)


#: Turns of history folded into a lookup prompt — 10 turns ≈ 5 exchanges, wide
#: enough that the channel/issue/file a follow-up points back at is still in view.
_HISTORY_TURNS = 10


def _render_history(history: list[dict] | None) -> str:
    """Recent turns as plain text, each one clamped.

    THE fold site for every connector adapter — Jira (via the jira_lookup shim),
    Slack, ClickUp, Fireflies, GitHub, HubSpot, Drive — which is why the
    per-turn clamp belongs here rather than in each caller. `clamp_turn_text`
    (app/prompt_history.py) strips base64 `data:` payloads, reduces an HTML
    report turn to its narrative and caps the rest: a chart-bearing report answer
    persisted verbatim as a conversation turn is ~1 MB of data URI, which
    replayed into the next prompt in the thread is a non-retryable 400. One site,
    every adapter, including ones added later.
    """
    if not history:
        return ""
    recent = history[-_HISTORY_TURNS:]
    rows = [
        f"{t.get('role', 'user').capitalize()}: {clamp_turn_text(t.get('content', ''))}"
        for t in recent
    ]
    return "Conversation so far:\n" + "\n".join(rows) + "\n\n"


def plain_payload(
    answer_text: str,
    *,
    skill_source: str = SKILL_SOURCE,
    skill_action: str | None = None,
    confidence: float = 0.0,
) -> dict:
    """Ask-shaped payload for the non-LLM branches (not connected, unsupported
    source), tagged so the UI attributes it to the lookup path."""
    return {
        "answer": answer_text, "key_points": [], "citations": [],
        "confidence": confidence, "unanswered": "",
        "_skill": None, "_skill_action": skill_action, "_skill_source": skill_source,
    }


def _open_sessions(
    providers: list[LookupProvider], enterprise_id: str
) -> tuple[list[tuple[LookupProvider, LookupSession]], list[LookupProvider]]:
    """Open a session per provider. Returns (connected, missing).

    A provider whose open_session raises is treated as NOT connected — the
    honest connect/reconnect copy is a better answer than a 500, and the
    exception is logged for us.
    """
    connected: list[tuple[LookupProvider, LookupSession]] = []
    missing: list[LookupProvider] = []
    for provider in providers:
        try:
            session = provider.open_session(enterprise_id)
        except Exception:  # noqa: BLE001 — degrade, never break the chat
            logger.exception(
                "connector-lookup: %s open_session failed for %s",
                provider.provider, enterprise_id,
            )
            session = None
        if session is None:
            missing.append(provider)
        else:
            connected.append((provider, session))
    return connected, missing


def connected_sources_sentence(enterprise_id: str) -> str:
    """"Connected right now: Jira, Slack." — or "" when nothing is connected.

    Included in the not-connected copy so the user is told what they CAN ask
    about instead of only what they can't.
    """
    from app.connector_lookup.registry import connected_display_names

    names = connected_display_names(enterprise_id)
    if not names:
        return ""
    return " Connected right now: " + ", ".join(names) + "."


def not_connected_message(
    providers: list[LookupProvider], enterprise_id: str
) -> str:
    """Deterministic copy for "you asked about X and X isn't connected"."""
    names = " or ".join(p.display_name for p in providers) or "that tool"
    return (
        f"I can read your {names} live and answer from what's actually there — "
        f"but {names} isn't connected yet (or its access needs refreshing). "
        f"Connect it in Settings → Connectors and ask me again."
        + connected_sources_sentence(enterprise_id)
    )


def kg_module():
    """The knowledge-graph toolset, imported lazily.

    Same reason the registry defers its adapter imports: this module is imported
    on every intercepted question, and the KG package pulls in the graph facade
    and embeddings. Also the seam tests patch.
    """
    from app.connector_lookup import knowledge_graph

    return knowledge_graph


def _unavailable_display_names(
    missing: list[LookupProvider], extra: list[str] | None
) -> list[str]:
    """Display names of sources the question referred to but we could not open —
    providers whose session failed to open, plus names the caller already knows
    have no adapter (registry passes those in)."""
    names = [p.display_name for p in missing]
    for name in extra or []:
        if name not in names:
            names.append(name)
    return names


def _build_system(
    connected: list[tuple[LookupProvider, LookupSession]],
    unavailable: list[str] | None = None,
    knowledge_graph: bool = False,
    primed: bool = False,
) -> str:
    """Framework rules + each connected adapter's own block + any honest mode
    notes the session recorded (e.g. Slack's search-vs-channel-read mode), plus
    the sources this answer could NOT reach.

    The unavailable list matters for a question like "check Slack and HubSpot"
    where only Slack is connected: answering purely from Slack, with no mention of
    the half that was never read, reads as a complete answer to both.
    """
    parts = [_SYSTEM_HEAD]
    for provider, session in connected:
        parts.append(f"\n## {provider.display_name}\n{provider.system_block()}")
        for note in session.notes:
            parts.append(f"Note about {provider.display_name}: {note}")
    if unavailable:
        parts.append(
            "\n## Not available for this question\n"
            + ", ".join(unavailable)
            + " — the question referred to this, but it is not connected (or "
            "Sprntly cannot read it live yet), so you did NOT check it. Say so "
            "explicitly in your answer; do not let an answer from the other "
            "source(s) imply you covered this one."
        )
    if primed:
        parts.append(
            "\n## Already gathered for you\n"
            "The user's turn begins with a LIVE CROSS-SOURCE SWEEP: sources the "
            "question named that you have NO tool for here, already searched for "
            "you in parallel just before this loop started. Treat it as real, "
            "current data and cite it by source exactly as you would a tool "
            "result — those sources ARE covered, so never say you did not check "
            "them.\n"
            "Two limits it states about itself and you must respect: it is a "
            "KEYWORD probe, so a source listed as returning nothing means "
            "'nothing matching those words', never 'it did not happen'; and any "
            "source it names as uncovered really was not read. You cannot drill "
            "further into a swept source — if the answer needs more from one, "
            "say so and invite the user to ask about that source directly."
        )
    if knowledge_graph:
        from app.connector_lookup import knowledge_graph as kg

        parts.append(f"\n## Sprntly knowledge graph\n{kg.SYSTEM}")
    if len(connected) > 1:
        parts.append(
            "\nSeveral sources are connected. Prefer the one the question names; "
            "when a fact could come from either, attribute it explicitly."
        )
    # LAST, and that position is the fix. This started life as a bullet inside
    # `_SYSTEM_HEAD` and did nothing: the head is the FIRST of a dozen parts,
    # and every adapter block after it describes how to present that source's
    # data. "Tickets per status" and "issues per assignee" both came back as
    # markdown tables from a prompt that had already been told to draw a chart —
    # the adapter simply spoke more recently and more specifically. A rule about
    # the SHAPE OF THE ANSWER belongs after everything about where the data came
    # from, so it is the last thing read before the model writes.
    parts.append(_PRESENTATION)
    return "\n".join(parts)


def _make_dispatch(
    connected: list[tuple[LookupProvider, LookupSession]],
    deadline: float,
    *,
    enterprise_id: str | None = None,
    knowledge_graph: bool = False,
):
    """One (name, input) -> str dispatcher over every connected adapter.

    Framework guarantees applied on top of whatever the adapter does:
    tool→provider routing (so no adapter can be asked to run another's tool), a
    per-result char cap with an honest truncation marker, the wall-clock budget,
    and a readable error string instead of an exception (run_tool_loop guards
    too; this keeps the message ours).

    The knowledge-graph tool is dispatched here too when enabled. It is not a
    provider — no session, no OAuth — so it sits beside the owner map rather than
    inside it, and it is deliberately subject to the SAME wall-clock deadline: a
    slow KG read late in a loop must not be what makes the user wait.
    """
    owner: dict[str, tuple[LookupProvider, LookupSession]] = {}
    for provider, session in connected:
        for tool in provider.tools():
            owner[tool["name"]] = (provider, session)

    def dispatch(name: str, inp: dict) -> str:
        inp = inp if isinstance(inp, dict) else {}
        if knowledge_graph and name == kg_module().TOOL_NAME:
            if time.monotonic() > deadline:
                return (
                    "(lookup time budget reached — no more fetches. Answer from "
                    "what you already read, and say it may be incomplete.)"
                )
            try:
                out = kg_module().dispatch(enterprise_id or "", name, inp)
            except Exception:  # noqa: BLE001 — a KG read must not break the loop
                logger.warning(
                    "connector-lookup: KG tool dispatch failed for %s",
                    enterprise_id, exc_info=True,
                )
                return (
                    "(the knowledge graph could not be read just now. This is NOT "
                    "a no-results answer — do not tell the user the graph holds "
                    "nothing.)"
                )
            return cap_text(out, limit=DEFAULT_RESULT_CHARS)
        pair = owner.get(name)
        if pair is None:
            return f"(unknown tool {name})"
        provider, session = pair
        if time.monotonic() > deadline:
            return (
                "(lookup time budget reached — no more fetches. Answer from what "
                "you already read, and say it may be incomplete.)"
            )
        try:
            out = provider.dispatch(session, name, inp)
        except Exception as exc:  # noqa: BLE001 — surface to the model, readably
            logger.warning(
                "connector-lookup: %s tool %s failed", provider.provider, name,
                exc_info=True,
            )
            return f"({provider.display_name} {name} failed: {exc})"
        cap = getattr(provider, "result_char_cap", DEFAULT_RESULT_CHARS)
        return cap_text(str(out), limit=cap)

    return dispatch


def answer(
    *,
    enterprise_id: str,
    question: str,
    history: list[dict] | None = None,
    providers: list[LookupProvider],
    skill_source: str = SKILL_SOURCE,
    skill_action: str | None = None,
    not_connected_text: str | None = None,
    empty_text: str | None = None,
    exception_text: str | None = None,
    system_text: str | None = None,
    unavailable_names: list[str] | None = None,
    include_knowledge_graph: bool = False,
    primed_context: str = "",
    budget_penalty_s: float = 0.0,
    run_loop=None,
    log=None,
) -> dict:
    """Run one ad-hoc lookup over `providers` and return an Ask-shaped payload.

    `run_loop` and `log` are injection points: the Jira shim passes its own
    module-level `run_tool_loop` and its own `_log(enterprise_id, meta)` so that
    path keeps its exact decision-log contract (and its long-standing test patch
    surface), and tests can drive the loop deterministically.
    `not_connected_text` / `empty_text` / `exception_text` / `system_text` let an
    adapter keep verbatim copy that predates the framework. The Jira path passes
    ALL FOUR: its three deterministic branches and its long-tuned system prompt
    are word-for-word what they were before this refactor, because generic copy
    about "the channel, ticket, file or person" is wrong for a Jira user who
    needs to be told to double-check the issue key.

    `unavailable_names` are sources the question referred to that could NOT be
    opened (not connected, or no adapter). They go into the system block so the
    answer says what it did not cover, instead of quietly answering from half the
    sources and sounding complete.

    `primed_context` is a cross-source digest ALREADY GATHERED before the loop
    started — the parallel keyword sweep registry.answer_for_hints runs over the
    named sources that did not fit `MAX_TOOL_PROVIDERS`. It rides the user turn
    beside the question so the model opens the loop already holding every named
    source's headline results, and spends its iterations drilling rather than
    reaching. An adapter passing verbatim `system_text` (Jira) never receives it,
    for the same reason it never receives the KG tool: its prompt predates the
    concept and would not tell the model what the block is.

    `budget_penalty_s` shortens the wall clock by however long the caller already
    spent gathering `primed_context`. Breadth therefore costs the user nothing in
    worst-case latency — a wide lookup and a narrow one share one ceiling instead
    of stacking two.

    `include_knowledge_graph` adds Sprntly's own extracted knowledge as a further
    tool (connector_lookup/knowledge_graph.py). OFF by default, so a caller that
    leaves it False is byte-identical to before. When ON it does two things beyond
    offering the tool: (1) it composes the KG system block onto a verbatim
    `system_text` (so an adapter with a tuned prompt — Jira — is TOLD the tool
    exists before it is handed one); and (2) it turns the nothing-connected branch
    into a KG-ONLY tool loop instead of the connect copy, because the connector
    sync keeps that same data live in the graph — so a tracker question answers
    from what is actually synced rather than false-denying. The document-intent
    path and the tracker/Jira paths turn it on; every other caller leaves it off.

    Never raises — a chat answer degrades, it does not error.
    """
    loop = run_loop or _default_run_loop
    connected, missing = _open_sessions(providers, enterprise_id)
    action = skill_action or (
        " + ".join(p.display_name for p in (
            [p for p, _ in connected] or providers
        )) + " lookup"
    )
    # Nothing live to read. With the knowledge graph enabled, that is no longer a
    # dead end: the connector sync keeps the same data fresh in the graph, so we
    # fall through to a KG-only tool loop (built below with connected == []) rather
    # than returning the connect copy. With the flag OFF — every caller but the
    # tracker/Jira paths — this is byte-identical to the pre-existing short-circuit.
    if not connected and not include_knowledge_graph:
        return plain_payload(
            not_connected_text or not_connected_message(missing or providers, enterprise_id),
            skill_source=skill_source, skill_action=action,
        )

    meta: dict = {}
    # Whatever the caller already spent priming comes OUT of this budget, not on
    # top of it, so a wide lookup's ceiling equals a narrow one's. Floored well
    # above zero: a penalty larger than the budget must still leave the loop
    # enough time to answer from what priming already found.
    # The floor is `min(budget, 15)`, not a flat 15: it guarantees a penalty can
    # never starve the loop below 15s, while never GRANTING more than the
    # configured budget. A flat floor would silently override a deliberately
    # tiny WALL_CLOCK_BUDGET_S — which is exactly how the existing
    # expired-deadline test drives this code.
    deadline = time.monotonic() + max(
        WALL_CLOCK_BUDGET_S - max(budget_penalty_s, 0.0),
        min(WALL_CLOCK_BUDGET_S, 15.0),
    )
    tools: list[dict] = []
    for provider, _session in connected:
        tools.extend(provider.tools())
    # The knowledge graph is offered whenever the caller asks for it — including
    # alongside a verbatim `system_text` (Jira). A prompt that predates the tool
    # cannot mention it, so when `system_text` is passed we also append the KG
    # system block below, so the model is told the tool exists before it is
    # handed one.
    kg_on = include_knowledge_graph
    if kg_on:
        tools.extend(kg_module().TOOLS)
    # A verbatim system prompt cannot explain a primed cross-source digest it was
    # written before, so Jira still never receives one (unchanged).
    primed = primed_context if (primed_context and not system_text) else ""
    if system_text:
        system_prompt = system_text
        if kg_on:
            system_prompt = (
                system_text + "\n## Sprntly knowledge graph\n" + kg_module().SYSTEM
            )
        # THE VERBATIM PATH NEEDS THIS TOO, and finding out why took two rebuilds.
        # An adapter with a tuned prompt (Jira, today) never touches
        # `_build_system`, so a presentation rule added there reaches only the
        # multi-source path. "What share of our open tickets sits in each
        # status?" is answered HERE — it came back as a markdown table from an
        # image that already carried the rule, because the rule was in the other
        # branch of this `if`. Appended last for the same reason it is last over
        # there: it is about the shape of the ANSWER, so it belongs after
        # everything about where the data came from.
        system_prompt += "\n" + _PRESENTATION
    else:
        system_prompt = _build_system(
            connected,
            # A KG-only fallback (connected == []) answers purely from the graph;
            # naming every requested source as "not available" there would push
            # the model toward the very connect-a-source deflection this path
            # exists to avoid, so the unavailable note is added only when a live
            # source WAS read.
            unavailable=(
                _unavailable_display_names(missing, unavailable_names)
                if connected else None
            ),
            knowledge_graph=kg_on,
            primed=bool(primed),
        )
    max_iters = min(
        MAX_ITERS + ITERS_PER_EXTRA_PROVIDER * max(len(connected) - 1, 0),
        MAX_ITERS_CEILING,
    )
    try:
        text = loop(
            system=system_prompt,
            user=(
                _render_history(history)
                + (f"{primed}\n\n---\n\n" if primed else "")
                + f"Question: {question}"
            ),
            tools=tools,
            dispatch=_make_dispatch(
                connected, deadline,
                enterprise_id=enterprise_id, knowledge_graph=kg_on,
            ),
            model=ANSWER_MODEL,
            max_tokens=MAX_TOKENS,
            max_iters=max_iters,
            meta_out=meta,
        )
    except Exception:  # noqa: BLE001 — never break the chat
        # On the KG-only fallback `connected` is empty; name the graph so the
        # degraded copy still reads sensibly.
        names = " / ".join(p.display_name for p, _ in connected) or "the knowledge graph"
        logger.exception(
            "connector-lookup: tool loop failed for %s (%s)", enterprise_id, names
        )
        return plain_payload(
            exception_text or (
                f"I couldn't reach {names} to look that up just now. Please retry "
                "in a moment — if it keeps failing, that connection may need "
                "reconnecting in Settings → Connectors."
            ),
            skill_source=skill_source, skill_action=action,
        )

    if log is not None:
        log(enterprise_id, meta)
    else:
        _log(enterprise_id, meta, [p.provider for p, _ in connected])
    if not text.strip():
        names = " / ".join(p.display_name for p, _ in connected) or "the knowledge graph"
        return plain_payload(
            empty_text or (
                f"I looked in {names} but couldn't find what your question refers "
                "to. Try naming the channel, ticket, file or person more exactly."
            ),
            skill_source=skill_source, skill_action=action,
        )
    payload = {
        "answer": text, "key_points": [], "citations": [],
        "confidence": 0.6, "unanswered": "",
        "_skill": None, "_skill_action": action, "_skill_source": skill_source,
    }
    # Adapter-contributed payload keys — e.g. the Jira adapter's pending change,
    # which the UI renders as a confirm card. Empty extras are dropped so the
    # payload shape is unchanged when nothing was proposed.
    for _provider, session in connected:
        for key, value in session.extras.items():
            if value:
                payload[key] = value
    return payload


def _log(
    enterprise_id: str,
    meta: dict,
    providers: list[str],
    decision_type: str = "connector_lookup",
    prompt_version: str = "qa-connector-lookup-v1",
) -> None:
    """Best-effort decision-log row (the tool-loop path bypasses the gateway's
    own logging, like jira_lookup._log and _answer_with_script in qa_agent)."""
    try:
        from app.graph.decision_log import log_agent_decision

        log_agent_decision(
            enterprise_id=enterprise_id,
            agent="qa",
            decision_type=decision_type,
            factors={
                "providers": providers,
                **{k: meta.get(k) for k in ("input_tokens", "output_tokens") if k in meta},
            },
            model=meta.get("model"),
            prompt_version=prompt_version,
        )
    except Exception:  # noqa: BLE001
        logger.exception("connector-lookup decision-log write failed")

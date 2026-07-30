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

logger = logging.getLogger(__name__)

ANSWER_MODEL = "claude-sonnet-4-6"
MAX_ITERS = 6
MAX_TOKENS = 4000
SKILL_SOURCE = "connector-lookup"

#: Wall-clock budget for the whole lookup, across every tool call. The iteration
#: bound alone doesn't bound time (6 slow calls × 15s each is a minute and a
#: half of a user staring at a spinner), so once the budget is spent the tools
#: stop firing and the model is told to answer from what it already has.
WALL_CLOCK_BUDGET_S = 75

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
    "- Be concise and concrete.\n\n"
    "Follow-ups often never repeat what they are about (\"who said that?\", "
    "\"and the rest of it\"). Resolve the reference from the conversation above "
    "rather than searching blind.\n"
)


def _render_history(history: list[dict] | None) -> str:
    """Recent turns as plain text (10 turns ≈ 5 exchanges) — wide enough that the
    channel/issue/file a follow-up points back at is still in view."""
    if not history:
        return ""
    recent = history[-10:]
    rows = [f"{t.get('role', 'user').capitalize()}: {t.get('content', '')}" for t in recent]
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


def _build_system(
    connected: list[tuple[LookupProvider, LookupSession]]
) -> str:
    """Framework rules + each connected adapter's own block + any honest mode
    notes the session recorded (e.g. Slack's search-vs-channel-read mode)."""
    parts = [_SYSTEM_HEAD]
    for provider, session in connected:
        parts.append(f"\n## {provider.display_name}\n{provider.system_block()}")
        for note in session.notes:
            parts.append(f"Note about {provider.display_name}: {note}")
    if len(connected) > 1:
        parts.append(
            "\nSeveral sources are connected. Prefer the one the question names; "
            "when a fact could come from either, attribute it explicitly."
        )
    return "\n".join(parts)


def _make_dispatch(connected: list[tuple[LookupProvider, LookupSession]], deadline: float):
    """One (name, input) -> str dispatcher over every connected adapter.

    Framework guarantees applied on top of whatever the adapter does:
    tool→provider routing (so no adapter can be asked to run another's tool), a
    per-result char cap with an honest truncation marker, the wall-clock budget,
    and a readable error string instead of an exception (run_tool_loop guards
    too; this keeps the message ours).
    """
    owner: dict[str, tuple[LookupProvider, LookupSession]] = {}
    for provider, session in connected:
        for tool in provider.tools():
            owner[tool["name"]] = (provider, session)

    def dispatch(name: str, inp: dict) -> str:
        inp = inp if isinstance(inp, dict) else {}
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
    system_text: str | None = None,
    run_loop=None,
    log=None,
) -> dict:
    """Run one ad-hoc lookup over `providers` and return an Ask-shaped payload.

    `run_loop` and `log` are injection points: the Jira shim passes its own
    module-level `run_tool_loop` and its own `_log(enterprise_id, meta)` so that
    path keeps its exact decision-log contract (and its long-standing test patch
    surface), and tests can drive the loop deterministically.
    `not_connected_text` and `system_text` let an adapter keep verbatim copy that
    predates the framework: the Jira path passes both, so its long-tuned system
    prompt and connect message are unchanged by this refactor.

    Never raises — a chat answer degrades, it does not error.
    """
    loop = run_loop or _default_run_loop
    connected, missing = _open_sessions(providers, enterprise_id)
    action = skill_action or (
        " + ".join(p.display_name for p in (
            [p for p, _ in connected] or providers
        )) + " lookup"
    )
    if not connected:
        return plain_payload(
            not_connected_text or not_connected_message(missing or providers, enterprise_id),
            skill_source=skill_source, skill_action=action,
        )

    meta: dict = {}
    deadline = time.monotonic() + WALL_CLOCK_BUDGET_S
    tools: list[dict] = []
    for provider, _session in connected:
        tools.extend(provider.tools())
    try:
        text = loop(
            system=system_text or _build_system(connected),
            user=_render_history(history) + f"Question: {question}",
            tools=tools,
            dispatch=_make_dispatch(connected, deadline),
            model=ANSWER_MODEL,
            max_tokens=MAX_TOKENS,
            max_iters=MAX_ITERS,
            meta_out=meta,
        )
    except Exception:  # noqa: BLE001 — never break the chat
        names = " / ".join(p.display_name for p, _ in connected)
        logger.exception(
            "connector-lookup: tool loop failed for %s (%s)", enterprise_id, names
        )
        return plain_payload(
            f"I couldn't reach {names} to look that up just now. Please retry in "
            "a moment — if it keeps failing, that connection may need "
            "reconnecting in Settings → Connectors.",
            skill_source=skill_source, skill_action=action,
        )

    if log is not None:
        log(enterprise_id, meta)
    else:
        _log(enterprise_id, meta, [p.provider for p, _ in connected])
    if not text.strip():
        names = " / ".join(p.display_name for p, _ in connected)
        return plain_payload(
            f"I looked in {names} but couldn't find what your question refers "
            "to. Try naming the channel, ticket, file or person more exactly.",
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

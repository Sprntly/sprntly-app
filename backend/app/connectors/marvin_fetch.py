"""Marvin read client for the chat live-lookup path.

Sibling of confluence_fetch / clickup_fetch: opens a tenant-bound session, runs
bounded reads, and renders each result as text for the model. The KG puller
(kg_ingest/pullers/marvin.py) is the OTHER reader — it sweeps the whole
repository on a schedule; this one answers one question at a time, against what
Marvin holds RIGHT NOW rather than against the last sync.

ONE CAPABILITY RESOLVER, NOT TWO
    Marvin publishes no schema for its MCP tools, so both readers have to
    discover them at call time. `resolve_capabilities` and `ToolSpec` are
    imported from the puller rather than reimplemented here: a second copy of
    that heuristic would drift, and the failure mode of drift is chat reading a
    different tool than the sync did and the two disagreeing about the same
    workspace.

WHY A FRESH MCP SESSION PER CALL
    `LookupSession` has no teardown hook — `connector_lookup/answer.py` opens
    adapter sessions and never closes them, because every other adapter's handle
    is a bare token. An initialized MCP conversation is not: the server may have
    allocated a session id for it, and holding one open for the length of a chat
    turn with nothing ever sending the DELETE would leak one per question.

    So each read opens its own `McpSession` inside a `with` block, which
    guarantees teardown at the cost of one extra handshake. The resolved
    capabilities ARE cached on the handle, so `tools/list` is paid once per turn
    rather than once per tool call, and the bound that matters — the framework's
    75s wall-clock budget and `HTTP_TIMEOUT` per request — is unaffected.

WHAT IS NOT READ
    Interview transcripts. Rendering reuses the puller's `_distill_text`, which
    reads only the analysis-bearing fields, so the §6 no-raw-dump contract holds
    on this path exactly as it does on the sync path. A live lookup is not a
    loophole around it.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.connector_lookup.base import HTTP_TIMEOUT, cap_items, cap_text
from app.connectors import marvin_oauth
from app.connectors.mcp_client import (
    McpSession,
    records_from_result,
    text_from_result,
)
from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
    encrypt_token_json,
)
from app.kg_ingest.pullers.marvin import (
    ToolSpec,
    _distill_text,
    _first_str,
    _ID_FIELDS,
    _MAX_TEXT,
    _MAX_TITLE,
    _properties,
    _TIME_FIELDS,
    _TITLE_FIELDS,
    resolve_capabilities,
)

logger = logging.getLogger(__name__)

PROVIDER = marvin_oauth.MARVIN_PROVIDER

#: Rows rendered from one search / listing. Well under the framework's
#: DEFAULT_RESULT_CHARS so a full page of hits plus its header still fits inside
#: one tool result.
RESULT_LIMIT = 15
#: Rows asked of the server. Larger than RESULT_LIMIT so the ordering marker
#: below describes a real ranking rather than an arbitrary page.
PAGE_SIZE = 50
#: Analysis chars rendered for ONE file.
FILE_TEXT_CHARS = 6000
#: Excerpt chars per row in a multi-row result.
EXCERPT_CHARS = 240
#: Refresh an access token this many seconds before it expires. Same margin
#: `connector_probe` uses for Marvin — the tokens live about an hour, so a chat
#: turn that starts inside the margin would otherwise fail mid-answer.
REFRESH_MARGIN_S = 120


class MarvinCapabilityMissing(RuntimeError):
    """This connection's MCP server exposes no tool for a capability.

    NOT an error condition — Marvin ships tool subsets per plan, and an admin
    can disable tools. The adapter turns this into copy that says the capability
    is unavailable, because "we could not look" and "we looked and found
    nothing" are different answers and only one of them is honest.
    """

    def __init__(self, capability: str, detail: str = ""):
        super().__init__(detail or f"no tool for {capability}")
        self.capability = capability
        self.detail = detail


@dataclass
class MarvinSession:
    """Tenant-bound Marvin access for the duration of one answer.

    `caps` is resolved lazily on the first read and cached for the turn.
    `reached` records that at least one tool call actually succeeded, which is
    what the adapter keys its background-sync kickoff off — a turn that never
    got an answer out of Marvin has nothing new to ingest.
    """

    enterprise_id: str
    # repr suppressed: this is the bearer token (see base.LookupSession).
    access_token: str = field(repr=False)
    mcp_url: str
    caps: dict[str, ToolSpec] | None = None
    reached: bool = False
    synced: bool = False


# ── Session ──────────────────────────────────────────────────────────────────


def open_session(enterprise_id: str) -> MarvinSession | None:
    """Resolve live access for this tenant, or None. Never raises — a chat
    answer must degrade to "not connected" copy, not a 500."""
    try:
        return _open(enterprise_id)
    except Exception:  # noqa: BLE001 — a bad or rejected credential is "not connected" here
        logger.warning(
            "marvin lookup: could not open a session for %s",
            enterprise_id, exc_info=True,
        )
        return None


def _open(enterprise_id: str) -> MarvinSession | None:
    from app import db

    row = db.get_connection(enterprise_id, PROVIDER)
    if not row:
        return None
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, ValueError, KeyError, TypeError):
        logger.warning(
            "marvin lookup: could not decrypt the marvin token for %s", enterprise_id
        )
        return None
    if not isinstance(token_json, dict):
        return None

    region = token_json.get("region")
    token_json = _refreshed(enterprise_id, token_json, region)
    access_token = token_json.get("access_token") or ""
    mcp_url = (
        token_json.get("mcp_url") or marvin_oauth.region_config(region)["mcp_url"]
    )
    if not access_token or not mcp_url:
        return None
    return MarvinSession(
        enterprise_id=enterprise_id, access_token=access_token, mcp_url=mcp_url,
    )


def _refreshed(
    company_id: str, token_json: dict[str, Any], region: str | None
) -> dict[str, Any]:
    """`token_json`, refreshed and PERSISTED when the access token is expiring.

    Marvin's access tokens live about an hour, so most chat turns land on a
    stale one. Persisting the refresh is what makes this worth doing at all:
    Marvin may rotate the refresh token on use, and a refresh we do not store
    would strand the connection at the NEXT expiry while looking fine today.
    Same dance `connector_probe` runs for this provider.

    A failed WRITE is not a failed lookup — the fresh token in hand still works
    for this turn — so it is logged and swallowed. A failed REFRESH propagates:
    the caller turns it into "not connected", which is the reconnect prompt the
    user actually needs.
    """
    from app import db

    refresh_token = token_json.get("refresh_token")
    obtained_at = token_json.get("obtained_at", 0)
    expires_in = token_json.get("expires_in", 3600)
    if not refresh_token:
        return token_json
    if time.time() <= obtained_at + expires_in - REFRESH_MARGIN_S:
        return token_json

    fresh = json.loads(
        marvin_oauth.token_payload_to_store(
            marvin_oauth.refresh_access_token(refresh_token, region=region),
            region=region,
            keep_refresh_token=refresh_token,
        )
    )
    try:
        db.update_connection_tokens(
            company_id, PROVIDER, encrypt_token_json(json.dumps(fresh)),
        )
    except Exception:  # noqa: BLE001 — the token works now; the write can be retried
        logger.warning(
            "marvin lookup: refreshed token could not be persisted for %s",
            company_id, exc_info=True,
        )
    return fresh


# ── Calling a resolved tool ──────────────────────────────────────────────────

#: Argument names a tool might use for its result ordering.
_ORDER_PARAMS = ("sort", "order", "sort_by", "order_by", "sort_order", "orderby")
#: Enum values that unambiguously mean NEWEST FIRST. Deliberately strict: a
#: value like "date" says which field, not which direction, and claiming
#: recency we did not actually request is the exact bug #1042 fixed in Slack.
_NEWEST_HINTS = ("newest", "recent", "latest", "desc")


def newest_first(spec: ToolSpec) -> tuple[str, str] | None:
    """`(param, value)` asking `spec` for newest-first results, or None.

    Only claimed from a declared `enum`: Marvin publishes no schema, so an
    invented sort value is a 400 at best and a silently different ordering at
    worst. When this returns None the caller renders "ordering unspecified"
    rather than implying recency — see `order_marker`.
    """
    param = spec.param(*_ORDER_PARAMS)
    if not param:
        return None
    schema = ((spec.schema or {}).get("properties") or {}).get(param) or {}
    options = schema.get("enum")
    if not isinstance(options, list):
        return None
    for option in options:
        if not isinstance(option, str):
            continue
        flat = option.lower().replace(" ", "").replace("-", "_")
        if any(hint in flat for hint in _NEWEST_HINTS):
            return param, option
    return None


def page_args(spec: ToolSpec) -> dict[str, Any]:
    """Ask for a full page when the tool accepts a page-size parameter."""
    param = spec.param(
        "limit", "page_size", "per_page", "max_results", "count", "size"
    )
    return {param: PAGE_SIZE} if param else {}


#: Parameter names a tool might use for each logical argument. Marvin publishes
#: no schema, so the caller says WHAT it wants to pass and the actual name is
#: resolved from the resolved tool's own inputSchema.
QUERY_PARAMS = ("query", "q", "search", "text", "keywords", "term")
PROJECT_PARAMS = ("project_id", "project", "projectid", "study_id")
FILE_PARAMS = ("file_id", "file", "id", "fileid")


@dataclass(frozen=True)
class Arg:
    """One logical argument to pass to a tool whose parameter names are unknown.

    `required=False` means the read still makes sense without it — a project
    scope on a listing tool that has none simply widens the listing, and the
    caller is handed a note saying so instead of an error.
    """

    candidates: tuple[str, ...]
    value: Any
    required: bool = True


def run(
    session: MarvinSession,
    capability: str,
    args: tuple[Arg, ...] = (),
) -> tuple[dict[str, Any], list[str]]:
    """Call the tool serving `capability`. Returns `(result, notes)`.

    `notes` are honest markers the caller MUST render alongside the rows. The
    first is always the ordering (see `order_marker` for why an unstated
    ordering is a correctness problem, not a cosmetic one); an optional argument
    the tool does not accept adds a second saying the read was wider than asked.

    Raises `MarvinCapabilityMissing` when the server exposes no tool for this
    capability, when a REQUIRED argument has no parameter to go in, or when the
    tool requires an argument we cannot supply — calling it blind buys a
    guaranteed 400 and tells the model nothing. Raises `McpError` on a
    transport, auth or tool-level failure; the framework renders that.
    """
    with McpSession(
        session.mcp_url, session.access_token, timeout=HTTP_TIMEOUT
    ) as mcp:
        if session.caps is None:
            session.caps = resolve_capabilities(mcp.list_tools())
        spec = session.caps.get(capability)
        if spec is None:
            raise MarvinCapabilityMissing(capability)

        call_args: dict[str, Any] = {}
        notes: list[str] = []
        for arg in args:
            if arg.value in (None, ""):
                continue
            param = spec.param(*arg.candidates)
            if param:
                call_args[param] = arg.value
            elif arg.required:
                raise MarvinCapabilityMissing(
                    capability,
                    f"its tool `{spec.name}` accepts no {arg.candidates[0]} "
                    "argument, so the request cannot be expressed",
                )
            else:
                notes.append(
                    f"(note: this Marvin tool accepts no {arg.candidates[0]} "
                    "argument, so the result is NOT narrowed to it — say that "
                    "the read was wider than asked.)"
                )

        call_args.update(page_args(spec))
        ordering = newest_first(spec)
        if ordering:
            call_args[ordering[0]] = ordering[1]

        missing = spec.unfillable_required(call_args)
        if missing:
            raise MarvinCapabilityMissing(
                capability,
                f"its tool `{spec.name}` requires {', '.join(missing)}, which "
                "this question does not provide",
            )

        result = mcp.call_tool(spec.name, call_args)
        session.reached = True
        return result, [order_marker(ordering), *notes]


def order_marker(ordering: tuple[str, str] | None) -> str:
    """The ordering line that ships WITH every list of rows.

    Slack shipped a bug (#1042) where search defaulted to relevance and nothing
    in the result said so, and the model confidently called the highest-scoring
    matches of all time "the latest". Marvin's ordering is worse than
    undocumented — it is unknowable, because the tool schema is discovered at
    runtime. So the result states which of the two situations it is in, every
    time, and never leaves the model to assume.
    """
    if ordering:
        return (
            f"(ordered NEWEST FIRST — this tool's `{ordering[0]}` argument was "
            f'set to "{ordering[1]}".)'
        )
    return (
        "(ordering NOT SPECIFIED — this Marvin tool exposes no sort argument, so "
        "these are in the server's own default order, which may be relevance or "
        "an internal ranking rather than recency. Do NOT describe them as \"the "
        "latest\" or infer anything about dates from their position.)"
    )


# ── Rendering ────────────────────────────────────────────────────────────────


def _excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    flat = " ".join((text or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def rows_from(result: dict[str, Any]) -> list[dict[str, Any]]:
    return records_from_result(result)


def render_rows(
    rows: list[dict[str, Any]],
    *,
    header: str,
    notes: list[str],
    empty: str,
) -> str:
    """One line per record: id, title, date, project, analysis excerpt, link.

    Capped with `cap_items` so the honest "(showing N of M)" marker travels with
    the rows, and the excerpt comes from `_distill_text` — the analysis fields
    only — so a transcript cannot reach the model through a listing.
    """
    ordering = "\n".join(notes)
    if not rows:
        return f"{empty}\n{ordering}"
    kept, truncation = cap_items(rows, RESULT_LIMIT)
    lines: list[str] = []
    for row in kept:
        row_id = _first_str(row, _ID_FIELDS)
        title = _first_str(row, _TITLE_FIELDS)[:_MAX_TITLE] or "(untitled)"
        properties = _properties(row)
        bits = [f"- {row_id or '(no id)'}: {title}"]
        when = _first_str(row, _TIME_FIELDS)
        if when:
            bits.append(f" · {when}")
        if properties.get("project"):
            bits.append(f" · project {properties['project']}")
        if properties.get("url"):
            bits.append(f" · {properties['url']}")
        line = "".join(bits)
        excerpt = _excerpt(_distill_text(row))
        if excerpt:
            line += f"\n    {excerpt}"
        lines.append(line)
    parts = [header, ordering, "\n".join(lines), truncation]
    return "\n".join(p for p in parts if p)


def render_file(row: dict[str, Any], *, prose: str = "") -> str:
    """One research file in full — its ANALYSIS layer, not its transcript.

    `prose` is the text a server returned instead of a structured record; it is
    capped rather than dropped, because Marvin's file view leads with its AI
    summary and the cap is what stops a transcript tail from following it.
    """
    title = _first_str(row, _TITLE_FIELDS)[:_MAX_TITLE] or "(untitled)"
    head = [f"{title}"]
    when = _first_str(row, _TIME_FIELDS)
    if when:
        head.append(f"last updated {when}")
    properties = _properties(row)
    for key in ("project", "file_type", "participant_count", "tags", "url"):
        value = properties.get(key)
        if value not in (None, ""):
            head.append(f"{key.replace('_', ' ')}: {value}")
    body = _distill_text(row)
    if body and len(body) >= _MAX_TEXT:
        # `_distill_text` caps silently at `_MAX_TEXT`. On the sync path that is
        # fine — the ingest cap is the ingest cap — but a chat answer built on a
        # silently clipped summary reads as an answer built on the whole thing.
        # State it, the same way `cap_text` states its own truncation.
        body += (
            f"\n\n(this file's analysis is longer than shown — truncated at "
            f"{_MAX_TEXT:,} characters. Say your answer covers part of it and "
            "link the file for the rest.)"
        )
    body = body or prose
    if not body:
        return "\n".join(head) + (
            "\n\n(this file carries no summary, key points or other analysis "
            "layer. Sprntly reads only those fields — the interview transcript "
            "itself is deliberately not read — so there is nothing to quote "
            "here. Say that rather than implying the file is empty.)"
        )
    return "\n".join(head) + "\n\n" + cap_text(body, limit=FILE_TEXT_CHARS)


def prose_from(result: dict[str, Any]) -> str:
    """A server's text answer when it returned prose rather than records."""
    return text_from_result(result).strip()

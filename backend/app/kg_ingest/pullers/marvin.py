"""Marvin puller — research projects, studies and Insight reports → RawRecords.

Marvin (heymarvin.com) is a customer-insights repository: interviews, surveys
and the synthesized Insight reports a research team writes on top of them. Its
only programmatic read surface is an MCP server, so this puller speaks MCP
(app/connectors/mcp_client.py) rather than REST.

TOOL DISCOVERY, NOT HARDCODED TOOL NAMES
    Marvin publishes no schema for its MCP tools — no docs, and `tools/list`
    needs a live token. Hardcoding guessed names would produce a connector that
    breaks the first time they rename anything, and that cannot be written
    correctly without a sandbox in the first place. So the puller RESOLVES
    capabilities at sync time: it calls `tools/list`, matches each tool's NAME
    against the capability it is looking for (falling back to descriptions only
    for capabilities no name claimed), then reads the tool's own `inputSchema`
    to learn what its arguments are called. Renames, added tools and per-plan
    tool subsets all survive this; only a genuine capability removal breaks it,
    and that surfaces as a clear error.

    The trade-off is honest: resolution is heuristic. It is bounded by
    requiring an unambiguous keyword match, and by refusing to call a tool
    whose required arguments we cannot fill.

DATA MINIMIZATION (the §6 no-raw-dump contract)
    Marvin holds full interview transcripts. Those are NOT ingested. Structured
    records are distilled to their summary-bearing fields, prose responses are
    capped, and the whole pull is bounded by project/file caps. This is the
    same discipline the Fireflies puller applies to meeting transcripts: the
    KG gets the distilled layer, never the verbatim corpus.

Auth: the packed (access_token, mcp_url) credential from marvin_oauth —
`token_for()` hands it over as one string (PULLERS key = "marvin_credential").
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterator

from app.connectors import marvin_oauth
from app.connectors.mcp_client import (
    McpError,
    McpSession,
    records_from_result,
    text_from_result,
)
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

PROVIDER = "marvin"

# Pilot-scale caps, mirroring the other voice-of-customer pullers. A research
# repository is small by row count but heavy by document size, so the binding
# constraint is characters, not pages.
_MAX_PROJECTS = 50
_MAX_FILES = 200
_MAX_TEXT = 4000
_MAX_TITLE = 300
#: Page size requested from list/search tools that accept one. Marvin's search
#: documents a default of 20 and a maximum of 100.
_PAGE_SIZE = 100


# ── Capability resolution ───────────────────────────────────────────────────


@dataclass
class ToolSpec:
    """A resolved MCP tool plus the argument names it actually uses."""

    name: str
    schema: dict[str, Any]

    #: Substring matching is skipped for candidates this short. "id" inside
    #: "projectId" is a coincidence, not a match — and acting on it would send
    #: a file id where a project id was wanted. Exact matches on short names
    #: are still honoured.
    _MIN_SUBSTRING_LEN = 3

    def param(self, *candidates: str) -> str | None:
        """This tool's parameter matching any of `candidates`, or None.

        Exact (case-insensitive) matches win over substring matches, so a tool
        exposing both `query` and `query_language` resolves `query` correctly.
        """
        props = (self.schema or {}).get("properties") or {}
        lowered = {k.lower(): k for k in props if isinstance(k, str)}
        for candidate in candidates:
            if candidate in lowered:
                return lowered[candidate]
        for candidate in candidates:
            if len(candidate) < self._MIN_SUBSTRING_LEN:
                continue
            for low, original in lowered.items():
                if candidate in low:
                    return original
        return None

    def unfillable_required(self, filled: dict[str, Any]) -> list[str]:
        """Required parameters `filled` doesn't supply.

        A tool we can't legally call is skipped rather than called blind — a
        400 from the server tells us nothing useful and burns a round trip.
        """
        required = (self.schema or {}).get("required") or []
        return [r for r in required if isinstance(r, str) and r not in filled]


#: capability → (must-have keyword, at-least-one-of keywords). Matched against
#: the tool's name and description, lowercased. Ordered most specific first so
#: a single tool can only claim one capability.
_CAPABILITY_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("get_file", "file", ("content", "detail", "summary", "read", "get", "show")),
    ("list_files", "file", ("list", "all", "browse")),
    ("list_projects", "project", ("list", "all", "browse")),
    ("search", "search", ()),
]


def _matches(rule: tuple[str, str, tuple[str, ...]], haystack: str) -> bool:
    _, required_kw, any_kw = rule
    if required_kw not in haystack:
        return False
    return not any_kw or any(kw in haystack for kw in any_kw)


def _resolve_pass(
    tools: list[dict[str, Any]],
    resolved: dict[str, ToolSpec],
    *,
    use_description: bool,
) -> None:
    """Claim unresolved capabilities from `tools`, mutating `resolved`.

    Each tool claims at most one capability (the first rule it matches), and
    each capability keeps the first tool that claims it, so a server exposing
    several search variants doesn't produce duplicate pulls.
    """
    for tool in tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        haystack = name.lower()
        if use_description:
            haystack = f"{haystack} {tool.get('description') or ''}".lower()
        for rule in _CAPABILITY_RULES:
            capability = rule[0]
            if capability in resolved or not _matches(rule, haystack):
                continue
            resolved[capability] = ToolSpec(
                name=name, schema=tool.get("inputSchema") or {}
            )
            break


def resolve_capabilities(tools: list[dict[str, Any]]) -> dict[str, ToolSpec]:
    """Map capability → ToolSpec for the tools this server exposes.

    Two passes, names first. Descriptions are prose and leak keywords across
    capability boundaries — "List files in a project. Returns file details"
    would otherwise let a LISTING tool claim the DETAIL capability, and the
    puller would then fetch every file with a tool that returns a page of
    them. Matching names alone resolves the common case unambiguously; the
    description pass only runs for capabilities still unclaimed, where a fuzzy
    match beats no match at all.
    """
    resolved: dict[str, ToolSpec] = {}
    _resolve_pass(tools, resolved, use_description=False)
    _resolve_pass(tools, resolved, use_description=True)
    return resolved


# ── Field distillation ──────────────────────────────────────────────────────

#: Fields that carry ANALYSIS rather than raw capture, in preference order.
#: Ingesting these instead of whatever the record's biggest string happens to
#: be is what keeps transcripts out of the knowledge graph.
_SUMMARY_FIELDS = (
    "summary", "ai_summary", "insight", "insights", "takeaways",
    "key_points", "highlights", "findings", "analysis", "notes",
    "description", "overview", "abstract",
)
_TITLE_FIELDS = ("title", "name", "file_name", "filename", "subject", "label")
_ID_FIELDS = ("id", "file_id", "project_id", "uuid", "key", "slug")
_TIME_FIELDS = (
    "updated_at", "modified_at", "modifiedTime", "last_modified",
    "created_at", "date", "createdTime",
)
_LINK_FIELDS = ("url", "link", "permalink", "web_url", "marvin_url")


def _normalize_key(key: str) -> str:
    """Fold a field name to its comparable form.

    Marvin's casing convention is undocumented and the MCP surface has already
    shown both styles, so `fileId`, `file_id` and `File ID` must all resolve to
    the same field. Lowercasing alone would not do it.
    """
    return "".join(ch for ch in key.lower() if ch.isalnum())


def _indexed(record: dict[str, Any]) -> dict[str, Any]:
    """`record` keyed by normalized field name."""
    return {
        _normalize_key(k): v for k, v in record.items() if isinstance(k, str)
    }


def _first_str(record: dict[str, Any], fields: tuple[str, ...]) -> str:
    """First non-empty string value among `fields`, casing-insensitive."""
    indexed = _indexed(record)
    normalized_ids = {_normalize_key(f) for f in _ID_FIELDS}
    for field in fields:
        key = _normalize_key(field)
        value = indexed.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        # Ids are frequently numeric; other fields are not coerced, so a stray
        # integer never becomes a title.
        if isinstance(value, (int, float)) and key in normalized_ids:
            return str(value)
    return ""


def _distill_text(record: dict[str, Any]) -> str:
    """The analysis-bearing text of a record, capped.

    Concatenates every summary field present rather than taking only the first,
    because Marvin splits analysis across several (a summary AND key points AND
    takeaways all say different things). List values are flattened to bullets.
    Fields outside `_SUMMARY_FIELDS` are deliberately ignored — that is the
    filter keeping verbatim transcript text out.
    """
    indexed = _indexed(record)
    parts: list[str] = []
    for field in _SUMMARY_FIELDS:
        value = indexed.get(_normalize_key(field))
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, list):
            bullets = [
                f"- {item.strip()}" for item in value
                if isinstance(item, str) and item.strip()
            ]
            if bullets:
                parts.append("\n".join(bullets))
    return "\n\n".join(parts)[:_MAX_TEXT]


def _properties(record: dict[str, Any]) -> dict[str, Any]:
    """Compact structured fields worth keeping alongside the text."""
    indexed = _indexed(record)
    tags = indexed.get("tags")
    out = {
        "project": _first_str(record, ("project", "project_name", "study")),
        "file_type": _first_str(record, ("type", "file_type", "kind", "media_type")),
        "participant_count": indexed.get("participantcount") or indexed.get("participants"),
        "tags": (
            ", ".join(t for t in tags if isinstance(t, str))
            if isinstance(tags, list)
            else _first_str(record, ("tags",))
        ),
        "url": _first_str(record, _LINK_FIELDS),
    }
    return {k: v for k, v in out.items() if v not in (None, "", [])}


# ── Tool invocation helpers ─────────────────────────────────────────────────


def _call(session: McpSession, spec: ToolSpec, args: dict[str, Any]) -> dict[str, Any] | None:
    """Invoke a resolved tool, or return None if it can't be called safely.

    Per-call failures are non-fatal: one project whose files we can't list
    shouldn't lose the rest of the pull. A 401/403 is re-raised — that's a dead
    token, and the sync must surface it rather than reporting an empty success.
    """
    missing = spec.unfillable_required(args)
    if missing:
        logger.info(
            "marvin: skipping tool %s — required args not resolvable: %s",
            spec.name, ", ".join(missing),
        )
        return None
    try:
        return session.call_tool(spec.name, args)
    except McpError as e:
        if e.status_code in (401, 403):
            raise
        logger.info("marvin: tool %s failed: %s", spec.name, e)
        return None


def _paging_args(spec: ToolSpec) -> dict[str, Any]:
    """Ask for a full page when the tool accepts a page-size parameter."""
    param = spec.param("limit", "page_size", "per_page", "max_results", "count", "size")
    return {param: _PAGE_SIZE} if param else {}


# ── Sub-pulls ───────────────────────────────────────────────────────────────


def _pull_projects(
    session: McpSession, caps: dict[str, ToolSpec]
) -> tuple[list[RawRecord], list[dict[str, Any]]]:
    """Projects as RawRecords, plus the raw rows so files can be pulled per project.

    A project in Marvin is a research study — its description and stated
    research questions are context the KG wants in their own right, not just a
    folder name.
    """
    spec = caps.get("list_projects")
    if not spec:
        return [], []
    result = _call(session, spec, _paging_args(spec))
    if result is None:
        return [], []

    rows = records_from_result(result)[:_MAX_PROJECTS]
    records: list[RawRecord] = []
    for row in rows:
        project_id = _first_str(row, _ID_FIELDS)
        title = _first_str(row, _TITLE_FIELDS)
        if not project_id or not title:
            continue
        records.append(RawRecord(
            provider=PROVIDER,
            kind="project",
            external_id=f"project:{project_id}",
            title=title[:_MAX_TITLE],
            text=_distill_text(row) or title,
            properties=_properties(row),
            timestamp=_first_str(row, _TIME_FIELDS) or None,
        ))
    return records, rows


def _pull_files(
    session: McpSession, caps: dict[str, ToolSpec], projects: list[dict[str, Any]]
) -> Iterator[RawRecord]:
    """Research files (interviews, surveys, studies) as distilled RawRecords.

    Scoped per project when the listing tool takes a project argument, so the
    pull stays bounded and each file inherits its project as context; otherwise
    listed once workspace-wide.
    """
    spec = caps.get("list_files")
    if not spec:
        return

    project_param = spec.param("project_id", "project", "projectid", "study_id")
    if project_param and projects:
        scopes: list[tuple[dict[str, Any], str]] = []
        for project in projects[:_MAX_PROJECTS]:
            project_id = _first_str(project, _ID_FIELDS)
            if project_id:
                scopes.append((
                    {**_paging_args(spec), project_param: project_id},
                    _first_str(project, _TITLE_FIELDS),
                ))
    else:
        scopes = [(_paging_args(spec), "")]

    seen: set[str] = set()
    for args, project_title in scopes:
        if len(seen) >= _MAX_FILES:
            logger.info("marvin: hit the %s-file cap — stopping", _MAX_FILES)
            return
        result = _call(session, spec, args)
        if result is None:
            continue
        for row in records_from_result(result):
            if len(seen) >= _MAX_FILES:
                return
            file_id = _first_str(row, _ID_FIELDS)
            title = _first_str(row, _TITLE_FIELDS)
            if not file_id or file_id in seen or not title:
                continue
            seen.add(file_id)
            record = _build_file_record(session, caps, row, file_id, title)
            if record:
                if project_title and "project" not in record.properties:
                    record.properties["project"] = project_title
                yield record


def _build_file_record(
    session: McpSession,
    caps: dict[str, ToolSpec],
    row: dict[str, Any],
    file_id: str,
    title: str,
) -> RawRecord | None:
    """One file's RawRecord, enriched with its detail view when available.

    The listing usually carries only a name and a date, so the analysis has to
    come from the per-file tool. When that tool returns prose rather than a
    structured record we cap it — Marvin's file view leads with its AI summary,
    so the first `_MAX_TEXT` characters are the distilled layer, and the cap is
    what stops a transcript tail from following it into the graph.
    """
    text = _distill_text(row)
    properties = _properties(row)
    timestamp = _first_str(row, _TIME_FIELDS) or None

    detail_spec = caps.get("get_file")
    if detail_spec:
        file_param = detail_spec.param("file_id", "file", "id", "fileid")
        if file_param:
            result = _call(session, detail_spec, {file_param: file_id})
            if result is not None:
                detail_rows = records_from_result(result)
                if detail_rows:
                    detail = detail_rows[0]
                    detailed_text = _distill_text(detail)
                    if detailed_text:
                        text = detailed_text
                    properties = {**_properties(detail), **properties}
                    timestamp = timestamp or _first_str(detail, _TIME_FIELDS) or None
                else:
                    prose = text_from_result(result).strip()
                    if prose:
                        text = prose[:_MAX_TEXT]

    if not text:
        return None
    return RawRecord(
        provider=PROVIDER,
        kind="research_file",
        external_id=f"file:{file_id}",
        title=title[:_MAX_TITLE],
        text=text,
        properties=properties,
        timestamp=timestamp,
    )


# ── Entry point ─────────────────────────────────────────────────────────────


def pull(credential: str) -> Iterator[RawRecord]:
    """Yield distilled RawRecords from a Marvin workspace.

    Raises when the connection is unusable — a rejected token, or an MCP
    surface exposing nothing we can read (which is what a workspace with the
    admin MCP toggle switched off looks like). Both need to reach the user as a
    sync error rather than as a silently empty success.
    """
    access_token, mcp_url = marvin_oauth.parse_credential(credential)

    with McpSession(mcp_url, access_token) as session:
        tools = session.list_tools()
        caps = resolve_capabilities(tools)
        logger.info(
            "marvin: %s tools exposed, resolved %s",
            len(tools), ", ".join(sorted(caps)) or "nothing",
        )
        if not caps.get("list_projects") and not caps.get("list_files"):
            raise McpError(
                "Marvin's MCP server exposed no project or file listing tool "
                f"(saw: {', '.join(t.get('name', '?') for t in tools) or 'none'}). "
                "Check that MCP is enabled for this workspace."
            )

        project_records, project_rows = _pull_projects(session, caps)
        yield from project_records
        yield from _pull_files(session, caps, project_rows)

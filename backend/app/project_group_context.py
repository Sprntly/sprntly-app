"""Project-awareness for the @Sprntly group agent — breadth (an injected,
bounded context block folded into every group reply) AND depth (on-demand
read tools the model can call to pull the full memory, artifact list, a
specific artifact's content, or the whole delegation ledger).

Load-bearing tenancy invariant: EVERY read here is scoped to the ONE
project (`project_id`) and its company (`company_id`)/dataset the caller
already resolved. The group agent must never reach another company's data.
`get_artifact_content` in particular gates on the project's OWN artifact
manifest (`list_artifacts_for_project`, itself tenant-scoped) before it
returns any bytes — a `(type, id)` that is not on this project's manifest
is refused, so a hallucinated or probed id can never resolve globally.

Everything is best-effort at the assembly layer the same way
`assemble_project_context` is: the injected-context builder degrades a
failed section to "(unavailable)" rather than raising, so a read hiccup
never blocks the group reply (AD-P7). The tool handlers return a short
string either way (content or a plain refusal/So-such message) — never
raise into `run_tool_loop`, which would otherwise surface as a tool error.
"""
from __future__ import annotations

import logging

from app.db import delegation_events as delegation_events_db
from app.db import project_memory_entries as memory_db
from app.db import projects as projects_db
from app.db.artifacts import list_artifacts_for_project

logger = logging.getLogger(__name__)

# Section caps for the INJECTED context block (breadth). Each is a soft
# guardrail mirroring `project_context.assemble_project_context`'s posture —
# a heavily-used project can't grow the group prompt unboundedly.
_LEDGER_DIGEST_ROWS = 15
_SUMMARY_CHARS = 1200
_INSIGHT_CHARS = 400
_MANIFEST_TITLE_CHARS = 60
_MANIFEST_TITLES_PER_TYPE = 6

# Cap on a single artifact's content returned by `get_artifact_content`
# (depth) — bounded like the brief's own artifact fold, so one large PRD/
# report body can't blow the tool-result back into the model unboundedly.
_ARTIFACT_CONTENT_CHARS = 8000

_TYPE_LABELS = {
    "prd": "PRDs",
    "prototype": "Prototypes",
    "evidence": "Evidence",
    "report": "Reports",
    "ticket_set": "Ticket sets",
}


def _first_name(name: str | None) -> str:
    if not name:
        return "someone"
    return name.split()[0]


def _members_by_id(project_id: int) -> dict[str, str | None]:
    try:
        return {m["user_id"]: m.get("name") for m in projects_db.list_members(project_id)}
    except Exception:  # noqa: BLE001 — best-effort
        return {}


def _ledger_digest(project_id: int, members: dict[str, str | None]) -> str:
    """"Open tasks: <assignee first name> — <task_summary> (<status>)" lines,
    capped. Open rows first (they are what the room usually asks about), then
    recent closed ones, up to the row cap."""
    try:
        rows = delegation_events_db.list_status_for_project(project_id)
    except Exception:  # noqa: BLE001
        return "(unavailable)"
    if not rows:
        return "(no tasks yet)"
    open_rows = [r for r in rows if r.get("status") in delegation_events_db.OPEN_STATES]
    closed_rows = [r for r in rows if r.get("status") not in delegation_events_db.OPEN_STATES]
    ordered = (open_rows + closed_rows)[:_LEDGER_DIGEST_ROWS]
    lines = []
    for r in ordered:
        who = _first_name(members.get(r.get("assignee_user_id")))
        summary = (r.get("task_summary") or "").strip() or "(no summary)"
        lines.append(f"- {who} — {summary} ({r.get('status')})")
    return "\n".join(lines)


def _artifact_manifest(project_id: int, dataset: str, company_id: str) -> str:
    """"PRDs (2): <title>, <title>; Prototypes (1): ...; Evidence (N): ..."
    grouped by type, titles capped per type and per length."""
    try:
        items = list_artifacts_for_project(
            project_id=project_id, dataset=dataset, company_id=company_id
        )
    except Exception:  # noqa: BLE001
        return "(unavailable)"
    if not items:
        return "(no artifacts yet)"
    by_type: dict[str, list[dict]] = {}
    for it in items:
        by_type.setdefault(it.get("type"), []).append(it)
    parts = []
    for atype, group in by_type.items():
        label = _TYPE_LABELS.get(atype, (atype or "other").capitalize())
        titles = []
        for it in group[:_MANIFEST_TITLES_PER_TYPE]:
            t = (it.get("title") or "Untitled").strip()
            if len(t) > _MANIFEST_TITLE_CHARS:
                t = t[:_MANIFEST_TITLE_CHARS].rstrip() + "…"
            titles.append(f"{t} [id {it.get('id')}]")
        more = "" if len(group) <= _MANIFEST_TITLES_PER_TYPE else ", …"
        parts.append(f"{label} ({len(group)}): {', '.join(titles)}{more}")
    return "; ".join(parts)


def _roster_block(project_id: int) -> str:
    """"<name> — <job_role>" lines for every human member of this project.
    Degrades to a placeholder on a read failure (AD-P7)."""
    try:
        members = projects_db.list_members(project_id)
    except Exception:  # noqa: BLE001 — best-effort
        return "(unavailable)"
    if not members:
        return "(no members yet)"
    lines = []
    for m in members:
        name = (m.get("name") or "A teammate").strip()
        role = (m.get("job_role") or "").strip()
        lines.append(f"- {name} — {role}" if role else f"- {name}")
    return "\n".join(lines)


def assemble_private_project_context(
    project_id: int, user_id: str, dataset: str, company_id: str
) -> str:
    """Enriched PROJECT CONTEXT block for the PRIVATE ("My chat with Sprntly")
    individual chat — the SAME breadth the @Sprntly group agent gets (memory
    summary + roster of members/roles + task-ledger digest + artifact
    manifest), on top of the caller's own memory entries + job_role that
    `project_context.assemble_project_context` already folds in.

    BREADTH only: one bounded injected block, single-shot — NO read tools, NO
    tool loop, NO write path (that is a separate build). Every section is
    bounded by the same soft caps the group-agent block uses so it can't blow
    the ask prompt. Never raises — each section degrades to a placeholder / is
    omitted on a read failure (AD-P7), and the whole block only ever reflects
    THIS project/company."""
    parts: list[str] = []

    # Summary + the caller's own memory entries + their job_role (recency-
    # ordered, budgeted) — the existing private-chat fold, kept intact.
    try:
        from app.project_context import assemble_project_context

        base = assemble_project_context(project_id, user_id)
    except Exception:  # noqa: BLE001 — best-effort
        base = ""
    if base:
        parts.append(base)

    members = _members_by_id(project_id)
    roster = _roster_block(project_id)
    ledger = _ledger_digest(project_id, members)
    manifest = _artifact_manifest(project_id, dataset, company_id)

    parts.append(
        "This project only — never another company's data.\n"
        f"Project roster (who is on this project):\n{roster}\n\n"
        f"Task ledger (open delegations first):\n{ledger}\n\n"
        f"Artifacts: {manifest}"
    )
    return "\n\n".join(p for p in parts if p)


def assemble_group_agent_context(project_id: int, dataset: str, company_id: str) -> str:
    """The bounded PROJECT CONTEXT block appended to the group agent's system
    prompt on every reply (breadth). Never raises — each section degrades to a
    placeholder on a read failure, so a folding hiccup can never block the
    reply (AD-P7). Scoped entirely to this one project/company."""
    members = _members_by_id(project_id)

    try:
        summary = memory_db.get_summary(project_id) or {}
        summary_md = (summary.get("summary_md") or "").strip()
    except Exception:  # noqa: BLE001
        summary_md = ""
    if summary_md and len(summary_md) > _SUMMARY_CHARS:
        summary_md = summary_md[:_SUMMARY_CHARS].rstrip() + "…"

    try:
        insight = memory_db.get_latest_insight(project_id)
    except Exception:  # noqa: BLE001
        insight = None
    insight_text = ((insight or {}).get("text") or "").strip()
    if insight_text and len(insight_text) > _INSIGHT_CHARS:
        insight_text = insight_text[:_INSIGHT_CHARS].rstrip() + "…"

    roster = _roster_block(project_id)
    ledger = _ledger_digest(project_id, members)
    manifest = _artifact_manifest(project_id, dataset, company_id)

    block = [
        "PROJECT CONTEXT (this project only — never another company's data):",
        f"Project memory summary: {summary_md or '(none yet)'}",
        f"Latest shared insight: {insight_text or '(none yet)'}",
        f"Project roster (who is on this project):\n{roster}",
        f"Task ledger:\n{ledger}",
        f"Artifacts: {manifest}",
    ]
    return "\n".join(block)


# ── Read tools (depth) — each scoped to THIS project/company ────────────────

GET_PROJECT_MEMORY_TOOL = {
    "name": "get_project_memory",
    "description": (
        "Read this project's full shared memory — the synthesized summary plus "
        "every memory entry (what the team has decided/learned). Call this when "
        "someone asks what the project knows, remembers, or has decided."
    ),
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}

LIST_PROJECT_ARTIFACTS_TOOL = {
    "name": "list_project_artifacts",
    "description": (
        "List every artifact attached to this project (PRDs, prototypes, "
        "evidence, reports, ticket sets) with their type, id, and title. Call "
        "this to answer how many/which artifacts exist, or to find an artifact's "
        "id before reading its content."
    ),
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}

GET_ARTIFACT_CONTENT_TOOL = {
    "name": "get_artifact_content",
    "description": (
        "Read the full content of ONE artifact on this project — e.g. a PRD's "
        "body, an evidence brief, or a report. Pass the artifact_type and "
        "artifact_id exactly as returned by list_project_artifacts. Call this "
        "when asked what a specific document says or to summarize it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "artifact_type": {
                "type": "string",
                "enum": ["prd", "prototype", "evidence", "report", "ticket_set"],
                "description": "the artifact's type, from list_project_artifacts",
            },
            "artifact_id": {
                "type": "integer",
                "description": "the artifact's id, from list_project_artifacts",
            },
        },
        "required": ["artifact_type", "artifact_id"],
        "additionalProperties": False,
    },
}

GET_TASK_LEDGER_TOOL = {
    "name": "get_task_ledger",
    "description": (
        "Read this project's full delegation ledger — every task handed off, who "
        "it is assigned to, and its current status. Call this to answer what is "
        "open, who is working on what, or the status of a specific task."
    ),
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def read_tools() -> list[dict]:
    """The four project-scoped read tools, in a stable order."""
    return [
        GET_PROJECT_MEMORY_TOOL,
        LIST_PROJECT_ARTIFACTS_TOOL,
        GET_ARTIFACT_CONTENT_TOOL,
        GET_TASK_LEDGER_TOOL,
    ]


_READ_TOOL_NAMES = frozenset(t["name"] for t in read_tools())


def _clamp(text: str, cap: int) -> str:
    text = text or ""
    return text if len(text) <= cap else text[:cap].rstrip() + "\n…(truncated)"


def _handle_get_project_memory(project_id: int) -> str:
    summary = memory_db.get_summary(project_id) or {}
    summary_md = (summary.get("summary_md") or "").strip()
    entries = memory_db.list_entries(project_id)
    lines = [f"Project memory summary: {summary_md or '(none yet)'}"]
    if entries:
        lines.append("Entries (most recent first):")
        for e in entries[:20]:
            body = (e.get("body") or "").strip()
            if body:
                lines.append(f"- {body}")
    else:
        lines.append("Entries: (none yet)")
    return _clamp("\n".join(lines), _ARTIFACT_CONTENT_CHARS)


def _handle_list_project_artifacts(project_id: int, dataset: str, company_id: str) -> str:
    items = list_artifacts_for_project(
        project_id=project_id, dataset=dataset, company_id=company_id
    )
    if not items:
        return "This project has no artifacts."
    lines = [
        f"- {it.get('type')} id={it.get('id')}: {(it.get('title') or 'Untitled').strip()}"
        for it in items
    ]
    return _clamp("\n".join(lines), _ARTIFACT_CONTENT_CHARS)


def _handle_get_task_ledger(project_id: int) -> str:
    members = _members_by_id(project_id)
    try:
        rows = delegation_events_db.list_status_for_project(project_id)
    except Exception:  # noqa: BLE001
        return "The task ledger is unavailable right now."
    if not rows:
        return "This project has no delegated tasks yet."
    lines = []
    for r in rows:
        who = _first_name(members.get(r.get("assignee_user_id")))
        by = _first_name(members.get(r.get("assigner_user_id")))
        summary = (r.get("task_summary") or "").strip() or "(no summary)"
        lines.append(
            f"- #{r.get('delegation_id')}: {summary} — assigned to {who} "
            f"by {by} ({r.get('status')})"
        )
    return _clamp("\n".join(lines), _ARTIFACT_CONTENT_CHARS)


def _artifact_content_for(atype: str, artifact_id: int, company_id: str) -> str | None:
    """Fetch ONE artifact's readable content by type. Returns None when the
    type has no readable body here. The CALLER has already proven the
    (type, id) is on this project's manifest, so this only reads."""
    if atype == "prd":
        from app.db.prds import get_prd_rendered

        row = get_prd_rendered(artifact_id)
        if not row:
            return None
        return (row.get("payload_md") or "").strip() or "(empty PRD)"
    if atype == "evidence":
        from app.db.evidences import get_evidence

        row = get_evidence(artifact_id)
        if not row:
            return None
        return (row.get("payload_md") or "").strip() or "(empty evidence)"
    if atype == "report":
        from app.db.reports import get_report

        row = get_report(artifact_id, company_id)
        if not row:
            return None
        return (row.get("html") or "").strip() or "(empty report)"
    if atype == "prototype":
        # Prototype code lives in checkpoints and is large/non-textual; the
        # manifest already carries its title/status, so surface a pointer
        # rather than dumping generated markup into the model.
        return (
            "This is an interactive prototype. Its title and status are in the "
            "project artifact list; open it in the app to view the running "
            "prototype."
        )
    if atype == "ticket_set":
        return (
            "This is a set of tickets generated from a chat. Open it in the app "
            "to view the individual tickets."
        )
    return None


def _handle_get_artifact_content(
    project_id: int, dataset: str, company_id: str, tool_input: dict
) -> str:
    atype = (tool_input.get("artifact_type") or "").strip()
    try:
        artifact_id = int(tool_input.get("artifact_id"))
    except (TypeError, ValueError):
        return "That artifact id isn't valid."

    # TENANCY GATE: only artifacts on THIS project's own (tenant-scoped)
    # manifest are readable. A (type, id) not on the manifest is refused — no
    # global lookup, no cross-project/cross-company reach.
    manifest = list_artifacts_for_project(
        project_id=project_id, dataset=dataset, company_id=company_id
    )
    if not any(it.get("type") == atype and it.get("id") == artifact_id for it in manifest):
        return "I can't find that artifact on this project."

    content = _artifact_content_for(atype, artifact_id, company_id)
    if content is None:
        return "I couldn't read that artifact's content."
    return _clamp(content, _ARTIFACT_CONTENT_CHARS)


def dispatch_read_tool(
    name: str, tool_input: dict, *, project_id: int, dataset: str, company_id: str
) -> str | None:
    """Route one read-tool call. Returns the tool-result string, or None when
    `name` is not one of these read tools (so the caller can fall through to
    its own tools, e.g. delegate_task). Never raises — a handler failure
    degrades to a plain apology string so `run_tool_loop` sees a normal
    tool_result, not an error."""
    if name not in _READ_TOOL_NAMES:
        return None
    try:
        if name == "get_project_memory":
            return _handle_get_project_memory(project_id)
        if name == "list_project_artifacts":
            return _handle_list_project_artifacts(project_id, dataset, company_id)
        if name == "get_artifact_content":
            return _handle_get_artifact_content(project_id, dataset, company_id, tool_input)
        if name == "get_task_ledger":
            return _handle_get_task_ledger(project_id)
    except Exception as exc:  # noqa: BLE001 — never raise into the tool loop
        logger.warning(
            "group_agent_read_tool_failed tool=%s project_id=%s error_class=%s",
            name, project_id, type(exc).__name__,
        )
        return "I hit a problem reading that just now."
    return None

"""Marvin adapter — live research reads from chat.

Read-only by construction. `mcp:read` is the only scope Marvin's authorization
server offers (marvin_oauth.MARVIN_SCOPES), so unlike the Jira adapter there is
no write path to guard with a confirm-card contract — there is no way for a
Sprntly connection to alter a customer's research at all.

WHY THIS EXISTS GIVEN MARVIN ALREADY SYNCS
    The same split as Confluence. The knowledge graph holds EXTRACTED SIGNALS —
    atomic evidence statements the extractor pulled out of research files — and
    it holds them as of the last sync. "What did the onboarding study actually
    conclude", "is there anything in Marvin about the pricing page", "what did
    we learn from the interviews we ran last week" are questions about the
    RESEARCH, and only a live read answers them against what is there now. The
    two readers are complementary and the system block tells the model which one
    it is holding.

ASK AI IS DELIBERATELY NOT WIRED
    Marvin exposes an "Ask AI" tool. It is not offered here for the same two
    reasons the puller refuses it: it is nondeterministic, so an answer cannot
    be traced back to the research it came from, and it bills the customer's own
    Marvin account for a question they did not know they were asking. Sprntly
    reads the repository and does its own synthesis.

THE TOOLS ARE DISCOVERED, NOT NAMED
    Marvin publishes no schema, so the four tools below are CAPABILITIES that
    `resolve_capabilities` maps onto whatever this server actually exposes (see
    connectors/marvin_fetch.py). A workspace whose plan omits one gets honest
    copy saying that capability is unavailable — never a "found nothing".
"""
from __future__ import annotations

import logging

from app.connector_lookup.base import LookupSession
from app.connectors import marvin_fetch

logger = logging.getLogger(__name__)

DISPLAY_NAME = "Marvin"

SYSTEM = (
    "Marvin (heymarvin.com) is the company's customer-research repository: "
    "interviews, surveys, tagged notes and the synthesized studies a research "
    "team writes on top of them.\n\n"
    "Tools:\n"
    "- marvin_search: search the repository by `query`. Returns one line per "
    "hit (id, title, date, project, an analysis excerpt and a link).\n"
    "- marvin_list_projects: the research projects/studies in the workspace. "
    "Use it to survey what research exists before searching blind.\n"
    "- marvin_list_files: research files, optionally scoped to one "
    "`project_id` from marvin_list_projects.\n"
    "- marvin_get_file: one research file in full by its id — its summary, key "
    "points and findings. Use it once search or listing has told you which "
    "file matters.\n\n"
    "Honest limits you MUST respect:\n"
    "Sprntly reads Marvin AS THE PERSON WHO CONNECTED IT. Workspace and project "
    "permissions apply, so research you cannot find may simply be INVISIBLE to "
    "that account rather than absent. Permission-invisible research is not "
    "missing research — say which of the two you mean rather than concluding "
    "the team has never studied a topic.\n"
    "INTERVIEW TRANSCRIPTS ARE NOT READ. Sprntly reads only the analysis layer "
    "— summaries, key points, takeaways, findings, descriptions — never the "
    "verbatim capture. So you can say what a study CONCLUDED and never quote "
    "what a participant said word for word. If asked for a verbatim quote, say "
    "plainly that Sprntly does not ingest transcripts and point at the file's "
    "link instead.\n"
    "Every list states the ORDER it came back in. When it says the ordering is "
    "not specified, do not call those results \"the latest\" or read anything "
    "into their position — the server chose the order, not the question.\n"
    "If a result says a capability is UNAVAILABLE on this connection, that is "
    "NOT a no-results answer: this workspace's MCP server exposes no tool for "
    "it. Say we could not look, and use the capabilities that did work.\n"
    "This connection is READ-ONLY: `mcp:read` is the only permission Sprntly "
    "holds, so you cannot create, edit, tag or comment on anything in Marvin. "
    "If asked, say so plainly and offer to summarize instead."
)

SEARCH_TOOL = {
    "name": "marvin_search",
    "description": (
        "Search the company's Marvin research repository. Provide `query` "
        "(keywords matched across research files, studies and their analysis). "
        "Returns matching files with an excerpt of their analysis and a link."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keyword(s) to search for."},
        },
        "required": ["query"],
    },
}

LIST_PROJECTS_TOOL = {
    "name": "marvin_list_projects",
    "description": (
        "List the research projects (studies) in the Marvin workspace, with "
        "each one's description and id. Use this to survey what research "
        "exists, and to get a `project_id` for marvin_list_files."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

LIST_FILES_TOOL = {
    "name": "marvin_list_files",
    "description": (
        "List research files (interviews, surveys, studies) in Marvin, "
        "optionally scoped to one `project_id` from marvin_list_projects. Use "
        "it to see what a project contains, or when search is unavailable."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "project_id": {
                "type": "string",
                "description": "Optional project id to restrict the listing to.",
            },
        },
    },
}

GET_FILE_TOOL = {
    "name": "marvin_get_file",
    "description": (
        "Fetch one Marvin research file in full by its id (as returned by "
        "marvin_search or marvin_list_files): its summary, key points and "
        "findings. Interview transcripts are not included."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "The Marvin file id."},
        },
        "required": ["file_id"],
    },
}

TOOLS = [SEARCH_TOOL, LIST_PROJECTS_TOOL, LIST_FILES_TOOL, GET_FILE_TOOL]

NOT_CONNECTED = (
    "I can read your Marvin research live — search studies, pull a file's "
    "findings — but Marvin isn't connected yet (or its access needs "
    "refreshing). Connect **Marvin** in Settings → Connectors and ask me again."
)

#: Which capability each tool needs. Also the membership test that keeps an
#: unknown tool name from reaching the server (or the sync kickoff).
_TOOL_CAPABILITY = {
    "marvin_search": "search",
    "marvin_list_projects": "list_projects",
    "marvin_list_files": "list_files",
    "marvin_get_file": "get_file",
}

_CAPABILITY_LABEL = {
    "search": "Searching Marvin",
    "list_projects": "Listing Marvin projects",
    "list_files": "Listing Marvin files",
    "get_file": "Reading one Marvin file",
}


def _unavailable(missing: marvin_fetch.MarvinCapabilityMissing) -> str:
    """Honest copy for a capability this connection's MCP server does not offer.

    Marvin ships tool subsets per plan and an admin can disable tools, so this
    is a normal state rather than a fault. It must never read as an empty result
    — that is the one failure mode capable of making chat state, with
    confidence, that a research team has studied nothing on a topic they have
    studied at length.
    """
    label = _CAPABILITY_LABEL.get(missing.capability, missing.capability)
    detail = f" ({missing.detail})" if missing.detail else ""
    return (
        f"({label} is UNAVAILABLE on this Marvin connection{detail}: its MCP "
        "server exposes no tool for it. This is NOT a no-results answer — we "
        "could not look. Try the other Marvin tools, and tell the user which "
        "part of Marvin could not be read.)"
    )


def _kickoff_sync(handle: marvin_fetch.MarvinSession) -> None:
    """Queue the normal background ingest once per turn, after a live read.

    What the user just pulled out of Marvin is exactly what the knowledge graph
    should hold, and a live lookup is the strongest possible signal that this
    repository is worth re-reading. Rather than extract inline — which would add
    minutes to a chat turn and put an unreviewed extraction pass inside an
    answer the user is waiting on — this hands the work to the same
    `kickoff_sync` a fresh connection uses. The records land through the normal
    ledger-deduped ingest path a few minutes later, having taken the same route
    as every other Marvin record.

    Once per TURN, not per tool call: a four-tool answer must not start four
    syncs. The flag is set before the call so a failure cannot make it retry,
    and `kickoff_sync` is itself a never-raising daemon-thread spawn.
    """
    if handle.synced or not handle.reached:
        return
    handle.synced = True
    try:
        from app.kg_ingest.auto_sync import kickoff_sync

        kickoff_sync(handle.enterprise_id, marvin_fetch.PROVIDER)
    except Exception:  # noqa: BLE001 — a background sync must never break the answer
        logger.warning(
            "marvin lookup: could not kick off a sync for %s",
            handle.enterprise_id, exc_info=True,
        )


class MarvinProvider:
    """LookupProvider over app/connectors/marvin_fetch.py."""

    provider = marvin_fetch.PROVIDER
    display_name = DISPLAY_NAME
    keywords = ("marvin", "research", "study", "interview")

    def open_session(self, enterprise_id: str) -> LookupSession | None:
        handle = marvin_fetch.open_session(enterprise_id)
        if handle is None:
            return None
        return LookupSession(provider=self.provider, handle=handle)

    def tools(self) -> list[dict]:
        return TOOLS

    def system_block(self) -> str:
        return SYSTEM

    def dispatch(self, session: LookupSession, name: str, inp: dict) -> str:
        handle: marvin_fetch.MarvinSession = session.handle
        if name not in _TOOL_CAPABILITY:
            return f"(unknown tool {name})"
        try:
            out = self._read(handle, name, inp)
        except marvin_fetch.MarvinCapabilityMissing as missing:
            return _unavailable(missing)
        _kickoff_sync(handle)
        return out

    def _read(
        self, handle: marvin_fetch.MarvinSession, name: str, inp: dict
    ) -> str:
        if name == "marvin_search":
            query = (inp.get("query") or "").strip()
            if not query:
                return "(marvin_search: 'query' is required)"
            result, notes = marvin_fetch.run(
                handle, "search",
                (marvin_fetch.Arg(marvin_fetch.QUERY_PARAMS, query),),
            )
            return marvin_fetch.render_rows(
                marvin_fetch.rows_from(result),
                header=f'Marvin search for "{query}":',
                notes=notes,
                empty=(
                    f'(no Marvin research matched "{query}". That means nothing '
                    "matched THIS account's visible research — say so, and "
                    "offer to list the projects instead.)"
                ),
            )

        if name == "marvin_list_projects":
            result, notes = marvin_fetch.run(handle, "list_projects")
            return marvin_fetch.render_rows(
                marvin_fetch.rows_from(result),
                header="Marvin research projects:",
                notes=notes,
                empty=(
                    "(this Marvin workspace has no projects visible to the "
                    "connected account.)"
                ),
            )

        if name == "marvin_list_files":
            project_id = (inp.get("project_id") or "").strip()
            result, notes = marvin_fetch.run(
                handle, "list_files",
                (
                    marvin_fetch.Arg(
                        marvin_fetch.PROJECT_PARAMS, project_id, required=False,
                    ),
                ),
            )
            scope = f" in project {project_id}" if project_id else ""
            return marvin_fetch.render_rows(
                marvin_fetch.rows_from(result),
                header=f"Marvin research files{scope}:",
                notes=notes,
                empty=f"(no Marvin research files{scope} visible to this account.)",
            )

        file_id = (inp.get("file_id") or "").strip()
        if not file_id:
            return "(marvin_get_file: 'file_id' is required)"
        result, _notes = marvin_fetch.run(
            handle, "get_file",
            (marvin_fetch.Arg(marvin_fetch.FILE_PARAMS, file_id),),
        )
        rows = marvin_fetch.rows_from(result)
        if rows:
            return marvin_fetch.render_file(rows[0])
        prose = marvin_fetch.prose_from(result)
        if prose:
            return marvin_fetch.render_file({}, prose=prose)
        return f"(no Marvin research file found with id {file_id})"


PROVIDER = MarvinProvider()

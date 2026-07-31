"""Confluence adapter — live wiki reads from chat.

Read-only by design. Sprntly requests no Confluence write scope at all, so
there is nothing to guard with a confirm-card contract the way the Jira
adapter does for issue edits.

Why this exists at all, given Confluence already syncs into the KG: the KG
holds EXTRACTED SIGNALS — atomic evidence statements the extractor pulled out
of pages — not the pages. "What does our onboarding spec actually say" is a
question about the document, and only a live read can answer it. The two
readers are complementary, and the system block below tells the model which
one it is holding.
"""
from __future__ import annotations

from app.connector_lookup.base import LookupSession
from app.connectors import confluence_fetch

DISPLAY_NAME = "Confluence"

SYSTEM = (
    "Tools:\n"
    "- confluence_search: full-text search across the wiki by `text`, "
    "optionally narrowed to one `space_key`. Returns one line per hit (id, "
    "title, space, last updated, excerpt, link).\n"
    "- confluence_list_pages: recently-updated pages, newest first, optionally "
    "for one `space_key`. Use it to survey what a space contains, or when "
    "search is unavailable.\n"
    "- confluence_list_spaces: the spaces this connection covers.\n"
    "- confluence_get_page: one page in full by its id — the actual body text. "
    "Use it once search or listing has told you which page matters.\n\n"
    "Honest limits you MUST respect:\n"
    "Sprntly reads Confluence AS THE PERSON WHO CONNECTED IT. Space "
    "permissions and per-page restrictions apply, so a page you cannot find "
    "may simply be invisible to that account rather than absent. Say that "
    "rather than concluding the wiki has nothing on a topic.\n"
    "If the workspace selected specific spaces to sync, these tools cover "
    "those spaces only — confluence_list_spaces shows which.\n"
    "If a search result says search is UNAVAILABLE, the connection predates "
    "the search permission: fall back to confluence_list_pages and "
    "confluence_get_page, and tell the user reconnecting Confluence in "
    "Settings → Connectors would enable proper search. Never report "
    "'no results' for a search that could not run — 'we found nothing' and "
    "'we could not look' are different answers.\n"
    "Page bodies are truncated for long pages; the result says so. "
    "This connection is READ-ONLY: you cannot create, edit or comment on a "
    "Confluence page. If asked, say so plainly and offer to summarize instead."
)

SEARCH_TOOL = {
    "name": "confluence_search",
    "description": (
        "Full-text search the company's Confluence wiki. Provide `text` "
        "(keywords matched against page titles and bodies) and optionally "
        "`space_key` to restrict to one space. Returns matching pages and blog "
        "posts, most recently updated first, with an excerpt and link."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Keyword(s) to search for."},
            "space_key": {
                "type": "string",
                "description": "Optional space key (e.g. 'ENG') to restrict the search.",
            },
        },
        "required": ["text"],
    },
}

LIST_PAGES_TOOL = {
    "name": "confluence_list_pages",
    "description": (
        "List recently-updated Confluence pages, newest first. Optionally "
        "restrict to one `space_key`. Use this to survey what the wiki "
        "contains, or when search is unavailable."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "space_key": {
                "type": "string",
                "description": "Optional space key (e.g. 'ENG') to list pages from.",
            },
        },
    },
}

LIST_SPACES_TOOL = {
    "name": "confluence_list_spaces",
    "description": (
        "List the Confluence spaces this connection covers — the workspace's "
        "selected spaces when a selection exists, otherwise every space the "
        "connected account can read."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

GET_PAGE_TOOL = {
    "name": "confluence_get_page",
    "description": (
        "Fetch one Confluence page in full by its id (as returned by "
        "confluence_search or confluence_list_pages): title, status, version, "
        "link and the page's body text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "page_id": {"type": "string", "description": "The Confluence page id."},
        },
        "required": ["page_id"],
    },
}

TOOLS = [SEARCH_TOOL, LIST_PAGES_TOOL, LIST_SPACES_TOOL, GET_PAGE_TOOL]

NOT_CONNECTED = (
    "I can read your Confluence wiki live — search pages, pull a spec in full "
    "— but Confluence isn't connected yet (or its access needs refreshing). "
    "Connect **Confluence** in Settings → Connectors and ask me again."
)

SEARCH_UNAVAILABLE = (
    "(confluence_search is UNAVAILABLE on this connection: it was authorized "
    "before Sprntly asked for the search permission, so full-text search "
    "cannot run. This is NOT a no-results answer. Use confluence_list_pages "
    "and confluence_get_page instead, and tell the user that reconnecting "
    "Confluence in Settings → Connectors would enable search.)"
)


class ConfluenceProvider:
    """LookupProvider over app/connectors/confluence_fetch.py."""

    provider = "confluence"
    display_name = DISPLAY_NAME
    keywords = ("confluence", "wiki")

    def open_session(self, enterprise_id: str) -> LookupSession | None:
        session = confluence_fetch.open_session(enterprise_id)
        if session is None:
            return None
        return LookupSession(provider=self.provider, handle=session)

    def tools(self) -> list[dict]:
        return TOOLS

    def system_block(self) -> str:
        return SYSTEM

    def dispatch(self, session: LookupSession, name: str, inp: dict) -> str:
        handle = session.handle
        if name == "confluence_search":
            text = (inp.get("text") or "").strip()
            if not text:
                return "(confluence_search: 'text' is required)"
            rows, available = confluence_fetch.search_pages(
                handle, text=text, space_key=inp.get("space_key"),
            )
            if not available:
                return SEARCH_UNAVAILABLE
            kept, marker = confluence_fetch.cap(rows)
            return confluence_fetch.render_rows(
                kept, header=f'Confluence search for "{text}":', truncation=marker,
            )
        if name == "confluence_list_pages":
            rows = confluence_fetch.list_pages(
                handle, space_key=inp.get("space_key"),
            )
            kept, marker = confluence_fetch.cap(rows)
            return confluence_fetch.render_rows(
                kept, header="Recently updated Confluence pages:", truncation=marker,
            )
        if name == "confluence_list_spaces":
            rows = confluence_fetch.spaces(handle)
            return confluence_fetch.render_spaces(
                rows, selected=bool(handle.ctx.space_ids),
            )
        if name == "confluence_get_page":
            page_id = (inp.get("page_id") or "").strip()
            if not page_id:
                return "(confluence_get_page: 'page_id' is required)"
            page = confluence_fetch.get_page(handle, page_id)
            if page is None:
                return f"(no Confluence page found with id {page_id})"
            return confluence_fetch.render_page(page)
        return f"(unknown tool {name})"


PROVIDER = ConfluenceProvider()

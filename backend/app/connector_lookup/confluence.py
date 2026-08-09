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

from typing import TYPE_CHECKING

from app.connector_lookup.base import LookupSession
from app.connectors import confluence_fetch

if TYPE_CHECKING:
    from app.kg_ingest.types import RawRecord

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


def _row_to_record(row: dict) -> "RawRecord":
    """One `confluence_fetch.search_pages` row → the CLOSEST `RawRecord` it can
    build with NO new HTTP call.

    NOT byte-identical to `kg_ingest.pullers.confluence.pull`'s record for the
    same page — property KEYS are kept matching the puller's for readability,
    but four of the seven are structurally unavailable from a CQL search hit
    and carry `None` (which `RawRecord.render()` then drops, same as an absent
    key):

      - `space_name`, `status`, `version`, `parent_id`, `author_id` — none of
        these ride the `/rest/api/search` response at all; only the v2
        `/pages`/`/blogposts` GETs the puller and `confluence_get_page` use
        carry them.
      - `text`: a ~240-char CQL `excerpt` (already HTML-stripped, but a
        snippet built by Confluence's own search ranking), not the puller's
        up-to-4,000-char converted page body — a different EXTRACTION, not a
        truncation of the same one.
      - `url`: built from the search result's own `url` field, which is a
        different response field than the puller's `_links.webui` — usually
        the same destination, not guaranteed the same exact path string.
      - `timestamp`: the search API's `lastModified` vs the v2 API's
        `version.createdAt` — two different endpoints' own notion of "when",
        not guaranteed to agree in value or format.

    Closing any of these needs `confluence_get_page` per hit — a new HTTP
    call this module's OWN `dispatch`/`dispatch_records` still may not make
    (sweep.py's latency contract). `enrich_record` below closes it anyway,
    from the sweep-persist background thread ONLY, where that contract does
    not apply — see connector_lookup/sweep_persist.py's module docstring.
    See the AC4 test for the assertion that THIS record and the puller's
    record for the same page do NOT collide (the lean, sweep-time shape);
    the enrichment test proves the ENRICHED one does.
    """
    from app.kg_ingest.types import RawRecord

    return RawRecord(
        provider="confluence",
        kind=row.get("kind") or "page",
        external_id=str(row.get("id") or ""),
        title=row.get("title") or "",
        text=row.get("excerpt") or "",
        properties={
            "space_key": row.get("space"),
            "space_name": None,
            "url": row.get("url"),
            "status": None,
            "version": None,
            "parent_id": None,
            "author_id": None,
        },
        timestamp=row.get("last_modified"),
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

    def dispatch_records(self, session: LookupSession, name: str, inp: dict):
        """`(text, records)` for `confluence_search`, `None` for anything else
        (and `None` when search itself is UNAVAILABLE for this connection —
        there is nothing to build records from). Calls
        `confluence_fetch.search_pages` / `render_rows` exactly as `dispatch`
        does above, so `text` is byte-identical to `dispatch`'s own output by
        construction. See `_row_to_record` for why the records themselves are
        NOT byte-identical to the scheduled pull's."""
        if name != "confluence_search":
            return None
        handle = session.handle
        text_in = (inp.get("text") or "").strip()
        if not text_in:
            return "(confluence_search: 'text' is required)", None
        rows, available = confluence_fetch.search_pages(
            handle, text=text_in, space_key=inp.get("space_key"),
        )
        if not available:
            return SEARCH_UNAVAILABLE, None
        kept, marker = confluence_fetch.cap(rows)
        text = confluence_fetch.render_rows(
            kept, header=f'Confluence search for "{text_in}":', truncation=marker,
        )
        records = [_row_to_record(r) for r in kept] if kept else None
        return text, records


#: Body-text slice — matches `kg_ingest.pullers.confluence._TEXT_CHARS`
#: exactly, so a page's enriched record carries the same `text` cap the
#: scheduled pull would have used.
_PULLER_TEXT_CHARS = 4000


def _space_for_id(handle: "confluence_fetch.ConfluenceSession", space_id) -> dict:
    """Best-effort space lookup by id — the v2 page GET carries only
    `spaceId`, never the space's display name, which the puller-shaped
    record needs for `space_name`. One extra `spaces()` listing per enriched
    hit; simplicity here beats a cross-call cache for the small, top-K-bound
    number of hits sweep_persist ever enriches in one run."""
    if not space_id:
        return {}
    for s in confluence_fetch.spaces(handle):
        if str(s.get("id")) == str(space_id):
            return s
    return {}


def _page_to_puller_record(
    handle: "confluence_fetch.ConfluenceSession", kind: str, item: dict
) -> "RawRecord":
    """Raw v2 page (`confluence_fetch.get_page_raw`) -> the SAME `RawRecord`
    `kg_ingest.pullers.confluence._to_record` builds for it: same
    `properties` KEYS in the SAME order, same `_PULLER_TEXT_CHARS` body
    slice via the SAME `_text_from_body` HTML/ADF-to-markdown conversion,
    same external_id/timestamp fallback.
    """
    from app.kg_ingest.pullers.confluence import _text_from_body
    from app.kg_ingest.types import RawRecord

    ctx = handle.ctx
    page_id = item.get("id")
    title = item.get("title") or ""
    text = _text_from_body(item.get("body"))[:_PULLER_TEXT_CHARS]
    webui = ((item.get("_links") or {}).get("webui")) or ""
    version = item.get("version") or {}
    space = _space_for_id(handle, item.get("spaceId"))
    return RawRecord(
        provider="confluence",
        kind=kind,
        external_id=str(page_id),
        title=title,
        text=text,
        properties={
            "space_key": space.get("key"),
            "space_name": space.get("name"),
            "url": f"{ctx.site_url}{webui}" if (ctx.site_url and webui) else None,
            "status": item.get("status"),
            "version": version.get("number"),
            "parent_id": item.get("parentId"),
            "author_id": item.get("authorId"),
        },
        timestamp=version.get("createdAt") or item.get("createdAt"),
    )


def enrich_record(session: LookupSession, record: "RawRecord") -> "RawRecord":
    """PERSIST-THREAD ONLY (see connector_lookup/sweep_persist.py's module
    docstring for why a per-hit fetch is safe here and NOT in
    `dispatch`/`dispatch_records`, which stay bound by sweep.py's own
    latency contract). One `confluence_get_page`-equivalent raw fetch
    (`confluence_fetch.get_page_raw`, not `get_page` — that one drops
    `spaceId`/`parentId`/`authorId` building the chat tool's own shape) plus
    one `spaces()` listing to resolve `space_name` (AC-A1).

    Raises on any failure other than "page no longer reachable", which is
    handled below by falling back to the lean record. The caller
    (`sweep_persist._enrich_source`) is what isolates a per-hit failure from
    the rest of the source/run (AC-A4).
    """
    handle = session.handle
    raw = confluence_fetch.get_page_raw(handle, record.external_id)
    if raw is None:
        return record  # gone/unreachable since the search — keep the lean record
    return _page_to_puller_record(handle, record.kind, raw)


PROVIDER = ConfluenceProvider()

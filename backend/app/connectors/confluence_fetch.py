"""Confluence read client for the chat live-lookup path.

Sibling of clickup_fetch / jira_fetch: opens a tenant-bound session, runs
bounded reads, and renders each result as text for the model. The KG puller
(kg_ingest/pullers/confluence.py) is the OTHER reader — it sweeps whole spaces
on a schedule; this one answers one question at a time.

THE V1/V2 SPLIT SHOWS UP HERE AS A CAPABILITY GAP:
    Listing and fetching pages are v2 endpoints, covered by the granular
    scopes every connection has. Full-text search is CQL, which exists only on
    v1 and needs the CLASSIC `search:confluence` scope — added to
    CONFLUENCE_SCOPES later than the rest, so a connection made before that
    has a token without it.

    Rather than let that surface as a dead connection, `search_pages` reports
    `available=False` and the adapter tells the model to fall back to listing.
    A capability the token lacks is a fact to state, not an error to raise —
    the alternative is chat claiming a wiki has nothing on a topic when really
    it just couldn't search.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.connector_lookup.base import HTTP_TIMEOUT, cap_items
from app.connectors.confluence_oauth import (
    ConfluenceAuthExpiredError,
    ConfluenceContext,
    ConfluenceNotConnectedError,
    api_get,
    list_spaces,
    sync_context,
)
from app.ingest import html_to_md

logger = logging.getLogger(__name__)

#: Max hits rendered from one search / listing.
SEARCH_LIMIT = 15
#: Body chars rendered for ONE page. Well under base.DEFAULT_RESULT_CHARS so a
#: full page plus its header still fits inside one tool result.
PAGE_BODY_CHARS = 6000
#: Excerpt chars per row in a multi-row result.
EXCERPT_CHARS = 240


@dataclass
class ConfluenceSession:
    """Tenant-bound Confluence access for the duration of one answer."""

    # repr suppressed: carries the bearer token (see base.LookupSession).
    ctx: ConfluenceContext = field(repr=False)


def open_session(enterprise_id: str) -> ConfluenceSession | None:
    """Resolve live access for this tenant, or None. Never raises — a chat
    answer must degrade to "not connected" copy, not a 500."""
    try:
        return ConfluenceSession(ctx=sync_context(enterprise_id))
    except ConfluenceNotConnectedError:
        return None
    except Exception:  # noqa: BLE001 — a bad credential is "not connected" here
        logger.warning(
            "confluence lookup: could not open a session for %s",
            enterprise_id, exc_info=True,
        )
        return None


def _page_url(ctx: ConfluenceContext, item: dict) -> str | None:
    webui = ((item.get("_links") or {}).get("webui")) or ""
    return f"{ctx.site_url}{webui}" if (ctx.site_url and webui) else None


def _body_text(item: dict) -> str:
    """Storage-format XHTML → plain text (see the puller for the same call)."""
    body = item.get("body")
    if not isinstance(body, dict):
        return ""
    node = body.get("storage") or body.get("view") or {}
    value = node.get("value") if isinstance(node, dict) else None
    if not isinstance(value, str) or not value.strip():
        return ""
    return html_to_md(value.encode("utf-8"))


def _excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    flat = " ".join((text or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


# ── Reads ────────────────────────────────────────────────────────────────────


def spaces(session: ConfluenceSession) -> list[dict[str, Any]]:
    """Spaces this connection syncs — the selected ones when a selection
    exists, else everything readable."""
    ctx = session.ctx
    all_spaces = list_spaces(ctx.access_token, ctx.cloud_id)
    if not ctx.space_ids:
        return all_spaces
    return [s for s in all_spaces if s["id"] in set(ctx.space_ids)]


def _space_id_for_key(session: ConfluenceSession, space_key: str | None) -> str | None:
    if not space_key:
        return None
    want = space_key.strip().lower()
    for s in spaces(session):
        if (s.get("key") or "").lower() == want or (s.get("name") or "").lower() == want:
            return s["id"]
    return None


def search_pages(
    session: ConfluenceSession, *, text: str, space_key: str | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """CQL full-text search. Returns `(rows, available)`.

    `available=False` means the token predates the `search:confluence` scope —
    the caller must say search was unavailable rather than report no results,
    because "we found nothing" and "we could not look" are different answers
    and only one of them is honest here.
    """
    ctx = session.ctx
    safe = (text or "").replace('"', '\\"').strip()
    if not safe:
        return [], True
    cql = f'type in (page, blogpost) and text ~ "{safe}"'
    if space_key:
        cql += f' and space.key = "{space_key.strip()}"'
    cql += " order by lastmodified desc"
    try:
        body = api_get(
            ctx.access_token,
            f"{ctx.base}/rest/api/search",
            {"cql": cql, "limit": SEARCH_LIMIT, "expand": "content.space"},
            what="search",
        )
    except ConfluenceAuthExpiredError:
        # The scope gap, not a dead token: every v2 read still works. Reported
        # as a capability fact so the adapter can steer to listing instead.
        logger.info("confluence lookup: search unavailable (no search:confluence scope)")
        return [], False
    rows: list[dict[str, Any]] = []
    for r in body.get("results") or []:
        content = r.get("content") or {}
        rows.append({
            "id": content.get("id"),
            "title": content.get("title") or r.get("title"),
            "kind": content.get("type"),
            "space": ((content.get("space") or {}).get("key")
                      or (r.get("resultGlobalContainer") or {}).get("title")),
            "excerpt": _excerpt(html_to_md((r.get("excerpt") or "").encode("utf-8"))),
            "url": f"{ctx.site_url}{r.get('url')}" if (ctx.site_url and r.get("url")) else None,
            "last_modified": r.get("lastModified"),
        })
    return rows, True


def list_pages(
    session: ConfluenceSession, *, space_key: str | None = None
) -> list[dict[str, Any]]:
    """Recently-updated content, newest first — the fallback when search is
    unavailable, and the natural read for "what's in our wiki".

    Covers PAGES AND BLOGPOSTS. Both this function's callers describe it as
    returning "pages and blog posts" (the search tool's description says so
    outright), but it read only /api/v2/pages, so a blogpost was invisible to
    every listing. That is survivable while search works and is not while it
    doesn't: on a connection authorized before the `search:confluence` scope,
    listing is the ONLY way content is reached, and content the listing cannot
    see is content chat will state does not exist.

    v2 keeps blogposts on their own collection rather than behind a type filter,
    so this is two calls per space merged on last_modified, not one.
    """
    ctx = session.ctx
    targets = spaces(session)
    if space_key:
        sid = _space_id_for_key(session, space_key)
        targets = [s for s in targets if s["id"] == sid] if sid else []
    rows: list[dict[str, Any]] = []
    for s in targets[:10]:
        for collection, kind in (("pages", "page"), ("blogposts", "blogpost")):
            try:
                body = api_get(
                    ctx.access_token, f"{ctx.base}/api/v2/{collection}",
                    {"space-id": s["id"], "sort": "-modified-date",
                     "limit": SEARCH_LIMIT},
                    what=f"list_{collection}",
                )
            except Exception:  # noqa: BLE001
                # One collection failing (a scope that covers pages but not
                # blogposts, a 5xx on one call) must not lose the other's rows.
                logger.warning(
                    "confluence lookup: %s listing failed for space %s",
                    collection, s.get("key"), exc_info=True,
                )
                continue
            for p in body.get("results") or []:
                rows.append({
                    "id": p.get("id"),
                    "title": p.get("title"),
                    "kind": kind,
                    "space": s.get("key"),
                    "url": _page_url(ctx, p),
                    "last_modified": (p.get("version") or {}).get("createdAt"),
                })
    rows.sort(key=lambda r: r.get("last_modified") or "", reverse=True)
    return rows


def get_page(session: ConfluenceSession, page_id: str) -> dict[str, Any] | None:
    """One page or blogpost in full, body included. None when it doesn't exist
    or the connected account can't read it.

    Tries /pages then /blogposts: v2 gives the two collections separate
    endpoints and an id from the wrong one 404s, so a blogpost surfaced by
    list_pages would otherwise be listed and then unreadable — the model would
    report the page as missing while looking straight at its id.
    """
    ctx = session.ctx
    body: dict[str, Any] | None = None
    for collection in ("pages", "blogposts"):
        try:
            body = api_get(
                ctx.access_token, f"{ctx.base}/api/v2/{collection}/{page_id}",
                {"body-format": "storage"}, what="get_page",
            )
        except Exception:  # noqa: BLE001 — a 404 here just means "try the other"
            body = None
        if body and body.get("id"):
            break
    if not body or not body.get("id"):
        return None
    return {
        "id": body.get("id"),
        "title": body.get("title"),
        "status": body.get("status"),
        "version": (body.get("version") or {}).get("number"),
        "last_modified": (body.get("version") or {}).get("createdAt"),
        "url": _page_url(ctx, body),
        "text": _body_text(body)[:PAGE_BODY_CHARS],
    }


# ── Rendering ────────────────────────────────────────────────────────────────


def render_rows(rows: list[dict], *, header: str, truncation: str = "") -> str:
    if not rows:
        return f"({header}: no matching pages)"
    lines = [header]
    for r in rows:
        bits = [f"[{r.get('kind') or 'page'} id={r.get('id')}]", r.get("title") or "(untitled)"]
        if r.get("space"):
            bits.append(f"· space {r['space']}")
        if r.get("last_modified"):
            bits.append(f"· updated {r['last_modified']}")
        lines.append(" ".join(bits))
        if r.get("excerpt"):
            lines.append(f"    {r['excerpt']}")
        if r.get("url"):
            lines.append(f"    {r['url']}")
    if truncation:
        lines.append(truncation)
    return "\n".join(lines)


def render_page(page: dict) -> str:
    head = [
        f"[page id={page.get('id')}] {page.get('title') or '(untitled)'}",
        f"status: {page.get('status')} · version {page.get('version')} "
        f"· updated {page.get('last_modified')}",
    ]
    if page.get("url"):
        head.append(page["url"])
    text = page.get("text") or "(this page has no readable body text)"
    return "\n".join(head) + "\n\n" + text


def render_spaces(rows: list[dict], *, selected: bool) -> str:
    if not rows:
        return "(no Confluence spaces are readable by the connected account)"
    scope = "synced" if selected else "readable"
    lines = [f"Confluence spaces ({scope}):"]
    for s in rows:
        lines.append(f"- {s.get('name') or s.get('key')} (key {s.get('key')}, id {s.get('id')})")
    return "\n".join(lines)


def cap(rows: list[dict]) -> tuple[list[dict], str]:
    return cap_items(rows, SEARCH_LIMIT)


__all__ = [
    "ConfluenceSession", "open_session", "spaces", "search_pages", "list_pages",
    "get_page", "render_rows", "render_page", "render_spaces", "cap",
    "HTTP_TIMEOUT", "SEARCH_LIMIT",
]

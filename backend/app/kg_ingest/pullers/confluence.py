"""Confluence puller — wiki pages + blog posts → RawRecords.

The credential kg_ingest hands us is the owning COMPANY ID, not an access
token (see runner.PULLERS and confluence_oauth.token_payload_to_store). A
Confluence pull needs the site id and the picked spaces as well as the token,
and `runner.token_for` can only pass one field — so the field it passes is the
company id and `confluence_oauth.sync_context` resolves the rest off the
connection row. The `uploads` puller uses the same trick for the same reason.

WHAT WE PULL, AND WHAT WE DON'T:
    pages + blog posts, bodies included. Deliberately excluded for now —
      * comments      one extra request PER PAGE. At 250 pages/space that's a
                      250x request multiplier for content that is mostly
                      "+1" and "LGTM".
      * attachments   binary; the text lives inside the file and would need
                      the app.ingest.convert path. That is the uploads/Drive
                      story, not this one.
      * labels        /pages/{id}/labels is another per-page call.
      * author names  resolving accountId → display name is a per-author call.
                      The raw accountId is carried instead, which is also the
                      GDPR-cleaner thing to hold.
    Everything we DO carry comes free in the list response — no N+1.

COVERAGE IS THE CONNECTING USER'S COVERAGE. 3LO acts as that person, so space
permissions and page restrictions silently bound what this yields. A space
that 404s mid-sync usually means exactly that, which is why it is skipped
rather than raised.

FRESHEST FIRST: pages are requested `sort=-modified-date`, so when a cap
truncates a space we keep what changed most recently. That is also why no
per-space watermark exists — the runner's content-hash ledger already makes an
unchanged page cost zero LLM, and a watermark would add a permanent blind spot
for pages MOVED into a space (old modified-date, never seen again).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from app.connectors.confluence_oauth import (
    ConfluenceAuthExpiredError,
    ConfluenceContext,
    ConfluenceNotConnectedError,
    api_get,
    list_spaces,
    next_cursor,
    sync_context,
)
from app import document_catalog
from app.config import settings
from app.ingest import html_to_md
from app.kg_ingest.recency import within_extraction_window
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

#: Spaces touched per sync — pilot-scale, mirroring github's repo cap.
_MAX_SPACES = 25
#: Page size for content listings. Smaller than the Jira puller's 100 because
#: these responses carry BODIES: 100 long pages is a multi-megabyte payload and
#: a fat unit against Atlassian's points-based rate budget.
_PAGE_SIZE = 50
#: Per-record extraction budget. Under the runner's 6000-char batch budget so
#: one record always fits in one batch. Long specs are truncated rather than
#: chunked in v1; pullers/uploads.py::_chunks is the ready-made follow-up.
_TEXT_CHARS = 4000
#: KG-extraction yield budget PER (space, kind). Once a (space, kind) walk has
#: yielded this many in-window records it stops YIELDING but keeps walking +
#: cataloguing, so a large space cannot spend a later space's extraction budget
#: (the starvation the old flat global cap caused). The 18-month window is the
#: real extraction boundary; this is the fairness backstop under it.
#: Raised from 200 on 2026-08-16, with the other pullers' pilot-scale caps.
#: This one was already the least harmful of them — it is a per-(space, kind)
#: FAIRNESS backstop under an 18-month window, not the history boundary — so it
#: moves less far than the others.
_MAX_EXTRACT_RECORDS_PER_SPACE = 500
#: First-scan volume guardrail: DOCUMENTS catalogued per (space, kind) before
#: the deep walk is bounded and LOGGED (never silently truncated). A DOCUMENT
#: count, decoupled from _PAGE_SIZE — this replaces the old 5-results-pages
#: ceiling with a higher-but-bounded one so "a map of everything" is more fully
#: catalogued while a pathologically huge tenant's first scan stays bounded.
_MAX_CATALOG_DOCS_PER_SPACE = 1000

#: kind → (v2 collection path, space filter param). Both listings accept the
#: same sort/body-format/cursor vocabulary.
_CONTENT_KINDS: tuple[tuple[str, str], ...] = (
    ("page", "pages"),
    ("blogpost", "blogposts"),
)


def _plain_text_from_adf(node: object) -> str:
    """Flatten an Atlassian Document Format doc into plain text.

    Carried locally rather than imported from jira_oauth: pullers/jira.py
    already duplicates its own copy, and keeping it here means a test can
    monkeypatch this module in isolation."""
    if not isinstance(node, dict):
        return ""
    out: list[str] = []
    if node.get("type") == "text" and isinstance(node.get("text"), str):
        out.append(node["text"])
    for child in node.get("content", []) or []:
        out.append(_plain_text_from_adf(child))
    return " ".join(p for p in out if p)


def _text_from_body(body: object) -> str:
    """Confluence bodies are never plain text. Three representations:

      storage             XHTML-ish, the canonical stored form and what we ask
                          for. BeautifulSoup (via app.ingest.html_to_md) drops
                          the <ac:*>/<ri:*> macro tags and keeps their text.
      atlas_doc_format    ADF — and note the doc arrives as a JSON *string*
                          inside `value`, so it needs a json.loads first.
      view                rendered HTML; handled by the same HTML path.

    Lossy for rich marks, which is what the extractor wants."""
    if not isinstance(body, dict):
        return ""
    node = (
        body.get("storage")
        or body.get("view")
        or body.get("atlas_doc_format")
        or body
    )
    value = node.get("value") if isinstance(node, dict) else None
    if not isinstance(value, str) or not value.strip():
        return ""
    representation = node.get("representation") if isinstance(node, dict) else ""
    if representation == "atlas_doc_format":
        try:
            return _plain_text_from_adf(json.loads(value))
        except (TypeError, ValueError):
            logger.info("confluence: unparseable ADF body — skipping its text")
            return ""
    return html_to_md(value.encode("utf-8"))


def _select_spaces(ctx: ConfluenceContext, spaces: list[dict]) -> list[dict]:
    """Narrow the readable spaces to the workspace's selection.

    An EMPTY selection means every readable space — the backwards-compatible
    default that lets a connection made before the picker existed keep working
    (same rule as slack_sync.select_sync_channels). Selected ids that no longer
    resolve are logged by key and skipped: the space was deleted, or the
    connecting account lost access to it."""
    if not ctx.space_ids:
        return spaces
    by_id = {s["id"]: s for s in spaces}
    chosen = [by_id[sid] for sid in ctx.space_ids if sid in by_id]
    missing = [sid for sid in ctx.space_ids if sid not in by_id]
    if missing:
        logger.info(
            "confluence: %d selected space(s) are no longer readable — %s",
            len(missing),
            ", ".join(ctx.space_keys.get(sid, sid) for sid in missing),
        )
    return chosen


def _content_records(
    ctx: ConfluenceContext, space: dict, kind: str, path: str
) -> Iterator[RawRecord]:
    """Walk one (space, kind) newest-first, cataloguing EVERY page and yielding
    only the recent ones for KG extraction.

    Two jobs, decoupled. `_to_record` registers EVERY walked page to the
    document catalog (Tier 3 — findable forever) and returns its RawRecord.
    This walk then yields that record for KG extraction ONLY when the page is
    inside the recency window AND the per-(space, kind) extraction budget is not
    yet spent; out-of-window / over-budget pages are catalogued then skipped
    (`continue`), never `break`, so cataloguing keeps going.

    The walk itself is bounded by `_MAX_CATALOG_DOCS_PER_SPACE` DOCUMENTS (not a
    results-page count): on hitting that ceiling it emits exactly one WARNING and
    stops — a bounded, LOGGED first-scan guardrail, never a silent truncation."""
    cursor: str | None = None
    catalogued = 0
    extracted = 0
    window_months = settings.kg_extraction_window_months
    while True:
        params: dict[str, Any] = {
            "space-id": space["id"],
            "sort": "-modified-date",
            "body-format": "storage",
            "limit": _PAGE_SIZE,
        }
        if cursor:
            params["cursor"] = cursor
        body = api_get(
            ctx.access_token, f"{ctx.base}/api/v2/{path}", params,
            what=f"list_{path}",
        )
        results = body.get("results") or []
        for item in results:
            record = _to_record(ctx, space, kind, item)
            if record is None:
                # Nothing to catalog or extract (no id, or empty title+body).
                continue
            catalogued += 1
            if (
                extracted < _MAX_EXTRACT_RECORDS_PER_SPACE
                and within_extraction_window(record.timestamp, window_months)
            ):
                extracted += 1
                yield record
            if catalogued >= _MAX_CATALOG_DOCS_PER_SPACE:
                logger.warning(
                    "confluence: catalog walk hit the %d-document ceiling for "
                    "company %s space %s (kind=%s) — deep scan bounded, not "
                    "truncated silently; narrow the space selection to cover "
                    "the rest",
                    _MAX_CATALOG_DOCS_PER_SPACE, ctx.company_id,
                    space.get("key") or space.get("id"), kind,
                )
                return
        cursor = next_cursor(body)
        if not cursor or not results:
            break


def _to_record(
    ctx: ConfluenceContext, space: dict, kind: str, item: dict
) -> RawRecord | None:
    """One listing entry → RawRecord, or None when there is nothing to extract.

    `version.number` rides in properties on purpose, beyond being useful: the
    runner hashes `RawRecord.render()`, which folds properties in — so a
    version bump alone yields a new hash and the page is re-extracted. That is
    how an edit that doesn't change the body we captured (a retitle, a move)
    still reaches the graph."""
    page_id = item.get("id")
    if not page_id:
        return None
    title = item.get("title") or ""
    # The FULL converted body, kept separately from the extraction slice
    # below. Both legs want different things out of the same bytes and the
    # difference is 4,000 chars vs 200,000 — see the registration comment.
    full_text = _text_from_body(item.get("body"))
    text = full_text[:_TEXT_CHARS]
    if not title.strip() and not text.strip():
        return None

    webui = ((item.get("_links") or {}).get("webui")) or ""
    version = item.get("version") or {}
    url = f"{ctx.site_url}{webui}" if (ctx.site_url and webui) else None

    # Catalog registration lives HERE, inside the puller, and uses `full_text`
    # — NOT `text`, and NOT the generic runner's record loop.
    #
    # The runner only ever sees `RawRecord.text`, which is already sliced to
    # _TEXT_CHARS for the extraction batch budget. Registering from there (the
    # natural reading of "register during record processing") would summarise
    # only the first 4,000 characters of a long spec, and — worse — take the
    # content hash over that same truncated text, so two revisions of a long
    # page differing only past the 4k mark would hash identically, the page
    # would never re-summarise, and its catalog entry would be frozen at the
    # first version forever. Nothing else in the system would report either
    # fault.
    #
    # The extraction cap itself is unchanged and still correct: `text` below
    # is what goes to the KG.
    try:
        document_catalog.register_document(
            ctx.company_id,
            provider=document_catalog.PROVIDER_CONFLUENCE,
            external_id=str(page_id),
            title=title,
            source_name=space.get("name") or space.get("key") or "",
            # The SPACE ID, and specifically not the key or the name. This is
            # what makes a deselected space's pages removable from the
            # catalog: the selection stored by POST /connectors/confluence/
            # spaces is a list of space IDS, so a stored id joins to it
            # directly. `source_name` above is the display name and joins to
            # nothing; the key is renameable and so is the site URL the key
            # could be parsed back out of. Only the id survives a rename.
            container_id=str(space.get("id") or "") or None,
            url=url,
            doc_date=version.get("createdAt") or item.get("createdAt"),
            # Over the FULL body, and this is the part that must not regress
            # now that the body itself is not stored. Hashing the truncated
            # text would make two revisions that differ only past the 4,000-
            # char mark hash identically — the page would never re-summarise,
            # freezing its catalog entry at the first version forever.
            content_hash=document_catalog.content_hash_for(full_text),
            # The summariser gets the full body; the catalog keeps only what
            # it produces. No `body_text` — the catalog is a POINTER to a
            # document (summary, topics, url), never a COPY of it, so Sprntly
            # holds no duplicate of the customer's wiki at rest.
            get_text=lambda: full_text,
        )
    except Exception:  # noqa: BLE001 — a sync that succeeds today must still
        # succeed if cataloguing fails. (Drive is the deliberate exception —
        # there a swallowed failure would strand the file permanently.)
        logger.warning(
            "confluence: catalog registration failed for page %s; the pull "
            "continues", page_id, exc_info=True,
        )
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


def pull(company_id: str) -> Iterator[RawRecord]:
    """Yield a RawRecord per page/blog post across the company's synced spaces.

    Error-isolated per space (an unreadable one is logged and skipped) — but if
    EVERY space failed and nothing was yielded, the last error is re-raised.
    Otherwise a revoked grant would report a cheerful zero-record sync, which
    looks identical to "this wiki is empty" on the connection row.
    """
    try:
        ctx = sync_context(company_id)
    except ConfluenceNotConnectedError as e:
        logger.warning("confluence puller: %s — nothing to pull", e)
        return

    spaces = _select_spaces(ctx, list_spaces(ctx.access_token, ctx.cloud_id))
    if not spaces:
        logger.info("confluence puller: no readable spaces for %s", company_id)
        return

    yielded = False
    last_error: Exception | None = None
    for space in spaces[:_MAX_SPACES]:
        try:
            for kind, path in _CONTENT_KINDS:
                # Per-(space, kind) fairness + catalog ceiling live inside the
                # walk; no space starves a later one and there is no global
                # list-order cap to end the sync early.
                for record in _content_records(ctx, space, kind, path):
                    yielded = True
                    yield record
        except ConfluenceAuthExpiredError:
            raise  # never swallow a reconnect signal behind per-space isolation
        except Exception as e:  # noqa: BLE001 — one bad space must not end the sync
            logger.info(
                "confluence: skipping space %s: %s", space.get("key") or space["id"], e
            )
            last_error = e
    if not yielded and last_error is not None:
        raise last_error

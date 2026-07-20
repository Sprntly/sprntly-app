"""Internal service-to-service API for the MCP server (mcp/).

Same trust model as app/routes/internal.py (the DS-Agent's internal API):
gated by X-Internal-Key, no session cookies or JWTs — purely machine-to-
machine. `mcp/` resolves a customer's bearer token to a company_id via
`/mcp-tokens/resolve`, then calls the data routes below passing that
company_id explicitly as a query param — it never derives company_id from
untrusted client input, and these routes never accept it from anywhere else.

These are thin wrappers around the SAME service functions the /v1/* routes
already call — no business-logic duplication, only route-wiring duplication
(matching the shape of app/routes/internal.py itself).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

# The web ticket view renders an attachment's `sub` directly as an anchor href
# (web/.../TicketDetail.tsx). Now that attachments are AI/token-writable, reject
# script-y URL schemes at the write boundary so a prompt-injected client can't
# store a link that runs script when a teammate clicks it in the app.
_UNSAFE_URL_SCHEME = re.compile(r"^\s*(?:javascript|data|vbscript|file):", re.IGNORECASE)

from app.db.companies import slug_for_company_id
from app.db.mcp_tokens import resolve_mcp_token
from app.routes.internal import _require_internal_key

logger = logging.getLogger(__name__)

# ── Ticket keys ──
#
# The web app composes each ticket's key CLIENT-SIDE (`ticketKeyFor` in
# web/app/components/shared/TicketDetail.tsx): "prd-{prd_id}-{story.id}", with
# a title-slug fallback for legacy stories generated before ids existed. Every
# ticket_edits / ticket_comments / ticket_attachments row the web writes is
# keyed by that composed string, while prd_tickets.stories only stores the bare
# content-hash id. These helpers make the MCP surface speak the SAME composed
# format so both surfaces read and write the same rows; bare keys written by
# older MCP clients still resolve their base story (their orphaned override
# rows stay under the bare key — accepted, no migration).

_TICKET_KEY_RX = re.compile(r"^prd-(\d+)-(.+)$")


def _title_slug(title: str | None) -> str:
    """Mirror of the web's legacy slug fallback: lowercase, non-alphanumeric
    runs → '-', strip leading/trailing '-', first 60 chars, default 'ticket'."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "ticket").lower()).strip("-")[:60]
    return slug or "ticket"


def _ticket_key_for(prd_id: int | None, story: dict) -> str:
    """The web-format ticket key for a generated story (`ticketKeyFor` mirror)."""
    sid = story.get("id")
    if sid:
        return f"prd-{prd_id}-{sid}"
    return f"prd-{prd_id}-{_title_slug(story.get('title'))}"


def _parse_ticket_key(ticket_key: str) -> tuple[int | None, str]:
    """Split a web-format key into (prd_id, story_ref). A key without the
    prd- prefix passes through as (None, key) so legacy bare story ids keep
    resolving."""
    m = _TICKET_KEY_RX.match(ticket_key)
    if not m:
        return None, ticket_key
    return int(m.group(1)), m.group(2)


def _find_story_by_slug(
    c, company_id: str, prd_id: int, slug: str
) -> tuple[dict | None, int | None]:
    """Locate a legacy id-less story by the title slug its web key embeds."""
    rows = (
        c.table("prd_tickets")
        .select("prd_id, stories")
        .eq("company_id", company_id)
        .eq("prd_id", prd_id)
        .execute()
        .data
        or []
    )
    for row in rows:
        for story in row.get("stories") or []:
            if (
                isinstance(story, dict)
                and not story.get("id")
                and _title_slug(story.get("title")) == slug
            ):
                return story, row.get("prd_id")
    return None, None

resolve_router = APIRouter(prefix="/internal/mcp-tokens", tags=["internal-mcp"])
data_router = APIRouter(prefix="/internal/mcp", tags=["internal-mcp"])


class ResolveTokenBody(BaseModel):
    token: str


@resolve_router.post("/resolve", dependencies=[Depends(_require_internal_key)])
def resolve_token(body: ResolveTokenBody) -> dict[str, Any]:
    ctx = resolve_mcp_token(body.token)
    if not ctx:
        raise HTTPException(401, "invalid_or_revoked_token")
    return ctx


@data_router.get("/datasets", dependencies=[Depends(_require_internal_key)])
def datasets(company_id: str) -> dict[str, Any]:
    """The one dataset belonging to this company (never other tenants' rows)."""
    from app.db.datasets import get_dataset

    slug = slug_for_company_id(company_id)
    row = get_dataset(slug) if slug else None
    return {"datasets": [row] if row else []}


@data_router.get("/brief/current", dependencies=[Depends(_require_internal_key)])
def brief_current(company_id: str) -> dict[str, Any]:
    from app.db.briefs import get_current_brief

    slug = slug_for_company_id(company_id)
    brief = get_current_brief(slug) if slug else None
    if not brief:
        raise HTTPException(404, "no_brief_generated_yet")
    return brief


@data_router.get("/ideation", dependencies=[Depends(_require_internal_key)])
def ideation(company_id: str) -> dict[str, Any]:
    from app.db.ideation import list_visible_ideation_items
    from app.db.briefs import get_current_brief

    # Empty-when-no-brief invariant, mirrored from routes/ideation.py: the
    # ideation pool is the by-product of a weekly brief, so no brief -> no
    # ideas. Returns the same visible set the page shows (the weekly shortlist
    # + user-pinned rows), not the hidden tail.
    slug = slug_for_company_id(company_id)
    if not slug or not get_current_brief(slug):
        return {"items": [], "count": 0}
    items = list_visible_ideation_items(company_id)
    return {"items": items, "count": len(items)}


@data_router.get("/prd/latest", dependencies=[Depends(_require_internal_key)])
def prd_latest(company_id: str) -> dict[str, Any]:
    from app.db.prds import get_prd_rendered, latest_prd_for_dataset

    slug = slug_for_company_id(company_id)
    row = latest_prd_for_dataset(slug) if slug else None
    if not row:
        raise HTTPException(404, "no_prd_found")
    return get_prd_rendered(row["id"]) or row


# NOTE: registered AFTER /prd/latest so the static path wins; prd_id is int-typed
# so "latest" could never match this route anyway.
@data_router.get("/prd/{prd_id}", dependencies=[Depends(_require_internal_key)])
def prd_by_id(prd_id: int, company_id: str) -> dict[str, Any]:
    """A specific PRD by id — the parent context of a ticket (its `prd_id`
    comes from list_tickets / get_ticket). Tenant-scoped via require_owned_prd
    (prd → brief → dataset → company); 404 on a foreign/missing id so cross-
    tenant existence is never disclosed."""
    from app.db.prds import get_prd_rendered
    from app.deps.ownership import require_owned_prd

    require_owned_prd(prd_id, company_id)  # raises 404 if not this company's
    return get_prd_rendered(prd_id) or {}


@data_router.get(
    "/prd/{prd_id}/prototype", dependencies=[Depends(_require_internal_key)]
)
def prd_prototype(prd_id: int, company_id: str) -> dict[str, Any]:
    """The design prototype behind a PRD: status + viewer links, for the MCP
    get_prd_prototype tool. Latest row of ANY status so a developer sees
    "generating"/"failed" too, not just a bare 404.

    Links only — never the signed bundle_url (it expires and bypasses the
    share model) and never the passcode hash. `app_url` is the in-app viewer
    (needs a Sprntly login); `public_url` exists only when a PM already
    shared the prototype (share_mode public/passcode) — this surface never
    changes share settings. Tenant-scoped via require_owned_prd, and the
    prototype lookup itself is workspace-filtered (prototypes.workspace_id
    IS the company_id)."""
    from app.config import settings
    from app.db.companies import slug_for_company_id
    # #683 renamed find_latest_prototype_by_prd → find_prototype_by_prd (statuses=None
    # keeps the "any status, newest wins" behavior this route needs); #692 shipped
    # the stale name, so the route ImportError'd at call time. See issue #697.
    from app.db.prototypes import find_prototype_by_prd
    from app.deps.ownership import require_owned_prd

    require_owned_prd(prd_id, company_id)  # raises 404 if not this company's
    row = find_prototype_by_prd(prd_id=prd_id, workspace_id=company_id)
    if not row:
        raise HTTPException(404, "no_prototype")

    frontend = (settings.frontend_url or "").rstrip("/")
    share_mode = row.get("share_mode") or "private"
    public_url = None
    if share_mode != "private" and row.get("share_token"):
        slug = slug_for_company_id(company_id)
        if slug:
            public_url = f"{frontend}/p/{slug}/{row['share_token']}"

    return {
        "prototype_id": row["id"],
        "prd_id": prd_id,
        "status": row.get("status"),
        "is_complete": bool(row.get("is_complete")),
        "target_platform": row.get("target_platform"),
        "preview_image_url": row.get("preview_image_url"),
        "created_at": row.get("created_at"),
        "completed_at": row.get("completed_at"),
        "error": row.get("error"),
        "app_url": f"{frontend}/prototype?prd={prd_id}",
        "share_mode": share_mode,
        "public_url": public_url,
    }


# Evidence content can be a large self-contained HTML brief (variant v3) —
# cap what rides back through the MCP so one tool call can't flood an AI
# client's context. Ready markdown briefs are far below this.
_EVIDENCE_CONTENT_CAP = 150_000


@data_router.get(
    "/prd/{prd_id}/evidence", dependencies=[Depends(_require_internal_key)]
)
def prd_evidence(prd_id: int, company_id: str) -> dict[str, Any]:
    """The research evidence behind a PRD — the provenance for WHY this PRD
    exists, for the MCP get_prd_evidence tool.

    A PRD is generated from one brief insight (prds.brief_id +
    prds.insight_index), and evidence rows are keyed by that same pair, so
    the PRD row is the join. Variant-permissive (newest ready/generating row
    of any format era) like GET /v1/evidence/{id}; 404 when none exists yet.
    Tenant-scoped via require_owned_prd (prd → brief → dataset → company)."""
    from app.db.evidences import find_latest_evidence
    from app.deps.ownership import require_owned_prd

    prd = require_owned_prd(prd_id, company_id)  # raises 404 if not this company's
    row = find_latest_evidence(prd["brief_id"], prd["insight_index"])
    if not row:
        raise HTTPException(404, "no_evidence")

    content = row.get("payload_md") or ""
    truncated = len(content) > _EVIDENCE_CONTENT_CAP
    return {
        "evidence_id": row["id"],
        "prd_id": prd_id,
        "title": row.get("title"),
        "status": row.get("status"),
        "variant": row.get("variant"),
        # v3 rows hold a self-contained HTML visual brief; earlier variants
        # are markdown. Tell the client which it's reading.
        "content_format": "html" if row.get("variant") == "v3" else "markdown",
        "content": content[:_EVIDENCE_CONTENT_CAP],
        "content_truncated": truncated,
    }


@data_router.get(
    "/tickets/{ticket_key}/data", dependencies=[Depends(_require_internal_key)]
)
def ticket_data(ticket_key: str, company_id: str) -> dict[str, Any]:
    """Full current ticket = generated base content (from prd_tickets.stories)
    merged with per-ticket overrides (ticket_edits) + comments + attachments.

    A developer needs the generated title / description / acceptance criteria /
    scope to implement the ticket; those live in the base story, so returning
    only the overrides (as this route once did) left an unedited ticket looking
    empty. Overrides win where set; base-story context fields (what/why/scope/
    subtasks/labels) are always included.

    `ticket_key` is the web-format key ("prd-{prd_id}-{story_id}") so the
    override rows this route reads are the SAME rows the web app writes; the
    embedded story id locates the generated base story. Bare legacy keys (the
    raw story id) still resolve the base story."""
    from app.db.client import require_client
    from app.db.prd_tickets import find_ticket_story

    c = require_client()
    prd_hint, story_ref = _parse_ticket_key(ticket_key)
    story, prd_id = find_ticket_story(company_id, story_ref)
    if story is None and prd_hint is not None:
        # Legacy id-less story: the web key embeds a title slug, not a story id.
        story, prd_id = _find_story_by_slug(c, company_id, prd_hint, story_ref)
    if prd_id is None:
        prd_id = prd_hint

    edit_resp = (
        c.table("ticket_edits")
        .select("*")
        .eq("company_id", company_id)
        .eq("ticket_key", ticket_key)
        .limit(1)
        .execute()
    )
    edit = edit_resp.data[0] if edit_resp.data else None

    attach_resp = (
        c.table("ticket_attachments")
        .select("*")
        .eq("company_id", company_id)
        .eq("ticket_key", ticket_key)
        .order("created_at")
        .execute()
    )
    comment_resp = (
        c.table("ticket_comments")
        .select("*")
        .eq("company_id", company_id)
        .eq("ticket_key", ticket_key)
        .order("created_at")
        .execute()
    )

    # 404 only when there is NO trace of this ticket for the company — no
    # generated story, no edit, no comments, no attachments (the tool turns
    # the 404 into a friendly "not found" message).
    if (
        story is None
        and edit is None
        and not (attach_resp.data or [])
        and not (comment_resp.data or [])
    ):
        raise HTTPException(404, "ticket_not_found")

    story = story or {}
    edit = edit or {}

    def _merged(field: str):
        """Override value when set (non-null), else the base story's value."""
        v = edit.get(field)
        return v if v is not None else story.get(field)

    # Tracker sync state (passive read): where this PRD's tickets sync to and
    # this ticket's last-pulled tracker status/url. None when never pushed.
    # Kept read-only on the MCP surface — syncs are triggered from the web /
    # scheduler; an MCP edit is picked up by the next sync pass automatically.
    tracker = None
    if prd_id is not None:
        from app.db.ticket_sync import get_sync_config

        cfg = get_sync_config(company_id, prd_id)
        if cfg:
            sid = story.get("id") or story_ref
            st = (cfg.get("statuses") or {}).get(sid) or {}
            tracker = {
                "provider": cfg.get("provider"),
                "destination_name": cfg.get("destination_name"),
                "status": st.get("status"),
                "assignee": st.get("assignee"),
                "url": st.get("url"),
                "last_synced_at": cfg.get("last_synced_at"),
                "last_error": cfg.get("last_error"),
                # Pulled custom-field values (normalized, keyed by field id);
                # a local override in ticket_edits.custom_fields wins over
                # these — see the top-level `custom_fields` key below.
                "custom_fields": st.get("custom_fields") or None,
            }
            # The destination's REAL status vocabulary (tracker-native), so an
            # AI client sets statuses this workspace actually has. Absent when
            # metadata was never fetched — canonical open/in progress/done
            # still resolve on write either way.
            try:
                from app.db.tracker_meta import get_cached_meta

                meta = get_cached_meta(
                    company_id, cfg["provider"], cfg["destination_id"]
                )
                if meta:
                    tracker["allowed_statuses"] = [
                        s.get("name") for s in meta.get("statuses") or []
                    ]
                    tracker["allowed_priorities"] = [
                        p.get("name") for p in meta.get("priorities") or []
                    ]
            except Exception:  # noqa: BLE001 — vocabulary hints are best-effort
                pass

    return {
        "tracker": tracker,
        "id": ticket_key,
        "prd_id": prd_id,
        "title": _merged("title"),
        # Description: an explicit edit wins; else the generated story body.
        "description": edit.get("description")
        if edit.get("description") is not None
        else story.get("body"),
        "acceptance_criteria": _merged("acceptance_criteria"),
        # Base stories carry no status; an unedited ticket's canonical status is
        # "Backlog" (matches the web UI's null→Backlog default), so filters and
        # the AI see a real value rather than null.
        "status": edit.get("status") or "Backlog",
        "priority": _merged("priority"),
        "sprint": edit.get("sprint"),
        "assignee": edit.get("assignee"),
        "ticket_type": story.get("ticket_type"),
        # Generated context a developer needs to implement the ticket.
        "what": story.get("what"),
        "why_now": story.get("why_now"),
        "user_story": story.get("user_story"),
        "scope": story.get("scope"),
        "out_of_scope": story.get("out_of_scope"),
        # Subtasks are editable in the web panel (ticket_edits.subtasks) —
        # override wins, same merge as title/priority.
        "subtasks": _merged("subtasks"),
        # Local custom-field overrides (tracker vocabulary; the tracker block
        # above carries the last-pulled values for fields without overrides).
        "custom_fields": edit.get("custom_fields"),
        "issue_type": edit.get("issue_type"),
        "labels": story.get("labels"),
        "attachments": [
            {"id": a["id"], "label": a["label"], "sub": a["sub"]}
            for a in (attach_resp.data or [])
        ],
        "comments": [
            {
                "id": c_row["id"],
                "author": c_row["author"],
                "body": c_row["body"],
                "time": str(c_row["created_at"]),
            }
            for c_row in (comment_resp.data or [])
        ],
    }


@data_router.get("/tickets", dependencies=[Depends(_require_internal_key)])
def list_tickets(
    company_id: str,
    status: str | None = None,
    ticket_type: str | None = None,
    assignee_user_id: str | None = None,
    prd_id: int | None = None,
) -> dict[str, Any]:
    """Every ticket for a company, flattened across PRDs, with each ticket's
    CURRENT status merged in (from ticket_edits) so a developer sees state at a
    glance. Optional `status` / `ticket_type` filters (case-insensitive).

    `assignee_user_id` narrows to tickets whose ticket_edits.assignee has that
    user_id — the MCP server passes the TOKEN OWNER's id so an AI client only
    sees the caller's own tickets. Assignment exists only as an edit (base
    stories carry none), so unassigned tickets never match this filter.

    `prd_id` narrows to one PRD's tickets (the MCP list_prd_tickets tool).
    The query is company-scoped BEFORE this filter, so a foreign prd_id can
    only ever match nothing — it returns an empty list, never another
    tenant's tickets.

    Tickets are elements of each PRD's `prd_tickets.stories` array. Each is
    returned under its WEB-FORMAT key ("prd-{prd_id}-{story_id}", the same key
    the web app composes and stores edits/comments under), so the status merge
    below sees web edits and the key round-trips into every /tickets/{key}
    route. Full per-ticket detail comes from GET /tickets/{key}/data.
    """
    from app.db.client import require_client

    c = require_client()
    rows = (
        c.table("prd_tickets")
        .select("prd_id, stories")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    # One query for all this company's edits → map ticket_key → override fields,
    # so the list reflects edited status/priority/title without N round-trips.
    edits = (
        c.table("ticket_edits")
        .select("ticket_key, status, priority, title, assignee")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    edit_by_key = {e["ticket_key"]: e for e in edits}

    # One query for all this company's tracker-sync rows → per-PRD provider +
    # per-ticket pulled tracker state, so each listed ticket can carry its
    # tracker status/url without N lookups.
    from app.db.ticket_sync import list_sync_configs

    sync_by_prd = {c["prd_id"]: c for c in list_sync_configs(company_id)}

    want_status = status.strip().lower() if status else None
    want_type = ticket_type.strip().lower() if ticket_type else None

    tickets: list[dict[str, Any]] = []
    for row in rows:
        if prd_id is not None and row.get("prd_id") != prd_id:
            continue
        for story in row.get("stories") or []:
            if not isinstance(story, dict):
                continue
            key = _ticket_key_for(row.get("prd_id"), story)
            e = edit_by_key.get(key, {})
            # Unedited status defaults to "Backlog" (as in get_ticket / the web
            # UI) so the recommended `status=Backlog` filter actually finds the
            # generated-but-unedited backlog. title/priority use is-not-None
            # (not `or`) to match get_ticket's merge exactly — an explicit ""
            # edit wins consistently across both surfaces.
            cur_status = e.get("status") or "Backlog"
            cur_type = story.get("ticket_type")
            if want_status and cur_status.lower() != want_status:
                continue
            if want_type and (cur_type or "").lower() != want_type:
                continue
            if assignee_user_id:
                assignee = e.get("assignee")
                if (
                    not isinstance(assignee, dict)
                    or assignee.get("user_id") != assignee_user_id
                ):
                    continue
            sync_cfg = sync_by_prd.get(row.get("prd_id")) or {}
            tracker_state = (sync_cfg.get("statuses") or {}).get(story.get("id")) or {}
            tickets.append(
                {
                    "id": key,
                    "title": e["title"] if e.get("title") is not None else story.get("title"),
                    "ticket_type": cur_type,
                    "status": cur_status,
                    "priority": e["priority"] if e.get("priority") is not None else story.get("priority"),
                    "prd_id": row.get("prd_id"),
                    # Tracker sync (when this PRD's tickets were pushed): the
                    # tool the PRD syncs with and this ticket's last-pulled
                    # status/url there. None/absent fields when never pushed.
                    "tracker_provider": sync_cfg.get("provider"),
                    "tracker_status": tracker_state.get("status"),
                    "tracker_url": tracker_state.get("url"),
                }
            )
    return {"tickets": tickets, "count": len(tickets)}


class TicketDescriptionIn(BaseModel):
    description: str = ""
    # None (omitted) = leave the ticket's existing/generated acceptance criteria
    # untouched; a list (incl. []) = an explicit replacement. This is why the
    # route writes it only when non-None — a description-only update must not
    # silently wipe generated criteria.
    acceptance_criteria: list[str] | None = None


class TicketFieldsIn(BaseModel):
    """All optional — only the fields actually sent are written (exclude_unset),
    so a partial update never clobbers the description or the untouched fields
    on the same ticket_edits row. Mirrors routes/tickets.py:FieldsIn EXCEPT
    `assignee`: assignment is web-only by product decision — an AI client must
    not (re)assign people, so the field doesn't exist on this model and any
    assignee sent by an older MCP client is silently dropped."""

    title: str | None = None
    priority: str | None = None
    status: str | None = None
    sprint: str | None = None
    # Tracker custom-field overrides, keyed by field id (normalized value
    # shapes — see app/connectors/tracker_meta.py; get_ticket's tracker block
    # lists the editable fields). Merged over the stored map; null clears one
    # field's override.
    custom_fields: dict[str, Any] | None = None
    # Tracker issue type (validated against the destination's issue_types).
    issue_type: str | None = None


class TicketCommentIn(BaseModel):
    body: str = Field(..., min_length=1)


class TicketAttachmentIn(BaseModel):
    label: str = Field(..., min_length=1)
    sub: str = ""


@data_router.put(
    "/tickets/{ticket_key}/description",
    dependencies=[Depends(_require_internal_key)],
)
def save_ticket_description(
    ticket_key: str, company_id: str, body: TicketDescriptionIn
) -> dict[str, Any]:
    """Upsert a ticket's description; replace acceptance criteria only when
    explicitly provided (None = leave the existing/generated criteria intact,
    so a description-only edit doesn't wipe them)."""
    from app.db.client import require_client, utc_now

    payload = {
        "company_id": company_id,
        "ticket_key": ticket_key,
        "description": body.description,
        "updated_at": utc_now(),
    }
    if body.acceptance_criteria is not None:
        payload["acceptance_criteria"] = body.acceptance_criteria
    require_client().table("ticket_edits").upsert(
        payload, on_conflict="company_id,ticket_key"
    ).execute()
    # Instant push: a bound ticket's edit lands in the tracker now (no-op
    # when unbound / a pass is already running).
    from app.stories.sync import kick_prd_sync_from_key

    kick_prd_sync_from_key(company_id, ticket_key)
    return {"ok": True}


@data_router.put(
    "/tickets/{ticket_key}/fields", dependencies=[Depends(_require_internal_key)]
)
def save_ticket_fields(
    ticket_key: str, company_id: str, body: TicketFieldsIn
) -> dict[str, Any]:
    """Upsert only the sent fields (title/priority/status/sprint/assignee),
    preserving the description + other fields (mirrors
    routes/tickets.py:save_fields).

    Tracker-bound tickets speak the tracker's vocabulary: status/priority
    validate against the destination's cached meta. Canonical/legacy names
    ("In progress", "Done", "high", …) resolve to the workspace's real status
    of the same category — so agents following the server instructions keep
    working on ANY workspace; a truly unknown value 422s with the allowed
    names so the agent can self-correct."""
    from app.connectors.tracker_meta import validate_fields_against_meta
    from app.db.client import require_client, utc_now

    fields = body.model_dump(exclude_unset=True)
    fields = validate_fields_against_meta(company_id, ticket_key, fields)
    # custom_fields merges over the stored map (mirrors routes/tickets.py —
    # one jsonb column holds many fields; null clears one field's override).
    if fields.get("custom_fields") is not None:
        existing = (
            require_client().table("ticket_edits").select("custom_fields")
            .eq("company_id", company_id).eq("ticket_key", ticket_key)
            .limit(1).execute().data
            or []
        )
        merged = dict((existing[0].get("custom_fields") if existing else None) or {})
        for fid, value in fields["custom_fields"].items():
            if value is None:
                merged.pop(fid, None)
            else:
                merged[fid] = value
        fields["custom_fields"] = merged
    require_client().table("ticket_edits").upsert(
        {
            "company_id": company_id,
            "ticket_key": ticket_key,
            "updated_at": utc_now(),
            **fields,
        },
        on_conflict="company_id,ticket_key",
    ).execute()
    # Instant push: a bound ticket's edit lands in the tracker now (no-op
    # when unbound / a pass is already running).
    from app.stories.sync import kick_prd_sync_from_key

    kick_prd_sync_from_key(company_id, ticket_key)
    return {"ok": True}


@data_router.post(
    "/tickets/{ticket_key}/comments", dependencies=[Depends(_require_internal_key)]
)
def add_ticket_comment(
    ticket_key: str, company_id: str, user_id: str, body: TicketCommentIn
) -> dict[str, Any]:
    """Insert a comment on a ticket, attributed to the TOKEN OWNER.

    The author is resolved server-side from `user_id` (the token's owner) →
    their profile name, else email, else "mcp" — never accepted from the
    caller, so the AI client can't attribute a comment to someone else.
    Mirrors routes/tickets.py:add_comment otherwise."""
    from app.db.client import require_client
    from app.db.companies import display_name_for_user

    author = display_name_for_user(user_id) or "mcp"
    resp = (
        require_client()
        .table("ticket_comments")
        .insert(
            {
                "company_id": company_id,
                "ticket_key": ticket_key,
                "author": author,
                "body": body.body,
            }
        )
        .execute()
    )
    row = resp.data[0]
    # Instant one-way push: a bound ticket's comment lands in the tracker as
    # a real comment now (no-op when unbound; the sync pass retries failures).
    from app.stories.sync import kick_comment_push

    kick_comment_push(company_id, ticket_key, row["id"], author, body.body)
    return {
        "id": row["id"],
        "author": row["author"],
        "body": row["body"],
        "time": str(row["created_at"]),
    }


@data_router.post(
    "/tickets/{ticket_key}/attachments",
    dependencies=[Depends(_require_internal_key)],
)
def add_ticket_attachment(
    ticket_key: str, company_id: str, body: TicketAttachmentIn
) -> dict[str, Any]:
    """Attach a link/reference to a ticket — e.g. a developer linking their PR
    or branch. `label` is the display text, `sub` an optional secondary line
    (URL/note). Mirrors routes/tickets.py:add_attachment.

    `sub` is rendered as a clickable href in the app, so script-y URL schemes
    are rejected here (this is an AI/token-writable surface)."""
    from app.db.client import require_client

    if _UNSAFE_URL_SCHEME.match(body.sub or ""):
        raise HTTPException(400, "unsafe_attachment_url")

    resp = (
        require_client()
        .table("ticket_attachments")
        .insert(
            {
                "company_id": company_id,
                "ticket_key": ticket_key,
                "label": body.label,
                "sub": body.sub,
            }
        )
        .execute()
    )
    row = resp.data[0]
    return {"id": row["id"], "label": row["label"], "sub": row["sub"]}

"""Aggregated artifact listing for the All-Chats "Artifacts" tab.

A read-only fan-out over the six artifact tables — PRDs, prototypes, evidence,
reports, standalone ticket sets, and custom artifacts (team documents of any
kind) — unified into one recency-sorted list for a single company.

Tenant scoping is split because the surfaces key off the tenant differently
(verified against the existing queries):

  - PRDs / evidences are scoped by the BRIEF's `dataset` slug:
    briefs.dataset = <company slug>  →  briefs.id  →  prds/evidences.brief_id.
    (Mirrors app.deps.ownership's brief→dataset→company chain.)

  - Prototypes are scoped by `workspace_id`, which the Design Agent routes set
    to `company.company_id` (the company UUID) — see routes/design_agent.py
    (`workspace_id = company.company_id`) and db/prototypes.py. So prototypes
    are filtered by the company UUID, NOT the slug.

  - Reports are scoped by `company_id` (the company UUID) — captured that way by
    app/report_capture.py so every workspace in a company shares one report
    library (the db/custom_skills.py posture).

  - Ticket sets are scoped by `company_id` too, for the same reason and by the
    same decision (see supabase/migrations/20260806120000_ticket_sets.sql).

  - Custom artifacts are scoped by `company_id` for that same reason — one
    shared library per company, editable by any member
    (supabase/migrations/20260813120000_custom_artifacts.sql).

The route passes BOTH (the slug for PRDs/evidence, the UUID for prototypes and
reports) so each surface is scoped the way its own writers scoped it. Joins are
done in Python (fetch brief ids for the dataset → prds/evidences by brief_id IN
(...); prototypes and reports by the company UUID; then map prd_id → title for
prototype titles and report attachments, and conversation_id → title for report
attachments) because the PostgREST client makes multi-table SQL joins awkward —
the same in-code-join posture db/prds.latest_prd_for_dataset already uses.
"""
from __future__ import annotations

from app.db.client import require_client, retry_on_disconnect

# Hard cap on the unified list. Recency-sorted, so the cap keeps the newest
# 200 artifacts; older ones are dropped (acceptable for a listing view — the
# brief/PRD screens remain the source of truth for deep history).
_LIST_CAP = 200


def _prd_family_key(row: dict) -> tuple:
    """Identity of the LOGICAL PRD a row belongs to (its regeneration family).

    Every regeneration is a NEW prds row and this listing keeps only the newest
    row per family, so the key has to match how each generation path actually
    establishes identity (mirrors db/prds.list_prd_generations):

      - chat / ideation PRDs have no brief insight: they anchor to the company's
        brief with insight_index 0 as a STORAGE SENTINEL and are keyed by
        `theme_id` ('chat:<hash>' from routes/prd._chat_task_theme_id, or the KG
        theme). Keying them on insight_index collapsed every chat PRD under one
        brief into a single entry — only the newest survived, and it shadowed
        the brief's own insight-0 PRD too.
      - uploaded PRDs have neither an insight nor a theme: they share the
        per-company uploads-anchor brief at the same sentinel index, and each
        import is its own document (there is no regenerate-in-place path for
        them), so the row IS its own family.
      - brief-insight PRDs keep (brief_id, insight_index) — theme_id is NULL and
        `source` is 'brief', so they fall through unchanged.

    A legacy row with a NULL `source` also falls through to the insight branch,
    i.e. the historical behaviour.
    """
    if row.get("theme_id"):
        return (row["brief_id"], "theme", row["theme_id"])
    if row.get("source") == "upload":
        return (row["brief_id"], "upload", row["id"])
    return (row["brief_id"], "insight", row.get("insight_index"))


def prd_is_brief_anchored(row: dict) -> bool:
    """True when this PRD's `insight_index` names a REAL brief insight.

    Chat, ideation and uploaded PRDs anchor to a brief with `insight_index = 0`
    as a pure STORAGE SENTINEL (see `_prd_family_key`) — that 0 is not insight
    zero, it is "no insight". Anything that resolves the pair (brief_id,
    insight_index) into content — the panel's Evidence tab loads by exactly
    that pair — has to know the difference, or a chat PRD silently shows the
    evidence belonging to the brief's first finding.

    The distinction is derived here, in the one module that already owns what a
    PRD family is, so callers never re-derive it from `theme_id` themselves.
    """
    return _prd_family_key(row)[1] == "insight"


def _prd_titles(c, prd_ids: list[int]) -> dict[int, str]:
    """prd_id → title for the ids given (empty dict for an empty list).

    Shared by the prototype rows (a prototype has no title of its own) and the
    report rows (a report attached to a PRD names it in its source line).
    """
    if not prd_ids:
        return {}
    rows = (
        c.table("prds").select("id, title").in_("id", prd_ids).execute().data or []
    )
    return {r["id"]: r.get("title") for r in rows}


# Statuses that are not a document anyone can open. `invalidated` is the one
# that matters operationally: a backend restart flips every in-flight PRD to it
# (invalidate_orphan_generating_prds), so it lands on real rows regularly.
_UNOPENABLE_PRD_STATUSES = frozenset({"failed", "invalidated"})


@retry_on_disconnect
def list_document_artifacts(*, dataset: str, openable_only: bool = False) -> list[dict]:
    """The PRD + evidence half of the artifact list, on its own.

    Split out of `list_artifacts_for_company` (which calls it) rather than
    duplicated, so the two can never drift on what a PRD family is, what a row
    normalizes to, or which brief scopes it — and so a caller that can only act
    on documents does not pay for a five-table fan-out.

    That caller is app.artifact_open, resolving "open the PRD for X" INSIDE the
    chat send path: prototypes, reports and ticket sets are not openable in the
    chat's right-hand panel, so querying them there would be three round trips
    spent on rows that can never be the answer.

    `openable_only` drops failed/invalidated PRD rows BEFORE the regeneration
    family collapses to its newest row, so a family whose newest attempt died
    falls back to the newest attempt that DIDN'T. Order matters and is the whole
    point of the flag: collapsing first and filtering after makes the whole
    family invisible the moment one restart invalidates its head, even though a
    perfectly readable generation sits right behind it. The default is False
    because the Artifacts LISTING deliberately shows those rows (a failed PRD is
    something the user should see); only an OPEN needs them gone.

    `dataset` scopes both types via `briefs.dataset` and must already be
    tenant-gated by the caller. Rows are normalized identically to the unified
    listing ({type, id, title, status, created_at, source, open}) and returned
    unsorted — every caller re-orders anyway.
    """
    c = require_client()

    # ── Briefs for this dataset: id → week_label. Drives PRD/evidence scoping
    #    and supplies the human "from Brief <week_label>" source line. ────────
    brief_rows = (
        c.table("briefs")
        .select("id, week_label")
        .eq("dataset", dataset)
        .execute()
        .data
        or []
    )
    brief_ids = [r["id"] for r in brief_rows]
    week_label_by_brief = {r["id"]: r.get("week_label") for r in brief_rows}
    if not brief_ids:
        return []

    items: list[dict] = []

    # ── PRDs (brief_id IN brief_ids) ────────────────────────────────────────
    prd_rows = (
        c.table("prds")
        .select(
            "id, brief_id, insight_index, theme_id, source, "
            "title, status, generated_at"
        )
        .in_("brief_id", brief_ids)
        .execute()
        .data
        or []
    )
    # A PRD is regenerated in place: each attempt is a new prds row in the
    # same family. The artifacts list shows only the LATEST generation per
    # logical PRD; older generations are reachable from the PRD's Version
    # History (see routes/prd.py /{prd_id}/generations). What counts as a
    # family is per-source — see _prd_family_key.
    latest_by_key: dict[tuple, dict] = {}
    for r in prd_rows:
        # BEFORE the collapse, never after — see the `openable_only` note in
        # the docstring.
        if openable_only and (r.get("status") or "") in _UNOPENABLE_PRD_STATUSES:
            continue
        key = _prd_family_key(r)
        cur = latest_by_key.get(key)
        if cur is None or (r.get("generated_at") or "") > (cur.get("generated_at") or ""):
            latest_by_key[key] = r
    for r in latest_by_key.values():
        bid = r["brief_id"]
        items.append({
            "type": "prd",
            "id": r["id"],
            "title": r.get("title") or "Untitled PRD",
            "status": r.get("status") or "",
            "created_at": r.get("generated_at"),
            # Whether `insight_index` names a real finding or is the storage
            # sentinel — see prd_is_brief_anchored. Consumers that turn the
            # (brief, insight) pair back into content must check it.
            "brief_anchored": prd_is_brief_anchored(r),
            "source": {
                "brief_id": bid,
                "week_label": week_label_by_brief.get(bid),
                "insight_index": r.get("insight_index"),
            },
            "open": {
                "brief_id": bid,
                "insight_index": r.get("insight_index"),
                "prd_id": r["id"],
            },
        })

    # ── Evidences (brief_id IN brief_ids) ───────────────────────────────────
    ev_rows = (
        c.table("evidences")
        .select("id, brief_id, insight_index, title, status, generated_at")
        .in_("brief_id", brief_ids)
        .execute()
        .data
        or []
    )
    for r in ev_rows:
        # Evidence has no regeneration family, so order doesn't matter here —
        # but a failed document is no more openable than a failed PRD.
        if openable_only and (r.get("status") or "") in _UNOPENABLE_PRD_STATUSES:
            continue
        bid = r["brief_id"]
        items.append({
            "type": "evidence",
            "id": r["id"],
            "title": r.get("title") or "Untitled evidence",
            "status": r.get("status") or "",
            "created_at": r.get("generated_at"),
            # Evidence has no sentinel form — it only ever exists FOR a finding.
            "brief_anchored": True,
            "source": {
                "brief_id": bid,
                "week_label": week_label_by_brief.get(bid),
                "insight_index": r.get("insight_index"),
            },
            "open": {
                "brief_id": bid,
                "insight_index": r.get("insight_index"),
                "evidence_id": r["id"],
            },
        })

    return items


@retry_on_disconnect
def list_artifacts_for_company(*, dataset: str, company_id: str) -> list[dict]:
    """Unified, recency-sorted artifact list for one company.

    `dataset` is the company slug (scopes PRDs + evidences via briefs.dataset);
    `company_id` is the company UUID (scopes prototypes via workspace_id). The
    caller (routes/artifacts.py) has already tenant-gated both.

    Returns a list of normalized dicts shaped:
        {type, id, title, status, created_at, source, open}
    sorted by created_at DESC and capped at 200.
    """
    c = require_client()

    # PRDs + evidences, normalized and family-collapsed (shared with the
    # chat's open-artifact lookup — see list_document_artifacts).
    items: list[dict] = list_document_artifacts(dataset=dataset)

    # ── Prototypes (workspace_id = company UUID). Title is derived from the
    #    parent PRD (prototypes have no title column). ─────────────────────────
    #    Surface only in-progress + built prototypes: status IN
    #    ('generating','ready'). 'failed' / 'invalidated' are intentionally
    #    excluded — they are not user-facing artifacts in this listing.
    proto_rows = (
        c.table("prototypes")
        .select("id, prd_id, status, created_at, preview_image_url, is_complete")
        .eq("workspace_id", company_id)
        .in_("status", ["generating", "ready"])
        .execute()
        .data
        or []
    )
    if proto_rows:
        prd_ids = sorted({r["prd_id"] for r in proto_rows if r.get("prd_id") is not None})
        prd_title_by_id = _prd_titles(c, prd_ids)
        for r in proto_rows:
            pid = r.get("prd_id")
            prd_title = prd_title_by_id.get(pid) or "Untitled PRD"
            items.append({
                "type": "prototype",
                "id": r["id"],
                # Derived from the parent PRD's title (no prototype title column).
                "title": prd_title,
                "status": r.get("status") or "",
                "created_at": r.get("created_at"),
                # Frontend derives Building/Completed/Draft + clickability +
                # thumbnail-vs-shimmer from these. preview_image_url is NULL
                # until completion (or when screenshotting isn't provisioned).
                "preview_image_url": r.get("preview_image_url"),
                "is_complete": bool(r.get("is_complete")),
                "source": {
                    "prd_id": pid,
                    "prd_title": prd_title,
                },
                "open": {
                    "prototype_id": r["id"],
                    "prd_id": pid,
                },
            })

    # ── Reports (company_id = company UUID). The captured HTML documents from
    #    the report skills — see app/report_capture.py. `html` is deliberately
    #    NOT selected: a listing must not carry N full documents, so the viewer
    #    fetches the body by id (GET /v1/reports/{id}).
    #
    #    A report has no lifecycle — capture happens after the answer is complete
    #    — so `status` is always empty rather than a state the UI must interpret.
    #
    #    Hoisted out of the reports block because the ticket-set block below
    #    shares it: both artifact types name the chat they were born in, and
    #    two independent conversation lookups for one listing is a wasted
    #    round-trip.
    convo_title_by_id: dict[int, str] = {}
    report_rows = (
        c.table("reports")
        .select(
            "id, skill, title, question, created_at, conversation_id, prd_id, "
            "share_mode"
        )
        .eq("company_id", company_id)
        .order("id", desc=True)
        .limit(_LIST_CAP)
        .execute()
        .data
        or []
    )
    if report_rows:
        # The ATTACHMENT's human names: the chat room and/or PRD each report was
        # generated in (report_capture.py copies both from the originating ask).
        # A missing id means the report stands alone; a present id with no row
        # means the chat/PRD was deleted (`on delete set null` fires, so this is
        # the transient in-flight case) and the label degrades to nothing rather
        # than inventing one.
        rep_prd_titles = _prd_titles(
            c, sorted({r["prd_id"] for r in report_rows if r.get("prd_id") is not None})
        )
        convo_ids = sorted(
            {r["conversation_id"] for r in report_rows if r.get("conversation_id") is not None}
        )
        if convo_ids:
            convo_rows = (
                c.table("conversations")
                .select("id, title")
                .in_("id", convo_ids)
                .execute()
                .data
                or []
            )
            convo_title_by_id.update({r["id"]: r.get("title") for r in convo_rows})
        for r in report_rows:
            cid, pid = r.get("conversation_id"), r.get("prd_id")
            items.append({
                "type": "report",
                "id": r["id"],
                "title": r.get("title") or "Untitled report",
                "status": "",
                "created_at": r.get("created_at"),
                # The report KIND (skill id, e.g. 'voice-of-customer-report').
                # The row's badge sub-label and what a per-kind filter keys off.
                "skill": r.get("skill") or "",
                # Whether a link exists for this report, so the row can mark
                # itself shared. The TOKEN is deliberately not in the listing —
                # only the share dialog fetches it.
                "share_mode": r.get("share_mode") or "private",
                "source": {
                    "skill": r.get("skill") or "",
                    "question": r.get("question") or "",
                    "conversation_id": cid,
                    "conversation_title": convo_title_by_id.get(cid) if cid else None,
                    "prd_id": pid,
                    "prd_title": rep_prd_titles.get(pid) if pid else None,
                },
                "open": {"report_id": r["id"]},
            })

    # ── Standalone ticket sets (company_id = company UUID). Tickets generated
    #    from a chat with NO PRD behind them — see app/db/ticket_sets.py.
    #
    #    `stories` IS selected (unlike the reports listing's `html`) purely to
    #    COUNT the tickets, and is dropped before the item is appended: the row
    #    needs "6 tickets" as its count affordance, and the alternative — a
    #    per-set count query — is N round-trips for one integer. Nothing here
    #    ships the ticket bodies to the client.
    #
    #    'generating' sets ARE listed (like in-progress prototypes above): a set
    #    the user just asked for should appear immediately, marked as building
    #    and not clickable, rather than materialising minutes later. 'failed'
    #    ones are excluded — a run that produced nothing is not an artifact.
    set_rows = (
        c.table("ticket_sets")
        .select("id, title, source_text, status, created_at, conversation_id, stories")
        .eq("company_id", company_id)
        .in_("status", ["generating", "ready"])
        .order("id", desc=True)
        .limit(_LIST_CAP)
        .execute()
        .data
        or []
    )
    if set_rows:
        # Reuse the conversation-title lookup the reports block may already have
        # built, filling in any ids it didn't need. One query serves both types.
        set_convo_ids = sorted(
            {
                r["conversation_id"] for r in set_rows
                if r.get("conversation_id") is not None
                and r["conversation_id"] not in convo_title_by_id
            }
        )
        if set_convo_ids:
            for r in (
                c.table("conversations")
                .select("id, title")
                .in_("id", set_convo_ids)
                .execute()
                .data
                or []
            ):
                convo_title_by_id[r["id"]] = r.get("title")
        for r in set_rows:
            cid = r.get("conversation_id")
            stories = [s for s in (r.get("stories") or []) if isinstance(s, dict)]
            items.append({
                "type": "ticket_set",
                "id": r["id"],
                # Empty until the naming leg lands; the web renders its own
                # "Tickets from this conversation" rather than a fabricated one.
                "title": r.get("title") or "",
                "status": r.get("status") or "",
                "created_at": r.get("created_at"),
                "ticket_count": len(stories),
                "source": {
                    "conversation_id": cid,
                    # None when the chat was deleted (`on delete set null`); the
                    # row then omits the "from <chat>" clause rather than
                    # inventing a label for a thread that no longer exists.
                    "conversation_title": convo_title_by_id.get(cid) if cid else None,
                    "question": r.get("source_text") or "",
                },
                "open": {"ticket_set_id": r["id"]},
            })

    # ── Custom artifacts (company_id = company UUID). Team documents of any
    #    kind — the "Others" section. Scoped like reports and ticket sets, so
    #    every workspace in a company shares one library.
    #
    #    `body_html` is NOT selected, for the reason the reports block gives
    #    about `html`: a listing must not carry N full documents. The editor
    #    fetches the body by id (GET /v1/custom-artifacts/{id}).
    #
    #    'generating' rows ARE listed — a document the user just asked for
    #    should appear immediately, marked as writing and not yet clickable,
    #    the same treatment building prototypes and ticket sets get.
    #
    #    SO ARE 'failed' ONES, which they previously were not. The old rule
    #    ("a run that produced nothing is not an artifact") is true of the row
    #    and false of the product: someone asked for that document and came
    #    here to find it. Excluding it meant the library answered with nothing
    #    at all — no document, no failure, no reason to look anywhere else —
    #    which is exactly how a failed generation stayed invisible. The row
    #    says it could not be written, and opens onto the reason.
    doc_rows = (
        c.table("custom_artifacts")
        .select(
            "id, kind, title, status, created_at, updated_at, conversation_id"
        )
        .eq("company_id", company_id)
        .in_("status", ["generating", "ready", "failed"])
        # ORDERED BY LAST EDIT, not by id, because the CAP is applied here and
        # the sort below cannot rescue a row the query already dropped. With
        # `id desc` the 200 most recently CREATED documents were selected and
        # only then reordered by last edit — so for a company past the cap, a
        # document created last month and edited this morning was cut before
        # the sort ever saw it, and vanished from the library instead of
        # appearing at the top. That is exactly the case this listing sorts for.
        .order("updated_at", desc=True)
        .limit(_LIST_CAP)
        .execute()
        .data
        or []
    )
    if doc_rows:
        # Same conversation-title lookup the two blocks above share, filled in
        # for any ids they didn't already need.
        doc_convo_ids = sorted(
            {
                r["conversation_id"] for r in doc_rows
                if r.get("conversation_id") is not None
                and r["conversation_id"] not in convo_title_by_id
            }
        )
        if doc_convo_ids:
            for r in (
                c.table("conversations")
                .select("id, title")
                .in_("id", doc_convo_ids)
                .execute()
                .data
                or []
            ):
                convo_title_by_id[r["id"]] = r.get("title")
        for r in doc_rows:
            cid = r.get("conversation_id")
            items.append({
                "type": "custom_artifact",
                "id": r["id"],
                # Empty until the user names it (or a generation does); the web
                # renders its own "Untitled document" rather than a fabricated
                # title stored on the row.
                "title": r.get("title") or "",
                "status": r.get("status") or "",
                # The document's own free-text label ('leadership update'),
                # shown in the row's source line. Never dispatched on — see the
                # migration's note on why `kind` is not an enum.
                "kind": r.get("kind") or "",
                # A document is EDITED after it is created, so the library
                # sorts by last touch rather than birth. The shared listing key
                # `created_at` is what every consumer sorts on, so it carries
                # the last-edit time — and the BIRTH date is emitted beside it
                # under its own name rather than being silently discarded, so a
                # surface that wants to say "Created 3 Aug" still can. (The two
                # were previously collapsed into one key, which made a document
                # edited today read as created today.)
                "created_at": r.get("updated_at") or r.get("created_at"),
                "updated_at": r.get("updated_at"),
                "born_at": r.get("created_at"),
                "source": {
                    "kind": r.get("kind") or "",
                    "conversation_id": cid,
                    # None when the chat was deleted (`on delete set null`); the
                    # row omits the "from <chat>" clause rather than inventing
                    # a label for a thread that no longer exists.
                    "conversation_title": convo_title_by_id.get(cid) if cid else None,
                },
                "open": {"custom_artifact_id": r["id"]},
            })

    # Recency sort (newest first). created_at is an ISO-8601 string; lexical
    # sort matches chronological order for same-format UTC timestamps. None
    # timestamps (shouldn't happen — all three tables default the column) sort
    # last via an empty-string fallback.
    items.sort(key=lambda it: it.get("created_at") or "", reverse=True)
    return items[:_LIST_CAP]


def list_artifacts_for_project(*, project_id: int, dataset: str, company_id: str) -> list[dict]:
    """A project's artifacts — the existing five-table fan-out, filtered.

    Reuses `list_artifacts_for_company` verbatim (AD-P1/AD-P12, build spec
    §5.2): fetches the project's `(artifact_type, artifact_id)` refs from
    `project_artifacts`, runs the caller's own company-wide unified list,
    then narrows it to the ref set. Zero new per-table scoping query is
    introduced here — every tenancy check already lives in
    `list_artifacts_for_company` / `list_document_artifacts`.

    Output shape is identical to `list_artifacts_for_company`'s:
    `{type, id, title, status, created_at, source, open}` (plus each type's
    extra fields), already recency-sorted.

    Tolerated-stale (AD-P1/§4.3): a ref whose underlying artifact is gone —
    or was never in the CALLER's own fan-out (e.g. a foreign-tenant row a
    write-time check should have rejected) — simply has no match in `items`
    and drops out silently, no error. This also means a project can never
    surface an artifact the caller's own company doesn't own, even if a ref
    somehow got written for one: the filter only keeps rows that are ALSO in
    the caller's own tenant-scoped fan-out.

    Resolve-forward for a superseded PRD pin: `project_artifacts` pins a PRD
    by a FIXED `artifact_id`, with no current-version indirection. When that
    PRD is regenerated (`force=True` mints a NEW `prds.id` in the same
    family — see `db/prds.start_prd` — while the old row stays `ready`), the
    company fan-out's family collapse (`latest_by_key` above) keeps only the
    new id, so the pinned OLD id no longer appears in `items` at all and a
    plain ref∩items intersection would silently drop the PRD from the
    project (neither generation shows, even though the family is very much
    alive). When a pinned `('prd', id)` ref is absent from `items` this way,
    resolve it FORWARD to its family's latest generation via
    `list_prd_generations` (keyed by prd_id, walks the SAME family the
    pinned id belongs to — see db/prds.py) and surface THAT row instead,
    provided the resolved row is itself present in the caller's own
    tenant-scoped `items` (so a foreign-tenant or fully-deleted family still
    drops out silently, preserving the tolerated-stale contract above). This
    can never re-point a pin at a family other than its own, and a family
    already pinned at its own latest generation is never double-surfaced
    (the final filter is keyed by resolved id, not by ref).

    Orphan GC — explicitly DEFERRED: orphaned `project_artifacts` rows are
    never cleaned up on hard-delete (no FK/cascade on `artifact_id`), but
    resolve-forward makes a stale pin harmless on read (it either resolves
    forward within its family or silently drops per tolerated-stale above),
    so no project's artifact list ever counts a permanently-unresolvable
    artifact. GC itself is a fast-follow, not required here.
    """
    from app.db.prds import list_prd_generations
    from app.db.projects import list_project_artifact_refs

    refs = {
        (r["artifact_type"], r["artifact_id"])
        for r in list_project_artifact_refs(project_id)
    }
    if not refs:
        return []
    items = list_artifacts_for_company(dataset=dataset, company_id=company_id)
    items_by_key = {(it["type"], it["id"]): it for it in items}

    # Direct hits: the pinned (type, id) is present verbatim in the caller's
    # own tenant-scoped fan-out — the common case, including a PRD pinned at
    # its own current generation.
    surfaced_keys = {key for key in refs if key in items_by_key}

    # Resolve-forward: only for PRD refs that missed a direct hit (their
    # generation was superseded, or the family/tenant is gone entirely).
    for ref_type, ref_id in refs:
        if ref_type != "prd" or (ref_type, ref_id) in surfaced_keys:
            continue
        family = list_prd_generations(ref_id)
        if not family:
            continue  # whole family unresolvable — tolerated-stale, drops out
        newest_key = ("prd", family[0]["id"])
        if newest_key in items_by_key:
            surfaced_keys.add(newest_key)

    # Iterate `items` (already recency-sorted), not `refs`, so a family
    # reached by more than one pin — or already both directly-hit and
    # resolved-forward to itself — surfaces exactly once.
    return [it for it in items if (it["type"], it["id"]) in surfaced_keys]

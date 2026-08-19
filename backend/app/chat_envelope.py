"""Shared reply-envelope enrichment for every chat surface.

`resolve_chat_intent` names WHAT the user asked for (an intent, a kind, a
query); the DATA the client renders — the open-artifact lookup with its
conversation stamps, the clickable artifact rows for a listing, the
full-library counts — is attached HERE, where the tenant scope lives.

Extracted from `routes/chat.py` so the project chat surfaces (private and
group) attach the SAME enrichment to their classify envelopes instead of
reimplementing it — one enrichment, three surfaces, no drift.
`routes/chat.py` re-imports these names, so its import surface (used by
`routes/projects.py` and the existing suites) is unchanged.

Every enrichment keeps its pre-extraction contract: read-only and
best-effort — a listing or stamp hiccup degrades the reply to prose, never
fails the send.
"""
from __future__ import annotations

import logging

from app.artifact_open import resolve_open_artifact

logger = logging.getLogger(__name__)


def _dataset_for(company) -> str:
    """The dataset slug backing the caller's active workspace ("" if none).

    Datasets are per-workspace ('{company}--{workspace}') except the DEFAULT
    workspace, which keeps the bare company slug and — for companies predating
    the workspace binding — often has no `datasets.workspace_id` at all. The
    company-slug fallback covers exactly that legacy case and is scoped to the
    default workspace on purpose: applying it to a non-default workspace with
    no dataset of its own would search the default workspace's artifacts from
    inside a workspace that must not see them.

    Returning "" (never a guess) is what makes an unresolvable workspace a
    clean `not_found` instead of a lookup against the wrong slug.
    """
    from app.db.companies import slug_for_company_id
    from app.db.workspaces import dataset_slug_for_workspace

    workspace_id = getattr(company, "workspace_id", None)
    if workspace_id:
        bound = dataset_slug_for_workspace(workspace_id)
        if bound:
            return bound
        if not getattr(company, "workspace_is_default", False):
            return ""
    return slug_for_company_id(company.company_id) or ""


def enrich_chat_envelope(
    envelope: dict, company, dataset: str | None = None,
    project_id: int | None = None,
) -> dict:
    """Attach the render-data legs to one classify envelope, in place.

    The single enrichment step every chat surface runs on the envelope its
    classifier produced — main chat (`routes/chat.py`), the private project
    chat and the group responder (`routes/projects.py`) — so a card the main
    surface can render always has the same DATA on the project surfaces.

    `company` needs only `.company_id` (plus, when `dataset` is not
    supplied, the workspace fields `_dataset_for` reads — the shape both
    `CompanyContext` and `WorkspaceContext` satisfy). `dataset` lets a
    caller that has already resolved its workspace dataset (the project
    routes) pass it through instead of paying a second lookup; when omitted
    it is resolved per leg, exactly where the pre-extraction inline code
    resolved it.

    `project_id` scopes the LISTING legs (`artifact_list`/`artifact_counts`)
    to one project's own artifacts, so a project surface's cards and counts
    agree with its project-scoped prose instead of showing the whole
    workspace's. When omitted (main chat) both legs keep the workspace-wide
    listing verbatim — the default changes nothing for existing callers.

    Returns the same dict for call-site convenience; mutation is in place.
    """
    if envelope.get("intent") == "open_artifact":
        # The resolver named a SUBJECT; the lookup happens here, where the
        # tenant scope lives. Same posture as the classify routes themselves:
        # read-only and scoped to the caller's workspace, so a phrase can only
        # ever resolve to a document this caller already owns.
        # `project_id`, when set, narrows the SOURCE the same way the listing
        # legs below do — a project surface's "open the PRD" must only ever
        # resolve against that project's own artifacts, never the whole
        # workspace's (see the `list_artifacts` branch's identical forward).
        envelope["open"] = resolve_open_artifact(
            artifact_type=envelope.get("artifact_type") or "prd",
            query=envelope.get("artifact_query") or "",
            dataset=_dataset_for(company) if dataset is None else dataset,
            project_id=project_id,
            company_id=company.company_id if project_id is not None else None,
        )
        _attach_open_conversations(envelope["open"], company.company_id)
    if envelope.get("intent") == "list_artifacts":
        # The planner named a KIND (and maybe a COUNT — "my last 5 PRDs"); the
        # rows come from here, where the tenant scope lives — same split as
        # `open` above. Read-only: listing what exists generates nothing and
        # never fails the envelope (an empty list renders as "you haven't made
        # any yet", which is an answer).
        envelope["artifact_list"] = _chat_artifact_list(
            company, envelope.get("list_kind"), envelope.get("list_limit"),
            dataset=dataset, project_id=project_id,
        )
        if envelope.get("list_mode") == "count":
            # A HOW-MANY ask: the numbers come from the FULL library, never
            # from the capped card list above — counting a 12-row page and
            # calling it the total is the lie the cap exists to avoid.
            envelope["artifact_counts"] = _chat_artifact_counts(
                company, envelope.get("list_kind"), dataset=dataset,
                project_id=project_id,
            )
    return envelope


def project_prd_edit_target(
    company, project_id: int, dataset: str | None = None
) -> int | None:
    """The PRD a project chat's act-on-PRD intent (`edit_prd`,
    `change_prd_template`, `assign_tickets`) targets when the client named none.

    `edit_prd` and its `_NEEDS_PRD` siblings downgrade to a plain `answer` when
    `chat_intent` has no target `prd_id` — the right call on main chat, where
    "make the PRD shorter" with nothing open is genuinely ambiguous. In a
    PROJECT chat it is not: the project OWNS its PRDs, so "make the PRD shorter"
    means the project's PRD even when the user hasn't opened it in the panel.
    Without this the edit silently becomes a summary (the reported defect).

    Returns the project's NEWEST openable PRD id — the common single-PRD project
    has exactly one answer, and with several the newest matches the recency
    collapse the open-resolver and listing legs already use. It is a FALLBACK:
    the route resolves the client's open-panel / conversation-bound PRD first,
    so a user with a specific PRD in front of them still edits that one. The
    downstream write is still gated by `project_prd_gate` (the PRD must be on
    this project), so a stale/foreign id can never be written. Best-effort: an
    empty project or any lookup failure → None (the intent degrades to answer
    exactly as before)."""
    try:
        if dataset is None:
            dataset = _dataset_for(company)
        if not dataset:
            return None
        from app.db.artifacts import list_artifacts_for_project

        # `list_artifacts_for_project` is already recency-sorted; the first
        # openable PRD row is therefore the newest.
        for row in list_artifacts_for_project(
            project_id=project_id, dataset=dataset, company_id=company.company_id,
        ):
            if row.get("type") != "prd":
                continue
            if (row.get("status") or "") in ("failed", "invalidated"):
                continue
            return (row.get("open") or {}).get("prd_id")
        return None
    except Exception:  # noqa: BLE001 — a target lookup must never fail the send
        logger.exception("project prd edit-target lookup failed; no target")
        return None


#: How many rows a chat listing carries. The chat is a picker, not the
#: library — the Artifacts screen is one click away for the long tail, and a
#: reply with two hundred cards is not an answer anyone can read.
_MAX_CHAT_ARTIFACTS = 12


def _chat_artifact_list(
    company, list_kind: str | None, list_limit: int | None = None,
    dataset: str | None = None, project_id: int | None = None,
) -> list[dict]:
    """The caller's own artifacts as the chat's clickable rows.

    The SAME aggregation the Artifacts screen reads
    (`db.artifacts.list_artifacts_for_company` — recency-sorted, tenant-scoped
    by the dataset/company pair), narrowed to the asked-for kind and capped.
    `list_limit` is the COUNT the user asked for ("my last 5 PRDs" → 5, "the
    latest PRD" → 1), already gated by the planner (`constraints.top_n` — a
    positive int or nothing); it tightens the cap, never widens it — the chat
    is a picker, and two hundred cards is not an answer anyone can read.
    `project_id`, when set, swaps the SOURCE to the project's own listing
    (`list_artifacts_for_project` — identical row shape, filtered at the
    source so a project artifact outside the workspace's newest page can
    never silently vanish); everything downstream is unchanged.
    PRD rows are enriched with the conversation that produced them
    (`conversations_for_prds`) so a click can resume the PRD's own thread —
    reports, ticket sets and team documents already carry their
    conversation_id/conversation_title in `source`, written by the listing
    itself. Evidence and prototypes have no thread and none is invented; the
    client's per-kind open falls back to the panel / the prototype canvas.

    Best-effort by contract: any failure returns [] and the reply degrades to
    prose — a listing hiccup must never fail the send."""
    try:
        if dataset is None:
            dataset = _dataset_for(company)
        if not dataset:
            return []
        from app.db.artifacts import (
            list_artifacts_for_company,
            list_artifacts_for_project,
        )
        from app.db.conversations import conversations_for_prds

        if project_id is not None:
            items = list_artifacts_for_project(
                project_id=project_id, dataset=dataset,
                company_id=company.company_id,
            )
        else:
            items = list_artifacts_for_company(
                dataset=dataset, company_id=company.company_id
            )
        kind = (list_kind or "all").strip() or "all"
        if kind != "all":
            items = [i for i in items if i.get("type") == kind]
        cap = _MAX_CHAT_ARTIFACTS
        if isinstance(list_limit, int) and not isinstance(list_limit, bool) \
                and 0 < list_limit < cap:
            cap = list_limit
        items = items[:cap]

        convo_by_prd = conversations_for_prds(
            [i["id"] for i in items if i.get("type") == "prd"],
            company.company_id,
        )
        out: list[dict] = []
        for i in items:
            source = dict(i.get("source") or {})
            if i.get("type") == "prd":
                convo = convo_by_prd.get(i["id"])
                # Same null-title rule the Artifacts screen applies on open: a
                # binding whose chat row is gone offers no thread to resume.
                source["conversation_id"] = (convo or {}).get("id")
                source["conversation_title"] = (convo or {}).get("title") or None
            out.append({
                "type": i.get("type") or "",
                "id": i.get("id"),
                "title": i.get("title") or "",
                "status": i.get("status") or "",
                "created_at": i.get("created_at"),
                "brief_anchored": bool(i.get("brief_anchored")),
                "source": source,
                "open": dict(i.get("open") or {}),
            })
        return out
    except Exception:  # noqa: BLE001 — a listing hiccup must never fail the send
        logger.warning("chat artifact listing failed", exc_info=True)
        return []


def _chat_artifact_counts(
    company, list_kind: str | None, dataset: str | None = None,
    project_id: int | None = None,
) -> dict | None:
    """Per-day tallies for a HOW-MANY ask ("how many PRDs today vs yesterday?").

    Computed over the SAME aggregation the listing reads but WITHOUT the card
    cap — the cap is presentation, and a count taken after it would report the
    page size as the library. Dates are the `created_at` calendar date in UTC
    (this product's timestamps are UTC throughout); `today`/`yesterday` are
    resolved server-side so the client never does timezone arithmetic.

    `project_id`, when set, tallies over the project's own listing instead
    (`list_artifacts_for_project` — same source swap as the card list, so a
    project surface's count and cards can never disagree with each other).

    Shape: {kind, total, today, yesterday, by_day: [{date, count}] newest-first
    (up to 14 days that actually have artifacts)}. None on any failure — the
    reply degrades to the cards alone, never fails the send."""
    try:
        if dataset is None:
            dataset = _dataset_for(company)
        if not dataset:
            return None
        from datetime import date, timedelta

        from app.db.artifacts import (
            list_artifacts_for_company,
            list_artifacts_for_project,
        )

        if project_id is not None:
            items = list_artifacts_for_project(
                project_id=project_id, dataset=dataset,
                company_id=company.company_id,
            )
        else:
            items = list_artifacts_for_company(
                dataset=dataset, company_id=company.company_id
            )
        kind = (list_kind or "all").strip() or "all"
        if kind != "all":
            items = [i for i in items if i.get("type") == kind]

        by_day: dict[str, int] = {}
        for i in items:
            created = str(i.get("created_at") or "")[:10]
            if created:
                by_day[created] = by_day.get(created, 0) + 1

        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        return {
            "kind": kind,
            "total": len(items),
            "today": by_day.get(today, 0),
            "yesterday": by_day.get(yesterday, 0),
            "by_day": [
                {"date": d, "count": by_day[d]}
                for d in sorted(by_day, reverse=True)[:14]
            ],
        }
    except Exception:  # noqa: BLE001 — a tally hiccup must never fail the send
        logger.warning("chat artifact counts failed", exc_info=True)
        return None


def _attach_open_conversations(open_result: dict, company_id: str) -> None:
    """Stamp each PRD candidate in an open-artifact lookup with the
    conversation that produced it, in place.

    What turns "open the checkout PRD" from a bare document in the panel into
    the PRD **with the chat the user had about it** (the stated requirement):
    the client resumes `conversation_id`'s thread when one exists and falls
    back to today's panel-only open when none does — an uploaded or
    brief-generated PRD never grows a fake history. Best-effort like every
    enrichment in this module."""
    try:
        from app.db.conversations import conversations_for_prds

        candidates = [
            c for c in (
                [open_result.get("artifact")] + list(open_result.get("candidates") or [])
            )
            if isinstance(c, dict) and c.get("type") == "prd"
        ]
        prd_ids = [
            c.get("prd_id") or c.get("id")
            for c in candidates
            if (c.get("prd_id") or c.get("id")) is not None
        ]
        convo_by_prd = conversations_for_prds(prd_ids, company_id)
        for c in candidates:
            convo = convo_by_prd.get(c.get("prd_id") or c.get("id"))
            c["conversation_id"] = (convo or {}).get("id")
            c["conversation_title"] = (convo or {}).get("title") or None
    except Exception:  # noqa: BLE001
        logger.warning("open-artifact conversation stamp failed", exc_info=True)

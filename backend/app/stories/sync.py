"""TWO-WAY ticket sync between a PRD's tickets and its tracker (ClickUp/Jira).

One `run_prd_sync` call is one full sync pass for one PRD. Per ticket, the
pass reconciles BOTH directions with last-writer-wins:

  Sprntly → tracker   Local edits (web panel + MCP tools, stored in
                      ticket_edits) push out: title, description, priority,
                      and — best-effort — workflow status.
  tracker → Sprntly   Tracker-side edits import back as ticket_edits
                      overrides: title, description (normalized back to the
                      labeled-text form), and workflow status (mapped onto
                      Sprntly's vocabulary). Assignee/url refresh as display
                      state on every pass.

Change detection: each pass stores, per ticket, a `content_hash` of the
tracker's title+description and the pass timestamp (`synced_at`) on the
prd_ticket_sync row. Next pass, "remote changed" = tracker hash differs from
the stored one; "local changed" = the ticket_edits row is newer than
`synced_at`. Both changed → the newer side wins (timestamps). Neither → no
API writes at all, so the 15-minute cadence stays cheap.

Tickets never pushed (no mapping row) bulk-create through the same idempotent
path the manual push uses (checklists + dependency links included). Stories
rehydrated here carry `pinned_id`, so an edited title/body keeps the SAME
mapping row and updates the same tracker task instead of creating a duplicate.

The sync destination (`prd_ticket_sync` row) is created by the first manual
push from the web; the scheduler's ticket_sync job then runs this for every
auto_sync row on an interval, and the web's sync button runs it ad-hoc.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.connectors import clickup_oauth, jira_oauth
from app.db.clickup_sync import get_clickup_task_id
from app.db.client import require_client, utc_now
from app.db.jira_sync import get_jira_issue_key
from app.db.prd_tickets import get_tickets
from app.db.ticket_sync import (
    STALE_SYNC_MINUTES,
    get_sync_config,
    mark_syncing,
    save_sync_result,
)
from app.stories.generate import Story
from app.stories.push import (
    _clickup_access_token,
    _jira_creds,
    push_stories_to_clickup,
    push_stories_to_jira,
)

logger = logging.getLogger(__name__)

#: Providers this ENGINE implements (a _Tracker branch + a push pair in
#: app.stories.push). Eligibility for the product feature is narrower — see
#: ticket_sync_providers(): a provider must also be TYPED task-tracking in
#: app/connectors/catalog.py. Adding a tool = catalog type + an entry here +
#: the engine branches (+ the web's TRACKERS catalog).
SYNC_PROVIDERS = ("clickup", "jira")


def ticket_sync_providers() -> tuple[str, ...]:
    """Providers tickets may sync with: typed `task-tracking` in the connector
    catalog AND implemented by this engine. Types drive discovery; the engine
    is the authority on capability."""
    from app.connectors.catalog import TASK_TRACKING, providers_with_type

    return tuple(
        p for p in providers_with_type(TASK_TRACKING) if p in SYNC_PROVIDERS
    )


class TicketSyncNotConfiguredError(LookupError):
    """Raised when a PRD has no sync destination yet (never pushed)."""


# ── Ticket keys (web/MCP parity) ─────────────────────────────────────────────


def title_slug(title: str | None) -> str:
    """The web's legacy ticket-key slug fallback (ticketKeyFor mirror):
    lowercase, non-alphanumeric runs → '-', trimmed, first 60 chars."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "ticket").lower()).strip("-")[:60]
    return slug or "ticket"


def ticket_key_for(prd_id: int, story: dict[str, Any]) -> str:
    """The composed ticket key ("prd-{prd_id}-{story_id}") every ticket_edits /
    ticket_comments row is stored under — the same format the web's
    ticketKeyFor and the MCP surface compose."""
    sid = story.get("id")
    if sid:
        return f"prd-{prd_id}-{sid}"
    return f"prd-{prd_id}-{title_slug(story.get('title'))}"


# ── Local (Sprntly-side) content forms ───────────────────────────────────────


def _apply_edit(story: Story, edit: dict[str, Any]) -> Story:
    """Overlay a ticket_edits row onto a rehydrated Story. Overrides win where
    set (non-null); the story's pinned_id keeps its identity stable."""
    if edit.get("title") is not None:
        story.title = edit["title"]
    if edit.get("description") is not None:
        # The description override is the labeled-text serialization of the
        # whole description — it REPLACES the structured sections, so the
        # pushed task shows exactly what the user sees in Sprntly.
        story.body = edit["description"]
        story.what = story.why_now = story.user_story = story.out_of_scope = ""
        story.scope = []
    if edit.get("acceptance_criteria") is not None:
        story.acceptance_criteria = [str(x) for x in edit["acceptance_criteria"]]
    if edit.get("priority") is not None:
        story.priority = edit["priority"]
    if edit.get("subtasks") is not None:
        story.subtasks = [str(x) for x in edit["subtasks"]]
    return story


def story_editable_text(s: Story) -> str:
    """The story's description in the labeled-text form the web edits and the
    override column stores (mirror of the web's storyToEditableText). Used to
    compare an imported tracker description against the current local one."""
    parts: list[str] = []
    if s.what:
        parts.append(f"What\n{s.what}")
    if s.why_now:
        parts.append(f"Why now\n{s.why_now}")
    if s.user_story:
        parts.append(f"User story\n{s.user_story}")
    if s.scope:
        parts.append("The ticket must cover\n" + "\n".join(f"- {x}" for x in s.scope))
    if s.out_of_scope:
        parts.append(f"Out of scope\n{s.out_of_scope}")
    return "\n\n".join(parts) if parts else (s.body or "")


def _ticket_contexts(company_id: str, prd_id: int) -> list[dict[str, Any]]:
    """Per-ticket sync context: the stored base story, its ticket_edits row
    (None when untouched), and the merged Story that pushes render from."""
    row = get_tickets(company_id, prd_id)
    raw = [s for s in (row.get("stories") if row else None) or [] if isinstance(s, dict)]
    if not raw:
        return []

    edits = (
        require_client().table("ticket_edits")
        .select("ticket_key, title, description, acceptance_criteria, priority, subtasks, status, updated_at")
        .eq("company_id", company_id)
        .like("ticket_key", f"prd-{prd_id}-%")
        .execute()
        .data
        or []
    )
    edit_by_key = {e["ticket_key"]: e for e in edits}

    out: list[dict[str, Any]] = []
    for s in raw:
        key = ticket_key_for(prd_id, s)
        edit = edit_by_key.get(key)
        story = Story.from_dict(s)  # pins the stored id
        if edit:
            story = _apply_edit(story, edit)
        out.append({
            "base": s,
            "edit": edit,
            "merged": story,
            "key": key,
            "tid": story.stable_id(),
        })
    return out


def merged_stories_for_prd(company_id: str, prd_id: int) -> list[Story]:
    """The PRD's stored tickets with every saved override applied — what the
    user (or an MCP client) last saw/edited, not the generator's first draft."""
    return [c["merged"] for c in _ticket_contexts(company_id, prd_id)]


# ── Import normalization (tracker → Sprntly) ─────────────────────────────────

# Section labels as the push renders them (bold markdown) → the labeled-text
# form parseDescBlocks recognizes. "Scope" is renamed on the way out, so map
# it back on the way in.
_IMPORT_LABELS = {
    "**What**": "What",
    "**Why now**": "Why now",
    "**User story**": "User story",
    "**Scope**": "The ticket must cover",
    "**Out of scope**": "Out of scope",
}

# Sections the push APPENDS to the description render (they live as their own
# fields in Sprntly). On import, everything from the first of these on is cut
# so acceptance criteria / child issues never duplicate into the description.
_GENERATED_TAIL = re.compile(
    r"^(\*\*Acceptance criteria\*\*|## Acceptance criteria|\*\*Child issues\*\*|## Child issues|## Notes|_Provenance:.*|_Route:.*)\s*$"
)


def normalize_imported_description(text: str) -> str:
    """A tracker-side description → the labeled-text override form: cut the
    generated tail sections (AC / child issues / provenance), and turn the
    bold section headers the push rendered back into plain labels."""
    lines: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if _GENERATED_TAIL.match(stripped):
            break
        lines.append(_IMPORT_LABELS.get(stripped, line))
    return "\n".join(lines).strip()


# ── Status vocabulary mapping ────────────────────────────────────────────────

_SPRNTLY_TO_CLICKUP_STATUS = {
    "backlog": "to do", "to do": "to do", "in progress": "in progress",
    "review": "review", "done": "complete",
}
_SPRNTLY_TO_JIRA_STATUS = {
    "backlog": "To Do", "to do": "To Do", "in progress": "In Progress",
    "review": "In Review", "done": "Done",
}


def tracker_status_to_sprntly(status: str | None) -> str | None:
    """Map a tracker's (free-form, per-workspace) status name onto Sprntly's
    vocabulary. None for names we can't confidently place — an unknown status
    is displayed on the chip but never imported over the local one."""
    v = (status or "").strip().lower()
    if not v:
        return None
    if "progress" in v or v == "doing":
        return "In progress"
    if "review" in v or v == "qa":
        return "Review"
    if "done" in v or "complet" in v or "closed" in v or "resolved" in v:
        return "Done"
    if "backlog" in v:
        return "Backlog"
    if v in ("to do", "todo", "open", "new"):
        return "To do"
    return None


# ── Change detection / direction ─────────────────────────────────────────────


def parse_ts(raw: Any) -> datetime | None:
    """Best-effort ISO/timestamptz parse ('Z', '+00', ms-epoch tolerated)."""
    if not raw:
        return None
    s = str(raw).strip().replace("Z", "+00:00").replace(" ", "T", 1)
    if re.fullmatch(r"[+-]?\d{13}", s):  # ms epoch
        return datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc)
    if re.search(r"[+-]\d{2}$", s):  # '+00' → '+00:00'
        s += ":00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def content_hash(title: str | None, description: str | None) -> str:
    """Fingerprint of a tracker task's content, for remote-change detection."""
    seed = f"{title or ''}\x1f{description or ''}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:16]


def decide_direction(
    *, local_changed: bool, remote_changed: bool,
    local_time: Any = None, remote_time: Any = None,
) -> str:
    """Which way content flows for one ticket this pass: 'push', 'import', or
    'none'. Both sides changed → last writer wins; uncomparable timestamps →
    Sprntly wins (it is the system of record when in doubt)."""
    if remote_changed and local_changed:
        lt, rt = parse_ts(local_time), parse_ts(remote_time)
        if lt and rt and rt > lt:
            return "import"
        return "push"
    if remote_changed:
        return "import"
    if local_changed:
        return "push"
    return "none"


def sync_in_flight(cfg: dict[str, Any]) -> bool:
    """True when the row says a sync is running AND it started recently —
    a 'syncing' row older than STALE_SYNC_MINUTES is a crashed run and may be
    taken over."""
    if (cfg.get("sync_status") or "idle") != "syncing":
        return False
    started = parse_ts(cfg.get("sync_started_at"))
    if started is None:
        return False
    return datetime.now(timezone.utc) - started < timedelta(minutes=STALE_SYNC_MINUTES)


# ── Tracker adapter ──────────────────────────────────────────────────────────


class _Tracker:
    """Thin per-provider adapter the two-way pass drives. Credentials resolve
    once per pass; every method is per-ticket except bulk_create (the manual
    push path, reused so new tickets get checklists + dependency links)."""

    def __init__(self, provider: str, company_id: str, destination: str):
        self.provider = provider
        self.company_id = company_id
        self.destination = destination
        if provider == "clickup":
            self._token = _clickup_access_token(company_id)
        elif provider == "jira":
            self._token, self._cloud = _jira_creds(company_id)
            self._site = jira_oauth._site_url_for_cloud(self._token, self._cloud)
        else:
            raise ValueError(f"unknown sync provider {provider!r}")

    def task_ref(self, ticket_id: str) -> str | None:
        """The tracker-side id previously created for this ticket, or None."""
        if self.provider == "clickup":
            return get_clickup_task_id(self.company_id, self.destination, ticket_id)
        return get_jira_issue_key(self.company_id, self.destination, ticket_id)

    def remote(self, ref: str) -> dict[str, Any] | None:
        """The task/issue's current state (see get_task/get_issue), or None
        when the fetch fails (deleted task, transient error)."""
        if self.provider == "clickup":
            state = clickup_oauth.get_task(self._token, ref)
        else:
            state = jira_oauth.get_issue(self._token, self._cloud, ref, site_url=self._site)
        return state or None

    def push(self, ref: str, story: Story) -> None:
        """Update the tracker task from the merged local story (content out)."""
        if self.provider == "clickup":
            clickup_oauth.update_task(
                self._token, ref,
                name=story.title,
                markdown_description=story.to_description(),
                priority=story.clickup_priority(),
            )
        else:
            jira_oauth.update_issue(
                self._token, self._cloud, ref,
                summary=story.title,
                description=story.to_description(),
                priority_name=story.jira_priority(),
            )

    def set_status(self, ref: str, sprntly_status: str) -> bool:
        """Best-effort Sprntly→tracker status write. Tracker workflows are
        custom per workspace/project, so failure is expected and non-fatal."""
        key = sprntly_status.strip().lower()
        try:
            if self.provider == "clickup":
                mapped = _SPRNTLY_TO_CLICKUP_STATUS.get(key)
                if not mapped:
                    return False
                clickup_oauth.set_task_status(self._token, ref, mapped)
                return True
            mapped = _SPRNTLY_TO_JIRA_STATUS.get(key)
            if not mapped:
                return False
            return jira_oauth.transition_issue(self._token, self._cloud, ref, mapped)
        except Exception:  # noqa: BLE001 — status push never fails the pass
            logger.info("status push failed for %s (%s)", ref, sprntly_status)
            return False

    def bulk_create(self, stories: list[Story]) -> dict[str, Any]:
        """First push for never-created tickets — the manual push path, which
        also saves the id mappings and creates checklists/dependency links."""
        if self.provider == "clickup":
            return push_stories_to_clickup(self.company_id, self.destination, stories)
        return push_stories_to_jira(self.company_id, self.destination, stories)


# ── Import writer ────────────────────────────────────────────────────────────


def _write_import(
    company_id: str, ticket_key: str, fields: dict[str, Any], now: str
) -> None:
    """Record tracker-side edits as a ticket_edits override — the same row the
    web panel and MCP tools write, so every surface sees the imported change."""
    require_client().table("ticket_edits").upsert(
        {
            "company_id": company_id,
            "ticket_key": ticket_key,
            **fields,
            # Stamped with the pass's own `synced_at` so the import doesn't
            # read as a fresh local edit on the next pass.
            "updated_at": now,
        },
        on_conflict="company_id,ticket_key",
    ).execute()


# ── The pass ─────────────────────────────────────────────────────────────────


def run_prd_sync(company_id: str, prd_id: int) -> dict[str, Any]:
    """One full two-way sync pass for one PRD (see module docstring).

    Raises TicketSyncNotConfiguredError when the PRD was never pushed. Any
    other failure is recorded on the sync row (last_error) and re-raised.
    Returns `{"pushed", "imported", "push_errors", "statuses"}`.
    """
    cfg = get_sync_config(company_id, prd_id)
    if cfg is None:
        raise TicketSyncNotConfiguredError(f"PRD {prd_id} has no sync destination")
    provider = cfg.get("provider") or ""
    destination = cfg.get("destination_id") or ""

    mark_syncing(company_id, prd_id)
    try:
        result = _two_way_pass(
            company_id, prd_id, provider, destination,
            prev_statuses=cfg.get("statuses") or {},
        )
        error = (
            f"{result['push_errors']} ticket(s) failed to push"
            if result["push_errors"] else None
        )
        save_sync_result(
            company_id, prd_id, statuses=result["statuses"], error=error
        )
        return result
    except Exception as e:
        # Leave the row idle with the failure recorded so the UI shows it and
        # the next tick retries; then let the caller see the real exception.
        try:
            save_sync_result(company_id, prd_id, error=str(e)[:500])
        except Exception:  # noqa: BLE001 — never mask the original failure
            logger.exception("ticket sync: failed to record error for prd %s", prd_id)
        raise


def _two_way_pass(
    company_id: str,
    prd_id: int,
    provider: str,
    destination: str,
    *,
    prev_statuses: dict[str, Any],
) -> dict[str, Any]:
    ctxs = _ticket_contexts(company_id, prd_id)
    if not ctxs:
        return {"pushed": 0, "imported": 0, "push_errors": 0, "statuses": {}}

    tracker = _Tracker(provider, company_id, destination)
    now = utc_now()
    statuses: dict[str, Any] = {}
    pushed = imported = push_errors = 0

    # Never-created tickets bulk-create through the manual push path first
    # (mapping rows + checklists + dependency links), then baseline below.
    creates = [c for c in ctxs if tracker.task_ref(c["tid"]) is None]
    if creates:
        result = tracker.bulk_create([c["merged"] for c in creates])
        pushed += len(result.get("created") or [])
        push_errors += len(result.get("errors") or [])

    for c in ctxs:
        tid = c["tid"]
        prev = dict(prev_statuses.get(tid) or {})
        ref = tracker.task_ref(tid)
        if ref is None:  # create failed — keep whatever we knew, retry next pass
            if prev:
                statuses[tid] = prev
            continue
        remote = tracker.remote(ref)
        if remote is None:  # transient fetch failure — don't guess, keep prev
            if prev:
                statuses[tid] = prev
            continue

        was_created = any(x is c for x in creates)
        remote_hash = content_hash(remote.get("title"), remote.get("description"))
        prev_hash = prev.get("content_hash")
        synced_at = parse_ts(prev.get("synced_at"))
        local_time = parse_ts((c["edit"] or {}).get("updated_at"))
        local_changed = bool(local_time and synced_at and local_time > synced_at)
        remote_changed = bool(prev_hash) and remote_hash != prev_hash

        if was_created or not prev_hash:
            # First two-way pass for this ticket (fresh create, or a row from
            # before two-way sync). No baseline to compare against, so import
            # nothing; but a ticket carrying local content edits pushes once —
            # Sprntly is the source of record when history is unknowable.
            has_content_edit = any(
                (c["edit"] or {}).get(f) is not None
                for f in ("title", "description", "acceptance_criteria", "priority", "subtasks")
            )
            direction = "push" if (not was_created and has_content_edit) else "baseline"
        else:
            direction = decide_direction(
                local_changed=local_changed, remote_changed=remote_changed,
                local_time=(c["edit"] or {}).get("updated_at"),
                remote_time=remote.get("updated_at"),
            )

        sprntly_status = (c["edit"] or {}).get("status")
        import_fields: dict[str, Any] = {}

        if direction == "import":
            remote_title = (remote.get("title") or "").strip()
            remote_text = normalize_imported_description(remote.get("description") or "")
            if remote_title and remote_title != c["merged"].title:
                import_fields["title"] = remote_title
            if remote_text and remote_text != story_editable_text(c["merged"]):
                import_fields["description"] = remote_text
        elif direction == "push":
            try:
                tracker.push(ref, c["merged"])
                pushed += 1
                # Re-read so the stored hash reflects the tracker's own
                # normalization of what we sent (hash comparisons stay
                # self-consistent; no false "remote changed" next pass).
                refreshed = tracker.remote(ref)
                if refreshed:
                    remote = refreshed
                remote_hash = content_hash(remote.get("title"), remote.get("description"))
            except Exception as e:  # noqa: BLE001 — isolate per-ticket failures
                logger.warning("ticket sync push failed for %s: %s", c["merged"].title, e)
                push_errors += 1
                remote_hash = prev_hash or remote_hash

        # ── Status, reconciled independently of content ──
        # Tracker moved it since last pass → import (devs work in the tracker).
        # Else Sprntly moved it since last pass → best-effort push out.
        remote_status = remote.get("status")
        if prev.get("status") is not None and remote_status != prev.get("status"):
            mapped = tracker_status_to_sprntly(remote_status)
            if mapped and mapped != (sprntly_status or "Backlog"):
                import_fields["status"] = mapped
                sprntly_status = mapped
        elif (
            prev_hash  # not the baseline pass
            and sprntly_status
            and sprntly_status != prev.get("sprntly_status")
        ):
            tracker.set_status(ref, sprntly_status)

        if import_fields:
            _write_import(company_id, c["key"], import_fields, now)
            imported += 1

        statuses[tid] = {
            "status": remote_status,
            "assignee": remote.get("assignee"),
            "url": remote.get("url"),
            "content_hash": remote_hash,
            "synced_at": now,
            "sprntly_status": sprntly_status,
        }

    return {
        "pushed": pushed, "imported": imported,
        "push_errors": push_errors, "statuses": statuses,
    }

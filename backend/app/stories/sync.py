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

from app.connectors import asana_oauth, clickup_oauth, jira_oauth
from app.db.asana_sync import get_asana_task_gid
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
    _asana_creds,
    _clickup_access_token,
    _jira_creds,
    push_asana_subtasks,
    push_stories_to_asana,
    push_stories_to_clickup,
    push_stories_to_jira,
)

logger = logging.getLogger(__name__)

#: Providers this ENGINE implements (a _Tracker branch + a push pair in
#: app.stories.push). Eligibility for the product feature is narrower — see
#: ticket_sync_providers(): a provider must also be TYPED task-management in
#: app/connectors/catalog.py. Adding a tool = catalog type + an entry here +
#: the engine branches (+ the web's TRACKERS catalog).
SYNC_PROVIDERS = ("clickup", "jira", "asana")


def ticket_sync_providers() -> tuple[str, ...]:
    """Providers tickets may sync with: typed `task-management` in the
    connector catalog AND implemented by this engine. Types drive discovery;
    the engine is the authority on capability."""
    from app.connectors.catalog import TASK_MANAGEMENT, providers_with_type

    return tuple(
        p for p in providers_with_type(TASK_MANAGEMENT) if p in SYNC_PROVIDERS
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
    if edit.get("issue_type") is not None:
        # Dynamic attr (same pattern as assignee_account_id): the Jira push
        # reads it per story for issuetype; ClickUp has no issue types.
        story.jira_issue_type = edit["issue_type"]
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
        .select("ticket_key, title, description, acceptance_criteria, priority, subtasks, status, custom_fields, issue_type, updated_at")
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
        elif provider == "asana":
            self._token = _asana_creds(company_id)
        else:
            raise ValueError(f"unknown sync provider {provider!r}")
        # The destination's cached vocabulary (statuses/priorities/fields).
        # With meta, status + priority sync goes TRACKER-NATIVE (verbatim
        # names, no heuristics); without it (never fetched, cache down) every
        # path below falls back to the legacy Sprntly-vocabulary behavior.
        # Cache-only read: a metadata gap must never add latency or failure
        # modes to a sync pass.
        try:
            from app.db.tracker_meta import get_cached_meta
            self.meta = get_cached_meta(company_id, provider, destination)
        except Exception:  # noqa: BLE001 — meta is an enhancement, never a gate
            self.meta = None

    def meta_status(self, name: str | None) -> dict[str, Any] | None:
        """The meta's status entry matching `name` (case-insensitive), or None
        when unknown/no meta — the gate for tracker-native status handling."""
        if not self.meta or not name:
            return None
        want = name.strip().lower()
        for s in self.meta.get("statuses") or []:
            if (s.get("name") or "").strip().lower() == want:
                return s
        return None

    def editable_fields(self) -> list[dict[str, Any]]:
        """The destination's custom fields Sprntly can edit (meta whitelist).
        Empty without meta — custom-field sync is meta-gated end to end."""
        return [
            f for f in (self.meta or {}).get("fields") or [] if f.get("editable")
        ]

    def task_ref(self, ticket_id: str) -> str | None:
        """The tracker-side id previously created for this ticket, or None."""
        if self.provider == "clickup":
            return get_clickup_task_id(self.company_id, self.destination, ticket_id)
        if self.provider == "asana":
            return get_asana_task_gid(self.company_id, self.destination, ticket_id)
        return get_jira_issue_key(self.company_id, self.destination, ticket_id)

    def remote(self, ref: str) -> dict[str, Any] | None:
        """The task/issue's current state (see get_task/get_issue), or None
        when the fetch fails (deleted task, transient error). Jira fetches the
        editable custom fields in the same call; ClickUp's get_task already
        returns them."""
        if self.provider == "clickup":
            state = clickup_oauth.get_task(self._token, ref)
        elif self.provider == "asana":
            state = asana_oauth.get_task(self._token, ref, project_gid=self.destination)
        else:
            state = jira_oauth.get_issue(
                self._token, self._cloud, ref, site_url=self._site,
                extra_fields=[f["id"] for f in self.editable_fields()] or None,
            )
        return state or None

    #: builtin field id → the get_task/get_issue key carrying its raw value.
    _BUILTIN_REMOTE_KEYS = {
        "builtin:start_date": "start_date",
        "builtin:due_date": "due_date",
        "builtin:points": "points",
        "builtin:tags": "tags",
        "builtin:labels": "labels",
    }

    def remote_custom_fields(self, remote: dict[str, Any]) -> dict[str, Any]:
        """The remote state's custom-field values (built-ins included),
        decoded to the normalized shapes and keyed by field id (every
        editable field present; unset → None). Undecodable values read as
        unset — never crash a pass."""
        from app.connectors.tracker_meta import decode_field_value

        out: dict[str, Any] = {}
        by_id = {
            r.get("id"): r
            for r in remote.get("custom_fields") or [] if isinstance(r, dict)
        } if self.provider == "clickup" else None
        raw_map = remote.get("custom_fields") or {} if by_id is None else {}
        for f in self.editable_fields():
            fid = f["id"]
            if fid.startswith("builtin:"):
                raw = remote.get(self._BUILTIN_REMOTE_KEYS.get(fid, ""))
                # Tag/label lists are already normalized [str] shapes.
                if f.get("type") == "labels":
                    out[fid] = [str(x) for x in raw] if raw else None
                else:
                    out[fid] = decode_field_value(self.provider, f, raw)
            elif self.provider == "clickup":
                out[fid] = decode_field_value(
                    "clickup", f, (by_id.get(fid) or {}).get("value")
                )
            else:
                out[fid] = decode_field_value("jira", f, raw_map.get(fid))
        return out

    #: builtin field id → the provider's write key (Jira update fields{} /
    #: ClickUp task-PUT body).
    _BUILTIN_WRITE_KEYS = {
        "builtin:start_date": "start_date",
        "builtin:due_date": {"jira": "duedate", "clickup": "due_date"},
        "builtin:points": "points",
    }

    def push_custom_fields(self, ref: str, values: dict[str, Any]) -> None:
        """Write normalized custom-field values out (Jira: one PUT batching
        every changed field, built-ins included; ClickUp: task PUT for
        built-ins + one field-endpoint call per custom field)."""
        from app.connectors.tracker_meta import encode_field_value, field_def

        if self.provider == "jira":
            extra: dict[str, Any] = {}
            for fid, v in values.items():
                fdef = field_def(self.meta, fid)
                if not fdef:
                    continue
                if fid == "builtin:labels":
                    extra["labels"] = [str(x) for x in v] if v else []
                elif fid == "builtin:due_date":
                    extra["duedate"] = str(v)[:10] if v else None
                else:
                    extra[fid] = encode_field_value("jira", fdef, v)
            if extra:
                jira_oauth.update_issue(
                    self._token, self._cloud, ref, extra_fields=extra
                )
            return

        task_patch: dict[str, Any] = {}
        for fid, v in values.items():
            fdef = field_def(self.meta, fid)
            if not fdef:
                continue
            if fid == "builtin:tags":
                # Add-only (ClickUp removal is a separate per-tag endpoint).
                for tag in v or []:
                    clickup_oauth.add_task_tag(self._token, ref, str(tag))
            elif fid.startswith("builtin:"):
                key = self._BUILTIN_WRITE_KEYS.get(fid)
                key = key["clickup"] if isinstance(key, dict) else key
                if key:
                    task_patch[key] = (
                        encode_field_value("clickup", fdef, v)
                        if v is not None else None
                    )
            elif v is not None:
                # ClickUp's field endpoint sets values; clearing (None) has
                # no uniform API — a cleared override just stops pushing.
                clickup_oauth.set_custom_field(
                    self._token, ref, fid, encode_field_value("clickup", fdef, v)
                )
        if task_patch:
            clickup_oauth.update_task(self._token, ref, extra=task_patch)

    def _priority_out(self, story: Story) -> Any:
        """The provider's priority write value for the story's priority.

        Tracker-native first: a priority matching the destination's REAL
        scheme (meta) pushes by its own name/id — so a workspace with e.g.
        "Blocker/Expedite" priorities round-trips exactly. Legacy Sprntly
        values (urgent/high/normal/low) keep flowing through the generator's
        fixed maps when meta doesn't know them."""
        if self.meta:
            from app.connectors.tracker_meta import priority_by_name

            hit = priority_by_name(self.meta, story.priority)
            if hit:
                if self.provider == "clickup":
                    try:
                        return int(hit["id"])
                    except (TypeError, ValueError):
                        pass
                else:
                    return hit.get("name")
        if self.provider == "clickup":
            return story.clickup_priority()
        return story.jira_priority()

    def push(self, ref: str, story: Story) -> None:
        """Update the tracker task from the merged local story (content out).

        Jira: when the project has a sub-task type, the story's child issues
        sync as REAL sub-tasks (missing ones created, add-only) and the
        description drops its Child issues text section."""
        if self.provider == "clickup":
            clickup_oauth.update_task(
                self._token, ref,
                name=story.title,
                markdown_description=story.to_description(),
                priority=self._priority_out(story),
            )
            return
        if self.provider == "asana":
            # Asana notes are plain text (no priority/issue-type); status is a
            # section, reconciled separately in set_status. Child issues become
            # real native subtasks (add-only), so they leave the notes body —
            # no duplication between the subtask list and the description text.
            asana_oauth.update_task(
                self._token, ref, name=story.title,
                notes=story.to_description(include_subtasks=False),
            )
            if story.subtasks:
                try:
                    push_asana_subtasks(
                        self.company_id, self.destination, ref, story.stable_id(),
                        story.subtasks, access_token=self._token,
                    )
                except Exception:  # noqa: BLE001 — children never fail the pass
                    logger.warning("Asana sub-task sync failed for %s", ref)
            return
        from app.stories.push import jira_subtask_type, push_jira_subtasks

        subtask_type = jira_subtask_type(self.company_id, self.destination)
        jira_oauth.update_issue(
            self._token, self._cloud, ref,
            summary=story.title,
            description=story.to_description(include_subtasks=subtask_type is None),
            priority_name=self._priority_out(story),
        )
        if subtask_type and story.subtasks:
            try:
                push_jira_subtasks(
                    self.company_id, self.destination, ref, story.stable_id(),
                    story.subtasks,
                    access_token=self._token, cloud_id=self._cloud,
                    subtask_type=subtask_type,
                )
            except Exception:  # noqa: BLE001 — children never fail the pass
                logger.warning("Jira sub-task sync failed for %s", ref)

    def set_status(self, ref: str, status: str) -> bool:
        """Best-effort Sprntly→tracker status write. Tracker workflows are
        custom per workspace/project, so failure is expected and non-fatal.

        Tracker-native first: a status that IS one of the destination's real
        statuses (meta) is written verbatim — for Jira via whatever transition
        lands on it. Legacy Sprntly-vocabulary values (from old edits, or when
        meta was never fetched) keep flowing through the fixed maps."""
        try:
            meta_hit = self.meta_status(status)
            if self.provider == "asana":
                # Asana has no status field: a status IS a section. Move the
                # task into the section whose name matches (meta carries the
                # section gid as the status id) and keep the `completed`
                # boolean in step with a done-category move. No meta / unknown
                # section → nothing to move to (best-effort, non-fatal).
                if not meta_hit or not meta_hit.get("id"):
                    return False
                asana_oauth.add_task_to_section(self._token, meta_hit["id"], ref)
                asana_oauth.update_task(
                    self._token, ref, completed=(meta_hit.get("category") == "done"),
                )
                return True
            if meta_hit:
                if self.provider == "clickup":
                    clickup_oauth.set_task_status(self._token, ref, status)
                    return True
                return jira_oauth.transition_issue(
                    self._token, self._cloud, ref, status
                )
            key = status.strip().lower()
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
            logger.info("status push failed for %s (%s)", ref, status)
            return False

    def add_comment(self, ref: str, text: str) -> str | None:
        """Post one comment on the tracker task/issue. Returns the tracker's
        comment id, or None on failure (best-effort — retried by the pass)."""
        if self.provider == "clickup":
            return clickup_oauth.add_task_comment(self._token, ref, text)
        if self.provider == "asana":
            return asana_oauth.add_task_comment(self._token, ref, text)
        return jira_oauth.add_issue_comment(self._token, self._cloud, ref, text)

    def set_issue_type(self, ref: str, issue_type: str) -> bool:
        """Best-effort Sprntly→Jira issue-type write. Jira may refuse a type
        change (cross-workflow moves need its Move wizard), so failure is
        expected and non-fatal. ClickUp has no issue types → always False."""
        if self.provider != "jira":
            return False
        try:
            jira_oauth.update_issue(
                self._token, self._cloud, ref,
                extra_fields={"issuetype": {"name": issue_type}},
            )
            return True
        except Exception:  # noqa: BLE001 — type push never fails the pass
            logger.info("issue-type push failed for %s (%s)", ref, issue_type)
            return False

    def meta_issue_type(self, name: str | None) -> str | None:
        """The meta issue-type name matching `name` (case-insensitive,
        non-subtask), or None — the gate for issue-type sync."""
        if not self.meta or not name:
            return None
        from app.connectors.tracker_meta import resolve_issue_type

        return resolve_issue_type(self.meta, name)

    def bulk_create(self, stories: list[Story]) -> dict[str, Any]:
        """First push for never-created tickets — the manual push path, which
        also saves the id mappings and creates checklists/dependency links."""
        if self.provider == "clickup":
            return push_stories_to_clickup(self.company_id, self.destination, stories)
        if self.provider == "asana":
            return push_stories_to_asana(self.company_id, self.destination, stories)
        return push_stories_to_jira(self.company_id, self.destination, stories)


# ── Import writer ────────────────────────────────────────────────────────────


def _write_import(
    company_id: str, ticket_key: str, fields: dict[str, Any], now: str
) -> None:
    """Record tracker-side edits as a ticket_edits override — the same row the
    web panel and MCP tools write, so every surface sees the imported change.

    `custom_fields` is MERGED over the row's existing value, never replaced:
    the one jsonb column holds many fields, so importing one remote field
    change must not clobber sibling local overrides."""
    if fields.get("custom_fields"):
        existing = (
            require_client().table("ticket_edits")
            .select("custom_fields")
            .eq("company_id", company_id).eq("ticket_key", ticket_key)
            .limit(1).execute().data
            or []
        )
        merged = dict((existing[0].get("custom_fields") if existing else None) or {})
        merged.update(fields["custom_fields"])
        fields = {**fields, "custom_fields": merged}
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


# ── Instant push (edit → tracker, no waiting for the scheduler) ─────────────


def kick_prd_sync_from_key(company_id: str, ticket_key: str) -> bool:
    """Fire-and-forget a sync pass for the PRD a just-saved ticket belongs to,
    so a Sprntly-side edit (status, priority, custom fields, description, …)
    lands in the tracker IMMEDIATELY instead of at the next scheduler tick.

    Safe to call after EVERY save: unbound PRDs and malformed keys are a
    no-op, and a pass already in flight is skipped (single-flight — the
    running pass or the next tick picks the edit up; rapid autosaves don't
    stampede the tracker API). Runs in a daemon thread because the save
    routes are sync-def (threadpool) with no event loop to schedule on.
    Returns True when a pass was actually started."""
    parts = ticket_key.split("-", 2)
    if not (len(parts) == 3 and parts[0] == "prd" and parts[1].isdigit()):
        return False
    prd_id = int(parts[1])
    try:
        cfg = get_sync_config(company_id, prd_id)
        if cfg is None or sync_in_flight(cfg):
            return False
        # Mark before spawning so a second save in the same instant reads
        # "syncing" and skips (mirrors the trigger_sync route).
        mark_syncing(company_id, prd_id)
    except Exception:  # noqa: BLE001 — instant push is an enhancement only
        logger.warning("instant sync kick failed for %s", ticket_key)
        return False

    import threading

    def _run() -> None:
        try:
            run_prd_sync(company_id, prd_id)
        except Exception:  # noqa: BLE001 — recorded on the row by run_prd_sync
            logger.exception("instant ticket sync failed for prd %s", prd_id)

    threading.Thread(
        target=_run, daemon=True, name=f"ticket-sync-{prd_id}"
    ).start()
    return True


# ── Comment push (Sprntly → tracker, one-way) ────────────────────────────────
#
# A Sprntly comment on a bound ticket becomes a REAL tracker comment
# ("Author: body"), pushed instantly at comment time; the sync pass catches
# up any comment that failed. One-way by product decision — tracker-side
# comments are not imported. `ticket_comments.tracker_comment_id` records the
# pushed id (the dedupe: NULL = not pushed yet).


def _mark_comment_pushed(comment_id: int, tracker_comment_id: str) -> None:
    require_client().table("ticket_comments").update(
        {"tracker_comment_id": tracker_comment_id}
    ).eq("id", comment_id).execute()


def _comment_text(author: str | None, body: str | None) -> str:
    return f"{author or 'Sprntly'}: {body or ''}".strip()


def kick_comment_push(
    company_id: str, ticket_key: str, comment_id: int, author: str, body: str
) -> bool:
    """Fire-and-forget push of one fresh comment to the bound tracker.
    No-op (False) for unbound PRDs, malformed keys, or never-pushed tickets;
    a failed push stays unmarked and the next sync pass retries it."""
    parts = ticket_key.split("-", 2)
    if not (len(parts) == 3 and parts[0] == "prd" and parts[1].isdigit()):
        return False
    try:
        cfg = get_sync_config(company_id, int(parts[1]))
    except Exception:  # noqa: BLE001 — comment push is an enhancement only
        return False
    if cfg is None:
        return False

    import threading

    def _run() -> None:
        try:
            tracker = _Tracker(
                cfg["provider"], company_id, cfg["destination_id"]
            )
            ref = tracker.task_ref(parts[2])
            if ref is None:
                return  # never pushed — the pass creates it, then catches up
            tracker_cid = tracker.add_comment(ref, _comment_text(author, body))
            if tracker_cid:
                _mark_comment_pushed(comment_id, tracker_cid)
        except Exception:  # noqa: BLE001 — best-effort; the pass retries
            logger.warning("instant comment push failed for %s", ticket_key)

    threading.Thread(
        target=_run, daemon=True, name=f"comment-push-{comment_id}"
    ).start()
    return True


def _unpushed_comments(
    company_id: str, prd_id: int, bound_since: Any
) -> dict[str, list[dict[str, Any]]]:
    """Unpushed comments per ticket_key, restricted to comments created AFTER
    the PRD was bound — pre-binding history must never flood the tracker
    (it already travels in the pushed description's Notes section)."""
    rows = (
        require_client().table("ticket_comments")
        .select("id, ticket_key, author, body, tracker_comment_id, created_at")
        .eq("company_id", company_id)
        .like("ticket_key", f"prd-{prd_id}-%")
        .order("created_at")
        .execute().data
        or []
    )
    since = parse_ts(bound_since)
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("tracker_comment_id") is not None:
            continue
        created = parse_ts(r.get("created_at"))
        if since and created and created <= since:
            continue
        out.setdefault(r["ticket_key"], []).append(r)
    return out


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
            bound_since=cfg.get("created_at"),
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
    bound_since: Any = None,
) -> dict[str, Any]:
    ctxs = _ticket_contexts(company_id, prd_id)
    if not ctxs:
        return {"pushed": 0, "imported": 0, "push_errors": 0, "statuses": {}}

    tracker = _Tracker(provider, company_id, destination)
    now = utc_now()
    # Comments not yet pushed (instant push failed / made while the pass ran),
    # post-binding only — see _unpushed_comments.
    try:
        pending_comments = _unpushed_comments(company_id, prd_id, bound_since)
    except Exception:  # noqa: BLE001 — comment catch-up never blocks a pass
        pending_comments = {}
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
        # With meta the imported value is the tracker's own status name,
        # VERBATIM — the whole point of tracker-native vocabulary; the old
        # substring heuristics remain only as the no-meta fallback.
        # Gate on `prev_hash` (baseline established), not on a non-None status:
        # Asana tasks can legitimately have NO section (status is None), so a
        # move from no-section INTO a section is a real change to import — the
        # old `status is not None` guard silently dropped it.
        remote_status = remote.get("status")
        if prev_hash and remote_status != prev.get("status"):
            if tracker.meta_status(remote_status):
                mapped = remote_status
            else:
                mapped = tracker_status_to_sprntly(remote_status)
            if mapped and mapped != (sprntly_status or "Backlog"):
                import_fields["status"] = mapped
                sprntly_status = mapped
        elif (
            sprntly_status
            and sprntly_status != prev.get("sprntly_status")
            # Push a local status edit even on the BASELINE pass (a status set
            # before the first push must still reach the tracker) — but only
            # when it IS a real local edit, never a phantom on a bare baseline.
            and (prev_hash or (c["edit"] or {}).get("status") is not None)
        ):
            tracker.set_status(ref, sprntly_status)

        # ── Priority, tracker-native only (meta present) ──
        # Same shape as status: tracker moved it since last pass → import the
        # tracker's own priority name into the edit row (the Sprntly-side edit
        # already pushes via _priority_out). No meta → no priority import,
        # exactly the pre-meta behavior.
        remote_priority = remote.get("priority")
        if (
            tracker.meta
            and prev.get("priority") is not None
            and remote_priority != prev.get("priority")
            and remote_priority
            and remote_priority != (c["edit"] or {}).get("priority")
        ):
            import_fields["priority"] = remote_priority

        # ── Issue type (Jira), tracker-native only (meta present) ──
        # Same reconcile shape: changed in Jira since last pass → import the
        # real type name; a local edit that differs from an UNCHANGED remote
        # pushes out best-effort with no freshness gate (Jira may refuse
        # cross-workflow changes — non-fatal; an accepted change stops the
        # retry because the next pull returns the new type).
        remote_type = remote.get("issue_type")
        local_type = (c["edit"] or {}).get("issue_type")
        if (
            tracker.meta
            and prev.get("issue_type") is not None
            and remote_type != prev.get("issue_type")
            and remote_type
            and remote_type != local_type
        ):
            import_fields["issue_type"] = remote_type
        elif (
            local_type
            and remote_type
            and tracker.meta_issue_type(local_type)
            and local_type.strip().lower() != remote_type.strip().lower()
            and (prev.get("issue_type") is None or remote_type == prev.get("issue_type"))
        ):
            tracker.set_issue_type(ref, tracker.meta_issue_type(local_type))

        # ── Custom fields, tracker-native only (meta present) ──
        # Field-by-field: the tracker changed a field since last pass →
        # import (merged into the edit row). A LOCAL OVERRIDE that differs
        # from an UNCHANGED remote value always pushes — no freshness gate:
        # remote hasn't moved, so writing Sprntly's value can't clobber
        # anything (and a value that predates the first snapshot isn't
        # silently swallowed). Both moved since last pass → the tracker wins
        # (devs work there). First pass with fields (no prev snapshot):
        # existing local overrides push once — Sprntly is the record when
        # history is unknowable — and everything else just baselines.
        remote_cf: dict[str, Any] = {}
        if tracker.editable_fields():
            remote_cf = tracker.remote_custom_fields(remote)
            prev_cf = prev.get("custom_fields")
            local_cf = (c["edit"] or {}).get("custom_fields") or {}
            baseline_cf = not isinstance(prev_cf, dict)
            import_cf: dict[str, Any] = {}
            push_cf: dict[str, Any] = {}
            for fid, rv in remote_cf.items():
                pv = (prev_cf or {}).get(fid)
                lv = local_cf.get(fid, pv)
                if baseline_cf:
                    if fid in local_cf and local_cf[fid] is not None and local_cf[fid] != rv:
                        push_cf[fid] = local_cf[fid]
                elif rv != pv and rv != lv:
                    import_cf[fid] = rv
                elif fid in local_cf and lv is not None and lv != rv and rv == pv:
                    push_cf[fid] = lv
            if push_cf:
                try:
                    tracker.push_custom_fields(ref, push_cf)
                    # Snapshot what we just wrote so the next pass doesn't
                    # read our own push back as a remote change.
                    remote_cf.update(push_cf)
                    pushed += 1
                except Exception as e:  # noqa: BLE001 — never fails the pass
                    logger.warning(
                        "custom-field push failed for %s: %s", c["merged"].title, e
                    )
                    push_errors += 1
            if import_cf:
                import_fields["custom_fields"] = import_cf

        # ── Comment catch-up (one-way, Sprntly → tracker) ──
        # Post-binding comments whose instant push didn't land. Best-effort
        # per comment; an unmarked comment simply retries next pass.
        for cm in pending_comments.get(c["key"], []):
            try:
                tracker_cid = tracker.add_comment(
                    ref, _comment_text(cm.get("author"), cm.get("body"))
                )
                if tracker_cid:
                    _mark_comment_pushed(cm["id"], tracker_cid)
            except Exception:  # noqa: BLE001 — never fails the pass
                logger.warning(
                    "comment catch-up push failed for %s", c["key"]
                )

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
            "priority": remote_priority,
            "issue_type": remote_type,
            # The canonical open/in_progress/done projection (from meta's
            # statusCategory / status type) — what Sprntly reads for
            # completion semantics regardless of the workspace's vocabulary.
            # Asana is dual-signal: a task's `completed` checkbox is the
            # authoritative done marker independent of its section (the
            # primary way work is finished there), so it wins the projection.
            # `completed` is absent for ClickUp/Jira, so they are unaffected.
            "status_category": (
                "done" if remote.get("completed")
                else (tracker.meta_status(remote_status) or {}).get("category")
            ),
            # Pulled custom-field values (normalized, keyed by field id) —
            # the detail screen's read-side value when there's no override.
            "custom_fields": remote_cf,
        }

    return {
        "pushed": pushed, "imported": imported,
        "push_errors": push_errors, "statuses": statuses,
    }

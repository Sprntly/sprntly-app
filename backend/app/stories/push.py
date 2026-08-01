"""Push generated user stories into ClickUp as tasks.

This is the OUTWARD-FACING write step: it creates tasks in the user's ClickUp
workspace, so it is an explicit action, kept SEPARATE from generation (the user
reviews the stories first). It decrypts the company's ClickUp token, then
creates one task per story, error-isolated — one task failing never stops the
rest, and every failure is reported back.

Auth: ClickUp tokens are unscoped (they carry the user's full ClickUp perms),
so no extra scope is needed to create tasks; the raw-token auth quirk is handled
by clickup_oauth.create_task.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from app import db
from app.connectors import asana_oauth, clickup_oauth, jira_oauth
from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
    encrypt_token_json,
)
from app.connectors.tracker_errors import TrackerDeleteForbiddenError
from app.db.asana_sync import (
    delete_asana_task_gid,
    get_asana_task_gid,
    list_asana_subtask_gids,
    save_asana_task_gid,
)
from app.db.clickup_sync import get_clickup_task_id, save_clickup_task_id
from app.db.jira_sync import (
    delete_jira_issue_key,
    get_jira_issue_key,
    list_jira_subtask_keys,
    save_jira_issue_key,
)
from app.stories.generate import Story

logger = logging.getLogger(__name__)

CLICKUP_PROVIDER = "clickup"
JIRA_PROVIDER = "jira"
ASANA_PROVIDER = "asana"


class ClickUpNotConnectedError(LookupError):
    """Raised when the company has no active ClickUp connection."""


class JiraNotConnectedError(LookupError):
    """Raised when the company has no active Jira connection."""


class AsanaNotConnectedError(LookupError):
    """Raised when the company has no active Asana connection."""


# ── Child issues, shared across the three trackers ──────────────────────────
#
# A ticket's child issues are a plain list of labels in Sprntly. Each tracker
# stores them differently (Jira sub-tasks, Asana subtasks, a ClickUp
# checklist), but the identity rule is the same everywhere: THE LABEL IS THE
# CHILD ISSUE. Sprntly has no per-child id to carry, so a child is addressed by
# a hash of its text, and a rename reads as one removed + one added — which is
# the honest outcome, because that is exactly what the user did in Sprntly.

#: The ClickUp checklist a ticket's child issues live on.
_CHILD_CHECKLIST = "Child issues"


def _child_labels(subtasks: Iterable[str] | None) -> list[str]:
    """A ticket's child issues as clean labels: the '[P]' parallel marker
    stripped, blanks dropped, order preserved. The single definition of what
    counts as a child issue, so the three trackers can never disagree about
    whether one is present."""
    import re as _re

    out: list[str] = []
    for raw in subtasks or []:
        label = _re.sub(r"^\s*\[P\]\s*", "", str(raw)).strip()
        if label:
            out.append(label)
    return out


def _sub_id(ticket_id: str, label: str) -> str:
    """The mapping key for one child issue: `{ticket_id}#sub#{hash(label)}`.
    Content-derived because Sprntly has no stable child id to key off."""
    import hashlib

    return (
        f"{ticket_id}#sub#"
        f"{hashlib.sha256(label.encode('utf-8')).hexdigest()[:12]}"
    )


def _done_status_name(company_id: str, provider: str, destination: str) -> str | None:
    """The destination's own name for a completed status (first with a `done`
    category in cached tracker metadata), or None when unknown.

    The close fallback's vocabulary: when a tracker REFUSES to delete something
    (Jira's "Delete Issues" permission is the usual reason), the removal still
    has to land somehow, and closing the item is the honest approximation.
    Reads the customer's real status names — "Complete", "Shipped", whatever
    their workflow calls it — never a hardcoded "Done".
    """
    try:
        from app.db.tracker_meta import get_cached_meta

        meta = get_cached_meta(company_id, provider, destination)
    except Exception:  # noqa: BLE001 — meta is an enhancement, never a gate
        return None
    for s in (meta or {}).get("statuses") or []:
        if s.get("category") == "done" and s.get("name"):
            return s["name"]
    return None


def _clickup_fields(story: Story) -> dict[str, Any]:
    """Map the canonical ticket's fields onto the ClickUp task-create body
    (beyond name/description/priority, which the caller sets directly).

    Only fields ClickUp accepts universally on task creation are mapped here —
    `tags` (from labels) and `points` (story points, ClickUp's native Sprint
    Points field). Fields that need list-specific config (custom statuses,
    per-site custom fields) or extra API calls (checklists from subtasks,
    dependency links) are intentionally left to the sync follow-up so a push
    never fails on a workspace that lacks them."""
    fields: dict[str, Any] = {}
    if story.labels:
        fields["tags"] = list(story.labels)
    if story.story_points is not None:
        fields["points"] = story.story_points
    return fields


def _clickup_access_token(company_id: str) -> str:
    """Decrypt the company's stored ClickUp access token. Raises
    ClickUpNotConnectedError if not connected or the token is unusable."""
    row = db.get_connection(company_id, CLICKUP_PROVIDER)
    if not row or not row.get("token_json_encrypted"):
        raise ClickUpNotConnectedError("ClickUp is not connected for this company")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, ValueError) as e:
        raise ClickUpNotConnectedError("ClickUp token is unreadable") from e
    token = token_json.get("access_token") or ""
    if not token:
        raise ClickUpNotConnectedError("ClickUp connection has no access_token")
    return token


def push_stories_to_clickup(
    company_id: str,
    list_id: str,
    stories: Iterable[Story],
) -> dict[str, Any]:
    """Create one ClickUp task per story in `list_id`.

    Returns `{"created": [{story, task_id, url}], "errors": [{story, error}]}`.
    Error-isolated: a single task failure is captured in `errors` and the rest
    continue. Raises ClickUpNotConnectedError up-front if ClickUp isn't
    connected (nothing to push to).
    """
    access_token = _clickup_access_token(company_id)

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    # story title → ClickUp task id, for resolving in-batch dependency links.
    title_to_task: dict[str, str] = {}
    stories = list(stories)

    # ── Pass 1: create/update each task + reconcile its child-issue checklist ──
    for story in stories:
        try:
            ticket_id = story.stable_id()
            existing = get_clickup_task_id(company_id, list_id, ticket_id)
            common = dict(
                name=story.title,
                # Rich body so the five-section description + acceptance criteria
                # render as headings/bullets in ClickUp rather than raw markdown.
                markdown_description=story.to_description(),
                priority=story.clickup_priority(),
                extra=_clickup_fields(story),
            )
            if existing:
                # Idempotent re-push: update the task we created before rather
                # than creating a duplicate.
                task = clickup_oauth.update_task(access_token, existing, **common)
                task_id = task.get("id") or existing
            else:
                task = clickup_oauth.create_task(access_token, list_id, **common)
                task_id = task.get("id")
                if task_id:
                    save_clickup_task_id(company_id, list_id, ticket_id, task_id)
            if task_id:
                # Child issues → a ClickUp checklist, reconciled on EVERY push,
                # not just the first. Create-only was why a child issue removed
                # (or added, or renamed) in Sprntly never reached ClickUp.
                _reconcile_subtasks_checklist(access_token, task_id, story)
                title_to_task[story.title] = task_id
            created.append({
                "story": story.title,
                "task_id": task_id,
                "url": task.get("url"),
                "updated": bool(existing),
            })
        except Exception as e:  # noqa: BLE001 — isolate per-story failures
            logger.warning("ClickUp push failed for story %r: %s", story.title, e)
            errors.append({"story": story.title, "error": str(e)})

    # ── Pass 2: link dependencies now that every task in the batch has an id ──
    for story in stories:
        src = title_to_task.get(story.title)
        if not src or not story.blocked_by:
            continue
        for dep in story.blocked_by:
            target = _resolve_dep(dep, title_to_task)
            if not target or target == src:
                continue
            try:
                # blocked_by ⇒ this task waits on the target (depends_on).
                clickup_oauth.add_dependency(access_token, src, depends_on=target)
            except Exception as e:  # noqa: BLE001 — a link failure never fails the push
                logger.warning("ClickUp dependency link failed %s→%s: %s", story.title, dep, e)

    return {"created": created, "errors": errors}


def _reconcile_subtasks_checklist(
    access_token: str, task_id: str, story: Story,
    *, checklists: list[dict[str, Any]] | None = None,
) -> None:
    """Make the task's 'Child issues' checklist match the ticket's child issues
    exactly — items added, and items the user REMOVED in Sprntly deleted.

    This used to run on create only, which meant a child issue deleted in
    Sprntly stayed on the customer's ClickUp checklist forever (and an edited
    list never reached ClickUp at all). Reconciling by NAME is right here
    because a ClickUp checklist item has no stable identity of its own — the
    label IS the child issue, so a renamed item reads as one removed and one
    added, which is exactly what should happen in ClickUp.

    `checklists` lets the sync pass hand over the ones its own task read
    already returned; omit it and they're fetched. Best-effort throughout —
    never fails the push.
    """
    labels = _child_labels(story.subtasks)
    try:
        if checklists is None:
            checklists = clickup_oauth.get_task_checklists(access_token, task_id)
        existing = next(
            (c for c in checklists if (c.get("name") or "") == _CHILD_CHECKLIST), None
        )
        if not labels:
            # The last child issue was removed. ClickUp leaves an empty
            # checklist behind, so drop the whole thing rather than a bare
            # "Child issues" heading the ticket no longer has.
            if existing:
                clickup_oauth.delete_checklist(access_token, existing["id"])
            return
        if existing is None:
            checklist_id = clickup_oauth.create_checklist(
                access_token, task_id, _CHILD_CHECKLIST
            )
            if not checklist_id:
                return
            for label in labels:
                clickup_oauth.create_checklist_item(access_token, checklist_id, label)
            return

        wanted = set(labels)
        kept: set[str] = set()
        for item in existing.get("items") or []:
            name = item.get("name") or ""
            # `name in kept` prunes a DUPLICATE of a wanted label too — one
            # child issue must not show as two rows after a re-push.
            if name in wanted and name not in kept:
                kept.add(name)
                continue
            clickup_oauth.delete_checklist_item(
                access_token, existing["id"], item["id"]
            )
        for label in labels:
            if label not in kept:
                clickup_oauth.create_checklist_item(
                    access_token, existing["id"], label
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("ClickUp checklist sync failed for %r: %s", story.title, e)


def _resolve_dep(dep: str, title_to_task: dict[str, str]) -> str | None:
    """Resolve a dependency reference (e.g. 'T-1 — Competitive Positioning
    One-Pager' or a bare title) to a ClickUp task id in this batch, by matching
    a known ticket title as a substring. In-batch only — cross-batch links need
    the reconcile pass (future work)."""
    d = dep.lower()
    for title, task_id in title_to_task.items():
        if title and title.lower() in d:
            return task_id
    return None


def pull_clickup_status(
    company_id: str, list_id: str, ticket_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Bidirectional read: for each ticket already synced to `list_id`, fetch its
    current ClickUp state (status, assignee, url) so Sprntly reflects work done
    in the tracker. Keyed by the ticket's stable_id; tickets never pushed (no
    mapping row) are simply absent from the result. Best-effort per ticket."""
    access_token = _clickup_access_token(company_id)
    out: dict[str, dict[str, Any]] = {}
    for ticket_id in ticket_ids:
        task_id = get_clickup_task_id(company_id, list_id, ticket_id)
        if not task_id:
            continue
        state = clickup_oauth.get_task(access_token, task_id)
        if state.get("status") or state.get("assignee"):
            out[ticket_id] = state
    return out


# ── Jira push ────────────────────────────────────────────────────────────────
#
# Mirrors the ClickUp push but targets a Jira PROJECT (not a list) and creates
# issues. Two Jira-specific concerns: access tokens expire in ~1h (so we refresh
# + persist before pushing), and every REST call needs the site's cloud_id
# (cached on the connection's config_json at connect time).


def _jira_creds(company_id: str) -> tuple[str, str]:
    """Return `(access_token, cloud_id)` for the company's Jira connection,
    refreshing + persisting an expired access token first.

    Raises JiraNotConnectedError if not connected, the token is unreadable, or
    the connection has no cloud_id / no way to refresh an expired token."""
    import time

    row = db.get_connection(company_id, JIRA_PROVIDER)
    if not row or not row.get("token_json_encrypted"):
        raise JiraNotConnectedError("Jira is not connected for this company")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, ValueError) as e:
        raise JiraNotConnectedError("Jira token is unreadable") from e

    # Refresh an expired/near-expiry access token and persist the rotated payload.
    obtained_at = token_json.get("obtained_at", 0)
    expires_in = token_json.get("expires_in", 3600)
    refresh_token = token_json.get("refresh_token")
    if refresh_token and time.time() > obtained_at + expires_in - 120:
        try:
            token_json = json.loads(
                jira_oauth.token_payload_to_store(
                    jira_oauth.refresh_access_token(refresh_token)
                )
            )
            db.update_connection_tokens(
                company_id, JIRA_PROVIDER, encrypt_token_json(json.dumps(token_json))
            )
        except jira_oauth.JiraAuthExpiredError as e:
            raise JiraNotConnectedError(str(e)) from e

    access_token = token_json.get("access_token") or ""
    if not access_token:
        raise JiraNotConnectedError("Jira connection has no access_token")
    cloud_id = (json.loads(row.get("config_json") or "{}")).get("cloud_id") \
        or jira_oauth.first_cloud_id(access_token)
    if not cloud_id:
        raise JiraNotConnectedError("Jira connection has no accessible site")
    return access_token, cloud_id


def jira_subtask_type(company_id: str, project_key: str) -> str | None:
    """The project's REAL sub-task issue type name (from cached tracker
    metadata), or None when the project has none / meta was never pulled —
    the gate for creating Sprntly child issues as real Jira sub-tasks."""
    try:
        from app.db.tracker_meta import get_cached_meta

        meta = get_cached_meta(company_id, "jira", project_key)
    except Exception:  # noqa: BLE001 — meta is an enhancement, never a gate
        return None
    for t in (meta or {}).get("issue_types") or []:
        if t.get("subtask") and t.get("name"):
            return t["name"]
    return None


def sync_jira_subtasks(
    company_id: str,
    project_key: str,
    parent_key: str,
    ticket_id: str,
    subtasks: Iterable[str],
    *,
    access_token: str,
    cloud_id: str,
    subtask_type: str,
) -> None:
    """Make the real Jira sub-tasks under `parent_key` match the ticket's child
    issues: missing ones created, ones REMOVED in Sprntly deleted.

    The removal half is what makes this a sync rather than a one-way seed. It
    used to be add-only, so deleting a child issue in Sprntly (or renaming
    one — a rename is a remove plus an add, since the label is the identity)
    left a live sub-task on the customer's board that nothing would ever clean
    up.

    What SHOULD exist comes from `subtasks`; what DOES exist comes from
    jira_issue_map's `{ticket_id}#sub#…` rows, so only sub-tasks Sprntly itself
    created are ever touched — a sub-task a developer added in Jira by hand has
    no mapping row and is left completely alone.

    Deletes take their children with them (`deleteSubtasks`) and fall back to
    CLOSING the issue when Jira refuses on permissions, which is the common
    case: "Delete Issues" is not in a default permission scheme. Per-item
    failures are isolated and logged; a child that could be neither deleted nor
    closed keeps its mapping row so the next pass retries it.
    """
    labels = _child_labels(subtasks)
    wanted = {_sub_id(ticket_id, label): label for label in labels}

    for sub_id, label in wanted.items():
        try:
            if get_jira_issue_key(company_id, project_key, sub_id):
                continue
            issue = jira_oauth.create_issue(
                access_token, cloud_id,
                project_key=project_key,
                summary=label[:255],
                issue_type=subtask_type,
                extra_fields={"parent": {"key": parent_key}},
            )
            if issue.get("key"):
                save_jira_issue_key(company_id, project_key, sub_id, issue["key"])
        except Exception as e:  # noqa: BLE001 — one child failing is isolated
            logger.warning(
                "Jira sub-task push failed under %s (%r): %s", parent_key, label, e
            )

    try:
        mapped = list_jira_subtask_keys(company_id, project_key, ticket_id)
    except Exception:  # noqa: BLE001 — no map read, no prune; adds still landed
        logger.warning("Jira sub-task prune skipped under %s (map read failed)", parent_key)
        return
    for sub_id, issue_key in mapped.items():
        if sub_id in wanted:
            continue
        if not _remove_jira_subtask(
            company_id, project_key, issue_key,
            access_token=access_token, cloud_id=cloud_id,
        ):
            continue  # keep the mapping so the next pass retries
        delete_jira_issue_key(company_id, project_key, sub_id)


def _remove_jira_subtask(
    company_id: str, project_key: str, issue_key: str,
    *, access_token: str, cloud_id: str,
) -> bool:
    """Delete one Jira sub-task, closing it instead when Jira refuses. True
    when the removal landed either way (so the caller can drop the mapping),
    False when neither worked and it should be retried next pass."""
    try:
        return jira_oauth.delete_issue(access_token, cloud_id, issue_key)
    except TrackerDeleteForbiddenError:
        done = _done_status_name(company_id, "jira", project_key)
        if done and jira_oauth.transition_issue(
            access_token, cloud_id, issue_key, done
        ):
            logger.info(
                "Jira sub-task %s could not be deleted (no Delete Issues "
                "permission) — closed as %r instead", issue_key, done,
            )
            return True
        logger.warning(
            "Jira sub-task %s could not be deleted or closed — it stays on the "
            "board; grant the connected account 'Delete Issues' on %s",
            issue_key, project_key,
        )
        return False
    except Exception as e:  # noqa: BLE001 — one child failing is isolated
        logger.warning("Jira sub-task delete failed for %s: %s", issue_key, e)
        return False


def push_stories_to_jira(
    company_id: str,
    project_key: str,
    stories: Iterable[Story],
    *,
    issue_type: str = "Task",
) -> dict[str, Any]:
    """Create (or idempotently update) one Jira issue per story in `project_key`.

    Returns `{"created": [{story, task_id, url, updated}], "errors": [...]}`.
    Error-isolated per story. Raises JiraNotConnectedError up-front if Jira
    isn't connected. Mirrors push_stories_to_clickup's create-or-update-by-map
    behavior so a re-push doesn't duplicate issues.

    Child issues: when the project has a sub-task type (tracker metadata),
    each story's subtasks become REAL Jira sub-tasks under the issue — and
    the description drops its Child issues text section (no duplication).
    Without a sub-task type the legacy description section stays.
    """
    access_token, cloud_id = _jira_creds(company_id)
    subtask_type = jira_subtask_type(company_id, project_key)

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for story in stories:
        try:
            ticket_id = story.stable_id()
            existing = get_jira_issue_key(company_id, project_key, ticket_id)
            assignee = getattr(story, "assignee_account_id", None) or None
            description = story.to_description(include_subtasks=subtask_type is None)
            if existing:
                issue = jira_oauth.update_issue(
                    access_token, cloud_id, existing,
                    summary=story.title,
                    description=description,
                    priority_name=story.jira_priority(),
                    assignee_account_id=assignee,
                )
                issue_key = issue.get("key") or existing
            else:
                issue = jira_oauth.create_issue(
                    access_token, cloud_id,
                    project_key=project_key,
                    summary=story.title,
                    description=description,
                    # A per-ticket issue-type edit (ticket_edits.issue_type,
                    # set from tracker metadata's real types) wins over the
                    # batch default.
                    issue_type=getattr(story, "jira_issue_type", None) or issue_type,
                    priority_name=story.jira_priority(),
                    assignee_account_id=assignee,
                )
                issue_key = issue.get("key")
                if issue_key:
                    save_jira_issue_key(company_id, project_key, ticket_id, issue_key)
            # No `and story.subtasks` guard: the reconcile has to run on an
            # EMPTY list too, or removing a ticket's last child issue is the
            # one removal that can never reach Jira.
            if issue_key and subtask_type:
                sync_jira_subtasks(
                    company_id, project_key, issue_key, ticket_id,
                    story.subtasks,
                    access_token=access_token, cloud_id=cloud_id,
                    subtask_type=subtask_type,
                )
            created.append({
                "story": story.title,
                "task_id": issue_key,
                "url": issue.get("url"),
                "updated": bool(existing),
            })
        except Exception as e:  # noqa: BLE001 — isolate per-story failures
            logger.warning("Jira push failed for story %r: %s", story.title, e)
            errors.append({"story": story.title, "error": str(e)})

    return {"created": created, "errors": errors}


def pull_jira_status(
    company_id: str, project_key: str, ticket_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Bidirectional read, Jira flavor of pull_clickup_status: for each ticket
    already synced to `project_key`, fetch its current Jira state (status,
    assignee, url). Keyed by the ticket's stable_id; tickets never pushed (no
    mapping row) are simply absent. Best-effort per ticket."""
    access_token, cloud_id = _jira_creds(company_id)
    # Resolve the browse base URL once for the whole batch.
    site_url = jira_oauth._site_url_for_cloud(access_token, cloud_id)
    out: dict[str, dict[str, Any]] = {}
    for ticket_id in ticket_ids:
        issue_key = get_jira_issue_key(company_id, project_key, ticket_id)
        if not issue_key:
            continue
        state = jira_oauth.get_issue(
            access_token, cloud_id, issue_key, site_url=site_url
        )
        if state.get("status") or state.get("assignee"):
            out[ticket_id] = state
    return out


# ── Asana push ─────────────────────────────────────────────────────────────
#
# Mirrors the ClickUp/Jira push but targets an Asana PROJECT (gid) and creates
# tasks. Asana access tokens expire ~1h (refresh + persist like Jira). Asana
# has no native priority / issue type, and a task's "status" is the SECTION it
# sits in — so status sync happens in the two-way pass (app/stories/sync.py),
# not on first create; a freshly-created task lands in the project's default
# (first) section.


def _asana_creds(company_id: str) -> str:
    """Return the company's Asana access token, refreshing + persisting an
    expired one first (Asana access tokens expire ~1h; refresh tokens are
    long-lived but a refresh response may omit the refresh token — carry the
    stored one forward). Raises AsanaNotConnectedError when unusable."""
    import time

    row = db.get_connection(company_id, ASANA_PROVIDER)
    if not row or not row.get("token_json_encrypted"):
        raise AsanaNotConnectedError("Asana is not connected for this company")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, ValueError) as e:
        raise AsanaNotConnectedError("Asana token is unreadable") from e

    obtained_at = token_json.get("obtained_at", 0)
    expires_in = token_json.get("expires_in", 3600)
    refresh_token = token_json.get("refresh_token")
    if refresh_token and time.time() > obtained_at + expires_in - 120:
        try:
            token_json = json.loads(
                asana_oauth.token_payload_to_store(
                    asana_oauth.refresh_access_token(refresh_token),
                    keep_refresh_token=refresh_token,
                )
            )
            db.update_connection_tokens(
                company_id, ASANA_PROVIDER, encrypt_token_json(json.dumps(token_json))
            )
        except Exception as e:  # noqa: BLE001 — refresh failed → surface reconnect
            raise AsanaNotConnectedError(
                "Asana authorization expired — reconnect Asana to continue"
            ) from e

    access_token = token_json.get("access_token") or ""
    if not access_token:
        raise AsanaNotConnectedError("Asana connection has no access_token")
    return access_token


def sync_asana_subtasks(
    company_id: str,
    project_gid: str,
    parent_gid: str,
    ticket_id: str,
    subtasks: Iterable[str],
    *,
    access_token: str,
) -> None:
    """Make the native Asana subtasks under `parent_gid` match the ticket's
    child issues: missing ones created, ones REMOVED in Sprntly deleted.
    Mirrors sync_jira_subtasks — see it for why removal matters and why only
    Sprntly-created subtasks (the `{ticket_id}#sub#…` rows in asana_task_map)
    are ever touched.

    Asana subtasks need no special type (unlike Jira), so this always runs when
    a story has child issues — they never live only as description text. A
    refused delete falls back to marking the subtask COMPLETE, Asana's
    equivalent of closing it.
    """
    labels = _child_labels(subtasks)
    wanted = {_sub_id(ticket_id, label): label for label in labels}

    for sub_id, label in wanted.items():
        try:
            if get_asana_task_gid(company_id, project_gid, sub_id):
                continue
            sub = asana_oauth.create_subtask(access_token, parent_gid, name=label[:255])
            if sub.get("gid"):
                save_asana_task_gid(company_id, project_gid, sub_id, sub["gid"])
        except Exception as e:  # noqa: BLE001 — one child failing is isolated
            logger.warning(
                "Asana sub-task push failed under %s (%r): %s", parent_gid, label, e
            )

    try:
        mapped = list_asana_subtask_gids(company_id, project_gid, ticket_id)
    except Exception:  # noqa: BLE001 — no map read, no prune; adds still landed
        logger.warning("Asana sub-task prune skipped under %s (map read failed)", parent_gid)
        return
    for sub_id, gid in mapped.items():
        if sub_id in wanted:
            continue
        if not _remove_asana_subtask(gid, access_token=access_token):
            continue  # keep the mapping so the next pass retries
        delete_asana_task_gid(company_id, project_gid, sub_id)


def _remove_asana_subtask(gid: str, *, access_token: str) -> bool:
    """Delete one Asana subtask, completing it instead when Asana refuses.
    True when the removal landed either way. Asana keeps deleted tasks in the
    author's trash for 30 days, so this is recoverable by the customer."""
    try:
        return asana_oauth.delete_task(access_token, gid)
    except TrackerDeleteForbiddenError:
        try:
            asana_oauth.update_task(access_token, gid, completed=True)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Asana subtask %s could not be deleted or completed: %s", gid, e
            )
            return False
        logger.info(
            "Asana subtask %s could not be deleted — marked complete instead", gid
        )
        return True
    except Exception as e:  # noqa: BLE001 — one child failing is isolated
        logger.warning("Asana subtask delete failed for %s: %s", gid, e)
        return False


def push_stories_to_asana(
    company_id: str,
    project_gid: str,
    stories: Iterable[Story],
) -> dict[str, Any]:
    """Create (or idempotently update) one Asana task per story in `project_gid`.

    Returns `{"created": [{story, task_id, url, updated}], "errors": [...]}`.
    Error-isolated per story. Raises AsanaNotConnectedError up-front if Asana
    isn't connected. Mirrors the ClickUp/Jira push's create-or-update-by-map
    behavior so a re-push doesn't duplicate tasks. Status (section) and
    comments are reconciled by the two-way sync pass, not here.

    Child issues become REAL native Asana subtasks under the task (add-only,
    idempotent by content) — so the description drops its Child issues text
    section, exactly like the Jira push does when a project has a sub-task
    type."""
    access_token = _asana_creds(company_id)

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for story in stories:
        try:
            ticket_id = story.stable_id()
            existing = get_asana_task_gid(company_id, project_gid, ticket_id)
            # Subtasks become real Asana subtasks below, so keep them OUT of the
            # notes body (no duplication between the subtask list and the text).
            notes = story.to_description(include_subtasks=False)
            if existing:
                task = asana_oauth.update_task(
                    access_token, existing, name=story.title, notes=notes,
                )
                task_gid = task.get("gid") or existing
            else:
                task = asana_oauth.create_task(
                    access_token, project_gid, name=story.title, notes=notes,
                )
                task_gid = task.get("gid")
                if task_gid:
                    save_asana_task_gid(company_id, project_gid, ticket_id, task_gid)
            # Runs on an empty list too — see the Jira push for why.
            if task_gid:
                sync_asana_subtasks(
                    company_id, project_gid, task_gid, ticket_id, story.subtasks,
                    access_token=access_token,
                )
            created.append({
                "story": story.title,
                "task_id": task_gid,
                "url": task.get("url"),
                "updated": bool(existing),
            })
        except Exception as e:  # noqa: BLE001 — isolate per-story failures
            logger.warning("Asana push failed for story %r: %s", story.title, e)
            errors.append({"story": story.title, "error": str(e)})

    return {"created": created, "errors": errors}

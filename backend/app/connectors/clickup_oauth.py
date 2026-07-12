"""ClickUp OAuth 2.0 helpers (commit H).

Flow:
    1. Frontend hits POST /v1/connectors/clickup/start-oauth (commit F)
    2. We build a state JWT + return ClickUp's authorize URL
    3. Browser navigates to ClickUp's consent screen
    4. ClickUp redirects back to /v1/connectors/clickup/callback?code=...&state=...
    5. We exchange the code for {access_token} and store an encrypted JSON
       blob under provider="clickup"

ClickUp specifics worth knowing:
    - Token exchange returns ONLY access_token (no refresh token, no expiry)
    - ClickUp access tokens don't expire under normal use; if they do, the
      user re-authorizes via Connect again
    - The user API requires the token in `Authorization: <token>` — RAW,
      no `Bearer ` prefix. Easy to get wrong.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import jwt
import requests
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

CLICKUP_PROVIDER = "clickup"
CLICKUP_AUTH_URL = "https://app.clickup.com/api"
CLICKUP_TOKEN_URL = "https://api.clickup.com/api/v2/oauth/token"
CLICKUP_USER_URL = "https://api.clickup.com/api/v2/user"
CLICKUP_API = "https://api.clickup.com/api/v2"
JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600
_WRITE_TIMEOUT = 20


def clickup_configured() -> bool:
    return bool(
        settings.clickup_client_id
        and settings.clickup_client_secret
        and settings.clickup_oauth_redirect_uri
    )


def authorize_url(state: str) -> str:
    """Build the URL the user gets redirected to for the ClickUp consent screen."""
    if not clickup_configured():
        raise HTTPException(500, "ClickUp OAuth is not configured on the server")
    from urllib.parse import urlencode

    params = {
        "client_id": settings.clickup_client_id,
        "redirect_uri": settings.clickup_oauth_redirect_uri,
        "state": state,
    }
    return f"{CLICKUP_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state(
    *, company_id: str, return_to: str | None = None,
) -> str:
    """Mint a signed state JWT that binds the OAuth round-trip to a
    specific company. The callback (which has no user session) trusts
    only this signature to know which company gets the new token.

    `return_to` is an optional relative path the callback redirects
    to instead of the default /settings?section=connectors."""
    now = int(time.time())
    payload = {
        "provider": CLICKUP_PROVIDER,
        "company_id": company_id,
        "return_to": return_to,
        "nonce": uuid.uuid4().hex,
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG)


def verify_oauth_state(state: str) -> dict:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[JWT_ALG])
    except jwt.PyJWTError as e:
        raise HTTPException(400, "Invalid or expired OAuth state") from e
    if payload.get("provider") != CLICKUP_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    return payload


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an authorization code for an access token. Returns the parsed JSON."""
    if not clickup_configured():
        raise HTTPException(500, "ClickUp OAuth is not configured on the server")
    resp = requests.post(
        CLICKUP_TOKEN_URL,
        json={
            "client_id": settings.clickup_client_id,
            "client_secret": settings.clickup_client_secret,
            "code": code,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning(
            "ClickUp token exchange failed: %s %s", resp.status_code, resp.text[:300]
        )
        raise HTTPException(400, "ClickUp token exchange failed")
    return resp.json()


def fetch_authenticated_user(access_token: str) -> dict[str, Any]:
    """Returns ClickUp's /user payload — {id, username, email, ...}.

    Important: ClickUp expects the access token RAW in the Authorization
    header, NOT prefixed with "Bearer ". Other APIs are different.
    """
    resp = requests.get(
        CLICKUP_USER_URL,
        headers={"Authorization": access_token},
        timeout=10,
    )
    if not resp.ok:
        logger.warning(
            "ClickUp /user failed: %s %s", resp.status_code, resp.text[:200]
        )
        return {}
    body = resp.json() or {}
    return body.get("user") or {}


def token_payload_to_store(token_json: dict[str, Any]) -> str:
    """Wrap ClickUp's token response with an obtained_at stamp before encryption."""
    payload = dict(token_json)
    payload["obtained_at"] = int(time.time())
    return json.dumps(payload)


# ── Write side (push generated user stories into ClickUp) ────────────────────
#
# ClickUp OAuth tokens are UNSCOPED — they already carry the authorizing
# user's full ClickUp permissions, so creating tasks needs no extra scope
# beyond what Connect already granted. Same auth quirk as the read puller:
# the token goes RAW in `Authorization`, never with a `Bearer ` prefix.


def list_lists(access_token: str) -> list[dict[str, Any]]:
    """Walk teams → spaces → (folderless) lists and return every list the
    token can see, as `{id, name, space, folder}` dicts.

    Used to let the caller pick a target list to push stories into. Mirrors
    the puller's team/space/list traversal. Folder-nested lists are included
    alongside the space's folderless lists. Best-effort: a failure on one
    space/folder is skipped so one bad node doesn't blank the whole picker.
    """
    out: list[dict[str, Any]] = []
    teams = _get(access_token, "/team").get("teams", [])
    for team in teams:
        team_id = team.get("id")
        if not team_id:
            continue
        try:
            spaces = _get(access_token, f"/team/{team_id}/space",
                          params={"archived": "false"}).get("spaces", [])
        except requests.RequestException:
            logger.warning("ClickUp: failed to list spaces for team %s", team_id)
            continue
        for space in spaces:
            space_id = space.get("id")
            space_name = space.get("name")
            if not space_id:
                continue
            # Folderless lists live directly under the space.
            try:
                folderless = _get(
                    access_token, f"/space/{space_id}/list",
                    params={"archived": "false"},
                ).get("lists", [])
            except requests.RequestException:
                folderless = []
            for lst in folderless:
                out.append({
                    "id": lst.get("id"), "name": lst.get("name"),
                    "space": space_name, "folder": None,
                })
            # Lists nested inside folders.
            try:
                folders = _get(
                    access_token, f"/space/{space_id}/folder",
                    params={"archived": "false"},
                ).get("folders", [])
            except requests.RequestException:
                folders = []
            for folder in folders:
                for lst in folder.get("lists", []):
                    out.append({
                        "id": lst.get("id"), "name": lst.get("name"),
                        "space": space_name, "folder": folder.get("name"),
                    })
    return [item for item in out if item.get("id")]


class ClickUpAuthExpiredError(RuntimeError):
    """The stored ClickUp token was rejected (401/403). ClickUp issues no
    refresh token, so the only remedy is the user re-authorizing via Connect.
    Raised so the caller can surface a "reconnect ClickUp" message instead of
    a generic upstream failure."""


def create_task(
    access_token: str,
    list_id: str,
    *,
    name: str,
    description: str | None = None,
    markdown_description: str | None = None,
    priority: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one task in a ClickUp list. Returns `{id, url}`.

    POST https://api.clickup.com/api/v2/list/{list_id}/task with the raw
    token in `Authorization` (no `Bearer `). `priority` is ClickUp's 1–4
    scale (1=urgent … 4=low); omitted when None.

    `markdown_description` uses ClickUp's `markdown_content` field so the body
    renders as rich text (headings, bullet acceptance criteria); when given it
    takes precedence over the plain-text `description`.

    Token handling: ClickUp tokens carry no expiry and ClickUp issues no
    refresh token, so there is nothing to silently refresh — we read the
    freshest stored token at push time (see app.stories.push). If ClickUp
    rejects it anyway (401/403), we raise ClickUpAuthExpiredError so the caller
    can tell the user to reconnect. Any other non-OK response raises
    HTTPException(502) so per-task failures stay isolated.
    """
    body: dict[str, Any] = {"name": name}
    if markdown_description is not None:
        body["markdown_content"] = markdown_description
    elif description is not None:
        body["description"] = description
    if priority is not None:
        body["priority"] = priority
    if extra:
        body.update(extra)
    resp = requests.post(
        f"{CLICKUP_API}/list/{list_id}/task",
        json=body,
        headers={"Authorization": access_token},
        timeout=_WRITE_TIMEOUT,
    )
    if resp.status_code in (401, 403):
        logger.warning(
            "ClickUp create_task auth rejected: %s %s",
            resp.status_code, resp.text[:200],
        )
        raise ClickUpAuthExpiredError(
            "ClickUp rejected the stored token — reconnect ClickUp to continue"
        )
    if not resp.ok:
        logger.warning(
            "ClickUp create_task failed: %s %s", resp.status_code, resp.text[:300]
        )
        raise HTTPException(502, "ClickUp task creation failed")
    data = resp.json() or {}
    return {"id": data.get("id"), "url": data.get("url")}


def update_task(
    access_token: str,
    task_id: str,
    *,
    name: str | None = None,
    markdown_description: str | None = None,
    priority: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update an existing ClickUp task (idempotent re-push). Returns `{id, url}`.

    PUT https://api.clickup.com/api/v2/task/{task_id}. Only the given fields are
    sent. `tags` cannot be set via the task PUT — ClickUp exposes tags through a
    separate add/remove-tag endpoint — so they are dropped from `extra` here
    (create still sets them); a full tag reconcile is part of the sync follow-up.
    Auth/error handling mirrors create_task (401/403 → reconnect; else 502)."""
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if markdown_description is not None:
        body["markdown_content"] = markdown_description
    if priority is not None:
        body["priority"] = priority
    if extra:
        body.update({k: v for k, v in extra.items() if k != "tags"})
    resp = requests.put(
        f"{CLICKUP_API}/task/{task_id}",
        json=body,
        headers={"Authorization": access_token},
        timeout=_WRITE_TIMEOUT,
    )
    if resp.status_code in (401, 403):
        logger.warning("ClickUp update_task auth rejected: %s", resp.status_code)
        raise ClickUpAuthExpiredError(
            "ClickUp rejected the stored token — reconnect ClickUp to continue"
        )
    if not resp.ok:
        logger.warning(
            "ClickUp update_task failed: %s %s", resp.status_code, resp.text[:300]
        )
        raise HTTPException(502, "ClickUp task update failed")
    data = resp.json() or {}
    return {"id": data.get("id") or task_id, "url": data.get("url")}


def _write(method: str, path: str, access_token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Shared writer for the ClickUp v2 API (raw-token auth). 401/403 →
    reconnect; any other non-OK → 502 so per-item failures stay isolated."""
    resp = requests.request(
        method, f"{CLICKUP_API}{path}", json=body or {},
        headers={"Authorization": access_token}, timeout=_WRITE_TIMEOUT,
    )
    if resp.status_code in (401, 403):
        raise ClickUpAuthExpiredError(
            "ClickUp rejected the stored token — reconnect ClickUp to continue"
        )
    if not resp.ok:
        logger.warning("ClickUp %s %s failed: %s %s", method, path, resp.status_code, resp.text[:200])
        raise HTTPException(502, "ClickUp write failed")
    return resp.json() or {}


def create_checklist(access_token: str, task_id: str, name: str) -> str | None:
    """Create a checklist on a task (used for a ticket's child issues). Returns
    the checklist id."""
    data = _write("POST", f"/task/{task_id}/checklist", access_token, {"name": name})
    return (data.get("checklist") or {}).get("id")


def create_checklist_item(access_token: str, checklist_id: str, name: str, resolved: bool = False) -> None:
    """Add one item to a checklist."""
    _write("POST", f"/checklist/{checklist_id}/checklist_item", access_token,
           {"name": name, "resolved": resolved})


def add_dependency(access_token: str, task_id: str, *, depends_on: str) -> None:
    """Record that `task_id` waits on `depends_on` (blocked-by). ClickUp models
    both directions from one call: depends_on = the task that must finish first."""
    _write("POST", f"/task/{task_id}/dependency", access_token, {"depends_on": depends_on})


# ── List metadata (tracker-native vocabulary) ────────────────────────────────
#
# Read side for the TrackerMeta cache (app/connectors/tracker_meta.py): a
# list's REAL statuses and custom fields, so the ticket UI can mirror the
# customer's workspace. Best-effort ({} / [] on failure) — metadata staleness
# must never break a push or sync pass.


def get_list(access_token: str, list_id: str) -> dict[str, Any]:
    """Fetch a list's raw payload — the interesting part is `statuses`:
    `[{id, status, type, color, orderindex}]` (statuses are LIST-SPECIFIC
    custom names in ClickUp). Returns {} on any failure."""
    try:
        return _get(access_token, f"/list/{list_id}")
    except Exception:  # noqa: BLE001 — metadata reads are best-effort
        logger.warning("ClickUp get_list failed for %s", list_id)
        return {}


def get_list_custom_fields(access_token: str, list_id: str) -> list[dict[str, Any]]:
    """The custom fields accessible on a list's tasks, raw from
    `GET /list/{id}/field`: `[{id, name, type, type_config, ...}]` (option
    values for drop_down/labels live in type_config.options). Returns [] on
    any failure."""
    try:
        return _get(access_token, f"/list/{list_id}/field").get("fields") or []
    except Exception:  # noqa: BLE001 — metadata reads are best-effort
        logger.warning("ClickUp get_list_custom_fields failed for %s", list_id)
        return []


def set_custom_field(
    access_token: str, task_id: str, field_id: str, value: Any
) -> None:
    """Write one custom-field value on a task
    (`POST /task/{task_id}/field/{field_id}`, body `{"value": ...}`). The
    value must already be provider-encoded (tracker_meta.encode_field_value):
    option id for drop_down, ms-epoch for date, `{"add": [ids]}` for users."""
    _write("POST", f"/task/{task_id}/field/{field_id}", access_token,
           {"value": value})


def get_task(access_token: str, task_id: str) -> dict[str, Any]:
    """Fetch a task's current state from ClickUp and normalize the fields the
    two-way sync reconciles: workflow state (status, first assignee's display
    name, url) plus the CONTENT side (title, markdown description) and
    ClickUp's last-update time (`updated_at`, ISO) so the sync can decide
    which side of an edit is newer. Returns {} on a 4xx/5xx so a single
    stale/deleted task never breaks the whole pull."""
    try:
        data = _get(
            access_token, f"/task/{task_id}",
            params={"include_markdown_description": "true"},
        )
    except Exception:  # noqa: BLE001 — a per-task fetch failure is non-fatal
        logger.warning("ClickUp get_task failed for %s", task_id)
        return {}
    assignees = data.get("assignees") or []
    assignee = None
    if assignees:
        a = assignees[0] or {}
        assignee = a.get("username") or a.get("email")
    # date_updated is a ms-epoch string.
    updated_at = None
    try:
        ms = int(data.get("date_updated") or 0)
        if ms:
            updated_at = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    return {
        "status": (data.get("status") or {}).get("status"),
        "assignee": assignee,
        "url": data.get("url"),
        "title": data.get("name"),
        "description": data.get("markdown_description") or data.get("description") or "",
        # Priority name ("urgent"/"high"/...) — ClickUp's fixed 1–4 scale.
        "priority": (data.get("priority") or {}).get("priority"),
        # Raw custom-field values ([{id, type, value, type_config}, ...]);
        # decoding to normalized shapes is tracker_meta's job.
        "custom_fields": data.get("custom_fields") or [],
        # Built-in task properties (tracker_meta's `builtin:` fields).
        "start_date": data.get("start_date"),
        "due_date": data.get("due_date"),
        "points": data.get("points"),
        "tags": [
            t.get("name") for t in data.get("tags") or [] if t.get("name")
        ],
        "updated_at": updated_at,
    }


def add_task_tag(access_token: str, task_id: str, tag_name: str) -> None:
    """Attach one workspace tag to a task (`POST /task/{id}/tag/{name}` —
    creates the tag when new). ClickUp models tag REMOVAL as a separate
    endpoint per tag; Sprntly-side tag edits are add-only by design."""
    from urllib.parse import quote

    _write("POST", f"/task/{task_id}/tag/{quote(tag_name, safe='')}", access_token)


def add_task_comment(access_token: str, task_id: str, text: str) -> str | None:
    """Post one comment on a task (`POST /task/{id}/comment` — the
    Sprntly→ClickUp half of comment push). Returns the created comment's id,
    or None on any failure — comment push is best-effort; the sync pass
    retries unpushed comments."""
    try:
        data = _write(
            "POST", f"/task/{task_id}/comment", access_token,
            {"comment_text": text, "notify_all": False},
        )
        cid = data.get("id")
        return str(cid) if cid is not None else None
    except Exception:  # noqa: BLE001 — comment push is best-effort by design
        logger.warning("ClickUp add_task_comment failed for %s", task_id)
        return None


def set_task_status(access_token: str, task_id: str, status: str) -> None:
    """Set a task's workflow status (the Sprntly→tracker half of two-way
    status sync). ClickUp statuses are LIST-SPECIFIC custom names, so this is
    inherently best-effort — an unknown name 400s; callers treat failure as
    non-fatal."""
    _write("PUT", f"/task/{task_id}", access_token, {"status": status})


def _get(token: str, path: str, params: dict | None = None) -> dict[str, Any]:
    """Authenticated GET against the ClickUp v2 API (raw-token auth quirk)."""
    r = requests.get(
        f"{CLICKUP_API}{path}", params=params or {},
        headers={"Authorization": token}, timeout=_WRITE_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()

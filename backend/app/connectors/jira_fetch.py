"""Live Jira reads for the Q&A agent — on-demand ticket / epic / search fetch.

The counterpart to app/call_digest.py (Fireflies), but shaped differently. A
call digest is a single deterministic pre-fetch feeding a fixed skill; a Jira
lookup is genuinely AGENTIC — the model has to decide WHICH issue or epic to
read, or SEARCH for the right one, from the question. So this module exposes
Jira as read-only TOOLS the agent calls in a loop (see app/jira_lookup.py),
rather than pre-fetching one thing.

Every read reuses the stored OAuth connection (app/connectors/jira_oauth.py) —
the same access token, cloud_id resolution, and rotating-refresh plumbing the
KG puller and the two-way sync already use. Nothing new to authorize.

Reads are free: search, get one issue (with comments + epic children), and
editmeta (which fields this user may change on this issue).

Writes are NOT free, and the read-only rule that used to sit here has one narrow
exception. Field updates, transitions and comments are now reachable from chat —
but only through `routes/jira_write.py`, after the user has confirmed the exact
change in the UI. The agent's tools can propose a change and nothing more: no
code path lets a model turn a sentence into a Jira mutation on its own. The HTTP
writes themselves still live in jira_oauth (the push side's helpers), so token
refresh and auth-expiry handling stay in one place.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from app.connectors.jira_oauth import (
    JIRA_API_BASE,
    get_accessible_resources,
    refresh_access_token,
    token_payload_to_store,
)
from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
    encrypt_token_json,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 30
# Refresh a token this many seconds before its nominal expiry (mirrors
# auto_sync._TOKEN_REFRESH_SKEW_S) so a lookup never races a just-expired token.
_TOKEN_REFRESH_SKEW_S = 300
# Bounds — pilot-scale, and small enough that a tool result stays inside the
# agent's context budget.
_SEARCH_LIMIT = 20
_COMMENT_LIMIT = 20
_CHILD_LIMIT = 50
_DESC_CHARS = 4000
_COMMENT_CHARS = 800
# A single issue is fetched with EVERY field Jira will give us (`*all`) plus
# `expand=names`, which maps each field id to its human label — the only way to
# render `customfield_10031` as "Story Points".
#
# It used to be a fixed 14-field list, which silently dropped everything the list
# did not anticipate: start date, story points, sprint, components, fix versions,
# time tracking, resolution, and every custom field a customer had defined. A
# hand-maintained list cannot cover per-project custom fields by construction, so
# the list is gone. The prompt is kept sane at RENDER time instead (see
# `_extra_fields`): known fields render in their curated positions, and anything
# else appears only when it has a value.
_ISSUE_FIELDS = "*all"
# Field ids rendered explicitly by `render_issue`, so the catch-all does not
# print them a second time. `comment` is excluded because comments are fetched
# separately (paged, newest-first) by `_get_comments`.
_CORE_FIELD_IDS = frozenset({
    "summary", "description", "status", "priority", "issuetype", "project",
    "labels", "assignee", "reporter", "parent", "subtasks", "updated",
    "created", "duedate", "comment",
})
# Caps for the catch-all block: one field's rendered value, and how many extra
# fields render at all. `*all` on a mature Jira site can return 100+ fields, most
# of them empty or internal; without these a single issue could crowd out the
# rest of the agent's context.
_FIELD_VALUE_CHARS = 300
_EXTRA_FIELDS_MAX = 40
# Legal options listed per editable field. Enough for a status/priority/component
# scheme; a 500-option customer picklist is truncated with a marker rather than
# flooding the prompt (the agent can still write a value it was not shown —
# Jira rejects an illegal one).
_ALLOWED_VALUES_MAX = 25
# Leaner field set for search hits — a one-line-per-result list, no bodies.
_SEARCH_FIELDS = "summary,status,issuetype,priority,assignee,updated"


# ── Session (resolved token + site) ──────────────────────────────────────────


@dataclass
class JiraSession:
    """A tenant's live Jira access for the duration of one lookup: a valid
    access token, the target site's cloud_id (needed on every REST call), and
    the site's browse base URL (for building human issue links)."""

    access_token: str
    cloud_id: str
    site_url: str | None = None

    @property
    def base(self) -> str:
        return f"{JIRA_API_BASE}/{self.cloud_id}/rest/api/3"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }


def _token_is_fresh(token_json: dict) -> bool:
    """True iff the access token is provably still valid past a safety skew.
    Mirrors auto_sync._token_is_fresh; when freshness can't be proven we refresh
    rather than risk a 401 mid-lookup."""
    obtained = token_json.get("obtained_at")
    expires_in = token_json.get("expires_in")
    if not isinstance(obtained, (int, float)) or not isinstance(expires_in, (int, float)):
        return False
    return time.time() < obtained + expires_in - _TOKEN_REFRESH_SKEW_S


def _refresh_if_stale(company_id: str, token_json: dict) -> dict:
    """Refresh an expiring Jira access token, persist the rotated payload, and
    return the updated token_json. Jira access tokens live ~1h and its refresh
    tokens ROTATE, so we persist the whole new payload every time (mirrors
    auto_sync._maybe_refresh_token, kept local so the light lookup path doesn't
    import the sync runner). Best-effort: on any refresh failure we return the
    input unchanged and let the ensuing 401 surface as "reconnect Jira"."""
    refresh_token = token_json.get("refresh_token")
    if not refresh_token or _token_is_fresh(token_json):
        return token_json
    try:
        from app import db

        new_json_str = token_payload_to_store(refresh_access_token(refresh_token))
        db.update_connection_tokens(
            company_id, "jira", encrypt_token_json(new_json_str)
        )
        logger.info("jira-lookup: refreshed access token for %s", company_id)
        return json.loads(new_json_str)
    except Exception:  # noqa: BLE001 — refresh is best-effort
        logger.warning(
            "jira-lookup: token refresh failed for %s — surfacing reconnect",
            company_id, exc_info=True,
        )
        return token_json


def open_session(company_id: str) -> JiraSession | None:
    """Resolve a live JiraSession for a company, or None when Jira isn't
    connected / the credential can't be read / the token sees no site. Never
    raises — the caller degrades to a "connect Jira" message."""
    from app import db

    row = db.get_connection(company_id, "jira")
    if not row:
        return None
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, ValueError, KeyError, TypeError):
        logger.warning("jira-lookup: could not decrypt jira token for %s", company_id)
        return None
    token_json = _refresh_if_stale(company_id, token_json)
    access_token = token_json.get("access_token")
    if not access_token:
        return None
    sites = get_accessible_resources(access_token)
    if not sites:
        logger.warning("jira-lookup: token for %s can see no Jira site", company_id)
        return None
    site = sites[0]
    cloud_id = site.get("id")
    if not cloud_id:
        return None
    return JiraSession(
        access_token=access_token, cloud_id=cloud_id, site_url=site.get("url")
    )


# ── ADF + JQL helpers ────────────────────────────────────────────────────────


def _adf_text(node: object) -> str:
    """Flatten an Atlassian Document Format node (or None) to plain text by
    collecting every `text` leaf. Good enough for the agent to read."""
    if not isinstance(node, dict):
        return ""
    out: list[str] = []
    if node.get("type") == "text" and isinstance(node.get("text"), str):
        out.append(node["text"])
    for child in node.get("content", []) or []:
        out.append(_adf_text(child))
    return " ".join(p for p in out if p)


def _jql_str(value: str) -> str:
    r"""Escape a user/model-supplied value for a double-quoted JQL string:
    backslash first, then the quote, so `"` → `\"` and `\` → `\\`. Bounds the
    injection surface of the model-proposed search terms."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_search_jql(
    *, text: str | None, project: str | None, status: str | None
) -> str:
    """Assemble a bounded JQL query from safe, escaped clauses. The enhanced
    /search/jql endpoint REJECTS unbounded JQL (a bare `ORDER BY` 400s), so when
    no filter is given we anchor with a wide `created` floor — same trick the
    puller uses — to bound the query while still matching everything."""
    clauses: list[str] = []
    if text and text.strip():
        clauses.append(f'text ~ "{_jql_str(text.strip())}"')
    if project and project.strip():
        clauses.append(f'project = "{_jql_str(project.strip())}"')
    if status and status.strip():
        clauses.append(f'status = "{_jql_str(status.strip())}"')
    if not clauses:
        clauses.append('created >= "2000-01-01"')
    return " AND ".join(clauses) + " ORDER BY updated DESC"


# ── Fetch ────────────────────────────────────────────────────────────────────


def search(
    session: JiraSession,
    *,
    text: str | None = None,
    project: str | None = None,
    status: str | None = None,
    limit: int = _SEARCH_LIMIT,
) -> list[dict[str, Any]]:
    """Search issues via the enhanced /search/jql endpoint. Returns lean hit
    dicts (key/summary/type/status/priority/assignee/updated/url), newest-updated
    first. Raises on a hard API failure so the tool dispatch can surface it to
    the model."""
    jql = _build_search_jql(text=text, project=project, status=status)
    return _search_jql(session, jql, limit=limit)


def _get_comments(session: JiraSession, issue_key: str) -> list[dict[str, str]]:
    """Fetch the most recent comments on an issue as {author, text} dicts.
    Best-effort: returns [] on any failure — a comment-fetch hiccup must not sink
    the whole issue read."""
    try:
        r = requests.get(
            f"{session.base}/issue/{issue_key}/comment",
            params={"maxResults": _COMMENT_LIMIT, "orderBy": "-created"},
            headers=session.headers,
            timeout=_TIMEOUT,
        )
        if not r.ok:
            return []
        comments = (r.json() or {}).get("comments", []) or []
    except Exception:  # noqa: BLE001 — comments are best-effort
        logger.warning("jira-lookup: comment fetch failed for %s", issue_key)
        return []
    out: list[dict[str, str]] = []
    for c in comments[:_COMMENT_LIMIT]:
        body = _adf_text(c.get("body")).strip()
        if not body:
            continue
        out.append({
            "author": ((c.get("author") or {}) or {}).get("displayName") or "?",
            "text": body[:_COMMENT_CHARS],
        })
    return out


def _get_epic_children(session: JiraSession, epic_key: str) -> list[dict[str, Any]]:
    """List the issues parented to an epic (`parent = KEY`). Best-effort — team-
    managed and modern company-managed projects both parent children this way;
    if a project uses the legacy Epic Link only, this returns [] and the model
    still gets the epic itself. Returns lean hit dicts like search()."""
    try:
        return _search_jql(
            session, f'parent = "{_jql_str(epic_key)}" ORDER BY created ASC',
            limit=_CHILD_LIMIT,
        )
    except Exception:  # noqa: BLE001 — children are best-effort context
        logger.warning("jira-lookup: epic-children fetch failed for %s", epic_key)
        return []


def _search_jql(session: JiraSession, jql: str, *, limit: int) -> list[dict[str, Any]]:
    """Run a raw (already-bounded) JQL string and return lean hit dicts. Internal
    — external callers use search(), which builds a safe JQL from typed args."""
    r = requests.get(
        f"{session.base}/search/jql",
        params={"jql": jql, "maxResults": limit, "fields": _SEARCH_FIELDS},
        headers=session.headers,
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    issues = (r.json() or {}).get("issues", []) or []
    out: list[dict[str, Any]] = []
    for it in issues:
        f = it.get("fields") or {}
        key = it.get("key") or it.get("id") or ""
        out.append({
            "key": key,
            "summary": f.get("summary") or "",
            "type": ((f.get("issuetype") or {}) or {}).get("name"),
            "status": ((f.get("status") or {}) or {}).get("name"),
            "priority": ((f.get("priority") or {}) or {}).get("name"),
            "assignee": ((f.get("assignee") or {}) or {}).get("displayName"),
            "updated": f.get("updated"),
            "url": f"{session.site_url}/browse/{key}" if session.site_url else None,
        })
    return out


def _flatten_field_value(value: Any) -> str:
    """Render an arbitrary Jira field value as short plain text.

    Jira field values are wildly polymorphic — scalars, `{name}` / `{value}` /
    `{displayName}` objects, arrays of those, ADF documents, and nested option
    trees. The agent only needs to READ them, so anything unrecognised degrades
    to compact JSON rather than being dropped: an unfamiliar custom field is
    still worth showing, and this is the path every customer-defined field takes.
    """
    if value is None or value == [] or value == {}:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [p for p in (_flatten_field_value(v) for v in value) if p]
        return ", ".join(parts)
    if isinstance(value, dict):
        # ADF rich text (description-like custom fields) — reuse the text walker.
        if value.get("type") == "doc":
            return _adf_text(value).strip()
        # The common display keys, in the order Jira tends to mean them.
        for k in ("displayName", "name", "value", "text"):
            v = value.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        # Time-tracking and similar composites have no single display key.
        try:
            return json.dumps(value, separators=(",", ":"))
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _extra_fields(
    fields: dict[str, Any], names: dict[str, Any]
) -> list[dict[str, str]]:
    """Every populated field NOT already rendered explicitly, newest Jira ids
    resolved to human labels via the `names` map from `expand=names`.

    Empty values are skipped: `*all` returns the full field catalogue, and on a
    real site most of it is null for any given issue. Sorted by label so the
    block is stable between reads of the same issue.
    """
    out: list[dict[str, str]] = []
    for fid, raw in (fields or {}).items():
        if fid in _CORE_FIELD_IDS:
            continue
        text = _flatten_field_value(raw)
        if not text:
            continue
        label = names.get(fid) if isinstance(names, dict) else None
        out.append({
            "id": fid,
            "name": str(label) if label else fid,
            "value": text[:_FIELD_VALUE_CHARS],
        })
    out.sort(key=lambda d: d["name"].lower())
    return out[:_EXTRA_FIELDS_MAX]


def get_issue(session: JiraSession, issue_key: str) -> dict[str, Any] | None:
    """Fetch one issue in full: fields + comments + (for an epic) its children.
    Returns None when the issue doesn't exist / isn't visible (404/410) so the
    caller can tell the model "no such issue" rather than inventing one. Raises
    on a transient/auth failure so the dispatch surfaces it."""
    r = requests.get(
        f"{session.base}/issue/{issue_key}",
        # `expand=names` rides along so custom field ids can be rendered under
        # their human labels rather than as `customfield_10031`.
        params={"fields": _ISSUE_FIELDS, "expand": "names"},
        headers=session.headers,
        timeout=_TIMEOUT,
    )
    if r.status_code in (404, 410):
        return None
    r.raise_for_status()
    body = r.json() or {}
    f = body.get("fields") or {}
    names = body.get("names") or {}
    issue_type = ((f.get("issuetype") or {}) or {}).get("name")
    parent = f.get("parent") or {}
    key = issue_key
    out: dict[str, Any] = {
        "key": key,
        "summary": f.get("summary") or "",
        "type": issue_type,
        "status": ((f.get("status") or {}) or {}).get("name"),
        "priority": ((f.get("priority") or {}) or {}).get("name"),
        "project": ((f.get("project") or {}) or {}).get("name"),
        "assignee": ((f.get("assignee") or {}) or {}).get("displayName"),
        "reporter": ((f.get("reporter") or {}) or {}).get("displayName"),
        "labels": f.get("labels") or [],
        "due_date": f.get("duedate"),
        "updated": f.get("updated"),
        "created": f.get("created"),
        "description": _adf_text(f.get("description"))[:_DESC_CHARS],
        "url": f"{session.site_url}/browse/{key}" if session.site_url else None,
        "parent": {
            "key": parent.get("key"),
            "summary": ((parent.get("fields") or {}) or {}).get("summary"),
        } if parent.get("key") else None,
        "subtasks": [
            {
                "key": st.get("key"),
                "summary": ((st.get("fields") or {}) or {}).get("summary"),
                "status": (((st.get("fields") or {}).get("status") or {}) or {}).get("name"),
            }
            for st in (f.get("subtasks") or [])
        ],
        "comments": _get_comments(session, issue_key),
        # Everything else Jira returned that has a value — start date, story
        # points, sprint, components, fix versions, time tracking, resolution,
        # and any custom field this customer has defined.
        "other_fields": _extra_fields(f, names),
    }
    if (issue_type or "").lower() == "epic":
        out["children"] = _get_epic_children(session, issue_key)
    return out


def get_editmeta(session: JiraSession, issue_key: str) -> dict[str, Any] | None:
    """Which fields can be EDITED on this issue, with their types and legal values.

    `GET /issue/{key}/editmeta` is answered per issue by Jira itself, so it
    already accounts for the project's screen configuration, the issue type, the
    workflow state, and the permissions of the connected user. That is why the
    writable set is discovered rather than hardcoded: a maintained list cannot
    know a customer's custom fields, and would happily offer a field the user is
    not allowed to set — a write that fails only once it reaches Jira.

    `allowedValues` matters as much as the field list. For select-style fields it
    is the closed set of legal options, which is what stops a status, priority or
    resolution being invented that this site's scheme has never had.

    Returns None on 404/410 (no such issue, or not visible). Raises on transient
    or auth failures so the dispatcher surfaces them.

    NOTE: status is absent by design — Jira does not edit status as a field. It
    moves through workflow transitions (`/transitions`), which is a separate call.
    """
    r = requests.get(
        f"{session.base}/issue/{issue_key}/editmeta",
        headers=session.headers,
        timeout=_TIMEOUT,
    )
    if r.status_code in (404, 410):
        return None
    r.raise_for_status()
    raw = ((r.json() or {}).get("fields") or {})
    fields: list[dict[str, Any]] = []
    for fid, meta in raw.items():
        if not isinstance(meta, dict):
            continue
        schema = meta.get("schema") or {}
        allowed = [
            a for a in (
                _flatten_field_value(v) for v in (meta.get("allowedValues") or [])
            ) if a
        ]
        fields.append({
            "id": fid,
            "name": meta.get("name") or fid,
            "type": schema.get("type") or "",
            # `items` distinguishes a multi-select from a single one, which
            # decides whether a write sends a value or a list of them.
            "items": schema.get("items") or "",
            "required": bool(meta.get("required")),
            "operations": meta.get("operations") or [],
            "allowed_values": allowed[:_ALLOWED_VALUES_MAX],
            "allowed_truncated": len(allowed) > _ALLOWED_VALUES_MAX,
        })
    fields.sort(key=lambda d: str(d["name"]).lower())
    return {"key": issue_key, "fields": fields}


# ── Writes ───────────────────────────────────────────────────────────────────
# The module docstring's "READ-ONLY by design" held until the chat needed to
# change tickets, not just read them. What has NOT changed is where the HTTP
# writes live: these delegate to app/connectors/jira_oauth.py, the same helpers
# the ticket-push side has used in production, so token refresh, auth-expiry
# mapping and error handling stay in one place.
#
# Nothing here decides to write on its own. Each function executes a change the
# user has already confirmed — see routes/jira_write.py, which is the only
# caller. The agent's tools can only PROPOSE.


def encode_for_editmeta(field: dict[str, Any], value: Any) -> Any:
    """Shape a plain value into Jira's write encoding, using the field's own
    editmeta schema rather than a guess about its name.

    This is what lets the agent pass through what the user said ("2028-08-31",
    "High", "Ada") without knowing that a priority writes as `{"name": …}`, a
    user as `{"accountId": …}` and labels as a bare list. Getting this wrong is
    not a soft failure: Jira rejects a mis-encoded field with a 400 naming only
    the field id.
    """
    schema_type = (field.get("type") or "").lower()
    items = (field.get("items") or "").lower()
    if value is None:
        return None
    if schema_type == "array":
        vals = value if isinstance(value, list) else [value]
        if items in ("string", ""):
            return [str(v) for v in vals]
        # component/version/option arrays take objects, matched by name.
        return [v if isinstance(v, dict) else {"name": str(v)} for v in vals]
    if schema_type in ("date", "datetime", "string"):
        return str(value)
    if schema_type == "number":
        if isinstance(value, (int, float)):
            return value
        return float(value)
    if schema_type == "user":
        return value if isinstance(value, dict) else {"accountId": str(value)}
    if schema_type in ("priority", "resolution", "option", "issuetype", "securitylevel", "version", "component"):
        return value if isinstance(value, dict) else {"name": str(value)}
    return value


def apply_field_updates(
    session: JiraSession, issue_key: str, changes: dict[str, Any]
) -> dict[str, Any]:
    """Write field changes, validated against this issue's editmeta first.

    Validation is not belt-and-braces: editmeta is per issue and per permission,
    so a field that is not in it either does not exist here or this user may not
    set it. Writing it anyway produces a 400 that reads like a bug in Sprntly.
    Rejected ids come back to the caller so the chat can say WHICH field failed
    and why, rather than a blanket failure.
    """
    from app.connectors import jira_oauth

    meta = get_editmeta(session, issue_key)
    if meta is None:
        return {"ok": False, "error": f"No Jira issue {issue_key}."}
    editable = {f["id"]: f for f in meta["fields"]}
    encoded: dict[str, Any] = {}
    rejected: list[str] = []
    for fid, raw in (changes or {}).items():
        field = editable.get(fid)
        if field is None:
            rejected.append(fid)
            continue
        try:
            encoded[fid] = encode_for_editmeta(field, raw)
        except (TypeError, ValueError):
            rejected.append(fid)
    if not encoded:
        return {"ok": False, "error": "No writable fields in the request.",
                "rejected": rejected}
    jira_oauth.update_issue(
        session.access_token, session.cloud_id, issue_key, extra_fields=encoded
    )
    return {"ok": True, "updated": sorted(encoded), "rejected": rejected}


def preview_change(
    session: JiraSession,
    issue_key: str,
    *,
    fields: dict[str, Any] | None = None,
    to_status: str = "",
    comment: str = "",
) -> dict[str, Any]:
    """Validate a proposed change and describe it — WITHOUT writing anything.

    This is what the user is shown before they confirm, so it has to be checked
    against Jira now rather than at write time: a field that is not editable, or
    a status that is not reachable from here, must surface as a problem the user
    can see, not as a confident preview followed by a silent failure.

    Returns `{ok, text, change}` where `change` is the exact payload the confirm
    endpoint will execute — the preview and the write describe the same thing by
    construction.
    """
    issue = get_issue(session, issue_key)
    if issue is None:
        return {"ok": False, "text": f"(no Jira issue found with key {issue_key})",
                "change": {}}
    meta = get_editmeta(session, issue_key)
    editable = {f["id"]: f for f in ((meta or {}).get("fields") or [])}
    current = {f["id"]: f["value"] for f in (issue.get("other_fields") or [])}
    # Curated fields the catch-all does not carry, so a before-value can still
    # be shown for the ones users change most.
    current.setdefault("summary", issue.get("summary") or "")
    current.setdefault("duedate", issue.get("due_date") or "")
    current.setdefault("priority", issue.get("priority") or "")
    current.setdefault("labels", ", ".join(issue.get("labels") or []))
    current.setdefault("assignee", issue.get("assignee") or "")

    lines = [f"Proposed change to {issue_key} — {issue.get('summary') or ''}"]
    problems: list[str] = []
    ok_fields: dict[str, Any] = {}
    for fid, val in (fields or {}).items():
        field = editable.get(fid)
        if field is None:
            problems.append(
                f"'{fid}' is not editable on this issue (check the id with "
                f"jira_editmeta, or the user may lack permission)"
            )
            continue
        allowed = field.get("allowed_values") or []
        if allowed and not field.get("allowed_truncated"):
            as_text = _flatten_field_value(val)
            if as_text and as_text.lower() not in {a.lower() for a in allowed}:
                problems.append(
                    f"'{as_text}' is not a legal value for {field['name']} "
                    f"(allowed: {', '.join(allowed)})"
                )
                continue
        ok_fields[fid] = val
        before = current.get(fid, "")
        lines.append(f"  {field['name']}: {before or '—'} → {_flatten_field_value(val)}")

    ok_status = ""
    if to_status:
        available = list_transitions(session, issue_key)
        names = {(t.get("to_status_name") or "").lower(): t.get("to_status_name")
                 for t in available}
        if to_status.lower() in names:
            ok_status = names[to_status.lower()]
            lines.append(f"  Status: {issue.get('status') or '—'} → {ok_status}")
        else:
            opts = ", ".join(sorted(v for v in names.values() if v))
            problems.append(
                f"'{to_status}' is not reachable from '{issue.get('status')}'"
                + (f" (available: {opts})" if opts else "")
            )

    if comment:
        lines.append(f"  Comment: {comment[:200]}")

    if problems:
        lines.append("\nProblems:")
        lines.extend(f"  - {p}" for p in problems)
    if not ok_fields and not ok_status and not comment:
        return {"ok": False, "text": "\n".join(lines), "change": {}}

    lines.append("\nNOT applied yet — awaiting the user's confirmation.")
    return {
        "ok": True,
        "text": "\n".join(lines),
        "change": {
            "issue_key": issue_key,
            "summary": issue.get("summary") or "",
            "fields": ok_fields,
            "to_status": ok_status,
            "comment": comment,
            # Rendered lines minus the header, for the confirmation card.
            "preview": [ln.strip() for ln in lines[1:] if ln.strip().startswith(("Status:", "Comment:")) or "→" in ln],
        },
    }


def list_transitions(session: JiraSession, issue_key: str) -> list[dict[str, Any]]:
    """Workflow transitions available FROM this issue's current status."""
    from app.connectors import jira_oauth

    return jira_oauth.list_transitions(
        session.access_token, session.cloud_id, issue_key
    )


def apply_transition(
    session: JiraSession, issue_key: str, to_status: str
) -> dict[str, Any]:
    """Move an issue to a target status by NAME.

    Status is not a field write — Jira only moves an issue along a transition
    that is legal from where it currently sits. A status that exists on the
    board but is not reachable from here has to fail loudly with the legal
    options, or the user is told the change happened when nothing moved.
    """
    from app.connectors import jira_oauth

    wanted = (to_status or "").strip().lower()
    available = list_transitions(session, issue_key)
    if not available:
        return {"ok": False, "error": f"No transitions available on {issue_key}."}
    match = next(
        (t for t in available
         if (t.get("to_status_name") or "").lower() == wanted
         or (t.get("name") or "").lower() == wanted),
        None,
    )
    if match is None:
        names = ", ".join(sorted({t.get("to_status_name") or "" for t in available if t.get("to_status_name")}))
        return {"ok": False,
                "error": f"'{to_status}' is not reachable from the current status. Available: {names}."}
    ok = jira_oauth.transition_issue_by_id(
        session.access_token, session.cloud_id, issue_key, str(match["id"])
    )
    if not ok:
        return {"ok": False, "error": f"Jira rejected the move to '{match.get('to_status_name')}'."}
    return {"ok": True, "status": match.get("to_status_name")}


def add_comment(session: JiraSession, issue_key: str, text: str) -> dict[str, Any]:
    """Post a comment. Returns ok=False rather than raising — the caller reports
    it in chat."""
    from app.connectors import jira_oauth

    body = (text or "").strip()
    if not body:
        return {"ok": False, "error": "Comment text is empty."}
    cid = jira_oauth.add_issue_comment(
        session.access_token, session.cloud_id, issue_key, body
    )
    if not cid:
        return {"ok": False, "error": "Jira rejected the comment."}
    return {"ok": True, "comment_id": cid}


# ── Rendering (dict → text for a tool result) ────────────────────────────────


def render_hit(h: dict[str, Any]) -> str:
    """One search hit as a single line."""
    bits = [f"{h['key']}: {h.get('summary') or ''}"]
    tags = [t for t in (h.get("type"), h.get("status"), h.get("priority")) if t]
    if tags:
        bits.append(f"[{' · '.join(tags)}]")
    if h.get("assignee"):
        bits.append(f"@{h['assignee']}")
    # The browse URL was computed by `search` and then dropped here, so the model
    # was told to "cite issue keys and their browse links when present" while
    # never being shown one for a search result. Only the single-issue path had it.
    if h.get("url"):
        bits.append(h["url"])
    return " ".join(bits)


def render_search(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "No matching Jira issues."
    return "\n".join(f"- {render_hit(h)}" for h in hits)


def render_editmeta(meta: dict[str, Any]) -> str:
    """Editable-field metadata → text for the model.

    Each line carries the field id (what a write must address), its human name
    (what the user says), its type (how to shape the value — e.g. a `date` takes
    `YYYY-MM-DD`, which is how "august 31 2028" becomes a legal write without a
    date parser on our side), and the legal values where the field is a closed set.
    """
    fields = meta.get("fields") or []
    if not fields:
        return f"{meta.get('key')}: no editable fields (check permissions)."
    lines = [f"Editable fields on {meta.get('key')}:"]
    for f in fields:
        bits = [f"  - {f['name']} (id: {f['id']})"]
        kind = f.get("type") or ""
        if f.get("items"):
            kind = f"array of {f['items']}" if kind == "array" else f"{kind}[{f['items']}]"
        if kind:
            bits.append(f"type: {kind}")
        if f.get("required"):
            bits.append("REQUIRED")
        if f.get("allowed_values"):
            shown = ", ".join(f["allowed_values"])
            if f.get("allowed_truncated"):
                shown += ", …"
            bits.append(f"allowed: {shown}")
        lines.append(" · ".join(bits))
    lines.append(
        "Status is NOT in this list: it changes via a workflow transition, "
        "not a field edit."
    )
    return "\n".join(lines)


def render_issue(issue: dict[str, Any]) -> str:
    """Full issue → text block for the model: header, metadata, description,
    parent/subtasks/epic-children, then comments."""
    lines = [f"{issue['key']}: {issue.get('summary') or ''}"]
    meta = [t for t in (issue.get("type"), issue.get("status"), issue.get("priority")) if t]
    if meta:
        lines.append(" · ".join(meta))
    row = []
    if issue.get("project"):
        row.append(f"project: {issue['project']}")
    if issue.get("assignee"):
        row.append(f"assignee: {issue['assignee']}")
    if issue.get("reporter"):
        row.append(f"reporter: {issue['reporter']}")
    if issue.get("labels"):
        row.append("labels: " + ", ".join(issue["labels"]))
    if issue.get("due_date"):
        row.append(f"due: {issue['due_date']}")
    # `created` was fetched but never rendered — a dead field in the request.
    if issue.get("created"):
        row.append(f"created: {issue['created']}")
    if issue.get("updated"):
        row.append(f"updated: {issue['updated']}")
    if row:
        lines.append(" · ".join(row))
    if issue.get("url"):
        lines.append(issue["url"])
    if issue.get("parent"):
        p = issue["parent"]
        lines.append(f"parent: {p.get('key')} — {p.get('summary') or ''}")
    if issue.get("description"):
        lines.append("\ndescription:\n" + issue["description"])
    if issue.get("subtasks"):
        lines.append("\nsubtasks:")
        lines.extend(
            f"  - {s.get('key')}: {s.get('summary') or ''}"
            + (f" [{s['status']}]" if s.get("status") else "")
            for s in issue["subtasks"]
        )
    if issue.get("children"):
        lines.append(f"\nepic children ({len(issue['children'])}):")
        lines.extend(f"  - {render_hit(c)}" for c in issue["children"])
    # Everything else that has a value, under its human label. Rendered AFTER the
    # curated block so the familiar shape of an issue is unchanged, and last
    # before comments so a long custom-field list can't push the summary out of
    # view. This is where start date, story points, sprint and per-customer
    # custom fields surface — none of which the old fixed field list could name.
    if issue.get("other_fields"):
        lines.append("\nother fields:")
        lines.extend(
            f"  - {ff['name']}: {ff['value']}" for ff in issue["other_fields"]
        )
    if issue.get("comments"):
        lines.append("\ncomments:")
        lines.extend(f'  - {c["author"]}: {c["text"]}' for c in issue["comments"])
    return "\n".join(lines)

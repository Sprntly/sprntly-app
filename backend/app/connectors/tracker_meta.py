"""TrackerMeta — a provider-agnostic snapshot of ONE tracker destination's
vocabulary (statuses, priorities, issue types, custom fields), so the ticket
UI can mirror a customer's REAL Jira project / ClickUp list instead of
Sprntly's canned status/priority lists.

The normalized shape (cached per destination in the tracker_meta table by
app/db/tracker_meta.py, served to the web by the tracker-meta routes):

    {
      "provider": "jira" | "clickup",
      "destination_id": "KAN" | "901234",
      "statuses":   [{"id", "name", "color" | None, "category"}],
      "priorities": [{"id", "name", "color" | None}],
      "issue_types": [{"id", "name", "subtask"}] | None,   # Jira only
      "fields":     [{"id", "name", "type", "raw_type", "required",
                      "editable", "options": [{"id", "name", "color"}] | None}],
    }

`category` is the minimal canonical projection (open / in_progress / done)
Sprntly keeps for completion semantics regardless of vocabulary — it comes
free from Jira's statusCategory and ClickUp's status `type`, never from name
heuristics.

Custom fields: `type` is one of SUPPORTED_FIELD_TYPES when we can render an
editor for it; anything exotic (cascading selects, formulas, rollups, …)
keeps its `raw_type` and normalizes with `editable: False` so the UI shows
the value read-only instead of guessing. Values cross the wire and are
stored (ticket_edits.custom_fields) in provider-agnostic shapes — see
decode_field_value / encode_field_value, the ONLY places provider encodings
exist.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

PROVIDERS = ("clickup", "jira")

# Canonical status projection values.
CATEGORY_OPEN = "open"
CATEGORY_IN_PROGRESS = "in_progress"
CATEGORY_DONE = "done"

#: Field types the web can render an EDITOR for. Everything else normalizes
#: to editable=False (shown read-only) — never guess an editor for an
#: unknown type.
SUPPORTED_FIELD_TYPES = frozenset({
    "text", "textarea", "number", "date", "datetime",
    "select", "multiselect", "user", "users", "labels",
    "checkbox", "url", "email",
})

# Jira statusCategory.key → canonical category.
_JIRA_CATEGORY = {
    "new": CATEGORY_OPEN,
    "indeterminate": CATEGORY_IN_PROGRESS,
    "done": CATEGORY_DONE,
}

# Jira custom-field type (the suffix of schema.custom after the last colon)
# → our editor type. Absent = unsupported → editable False.
_JIRA_TYPE_MAP = {
    "textfield": "text",
    "textarea": "textarea",
    "float": "number",
    "datepicker": "date",
    "datetime": "datetime",
    "select": "select",
    "radiobuttons": "select",
    "multiselect": "multiselect",
    "multicheckboxes": "multiselect",
    "userpicker": "user",
    "multiuserpicker": "users",
    "labels": "labels",
    "url": "url",
}

# ClickUp custom-field `type` → our editor type. Absent = unsupported.
_CLICKUP_TYPE_MAP = {
    "short_text": "text",
    "text": "textarea",
    "number": "number",
    "currency": "number",
    "date": "date",
    "drop_down": "select",
    # ClickUp labels are option-id based (unlike Jira's free-string labels),
    # so they normalize as multiselect and carry their options.
    "labels": "multiselect",
    "checkbox": "checkbox",
    "email": "email",
    "url": "url",
    "phone": "text",
    "users": "users",
}

# ClickUp's fixed 1–4 priority scale (colors are ClickUp's defaults). Not an
# API read — ClickUp priorities aren't customizable per list.
CLICKUP_PRIORITIES = [
    {"id": "1", "name": "Urgent", "color": "#f50000"},
    {"id": "2", "name": "High", "color": "#ffcc00"},
    {"id": "3", "name": "Normal", "color": "#6fddff"},
    {"id": "4", "name": "Low", "color": "#d8d8d8"},
]

# BUILT-IN task properties (not custom fields — no definition API lists
# them), surfaced as meta fields with reserved `builtin:` ids so the ticket
# detail shows/edits them like everything else. The sync reads them off the
# task payload and writes them through the task-update APIs (see
# _Tracker.remote_custom_fields / push_custom_fields). Assignees and time
# tracking are deliberately absent — cross-system identity and timers aren't
# editable from Sprntly.
CLICKUP_BUILTIN_FIELDS = [
    {"id": "builtin:start_date", "name": "Start date", "type": "date",
     "raw_type": "builtin", "required": False, "editable": True, "options": None},
    {"id": "builtin:due_date", "name": "Due date", "type": "date",
     "raw_type": "builtin", "required": False, "editable": True, "options": None},
    {"id": "builtin:points", "name": "Sprint points", "type": "number",
     "raw_type": "builtin", "required": False, "editable": True, "options": None},
    # ClickUp tag removal has no uniform API — Sprntly-side tag edits ADD.
    {"id": "builtin:tags", "name": "Tags", "type": "labels",
     "raw_type": "builtin", "required": False, "editable": True, "options": None},
]
JIRA_BUILTIN_FIELDS = [
    {"id": "builtin:due_date", "name": "Due date", "type": "date",
     "raw_type": "builtin", "required": False, "editable": True, "options": None},
    {"id": "builtin:labels", "name": "Labels", "type": "labels",
     "raw_type": "builtin", "required": False, "editable": True, "options": None},
]


def jira_category_key_to_canonical(key: str | None) -> str:
    """Jira's raw statusCategory key ("new"/"indeterminate"/"done") → the
    canonical projection. Used wherever a Jira payload (e.g. a transition's
    target status) is normalized outside normalize_jira_meta."""
    return _JIRA_CATEGORY.get((key or "").lower(), CATEGORY_IN_PROGRESS)


def _clickup_category(status_type: str | None) -> str:
    """ClickUp status `type` → canonical category. ClickUp ships types
    open/custom/closed (some workspaces also surface "done")."""
    t = (status_type or "").lower()
    if t == "open":
        return CATEGORY_OPEN
    if t in ("closed", "done"):
        return CATEGORY_DONE
    return CATEGORY_IN_PROGRESS


# ── Normalizers ──────────────────────────────────────────────────────────────


def normalize_jira_meta(
    destination_id: str,
    *,
    statuses: list[dict[str, Any]],
    priorities: list[dict[str, Any]],
    createmeta: dict[str, Any],
) -> dict[str, Any]:
    """Build the normalized TrackerMeta from the three Jira reads
    (jira_oauth.get_project_statuses / list_priorities / get_create_meta)."""
    issue_types: list[dict[str, Any]] = []
    fields_by_id: dict[str, dict[str, Any]] = {}
    for it in createmeta.get("issuetypes") or []:
        if it.get("id") and it.get("name"):
            issue_types.append({
                "id": it["id"], "name": it["name"],
                "subtask": bool(it.get("subtask")),
            })
        for fid, fdef in (it.get("fields") or {}).items():
            # Only CUSTOM fields — system fields (summary, priority, …) have
            # dedicated UI already.
            if not fid.startswith("customfield_") or fid in fields_by_id:
                continue
            schema = fdef.get("schema") or {}
            raw_type = (schema.get("custom") or schema.get("type") or "")
            our_type = _JIRA_TYPE_MAP.get(raw_type.rsplit(":", 1)[-1])
            options = [
                {
                    "id": o.get("id"),
                    "name": o.get("value") or o.get("name"),
                    "color": None,
                }
                for o in fdef.get("allowedValues") or []
                if o.get("value") or o.get("name")
            ] or None
            fields_by_id[fid] = {
                "id": fid,
                "name": fdef.get("name") or fid,
                "type": our_type or "unsupported",
                "raw_type": raw_type,
                "required": bool(fdef.get("required")),
                "editable": our_type in SUPPORTED_FIELD_TYPES,
                "options": options,
            }
    return {
        "provider": "jira",
        "destination_id": destination_id,
        "statuses": [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "color": None,
                "category": _JIRA_CATEGORY.get(
                    (s.get("category") or "").lower(), CATEGORY_IN_PROGRESS
                ),
            }
            for s in statuses
        ],
        "priorities": [
            {"id": p.get("id"), "name": p.get("name"), "color": p.get("color")}
            for p in priorities
        ],
        "issue_types": issue_types or None,
        "fields": [dict(f) for f in JIRA_BUILTIN_FIELDS] + list(fields_by_id.values()),
    }


def normalize_clickup_meta(
    destination_id: str,
    *,
    list_payload: dict[str, Any],
    fields: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the normalized TrackerMeta from the two ClickUp reads
    (clickup_oauth.get_list / get_list_custom_fields)."""
    out_fields: list[dict[str, Any]] = []
    for f in fields:
        fid = f.get("id")
        if not fid:
            continue
        raw_type = f.get("type") or ""
        our_type = _CLICKUP_TYPE_MAP.get(raw_type)
        options = [
            {
                "id": o.get("id"),
                # drop_down options carry `name`; labels options carry `label`.
                "name": o.get("name") or o.get("label"),
                "color": o.get("color"),
            }
            for o in ((f.get("type_config") or {}).get("options") or [])
            if o.get("name") or o.get("label")
        ] or None
        out_fields.append({
            "id": fid,
            "name": f.get("name") or fid,
            "type": our_type or "unsupported",
            "raw_type": raw_type,
            "required": bool(f.get("required")),
            "editable": our_type in SUPPORTED_FIELD_TYPES,
            "options": options,
        })
    return {
        "provider": "clickup",
        "destination_id": destination_id,
        "statuses": [
            {
                "id": s.get("id") or s.get("status"),
                "name": s.get("status"),
                "color": s.get("color"),
                "category": _clickup_category(s.get("type")),
            }
            for s in (list_payload.get("statuses") or [])
            if s.get("status")
        ],
        "priorities": [dict(p) for p in CLICKUP_PRIORITIES],
        "issue_types": None,
        "fields": [dict(f) for f in CLICKUP_BUILTIN_FIELDS] + out_fields,
    }


# ── Fetch (live read → normalized shape) ─────────────────────────────────────


def fetch_tracker_meta(
    company_id: str, provider: str, destination_id: str
) -> dict[str, Any]:
    """Fetch + normalize a destination's live metadata. Raises the provider's
    NotConnected error when there's no usable connection; the db layer
    (get_or_fetch_meta) decides whether to fall back to a stale cache."""
    # Lazy imports: push.py owns credential resolution (token refresh etc.).
    if provider == "jira":
        from app.connectors import jira_oauth
        from app.stories.push import _jira_creds

        access_token, cloud_id = _jira_creds(company_id)
        meta = normalize_jira_meta(
            destination_id,
            statuses=jira_oauth.get_project_statuses(
                access_token, cloud_id, destination_id
            ),
            priorities=jira_oauth.list_priorities(access_token, cloud_id),
            createmeta=jira_oauth.get_create_meta(
                access_token, cloud_id, destination_id
            ),
        )
    elif provider == "clickup":
        from app.connectors import clickup_oauth
        from app.stories.push import _clickup_access_token

        access_token = _clickup_access_token(company_id)
        meta = normalize_clickup_meta(
            destination_id,
            list_payload=clickup_oauth.get_list(access_token, destination_id),
            fields=clickup_oauth.get_list_custom_fields(
                access_token, destination_id
            ),
        )
    else:
        raise ValueError(f"unknown tracker provider {provider!r}")
    meta["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return meta


# ── Connect-time warm (whole workspace, no binding required) ────────────────


def warm_company_tracker_meta(
    company_id: str, provider: str, max_destinations: int = 10
) -> int:
    """Prefetch + cache the vocabulary of EVERY destination the connection
    can see (Jira projects / ClickUp lists), so the ticket detail is
    tracker-native from the moment of connection — before any push/binding.
    Capped for pathological workspaces; the bind hook + TTL cover the rest.
    Returns the number of destinations cached."""
    from app.db.tracker_meta import get_or_fetch_meta

    if provider == "jira":
        from app.connectors import jira_oauth
        from app.stories.push import _jira_creds

        token, cloud = _jira_creds(company_id)
        destinations = [
            p["key"] for p in jira_oauth.list_projects(token, cloud) if p.get("key")
        ]
    elif provider == "clickup":
        from app.connectors import clickup_oauth
        from app.stories.push import _clickup_access_token

        token = _clickup_access_token(company_id)
        destinations = [
            l["id"] for l in clickup_oauth.list_lists(token) if l.get("id")
        ]
    else:
        return 0

    warmed = 0
    if len(destinations) > max_destinations:
        logger.info(
            "tracker-meta warm capped at %s of %s destinations for %s/%s",
            max_destinations, len(destinations), company_id, provider,
        )
    for destination_id in destinations[:max_destinations]:
        if get_or_fetch_meta(company_id, provider, destination_id, refresh=True):
            warmed += 1
    return warmed


def kick_company_meta_warm(company_id: str, provider: str) -> bool:
    """Fire-and-forget warm_company_tracker_meta on a daemon thread — called
    from the OAuth callbacks (sync-def routes, no event loop). Best-effort:
    a failure only means the cache warms later (bind hook / TTL / route)."""
    if provider not in PROVIDERS:
        return False
    import threading

    def _run() -> None:
        try:
            n = warm_company_tracker_meta(company_id, provider)
            logger.info(
                "tracker-meta warmed %s destination(s) for %s/%s",
                n, company_id, provider,
            )
        except Exception:  # noqa: BLE001 — warming is best-effort by design
            logger.warning(
                "tracker-meta connect-time warm failed for %s/%s",
                company_id, provider,
            )

    threading.Thread(
        target=_run, daemon=True, name=f"tracker-meta-warm-{provider}"
    ).start()
    return True


# ── Lookups on a normalized meta ─────────────────────────────────────────────


def status_category(meta: dict[str, Any] | None, status_name: str | None) -> str | None:
    """The canonical open/in_progress/done projection for a tracker status
    name (case-insensitive), or None when the meta/status is unknown."""
    if not meta or not status_name:
        return None
    want = status_name.strip().lower()
    for s in meta.get("statuses") or []:
        if (s.get("name") or "").strip().lower() == want:
            return s.get("category")
    return None


def priority_by_name(meta: dict[str, Any] | None, name: str | None) -> dict[str, Any] | None:
    """The meta's priority entry matching `name` (case-insensitive), or None."""
    if not meta or not name:
        return None
    want = name.strip().lower()
    for p in meta.get("priorities") or []:
        if (p.get("name") or "").strip().lower() == want:
            return p
    return None


def field_def(meta: dict[str, Any] | None, field_id: str) -> dict[str, Any] | None:
    """The meta's custom-field definition for `field_id`, or None."""
    for f in (meta or {}).get("fields") or []:
        if f.get("id") == field_id:
            return f
    return None


# ── Write validation (bound tickets speak the tracker's vocabulary) ──────────
#
# Legacy Sprntly status/priority names resolve THROUGH the canonical category
# instead of being rejected: an MCP agent following the server instructions
# ("move to 'In progress' → 'Done'"), or an old edit row, keeps working
# against any workspace — the write lands on the destination's real status of
# the same category.

_LEGACY_STATUS_CATEGORY = {
    "backlog": CATEGORY_OPEN, "to do": CATEGORY_OPEN, "todo": CATEGORY_OPEN,
    "open": CATEGORY_OPEN,
    "in progress": CATEGORY_IN_PROGRESS, "review": CATEGORY_IN_PROGRESS,
    "in review": CATEGORY_IN_PROGRESS,
    "done": CATEGORY_DONE, "closed": CATEGORY_DONE,
}

# Legacy priority vocab (generator enum + the drawer's P-labels) → candidate
# tracker names in preference order (Jira ships Highest/../Low; ClickUp ships
# Urgent/../Low; custom Jira schemes may carry either style).
_LEGACY_PRIORITY_CANDIDATES = {
    "urgent": ("highest", "urgent"), "p0": ("highest", "urgent"),
    "high": ("high",), "p1": ("high",),
    "normal": ("medium", "normal"), "p2": ("medium", "normal"),
    "low": ("low",), "p3": ("low",),
}


def resolve_status(meta: dict[str, Any], value: str) -> str | None:
    """`value` → a REAL status name of the destination: exact (case-insensitive)
    match wins; else a legacy/canonical alias lands on the first status of the
    same category. None when unresolvable."""
    want = value.strip().lower()
    for s in meta.get("statuses") or []:
        if (s.get("name") or "").strip().lower() == want:
            return s.get("name")
    category = _LEGACY_STATUS_CATEGORY.get(want)
    if category:
        for s in meta.get("statuses") or []:
            if s.get("category") == category:
                return s.get("name")
    return None


def resolve_priority(meta: dict[str, Any], value: str) -> str | None:
    """`value` → a REAL priority name of the destination (exact match, else
    legacy-vocab candidates). None when unresolvable."""
    hit = priority_by_name(meta, value)
    if hit:
        return hit.get("name")
    for candidate in _LEGACY_PRIORITY_CANDIDATES.get(value.strip().lower(), ()):
        hit = priority_by_name(meta, candidate)
        if hit:
            return hit.get("name")
    return None


def resolve_issue_type(meta: dict[str, Any], value: str) -> str | None:
    """`value` → a REAL issue-type name of the destination (case-insensitive
    exact match, non-subtask types only). None when unresolvable or the
    destination carries no issue types (ClickUp)."""
    want = value.strip().lower()
    for t in meta.get("issue_types") or []:
        if not t.get("subtask") and (t.get("name") or "").strip().lower() == want:
            return t.get("name")
    return None


def validate_fields_against_meta(
    company_id: str, ticket_key: str, fields: dict[str, Any]
) -> dict[str, Any]:
    """Validate + tracker-normalize a ticket-fields patch before it's saved.

    When the ticket's PRD is bound to a destination WITH cached meta, `status`
    and `priority` must resolve to that destination's real vocabulary — the
    resolved (exact-cased) names are returned in a copy of the patch, and an
    unresolvable value raises 422 listing the allowed names (so both the web
    and MCP agents can self-correct). `custom_fields` entries must name an
    editable field and carry an encodable value. Unbound PRDs / no meta /
    malformed keys return the patch untouched — exactly the legacy free-text
    behavior."""
    if not fields or (
        fields.get("status") is None
        and fields.get("priority") is None
        and fields.get("issue_type") is None
        and not fields.get("custom_fields")
    ):
        return fields
    parts = ticket_key.split("-", 2)
    if not (len(parts) == 3 and parts[0] == "prd" and parts[1].isdigit()):
        return fields

    from app.db.ticket_sync import get_sync_config
    from app.db.tracker_meta import get_cached_meta

    cfg = get_sync_config(company_id, int(parts[1]))
    if not cfg:
        return fields
    try:
        meta = get_cached_meta(company_id, cfg["provider"], cfg["destination_id"])
    except Exception:  # noqa: BLE001 — a cache outage must not block saves
        meta = None
    if not meta:
        return fields

    from fastapi import HTTPException

    out = dict(fields)
    if fields.get("status") is not None:
        resolved = resolve_status(meta, str(fields["status"]))
        if not resolved:
            allowed = [s.get("name") for s in meta.get("statuses") or []]
            raise HTTPException(422, {
                "message": f"Unknown status {fields['status']!r} for this "
                           f"PRD's {cfg['provider']} destination",
                "allowed_statuses": allowed,
            })
        out["status"] = resolved
    if fields.get("priority") is not None:
        resolved = resolve_priority(meta, str(fields["priority"]))
        if not resolved:
            allowed = [p.get("name") for p in meta.get("priorities") or []]
            raise HTTPException(422, {
                "message": f"Unknown priority {fields['priority']!r} for this "
                           f"PRD's {cfg['provider']} destination",
                "allowed_priorities": allowed,
            })
        out["priority"] = resolved
    if fields.get("issue_type") is not None and meta.get("issue_types"):
        resolved = resolve_issue_type(meta, str(fields["issue_type"]))
        if not resolved:
            allowed = [
                t.get("name") for t in meta.get("issue_types") or []
                if not t.get("subtask")
            ]
            raise HTTPException(422, {
                "message": f"Unknown issue type {fields['issue_type']!r} for "
                           f"this PRD's {cfg['provider']} destination",
                "allowed_issue_types": allowed,
            })
        out["issue_type"] = resolved
    if fields.get("custom_fields"):
        for fid, value in fields["custom_fields"].items():
            fdef = field_def(meta, fid)
            if not fdef or not fdef.get("editable"):
                editable = [
                    f["id"] for f in meta.get("fields") or [] if f.get("editable")
                ]
                raise HTTPException(422, {
                    "message": f"Unknown or read-only custom field {fid!r}",
                    "editable_fields": editable,
                })
            if value is None:  # null clears the override — always valid
                continue
            try:
                encode_field_value(cfg["provider"], fdef, value)
            except (TypeError, ValueError) as e:
                raise HTTPException(422, {
                    "message": f"Invalid value for custom field "
                               f"{fdef.get('name') or fid!r}: {e}",
                    "field": fid,
                    "type": fdef.get("type"),
                    "options": fdef.get("options"),
                }) from e
    return out


# ── Custom-field value codecs ────────────────────────────────────────────────
#
# Normalized value shapes (what ticket_edits.custom_fields stores and what
# the wire uses), by editor type:
#   text/textarea/url/email → str        number → int|float     checkbox → bool
#   date → "YYYY-MM-DD"                  datetime → ISO str
#   select/user → {"id", "name"}         multiselect/users → [{"id", "name"}]
#   labels → [str]
# Provider encodings exist ONLY here.


def _option_ref(field: dict[str, Any], raw: Any) -> dict[str, Any] | None:
    """Resolve a provider option reference (id / orderindex / {id,value} obj)
    to the normalized {"id", "name"} via the field's options."""
    options = field.get("options") or []
    if isinstance(raw, dict):
        oid = raw.get("id")
        name = raw.get("value") or raw.get("name") or raw.get("label")
        if oid or name:
            return {"id": oid, "name": name}
        return None
    for idx, o in enumerate(options):
        if raw == o.get("id") or raw == idx:
            return {"id": o.get("id"), "name": o.get("name")}
    return None


def decode_field_value(
    provider: str, field: dict[str, Any], raw: Any
) -> Any:
    """Provider-raw custom-field value → normalized shape (None when empty
    or undecodable — an undecodable value must read as 'unset', never crash
    a sync pass)."""
    if raw is None:
        return None
    ftype = field.get("type")
    try:
        if ftype in ("text", "textarea", "url", "email"):
            if isinstance(raw, dict):  # Jira v3 rich-text (ADF) textarea
                from app.connectors.jira_oauth import _text_from_adf
                return _text_from_adf(raw) or None
            return str(raw) or None
        if ftype == "number":
            return float(raw) if not isinstance(raw, (int, float)) else raw
        if ftype == "checkbox":
            if isinstance(raw, str):
                return raw.strip().lower() == "true"
            return bool(raw)
        if ftype in ("date", "datetime"):
            if provider == "clickup":  # ms-epoch (str or int) → ISO date
                ms = int(raw)
                dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
                return dt.date().isoformat() if ftype == "date" else dt.isoformat()
            return str(raw)
        if ftype == "select":
            return _option_ref(field, raw)
        if ftype == "multiselect":
            items = raw if isinstance(raw, list) else [raw]
            return [r for r in (_option_ref(field, i) for i in items) if r] or None
        if ftype == "user":
            if isinstance(raw, dict):
                return {
                    "id": str(raw.get("accountId") or raw.get("id") or ""),
                    "name": raw.get("displayName") or raw.get("username")
                            or raw.get("email"),
                }
            return None
        if ftype == "users":
            items = raw if isinstance(raw, list) else [raw]
            out = [
                {
                    "id": str(u.get("accountId") or u.get("id") or ""),
                    "name": u.get("displayName") or u.get("username")
                            or u.get("email"),
                }
                for u in items if isinstance(u, dict)
            ]
            return out or None
        if ftype == "labels":
            return [str(x) for x in raw] if isinstance(raw, list) else None
    except (TypeError, ValueError):
        logger.warning(
            "undecodable %s custom-field value for %s", provider, field.get("id")
        )
        return None
    return None


def encode_field_value(
    provider: str, field: dict[str, Any], value: Any
) -> Any:
    """Normalized custom-field value → the provider's write encoding (Jira:
    the fields{} entry for update_issue extra_fields; ClickUp: the `value`
    for set_custom_field). Raises ValueError for a value that can't encode —
    callers validate before writing."""
    ftype = field.get("type")
    if field.get("editable") is False:
        raise ValueError(f"field {field.get('id')} is not editable")
    if value is None:
        return None
    if ftype in ("text", "url", "email"):
        return str(value)
    if ftype == "textarea":
        if provider == "jira":  # Jira v3 rich-text custom fields want ADF
            from app.connectors.jira_oauth import _adf_from_text
            return _adf_from_text(str(value))
        return str(value)
    if ftype == "number":
        return value if isinstance(value, (int, float)) else float(value)
    if ftype == "checkbox":
        return bool(value)
    if ftype in ("date", "datetime"):
        if provider == "clickup":  # ClickUp date fields take ms-epoch
            dt = datetime.fromisoformat(str(value))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        return str(value)
    if ftype == "select":
        oid = (value or {}).get("id") if isinstance(value, dict) else None
        if provider == "jira":
            if oid:
                return {"id": str(oid)}
            name = value.get("name") if isinstance(value, dict) else value
            return {"value": str(name)}
        if not oid:
            raise ValueError("ClickUp drop_down values need an option id")
        return str(oid)
    if ftype == "multiselect":
        items = value if isinstance(value, list) else [value]
        ids = [str(i["id"]) for i in items if isinstance(i, dict) and i.get("id")]
        if provider == "jira":
            return [{"id": i} for i in ids]
        return ids
    if ftype == "user":
        uid = (value or {}).get("id") if isinstance(value, dict) else value
        if provider == "jira":
            return {"accountId": str(uid)}
        return {"add": [uid]}
    if ftype == "users":
        items = value if isinstance(value, list) else [value]
        ids = [i["id"] for i in items if isinstance(i, dict) and i.get("id")]
        if provider == "jira":
            return [{"accountId": str(i)} for i in ids]
        return {"add": ids}
    if ftype == "labels":
        return [str(x) for x in (value if isinstance(value, list) else [value])]
    raise ValueError(f"cannot encode field type {ftype!r}")

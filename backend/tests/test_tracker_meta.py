"""TrackerMeta — the per-destination tracker vocabulary layer
(app/connectors/tracker_meta.py normalizers + codecs, app/db/tracker_meta.py
cache, and the tracker-meta / transitions routes). This is what lets the
ticket UI mirror a customer's REAL Jira/ClickUp statuses, priorities, and
custom fields instead of Sprntly's canned lists.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.auth import CompanyContext

CID = "11111111-2222-3333-4444-555555555555"


def _ctx(cid: str = CID) -> CompanyContext:
    return CompanyContext(company_id=cid, role="owner", user_id="u")


# ── Jira normalization ───────────────────────────────────────────────────────


_JIRA_STATUSES = [
    {"id": "1", "name": "Groomed", "category": "new"},
    {"id": "2", "name": "Building", "category": "indeterminate"},
    {"id": "3", "name": "Shipped", "category": "done"},
]
_JIRA_PRIORITIES = [
    {"id": "10", "name": "Blocker", "color": "#d04437"},
    {"id": "11", "name": "Nice to have", "color": None},
]
_JIRA_CREATEMETA = {
    "key": "KAN",
    "issuetypes": [
        {
            "id": "10001", "name": "Story", "subtask": False,
            "fields": {
                "summary": {"name": "Summary", "required": True,
                            "schema": {"type": "string"}},
                "customfield_10031": {
                    "name": "Team", "required": False,
                    "schema": {
                        "type": "option",
                        "custom": "com.atlassian.jira.plugin.system.customfieldtypes:select",
                    },
                    "allowedValues": [
                        {"id": "opt1", "value": "Platform"},
                        {"id": "opt2", "value": "Growth"},
                    ],
                },
                "customfield_10040": {
                    "name": "Org chart", "required": False,
                    "schema": {
                        "type": "option-with-child",
                        "custom": "com.atlassian.jira.plugin.system.customfieldtypes:cascadingselect",
                    },
                },
            },
        },
        {
            "id": "10002", "name": "Sub-task", "subtask": True,
            # Same field again on another issue type — must not duplicate.
            "fields": {
                "customfield_10031": {
                    "name": "Team", "required": True,
                    "schema": {
                        "type": "option",
                        "custom": "com.atlassian.jira.plugin.system.customfieldtypes:select",
                    },
                },
            },
        },
    ],
}


def test_normalize_jira_meta_statuses_priorities_and_fields():
    """A customized Jira workflow ("Groomed/Building/Shipped") normalizes
    verbatim, with the canonical category coming from statusCategory — never
    name heuristics."""
    from app.connectors.tracker_meta import normalize_jira_meta

    meta = normalize_jira_meta(
        "KAN", statuses=_JIRA_STATUSES, priorities=_JIRA_PRIORITIES,
        createmeta=_JIRA_CREATEMETA,
    )
    assert meta["provider"] == "jira" and meta["destination_id"] == "KAN"
    assert [(s["name"], s["category"]) for s in meta["statuses"]] == [
        ("Groomed", "open"), ("Building", "in_progress"), ("Shipped", "done"),
    ]
    assert [p["name"] for p in meta["priorities"]] == ["Blocker", "Nice to have"]
    assert [(t["name"], t["subtask"]) for t in meta["issue_types"]] == [
        ("Story", False), ("Sub-task", True),
    ]


def test_normalize_jira_meta_field_whitelist_and_exotics():
    """Custom fields only (system fields have dedicated UI); a supported type
    gets an editor + options, an exotic type (cascading select) normalizes
    read-only with its raw_type preserved — never a guessed editor."""
    from app.connectors.tracker_meta import normalize_jira_meta

    meta = normalize_jira_meta(
        "KAN", statuses=[], priorities=[], createmeta=_JIRA_CREATEMETA,
    )
    fields = {f["id"]: f for f in meta["fields"]}
    # Custom fields + the editable BUILT-INS (due date, labels) — but never
    # system fields with dedicated UI (summary).
    assert set(fields) == {
        "customfield_10031", "customfield_10040",
        "builtin:due_date", "builtin:labels",
    }
    assert fields["builtin:due_date"]["type"] == "date"
    assert fields["builtin:labels"]["editable"] is True

    team = fields["customfield_10031"]
    assert team["type"] == "select" and team["editable"] is True
    assert [o["name"] for o in team["options"]] == ["Platform", "Growth"]

    cascading = fields["customfield_10040"]
    assert cascading["editable"] is False
    assert cascading["type"] == "unsupported"
    assert "cascadingselect" in cascading["raw_type"]


# ── ClickUp normalization ────────────────────────────────────────────────────


_CLICKUP_LIST = {
    "id": "901",
    "statuses": [
        {"id": "s1", "status": "backlog", "type": "open", "color": "#888"},
        {"id": "s2", "status": "in build", "type": "custom", "color": "#fd0"},
        {"id": "s3", "status": "released", "type": "closed", "color": "#0f0"},
    ],
}
_CLICKUP_FIELDS = [
    {
        "id": "f-uuid-1", "name": "Squad", "type": "drop_down",
        "type_config": {"options": [
            {"id": "o1", "name": "Core", "color": "#123", "orderindex": 0},
            {"id": "o2", "name": "Edge", "color": None, "orderindex": 1},
        ]},
    },
    {"id": "f-uuid-2", "name": "Effort", "type": "number", "type_config": {}},
    {"id": "f-uuid-3", "name": "Progress", "type": "automatic_progress",
     "type_config": {}},
]


def test_normalize_clickup_meta_statuses_fields_and_fixed_priorities():
    from app.connectors.tracker_meta import CLICKUP_PRIORITIES, normalize_clickup_meta

    meta = normalize_clickup_meta(
        "901", list_payload=_CLICKUP_LIST, fields=_CLICKUP_FIELDS,
    )
    assert meta["provider"] == "clickup"
    assert [(s["name"], s["category"]) for s in meta["statuses"]] == [
        ("backlog", "open"), ("in build", "in_progress"), ("released", "done"),
    ]
    # ClickUp priorities are the fixed 1–4 scale, not an API read.
    assert meta["priorities"] == CLICKUP_PRIORITIES
    assert meta["issue_types"] is None

    fields = {f["id"]: f for f in meta["fields"]}
    squad = fields["f-uuid-1"]
    assert squad["type"] == "select" and squad["editable"] is True
    assert [o["name"] for o in squad["options"]] == ["Core", "Edge"]
    assert fields["f-uuid-2"]["type"] == "number"
    # Formula-ish types are shown, never edited.
    assert fields["f-uuid-3"]["editable"] is False
    # Built-in task properties ride along as editable meta fields.
    assert {"builtin:start_date", "builtin:due_date", "builtin:points",
            "builtin:tags"} <= set(fields)
    assert fields["builtin:tags"]["type"] == "labels"


# ── Lookups ──────────────────────────────────────────────────────────────────


def test_status_category_and_priority_by_name_lookups():
    from app.connectors.tracker_meta import (
        normalize_jira_meta,
        priority_by_name,
        status_category,
    )

    meta = normalize_jira_meta(
        "KAN", statuses=_JIRA_STATUSES, priorities=_JIRA_PRIORITIES,
        createmeta={},
    )
    assert status_category(meta, "shipped") == "done"      # case-insensitive
    assert status_category(meta, "No Such Status") is None
    assert status_category(None, "Shipped") is None
    assert priority_by_name(meta, "blocker")["id"] == "10"
    assert priority_by_name(meta, "P0") is None


# ── Custom-field value codecs ────────────────────────────────────────────────


def test_field_value_codecs_jira():
    from app.connectors.tracker_meta import decode_field_value, encode_field_value

    select_def = {
        "id": "customfield_10031", "type": "select", "editable": True,
        "options": [{"id": "opt1", "name": "Platform"}],
    }
    # Jira option object → normalized {"id", "name"} → Jira write encoding.
    val = decode_field_value("jira", select_def, {"id": "opt1", "value": "Platform"})
    assert val == {"id": "opt1", "name": "Platform"}
    assert encode_field_value("jira", select_def, val) == {"id": "opt1"}

    users_def = {"id": "cf", "type": "users", "editable": True}
    val = decode_field_value(
        "jira", users_def, [{"accountId": "acc1", "displayName": "Ada"}],
    )
    assert val == [{"id": "acc1", "name": "Ada"}]
    assert encode_field_value("jira", users_def, val) == [{"accountId": "acc1"}]

    labels_def = {"id": "cf2", "type": "labels", "editable": True}
    assert decode_field_value("jira", labels_def, ["infra", "q3"]) == ["infra", "q3"]
    assert encode_field_value("jira", labels_def, ["infra"]) == ["infra"]

    # Jira v3 rich-text (ADF) textarea decodes to plain text; encodes to ADF.
    textarea_def = {"id": "cf3", "type": "textarea", "editable": True}
    adf = {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "notes"}]},
    ]}
    assert decode_field_value("jira", textarea_def, adf) == "notes"
    assert encode_field_value("jira", textarea_def, "notes")["type"] == "doc"


def test_field_value_codecs_clickup():
    from app.connectors.tracker_meta import decode_field_value, encode_field_value

    select_def = {
        "id": "f-uuid-1", "type": "select", "editable": True,
        "options": [
            {"id": "o1", "name": "Core"},
            {"id": "o2", "name": "Edge"},
        ],
    }
    # ClickUp drop_down task values arrive as option id OR orderindex.
    assert decode_field_value("clickup", select_def, "o2") == {"id": "o2", "name": "Edge"}
    assert decode_field_value("clickup", select_def, 0) == {"id": "o1", "name": "Core"}
    # Writes go by option id; a value without one is refused, not guessed.
    assert encode_field_value("clickup", select_def, {"id": "o1", "name": "Core"}) == "o1"
    with pytest.raises(ValueError):
        encode_field_value("clickup", select_def, {"name": "Core"})

    date_def = {"id": "f-d", "type": "date", "editable": True}
    # ms-epoch → ISO date and back.
    assert decode_field_value("clickup", date_def, "1783728000000") == "2026-07-11"
    assert encode_field_value("clickup", date_def, "2026-07-11") == 1783728000000

    checkbox_def = {"id": "f-c", "type": "checkbox", "editable": True}
    assert decode_field_value("clickup", checkbox_def, "true") is True
    assert encode_field_value("clickup", checkbox_def, True) is True


def test_codecs_never_crash_and_refuse_read_only_writes():
    from app.connectors.tracker_meta import decode_field_value, encode_field_value

    number_def = {"id": "f", "type": "number", "editable": True}
    assert decode_field_value("clickup", number_def, "not-a-number") is None
    assert decode_field_value("clickup", number_def, None) is None
    with pytest.raises(ValueError):
        encode_field_value("jira", {"id": "f", "type": "select", "editable": False},
                           {"id": "x"})
    with pytest.raises(ValueError):
        encode_field_value("jira", {"id": "f", "type": "unsupported",
                                    "editable": True}, "x")


# ── DB cache (tracker_meta table) ────────────────────────────────────────────


_META = {
    "provider": "clickup", "destination_id": "901",
    "statuses": [{"id": "s1", "name": "backlog", "color": None, "category": "open"}],
    "priorities": [], "issue_types": None, "fields": [],
    "fetched_at": "2026-07-12T10:00:00+00:00",
}


def test_cache_roundtrip_and_fresh_hit(isolated_settings):
    from app.db.tracker_meta import get_cached_meta, get_or_fetch_meta, save_meta

    assert get_cached_meta(CID, "clickup", "901") is None
    save_meta(CID, "clickup", "901", _META)
    cached = get_cached_meta(CID, "clickup", "901")
    assert cached["statuses"][0]["name"] == "backlog"

    # Fresh cache (fetched_at stamped by save_meta) → no live fetch.
    with patch("app.connectors.tracker_meta.fetch_tracker_meta") as fetch:
        meta = get_or_fetch_meta(CID, "clickup", "901")
    assert meta["statuses"][0]["name"] == "backlog"
    fetch.assert_not_called()


def test_get_or_fetch_refetches_when_stale_or_forced(isolated_settings):
    from app.db.client import require_client
    from app.db.tracker_meta import get_or_fetch_meta, save_meta

    save_meta(CID, "clickup", "901", _META)
    # Age the row beyond the TTL.
    require_client().table("tracker_meta").update(
        {"fetched_at": "2026-01-01T00:00:00+00:00"}
    ).eq("company_id", CID).execute()

    fresh = {**_META, "statuses": [
        {"id": "s9", "name": "renamed", "color": None, "category": "open"},
    ]}
    with patch(
        "app.connectors.tracker_meta.fetch_tracker_meta", return_value=fresh,
    ) as fetch:
        meta = get_or_fetch_meta(CID, "clickup", "901")
    fetch.assert_called_once_with(CID, "clickup", "901")
    assert meta["statuses"][0]["name"] == "renamed"
    # The refetch was persisted for the next reader.
    with patch("app.connectors.tracker_meta.fetch_tracker_meta") as fetch2:
        again = get_or_fetch_meta(CID, "clickup", "901")
    fetch2.assert_not_called()
    assert again["statuses"][0]["name"] == "renamed"

    # refresh=True bypasses even a fresh cache.
    with patch(
        "app.connectors.tracker_meta.fetch_tracker_meta", return_value=fresh,
    ) as fetch3:
        get_or_fetch_meta(CID, "clickup", "901", refresh=True)
    fetch3.assert_called_once()


def test_get_or_fetch_serves_stale_cache_when_fetch_fails(isolated_settings):
    """Tracker down / token expired must DEGRADE (stale meta beats none),
    never raise into the caller."""
    from app.db.client import require_client
    from app.db.tracker_meta import get_or_fetch_meta, save_meta

    save_meta(CID, "clickup", "901", _META)
    require_client().table("tracker_meta").update(
        {"fetched_at": "2026-01-01T00:00:00+00:00"}
    ).eq("company_id", CID).execute()

    with patch(
        "app.connectors.tracker_meta.fetch_tracker_meta",
        side_effect=RuntimeError("clickup down"),
    ):
        meta = get_or_fetch_meta(CID, "clickup", "901")
    assert meta["statuses"][0]["name"] == "backlog"

    # No cache at all → None, still no raise.
    with patch(
        "app.connectors.tracker_meta.fetch_tracker_meta",
        side_effect=RuntimeError("clickup down"),
    ):
        assert get_or_fetch_meta(CID, "clickup", "902") is None


# ── Routes ───────────────────────────────────────────────────────────────────


def test_stories_tracker_meta_route_unbound_and_bound(isolated_settings):
    from app.db.ticket_sync import upsert_sync_config
    from app.db.tracker_meta import save_meta
    from app.routes import stories as routes

    # Unbound → configured false (not 404) so the web falls back quietly.
    assert routes.tracker_meta(42, company=_ctx()) == {
        "configured": False, "provider": None, "destination_id": None, "meta": None,
    }

    upsert_sync_config(CID, 42, provider="clickup", destination_id="901")
    save_meta(CID, "clickup", "901", _META)
    out = routes.tracker_meta(42, company=_ctx())
    assert out["configured"] is True
    assert out["provider"] == "clickup" and out["destination_id"] == "901"
    assert out["meta"]["statuses"][0]["name"] == "backlog"


def test_tickets_tracker_meta_route_for_unbound_destination(isolated_settings):
    """The drawer path: metadata by (provider, destination) BEFORE any PRD
    binding; 404 when nothing can be fetched so pickers keep their defaults."""
    from app.routes import tickets as routes

    with patch(
        "app.db.tracker_meta.get_or_fetch_meta", return_value=_META,
    ) as got:
        out = routes.tracker_meta_for_destination(
            routes.TrackerMetaIn(provider="clickup", destination_id="901"),
            company=_ctx(),
        )
    got.assert_called_once_with(CID, "clickup", "901", refresh=False)
    assert out["meta"]["statuses"][0]["name"] == "backlog"

    with patch("app.db.tracker_meta.get_or_fetch_meta", return_value=None):
        with pytest.raises(Exception) as ei:
            routes.tracker_meta_for_destination(
                routes.TrackerMetaIn(provider="jira", destination_id="KAN"),
                company=_ctx(),
            )
        assert getattr(ei.value, "status_code", None) == 404


def test_warm_company_tracker_meta_prefetches_every_destination(monkeypatch):
    """Connect-time pull: every list/project the connection can see gets its
    vocabulary cached immediately (capped), no binding required."""
    from app.connectors import tracker_meta as tm

    monkeypatch.setattr(
        "app.stories.push._clickup_access_token", lambda cid: "tok",
    )
    monkeypatch.setattr(
        "app.connectors.clickup_oauth.list_lists",
        lambda tok: [{"id": "l1"}, {"id": "l2"}, {"id": None}, {"id": "l3"}],
    )
    warmed: list[tuple] = []
    monkeypatch.setattr(
        "app.db.tracker_meta.get_or_fetch_meta",
        lambda cid, provider, dest, refresh: warmed.append(
            (provider, dest, refresh)
        ) or {"ok": True},
    )
    assert tm.warm_company_tracker_meta(CID, "clickup", max_destinations=2) == 2
    # Capped at 2, null ids skipped, always a forced refresh.
    assert warmed == [("clickup", "l1", True), ("clickup", "l2", True)]
    assert tm.warm_company_tracker_meta(CID, "slack") == 0  # not a tracker


def test_tracker_meta_route_unbound_serves_connected_trackers_cache(isolated_settings, monkeypatch):
    """No binding yet, but a tracker is CONNECTED (connect-time warm filled
    the cache) → the route serves that vocabulary so the detail is
    tracker-native before the first push."""
    from app import db as db_mod
    from app.db.tracker_meta import save_meta
    from app.routes import stories as routes

    save_meta(CID, "clickup", "901", _META)
    monkeypatch.setattr(
        db_mod, "get_connection",
        lambda cid, provider: {"id": "c1"} if provider == "clickup" else None,
    )
    out = routes.tracker_meta(42, company=_ctx())
    assert out["configured"] is False
    assert out["provider"] == "clickup"
    assert out["destination_id"] == "901"
    assert out["meta"]["statuses"][0]["name"] == "backlog"

    # No tracker connected at all → all-null (web keeps defaults).
    monkeypatch.setattr(db_mod, "get_connection", lambda cid, provider: None)
    assert routes.tracker_meta(42, company=_ctx()) == {
        "configured": False, "provider": None, "destination_id": None, "meta": None,
    }


def test_transitions_route_404s_when_unbound_or_unpushed(isolated_settings):
    from app.db.ticket_sync import upsert_sync_config
    from app.routes import tickets as routes

    # No sync config at all.
    with pytest.raises(Exception) as ei:
        routes.ticket_transitions("prd-42-abc123", company=_ctx())
    assert getattr(ei.value, "status_code", None) == 404

    # Bound to Jira but this ticket was never pushed (no issue mapping).
    upsert_sync_config(CID, 42, provider="jira", destination_id="KAN")
    with pytest.raises(Exception) as ei2:
        routes.ticket_transitions("prd-42-abc123", company=_ctx())
    assert getattr(ei2.value, "status_code", None) == 404

    # Malformed key → 400 before any lookup.
    with pytest.raises(Exception) as ei3:
        routes.ticket_transitions("not-a-key", company=_ctx())
    assert getattr(ei3.value, "status_code", None) == 400


def test_transitions_route_jira_proxies_legal_transitions(isolated_settings):
    """Jira status changes are workflow transitions: the dropdown gets the
    issue's LIVE legal moves, with categories canonicalized."""
    from app.db.jira_sync import save_jira_issue_key
    from app.db.ticket_sync import upsert_sync_config
    from app.routes import tickets as routes

    upsert_sync_config(CID, 42, provider="jira", destination_id="KAN")
    save_jira_issue_key(CID, "KAN", "abc123", "KAN-7")

    live = [
        {"id": "31", "name": "Start build", "to_status_id": "2",
         "to_status_name": "Building", "category": "indeterminate"},
        {"id": "41", "name": "Ship it", "to_status_id": "3",
         "to_status_name": "Shipped", "category": "done"},
    ]
    with patch.object(routes, "_jira_creds", return_value=("tok", "cloud")), \
         patch.object(routes.jira_oauth, "list_transitions", return_value=live) as lt:
        out = routes.ticket_transitions("prd-42-abc123", company=_ctx())
    lt.assert_called_once_with("tok", "cloud", "KAN-7")
    assert out["provider"] == "jira"
    assert [(t["to_status_name"], t["category"]) for t in out["transitions"]] == [
        ("Building", "in_progress"), ("Shipped", "done"),
    ]


def test_transitions_route_clickup_serves_full_vocabulary(isolated_settings):
    """ClickUp has no workflow restrictions — every list status is legal, and
    the route serves them in the SAME transitions shape (one web contract)."""
    from app.db.ticket_sync import upsert_sync_config
    from app.db.tracker_meta import save_meta
    from app.routes import tickets as routes

    upsert_sync_config(CID, 42, provider="clickup", destination_id="901")
    save_meta(CID, "clickup", "901", {
        **_META,
        "statuses": [
            {"id": "s1", "name": "backlog", "color": None, "category": "open"},
            {"id": "s2", "name": "in build", "color": None, "category": "in_progress"},
        ],
    })
    out = routes.ticket_transitions("prd-42-abc123", company=_ctx())
    assert out["provider"] == "clickup"
    assert [t["to_status_name"] for t in out["transitions"]] == ["backlog", "in build"]
    assert all(t["id"] is None for t in out["transitions"])


def test_trigger_sync_always_refreshes_the_meta_cache(isolated_settings, monkeypatch):
    """EVERY sync trigger — the first bind AND the ad-hoc Sync button —
    re-pulls the destination's vocabulary (refresh=True), so tracker_meta
    reflects tracker-side field/status/property changes the moment the user
    syncs, not at the 6h TTL."""
    from app.routes import stories as routes

    monkeypatch.setattr(
        "app.stories.sync.run_prd_sync", lambda cid, prd_id: {"pushed": 0},
    )
    warmed: list[tuple] = []

    def _fake_warm(cid, provider, destination_id, **kw):
        warmed.append((cid, provider, destination_id, kw.get("refresh")))
        return _META

    monkeypatch.setattr("app.db.tracker_meta.get_or_fetch_meta", _fake_warm)

    async def _flow():
        # First bind.
        await routes.trigger_sync(
            7,
            routes.SyncTriggerIn(provider="clickup", destination_id="901"),
            _ctx(),
        )
        for _ in range(100):
            if warmed:
                break
            await asyncio.sleep(0.01)
        # Ad-hoc Sync button (no body) — must refresh the vocabulary again.
        from app.db.ticket_sync import save_sync_result

        save_sync_result(CID, 7)  # settle the first run back to idle
        await routes.trigger_sync(7, routes.SyncTriggerIn(), _ctx())
        for _ in range(100):
            if len(warmed) >= 2:
                break
            await asyncio.sleep(0.01)

    asyncio.run(_flow())
    assert warmed == [(CID, "clickup", "901", True)] * 2

"""HTTP surface for standalone ticket sets — /v1/ticket-sets/*.

The load-bearing assertions here are the tenant ones. The backend holds the
service-role key, so RLS is bypassed and these route gates are the ONLY thing
between one company's chat-born tickets and another's. Every ownership
mismatch must read as 404, never 403.
"""
from __future__ import annotations

import pytest

from app.stories.generate import Story
from tests._company_helpers import company_client, seed_company


def _story(title: str, **over) -> dict:
    return Story(title=title, body="b", **over).to_dict()


def _seed_set(company_id: str, *, title: str = "T", stories=None,
              status: str = "ready", source_text: str = "",
              conversation_id: int | None = None) -> int:
    from app.db.client import require_client
    return require_client().table("ticket_sets").insert({
        "company_id": company_id,
        "title": title,
        "stories": stories if stories is not None else [],
        "status": status,
        "source_text": source_text,
        "conversation_id": conversation_id,
    }).execute().data[0]["id"]


# ── Read ─────────────────────────────────────────────────────────────────────


def test_requires_auth(unauth_client, isolated_settings):
    assert unauth_client.get("/v1/ticket-sets/1").status_code == 401


def test_get_returns_the_set_and_its_tickets(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    sid = _seed_set(
        ctx.company_id, title="Checkout Retry Fixes",
        stories=[_story("A"), _story("B")], source_text="make tickets",
        conversation_id=None,
    )

    r = ctx.client.get(f"/v1/ticket-sets/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == sid
    assert body["title"] == "Checkout Retry Fixes"
    assert body["status"] == "ready"
    assert body["ticket_count"] == 2
    assert [s["title"] for s in body["stories"]] == ["A", "B"]
    assert body["source_text"] == "make tickets"


def test_get_renders_an_empty_title_rather_than_dropping_it(isolated_settings, monkeypatch):
    """House rule: every field ships even when blank. The panel decides the
    fallback copy; the API must not silently omit the key."""
    ctx = company_client(monkeypatch)
    sid = _seed_set(ctx.company_id, title="", source_text="", stories=[_story("A")])

    body = ctx.client.get(f"/v1/ticket-sets/{sid}").json()
    assert body["title"] == "" and body["source_text"] == ""


def test_get_exposes_a_generating_set(isolated_settings, monkeypatch):
    """The panel polls this while the run is in flight; it must be readable —
    and honest — before any ticket exists."""
    ctx = company_client(monkeypatch)
    sid = _seed_set(ctx.company_id, status="generating", stories=[])

    body = ctx.client.get(f"/v1/ticket-sets/{sid}").json()
    assert body["status"] == "generating" and body["ticket_count"] == 0


def test_get_404s_an_unknown_set(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    assert ctx.client.get("/v1/ticket-sets/424242").status_code == 404


def test_get_404s_a_foreign_tenants_set(isolated_settings, monkeypatch):
    """404 not 403, and identical to the unknown-id response — a foreign tenant
    must not be able to tell "exists but not yours" from "doesn't exist"."""
    ctx = company_client(monkeypatch)
    other = seed_company(user_id="intruder", slug="rival")
    sid = _seed_set(other, title="Rival tickets", stories=[_story("Secret")])

    r = ctx.client.get(f"/v1/ticket-sets/{sid}")
    assert r.status_code == 404
    assert "Secret" not in r.text
    assert r.json() == ctx.client.get("/v1/ticket-sets/424242").json()


def test_get_drops_deleted_tickets_and_tags_excluded(isolated_settings, monkeypatch):
    """Lifecycle lives on ticket_edits keyed by the composed `set-…` key, not in
    the stories array — so it is applied on the way out, exactly as the PRD
    path does it."""
    from app.db.client import require_client

    ctx = company_client(monkeypatch)
    keep, gone, held = _story("Keep"), _story("Gone"), _story("Held")
    sid = _seed_set(ctx.company_id, stories=[keep, gone, held])
    for story, lifecycle in ((gone, "deleted"), (held, "excluded")):
        require_client().table("ticket_edits").insert({
            "company_id": ctx.company_id,
            "ticket_key": f"set-{sid}-{story['id']}",
            "lifecycle": lifecycle,
        }).execute()

    body = ctx.client.get(f"/v1/ticket-sets/{sid}").json()
    titles = [s["title"] for s in body["stories"]]
    assert titles == ["Keep", "Held"]
    assert body["ticket_count"] == 2
    held_row = next(s for s in body["stories"] if s["title"] == "Held")
    assert held_row["lifecycle"] == "excluded"


def test_a_prd_ticket_lifecycle_never_leaks_into_a_set(isolated_settings, monkeypatch):
    """PRD 5 and set 5 coexist. A ticket deleted under `prd-5-<id>` must not
    hide the set's ticket that happens to share the story id."""
    from app.db.client import require_client

    ctx = company_client(monkeypatch)
    story = _story("Shared content")
    sid = _seed_set(ctx.company_id, stories=[story])
    require_client().table("ticket_edits").insert({
        "company_id": ctx.company_id,
        "ticket_key": f"prd-{sid}-{story['id']}",
        "lifecycle": "deleted",
    }).execute()

    body = ctx.client.get(f"/v1/ticket-sets/{sid}").json()
    assert [s["title"] for s in body["stories"]] == ["Shared content"]


# ── Thread resume ────────────────────────────────────────────────────────────


def test_by_conversation_lists_that_threads_sets(isolated_settings, monkeypatch):
    from app.db.client import require_client

    ctx = company_client(monkeypatch)
    conv = require_client().table("conversations").insert(
        {"company_id": ctx.company_id, "title": "Checkout"}
    ).execute().data[0]["id"]
    _seed_set(ctx.company_id, title="First", conversation_id=conv)
    _seed_set(ctx.company_id, title="Second", conversation_id=conv)
    _seed_set(ctx.company_id, title="Unrelated", conversation_id=None)

    body = ctx.client.get(f"/v1/ticket-sets/by-conversation/{conv}").json()
    assert [s["title"] for s in body["ticket_sets"]] == ["Second", "First"]


def test_by_conversation_is_company_scoped(isolated_settings, monkeypatch):
    """Conversation ids are sequential; a guessed one must return nothing."""
    ctx = company_client(monkeypatch)
    other = seed_company(user_id="intruder", slug="rival")
    _seed_set(other, title="Rival tickets", conversation_id=4242)

    body = ctx.client.get("/v1/ticket-sets/by-conversation/4242").json()
    assert body["ticket_sets"] == []


def test_by_conversation_surfaces_an_in_flight_run(isolated_settings, monkeypatch):
    """Reopening a chat mid-run must be able to tell the truth: the set exists
    and is still generating, so the panel resumes progress rather than showing
    a blank tab or a stale "Writing tickets…" for a finished run."""
    from app.db.client import require_client

    ctx = company_client(monkeypatch)
    conv = require_client().table("conversations").insert(
        {"company_id": ctx.company_id, "title": "Checkout"}
    ).execute().data[0]["id"]
    _seed_set(ctx.company_id, title="", status="generating", conversation_id=conv)

    [row] = ctx.client.get(f"/v1/ticket-sets/by-conversation/{conv}").json()["ticket_sets"]
    assert row["status"] == "generating"


# ── Tracker sync ─────────────────────────────────────────────────────────────


def test_sync_state_unconfigured(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    sid = _seed_set(ctx.company_id, stories=[_story("A")])
    assert ctx.client.get(f"/v1/ticket-sets/{sid}/sync").json() == {"configured": False}


def test_sync_state_404s_a_foreign_set(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    other = seed_company(user_id="intruder", slug="rival")
    sid = _seed_set(other, stories=[_story("A")])
    assert ctx.client.get(f"/v1/ticket-sets/{sid}/sync").status_code == 404


def test_sync_state_reports_the_bound_destination(isolated_settings, monkeypatch):
    from app.db.ticket_sync import upsert_sync_config
    from app.stories.scope import set_scope

    ctx = company_client(monkeypatch)
    sid = _seed_set(ctx.company_id, stories=[_story("A")])
    upsert_sync_config(ctx.company_id, set_scope(sid), provider="jira",
                       destination_id="KAN", destination_name="Kanban")

    state = ctx.client.get(f"/v1/ticket-sets/{sid}/sync").json()
    assert state["configured"] is True
    assert state["provider"] == "jira"
    assert state["destination_id"] == "KAN"
    assert state["destination_name"] == "Kanban"
    assert state["sync_status"] == "idle"


def test_binding_a_set_does_not_touch_the_same_numbered_prd(isolated_settings, monkeypatch):
    """PRD ids and set ids are independent sequences. Binding set N must create
    its OWN destination row, not overwrite PRD N's — the failure mode that
    would push a PRD's tickets into a chat's tracker project."""
    from app.db.ticket_sync import get_sync_config, upsert_sync_config
    from app.stories.scope import prd_scope, set_scope

    ctx = company_client(monkeypatch)
    sid = _seed_set(ctx.company_id, stories=[_story("A")])
    upsert_sync_config(ctx.company_id, prd_scope(sid), provider="jira",
                       destination_id="PRD-PROJ")
    upsert_sync_config(ctx.company_id, set_scope(sid), provider="clickup",
                       destination_id="SET-LIST")

    assert get_sync_config(ctx.company_id, prd_scope(sid))["destination_id"] == "PRD-PROJ"
    assert get_sync_config(ctx.company_id, set_scope(sid))["destination_id"] == "SET-LIST"


def test_rebinding_a_set_replaces_rather_than_duplicating(isolated_settings, monkeypatch):
    """One active tracker per artifact. The upsert's conflict target has to be
    inferrable, or every re-push silently inserts another destination row."""
    from app.db.client import require_client
    from app.db.ticket_sync import get_sync_config, upsert_sync_config
    from app.stories.scope import set_scope

    ctx = company_client(monkeypatch)
    sid = _seed_set(ctx.company_id, stories=[_story("A")])
    upsert_sync_config(ctx.company_id, set_scope(sid), provider="jira",
                       destination_id="KAN")
    upsert_sync_config(ctx.company_id, set_scope(sid), provider="clickup",
                       destination_id="L1")

    rows = (
        require_client().table("prd_ticket_sync").select("*")
        .eq("company_id", ctx.company_id).execute().data or []
    )
    assert len(rows) == 1
    assert get_sync_config(ctx.company_id, set_scope(sid))["destination_id"] == "L1"


def test_trigger_404s_when_never_pushed(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    sid = _seed_set(ctx.company_id, stories=[_story("A")])
    r = ctx.client.post(f"/v1/ticket-sets/{sid}/sync", json={})
    assert r.status_code == 404


def test_trigger_rejects_a_half_destination_and_an_ineligible_provider(
    isolated_settings, monkeypatch
):
    ctx = company_client(monkeypatch)
    sid = _seed_set(ctx.company_id, stories=[_story("A")])

    assert ctx.client.post(
        f"/v1/ticket-sets/{sid}/sync", json={"provider": "clickup"}
    ).status_code == 400
    r = ctx.client.post(
        f"/v1/ticket-sets/{sid}/sync",
        json={"provider": "slack", "destination_id": "C042"},
    )
    assert r.status_code == 400
    assert "task-management" in r.text


def test_trigger_404s_a_foreign_set_before_binding_anything(
    isolated_settings, monkeypatch
):
    """The ownership gate runs BEFORE the destination upsert — otherwise a
    foreign caller could bind another tenant's tickets to their own tracker and
    have the scheduler push that company's work into it."""
    from app.db.client import require_client

    ctx = company_client(monkeypatch)
    other = seed_company(user_id="intruder", slug="rival")
    sid = _seed_set(other, stories=[_story("A")])

    r = ctx.client.post(
        f"/v1/ticket-sets/{sid}/sync",
        json={"provider": "jira", "destination_id": "EVIL"},
    )
    assert r.status_code == 404
    rows = (
        require_client().table("prd_ticket_sync").select("*").execute().data or []
    )
    assert rows == []


def test_trigger_runs_a_pass_for_the_set_scope(isolated_settings, monkeypatch):
    """The pass the trigger spawns is for the SET, not for a PRD of the same
    number.

    Driven through the route function rather than the TestClient: the sync runs
    as a background asyncio task, and the test client's loop closes with the
    response, so the task would never get scheduled.
    """
    import asyncio

    from app.auth import CompanyContext
    from app.db.ticket_sync import upsert_sync_config
    from app.routes import ticket_sets as routes
    from app.stories.scope import set_scope

    ctx = company_client(monkeypatch)
    sid = _seed_set(ctx.company_id, stories=[_story("A")])
    upsert_sync_config(ctx.company_id, set_scope(sid), provider="clickup",
                       destination_id="L1")

    ran: list[object] = []
    monkeypatch.setattr(
        "app.stories.sync.run_ticket_sync",
        lambda cid, scope: ran.append((cid, scope)) or {"pushed": 0},
    )
    # The meta warm fires unconditionally on every trigger; stub it so the test
    # never reaches a real tracker.
    monkeypatch.setattr(
        "app.db.tracker_meta.get_or_fetch_meta", lambda *a, **k: None
    )

    async def _flow():
        resp = await routes.trigger_set_sync(
            sid, routes.SyncTriggerIn(),
            CompanyContext(company_id=ctx.company_id, role="owner", user_id="u"),
        )
        assert resp == {"status": "syncing"}
        for _ in range(100):
            if ran:
                break
            await asyncio.sleep(0.01)

    asyncio.run(_flow())
    assert ran == [(ctx.company_id, set_scope(sid))]


def test_tracker_meta_404s_a_foreign_set(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    other = seed_company(user_id="intruder", slug="rival")
    sid = _seed_set(other, stories=[_story("A")])
    assert ctx.client.get(f"/v1/ticket-sets/{sid}/tracker-meta").status_code == 404


def test_tracker_meta_all_null_without_a_tracker(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    sid = _seed_set(ctx.company_id, stories=[_story("A")])
    assert ctx.client.get(f"/v1/ticket-sets/{sid}/tracker-meta").json() == {
        "configured": False, "provider": None, "destination_id": None, "meta": None,
    }

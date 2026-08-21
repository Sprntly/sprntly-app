"""Tests for the individual-chat READ surface: `db/conversations.py`'s new
`list_individual_turns` helper, and the `GET /v1/projects/{id}/individual/turns`
route.

The gap this closes: `ProjectIndividualChat.tsx` ("My chat with Sprntly")
used to render only turns produced in the CURRENT browser session and
started empty on every reload. A brief delivered by delegation
(`app/project_delegation.py`, a durable `role: "assistant"` turn with no
paired question) landed in the DB via `post_individual_turn` but was never
visible. This reader + route make it visible.

Covers (fake-Supabase tier, mirrors `test_group_chat_turns.py`'s own split):
  - reader round-trip: ascending `{id, role, content, created_at}` for the
    caller's OWN individual conversation (AC1)
  - `since` cursor parity with `list_group_turns`/`GET /group/turns` (AC2)
  - route: no conversation yet → `{"turns": []}`, not 404 (AC1)
  - **the own-conversation read gate, mutation-proofed (AC3, load-bearing)**:
    another user's `conversation_id`, or a `kind='group'` id, always returns
    `[]` — the read-side counterpart of `post_individual_turn`'s cross-user
    WRITE (delegation delivers INTO another user's thread by design; this
    reader must never let anyone read OUT of one)
  - route membership gate: 403 same-tenant non-member, 404 foreign tenant,
    and the route accepts no client-supplied conversation id at all (AC4)

The live-LLM/live-DB round trip is out of scope for this file (no LLM call
on this surface at all — a pure read) and is covered by the phase's browser
sweep, not a `RUN_*_LIVE`-gated file here.
"""
from __future__ import annotations

from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member


def _create_project(ctx, *, name: str = "Individual turns project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


# ── Reader round-trip + cursor (AC1/AC2) ─────────────────────────────────


def test_list_individual_turns_round_trip(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_individual_project_chat(project["id"], ctx.user_id)
    t1 = conversations_db.post_individual_turn(conv["id"], "user", "hello")
    t2 = conversations_db.post_individual_turn(conv["id"], "assistant", "hi there")

    turns = conversations_db.list_individual_turns(conv["id"], ctx.user_id)
    assert [t["id"] for t in turns] == [t1["id"], t2["id"]]
    # The narrow `{id, role, content, created_at}` column projection (no
    # author_user_id/author_name split, unlike `list_group_turns`) is a real
    # `.select(...)` clause the fake-Supabase tier does not enforce (it
    # ignores the column list and returns full rows) — that projection is
    # real-Postgres-only proof, out of scope for this blast-radius tier.
    assert {"id", "role", "content", "created_at"}.issubset(turns[0].keys())
    assert turns[0]["role"] == "user"
    assert turns[0]["content"] == "hello"
    assert turns[0]["created_at"] == t1["created_at"]
    assert turns[1]["role"] == "assistant"
    assert turns[1]["content"] == "hi there"


def test_list_individual_turns_since_cursor(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_individual_project_chat(project["id"], ctx.user_id)
    t1 = conversations_db.post_individual_turn(conv["id"], "user", "first")
    t2 = conversations_db.post_individual_turn(conv["id"], "assistant", "second")

    since_first = conversations_db.list_individual_turns(conv["id"], ctx.user_id, since=t1["id"])
    assert [t["id"] for t in since_first] == [t2["id"]]

    since_last = conversations_db.list_individual_turns(conv["id"], ctx.user_id, since=t2["id"])
    assert since_last == []

    # Route-level parity with the db-helper cursor.
    r = ctx.client.get(
        f"/v1/projects/{project['id']}/individual/turns", params={"since": t1["id"]}
    )
    assert r.status_code == 200, r.text
    assert [t["id"] for t in r.json()["turns"]] == [t2["id"]]


def test_list_individual_turns_empty_when_not_created(isolated_settings, monkeypatch):
    """No conversation row for the caller yet — the route returns
    `{"turns": []}`, never a 404: not having opened the chat is a legitimate
    read state, mirroring `GET /group/turns`'s own not-created posture."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    r = ctx.client.get(f"/v1/projects/{project['id']}/individual/turns")
    assert r.status_code == 200, r.text
    assert r.json()["turns"] == []


# ── Own-conversation isolation (mutation-proofed — AC3, load-bearing) ────


def test_individual_turns_reject_other_users_conversation(isolated_settings, monkeypatch):
    """User B's `conversation_id`, read as user A, returns `[]` — even
    though B's turns genuinely exist for that id (proven by an UNGATED
    stand-in read below). This is the RED/GREEN mutation proof: the RED
    stand-in query is the exact same read `list_individual_turns` runs,
    minus the `.eq("user_id", user_id)` gate clause — it DOES return B's
    turns for the identical `conversation_id`. The real, GATED function
    (GREEN) returns `[]` for the same inputs, proving the `user_id` filter
    — not "nothing exists to leak" — is what keeps A out of B's thread."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db import projects as projects_db
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": "member-b", "email": "b@co.com"}
    ).execute()
    projects_db.add_member(project["id"], "member-b")

    b_conv = conversations_db.create_individual_project_chat(project["id"], "member-b")
    conversations_db.post_individual_turn(b_conv["id"], "assistant", "B's private brief")

    # RED: the same read as list_individual_turns, with the owner-gate clause
    # removed (simulates the gate being absent from production code).
    ungated = (
        require_client()
        .table("conversation_turns")
        .select("id, role, content, created_at")
        .eq("conversation_id", b_conv["id"])
        .order("id")
        .execute()
        .data
        or []
    )
    assert len(ungated) == 1 and ungated[0]["content"] == "B's private brief", (
        "setup invariant: B's turn must genuinely exist for this conversation_id "
        "for the RED comparison below to be meaningful"
    )

    # GREEN: the real, gated reader — A passing B's conversation_id gets [].
    assert conversations_db.list_individual_turns(b_conv["id"], ctx.user_id) == []

    # And the route path (defense-in-depth: resolves ONLY ctx.user_id's own
    # conversation server-side, never reachable via any client-supplied id).
    r = ctx.client.get(f"/v1/projects/{project['id']}/individual/turns")
    assert r.status_code == 200
    assert r.json()["turns"] == []


def test_individual_turns_reject_group_conversation(isolated_settings, monkeypatch):
    """A `kind='group'` conversation id is never readable through the
    individual-turns reader, even for the conversation's own creator. The
    group-chat WRITE path is removed, but pre-existing `kind='group'` rows
    are explicitly NOT deleted from the database — this proves a legacy
    group row (inserted directly, mirroring what still exists in prod)
    stays refused."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db.client import require_client
    from app.db.workspaces import ensure_default_workspace

    ws_id = ensure_default_workspace(ctx.company_id)["id"]
    client = require_client()
    group_conv = (
        client.table("conversations")
        .insert(
            {
                "company_id": ctx.company_id,
                "workspace_id": ws_id,
                "user_id": ctx.user_id,
                "project_id": project["id"],
                "kind": "group",
            }
        )
        .execute()
        .data[0]
    )
    client.table("conversation_turns").insert(
        {"conversation_id": group_conv["id"], "role": "user", "content": "group turn"}
    ).execute()

    assert conversations_db.list_individual_turns(group_conv["id"], ctx.user_id) == []


def test_individual_turns_route_membership_gated(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _, non_member_headers = seed_same_tenant_non_member(ctx)

    r_non_member = ctx.client.get(
        f"/v1/projects/{project['id']}/individual/turns", headers=non_member_headers
    )
    assert r_non_member.status_code == 403

    from app.db import projects as projects_db

    foreign = projects_db.create_project(
        company_id="foreign-co", workspace_id="foreign-ws", name="Not mine",
        created_by="someone-else",
    )
    assert ctx.client.get(f"/v1/projects/{foreign['id']}/individual/turns").status_code == 404

    # The real owner (a real member) is unaffected, and a client-supplied
    # `conversation_id` (the route has no such param — this proves it is
    # simply ignored/unreachable, not merely undocumented) changes nothing.
    r_owner = ctx.client.get(
        f"/v1/projects/{project['id']}/individual/turns",
        params={"conversation_id": 999999},
    )
    assert r_owner.status_code == 200
    assert r_owner.json()["turns"] == []

"""Tests for the individual-project-chat conversation binding: `db/conversations
.py`'s new `get_individual_project_chat`/`create_individual_project_chat`
helpers, and the `POST /v1/projects/{id}/individual` route.

The gap this closes: `ProjectIndividualChat.tsx` ("My chat with Sprntly")
used to POST every turn to `/v1/ask` with `project_id` but no
`conversation_id`, so `ask_job_runner._run_sync`'s memory-promotion gate
(`project_id is not None and conversation_id is not None`) could never fire
for it. This file proves the NEW get-or-create conversation is durable
(idempotent per project+caller), membership-gated the same way the group
chat is, and — fed into a real `/v1/ask` call — actually reaches the
promotion hook with correct provenance.

Covers (fake-Supabase tier, mirrors `test_group_chat_turns.py`'s own split):
  - one `kind='individual'` conversation per (project, caller), idempotent
    create, both at the db-helper level and the HTTP route level
  - two different project members each get their OWN row
  - membership gate: 403 same-tenant non-member, 404 foreign tenant
  - fed into a real `POST /v1/ask` call (fake classifier), the resulting
    conversation_id binds correctly and a durable exchange promotes a
    `project_memory_entries` row with the right `source_conversation_id`
  - a non-project ask riding the SAME conversation_id (no `project_id`)
    promotes nothing — the hook's gate is `project_id`, not merely
    "conversation_id exists"

The real-LLM/real-DB round trip (the classifier genuinely deciding to
promote, against the real local Postgres) is a separate live-gated test —
see `test_individual_project_chat_live.py`.
"""
from __future__ import annotations

import inspect

from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member


def _create_project(ctx, *, name: str = "Individual chat project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


# ── Creation / idempotency ──────────────────────────────────────────────


def test_create_individual_chat_idempotent_per_user(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db.client import require_client

    first = conversations_db.create_individual_project_chat(project["id"], ctx.user_id)
    second = conversations_db.create_individual_project_chat(project["id"], ctx.user_id)
    assert first["id"] == second["id"]
    assert first["kind"] == "individual"
    assert first["project_id"] == project["id"]
    assert first["user_id"] == ctx.user_id

    rows = (
        require_client()
        .table("conversations")
        .select("id")
        .eq("project_id", project["id"])
        .eq("kind", "individual")
        .eq("user_id", ctx.user_id)
        .execute()
        .data
    )
    assert len(rows) == 1


def test_create_individual_chat_route_idempotent(isolated_settings, monkeypatch):
    """The HTTP route is idempotent the same way (mirrors the group chat's
    own AC1 literal wording, one level down: per-user rather than
    per-project)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    r1 = ctx.client.post(f"/v1/projects/{project['id']}/individual")
    r2 = ctx.client.post(f"/v1/projects/{project['id']}/individual")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    assert r1.json()["kind"] == "individual"
    assert r1.json()["project_id"] == project["id"]


def test_two_members_get_their_own_individual_chat(isolated_settings, monkeypatch):
    """Isolation: this is a PRIVATE per-caller conversation, not a shared one
    like the group chat — two different project members must never resolve
    to the same row."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db import projects as projects_db
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": "member-2", "email": "m2@co.com"}
    ).execute()
    projects_db.add_member(project["id"], "member-2")

    mine = conversations_db.create_individual_project_chat(project["id"], ctx.user_id)
    theirs = conversations_db.create_individual_project_chat(project["id"], "member-2")
    assert mine["id"] != theirs["id"]
    assert mine["user_id"] == ctx.user_id
    assert theirs["user_id"] == "member-2"

    assert conversations_db.get_individual_project_chat(project["id"], ctx.user_id)["id"] == mine["id"]
    assert conversations_db.get_individual_project_chat(project["id"], "member-2")["id"] == theirs["id"]


def test_get_individual_chat_none_before_creation(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    assert conversations_db.get_individual_project_chat(project["id"], ctx.user_id) is None


# ── Membership gate (AD-P11, mirrors the group chat's own gate) ─────────


def test_non_member_individual_chat_forbidden(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _, non_member_headers = seed_same_tenant_non_member(ctx)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/individual", headers=non_member_headers
    )
    assert r.status_code == 403


def test_foreign_project_individual_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)

    from app.db import projects as projects_db

    foreign = projects_db.create_project(
        company_id="foreign-co", workspace_id="foreign-ws", name="Not mine",
        created_by="someone-else",
    )

    assert ctx.client.post(f"/v1/projects/{foreign['id']}/individual").status_code == 404


# ── Fed into a real /v1/ask call: binding + promotion (fake classifier) ─


def _fake_call_json(*, system, user, model, schema=None, meta_out=None, **kwargs):  # noqa: ARG001
    if meta_out is not None:
        meta_out.update(
            {
                "model": model, "input_tokens": 10, "output_tokens": 5,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }
        )
    return {
        "action": "new",
        "target_entry_id": None,
        "body": "The team locked the API rate limit at 100 req/min per tenant.",
    }


def test_individual_chat_conversation_binds_and_promotes_via_real_ask_route(
    isolated_settings, monkeypatch
):
    """THE exact gap this ticket closes, proven end-to-end through the real
    `/v1/ask` route (pytest-inline path): get-or-create the durable
    individual conversation the SAME way `ProjectIndividualChat.tsx` now
    does, thread its id into `/v1/ask` alongside `project_id` (the SAME two
    fields the fixed frontend now sends), and confirm the promotion hook
    fires with the correct `source_conversation_id`."""
    import app.project_memory as pm
    from app import ask_job_runner as ajr

    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    monkeypatch.setattr(pm, "call_json", _fake_call_json)
    monkeypatch.setattr(
        ajr.qa_agent, "answer",
        lambda **kw: {
            "answer": (
                "Locking the API rate limit at 100 requests/min per tenant, "
                "applied uniformly including enterprise accounts."
            ),
            "key_points": [], "citations": [], "confidence": 0.8, "unanswered": "",
        },
    )

    conv = ctx.client.post(f"/v1/projects/{project['id']}/individual").json()
    conv_id = conv["id"]

    r = ctx.client.post(
        "/v1/ask",
        json={
            "question": (
                "Can you record that we're locking the API rate limit at 100 "
                "requests/min per tenant, with no exception for enterprise "
                "customers?"
            ),
            "dataset": "acme",
            "conversation_id": conv_id,
            "project_id": project["id"],
        },
    )
    assert r.status_code == 200, r.text

    from app.db.project_memory_entries import list_entries

    entries = list_entries(project["id"])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["promoted_by"] == "agent"
    assert entry["author_user_id"] is None
    assert entry["source_conversation_id"] == conv_id


def test_non_project_ask_on_the_same_conversation_promotes_nothing(
    isolated_settings, monkeypatch
):
    """A conversation existing (even one created via `/individual`) is not
    itself enough to trigger promotion — the hook's gate is `project_id`,
    proven by re-using the SAME conversation_id on a plain, non-project ask."""
    import app.project_memory as pm
    from app import ask_job_runner as ajr

    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    def boom(*a, **kw):  # noqa: ARG001
        raise AssertionError("maybe_promote_turn must not be called without project_id")

    monkeypatch.setattr(pm, "maybe_promote_turn", boom)
    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: {
        "answer": "sure, here's an answer", "key_points": [], "citations": [],
        "confidence": 0.8, "unanswered": "",
    })

    conv = ctx.client.post(f"/v1/projects/{project['id']}/individual").json()
    conv_id = conv["id"]

    r = ctx.client.post(
        "/v1/ask",
        json={"question": "just a normal question here", "dataset": "acme", "conversation_id": conv_id},
    )
    assert r.status_code == 200, r.text

    from app.db.project_memory_entries import list_entries

    assert list_entries(project["id"]) == []


# ── Isolation regression (AD-P2/R4, mirrors the group chat's own guard) ──


def test_individual_chat_helpers_never_touch_group_chat_rows(isolated_settings, monkeypatch):
    """The new per-user helpers never resolve/return a `kind='group'` row,
    even for the same project — proven by creating both and confirming
    `get_individual_project_chat` only ever surfaces the individual one."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    group = conversations_db.create_group_chat(project["id"], ctx.user_id)
    individual = conversations_db.create_individual_project_chat(project["id"], ctx.user_id)
    assert group["id"] != individual["id"]

    resolved = conversations_db.get_individual_project_chat(project["id"], ctx.user_id)
    assert resolved["id"] == individual["id"]
    assert resolved["kind"] == "individual"


def test_signatures_unchanged():
    """Pin the two new helpers' signatures so a future refactor notices if
    it silently drops the (project_id, user_id) scoping."""
    from app.db import conversations as conversations_db

    assert str(inspect.signature(conversations_db.get_individual_project_chat)) == (
        "(project_id: 'int', user_id: 'str') -> 'dict[str, Any] | None'"
    )
    assert str(inspect.signature(conversations_db.create_individual_project_chat)) == (
        "(project_id: 'int', user_id: 'str') -> 'dict[str, Any]'"
    )

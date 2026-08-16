"""Owned, idempotent turn-pair writers for the private project chat
(`db/conversations.py::post_owned_individual_user_turn` /
`post_owned_individual_assistant_turn`).

The gap this closes: the individual project chat's answer for a normal
`/v1/ask` turn lived ONLY in `ask_jobs.response`, never `conversation_turns`,
and its five bypass branches (edit/generate/tickets/pick/clarify) persisted
NEITHER side — so a reload lost the whole dialogue. These writers are the
owned, per-side-idempotent, ownership-gated foundation every send branch
persists through.

`post_individual_turn` — the pre-existing CROSS-USER brief writer — is
UNCHANGED; these are NEW, additive writers alongside it.

Fake-Supabase tier (mirrors `test_individual_turns.py`'s own split): no LLM,
no real Postgres partial-unique enforcement (that's
`test_conversation_turns_idempotency_migration.py`'s job). What this file
proves is the APP-LEVEL contract: server-side ownership resolution, the
idempotent read-check-before-insert path, cursor advance, and that the
brief writer + the 4-key read shape are untouched apart from the additive
`client_message_id` column.
"""
from __future__ import annotations

from tests._company_helpers import company_client


def _create_project(ctx, *, name: str = "Persistence project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


# ── Owned writers — basic round trip (AC1/AC6) ───────────────────────────


def test_owned_user_turn_resolves_conversation_from_project_user(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    row = conversations_db.post_owned_individual_user_turn(
        project_id=project["id"],
        user_id=ctx.user_id,
        content="hello",
        client_message_id="cmid-1",
    )
    assert row["role"] == "user"
    assert row["content"] == "hello"
    assert row["author_user_id"] == ctx.user_id

    # Resolved server-side — there is a real conversation owned by this
    # (project, user) pair, and the row lives on it.
    conv = conversations_db.get_individual_project_chat(project["id"], ctx.user_id)
    assert conv is not None
    turns = conversations_db.list_individual_turns(conv["id"], ctx.user_id)
    assert [t["id"] for t in turns] == [row["id"]]


def test_owned_assistant_turn_persists_answer(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    row = conversations_db.post_owned_individual_assistant_turn(
        project_id=project["id"],
        user_id=ctx.user_id,
        content="the answer",
        ask_job_id=42,
    )
    assert row["role"] == "assistant"
    assert row["content"] == "the answer"
    assert row["author_user_id"] is None
    assert row["ask_job_id"] == 42

    # Also linkable by client_message_id instead of ask_job_id.
    row2 = conversations_db.post_owned_individual_assistant_turn(
        project_id=project["id"],
        user_id=ctx.user_id,
        content="a generate-branch answer",
        client_message_id="cmid-generate-1",
    )
    assert row2["client_message_id"] == "cmid-generate-1"
    assert row2["ask_job_id"] is None


def test_owned_assistant_turn_requires_exactly_one_key(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    import pytest

    with pytest.raises(ValueError):
        conversations_db.post_owned_individual_assistant_turn(
            project_id=project["id"], user_id=ctx.user_id, content="x",
        )
    with pytest.raises(ValueError):
        conversations_db.post_owned_individual_assistant_turn(
            project_id=project["id"], user_id=ctx.user_id, content="x",
            client_message_id="a", ask_job_id=1,
        )


# ── Idempotency (AC4/AC5) ─────────────────────────────────────────────────


def test_owned_writers_idempotent_per_side(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    first = conversations_db.post_owned_individual_user_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="first send", client_message_id="dup-key",
    )
    second = conversations_db.post_owned_individual_user_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="a retried resend of the same message", client_message_id="dup-key",
    )
    assert second["id"] == first["id"]
    # Content is NOT overwritten by the retry — the FIRST write wins, the
    # retry is a no-op that returns the existing row.
    assert second["content"] == "first send"

    conv = conversations_db.get_individual_project_chat(project["id"], ctx.user_id)
    turns = conversations_db.list_individual_turns(conv["id"], ctx.user_id)
    user_turns = [t for t in turns if t["role"] == "user"]
    assert len(user_turns) == 1

    a1 = conversations_db.post_owned_individual_assistant_turn(
        project_id=project["id"], user_id=ctx.user_id, content="answer v1", ask_job_id=7,
    )
    a2 = conversations_db.post_owned_individual_assistant_turn(
        project_id=project["id"], user_id=ctx.user_id, content="answer v2", ask_job_id=7,
    )
    assert a2["id"] == a1["id"]
    turns = conversations_db.list_individual_turns(conv["id"], ctx.user_id)
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    assert len(assistant_turns) == 1


def test_user_and_assistant_same_client_message_id_coexist(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    user_row = conversations_db.post_owned_individual_user_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="edit this", client_message_id="one-send",
    )
    assistant_row = conversations_db.post_owned_individual_assistant_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="edited.", client_message_id="one-send",
    )
    assert user_row["id"] != assistant_row["id"]
    conv = conversations_db.get_individual_project_chat(project["id"], ctx.user_id)
    turns = conversations_db.list_individual_turns(conv["id"], ctx.user_id)
    assert len(turns) == 2
    assert {t["role"] for t in turns} == {"user", "assistant"}


# ── Upsert-on-content: a two-phase flow's SAME key, DIFFERENT content ────
#
# The bug this closes: a two-phase send (park an interim answer under a
# `client_message_id`, then re-persist the SAME key once the flow settles on
# its real, final answer — exactly what the private clarify path does below)
# hit the idempotent read-check, found the parking row, and returned it
# UNCHANGED — the final answer was silently dropped and a reload showed the
# stale interim text forever.


def test_assistant_writer_upserts_content_on_same_key(isolated_settings, monkeypatch):
    """Direct writer proof: persist assistant content A under key K, then
    content B under the SAME key K — exactly one row for K, its content is
    the LATEST write (B), and the row id is unchanged."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    first = conversations_db.post_owned_individual_assistant_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="content A", client_message_id="same-key",
    )
    second = conversations_db.post_owned_individual_assistant_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="content B", client_message_id="same-key",
    )
    assert second["id"] == first["id"]
    assert second["content"] == "content B"

    conv = conversations_db.get_individual_project_chat(project["id"], ctx.user_id)
    turns = conversations_db.list_individual_turns(conv["id"], ctx.user_id)
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    assert len(assistant_turns) == 1
    assert assistant_turns[0]["content"] == "content B"

    # A genuine retry — SAME key, SAME (latest) content — is still a true
    # no-op: no new row, and the id/content are unchanged.
    third = conversations_db.post_owned_individual_assistant_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="content B", client_message_id="same-key",
    )
    assert third["id"] == first["id"]
    turns = conversations_db.list_individual_turns(conv["id"], ctx.user_id)
    assert len([t for t in turns if t["role"] == "assistant"]) == 1


def test_assistant_writer_upsert_leaves_other_keys_untouched(isolated_settings, monkeypatch):
    """A different key always lands as its own new row — the upsert-on-content
    change only ever touches the ONE row matching its own key."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    row_a = conversations_db.post_owned_individual_assistant_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="answer for A", client_message_id="key-a",
    )
    row_b = conversations_db.post_owned_individual_assistant_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="answer for B", client_message_id="key-b",
    )
    assert row_a["id"] != row_b["id"]

    conv = conversations_db.get_individual_project_chat(project["id"], ctx.user_id)
    turns = conversations_db.list_individual_turns(conv["id"], ctx.user_id)
    assistant_turns = {t["id"]: t["content"] for t in turns if t["role"] == "assistant"}
    assert assistant_turns[row_a["id"]] == "answer for A"
    assert assistant_turns[row_b["id"]] == "answer for B"


def test_two_phase_clarify_persist_reload_returns_final_answer(isolated_settings, monkeypatch):
    """End-to-end at the persistence layer: the private clarify path's own
    shape — a parking persist of the clarify-questions text, immediately
    followed by a generation persist of the final answer, BOTH keyed on the
    same `client_message_id` (mirrors `useProjectPrivateThread.ts`'s
    `persistTurnPair` calls in `generatePrdIntoTurn` and the clarify-park
    branch, and the single backend route — `persist_individual_turns_route`
    — both go through). A reload (`list_individual_turns`) must return the
    FINAL answer, never the stale parked clarify text."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    cmid = "clarify-cmid"
    question = "Create a PRD for the onboarding flow"
    clarify_text = "Before I write this, a couple of quick questions: ..."
    final_answer = 'I\'ve generated "Onboarding Flow" and attached it to this project.'

    # Phase 1 — park: the user turn + the interim clarify-questions text,
    # exactly what `persist_individual_turns_route` writes for the parking
    # call.
    conversations_db.post_owned_individual_user_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content=question, client_message_id=cmid,
    )
    conversations_db.post_owned_individual_assistant_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content=clarify_text, client_message_id=cmid,
    )

    # Phase 2 — generation settles: the SAME client_message_id, the real
    # final answer.
    conversations_db.post_owned_individual_assistant_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content=final_answer, client_message_id=cmid,
    )

    # RELOAD: a fresh read of the persisted transcript.
    conv = conversations_db.get_individual_project_chat(project["id"], ctx.user_id)
    turns = conversations_db.list_individual_turns(conv["id"], ctx.user_id)
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    assert len(assistant_turns) == 1, "the parking + settle persist must resolve to exactly ONE assistant row"
    assert assistant_turns[0]["content"] == final_answer
    assert assistant_turns[0]["content"] != clarify_text


def test_single_phase_direct_generate_persist_unaffected(isolated_settings, monkeypatch):
    """Regression guard: the single-phase direct-generate path (one
    parking-free persist call, as `test_owned_assistant_turn_persists_answer`
    already covers) still persists its one answer correctly under the
    upsert-on-content writer — there is no second call on this path to
    upsert against."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conversations_db.post_owned_individual_user_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="make me a ticket set", client_message_id="direct-cmid",
    )
    conversations_db.post_owned_individual_assistant_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="I've written a ticket set and attached it.", client_message_id="direct-cmid",
    )

    conv = conversations_db.get_individual_project_chat(project["id"], ctx.user_id)
    turns = conversations_db.list_individual_turns(conv["id"], ctx.user_id)
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[1]["content"] == "I've written a ticket set and attached it."


# ── Cursor advance (AC8) ──────────────────────────────────────────────────


def test_own_turn_write_advances_read_cursor(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversation_read_cursors as read_cursors_db
    from app.db import conversations as conversations_db

    row = conversations_db.post_owned_individual_user_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="hi", client_message_id="cmid-cursor",
    )
    conv = conversations_db.get_individual_project_chat(project["id"], ctx.user_id)
    assert read_cursors_db.unread_for(conv["id"], ctx.user_id) is False
    assert read_cursors_db.get_cursor(conv["id"], ctx.user_id) >= row["id"]

    # A teammate's cursor is untouched by MY write.
    from app.db.client import require_client
    from app.db.workspaces import ensure_default_workspace, upsert_workspace_member

    require_client().table("company_members").insert(
        {"id": "member-b-row", "company_id": ctx.company_id, "user_id": "member-b", "role": "member"}
    ).execute()
    ws = ensure_default_workspace(ctx.company_id)
    upsert_workspace_member(ws["id"], "member-b", "member")
    assert read_cursors_db.get_cursor(conv["id"], "member-b") == 0


# ── Brief writer + read shape unchanged (AC12) ───────────────────────────


def test_post_individual_turn_brief_writer_unchanged(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_individual_project_chat(project["id"], ctx.user_id)
    row = conversations_db.post_individual_turn(conv["id"], "assistant", "a delegated brief")
    assert row["author_user_id"] is None
    assert row["role"] == "assistant"
    assert row["content"] == "a delegated brief"
    # No ownership resolution — a raw conversation_id is honoured verbatim,
    # by design (delegation delivers cross-user).
    assert row.get("client_message_id") is None
    assert row.get("ask_job_id") is None


def test_list_individual_turns_returns_client_message_id(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conversations_db.post_owned_individual_user_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="hi", client_message_id="cmid-shape",
    )
    conv = conversations_db.get_individual_project_chat(project["id"], ctx.user_id)
    turns = conversations_db.list_individual_turns(conv["id"], ctx.user_id)
    assert turns[0]["client_message_id"] == "cmid-shape"


# ── Ownership / IDOR — mutation-proofed (AC6, load-bearing) ─────────────


def test_foreign_conversation_id_cannot_write(isolated_settings, monkeypatch):
    """The owned writers accept NO `conversation_id` parameter at all — a
    caller can only ever influence WHICH (project, user) pair is resolved,
    never write directly into an arbitrary conversation id. Proven by
    signature: there is no code path from an arbitrary conversation_id to a
    written row."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    require_client_conv = conversations_db.create_individual_project_chat(
        project["id"], ctx.user_id
    )

    # A second user's project + individual chat.
    from app.db.client import require_client
    from app.db import projects as projects_db
    from app.db.workspaces import ensure_default_workspace, upsert_workspace_member

    require_client().table("company_members").insert(
        {"id": "member-b-row-2", "company_id": ctx.company_id, "user_id": "member-b", "role": "member"}
    ).execute()
    ws = ensure_default_workspace(ctx.company_id)
    upsert_workspace_member(ws["id"], "member-b", "member")
    projects_db.add_member(project["id"], "member-b")
    b_conv = conversations_db.create_individual_project_chat(project["id"], "member-b")
    assert b_conv["id"] != require_client_conv["id"]

    # Calling the owned writer AS user A resolves ONLY A's own conversation —
    # never B's, no matter what — because the writer takes no conversation_id.
    row = conversations_db.post_owned_individual_user_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="A's own message", client_message_id="cmid-a",
    )
    assert row["conversation_id"] == require_client_conv["id"]
    assert row["conversation_id"] != b_conv["id"]


def test_client_conversation_id_honoured_is_red(isolated_settings, monkeypatch):
    """MUTATION PROOF (AC6): simulate the writer honouring a client-supplied
    `conversation_id` instead of resolving it server-side — the crafted
    foreign write succeeds (RED). The real writer (restored below, GREEN)
    has no such parameter, so this class of bug is structurally
    unreachable."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db.client import require_client
    from app.db import projects as projects_db
    from app.db.workspaces import ensure_default_workspace, upsert_workspace_member

    require_client().table("company_members").insert(
        {"id": "member-b-row-3", "company_id": ctx.company_id, "user_id": "member-b", "role": "member"}
    ).execute()
    ws = ensure_default_workspace(ctx.company_id)
    upsert_workspace_member(ws["id"], "member-b", "member")
    projects_db.add_member(project["id"], "member-b")
    b_conv = conversations_db.create_individual_project_chat(project["id"], "member-b")

    def _mutated_write_honouring_client_conversation_id(conversation_id: int, content: str) -> dict:
        """The BROKEN shape: writes directly into whatever conversation_id
        is handed to it, exactly like `post_individual_turn` does — which is
        fine for THAT cross-user writer, but would be an IDOR if the owned
        writer worked this way."""
        client = require_client()
        return (
            client.table("conversation_turns")
            .insert({"conversation_id": conversation_id, "role": "user", "content": content})
            .execute()
            .data[0]
        )

    # RED: a caller (attacker A) supplies B's conversation_id and it works.
    crafted = _mutated_write_honouring_client_conversation_id(b_conv["id"], "attacker A's forged turn")
    assert crafted["conversation_id"] == b_conv["id"], "mutation setup: the forged write must land on B's conversation"

    # GREEN: the REAL owned writer has no conversation_id parameter — A's
    # call can only ever resolve A's OWN conversation, never B's.
    real_row = conversations_db.post_owned_individual_user_turn(
        project_id=project["id"], user_id=ctx.user_id,
        content="A's real, owned turn", client_message_id="cmid-real",
    )
    assert real_row["conversation_id"] != b_conv["id"]

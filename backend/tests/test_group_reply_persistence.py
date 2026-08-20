"""The group agent's assistant turn persists the FULL structured reply.

Pre-change, `_respond_as_group_agent` persisted only the answer STRING onto
`conversation_turns.content`, collapsing the engine's structured response
(key_points/citations) and the classify envelope's card data
(`artifact_list`, the nested `open.candidates`) — so a reload rendered a
bare paragraph where the live turn had cards. Proven here:

  * the assistant turn's `reply` (jsonb) carries the engine payload MERGED
    with the classify envelope's card data; `content` keeps the plain
    answer text (the pre-column fallback);
  * the `turn.created` broadcast whitelist carries `reply` too, so a
    realtime-delivered agent turn renders the same cards a reload does;
  * the read DTO exposes `reply`, still whitelists (no `attachments`, no
    `client_message_id` leak);
  * a pre-column assistant turn (`reply` NULL) round-trips from `content`;
  * the migration is additive-only in form (nullable, no default,
    idempotent `if not exists`).
"""
from __future__ import annotations

from pathlib import Path

import app.routes.projects as projects_route
from app.db import conversations as conversations_db
from app.db.workspaces import ensure_default_workspace

REPO_ROOT = Path(__file__).resolve().parents[2]


def _seed_project(t) -> int:
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    return projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Reply project",
        created_by=t.user_id,
    )["id"]


def test_group_reply_persists_structured_payload_with_card_data(
    tenant_client, isolated_settings, monkeypatch
):
    """Retargeted from the deleted in-band group-reply path (the group POST no
    longer classifies + answers inline; the reply is produced on the `/v1/ask`
    mount and persisted by `ask_job_runner._persist_group_reply`). The invariant
    is unchanged: the assistant turn's `content` keeps the plain answer while its
    `reply` carries the FULL structured payload (answer + key_points + the
    classify envelope's `artifact_list`/`open` card data), and the realtime
    broadcast carries the same reply."""
    from app import ask_job_runner

    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv = conversations_db.create_group_chat(project_id, t.user_id)

    card_rows = [{
        "type": "prd", "id": 7, "title": "Checkout PRD", "status": "ready",
        "created_at": "2026-08-15T00:00:00Z", "brief_anchored": False,
        "source": {}, "open": {"prd_id": 7},
    }]
    open_result = {
        "status": "resolved", "artifact_type": "prd", "query": "checkout",
        "artifact": {"type": "prd", "id": 7, "prd_id": 7, "title": "Checkout PRD"},
        "candidates": [{"type": "prd", "id": 7, "prd_id": 7, "title": "Checkout PRD"}],
    }
    # The answer payload the shared engine produces for a "what are my PRDs?"
    # group turn — the classify enrichment now rides the reply directly.
    payload = {
        "answer": "Here are your PRDs.", "key_points": ["one PRD"], "citations": [],
        "artifact_list": card_rows, "open": open_result,
    }
    broadcasts: list[dict] = []
    monkeypatch.setattr(
        "app.project_group_realtime.publish_group_turn_created",
        lambda pid, cid, turn: broadcasts.append(turn),
    )

    ask_job_runner._persist_group_reply(
        ask_id=999_999, project_id=project_id, conversation_id=conv["id"], payload=payload,
    )

    turns = conversations_db.list_group_turns(conv["id"])
    assistant = [x for x in turns if x["role"] == "assistant"]
    assert assistant, "the reply must have posted an assistant turn"
    reply = assistant[-1]["reply"]
    # `content` keeps the plain answer (the pre-column fallback renderer)…
    assert assistant[-1]["content"] == "Here are your PRDs."
    # …and `reply` carries the FULL structured payload + the card data.
    assert reply["answer"] == "Here are your PRDs."
    assert reply["key_points"] == ["one PRD"]
    assert reply["artifact_list"] == card_rows
    # Nested — never a top-level open_candidates key.
    assert reply["open"]["candidates"][0]["prd_id"] == 7
    assert "open_candidates" not in reply

    # The realtime broadcast for the assistant turn carries the same reply.
    assert broadcasts and broadcasts[-1]["reply"]["artifact_list"] == card_rows


def test_prehistory_assistant_turn_roundtrips_from_content(
    tenant_client, isolated_settings
):
    """An assistant turn persisted WITHOUT a structured reply (every
    pre-column row) reads back with `reply` None and its `content` intact —
    the client's render-from-content fallback contract."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv = conversations_db.create_group_chat(project_id, t.user_id)

    conversations_db.post_group_turn(conv["id"], None, "plain old answer", role="assistant")
    turns = conversations_db.list_group_turns(conv["id"])
    assert turns[-1]["role"] == "assistant"
    assert turns[-1]["content"] == "plain old answer"
    assert turns[-1]["reply"] is None


def test_dto_whitelist_still_strips_attachments(
    tenant_client, isolated_settings
):
    """Adding `reply` (and later deliberately EXPOSING `client_message_id` so the
    poster can dedup its own realtime echo) must not let `attachments` — a
    genuinely internal column — leak onto the read DTO or the broadcast whitelist
    (`_GROUP_TURN_DTO_KEYS`)."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv = conversations_db.create_group_chat(project_id, t.user_id)

    from app.db.client import require_client

    require_client().table("conversation_turns").insert({
        "conversation_id": conv["id"], "role": "user", "content": "hi team",
        "author_user_id": t.user_id,
        "attachments": [{"name": "secret.md", "content": "internal"}],
        "client_message_id": "cmid-1",
    }).execute()

    turns = conversations_db.list_group_turns(conv["id"])
    assert turns and turns[-1]["content"] == "hi team"
    # `attachments` never rides the DTO…
    assert "attachments" not in turns[-1]
    # …while `client_message_id` is now a deliberately-exposed field (echo-dedup).
    assert turns[-1]["client_message_id"] == "cmid-1"
    assert "reply" in projects_route._GROUP_TURN_DTO_KEYS
    assert "attachments" not in projects_route._GROUP_TURN_DTO_KEYS


def test_reply_migration_is_additive_only():
    """Nullable, no default, idempotent — a live prod-shared table only ever
    gets the additive form (no rewrite, no backfill)."""
    sql = (
        REPO_ROOT / "supabase" / "migrations"
        / "20260816160000_conversation_turns_reply.sql"
    ).read_text()
    # Statements only — the prose comments may legitimately SAY "default".
    stmts = "\n".join(
        line for line in sql.lower().splitlines()
        if line.strip() and not line.strip().startswith("--")
    )
    assert "add column if not exists reply jsonb" in stmts
    assert "default" not in stmts
    assert "not null" not in stmts
    assert "drop" not in stmts

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
    monkeypatch.setattr(
        projects_route, "_classify_group_envelope",
        lambda *a, **kw: {
            "intent": "list_artifacts",
            "artifact_list": card_rows,
            "open": open_result,
        },
    )
    monkeypatch.setattr(
        projects_route.qa_agent, "answer",
        lambda **kw: {"answer": "Here are your PRDs.", "key_points": ["one PRD"], "citations": []},
    )
    broadcasts: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        projects_route, "publish_broadcast",
        lambda topic, event, payload: broadcasts.append((event, payload)),
    )

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns", json={"content": "@Sprntly what are my PRDs?"},
    )
    assert resp.status_code == 200, resp.text

    turns = conversations_db.list_group_turns(conv["id"])
    assistant = [x for x in turns if x["role"] == "assistant"]
    assert assistant, "the mention reply must have posted an assistant turn"
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
    agent_payloads = [
        p for (event, p) in broadcasts
        if event == "turn.created" and p.get("role") == "assistant"
    ]
    assert agent_payloads and agent_payloads[-1]["reply"]["artifact_list"] == card_rows


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


def test_dto_whitelist_still_strips_internal_columns(
    tenant_client, isolated_settings
):
    """Adding `reply` must not loosen the read whitelist: `attachments` and
    `client_message_id` still never ride the DTO (or the broadcast, which
    uses `_GROUP_TURN_DTO_KEYS`)."""
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
    assert "attachments" not in turns[-1]
    assert "client_message_id" not in turns[-1]
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

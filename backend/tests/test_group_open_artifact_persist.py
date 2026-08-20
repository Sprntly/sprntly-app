"""Group open-artifact persists a turn (R3) — mutation proof, not a dropped
message.

When a group member says "open the PRD", the agent's open-artifact reply must
land in the shared thread as a PERSISTED group turn carrying the resolved
`open` payload — otherwise the action happens but the message vanishes from the
thread (the "dropped message" bug). The group reply is produced by the shared
`/v1/ask` execution job and persisted+broadcast by
`ask_job_runner._persist_group_reply` (the "mount-not-scheduler" Choice-A seam),
which persists the FULL structured reply as the turn's `reply`. These drive that
seam directly and prove the row is written — including the terse/open-only case
where the prose is minimal.
"""
from __future__ import annotations

from app import ask_job_runner
from app.db import conversations as conversations_db
from tests._company_helpers import company_client


def _create_project(ctx, *, name: str = "Open-artifact project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def test_group_open_artifact_reply_persists_a_turn_carrying_the_open(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)

    # The "open the PRD" reply: an open-artifact payload with the resolved target.
    payload = {
        "answer": "Opening the PRD now.",
        "citations": [],
        "intent": "open_artifact",
        "open": {"kind": "prd", "prd_id": 42},
    }
    ask_job_runner._persist_group_reply(
        ask_id=999_999, project_id=project["id"], conversation_id=conv["id"], payload=payload,
    )

    turns = conversations_db.list_group_turns(conv["id"])
    assistant = [t for t in turns if t["role"] == "assistant"]
    # The open-artifact message is PERSISTED (not dropped from the thread) …
    assert len(assistant) == 1
    assert assistant[0]["author_user_id"] is None
    # … and the resolved `open` rides the turn's structured reply, so a reload
    # restores the openable artifact alongside the message.
    assert (assistant[0].get("reply") or {}).get("open") == {"kind": "prd", "prd_id": 42}


def test_group_open_only_reply_with_terse_prose_still_persists(isolated_settings, monkeypatch):
    """The dropped-message edge: an open action whose prose is empty (the reply
    is essentially "just open it") must STILL persist a group turn — the `open`
    payload is the message, and losing it would leave the thread with no record
    that the PRD was opened."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)

    payload = {"answer": "", "citations": [], "intent": "open_artifact", "open": {"kind": "prd", "prd_id": 7}}
    ask_job_runner._persist_group_reply(
        ask_id=999_998, project_id=project["id"], conversation_id=conv["id"], payload=payload,
    )

    turns = conversations_db.list_group_turns(conv["id"])
    assistant = [t for t in turns if t["role"] == "assistant"]
    assert len(assistant) == 1, "an open-only reply must not be dropped from the group thread"
    assert (assistant[0].get("reply") or {}).get("open") == {"kind": "prd", "prd_id": 7}

"""The group composer's `+` payloads persist + are accepted by the group POST.

SCOPE NOTE (post-rewrite): the group POST is now "mount-not-scheduler" — it
persists + broadcasts the human turn and does NOT answer in-band (the reply runs
on the shared `/v1/ask` mount). So the ANSWER-INPUT shaping this file used to
assert on the in-band call (the `[Attached files]` fold into the agent's
question, `pinned_skill` reaching `qa_agent.answer`, `history` recent-minus-
trigger) moved to the `/v1/ask` path and is covered by
`test_project_answer_collapse.py` (the group history/transcript-scope tests) +
the route's generic attachment fold. The deleted `_respond_as_group_agent` /
`_schedule_group_reply` / group-retry-route tests are removed with that path.

What remains here is the COMPOSER PAYLOAD contract at the POST route, unchanged:
attachments persist onto the human turn (existing column) yet never ride the read
DTO; a `pinned_skill` dict + `client_message_id` are accepted (200, turn posted);
a plain post behaves exactly as before.
"""
from __future__ import annotations

from app.db import conversations as conversations_db


def _seed_project(t) -> int:
    from app.db import projects as projects_db
    from app.db.workspaces import ensure_default_workspace

    ws_id = ensure_default_workspace(t.company_id)["id"]
    return projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Group composer", created_by=t.user_id,
    )["id"]


def test_attachments_persist_on_turn_but_never_ride_the_dto(
    tenant_client, isolated_settings
):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv = conversations_db.create_group_chat(project_id, t.user_id)

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={
            "content": "@Sprntly summarize the attached notes",
            "attachments": [{"name": "notes.md", "content": "Q3 pipeline is slipping."}],
            "client_message_id": "cmid-attach-1",
        },
    )
    assert resp.status_code == 200, resp.text

    # The visible thread keeps the plain content; the read DTO never carries the
    # file text (attachments is an internal column).
    turns = conversations_db.list_group_turns(conv["id"])
    human = [x for x in turns if x["role"] == "user"]
    assert human[-1]["content"] == "@Sprntly summarize the attached notes"
    assert all("attachments" not in x for x in turns)

    # …but the attachment IS persisted on the raw turn row (available to the
    # `/v1/ask` answer that folds it into the agent's question).
    from app.db.client import require_client

    raw = (
        require_client().table("conversation_turns")
        .select("attachments").eq("id", human[-1]["id"]).execute().data[0]
    )
    assert raw["attachments"] == [{"name": "notes.md", "content": "Q3 pipeline is slipping."}]


def test_pinned_skill_and_client_message_id_accepted(tenant_client, isolated_settings):
    """The composer's skill pick + idempotency key post without error and the
    turn is persisted; the SPLICED trigger rides `content` (the FE's one splice
    rule the engine's slash routing reads), and the id rides the DTO."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv = conversations_db.create_group_chat(project_id, t.user_id)

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={
            "content": "/weekly-report @Sprntly pull the numbers",
            "pinned_skill": {"id": "sk-1", "trigger": "/weekly-report", "label": "Weekly report"},
            "client_message_id": "cmid-skill-1",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["client_message_id"] == "cmid-skill-1"

    turns = conversations_db.list_group_turns(conv["id"])
    assert turns[-1]["content"] == "/weekly-report @Sprntly pull the numbers"


def test_plain_post_without_opts_unchanged(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv = conversations_db.create_group_chat(project_id, t.user_id)

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly hello there"},
    )
    assert resp.status_code == 200, resp.text
    turns = conversations_db.list_group_turns(conv["id"])
    assert turns[0]["content"] == "@Sprntly hello there"
    assert all("attachments" not in x for x in turns)

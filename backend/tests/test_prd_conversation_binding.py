"""The chat that COMMANDED a PRD stays attached to it, server-side.

Reported bug: a user typed "generate prd" over an uploaded deck, then left the
page while the import was still running. Reopening that chat from history showed
only the agent's acknowledgment — no PRD, no View PRD button, no panel. The chat
had produced a document it could no longer reach.

Cause: the conversation row is necessarily created BEFORE the prd_id exists (the
seed turn renders instantly; the import returns seconds later), so it was stored
with prd_id NULL and back-patched by the browser afterwards. Navigating away
killed the patch, and NULL is forever.

Fix: the PRD routes accept the commanding `conversation_id` and write the link
themselves, at PRD-creation time — no browser required. These tests pin that,
plus the tenancy rules that keep it from binding chats it has no business
touching.
"""
from __future__ import annotations

import io

from app.db.client import require_client


def _save_current_brief(db_mod, dataset):
    payload = {
        "summary_headline": "stub",
        "insights": [{"title": "Brief insight 0", "theme_id": "brief-theme"}],
        "_schema_version": 1,
    }
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


def _new_conversation(company_id, user_id, prd_id=None):
    """A chat row as the client persists it for a PRD command: no prd_id yet."""
    row = {
        "company_id": company_id,
        "user_id": user_id,
        "title": "generate prd",
        "query": "generate prd",
        "agent_type": "ask",
    }
    if prd_id is not None:
        row["prd_id"] = prd_id
    resp = require_client().table("conversations").insert(row).execute()
    return resp.data[0]["id"]


def _conversation(conv_id):
    return (
        require_client().table("conversations").select("*")
        .eq("id", conv_id).execute().data[0]
    )


def _upload(name="deck.txt", body=b"# Requirements\n\nUsers want dark mode."):
    return {"file": (name, io.BytesIO(body), "text/plain")}


# ── generate-from-task ───────────────────────────────────────────────────────

def test_generate_from_task_binds_the_commanding_conversation(
    tenant_client, isolated_settings
):
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    conv_id = _new_conversation(t.company_id, t.user_id)

    resp = t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "conversation_id": conv_id},
    )
    assert resp.status_code == 200
    prd_id = resp.json()["prd_id"]

    # Bound by the ROUTE, before generation finished — the browser never has to
    # come back for this to hold.
    assert _conversation(conv_id)["prd_id"] == prd_id


def test_generate_from_task_binds_when_the_task_resolves_an_EXISTING_prd(
    tenant_client, isolated_settings
):
    """Re-issuing the same command returns the existing PRD (find-or-create).
    The new chat must point at it too, or reopening that chat shows nothing."""
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    _save_current_brief(db_mod, dataset="acme")

    first = t.client.post(
        "/v1/prd/generate-from-task", json={"task": "dark mode on mobile"}
    ).json()["prd_id"]

    conv_id = _new_conversation(t.company_id, t.user_id)
    again = t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "conversation_id": conv_id},
    )
    assert again.json()["prd_id"] == first
    assert _conversation(conv_id)["prd_id"] == first


def test_generate_from_task_without_a_conversation_id_is_unchanged(
    tenant_client, isolated_settings
):
    """The parameter is optional: callers that have no chat (and older clients)
    generate exactly as before."""
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")

    resp = t.client.post(
        "/v1/prd/generate-from-task", json={"task": "dark mode on mobile"}
    )
    assert resp.status_code == 200
    assert resp.json()["prd_id"]


# ── import ───────────────────────────────────────────────────────────────────

def test_import_binds_the_commanding_conversation(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    conv_id = _new_conversation(t.company_id, t.user_id)

    resp = t.client.post(
        "/v1/prd/import",
        files=_upload(),
        data={"dataset": "acme", "conversation_id": str(conv_id)},
    )
    assert resp.status_code == 200
    prd_id = resp.json()["prd_id"]

    # This is the exact repro: the user leaves now. The link is already written.
    assert _conversation(conv_id)["prd_id"] == prd_id


def test_import_without_a_conversation_id_still_works(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    resp = t.client.post("/v1/prd/import", files=_upload(), data={"dataset": "acme"})
    assert resp.status_code == 200
    assert resp.json()["prd_id"]


# ── the binding must not become a way to touch other people's chats ──────────

def test_binding_ignores_a_conversation_owned_by_another_user(
    tenant_client, isolated_settings
):
    """Chats are per-user. A conversation id belonging to someone else must not
    be bound — nor may the mismatch fail the generation."""
    owner = tenant_client.make(slug="acme")
    other = tenant_client.make(slug="acme", user_id="user-someone-else")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    victim = _new_conversation(owner.company_id, owner.user_id)

    resp = other.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "conversation_id": victim},
    )
    assert resp.status_code == 200
    assert _conversation(victim)["prd_id"] is None


def test_binding_never_repoints_a_conversation_that_already_has_a_prd(
    tenant_client, isolated_settings
):
    """Only a NULL prd_id is filled. A chat already bound to a document keeps
    it, so a stray command can't silently swap what a chat is about."""
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    conv_id = _new_conversation(t.company_id, t.user_id, prd_id=4242)

    t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "conversation_id": conv_id},
    )
    assert _conversation(conv_id)["prd_id"] == 4242


def test_an_unknown_conversation_id_does_not_fail_the_generation(
    tenant_client, isolated_settings
):
    """The PRD is what the user is waiting on; a bad id must never cost them it."""
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")

    resp = t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "conversation_id": 999999},
    )
    assert resp.status_code == 200
    assert resp.json()["prd_id"]

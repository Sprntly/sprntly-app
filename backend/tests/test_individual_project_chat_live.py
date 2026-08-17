"""Real-LLM / real-DB round trip for the individual-project-chat conversation
binding — the gap the fake-Supabase tier (`test_individual_project_chat.py`)
cannot close: a stubbed classifier proves the WIRING but never proves the
actual promotion DECISION was made by the real model.

Mirrors `test_ask_project_promotion.py`'s own live tier exactly (same
fixture shape, same non-loopback guard, same "stub qa_agent.answer, leave
the classifier real" split — answer generation is a separate surface).
The one thing new here: the conversation is obtained through THIS ticket's
`create_individual_project_chat` get-or-create helper (proven idempotent
against the REAL Postgres), not a raw table insert — closing the exact gap
the Phase-3 sweep found (the shipped UI never had a durable, reusable
conversation_id to send).

Run it with:

    RUN_ASK_PROJECT_PROMOTION_LIVE=1 \\
        pytest tests/test_individual_project_chat_live.py -m integration
"""
from __future__ import annotations

import os
import uuid

import pytest

from app import ask_job_runner as ajr

_DURABLE_ANSWER = (
    "Locking the API rate limit at 100 requests/min per tenant, applied "
    "uniformly including enterprise accounts."
)
_DURABLE_QUESTION = (
    "Can you record that we're locking the API rate limit at 100 "
    "requests/min per tenant, with no exception for enterprise customers?"
)


def _payload(answer: str) -> dict:
    return {
        "answer": answer, "key_points": [], "citations": [],
        "confidence": 0.8, "unanswered": "",
    }


_RUN_LIVE = os.getenv("RUN_ASK_PROJECT_PROMOTION_LIVE") == "1" and bool(
    os.getenv("ANTHROPIC_API_KEY")
)

_LIVE_SKIP_REASON = (
    "needs a real local Supabase + a real ANTHROPIC_API_KEY — set "
    "RUN_ASK_PROJECT_PROMOTION_LIVE=1 with SUPABASE_URL/"
    "SUPABASE_SERVICE_ROLE_KEY/SUPABASE_JWT_SECRET/ANTHROPIC_API_KEY "
    "pointed at the local rig and the projects/chat/memory migrations applied"
)


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live promotion round-trip against a "
        f"non-loopback SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    if not _RUN_LIVE:
        pytest.skip("live tier disabled")
    return _sb()


@pytest.fixture(scope="module")
def fixture_ids(sb):
    companies = sb.table("companies").select("id").limit(1).execute().data
    assert companies, "no company row in the local rig — seed one before running this test"
    company_id = companies[0]["id"]

    workspaces = (
        sb.table("workspaces").select("id").eq("company_id", company_id).limit(1).execute().data
    )
    assert workspaces, f"no workspace for company {company_id}"
    workspace_id = workspaces[0]["id"]

    owners = (
        sb.table("company_members")
        .select("user_id, role")
        .eq("company_id", company_id)
        .in_("role", ["owner", "admin"])
        .limit(1)
        .execute()
        .data
    )
    assert owners, f"need >=1 owner/admin company_members row for company {company_id}"
    user_id = owners[0]["user_id"]

    yield {"company_id": company_id, "workspace_id": workspace_id, "user_id": user_id}


@pytest.fixture
def project_ids(sb):
    created: list[int] = []
    yield created
    for pid in created:
        # A promoted entry's `source_conversation_id` FKs to the
        # conversation row — clear entries FIRST or the conversation delete
        # below trips `project_memory_entries_source_conversation_id_fkey`
        # (mirrors `test_ask_project_promotion.py`'s `conversation_ids`
        # teardown).
        sb.table("project_memory_entries").delete().eq("project_id", pid).execute()
        sb.table("conversations").delete().eq("project_id", pid).execute()
        sb.table("projects").delete().eq("id", pid).execute()


def _make_project(sb, fixture_ids, project_ids, *, name: str) -> dict:
    from app.db import projects as projects_db

    project = projects_db.create_project(
        company_id=fixture_ids["company_id"],
        workspace_id=fixture_ids["workspace_id"],
        name=f"{name} {uuid.uuid4().hex[:8]}",
        created_by=fixture_ids["user_id"],
    )
    project_ids.append(project["id"])
    return project


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_get_or_create_individual_chat_idempotent_against_real_postgres(
    sb, fixture_ids, project_ids
):
    """(a) The get-or-create helper is genuinely idempotent against the real
    database — two calls for the same (project, caller) return the SAME row,
    and exactly one row exists after both."""
    from app.db import conversations as conversations_db

    project = _make_project(sb, fixture_ids, project_ids, name="Live idempotency")

    first = conversations_db.create_individual_project_chat(project["id"], fixture_ids["user_id"])
    second = conversations_db.create_individual_project_chat(project["id"], fixture_ids["user_id"])
    assert first["id"] == second["id"]

    rows = (
        sb.table("conversations")
        .select("id")
        .eq("project_id", project["id"])
        .eq("kind", "individual")
        .eq("user_id", fixture_ids["user_id"])
        .execute()
        .data
    )
    assert len(rows) == 1


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_individual_chat_conversation_promotes_via_real_classifier(
    sb, fixture_ids, project_ids, monkeypatch
):
    """(b) THE closed gap, end-to-end for real: get-or-create the durable
    conversation the exact way the fixed `ProjectIndividualChat.tsx` now
    does, run it through the real ask pipeline (answer stubbed, classifier
    real), and confirm the real model's decision lands a correctly-
    provenanced `project_memory_entries` row."""
    from app.db import conversations as conversations_db
    from app.db.asks import start_ask_job

    project = _make_project(sb, fixture_ids, project_ids, name="Live durable promotion")
    conv = conversations_db.create_individual_project_chat(project["id"], fixture_ids["user_id"])
    conv_id = conv["id"]

    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: _payload(_DURABLE_ANSWER))
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)

    import asyncio

    ask_id = start_ask_job(
        company_id=fixture_ids["company_id"], dataset="d", question=_DURABLE_QUESTION,
    )
    asyncio.run(
        ajr.run_ask_job(
            ask_id=ask_id,
            enterprise_id=fixture_ids["company_id"],
            question=_DURABLE_QUESTION,
            dataset="d",
            conversation_id=conv_id,
            project_id=project["id"],
        )
    )

    rows = (
        sb.table("project_memory_entries")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert len(rows) == 1, "a durable rate-limit decision must be promoted"
    entry = rows[0]
    assert entry["promoted_by"] == "agent"
    assert entry["author_user_id"] is None
    assert entry["source_conversation_id"] == conv_id

"""Real local-Supabase round-trip for `app.project_join_greeting.post_join_greeting`.

Mirrors `test_resolve_candidate_live.py`'s fixture shape for the identical
reason: `FakeSupabaseClient` is an in-memory store with no real SQL engine
behind it, so the fast-lane suite (`test_project_join_greeting.py`) can only
prove the CONTRACT against monkeypatched stand-ins — not that a genuinely
new membership, added through the real client, lands exactly one greeting
turn in the real member's individual conversation.

Run it with:

    RUN_PROJECT_JOIN_GREETING_LIVE=1 \\
        pytest tests/test_project_join_greeting_live.py -m integration

No `ANTHROPIC_API_KEY` needed — the greeting path REUSES the cached
`project_memory_summary` and makes no fresh LLM call (AC-5).
"""
from __future__ import annotations

import os
import uuid

import pytest

_RUN_LIVE = os.getenv("RUN_PROJECT_JOIN_GREETING_LIVE") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase — set RUN_PROJECT_JOIN_GREETING_LIVE=1 "
            "with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig "
            "and the projects/conversations migrations applied"
        ),
    ),
]


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live join-greeting round-trip against a "
        f"non-loopback SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _sb()


@pytest.fixture
def fixture_ids(sb):
    """A real (company, workspace, project) triple already in the rig plus a
    fabricated member user id — a genuinely new membership target the
    greeting posts into."""
    companies = sb.table("companies").select("id").limit(1).execute().data
    assert companies, "no company row in the local rig — seed one before running this test"
    company_id = companies[0]["id"]

    workspaces = (
        sb.table("workspaces")
        .select("id")
        .eq("company_id", company_id)
        .eq("is_default", True)
        .limit(1)
        .execute()
        .data
    )
    assert workspaces, f"no default workspace for company {company_id}"
    workspace_id = workspaces[0]["id"]

    owner_rows = (
        sb.table("company_members")
        .select("user_id")
        .eq("company_id", company_id)
        .limit(1)
        .execute()
        .data
    )
    assert owner_rows, f"no company_members row for company {company_id}"
    creator_user_id = owner_rows[0]["user_id"]

    project = (
        sb.table("projects")
        .insert(
            {
                "company_id": company_id,
                "workspace_id": workspace_id,
                "name": f"join-greeting live {uuid.uuid4().hex[:8]}",
                "created_by": creator_user_id,
            }
        )
        .execute()
        .data[0]
    )

    member_user_id = "join-greeting-live-" + uuid.uuid4().hex[:10]
    sb.table("profiles").insert({"id": member_user_id}).execute()

    yield {"project_id": project["id"], "member_user_id": member_user_id}

    sb.table("conversations").delete().eq("project_id", project["id"]).execute()
    sb.table("project_members").delete().eq("project_id", project["id"]).execute()
    sb.table("projects").delete().eq("id", project["id"]).execute()
    sb.table("profiles").delete().eq("id", member_user_id).execute()


def test_real_new_membership_lands_one_greeting_turn(sb, fixture_ids):
    from app.project_join_greeting import post_join_greeting

    project_id = fixture_ids["project_id"]
    user_id = fixture_ids["member_user_id"]

    post_join_greeting(project_id, user_id)

    conversations = (
        sb.table("conversations")
        .select("id")
        .eq("project_id", project_id)
        .eq("kind", "individual")
        .eq("user_id", user_id)
        .execute()
        .data
    )
    assert len(conversations) == 1, "expected exactly one get-or-created individual chat"
    conversation_id = conversations[0]["id"]

    turns = (
        sb.table("conversation_turns")
        .select("*")
        .eq("conversation_id", conversation_id)
        .execute()
        .data
    )
    assert len(turns) == 1, "expected exactly one posted greeting turn"
    assert turns[0]["role"] == "assistant"
    assert turns[0]["content"].strip() != ""

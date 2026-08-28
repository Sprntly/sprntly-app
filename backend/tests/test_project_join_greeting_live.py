"""Real local-Supabase + real-Anthropic round trip for
`app.project_join_greeting.post_join_greeting`.

Mirrors `test_project_origin_seed_live.py`'s gating shape: the private-first
memory wave's rebuilt greeting makes ONE real `claude-sonnet-4-6` call per
member-add (the module's FIRST LLM call — before this wave the greeting was
a deterministic digest with no LLM call at all), so a fully-stubbed run
(`test_project_join_greeting.py`) can only prove the writer's CONTRACT, never
that a real model actually produces the item-#5 brief or that the turn lands
through real PostgREST.

Run it with:

    RUN_PROJECT_JOIN_GREETING_LIVE=1 \\
        pytest tests/test_project_join_greeting_live.py -m integration
"""
from __future__ import annotations

import os
import uuid

import pytest

_RUN_LIVE = os.getenv("RUN_PROJECT_JOIN_GREETING_LIVE") == "1" and bool(
    os.getenv("ANTHROPIC_API_KEY")
)

_LIVE_SKIP_REASON = (
    "needs a real local Supabase + a real ANTHROPIC_API_KEY — set "
    "RUN_PROJECT_JOIN_GREETING_LIVE=1 with SUPABASE_URL/"
    "SUPABASE_SERVICE_ROLE_KEY/ANTHROPIC_API_KEY pointed at the local rig "
    "and the projects/conversations/memory/delegation migrations applied"
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.real_greeting_synthesis,  # opt OUT of conftest's autouse
    # call_md stub for the greeting's own narrative pass — this suite needs
    # the real thing.
    pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON),
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

    yield {
        "project_id": project["id"],
        "member_user_id": member_user_id,
        "company_id": company_id,
        "creator_user_id": creator_user_id,
    }

    sb.table("conversations").delete().eq("project_id", project["id"]).execute()
    sb.table("project_memory_summary").delete().eq("project_id", project["id"]).execute()
    sb.table("project_memory_entries").delete().eq("project_id", project["id"]).execute()
    sb.table("project_artifacts").delete().eq("project_id", project["id"]).execute()
    sb.table("project_members").delete().eq("project_id", project["id"]).execute()
    sb.table("projects").delete().eq("id", project["id"]).execute()
    sb.table("profiles").delete().eq("id", member_user_id).execute()


def test_real_new_membership_lands_one_greeting_turn(sb, fixture_ids):
    """Wiring proof on a bare (no summary/artifacts/delegations) project —
    the real LLM still produces an honest greeting turn even with almost
    nothing to say."""
    from app.db.companies import slug_for_company_id
    from app.project_join_greeting import post_join_greeting

    project_id = fixture_ids["project_id"]
    user_id = fixture_ids["member_user_id"]
    company_id = fixture_ids["company_id"]

    post_join_greeting(
        project_id, user_id, dataset=slug_for_company_id(company_id) or "", company_id=company_id
    )

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


def test_member_add_greeting_live(sb, fixture_ids):
    """AC2/AC13: a member-add on a project with a memory summary, >=1
    artifact, and an open delegation for the new member posts exactly ONE
    assistant turn whose body (a) contains `MORE_MARKER`, (b) leads with a
    zero-context problem statement, and (c) references the member's
    assigned item."""
    from app.db.companies import slug_for_company_id
    from app.db.project_delegations import record_delegation
    from app.project_join_greeting import MORE_MARKER, post_join_greeting

    project_id = fixture_ids["project_id"]
    user_id = fixture_ids["member_user_id"]
    company_id = fixture_ids["company_id"]

    sb.table("project_memory_summary").insert(
        {
            "project_id": project_id,
            "summary_md": (
                "The team is building a self-serve bulk-close action for support "
                "reps to clear tickets untouched for 30+ days, gated behind a "
                "manager approval step before the close is final."
            ),
            "entry_count": 1,
            "stale": False,
        }
    ).execute()

    artifact = (
        sb.table("custom_artifacts")
        .insert(
            {
                "company_id": company_id,
                "kind": "report",
                "title": "Bulk-close design notes",
                "status": "ready",
            }
        )
        .execute()
        .data[0]
    )
    sb.table("project_artifacts").insert(
        {"project_id": project_id, "artifact_type": "custom_artifact", "artifact_id": artifact["id"]}
    ).execute()

    delegation = record_delegation(
        project_id=project_id,
        assigner_user_id=fixture_ids["creator_user_id"],
        assignee_user_id=user_id,
        task_summary=f"live greeting round-trip {uuid.uuid4().hex[:8]}",
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=None,
        delivered_turn_id=None,
    )

    post_join_greeting(
        project_id, user_id, dataset=slug_for_company_id(company_id) or "", company_id=company_id
    )

    conversations = (
        sb.table("conversations")
        .select("id")
        .eq("project_id", project_id)
        .eq("kind", "individual")
        .eq("user_id", user_id)
        .execute()
        .data
    )
    assert len(conversations) == 1
    conversation_id = conversations[0]["id"]

    turns = (
        sb.table("conversation_turns")
        .select("*")
        .eq("conversation_id", conversation_id)
        .execute()
        .data
    )
    assert len(turns) == 1, "expected exactly ONE posted greeting turn"
    body = turns[0]["content"]
    assert turns[0]["role"] == "assistant"
    assert MORE_MARKER in body, "the item-#5 brief must split lead/rest on the real marker"
    lead, _, rest = body.partition(MORE_MARKER)
    assert lead.strip() != "", "the lead must be a non-empty zero-context problem statement"
    assert rest.strip() != ""
    assert delegation["task_summary"] in body, (
        "the greeting must reference the new member's own assigned item"
    )

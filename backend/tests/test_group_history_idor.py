"""Regression tests for the group-history fold IDOR
(`routes.ask._load_group_history`).

The bug: `_load_group_history` gated ONLY on `is_project_member` for the
CLIENT-supplied `project_id`, then read turns for the CLIENT-supplied
`conversation_id` — with nothing binding the two. `list_group_turns`
scopes only on `kind='group'`, so a member of ANY project could pass
`(their own project_id, a victim's group conversation_id)` and fold the
victim's group thread into the LLM prompt (cross-project, even
cross-tenant).

The fix binds `conversation_id` to `project_id`: `_load_group_history`
resolves the project's OWN canonical group conversation
(`get_group_chat_id`) and requires the client's id to equal it, and
`list_group_turns` gained an optional `project_id` scope as
belt-and-suspenders. These tests prove the leak is closed and the
legitimate member-reads-own-thread path is unchanged.

Everything runs against the in-memory fake Supabase (`isolated_settings`)
at the `_load_group_history` / `list_group_turns` seam — the faithful
level for this boundary (no LLM, no HTTP auth plumbing needed to prove the
read gate).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.db import conversations as conversations_db
from app.db import projects as projects_db
from app.routes.ask import _load_group_history
from tests._company_helpers import seed_company


def _seed_company_ws(*, user_id: str, slug: str) -> tuple[str, str]:
    """A company + default workspace with `user_id` seeded into both."""
    from app.db.client import require_client  # noqa: F401 — parity w/ helpers
    from app.db.workspaces import ensure_default_workspace, upsert_workspace_member

    company_id = seed_company(user_id=user_id, slug=slug)
    ws = ensure_default_workspace(company_id)
    upsert_workspace_member(ws["id"], user_id, "admin")
    return company_id, ws["id"]


def _seed_project_with_group(
    *,
    company_id: str,
    workspace_id: str,
    user_id: str,
    name: str,
    turns: list[str],
    trailing_agent_turn: str | None = None,
) -> SimpleNamespace:
    """A project (owned by `user_id`) with a group chat carrying `turns`.

    `trailing_agent_turn` appends an assistant turn LAST — important for the
    IDOR victim threads: `_load_group_history`'s no-key fallback pops a
    trailing USER turn (treating it as the current question), so a victim
    thread that ended on a user turn would come back empty for a reason
    UNRELATED to the binding fix. Ending on an assistant turn (as a real
    replied-to thread does) means content survives that pop and WOULD leak if
    the binding were absent — the mutation the tests must actually catch."""
    project = projects_db.create_project(
        company_id=company_id,
        workspace_id=workspace_id,
        name=name,
        created_by=user_id,
    )
    conv = conversations_db.create_group_chat(project["id"], user_id)
    for content in turns:
        conversations_db.post_group_turn(conv["id"], user_id, content)
    if trailing_agent_turn is not None:
        conversations_db.post_group_turn(
            conv["id"], None, trailing_agent_turn, role="assistant"
        )
    return SimpleNamespace(project=project, conv=conv, user_id=user_id)


# ── The headline: cross-project IDOR is blocked ─────────────────────────


def test_idor_cross_project_same_tenant_blocked(isolated_settings):
    """A member of project A passing project B's group conversation_id (both
    in the SAME company) gets `[]` — never B's turns."""
    attacker = "attacker-" + uuid.uuid4().hex[:8]
    victim = "victim-" + uuid.uuid4().hex[:8]
    company_id, ws_id = _seed_company_ws(user_id=attacker, slug="acme-idor")

    a = _seed_project_with_group(
        company_id=company_id, workspace_id=ws_id, user_id=attacker,
        name="Attacker project A", turns=["attacker's own harmless note"],
    )
    # Victim project in the SAME company; attacker is deliberately never added
    # to its project_members.
    from app.db.client import require_client
    require_client().table("profiles").insert(
        {"id": victim, "full_name": "Victim Vim", "role": "Founder"}
    ).execute()
    b = _seed_project_with_group(
        company_id=company_id, workspace_id=ws_id, user_id=victim,
        name="Victim project B",
        turns=["SECRET: acquisition price is 4.2M"],
        trailing_agent_turn="Noted — keeping that confidential.",
    )

    # The exploit shape: attacker's OWN project_id (membership passes) +
    # victim's group conversation_id.
    leaked = _load_group_history(
        b.conv["id"], a.project["id"], attacker, None
    )
    assert leaked == [], "cross-project group thread leaked via mismatched conversation_id"
    # Explicitly: no victim content anywhere in the result.
    blob = " ".join((t.get("content") or "") for t in leaked)
    assert "acquisition price" not in blob and "confidential" not in blob


def test_idor_cross_tenant_blocked(isolated_settings):
    """The cross-TENANT variant: victim project B is in a DIFFERENT company.
    Attacker (member of A in company 1) passing B's conversation_id → `[]`."""
    attacker = "attacker-" + uuid.uuid4().hex[:8]
    victim = "victim-" + uuid.uuid4().hex[:8]
    company_a, ws_a = _seed_company_ws(user_id=attacker, slug="tenant-a-idor")
    company_b, ws_b = _seed_company_ws(user_id=victim, slug="tenant-b-idor")

    a = _seed_project_with_group(
        company_id=company_a, workspace_id=ws_a, user_id=attacker,
        name="Tenant A project", turns=["nothing sensitive"],
    )
    b = _seed_project_with_group(
        company_id=company_b, workspace_id=ws_b, user_id=victim,
        name="Tenant B project",
        turns=["SECRET: tenant B roadmap Q4"],
        trailing_agent_turn="Got it — internal only.",
    )

    leaked = _load_group_history(
        b.conv["id"], a.project["id"], attacker, None
    )
    assert leaked == [], "cross-TENANT group thread leaked via mismatched conversation_id"
    blob = " ".join((t.get("content") or "") for t in leaked)
    assert "roadmap" not in blob and "internal only" not in blob


def test_non_member_still_denied(isolated_settings):
    """The pre-existing membership gate still holds: a non-member passing the
    (foreign) project_id itself gets `[]` (membership fails first)."""
    outsider = "outsider-" + uuid.uuid4().hex[:8]
    owner = "owner-" + uuid.uuid4().hex[:8]
    company_id, ws_id = _seed_company_ws(user_id=owner, slug="acme-nonmember")
    # Outsider is in the same company but not the project.
    from app.db.client import require_client
    require_client().table("company_members").insert(
        {"id": uuid.uuid4().hex, "company_id": company_id, "user_id": outsider, "role": "member"}
    ).execute()

    b = _seed_project_with_group(
        company_id=company_id, workspace_id=ws_id, user_id=owner,
        name="Owned project",
        turns=["members-only chatter"],
        trailing_agent_turn="Acknowledged.",
    )
    # Even with the CORRECT conversation_id (so the binding would PASS), a
    # non-member reads nothing — the membership gate denies first. The trailing
    # agent turn means a leak would be non-empty, so [] proves the gate.
    assert _load_group_history(b.conv["id"], b.project["id"], outsider, None) == []


# ── The legitimate path is unchanged ────────────────────────────────────


def test_legitimate_member_reads_own_group_history(isolated_settings):
    """A real member fetching their OWN project's group history still gets it,
    author-attributed, oldest-first, current-turn excluded."""
    member = "member-" + uuid.uuid4().hex[:8]
    company_id, ws_id = _seed_company_ws(user_id=member, slug="acme-legit")
    from app.db.client import require_client
    require_client().table("profiles").insert(
        {"id": member, "full_name": "Ada Member", "role": "PM"}
    ).execute()

    project = projects_db.create_project(
        company_id=company_id, workspace_id=ws_id, name="Own project", created_by=member
    )
    conv = conversations_db.create_group_chat(project["id"], member)
    conversations_db.post_group_turn(conv["id"], member, "kickoff thoughts")
    conversations_db.post_group_turn(conv["id"], None, "On it.", role="assistant")
    conversations_db.post_group_turn(
        conv["id"], member, "current question", client_message_id="cmid-legit"
    )

    hist = _load_group_history(conv["id"], project["id"], member, "cmid-legit")
    contents = [t["content"] for t in hist]
    # Prior human turn is folded with author attribution; the assistant turn is
    # plain (Sprntly's own voice); the current turn is excluded by key.
    assert "Ada Member (PM): kickoff thoughts" in contents
    assert "On it." in contents
    assert "current question" not in " ".join(contents)
    assert all(t["role"] in ("user", "assistant") for t in hist)


def test_legitimate_path_excludes_current_turn_by_key(isolated_settings):
    """The current turn (matched by client_message_id) is excluded from the
    folded history — behavior preserved through the fix."""
    member = "member-" + uuid.uuid4().hex[:8]
    company_id, ws_id = _seed_company_ws(user_id=member, slug="acme-cur")

    project = projects_db.create_project(
        company_id=company_id, workspace_id=ws_id, name="Cur project", created_by=member
    )
    conv = conversations_db.create_group_chat(project["id"], member)
    conversations_db.post_group_turn(conv["id"], member, "earlier turn")
    conversations_db.post_group_turn(
        conv["id"], member, "the current question", client_message_id="cmid-123"
    )

    hist = _load_group_history(conv["id"], project["id"], member, "cmid-123")
    joined = " ".join(t["content"] for t in hist)
    assert "earlier turn" in joined
    assert "the current question" not in joined, "current turn must be excluded from history"


# ── Belt-and-suspenders: list_group_turns project scope ─────────────────


def test_list_group_turns_project_scope_blocks_foreign(isolated_settings):
    """`list_group_turns` with an explicit foreign `project_id` returns `[]`
    even given a valid group conversation_id — the second layer of defense."""
    u = "u-" + uuid.uuid4().hex[:8]
    company_id, ws_id = _seed_company_ws(user_id=u, slug="acme-scope")
    a = _seed_project_with_group(
        company_id=company_id, workspace_id=ws_id, user_id=u,
        name="Project A", turns=["a-turn"],
    )
    b = _seed_project_with_group(
        company_id=company_id, workspace_id=ws_id, user_id=u,
        name="Project B", turns=["b-turn"],
    )

    # Correct binding: B's conv scoped to B's project → returns B's turns.
    scoped_ok = conversations_db.list_group_turns(b.conv["id"], project_id=b.project["id"])
    assert [t["content"] for t in scoped_ok] == ["b-turn"]

    # Wrong binding: B's conv scoped to A's project → [].
    scoped_bad = conversations_db.list_group_turns(b.conv["id"], project_id=a.project["id"])
    assert scoped_bad == []

    # Default (no project_id) preserves legacy behavior for existing callers.
    assert [t["content"] for t in conversations_db.list_group_turns(b.conv["id"])] == ["b-turn"]

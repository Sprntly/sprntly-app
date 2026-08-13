"""Tests for `app.db.projects.resolve_candidate` — the tenant-scoped,
fail-closed tier classifier (AD-TNM1) the tag-non-members loop is built on.

Pure unit tests, same posture as `test_resolve_member.py`: every composed
helper (`get_project`, `list_members`, `is_project_member`,
`list_workspace_members`, `get_workspace_member`, `list_company_members`,
`get_member`, `user_id_for_email`, `owning_company_domain`,
`email_belongs_to_other_company`) is monkeypatched directly on
`app.db.projects`, so no real Supabase connection is needed. `resolve_member`
itself is left to run FOR REAL wherever a test does not override it — it is
composed over the same stubbed `list_members`/`is_project_member`, so the
t_member tier is exercised end to end rather than re-stubbed.
"""
from __future__ import annotations

from app.db import projects as projects_db

PROJECT_ID = 101
COMPANY_ID = "company-a"
WORKSPACE_ID = "ws-a"

PROJECT = {"id": PROJECT_ID, "company_id": COMPANY_ID, "workspace_id": WORKSPACE_ID}

ROSTER = [
    {
        "user_id": "u-fortune",
        "kind": "human",
        "name": "Fortune Adeyemi",
        "email": "fortune@example.com",
        "avatar_url": None,
        "job_role": "Designer",
        "added_at": "2026-01-01T00:00:00Z",
    },
]


def _base_stubs(monkeypatch, *, project=PROJECT, roster=None, is_member=True):
    """Wires every dependency `resolve_candidate` composes to a deterministic
    default: this project's roster/tenancy, no workspace/company directory
    matches, no existing account for any email, and this company's owning
    domain is "example.com". Individual tests override only what they need."""
    roster = ROSTER if roster is None else roster
    monkeypatch.setattr(
        projects_db, "get_project", lambda pid: project if pid == PROJECT_ID else None
    )
    monkeypatch.setattr(
        projects_db, "list_members", lambda pid: roster if pid == PROJECT_ID else []
    )
    monkeypatch.setattr(projects_db, "is_project_member", lambda pid, uid: is_member)
    monkeypatch.setattr(projects_db, "list_workspace_members", lambda wid: [])
    monkeypatch.setattr(projects_db, "list_company_members", lambda cid: [])
    monkeypatch.setattr(projects_db, "get_workspace_member", lambda wid, uid: None)
    monkeypatch.setattr(
        projects_db, "get_member", lambda *, company_id, user_id: None
    )
    monkeypatch.setattr(projects_db, "user_id_for_email", lambda email: None)
    monkeypatch.setattr(
        projects_db,
        "owning_company_domain",
        lambda cid: "example.com" if cid == COMPANY_ID else None,
    )
    monkeypatch.setattr(
        projects_db,
        "email_belongs_to_other_company",
        lambda *, company_id, email: False,
    )


# ── Tier classification (fast lane) ──────────────────────────────────────


def test_project_member_by_name_returns_t_member(monkeypatch):
    """AC1: a roster name match resolves via the real `resolve_member`."""
    _base_stubs(monkeypatch)
    out = projects_db.resolve_candidate(PROJECT_ID, "fortune")
    assert out == {"tier": "t_member", "member": ROSTER[0]}


def test_project_member_by_email_returns_t_member(monkeypatch):
    """AC1: `resolve_member` matches only name/job_role (`_match_keys` never
    reads `email`) — this proves `resolve_candidate` independently checks
    the roster's email column, not that it leans on `resolve_member` for it."""
    _base_stubs(monkeypatch)
    out = projects_db.resolve_candidate(PROJECT_ID, "Fortune@Example.com")
    assert out == {"tier": "t_member", "member": ROSTER[0]}


def test_workspace_member_not_on_project_t_workspace(monkeypatch):
    """AC2: a name resolving to a `workspace_members` row of the project's
    workspace (not on the project) -> t_workspace with ids sourced from the
    matched directory row."""
    _base_stubs(monkeypatch)
    ws_row = {
        "id": "wm-1",
        "user_id": "u-priya",
        "role": "member",
        "created_at": "2026-01-01T00:00:00Z",
        "display_name": "Priya Shah",
        "email": "Priya@Example.com",
        "avatar_url": None,
    }
    monkeypatch.setattr(
        projects_db, "list_workspace_members", lambda wid: [ws_row] if wid == WORKSPACE_ID else []
    )
    monkeypatch.setattr(
        projects_db,
        "get_workspace_member",
        lambda wid, uid: ws_row if (wid, uid) == (WORKSPACE_ID, "u-priya") else None,
    )

    out = projects_db.resolve_candidate(PROJECT_ID, "priya")
    assert out == {
        "tier": "t_workspace",
        "user_id": "u-priya",
        "email": "priya@example.com",
        "name": "Priya Shah",
    }


def test_company_member_not_in_workspace_t_company(monkeypatch):
    """AC3: a name resolving to a `company_members` row of the project's
    company, NOT in the project's workspace -> t_company. The widened search
    falls through workspace (empty) to the company directory."""
    _base_stubs(monkeypatch)
    co_row = {
        "id": "cm-1",
        "user_id": "u-jordan",
        "role": "member",
        "created_at": "2026-01-01T00:00:00Z",
        "display_name": "Jordan Lee",
        "email": "Jordan@Example.com",
        "avatar_url": None,
        "job_role": None,
    }
    monkeypatch.setattr(
        projects_db, "list_company_members", lambda cid: [co_row] if cid == COMPANY_ID else []
    )
    monkeypatch.setattr(
        projects_db,
        "get_member",
        lambda *, company_id, user_id: co_row
        if (company_id, user_id) == (COMPANY_ID, "u-jordan")
        else None,
    )

    out = projects_db.resolve_candidate(PROJECT_ID, "jordan")
    assert out == {
        "tier": "t_company",
        "user_id": "u-jordan",
        "email": "jordan@example.com",
        "name": "Jordan Lee",
    }


def test_new_user_matching_domain_t_newuser(monkeypatch):
    """AC4, AC9: no account, domain == the company's owning domain, and not
    an other-company email -> t_newuser with the email normalized lower."""
    _base_stubs(monkeypatch)
    out = projects_db.resolve_candidate(PROJECT_ID, "New.Person@Example.COM")
    assert out == {"tier": "t_newuser", "email": "new.person@example.com"}


# ── Cross-tenant / refuse (the load-bearing tests) ───────────────────────


def test_foreign_domain_email_t_refuse_cross_company(monkeypatch):
    """AC5a: an email at a domain != the company's owning domain refuses
    cross_company (no account exists, so the domain gate is the deciding
    check)."""
    _base_stubs(monkeypatch)
    out = projects_db.resolve_candidate(PROJECT_ID, "outsider@foreign.com")
    assert out == {"tier": "t_refuse", "reason": "cross_company"}


def test_other_company_account_t_refuse_other_company(monkeypatch):
    """AC5b, AC5d: an email OR a name resolving to a real account that is
    NOT in this project's workspace/company refuses other_company — never
    t_workspace/t_company."""
    _base_stubs(monkeypatch)
    monkeypatch.setattr(
        projects_db,
        "user_id_for_email",
        lambda email: "u-foreign" if email.lower() == "foreign@other.com" else None,
    )
    out = projects_db.resolve_candidate(PROJECT_ID, "foreign@other.com")
    assert out == {"tier": "t_refuse", "reason": "other_company"}
    assert out["tier"] not in ("t_workspace", "t_company")

    # A NAME needle that resolves (via the widened directory search) to the
    # SAME real account must refuse identically: the live get_workspace_member/
    # get_member gate — not the list that produced the candidate — decides
    # the tier (AD-TNM1).
    foreign_row = {
        "id": "wm-x",
        "user_id": "u-foreign",
        "role": "member",
        "created_at": "2026-01-01T00:00:00Z",
        "display_name": "Foreign Person",
        "email": "foreign@other.com",
        "avatar_url": None,
    }
    monkeypatch.setattr(
        projects_db, "list_workspace_members", lambda wid: [foreign_row] if wid == WORKSPACE_ID else []
    )
    out2 = projects_db.resolve_candidate(PROJECT_ID, "Foreign Person")
    assert out2 == {"tier": "t_refuse", "reason": "other_company"}


def test_name_needle_no_in_tenant_match_t_refuse_no_match(monkeypatch):
    """AC5c: an unknown name refuses no_match and performs NO cross-company
    or global directory read — `user_id_for_email`, `owning_company_domain`,
    and `email_belongs_to_other_company` are never called for a NAME needle."""
    _base_stubs(monkeypatch)
    global_calls: list = []
    monkeypatch.setattr(
        projects_db, "user_id_for_email", lambda email: global_calls.append(("email", email)) or None
    )
    monkeypatch.setattr(
        projects_db,
        "email_belongs_to_other_company",
        lambda **kw: global_calls.append(("other_company", kw)) or False,
    )
    monkeypatch.setattr(
        projects_db,
        "owning_company_domain",
        lambda cid: global_calls.append(("domain", cid)) or "example.com",
    )

    out = projects_db.resolve_candidate(PROJECT_ID, "nobody-here")
    assert out == {"tier": "t_refuse", "reason": "no_match"}
    assert global_calls == []


def test_workspace_tier_gate_mutation_proof(monkeypatch):
    """AC6 (load-bearing): stubbing the live `get_workspace_member` check to
    always-true makes a cross-tenant account (wrongly) classify as
    t_workspace; the REAL live check keeps it at t_refuse — proving the
    tenancy assertion, not the match set, is what closes the tier."""
    _base_stubs(monkeypatch)
    monkeypatch.setattr(
        projects_db,
        "user_id_for_email",
        lambda email: "u-foreign" if email.lower() == "foreign@other.com" else None,
    )

    # RED (real check): not a member of this tenant anywhere -> refused.
    out_real = projects_db.resolve_candidate(PROJECT_ID, "foreign@other.com")
    assert out_real == {"tier": "t_refuse", "reason": "other_company"}

    # GREEN (mutated): always-true stub flips the cross-tenant account into
    # t_workspace.
    monkeypatch.setattr(projects_db, "get_workspace_member", lambda wid, uid: {"id": "stub"})
    out_stubbed = projects_db.resolve_candidate(PROJECT_ID, "foreign@other.com")
    assert out_stubbed["tier"] == "t_workspace"

    # Restore: the real (falsy) check is what closes it back down.
    monkeypatch.setattr(projects_db, "get_workspace_member", lambda wid, uid: None)
    out_restored = projects_db.resolve_candidate(PROJECT_ID, "foreign@other.com")
    assert out_restored == {"tier": "t_refuse", "reason": "other_company"}


def test_missing_project_t_refuse_no_project(monkeypatch):
    """AC7: a falsy `get_project` short-circuits to t_refuse(no_project) and
    reads NO membership table — every other composed helper is stubbed to
    record a call if it is ever reached."""
    reads: list[str] = []
    monkeypatch.setattr(projects_db, "get_project", lambda pid: None)
    monkeypatch.setattr(projects_db, "list_members", lambda pid: reads.append("list_members") or [])
    monkeypatch.setattr(
        projects_db,
        "resolve_member",
        lambda pid, needle: reads.append("resolve_member") or {"status": "no_match", "roster": []},
    )
    monkeypatch.setattr(
        projects_db, "list_workspace_members", lambda wid: reads.append("list_workspace_members") or []
    )
    monkeypatch.setattr(
        projects_db, "list_company_members", lambda cid: reads.append("list_company_members") or []
    )
    monkeypatch.setattr(
        projects_db, "get_workspace_member", lambda wid, uid: reads.append("get_workspace_member") or None
    )
    monkeypatch.setattr(
        projects_db,
        "get_member",
        lambda *, company_id, user_id: reads.append("get_member") or None,
    )
    monkeypatch.setattr(
        projects_db, "user_id_for_email", lambda email: reads.append("user_id_for_email") or None
    )
    monkeypatch.setattr(
        projects_db,
        "owning_company_domain",
        lambda cid: reads.append("owning_company_domain") or None,
    )
    monkeypatch.setattr(
        projects_db,
        "email_belongs_to_other_company",
        lambda **kw: reads.append("email_belongs_to_other_company") or False,
    )

    out = projects_db.resolve_candidate(999, "anyone@example.com")
    assert out == {"tier": "t_refuse", "reason": "no_project"}
    assert reads == []


def test_ambiguous_name_and_empty_needle_refused(monkeypatch):
    """AC8: >1 in-tenant directory match refuses ambiguous, never guesses;
    an empty/whitespace needle refuses no_match."""
    _base_stubs(monkeypatch)
    dup_a = {
        "id": "wm-1",
        "user_id": "u-a",
        "role": "member",
        "created_at": "2026-01-01T00:00:00Z",
        "display_name": "Alex Wong",
        "email": "alex1@example.com",
        "avatar_url": None,
    }
    dup_b = {
        "id": "wm-2",
        "user_id": "u-b",
        "role": "member",
        "created_at": "2026-01-01T00:00:00Z",
        "display_name": "Alex Nguyen",
        "email": "alex2@example.com",
        "avatar_url": None,
    }
    monkeypatch.setattr(projects_db, "list_workspace_members", lambda wid: [dup_a, dup_b])

    out = projects_db.resolve_candidate(PROJECT_ID, "alex")
    assert out == {"tier": "t_refuse", "reason": "ambiguous"}

    for needle in ("", "   "):
        out_empty = projects_db.resolve_candidate(PROJECT_ID, needle)
        assert out_empty == {"tier": "t_refuse", "reason": "no_match"}, f"needle={needle!r}"


def test_resolve_member_ambiguous_maps_to_refuse(monkeypatch):
    """AC8: a `resolve_member` `ambiguous` status maps straight to
    t_refuse(ambiguous) — the picker disambiguates upstream, the resolver
    never guesses."""
    _base_stubs(monkeypatch)
    monkeypatch.setattr(
        projects_db,
        "resolve_member",
        lambda pid, needle: {"status": "ambiguous", "candidates": ROSTER},
    )
    out = projects_db.resolve_candidate(PROJECT_ID, "alex")
    assert out == {"tier": "t_refuse", "reason": "ambiguous"}


# ── Cost / purity ─────────────────────────────────────────────────────────


def test_no_llm_call_no_write(monkeypatch):
    """AC10: no Anthropic entry point exists on the module (mirrors
    `resolve_member`'s own purity test), and `resolve_candidate` never
    touches `require_client()` directly across any tier — it composes only
    already-stubbed READ helpers, so this also proves it issues zero
    mutating table calls."""
    assert not hasattr(projects_db, "call_json")
    assert not hasattr(projects_db, "call_md")

    class _NoDBClient:
        def table(self, name):  # pragma: no cover - only reached on a regression
            raise AssertionError(
                f"resolve_candidate touched require_client().table({name!r}) "
                "directly instead of going through a composed helper"
            )

    monkeypatch.setattr(projects_db, "require_client", lambda: _NoDBClient())
    _base_stubs(monkeypatch)

    assert projects_db.resolve_candidate(PROJECT_ID, "fortune")["tier"] == "t_member"

    ws_row = {"user_id": "u-priya", "display_name": "Priya Shah", "email": "priya@example.com"}
    monkeypatch.setattr(projects_db, "list_workspace_members", lambda wid: [ws_row])
    monkeypatch.setattr(
        projects_db, "get_workspace_member", lambda wid, uid: ws_row if uid == "u-priya" else None
    )
    assert projects_db.resolve_candidate(PROJECT_ID, "priya")["tier"] == "t_workspace"

    monkeypatch.setattr(projects_db, "list_workspace_members", lambda wid: [])
    monkeypatch.setattr(projects_db, "get_workspace_member", lambda wid, uid: None)
    co_row = {"user_id": "u-jordan", "display_name": "Jordan Lee", "email": "jordan@example.com"}
    monkeypatch.setattr(projects_db, "list_company_members", lambda cid: [co_row])
    monkeypatch.setattr(
        projects_db, "get_member", lambda *, company_id, user_id: co_row if user_id == "u-jordan" else None
    )
    assert projects_db.resolve_candidate(PROJECT_ID, "jordan")["tier"] == "t_company"

    monkeypatch.setattr(projects_db, "list_company_members", lambda cid: [])
    monkeypatch.setattr(projects_db, "get_member", lambda *, company_id, user_id: None)
    assert projects_db.resolve_candidate(PROJECT_ID, "new.person@example.com")["tier"] == "t_newuser"
    assert projects_db.resolve_candidate(PROJECT_ID, "outsider@foreign.com")["tier"] == "t_refuse"
    assert projects_db.resolve_candidate(999, "anyone@example.com") == {
        "tier": "t_refuse",
        "reason": "no_project",
    }


# ── CI-lane registry ───────────────────────────────────────────────────────


def test_ci_lane_registry_has_resolve_candidate_live():
    """Backstop: `test_resolve_candidate_live.py`'s env gate must be
    registered in `_KNOWN_UNRUNNABLE`, or `test_ci_lane_coverage.py`'s
    `test_no_test_is_gated_on_an_env_var_no_workflow_provides` ratchet goes
    red — removing the entry reproduces that failure."""
    from tests import test_ci_lane_coverage as ci_lane

    assert (
        "test_resolve_candidate_live.py",
        "RUN_RESOLVE_CANDIDATE_LIVE",
    ) in ci_lane._KNOWN_UNRUNNABLE

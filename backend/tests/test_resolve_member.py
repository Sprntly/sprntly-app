"""Tests for `app.db.projects.resolve_member` — the deterministic,
roster-constrained, fail-closed assignee resolver (AD-P18 fast-path).

Pure unit tests: `list_members` and `is_project_member` are monkeypatched
directly on `app.db.projects` (same pattern `test_project_group_gate.py`
uses for `list_members`), so no real Supabase connection is needed to
exercise the matcher, disambiguation, isolation, or membership-recheck
logic. `resolve_member` composes those two functions and nothing else —
it never touches `require_client()` directly.
"""
from __future__ import annotations

from app.db import projects as projects_db

PROJECT_A = 101
PROJECT_B = 202

_ROSTER_A = [
    {
        "user_id": "u-fortune",
        "kind": "human",
        "name": "Fortune Adeyemi",
        "email": "fortune@example.com",
        "avatar_url": None,
        "job_role": "Designer",
        "added_at": "2026-01-01T00:00:00Z",
    },
    {
        "user_id": "u-alex-w",
        "kind": "human",
        "name": "Alex Wong",
        "email": "alexw@example.com",
        "avatar_url": None,
        "job_role": "Engineer",
        "added_at": "2026-01-01T00:00:00Z",
    },
]

_ROSTER_B = [
    {
        "user_id": "u-zola",
        "kind": "human",
        "name": "Zola Ngcobo",
        "email": "zola@example.com",
        "avatar_url": None,
        "job_role": "Designer",
        "added_at": "2026-01-01T00:00:00Z",
    },
]


def _stub_roster(monkeypatch, by_project: dict[int, list[dict]]) -> None:
    monkeypatch.setattr(
        projects_db, "list_members", lambda project_id: by_project.get(project_id, [])
    )


def _stub_membership(monkeypatch, *, result: bool = True) -> list[tuple]:
    calls: list[tuple] = []

    def _fake(project_id, user_id):
        calls.append((project_id, user_id))
        return result

    monkeypatch.setattr(projects_db, "is_project_member", _fake)
    return calls


# ── Resolution (happy path) ──────────────────────────────────────────────


def test_resolve_exact_first_name(monkeypatch):
    """AC1: "fortune" resolves to Fortune Adeyemi's user_id."""
    _stub_roster(monkeypatch, {PROJECT_A: _ROSTER_A})
    _stub_membership(monkeypatch, result=True)

    out = projects_db.resolve_member(PROJECT_A, "fortune")
    assert out["status"] == "resolved"
    assert out["member"]["user_id"] == "u-fortune"


def test_resolve_by_job_role_single(monkeypatch):
    """AC2: "designer" with exactly one Designer resolves."""
    _stub_roster(monkeypatch, {PROJECT_A: _ROSTER_A})
    _stub_membership(monkeypatch, result=True)

    out = projects_db.resolve_member(PROJECT_A, "designer")
    assert out["status"] == "resolved"
    assert out["member"]["user_id"] == "u-fortune"


def test_resolve_strips_leading_at(monkeypatch):
    """AC3: "@fortune" resolves identically to "fortune"."""
    _stub_roster(monkeypatch, {PROJECT_A: _ROSTER_A})
    _stub_membership(monkeypatch, result=True)

    out = projects_db.resolve_member(PROJECT_A, "@fortune")
    assert out["status"] == "resolved"
    assert out["member"]["user_id"] == "u-fortune"


def test_resolve_prefix_single_match(monkeypatch):
    """Prefix tier (len>=2, exact tier empty, one hit) resolves."""
    _stub_roster(monkeypatch, {PROJECT_A: _ROSTER_A})
    _stub_membership(monkeypatch, result=True)

    out = projects_db.resolve_member(PROJECT_A, "fort")
    assert out["status"] == "resolved"
    assert out["member"]["user_id"] == "u-fortune"


# ── Fail-closed disambiguation ────────────────────────────────────────────


def test_no_match_returns_roster(monkeypatch):
    """AC4: a needle matching nobody returns no_match with the full roster,
    never a resolved member."""
    _stub_roster(monkeypatch, {PROJECT_A: _ROSTER_A})
    _stub_membership(monkeypatch, result=True)

    out = projects_db.resolve_member(PROJECT_A, "zola")
    assert out["status"] == "no_match"
    assert out["roster"] == _ROSTER_A
    assert "member" not in out


def test_ambiguous_two_designers(monkeypatch):
    """AC5: two Designers + needle "designer" -> ambiguous with both."""
    roster = _ROSTER_A + [
        {
            "user_id": "u-second-designer",
            "kind": "human",
            "name": "Priya Shah",
            "email": "priya@example.com",
            "avatar_url": None,
            "job_role": "Designer",
            "added_at": "2026-01-01T00:00:00Z",
        }
    ]
    _stub_roster(monkeypatch, {PROJECT_A: roster})
    membership_calls = _stub_membership(monkeypatch, result=True)

    out = projects_db.resolve_member(PROJECT_A, "designer")
    assert out["status"] == "ambiguous"
    ids = {c["user_id"] for c in out["candidates"]}
    assert ids == {"u-fortune", "u-second-designer"}
    # Ambiguity never reaches the membership re-check — no resolved id to
    # verify.
    assert membership_calls == []


def test_ambiguous_same_first_name(monkeypatch):
    """AC5: two "Alex ..." members + needle "alex" -> ambiguous."""
    roster = [
        {
            "user_id": "u-alex-1",
            "kind": "human",
            "name": "Alex Wong",
            "email": "alex1@example.com",
            "avatar_url": None,
            "job_role": "Engineer",
            "added_at": "2026-01-01T00:00:00Z",
        },
        {
            "user_id": "u-alex-2",
            "kind": "human",
            "name": "Alex Nguyen",
            "email": "alex2@example.com",
            "avatar_url": None,
            "job_role": "PM",
            "added_at": "2026-01-01T00:00:00Z",
        },
    ]
    _stub_roster(monkeypatch, {PROJECT_A: roster})
    _stub_membership(monkeypatch, result=True)

    out = projects_db.resolve_member(PROJECT_A, "alex")
    assert out["status"] == "ambiguous"
    ids = {c["user_id"] for c in out["candidates"]}
    assert ids == {"u-alex-1", "u-alex-2"}


def test_blank_and_single_char_needle(monkeypatch):
    """AC8: "", "  ", and a 1-char needle with no exact hit -> no_match,
    never an over-broad prefix match."""
    _stub_roster(monkeypatch, {PROJECT_A: _ROSTER_A})
    _stub_membership(monkeypatch, result=True)

    for needle in ("", "  ", "f"):
        out = projects_db.resolve_member(PROJECT_A, needle)
        assert out["status"] == "no_match", f"needle={needle!r}"
        assert out["roster"] == _ROSTER_A


# ── Isolation (mutation-proofed at ship gate) ─────────────────────────────


def test_resolve_isolated_to_project(monkeypatch):
    """AC6: a member of project B is never resolved when resolving for
    project A (candidates come only from project A's list_members)."""
    _stub_roster(monkeypatch, {PROJECT_A: _ROSTER_A, PROJECT_B: _ROSTER_B})
    _stub_membership(monkeypatch, result=True)

    # "zola" is a project-B member only; resolving against project A must
    # be a no_match, not a cross-project resolution.
    out = projects_db.resolve_member(PROJECT_A, "zola")
    assert out["status"] == "no_match"
    assert out["roster"] == _ROSTER_A

    # Sanity: the same needle DOES resolve for project B, proving the
    # roster source is genuinely project-scoped, not a global miss.
    out_b = projects_db.resolve_member(PROJECT_B, "zola")
    assert out_b["status"] == "resolved"
    assert out_b["member"]["user_id"] == "u-zola"


def test_membership_recheck_fails_closed(monkeypatch):
    """AC7 (mutation-proofed): forced `is_project_member=False` on an
    otherwise-matched candidate returns no_match, not resolved. Flipping
    the gate back to True on the SAME input resolves — proving the
    re-check is load-bearing, not dead code."""
    _stub_roster(monkeypatch, {PROJECT_A: _ROSTER_A})

    _stub_membership(monkeypatch, result=False)
    out = projects_db.resolve_member(PROJECT_A, "fortune")
    assert out["status"] == "no_match"
    assert "member" not in out

    _stub_membership(monkeypatch, result=True)
    out2 = projects_db.resolve_member(PROJECT_A, "fortune")
    assert out2["status"] == "resolved"
    assert out2["member"]["user_id"] == "u-fortune"


# ── Determinism ────────────────────────────────────────────────────────


def test_resolve_no_llm_and_deterministic(monkeypatch):
    """AC9: no LLM/network call beyond the roster read; two identical
    calls with the same roster + needle return equal results. Asserts no
    LLM entry point is even imported into this module's namespace, since
    the function makes no such call."""
    assert not hasattr(projects_db, "call_json")
    assert not hasattr(projects_db, "call_md")

    _stub_roster(monkeypatch, {PROJECT_A: _ROSTER_A})
    _stub_membership(monkeypatch, result=True)

    out1 = projects_db.resolve_member(PROJECT_A, "fortune")
    out2 = projects_db.resolve_member(PROJECT_A, "fortune")
    assert out1 == out2

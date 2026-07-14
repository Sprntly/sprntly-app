"""Persisted PRD→tickets cache: the content hash that decides freshness, and the
GET /v1/stories/for-prd/{prd_id} read that serves the cache vs. signals regen.

DB access is monkeypatched — these never reach Supabase.
"""
from __future__ import annotations

import pytest

from app.auth import CompanyContext
from app.db import prd_tickets
from app.routes import stories


def _ctx(cid: str = "ent-A") -> CompanyContext:
    return CompanyContext(company_id=cid, role="owner", user_id="u")


# ── content hash ──────────────────────────────────────────────────────────────
def test_hash_is_stable_and_content_sensitive():
    base = {"title": "Onboarding", "payload_md": "Body A", "llm_part": "Spec B"}
    h = prd_tickets.hash_prd_row(base)
    # Stable: same content → same hash.
    assert h == prd_tickets.hash_prd_row(dict(base))
    # Sensitive: any part changing flips the hash.
    assert h != prd_tickets.hash_prd_row({**base, "title": "Onboarding!"})
    assert h != prd_tickets.hash_prd_row({**base, "payload_md": "Body A2"})
    assert h != prd_tickets.hash_prd_row({**base, "llm_part": "Spec B2"})


def test_hash_no_field_collision_across_boundaries():
    # The \x1f separator must stop "AB"+"" from colliding with "A"+"B".
    a = {"title": "A", "payload_md": "B", "llm_part": ""}
    b = {"title": "AB", "payload_md": "", "llm_part": ""}
    assert prd_tickets.hash_prd_row(a) != prd_tickets.hash_prd_row(b)


# ── prd_hash_matches — freshness incl. the impl-spec pre-warm hash ───────────
def test_hash_matches_full_current_row(monkeypatch):
    prd = {"title": "T", "payload_md": "Body", "llm_part": "Spec"}
    monkeypatch.setattr(prd_tickets, "get_prd_rendered", lambda pid: prd)
    assert prd_tickets.prd_hash_matches(42, prd_tickets.hash_prd_row(prd)) is True


def test_hash_matches_accepts_pre_impl_spec_warm_hash(monkeypatch):
    """A set stored BEFORE the background impl-spec warm landed carries the
    empty-llm_part hash. Body unchanged → still fresh; the warm alone must not
    read as 'the PRD changed' (the race that regenerated tickets every open)."""
    prd = {"title": "T", "payload_md": "Body", "llm_part": "Spec landed later"}
    monkeypatch.setattr(prd_tickets, "get_prd_rendered", lambda pid: prd)
    pre_warm_hash = prd_tickets.hash_prd_row({**prd, "llm_part": ""})
    assert prd_tickets.prd_hash_matches(42, pre_warm_hash) is True


def test_hash_matches_false_on_real_body_change(monkeypatch):
    prd = {"title": "T", "payload_md": "Body EDITED", "llm_part": "Spec"}
    monkeypatch.setattr(prd_tickets, "get_prd_rendered", lambda pid: prd)
    old = prd_tickets.hash_prd_row({"title": "T", "payload_md": "Body", "llm_part": "Spec"})
    assert prd_tickets.prd_hash_matches(42, old) is False


def test_hash_matches_false_on_missing_prd_or_hash(monkeypatch):
    monkeypatch.setattr(prd_tickets, "get_prd_rendered", lambda pid: None)
    assert prd_tickets.prd_hash_matches(42, "anything") is False
    monkeypatch.setattr(
        prd_tickets, "get_prd_rendered", lambda pid: {"title": "T", "payload_md": "B"}
    )
    assert prd_tickets.prd_hash_matches(42, None) is False


# ── GET /for-prd freshness ──────────────────────────────────────────────────────
def test_for_prd_none_when_no_row(monkeypatch):
    monkeypatch.setattr(prd_tickets, "get_tickets", lambda cid, pid: None)
    monkeypatch.setattr(prd_tickets, "prd_hash_matches", lambda pid, h: True)
    out = stories.tickets_for_prd(42, _ctx())
    assert out == {"status": "none", "fresh": False, "stories": []}


def test_for_prd_fresh_when_hash_matches(monkeypatch):
    row = {"status": "ready", "content_hash": "h1",
           "stories": [{"title": "T1"}], "generated_at": "2026-06-25T00:00:00Z"}
    monkeypatch.setattr(prd_tickets, "get_tickets", lambda cid, pid: row)
    monkeypatch.setattr(prd_tickets, "prd_hash_matches", lambda pid, h: h == "h1")
    out = stories.tickets_for_prd(42, _ctx())
    assert out["fresh"] is True
    assert out["status"] == "ready"
    assert out["stories"] == [{"title": "T1"}]


def test_for_prd_stale_when_hash_differs(monkeypatch):
    row = {"status": "ready", "content_hash": "old", "stories": [{"title": "T1"}]}
    monkeypatch.setattr(prd_tickets, "get_tickets", lambda cid, pid: row)
    monkeypatch.setattr(prd_tickets, "prd_hash_matches", lambda pid, h: False)
    out = stories.tickets_for_prd(42, _ctx())
    assert out["fresh"] is False  # PRD changed → frontend regenerates


def test_for_prd_not_fresh_when_status_failed(monkeypatch):
    # Even with a matching hash, a failed row must not be served as fresh.
    row = {"status": "failed", "content_hash": "h", "stories": []}
    monkeypatch.setattr(prd_tickets, "get_tickets", lambda cid, pid: row)
    monkeypatch.setattr(prd_tickets, "prd_hash_matches", lambda pid, h: True)
    out = stories.tickets_for_prd(42, _ctx())
    assert out["fresh"] is False

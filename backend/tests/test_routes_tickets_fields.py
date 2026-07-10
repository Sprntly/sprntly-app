"""Tests for per-ticket editable metadata: PUT /v1/tickets/{key}/fields and its
round-trip through GET /v1/tickets/{key}/data.

The detail view persists title / priority / status / sprint / assignee onto the
same `ticket_edits` row that holds description + acceptance criteria. The key
invariant is that a partial save (e.g. just the priority picker) updates only
what was sent and never clobbers the description or the other fields.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests._company_helpers import company_client


@pytest.fixture
def client(isolated_settings, monkeypatch) -> TestClient:
    """Bearer-authed TestClient with a seeded company (require_company path)."""
    return company_client(monkeypatch).client


KEY = "prd-7-guest-alert-data-model"


def test_fields_roundtrip(client: TestClient):
    assignee = {
        "user_id": "u-1", "display_name": "Neville Crawley",
        "email": "neville@slickdeals.net", "role": "Product", "avatar_url": None,
    }
    resp = client.put(f"/v1/tickets/{KEY}/fields", json={
        "title": "Guest alert data model · schema & migration",
        "priority": "P0 — Critical",
        "status": "Backlog",
        "sprint": "Sprint 25",
        "assignee": assignee,
    })
    assert resp.status_code == 200, resp.text

    data = client.get(f"/v1/tickets/{KEY}/data").json()
    assert data["title"] == "Guest alert data model · schema & migration"
    assert data["priority"] == "P0 — Critical"
    assert data["status"] == "Backlog"
    assert data["sprint"] == "Sprint 25"
    assert data["assignee"] == assignee


def test_partial_fields_save_preserves_description(client: TestClient):
    # Seed a description first.
    client.put(f"/v1/tickets/{KEY}/description", json={
        "description": "One-click guest-alert for Deal Alerts.",
        "acceptance_criteria": ["Admin can enable in one click"],
    })
    # Partial fields saves (priority, then status) must not wipe description/AC
    # or each other.
    client.put(f"/v1/tickets/{KEY}/fields", json={"priority": "P1 — High"})
    client.put(f"/v1/tickets/{KEY}/fields", json={"status": "In progress"})

    data = client.get(f"/v1/tickets/{KEY}/data").json()
    assert data["description"] == "One-click guest-alert for Deal Alerts."
    assert data["acceptance_criteria"] == ["Admin can enable in one click"]
    assert data["priority"] == "P1 — High"   # earlier partial save survived
    assert data["status"] == "In progress"


def test_data_defaults_when_no_edits(client: TestClient):
    data = client.get("/v1/tickets/prd-7-never-touched/data").json()
    assert data["title"] is None
    assert data["priority"] is None
    assert data["assignee"] is None
    assert data["subtasks"] is None  # no override → UI keeps generated child issues
    assert data["attachments"] == []
    assert data["comments"] == []


def test_subtasks_roundtrip_and_partial_save_preserves_them(client: TestClient):
    """Child issues persist as an override and survive later partial saves;
    an explicit [] is a real override (clear all), distinct from None."""
    resp = client.put(f"/v1/tickets/{KEY}/fields", json={
        "subtasks": ["[P] Write migration", "Wire the endpoint"],
    })
    assert resp.status_code == 200, resp.text

    data = client.get(f"/v1/tickets/{KEY}/data").json()
    assert data["subtasks"] == ["[P] Write migration", "Wire the endpoint"]

    # A later status-only save must not clobber the subtasks override.
    client.put(f"/v1/tickets/{KEY}/fields", json={"status": "In progress"})
    data = client.get(f"/v1/tickets/{KEY}/data").json()
    assert data["subtasks"] == ["[P] Write migration", "Wire the endpoint"]

    # Explicit clear-all round-trips as [] (an override), not None.
    client.put(f"/v1/tickets/{KEY}/fields", json={"subtasks": []})
    assert client.get(f"/v1/tickets/{KEY}/data").json()["subtasks"] == []


def test_fields_only_edit_leaves_description_null(client: TestClient):
    # Regression: editing only a field (status/assignee) must NOT fabricate an
    # empty description/criteria that would blank out the generated ticket body.
    client.put(f"/v1/tickets/{KEY}/fields", json={"status": "In progress"})
    data = client.get(f"/v1/tickets/{KEY}/data").json()
    assert data["status"] == "In progress"
    assert data["description"] is None          # not "" → UI keeps the generated body
    assert data["acceptance_criteria"] is None  # not [] → UI keeps the generated AC


def test_comment_author_resolved_from_session_not_client(isolated_settings, monkeypatch):
    """A comment is attributed to the signed-in user (profile name → email →
    'user'); a client-supplied author is ignored — except the 'Sprntly'
    system author used by Accept & propagate notes."""
    ctx = company_client(monkeypatch)
    db = isolated_settings["supabase"]
    db.table("profiles").insert({"id": ctx.user_id, "full_name": "Ada Lovelace"}).execute()

    spoofed = ctx.client.post(
        f"/v1/tickets/{KEY}/comments", json={"author": "Mallory", "body": "hi"}
    ).json()
    assert spoofed["author"] == "Ada Lovelace"

    omitted = ctx.client.post(f"/v1/tickets/{KEY}/comments", json={"body": "again"}).json()
    assert omitted["author"] == "Ada Lovelace"

    system = ctx.client.post(
        f"/v1/tickets/{KEY}/comments", json={"author": "Sprntly", "body": "✳ propagated"}
    ).json()
    assert system["author"] == "Sprntly"


def test_comment_summary_needs_two_comments(client: TestClient):
    # 0 comments → null, no LLM call.
    assert client.get(f"/v1/tickets/{KEY}/comments/summary").json()["summary"] is None
    client.post(f"/v1/tickets/{KEY}/comments", json={"author": "Sam", "body": "Ship behind a flag?"})
    # Still < 2 → null.
    assert client.get(f"/v1/tickets/{KEY}/comments/summary").json()["summary"] is None


def test_comment_summary_calls_llm(client: TestClient, monkeypatch):
    import app.routes.tickets as tickets_mod
    seen = {}

    def fake_call_json(*, system, user, **kwargs):
        seen["system"] = system
        seen["user"] = user
        return {
            "summary": "Team aligned to ship behind a flag; open question on step 3.",
            "proposed_criterion": None,
        }

    monkeypatch.setattr(tickets_mod, "call_json", fake_call_json)

    client.post(f"/v1/tickets/{KEY}/comments", json={"author": "Sam", "body": "Ship behind a flag?"})
    client.post(f"/v1/tickets/{KEY}/comments", json={"author": "Lee", "body": "Yes, flag it; step 3 still open."})

    out = client.get(f"/v1/tickets/{KEY}/comments/summary").json()
    assert out["summary"] == "Team aligned to ship behind a flag; open question on step 3."
    assert out["proposed_criterion"] is None
    # The thread (author: body lines) was handed to the model. Authors are
    # resolved server-side from the session (no profile/email seeded → "user"),
    # never taken from the client — the spoofed "Sam"/"Lee" don't appear.
    assert "user: Ship behind a flag?" in seen["user"]
    assert "user: Yes, flag it; step 3 still open." in seen["user"]
    assert "Sam:" not in seen["user"]


def test_comment_summary_surfaces_proposed_criterion(client: TestClient, monkeypatch):
    """When the thread proposes a concrete AC change, the endpoint returns the
    exact Given/When/Then rule the change loop's Accept & propagate will apply."""
    import app.routes.tickets as tickets_mod

    def fake_call_json(*, system, user, **kwargs):
        return {
            "summary": "Agreed to add a 30-day staleness rule.",
            "proposed_criterion": "[failure] Given the card is older than 30 days, When opened, Then a staleness banner appears.",
        }

    monkeypatch.setattr(tickets_mod, "call_json", fake_call_json)

    client.post(f"/v1/tickets/{KEY}/comments", json={"author": "Priya", "body": "Competitor facts rot — add a freshness rule."})
    client.post(f"/v1/tickets/{KEY}/comments", json={"author": "Sam", "body": "Agreed, 30-day staleness."})

    out = client.get(f"/v1/tickets/{KEY}/comments/summary").json()
    assert "staleness" in out["summary"]
    assert out["proposed_criterion"].startswith("[failure]")


def test_generated_story_has_stable_content_id():
    from app.stories.generate import Story
    a = Story(title="Guest alert", body="one click").to_dict()
    b = Story(title="Guest alert", body="one click").to_dict()
    c = Story(title="Guest alert", body="different body").to_dict()
    assert a["id"] and a["id"] == b["id"]   # same content → same id (survives reorder/regen)
    assert a["id"] != c["id"]               # different content → different id (no misattach)

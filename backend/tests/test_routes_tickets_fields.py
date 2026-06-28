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
    assert data["attachments"] == []
    assert data["comments"] == []


def test_fields_only_edit_leaves_description_null(client: TestClient):
    # Regression: editing only a field (status/assignee) must NOT fabricate an
    # empty description/criteria that would blank out the generated ticket body.
    client.put(f"/v1/tickets/{KEY}/fields", json={"status": "In progress"})
    data = client.get(f"/v1/tickets/{KEY}/data").json()
    assert data["status"] == "In progress"
    assert data["description"] is None          # not "" → UI keeps the generated body
    assert data["acceptance_criteria"] is None  # not [] → UI keeps the generated AC


def test_comment_summary_needs_two_comments(client: TestClient):
    # 0 comments → null, no LLM call.
    assert client.get(f"/v1/tickets/{KEY}/comments/summary").json()["summary"] is None
    client.post(f"/v1/tickets/{KEY}/comments", json={"author": "Sam", "body": "Ship behind a flag?"})
    # Still < 2 → null.
    assert client.get(f"/v1/tickets/{KEY}/comments/summary").json()["summary"] is None


def test_comment_summary_calls_llm(client: TestClient, monkeypatch):
    import app.routes.tickets as tickets_mod
    seen = {}

    def fake_call_md(*, system, user, **kwargs):
        seen["system"] = system
        seen["user"] = user
        return "Team aligned to ship behind a flag; open question on step 3."

    monkeypatch.setattr(tickets_mod, "call_md", fake_call_md)

    client.post(f"/v1/tickets/{KEY}/comments", json={"author": "Sam", "body": "Ship behind a flag?"})
    client.post(f"/v1/tickets/{KEY}/comments", json={"author": "Lee", "body": "Yes, flag it; step 3 still open."})

    out = client.get(f"/v1/tickets/{KEY}/comments/summary").json()
    assert out["summary"] == "Team aligned to ship behind a flag; open question on step 3."
    # The thread (author: body lines) was handed to the model.
    assert "Sam: Ship behind a flag?" in seen["user"]
    assert "Lee:" in seen["user"]


def test_generated_story_has_stable_content_id():
    from app.stories.generate import Story
    a = Story(title="Guest alert", body="one click").to_dict()
    b = Story(title="Guest alert", body="one click").to_dict()
    c = Story(title="Guest alert", body="different body").to_dict()
    assert a["id"] and a["id"] == b["id"]   # same content → same id (survives reorder/regen)
    assert a["id"] != c["id"]               # different content → different id (no misattach)

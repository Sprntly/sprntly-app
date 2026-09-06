"""Tests for the project candidate picker's pending-invite state:

  - `db/team.py::list_pending_invite_emails` — company-wide, lower-cased,
    expiry-derived-at-read (no status column on `workspace_invites`; pending
    means the row exists, expired-by-age is derived from
    `created_at + settings.invite_expiry_days`).
  - `GET /v1/projects/{id}/candidates` now also returns `pending_invites`
    alongside `candidates` (`routes/projects.py::candidate_search_route`).

The frontend "Invited" vs "Added" render logic is covered separately in
`web/app/components/screens/app/projects/__tests__/ProjectInviteModal.dom.
test.tsx`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import team as team_db
from app.db.client import require_client
from tests._company_helpers import company_client


def _iso_days_ago(days: float) -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(days=days))
        .replace(microsecond=0)
        .isoformat()
    )


def _seed_invite_row(
    *, invite_id: str, company_id: str, email: str, created_days_ago: float = 0,
    project_id: int | None = None,
) -> None:
    """Insert a `workspace_invites` row directly (bypassing `create_invite`
    so the test controls `created_at` precisely for expiry assertions)."""
    payload = {
        "id": invite_id,
        "company_id": company_id,
        "email": email,
        "role": "member",
        "invited_by": "inviter-1",
        "created_at": _iso_days_ago(created_days_ago),
        "workspace_ids": [],
    }
    if project_id is not None:
        payload["project_id"] = project_id
    require_client().table("workspace_invites").insert(payload).execute()


def _new_project(ctx, name: str = "Launch") -> dict:
    r = ctx.client.post("/v1/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


# ── db/team.py::list_pending_invite_emails ──────────────────────────────


def test_list_pending_invite_emails_lowercases_and_is_company_wide(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_invite_row(invite_id="inv-1", company_id=ctx.company_id, email="Mixed.Case@Acme.example")

    out = team_db.list_pending_invite_emails(ctx.company_id)
    assert out == ["mixed.case@acme.example"]

    # Company-wide by construction: passing an arbitrary project_id changes
    # nothing (see the helper's own docstring — `(company_id, email)` is
    # UNIQUE on the table, so there is only ever one row to find).
    assert team_db.list_pending_invite_emails(ctx.company_id, project_id=999) == out


def test_list_pending_invite_emails_excludes_expired_by_age(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    # Default invite_expiry_days is 30 — 31 days old is past it.
    _seed_invite_row(invite_id="inv-old", company_id=ctx.company_id, email="stale@acme.example", created_days_ago=31)
    _seed_invite_row(invite_id="inv-fresh", company_id=ctx.company_id, email="fresh@acme.example", created_days_ago=1)

    out = team_db.list_pending_invite_emails(ctx.company_id)
    assert out == ["fresh@acme.example"]


def test_list_pending_invite_emails_respects_configured_expiry(isolated_settings, monkeypatch):
    from app import config as config_mod

    ctx = company_client(monkeypatch)
    monkeypatch.setattr(config_mod.settings, "invite_expiry_days", 5, raising=False)
    _seed_invite_row(invite_id="inv-6d", company_id=ctx.company_id, email="six@acme.example", created_days_ago=6)
    _seed_invite_row(invite_id="inv-4d", company_id=ctx.company_id, email="four@acme.example", created_days_ago=4)

    out = team_db.list_pending_invite_emails(ctx.company_id)
    assert out == ["four@acme.example"]


def test_list_pending_invite_emails_reflects_accept_deleting_the_row(isolated_settings, monkeypatch):
    """No status column — "accepted" is just "the row is gone". Deleting it
    (what the real accept flow does, `db/team.py::delete_invite`) removes the
    email from the pending set with no other code change needed."""
    ctx = company_client(monkeypatch)
    _seed_invite_row(invite_id="inv-accept", company_id=ctx.company_id, email="joins@acme.example")
    assert team_db.list_pending_invite_emails(ctx.company_id) == ["joins@acme.example"]

    team_db.delete_invite("inv-accept")
    assert team_db.list_pending_invite_emails(ctx.company_id) == []


def test_list_pending_invite_emails_empty_when_no_invites(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    assert team_db.list_pending_invite_emails(ctx.company_id) == []


# ── GET /v1/projects/{id}/candidates — pending_invites field ────────────


def test_candidates_route_returns_pending_invites_alongside_candidates(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    _seed_invite_row(
        invite_id="inv-route-1", company_id=ctx.company_id, email="Invited.Person@Acme.example",
        project_id=project["id"],
    )

    r = ctx.client.get(f"/v1/projects/{project['id']}/candidates?q=")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "candidates" in body
    assert body["pending_invites"] == ["invited.person@acme.example"]


def test_candidates_route_excludes_expired_pending_invite(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    _seed_invite_row(
        invite_id="inv-route-old", company_id=ctx.company_id, email="gone@acme.example",
        created_days_ago=45,
    )

    r = ctx.client.get(f"/v1/projects/{project['id']}/candidates?q=")
    assert r.status_code == 200, r.text
    assert r.json()["pending_invites"] == []


def test_candidates_route_pending_email_is_not_a_candidate_but_is_pending(isolated_settings, monkeypatch):
    """A brand-new email invite (no `profiles` row at all — the invitee has
    never signed up) never appears in `candidates` — the picker's only
    signal for it is `pending_invites`, exactly the by-email row's use
    case."""
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    _seed_invite_row(invite_id="inv-brand-new", company_id=ctx.company_id, email="brand.new@example.com")

    r = ctx.client.get(f"/v1/projects/{project['id']}/candidates?q=")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "brand.new@example.com" in body["pending_invites"]
    assert all(c.get("email") != "brand.new@example.com" for c in body["candidates"])


def test_candidates_route_never_leaks_a_foreign_company_pending_invite(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    require_client().table("companies").insert(
        {"id": "other-co", "slug": "other-co", "display_name": "Other Co"}
    ).execute()
    _seed_invite_row(invite_id="inv-foreign", company_id="other-co", email="outsider@evil.example")

    r = ctx.client.get(f"/v1/projects/{project['id']}/candidates?q=")
    assert r.status_code == 200, r.text
    assert "outsider@evil.example" not in r.json()["pending_invites"]

"""Staff admin panel — org invites + per-company entitlements.

Covers:
  * require_staff gate: 401 unauth; 404 for non-staff / empty allowlist;
    200 for allowlisted staff (email resolved via profiles fallback).
  * GET/PATCH /v1/staff/companies — entitlement snapshot + partial edits
    (feature_flags merge, explicit seat_limit null clears the limit).
  * /v1/staff/invites CRUD — create (email best-effort), duplicate-pending
    409, revoke, resend.
  * POST /v1/org-invites/claim — applies the invite's entitlements to the
    owner's company, settles the invite, is a 404 no-op afterwards, and is
    owner-only.
  * Seat-limit enforcement on POST /v1/team/invites (members + pending
    invites vs companies.seat_limit; NULL = unlimited).
  * Per-company prototype gate helpers (design agent /generate + /locate).
"""
from __future__ import annotations

import uuid

import pytest

import app.auth  # noqa: F401 — ensure app.config/app.auth in sys.modules

from tests._company_helpers import company_client

STAFF_EMAIL = "staff@sprntly.ai"


def _db():
    from app.db.client import require_client

    return require_client()


def _seed_profile_email(user_id: str, email: str) -> None:
    """The minted test JWT carries no `email` claim, so require_staff resolves
    the caller's email via the profiles fallback — seed it."""
    db = _db()
    if db.table("profiles").select("id").eq("id", user_id).execute().data:
        db.table("profiles").update({"email": email}).eq("id", user_id).execute()
    else:
        db.table("profiles").insert({"id": user_id, "email": email}).execute()


def _staff_ctx(monkeypatch, *, email: str = STAFF_EMAIL, allowlist: str | None = None):
    """A bearer-authed client whose user is on the STAFF_EMAILS allowlist."""
    ctx = company_client(monkeypatch)
    _seed_profile_email(ctx.user_id, email)
    import app.auth as auth_mod

    monkeypatch.setattr(
        auth_mod.settings, "staff_emails", allowlist if allowlist is not None else STAFF_EMAIL
    )
    return ctx


def _no_email_sends(monkeypatch):
    import app.routes.staff_admin as staff_mod

    sent: list[str] = []

    def _fake_send(email: str) -> str:
        sent.append(email)
        return "sent"

    monkeypatch.setattr(staff_mod, "send_invite_email", _fake_send)
    return sent


# ─────────────────────── require_staff gate ───────────────────────


def test_staff_routes_require_auth(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    ctx.client.headers.pop("Authorization")
    assert ctx.client.get("/v1/staff/companies").status_code == 401


def test_staff_routes_404_when_allowlist_empty(isolated_settings, monkeypatch):
    ctx = _staff_ctx(monkeypatch, allowlist="")
    assert ctx.client.get("/v1/staff/companies").status_code == 404


def test_staff_routes_404_for_non_staff(isolated_settings, monkeypatch):
    ctx = _staff_ctx(monkeypatch, email="customer@acme.com")
    assert ctx.client.get("/v1/staff/companies").status_code == 404
    assert ctx.client.get("/v1/staff/invites").status_code == 404
    assert (
        ctx.client.post(
            "/v1/staff/invites",
            json={"email": "a@b.com", "company_name": "B"},
        ).status_code
        == 404
    )


def test_staff_allowlist_is_case_insensitive_and_multi(isolated_settings, monkeypatch):
    ctx = _staff_ctx(
        monkeypatch,
        email=STAFF_EMAIL,
        allowlist=f"Other@sprntly.ai,  {STAFF_EMAIL.upper()} ",
    )
    assert ctx.client.get("/v1/staff/companies").status_code == 200


# ─────────────────────── companies list + entitlement edits ───────────────────────


def test_staff_lists_companies_with_counts(isolated_settings, monkeypatch):
    ctx = _staff_ctx(monkeypatch)
    r = ctx.client.get("/v1/staff/companies")
    assert r.status_code == 200, r.text
    rows = r.json()["companies"]
    mine = next(c for c in rows if c["id"] == ctx.company_id)
    assert mine["member_count"] == 1
    assert mine["pending_invite_count"] == 0
    assert mine["seat_limit"] is None
    assert mine["llm_key_configured"] is False


def test_staff_patch_entitlements_roundtrip(isolated_settings, monkeypatch):
    ctx = _staff_ctx(monkeypatch)
    _db().table("companies").update(
        {"feature_flags": {"weekly_brief": True}}
    ).eq("id", ctx.company_id).execute()

    r = ctx.client.patch(
        f"/v1/staff/companies/{ctx.company_id}",
        json={
            "seat_limit": 5,
            "prototype_enabled": True,
            "use_platform_key": True,
            "feature_flags": {"research_agent": True},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["seat_limit"] == 5
    assert body["prototype_enabled"] is True
    assert body["use_platform_key"] is True
    # feature_flags is a partial MERGE — pre-existing keys survive.
    assert body["feature_flags"] == {"weekly_brief": True, "research_agent": True}

    # Explicit null clears the seat limit (unlimited).
    r = ctx.client.patch(
        f"/v1/staff/companies/{ctx.company_id}", json={"seat_limit": None}
    )
    assert r.status_code == 200
    assert r.json()["seat_limit"] is None
    # …and leaves the other entitlements untouched.
    assert r.json()["prototype_enabled"] is True


def test_staff_patch_unknown_company_404(isolated_settings, monkeypatch):
    ctx = _staff_ctx(monkeypatch)
    r = ctx.client.patch(
        f"/v1/staff/companies/{uuid.uuid4().hex}", json={"prototype_enabled": True}
    )
    assert r.status_code == 404


# ─────────────────────── org invites CRUD ───────────────────────


def test_staff_invite_create_list_revoke_resend(isolated_settings, monkeypatch):
    ctx = _staff_ctx(monkeypatch)
    sent = _no_email_sends(monkeypatch)

    r = ctx.client.post(
        "/v1/staff/invites",
        json={
            "email": "Admin@Customer.com",
            "company_name": "Customer Inc",
            "seat_limit": 3,
            "prototype_enabled": True,
            "use_platform_key": True,
            "feature_flags": {"weekly_brief": True},
        },
    )
    assert r.status_code == 201, r.text
    invite = r.json()
    assert invite["email"] == "admin@customer.com"  # normalized
    assert invite["status"] == "pending"
    assert invite["email_sent"] is True
    assert sent == ["admin@customer.com"]

    # Duplicate pending invite for the same email → 409.
    r = ctx.client.post(
        "/v1/staff/invites",
        json={"email": "admin@customer.com", "company_name": "Customer Inc"},
    )
    assert r.status_code == 409

    rows = ctx.client.get("/v1/staff/invites").json()["invites"]
    assert [i["id"] for i in rows] == [invite["id"]]

    r = ctx.client.post(f"/v1/staff/invites/{invite['id']}/resend")
    assert r.status_code == 200
    assert sent == ["admin@customer.com", "admin@customer.com"]

    assert ctx.client.delete(f"/v1/staff/invites/{invite['id']}").status_code == 204
    # Revoked ⇒ no longer pending: revoke/resend now 404, email freed for reuse.
    assert ctx.client.delete(f"/v1/staff/invites/{invite['id']}").status_code == 404
    assert (
        ctx.client.post(f"/v1/staff/invites/{invite['id']}/resend").status_code == 404
    )
    r = ctx.client.post(
        "/v1/staff/invites",
        json={"email": "admin@customer.com", "company_name": "Customer Inc"},
    )
    assert r.status_code == 201


def test_staff_invite_validation(isolated_settings, monkeypatch):
    ctx = _staff_ctx(monkeypatch)
    assert (
        ctx.client.post(
            "/v1/staff/invites",
            json={"email": "not-an-email", "company_name": "X"},
        ).status_code
        == 422
    )
    assert (
        ctx.client.post(
            "/v1/staff/invites",
            json={"email": "a@b.com", "company_name": "   "},
        ).status_code
        == 422
    )


# ─────────────────────── claim ───────────────────────


def _seed_org_invite(email: str, **overrides) -> dict:
    from app.db.org_invites import create_org_invite

    defaults = dict(
        email=email,
        company_name="Customer Inc",
        invited_by=None,
        seat_limit=4,
        prototype_enabled=True,
        use_platform_key=True,
        feature_flags={"research_agent": True},
    )
    defaults.update(overrides)
    return create_org_invite(**defaults)


def test_claim_applies_entitlements_and_settles_invite(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_profile_email(ctx.user_id, "owner@customer.com")
    invite = _seed_org_invite("owner@customer.com")

    r = ctx.client.post("/v1/org-invites/claim")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is True
    ent = body["entitlements"]
    assert ent["seat_limit"] == 4
    assert ent["prototype_enabled"] is True
    assert ent["use_platform_key"] is True
    assert ent["feature_flags"]["research_agent"] is True

    from app.db.org_invites import get_org_invite

    settled = get_org_invite(invite["id"])
    assert settled["status"] == "accepted"
    assert settled["company_id"] == ctx.company_id

    # Second claim: the invite is settled — nothing pending → 404.
    assert ctx.client.post("/v1/org-invites/claim").status_code == 404


def test_claim_404_without_pending_invite(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_profile_email(ctx.user_id, "owner@nobody.com")
    assert ctx.client.post("/v1/org-invites/claim").status_code == 404


def test_claim_is_owner_only(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_profile_email(ctx.user_id, "member@customer.com")
    _seed_org_invite("member@customer.com")
    _db().table("company_members").update({"role": "admin"}).eq(
        "company_id", ctx.company_id
    ).execute()
    assert ctx.client.post("/v1/org-invites/claim").status_code == 403


# ─────────────────────── seat-limit enforcement ───────────────────────


def _set_seat_limit(company_id: str, limit: int | None) -> None:
    _db().table("companies").update({"seat_limit": limit}).eq(
        "id", company_id
    ).execute()


def test_team_invite_blocked_at_seat_limit(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_seat_limit(ctx.company_id, 1)  # the owner occupies the only seat
    r = ctx.client.post("/v1/team/invites", json={"email": "new@co.com"})
    assert r.status_code == 403
    assert "seat" in r.json()["detail"].lower() or "member" in r.json()["detail"].lower()


def test_team_invite_pending_invites_reserve_seats(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_seat_limit(ctx.company_id, 2)  # owner + 1 free seat
    assert (
        ctx.client.post("/v1/team/invites", json={"email": "a@co.com"}).status_code
        == 201
    )
    # The pending invite reserves the second seat.
    assert (
        ctx.client.post("/v1/team/invites", json={"email": "b@co.com"}).status_code
        == 403
    )


def test_team_invite_unlimited_when_no_seat_limit(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/team/invites", json={"email": "any@co.com"})
    assert r.status_code == 201


# ─────────────────────── per-company prototype gate ───────────────────────


def test_prototype_enabled_helper_policy(isolated_settings, monkeypatch):
    """Lenient on missing data (grandfather posture), strict on explicit false."""
    from app.db.companies import prototype_enabled_for_company

    ctx = company_client(monkeypatch)

    # No column value on the row (fake schema) → enabled.
    assert prototype_enabled_for_company(ctx.company_id) is True
    # Missing row entirely → enabled (the env master gate still applies).
    assert prototype_enabled_for_company(uuid.uuid4().hex) is True

    _db().table("companies").update({"prototype_enabled": False}).eq(
        "id", ctx.company_id
    ).execute()
    assert prototype_enabled_for_company(ctx.company_id) is False

    _db().table("companies").update({"prototype_enabled": True}).eq(
        "id", ctx.company_id
    ).execute()
    assert prototype_enabled_for_company(ctx.company_id) is True


def test_design_agent_company_gate_raises_404(isolated_settings, monkeypatch):
    from fastapi import HTTPException

    import app.routes.design_agent as da

    monkeypatch.setattr(
        "app.db.companies.prototype_enabled_for_company", lambda _cid: False
    )
    with pytest.raises(HTTPException) as ei:
        da._require_company_prototype_enabled("co-x")
    assert ei.value.status_code == 404

    monkeypatch.setattr(
        "app.db.companies.prototype_enabled_for_company", lambda _cid: True
    )
    da._require_company_prototype_enabled("co-x")  # no raise

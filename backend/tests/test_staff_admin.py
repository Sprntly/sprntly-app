"""Staff admin panel — org invites + per-company entitlements.

Covers:
  * The dedicated-credential login (POST /v1/staff/login): success mints a
    12h staff JWT that passes require_staff; wrong id / wrong password both
    401 with the SAME generic message (no enumeration); unset
    STAFF_ADMIN_ID / STAFF_ADMIN_PASSWORD_HASH ⇒ 404 everywhere, login
    included (fail closed, invisible).
  * require_staff gate: 404 for no token, a Supabase USER token, a demo/app
    session token (same signing secret, wrong audience), and an expired
    staff token — the surface is invisible to anything but a live staff JWT.
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

import time
import uuid

import jwt as pyjwt
import pytest
from argon2 import PasswordHasher

import app.auth  # noqa: F401 — ensure app.config/app.auth in sys.modules

from tests._company_helpers import company_client

STAFF_ID = "sprntly-owner"
STAFF_PASSWORD = "correct-horse-battery-staple"
# argon2id is deliberately slow — hash once at import, reuse in every test.
STAFF_PASSWORD_HASH = PasswordHasher().hash(STAFF_PASSWORD)


def _db():
    from app.db.client import require_client

    return require_client()


def _seed_profile_email(user_id: str, email: str) -> None:
    """The minted test JWT carries no `email` claim, so the claim route
    resolves the caller's email via the profiles fallback — seed it."""
    db = _db()
    if db.table("profiles").select("id").eq("id", user_id).execute().data:
        db.table("profiles").update({"email": email}).eq("id", user_id).execute()
    else:
        db.table("profiles").insert({"id": user_id, "email": email}).execute()


def _enable_staff_surface(
    monkeypatch,
    *,
    admin_id: str = STAFF_ID,
    password_hash: str = STAFF_PASSWORD_HASH,
):
    """Configure the dedicated staff credential (STAFF_ADMIN_ID/…_HASH).

    Must run AFTER company_client() — that helper reloads app.config/app.auth,
    which would discard an earlier settings patch."""
    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod.settings, "staff_admin_id", admin_id)
    monkeypatch.setattr(
        auth_mod.settings, "staff_admin_password_hash", password_hash
    )


def _login(ctx, *, admin_id: str = STAFF_ID, password: str = STAFF_PASSWORD):
    return ctx.client.post(
        "/v1/staff/login", json={"id": admin_id, "password": password}
    )


def _staff_ctx(monkeypatch):
    """A client authed with a freshly minted staff JWT (dedicated login)."""
    ctx = company_client(monkeypatch)
    _enable_staff_surface(monkeypatch)
    r = _login(ctx)
    assert r.status_code == 200, r.text
    ctx.client.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return ctx


def _no_email_sends(monkeypatch):
    import app.routes.staff_admin as staff_mod

    sent: list[str] = []

    def _fake_send(email: str) -> str:
        sent.append(email)
        return "sent"

    monkeypatch.setattr(staff_mod, "send_invite_email", _fake_send)
    return sent


# ─────────────────────── dedicated login ───────────────────────


def test_staff_login_token_passes_staff_gate(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _enable_staff_surface(monkeypatch)

    r = _login(ctx)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 12 * 3600

    ctx.client.headers["Authorization"] = f"Bearer {body['token']}"
    assert ctx.client.get("/v1/staff/companies").status_code == 200


def test_staff_login_bad_credentials_401_generic(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _enable_staff_surface(monkeypatch)

    wrong_id = _login(ctx, admin_id="somebody-else")
    wrong_pw = _login(ctx, password="nope")
    assert wrong_id.status_code == 401
    assert wrong_pw.status_code == 401
    # Same generic message either way — no way to enumerate the id.
    assert wrong_id.json()["detail"] == wrong_pw.json()["detail"]


def test_staff_surface_404_when_env_unset(isolated_settings, monkeypatch):
    """Either env var missing ⇒ 404 everywhere, login included (fail closed)."""
    ctx = company_client(monkeypatch)

    for admin_id, pw_hash in (
        ("", ""),
        (STAFF_ID, ""),
        ("", STAFF_PASSWORD_HASH),
    ):
        _enable_staff_surface(monkeypatch, admin_id=admin_id, password_hash=pw_hash)
        assert _login(ctx).status_code == 404
        assert ctx.client.get("/v1/staff/companies").status_code == 404
        assert ctx.client.get("/v1/staff/invites").status_code == 404


# ─────────────────────── require_staff gate ───────────────────────


def test_staff_routes_404_without_token(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _enable_staff_surface(monkeypatch)
    ctx.client.headers.pop("Authorization")
    assert ctx.client.get("/v1/staff/companies").status_code == 404


def test_staff_routes_404_for_supabase_user_token(isolated_settings, monkeypatch):
    """A signed-in customer's Supabase JWT never passes the staff gate —
    the surface stays invisible to every normal user."""
    ctx = company_client(monkeypatch)  # client carries a valid Supabase bearer
    _enable_staff_surface(monkeypatch)
    assert ctx.client.get("/v1/staff/companies").status_code == 404
    assert ctx.client.get("/v1/staff/invites").status_code == 404
    assert (
        ctx.client.post(
            "/v1/staff/invites",
            json={"email": "a@b.com", "company_name": "B"},
        ).status_code
        == 404
    )


def test_staff_routes_404_for_wrong_audience_token(isolated_settings, monkeypatch):
    """A token signed with the SAME jwt_secret but a different audience (the
    demo/app session shape) must not pass — the aud claim is the isolation."""
    import app.auth as auth_mod

    ctx = company_client(monkeypatch)
    _enable_staff_surface(monkeypatch)
    now = int(time.time())
    imposter = pyjwt.encode(
        {"iat": now, "exp": now + 3600, "aud": "app", "scope": "app"},
        auth_mod.settings.jwt_secret,
        algorithm="HS256",
    )
    ctx.client.headers["Authorization"] = f"Bearer {imposter}"
    assert ctx.client.get("/v1/staff/companies").status_code == 404


def test_staff_routes_404_for_expired_staff_token(isolated_settings, monkeypatch):
    import app.auth as auth_mod

    ctx = company_client(monkeypatch)
    _enable_staff_surface(monkeypatch)
    now = int(time.time())
    expired = pyjwt.encode(
        {
            "sub": "staff",
            "role": "staff_admin",
            "aud": "sprntly-staff",
            "iat": now - 13 * 3600,
            "exp": now - 3600,
        },
        auth_mod.settings.jwt_secret,
        algorithm="HS256",
    )
    ctx.client.headers["Authorization"] = f"Bearer {expired}"
    assert ctx.client.get("/v1/staff/companies").status_code == 404


def test_staff_token_never_passes_user_gates(isolated_settings, monkeypatch):
    """The staff JWT must not be usable as a normal user session: aud and
    signing path differ from Supabase tokens, so require_session rejects it."""
    ctx = _staff_ctx(monkeypatch)
    # A tenant-scoped user route: the staff token is not a Supabase session.
    assert ctx.client.post("/v1/org-invites/claim").status_code in (401, 403)


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


def test_staff_invite_prototype_defaults_on(isolated_settings, monkeypatch):
    """An invite that never touches the Prototype toggle grants it: prototype
    is a default-ON module for every organization (the staff toggle is an
    opt-OUT). Regression for the pre-20260721130000 default-false, which made
    every un-ticked invite (and self-serve signup) silently 404 on
    /v1/design-agent/generate."""
    ctx = _staff_ctx(monkeypatch)
    _no_email_sends(monkeypatch)

    r = ctx.client.post(
        "/v1/staff/invites",
        json={"email": "defaults@customer.com", "company_name": "Customer Inc"},
    )
    assert r.status_code == 201, r.text
    invite = r.json()
    assert invite["prototype_enabled"] is True

    # An explicit opt-out still sticks.
    r = ctx.client.post(
        "/v1/staff/invites",
        json={
            "email": "optout@customer.com",
            "company_name": "Optout Inc",
            "prototype_enabled": False,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["prototype_enabled"] is False


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

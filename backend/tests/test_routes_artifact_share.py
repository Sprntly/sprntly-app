"""Route-level coverage for the artifact share-grant primitive: mint,
metadata, resolve (Group A), join, content (Group B).
"""
from __future__ import annotations

import importlib
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

from tests._company_helpers import (
    company_client,
    seed_company,
    setup_supabase_auth,
    supabase_bearer,
)


def _bare_client(monkeypatch, user_id: str) -> TestClient:
    """A TestClient authed as `user_id` with NO company_members row — the
    "fresh signup mid-flow" identity /resolve, /join, /content must support
    (require_session only, never require_company)."""
    setup_supabase_auth(monkeypatch)
    import app.main as main_mod

    importlib.reload(sys.modules["app.main"])
    return TestClient(main_mod.app, headers=supabase_bearer(user_id))


def _seed_prd(db, dataset: str, *, title: str = "t") -> int:
    brief_id = db.save_brief(dataset, "W", {"insights": []}, schema_version=1)
    return db.start_prd(
        brief_id=brief_id, insight_index=0, title=title, template_version=1, variant="v2"
    )


# ── Mint (AC1, AC2) ─────────────────────────────────────────────────────


def test_mint_route_returns_201_and_token(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    db = isolated_settings["db"]
    prd_id = _seed_prd(db, "acme")

    r = ctx.client.post(
        "/v1/artifact-share",
        json={"artifact_type": "prd", "artifact_id": prd_id},
        headers={"X-Workspace-Id": default_ws["id"]},
    )

    assert r.status_code == 201
    token = r.json()["token"]
    assert uuid.UUID(token).version == 4


def test_mint_route_denies_non_owned_prd(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    ctx.client.get("/v1/workspaces")
    db = isolated_settings["db"]
    seed_company(user_id="other-" + uuid.uuid4().hex[:8], slug="rival")
    foreign_prd_id = _seed_prd(db, "rival")

    r = ctx.client.post(
        "/v1/artifact-share", json={"artifact_type": "prd", "artifact_id": foreign_prd_id}
    )

    assert r.status_code == 404
    from app.db.client import require_client

    rows = (
        require_client()
        .table("artifact_shares")
        .select("*")
        .eq("artifact_id", foreign_prd_id)
        .execute()
        .data
    )
    assert rows == []


# ── Metadata (AC3, AC4) ──────────────────────────────────────────────────


def test_metadata_route_returns_fields(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    db = isolated_settings["db"]
    prd_id = _seed_prd(db, "acme", title="Great PRD")
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": ctx.user_id, "email": "owner@acme.com", "first_name": "Own", "last_name": "Er"}
    ).execute()
    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd", artifact_id=prd_id, owner_company_id=ctx.company_id,
        owner_workspace_id=default_ws["id"], created_by_user_id=ctx.user_id,
    )

    r = ctx.client.get(f"/v1/artifact-share/{share['token']}")

    assert r.status_code == 200
    body = r.json()
    assert body["artifact_type"] == "prd"
    assert body["title"] == "Great PRD"
    assert body["sharer_name"] == "Own Er"
    assert body["owning_company_name"] == "Acme"
    assert body["required_email_domain"] == "acme.com"


def test_metadata_route_same_404_shape_missing_and_revoked(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.db.artifact_shares import mint_share
    from app.db.client import require_client

    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=ctx.company_id,
        owner_workspace_id="ws-1", created_by_user_id=ctx.user_id,
    )
    require_client().table("artifact_shares").update({"revoked_at": "2020-01-01T00:00:00"}).eq(
        "id", share["id"]
    ).execute()

    r_revoked = ctx.client.get(f"/v1/artifact-share/{share['token']}")
    r_missing = ctx.client.get(f"/v1/artifact-share/{uuid.uuid4()}")

    assert r_revoked.status_code == 404
    assert r_missing.status_code == 404
    assert r_revoked.json() == r_missing.json()


# ── Resolve (AC5-8, AC16) ─────────────────────────────────────────────────


def test_resolve_route_same_company_different_workspace_guest_view(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=ctx.company_id,
        owner_workspace_id="some-other-ws", created_by_user_id=ctx.user_id,
    )

    r = ctx.client.get(f"/v1/artifact-share/{share['token']}/resolve")

    assert r.status_code == 200
    assert r.json()["outcome"] == "guest_view"


def test_resolve_route_returns_public_id_never_the_raw_id_alone(isolated_settings, monkeypatch):
    """A guest_view outcome must carry the PRD's opaque public_id — what
    postLoginPath()'s redirect and the sign-in page's own resolve both use
    instead of the raw sequential artifact_id, so a copied/bookmarked link
    never discloses a blind-enumerable id."""
    ctx = company_client(monkeypatch)
    db = isolated_settings["db"]
    from app.db.client import require_client
    from app.db.artifact_shares import mint_share
    import uuid

    brief_id = db.save_brief("acme", "W", {"insights": []}, schema_version=1)
    prd_id = db.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1, variant="v2"
    )
    public_id = str(uuid.uuid4())
    require_client().table("prds").update({"public_id": public_id}).eq("id", prd_id).execute()

    share = mint_share(
        artifact_type="prd", artifact_id=prd_id, owner_company_id=ctx.company_id,
        owner_workspace_id="ws-1", created_by_user_id=ctx.user_id,
    )

    r = ctx.client.get(f"/v1/artifact-share/{share['token']}/resolve")

    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "guest_view"
    assert body["artifact_id"] == prd_id
    assert body["public_id"] == public_id


def test_resolve_route_different_company_blocked(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    other_company = seed_company(user_id="other-" + uuid.uuid4().hex[:8], slug="rival2")
    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=other_company,
        owner_workspace_id="ws-1", created_by_user_id="creator",
    )

    r = ctx.client.get(f"/v1/artifact-share/{share['token']}/resolve")

    assert r.status_code == 200
    assert r.json() == {"outcome": "blocked", "reason": "different_company"}


def test_resolve_route_zero_company_matching_domain_now_blocked(isolated_settings, monkeypatch):
    """Revision 2026-08-02: a zero-membership caller is ALWAYS blocked, even
    with a matching email domain — see resolve_share_access's docstring."""
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme8")
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": creator_id, "email": "creator@acme8.com"}
    ).execute()
    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id="ws-1", created_by_user_id=creator_id,
    )

    fresh_user = "fresh-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": fresh_user, "email": "joiner@acme8.com"}
    ).execute()
    client = _bare_client(monkeypatch, fresh_user)

    r = client.get(f"/v1/artifact-share/{share['token']}/resolve")

    assert r.status_code == 200
    assert r.json() == {"outcome": "blocked", "reason": "different_company"}


def test_resolve_route_domain_mismatched_zero_company_blocked(isolated_settings, monkeypatch):
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme9")
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": creator_id, "email": "creator@acme9.com"}
    ).execute()
    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id="ws-1", created_by_user_id=creator_id,
    )

    fresh_user = "fresh-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": fresh_user, "email": "joiner@other.com"}
    ).execute()
    client = _bare_client(monkeypatch, fresh_user)

    r = client.get(f"/v1/artifact-share/{share['token']}/resolve")

    assert r.status_code == 200
    assert r.json() == {"outcome": "blocked", "reason": "different_company"}


def test_resolve_route_invalid_token_returns_404(isolated_settings, monkeypatch):
    client = _bare_client(monkeypatch, "whoever-" + uuid.uuid4().hex[:8])

    r = client.get(f"/v1/artifact-share/{uuid.uuid4()}/resolve")

    assert r.status_code == 404


# ── auto-join-company (AC-revision: signup-time company grant) ───────────


def test_auto_join_company_route_grants_on_matching_domain(isolated_settings, monkeypatch):
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme-route1")
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": creator_id, "email": "creator@acme-route1.com"}
    ).execute()
    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id="ws-1", created_by_user_id=creator_id,
    )
    fresh_user = "fresh-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": fresh_user, "email": "joiner@acme-route1.com"}
    ).execute()
    client = _bare_client(monkeypatch, fresh_user)

    r = client.post(f"/v1/artifact-share/{share['token']}/auto-join-company")

    assert r.status_code == 200
    assert r.json() == {"joined_company_id": company_id}


def test_auto_join_company_route_200_no_op_on_mismatched_domain(isolated_settings, monkeypatch):
    """No-op-success, never an error status — mirrors this router's
    non-disclosure convention (never signal via status code whether the
    token/domain was valid)."""
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme-route2")
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": creator_id, "email": "creator@acme-route2.com"}
    ).execute()
    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id="ws-1", created_by_user_id=creator_id,
    )
    fresh_user = "fresh-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": fresh_user, "email": "joiner@other.com"}
    ).execute()
    client = _bare_client(monkeypatch, fresh_user)

    r = client.post(f"/v1/artifact-share/{share['token']}/auto-join-company")

    assert r.status_code == 200
    assert r.json() == {"joined_company_id": None}


def test_auto_join_company_route_200_no_op_on_invalid_token(isolated_settings, monkeypatch):
    client = _bare_client(monkeypatch, "whoever-" + uuid.uuid4().hex[:8])

    r = client.post(f"/v1/artifact-share/{uuid.uuid4()}/auto-join-company")

    assert r.status_code == 200
    assert r.json() == {"joined_company_id": None}


def test_blocked_response_never_contains_artifact_title(isolated_settings, monkeypatch):
    """AC16 non-disclosure guard, mutation-proofed: a disposable
    (test-local only, never committed to product code) leaky reconstruction
    of the blocked-response body is proven to fail this file's own
    assertions (RED) before the real route (which never does this) is
    proven to pass the SAME assertions (GREEN) — so the assertions below
    are proven to actually catch a title leak, not just happen to pass."""
    from app.db import save_brief, start_prd
    from app.db.artifact_shares import mint_share, resolve_share_access
    from app.db.prds import get_prd_rendered

    other_company = seed_company(user_id="other-" + uuid.uuid4().hex[:8], slug="rival9")
    brief_id = save_brief("rival9", "W", {"insights": []}, schema_version=1)
    prd_id = start_prd(
        brief_id=brief_id, insight_index=0, title="Secret PRD Title",
        template_version=1, variant="v2",
    )
    share = mint_share(
        artifact_type="prd", artifact_id=prd_id, owner_company_id=other_company,
        owner_workspace_id="ws-1", created_by_user_id="creator",
    )

    # --- RED: a disposable leaky reconstruction fails the assertion ---
    outcome = resolve_share_access(token=share["token"], user_id="someone-else", user_email=None)
    assert outcome["outcome"] == "blocked"
    leaked_share = outcome["share"]
    leaky_body = {
        "outcome": outcome["outcome"],
        "reason": outcome["reason"],
        # The disposable mutation: a body-builder that (wrongly) includes the
        # artifact title on a blocked outcome — exactly what AC16 forbids.
        "title": (get_prd_rendered(leaked_share["artifact_id"]) or {}).get("title"),
    }
    with pytest.raises(AssertionError):
        assert "title" not in leaky_body
    with pytest.raises(AssertionError):
        assert "Secret PRD Title" not in str(leaky_body)

    # --- GREEN: the real route never leaks ---
    ctx = company_client(monkeypatch)
    r = ctx.client.get(f"/v1/artifact-share/{share['token']}/resolve")

    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "blocked"
    assert "title" not in body
    assert "Secret PRD Title" not in str(body)


# ── Join (AC9-13, AC17-adjacent attribution) ─────────────────────────────


def test_join_denies_blocked_outcome_with_no_writes(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    other_company = seed_company(user_id="other-" + uuid.uuid4().hex[:8], slug="rival10")
    from app.db.artifact_shares import mint_share
    from app.db.client import require_client

    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=other_company,
        owner_workspace_id="ws-1", created_by_user_id="creator",
    )

    r = ctx.client.post(f"/v1/artifact-share/{share['token']}/join")

    assert r.status_code == 403
    assert require_client().table("artifact_share_joins").select("*").execute().data == []
    other_members = (
        require_client().table("company_members").select("user_id")
        .eq("company_id", other_company).execute().data
    )
    assert ctx.user_id not in [m["user_id"] for m in other_members]
    ws_members = require_client().table("workspace_members").select("user_id").execute().data
    assert ctx.user_id not in [m["user_id"] for m in ws_members]


def test_join_denies_a_truly_fresh_zero_membership_caller_directly(isolated_settings, monkeypatch):
    """Revision 2026-08-02: /join never grants company membership itself —
    a genuinely fresh (zero-membership) caller who hits /join WITHOUT ever
    going through auto-join-company first is blocked, same as any other
    zero-membership caller, no writes. This is the direct replacement for
    the pre-revision test of the same name that expected a 200 + full
    Member grant — that flow is retired; auto-join-company is now the ONLY
    path to a fresh signup's company membership (see resolve_share_access's
    and _grant_workspace_membership's docstrings)."""
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme11")
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": creator_id, "email": "creator@acme11.com"}
    ).execute()
    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id="ws-1", created_by_user_id=creator_id,
    )
    fresh_user = "fresh-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": fresh_user, "email": "joiner@acme11.com"}
    ).execute()
    client = _bare_client(monkeypatch, fresh_user)

    r = client.post(f"/v1/artifact-share/{share['token']}/join")

    assert r.status_code == 403
    assert (
        require_client().table("company_members").select("*")
        .eq("user_id", fresh_user).execute().data == []
    )
    assert (
        require_client().table("artifact_share_joins").select("*")
        .eq("joined_user_id", fresh_user).execute().data == []
    )


def test_auto_join_then_join_end_to_end_grants_company_then_workspace_only(
    isolated_settings, monkeypatch
):
    """The full new signup flow, chained exactly as postLoginPath()'s guest
    branch does it: auto-join-company (grants COMPANY only) THEN /join
    (grants WORKSPACE only) — proving resolve_share_access's same_company
    branch really does fire naturally on the second call, end to end
    through the real routes, not just the db-helper unit test."""
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme11b")
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": creator_id, "email": "creator@acme11b.com"}
    ).execute()
    from app.db.artifact_shares import mint_share
    from app.db.workspaces import ensure_default_workspace

    default_ws = ensure_default_workspace(company_id)
    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id=default_ws["id"], created_by_user_id=creator_id,
    )
    fresh_user = "fresh-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": fresh_user, "email": "joiner@acme11b.com"}
    ).execute()
    client = _bare_client(monkeypatch, fresh_user)

    r_auto = client.post(f"/v1/artifact-share/{share['token']}/auto-join-company")
    assert r_auto.status_code == 200
    assert r_auto.json()["joined_company_id"] == company_id

    # Company membership is real now — but NOT workspace membership yet.
    members = (
        require_client().table("company_members").select("*")
        .eq("company_id", company_id).eq("user_id", fresh_user).execute().data
    )
    assert len(members) == 1
    assert members[0]["role"] == "member"
    assert (
        require_client().table("workspace_members").select("*")
        .eq("user_id", fresh_user).execute().data == []
    )

    # /resolve now sees the freshly-granted company membership and returns
    # guest_view via the same_company branch — no separate code path.
    r_resolve = client.get(f"/v1/artifact-share/{share['token']}/resolve")
    assert r_resolve.status_code == 200
    assert r_resolve.json()["outcome"] == "guest_view"

    r_join = client.post(f"/v1/artifact-share/{share['token']}/join")
    assert r_join.status_code == 200
    body = r_join.json()
    assert body["workspace_id"] == default_ws["id"]

    # Company membership is untouched by /join (still exactly one row).
    members_after = (
        require_client().table("company_members").select("*")
        .eq("company_id", company_id).eq("user_id", fresh_user).execute().data
    )
    assert len(members_after) == 1

    ws_members = (
        require_client().table("workspace_members").select("*")
        .eq("user_id", fresh_user).execute().data
    )
    assert len(ws_members) == 1
    assert ws_members[0]["role"] == "member"
    assert ws_members[0]["workspace_id"] == default_ws["id"]

    joins = (
        require_client().table("artifact_share_joins").select("*")
        .eq("joined_user_id", fresh_user).execute().data
    )
    assert len(joins) == 1


def test_join_same_company_different_workspace_creates_workspace_membership_only(
    isolated_settings, monkeypatch
):
    ctx = company_client(monkeypatch)
    ws2 = ctx.client.post("/v1/workspaces", json={"name": "Notifications"}).json()
    from app.db.artifact_shares import mint_share
    from app.db.client import require_client

    before_members = (
        require_client().table("company_members").select("*")
        .eq("company_id", ctx.company_id).execute().data
    )
    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=ctx.company_id,
        owner_workspace_id=ws2["id"], created_by_user_id=ctx.user_id,
    )

    r = ctx.client.post(f"/v1/artifact-share/{share['token']}/join")

    assert r.status_code == 200
    body = r.json()
    assert body["workspace_id"] == ws2["id"]

    after_members = (
        require_client().table("company_members").select("*")
        .eq("company_id", ctx.company_id).execute().data
    )
    assert len(after_members) == len(before_members)  # no NEW company_members row

    ws_members = (
        require_client().table("workspace_members").select("*")
        .eq("workspace_id", ws2["id"]).eq("user_id", ctx.user_id).execute().data
    )
    assert len(ws_members) == 1
    assert ws_members[0]["role"] == "member"


def test_join_idempotent_on_repeat_call(isolated_settings, monkeypatch):
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme12")
    from app.db.client import require_client
    from app.db.workspaces import ensure_default_workspace

    require_client().table("profiles").insert(
        {"id": creator_id, "email": "creator@acme12.com"}
    ).execute()
    # A REAL workspace (not a fabricated id): the second /join call resolves
    # this user as same-company (post-first-join) and re-grants into this
    # exact workspace — must be a genuine row or the FK on workspace_members
    # would reject it.
    default_ws = ensure_default_workspace(company_id)
    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id=default_ws["id"], created_by_user_id=creator_id,
    )
    fresh_user = "fresh-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": fresh_user, "email": "joiner@acme12.com"}
    ).execute()
    client = _bare_client(monkeypatch, fresh_user)

    # /join no longer grants company membership itself (revision 2026-08-02)
    # — the real flow always runs auto-join-company first, exactly as
    # postLoginPath()'s guest branch does.
    assert client.post(f"/v1/artifact-share/{share['token']}/auto-join-company").status_code == 200

    r1 = client.post(f"/v1/artifact-share/{share['token']}/join")
    r2 = client.post(f"/v1/artifact-share/{share['token']}/join")

    assert r1.status_code == 200
    assert r2.status_code == 200
    joins = (
        require_client().table("artifact_share_joins").select("*")
        .eq("joined_user_id", fresh_user).execute().data
    )
    assert len(joins) == 1


def test_join_invalidates_membership_cache(isolated_settings, monkeypatch):
    """AC13, mutation-proof: spies on the real `invalidate_user` (never a
    stub) to prove /join actually calls it, AND directly asserts the cache
    no longer returns the pre-join empty membership list — no sleep/TTL
    wait."""
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme13")
    from app.db.client import require_client
    from app.db.workspaces import ensure_default_workspace

    require_client().table("profiles").insert(
        {"id": creator_id, "email": "creator@acme13.com"}
    ).execute()
    from app.db.artifact_shares import mint_share

    # A REAL workspace: /join now always grants INTO share["owner_workspace_id"]
    # directly (never a freshly-created default) — must be a genuine row or
    # the workspace_members FK rejects it.
    default_ws = ensure_default_workspace(company_id)
    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id=default_ws["id"], created_by_user_id=creator_id,
    )
    fresh_user = "fresh-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": fresh_user, "email": "joiner@acme13.com"}
    ).execute()

    from app.db.companies import memberships_for_user

    # Warm-probe pre-join (empty — never actually cached per authcache's own
    # "never cache empty" invariant, but this mirrors the ticket's own
    # described sequence).
    assert memberships_for_user(fresh_user) == []

    import app.db.authcache as authcache_mod

    real_invalidate_user = authcache_mod.invalidate_user
    calls: list[str] = []

    def _spy(user_id: str) -> None:
        calls.append(user_id)
        real_invalidate_user(user_id)

    monkeypatch.setattr(authcache_mod, "invalidate_user", _spy)

    client = _bare_client(monkeypatch, fresh_user)
    # Company membership first (the real flow's auto-join-company step) —
    # /join itself only grants workspace membership now.
    assert client.post(f"/v1/artifact-share/{share['token']}/auto-join-company").status_code == 200
    r = client.post(f"/v1/artifact-share/{share['token']}/join")

    assert r.status_code == 200
    assert fresh_user in calls  # invalidate_user was actually invoked, not merely coincidentally cold
    assert memberships_for_user(fresh_user) != []  # resolves the NEW membership immediately


def test_join_records_attribution_row(isolated_settings, monkeypatch):
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme14")
    from app.db.client import require_client
    from app.db.workspaces import ensure_default_workspace

    require_client().table("profiles").insert(
        {"id": creator_id, "email": "creator@acme14.com", "first_name": "Cre", "last_name": "Ator"}
    ).execute()
    from app.db.artifact_shares import mint_share

    default_ws = ensure_default_workspace(company_id)
    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id=default_ws["id"], created_by_user_id=creator_id,
    )
    fresh_user = "fresh-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": fresh_user, "email": "joiner@acme14.com"}
    ).execute()
    client = _bare_client(monkeypatch, fresh_user)

    assert client.post(f"/v1/artifact-share/{share['token']}/auto-join-company").status_code == 200
    r = client.post(f"/v1/artifact-share/{share['token']}/join")

    assert r.status_code == 200
    body = r.json()
    assert body["sharer_name"] == "Cre Ator"

    joins = (
        require_client().table("artifact_share_joins").select("*")
        .eq("joined_user_id", fresh_user).execute().data
    )
    assert len(joins) == 1
    joined_row = joins[0]
    assert joined_row["share_id"] == share["id"]
    assert joined_row["joined_user_id"] == fresh_user
    assert joined_row["joined_company_id"] == company_id
    assert joined_row["joined_workspace_id"] == body["workspace_id"]


# ── Content (AC14, AC15) ──────────────────────────────────────────────────


def test_content_endpoint_reads_across_workspace_boundary(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    db = isolated_settings["db"]
    prd_id = _seed_prd(db, "acme", title="Cross-workspace PRD")

    ws2 = ctx.client.post("/v1/workspaces", json={"name": "Notifications"}).json()

    # A PLAIN member whose only workspace_members row is on ws2 — NOT the
    # default workspace the artifact actually lives in.
    from app.db.client import require_client
    from app.db.workspaces import upsert_workspace_member

    member_id = "member-" + uuid.uuid4().hex[:8]
    require_client().table("company_members").insert(
        {"id": uuid.uuid4().hex, "company_id": ctx.company_id, "user_id": member_id, "role": "member"}
    ).execute()
    upsert_workspace_member(ws2["id"], member_id, "member")

    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd", artifact_id=prd_id, owner_company_id=ctx.company_id,
        owner_workspace_id=default_ws["id"], created_by_user_id=ctx.user_id,
    )

    r = ctx.client.get(
        f"/v1/artifact-share/{share['token']}/content", headers=supabase_bearer(member_id)
    )

    assert r.status_code == 200
    body = r.json()
    assert body["prd"]["title"] == "Cross-workspace PRD"


def test_content_endpoint_denies_blocked_caller(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    other_company = seed_company(user_id="other-" + uuid.uuid4().hex[:8], slug="rival15")
    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=other_company,
        owner_workspace_id="ws-1", created_by_user_id="creator",
    )

    r = ctx.client.get(f"/v1/artifact-share/{share['token']}/content")

    assert r.status_code == 404

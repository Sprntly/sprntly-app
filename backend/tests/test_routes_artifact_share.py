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


def test_resolve_route_domain_matched_zero_company_guest_view(isolated_settings, monkeypatch):
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
    assert r.json()["outcome"] == "guest_view"


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
    assert r.json() == {"outcome": "blocked", "reason": "domain_mismatch"}


def test_resolve_route_invalid_token_returns_404(isolated_settings, monkeypatch):
    client = _bare_client(monkeypatch, "whoever-" + uuid.uuid4().hex[:8])

    r = client.get(f"/v1/artifact-share/{uuid.uuid4()}/resolve")

    assert r.status_code == 404


def test_blocked_response_never_contains_artifact_title(isolated_settings, monkeypatch):
    """AC16 non-disclosure guard, mutation-proofed per PI13: a DISPOSABLE
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

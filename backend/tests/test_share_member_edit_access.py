"""A colleague who opens a shared artifact must land in the EDITABLE app.

Both share resolvers (token-keyed `artifact_share`, bare-link `prd_access`)
used to answer `guest_view` for every same-company caller, and the frontend
renders `guest_view` as a read-only shell. So a teammate who followed a share
link could not edit the PRD or its tickets — while the exact same person
could edit them by opening the PRD from the sidebar. Edit rights belong to
COMPANY MEMBERSHIP, not to who created the artifact or who minted the link.

`member` is the new outcome for a same-company caller who can already act in
the artifact's owning workspace. `guest_view` survives for the case it was
built for — same company, no access to that workspace yet — and the
cross-COMPANY boundary is untouched, which is what the `blocked` tests here
pin down.
"""
from __future__ import annotations

import importlib
import sys
import uuid

from fastapi.testclient import TestClient

from tests._company_helpers import (
    company_client,
    seed_company,
    setup_supabase_auth,
    supabase_bearer,
)


def _client_for(monkeypatch, user_id: str) -> TestClient:
    setup_supabase_auth(monkeypatch)
    import app.main as main_mod

    importlib.reload(sys.modules["app.main"])
    return TestClient(main_mod.app, headers=supabase_bearer(user_id))


def _bind_dataset(slug: str, workspace_id: str) -> None:
    """Give `slug` a real `datasets` row bound to `workspace_id`.

    `_company_helpers.seed_company` creates companies + company_members but
    NO datasets row, so without this `workspace_for_dataset_slug` returns
    None, `owning_info_for_prd` yields `workspace_id: None`, and
    `user_can_act_in_workspace` takes its unbound-legacy branch — which means
    a bare-link test's `upsert_workspace_member(...)` line is INERT and the
    member/guest_view split is never actually exercised. Any bare-link test
    that means to test workspace scoping must call this.
    """
    from app.db.client import require_client

    require_client().table("datasets").insert(
        {"slug": slug, "display_name": slug.title(), "workspace_id": workspace_id}
    ).execute()


def _seed_prd(db, dataset: str, *, title: str = "t") -> tuple[int, str]:
    """A real PRD stamped with a real public_id (the sqlite test schema has no
    gen_random_uuid() default). Returns (prd_id, public_id).

    NOTE: does not bind the dataset — call `_bind_dataset` when the test
    depends on workspace scoping being real."""
    from app.db.client import require_client

    brief_id = db.save_brief(dataset, "W", {"insights": []}, schema_version=1)
    prd_id = db.start_prd(
        brief_id=brief_id, insight_index=0, title=title, template_version=1, variant="v2"
    )
    public_id = str(uuid.uuid4())
    require_client().table("prds").update({"public_id": public_id}).eq(
        "id", prd_id
    ).execute()
    return prd_id, public_id


def _add_plain_member(company_id: str, user_id: str) -> None:
    from app.db.client import require_client

    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": company_id,
            "user_id": user_id,
            "role": "member",
        }
    ).execute()


# ── The fix: a non-creator member resolves to `member`, not `guest_view` ──


def test_share_resolve_gives_a_non_creator_member_the_editable_app(
    isolated_settings, monkeypatch
):
    """The headline case. The PRD's creator mints a share link; a PLAIN member
    of the same company (not the creator, not an admin) opens it. They hold a
    workspace_members row on the artifact's own workspace, so they can already
    edit that PRD in-app — the link must not downgrade them to read-only."""
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    db = isolated_settings["db"]
    prd_id, _ = _seed_prd(db, "acme", title="Shared PRD")

    from app.db.artifact_shares import mint_share
    from app.db.workspaces import upsert_workspace_member

    colleague = "colleague-" + uuid.uuid4().hex[:8]
    _add_plain_member(ctx.company_id, colleague)
    upsert_workspace_member(default_ws["id"], colleague, "member")

    share = mint_share(
        artifact_type="prd",
        artifact_id=prd_id,
        owner_company_id=ctx.company_id,
        owner_workspace_id=default_ws["id"],
        created_by_user_id=ctx.user_id,  # the CREATOR, not the caller below
    )

    r = ctx.client.get(
        f"/v1/artifact-share/{share['token']}/resolve",
        headers=supabase_bearer(colleague),
    )

    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "member"
    # The workspace the gate must activate before handing over to the app.
    assert body["owner_workspace_id"] == default_ws["id"]


def test_bare_link_resolve_gives_a_non_creator_member_the_editable_app(
    isolated_settings, monkeypatch
):
    """Same for the token-less `?prd=` sibling.

    The dataset is BOUND, so `user_can_act_in_workspace` really has to
    consult the workspace_members row. Without the binding this test passed
    through the unbound short-circuit and the grant below was inert — its
    guest_view twin immediately after is what pins that down."""
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    db = isolated_settings["db"]
    prd_id, public_id = _seed_prd(db, "acme")
    _bind_dataset("acme", default_ws["id"])

    from app.db.workspaces import upsert_workspace_member

    colleague = "colleague-" + uuid.uuid4().hex[:8]
    _add_plain_member(ctx.company_id, colleague)
    upsert_workspace_member(default_ws["id"], colleague, "member")

    r = ctx.client.get(
        f"/v1/prd-access/{public_id}/resolve", headers=supabase_bearer(colleague)
    )

    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "member"
    assert body["artifact_id"] == prd_id


def test_bare_link_resolve_guest_view_without_the_workspace_row(
    isolated_settings, monkeypatch
):
    """The other side of the split, and the reason the test above is now
    meaningful: SAME setup, SAME bound dataset, but no workspace_members
    row. Delete the grant from the member test and this is what you get —
    so the grant is load-bearing, which it was not before the binding."""
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    db = isolated_settings["db"]
    _, public_id = _seed_prd(db, "acme")
    _bind_dataset("acme", default_ws["id"])

    colleague = "colleague-" + uuid.uuid4().hex[:8]
    _add_plain_member(ctx.company_id, colleague)  # company only, NO workspace row

    r = ctx.client.get(
        f"/v1/prd-access/{public_id}/resolve", headers=supabase_bearer(colleague)
    )

    assert r.status_code == 200
    assert r.json()["outcome"] == "guest_view"


def test_bare_link_unbound_dataset_needs_a_workspace_somewhere(
    isolated_settings, monkeypatch
):
    """The Finding-2 case: an UNBOUND legacy dataset.

    `user_can_act_in_workspace` used to answer True unconditionally here, so
    a domain-matched fresh signup — company_members row, no workspace row
    anywhere — resolved to `member`, was routed into the app, and then hit
    require_workspace's 403 with no Join prompt. Worse than the read-only
    viewer they had before. Now the answer depends on whether the caller can
    obtain a WorkspaceContext at all."""
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    db = isolated_settings["db"]
    _, public_id = _seed_prd(db, "acme")  # deliberately NOT bound

    from app.db.workspaces import upsert_workspace_member

    stranded = "stranded-" + uuid.uuid4().hex[:8]
    _add_plain_member(ctx.company_id, stranded)
    r = ctx.client.get(
        f"/v1/prd-access/{public_id}/resolve", headers=supabase_bearer(stranded)
    )
    assert r.status_code == 200
    assert r.json()["outcome"] == "guest_view", (
        "a caller with no workspace row anywhere would 403 inside the app"
    )

    # Give them a workspace row and the same call flips to `member`, because
    # an unbound dataset IS reachable from any workspace of the company.
    upsert_workspace_member(default_ws["id"], stranded, "member")
    r2 = ctx.client.get(
        f"/v1/prd-access/{public_id}/resolve", headers=supabase_bearer(stranded)
    )
    assert r2.json()["outcome"] == "member"


def test_share_resolve_gives_the_creator_themselves_the_editable_app(
    isolated_settings, monkeypatch
):
    """Not just colleagues: the person who minted the link gets `member` too
    when they follow their own link back (e.g. from their sent message)."""
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    db = isolated_settings["db"]
    prd_id, _ = _seed_prd(db, "acme")

    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd",
        artifact_id=prd_id,
        owner_company_id=ctx.company_id,
        owner_workspace_id=default_ws["id"],
        created_by_user_id=ctx.user_id,
    )

    r = ctx.client.get(f"/v1/artifact-share/{share['token']}/resolve")

    assert r.status_code == 200
    assert r.json()["outcome"] == "member"


# ── guest_view survives where the app genuinely can't serve the caller ────


def test_member_without_that_workspace_still_gets_the_read_only_guest_view(
    isolated_settings, monkeypatch
):
    """A same-company member with NO access to the artifact's workspace keeps
    `guest_view`. Routing them into the app would strand them on
    require_owned_prd's 404 — the guest viewer plus its Join prompt is the
    path that actually works for them."""
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    ws2 = ctx.client.post("/v1/workspaces", json={"name": "Notifications"}).json()
    db = isolated_settings["db"]
    prd_id, _ = _seed_prd(db, "acme")

    from app.db.artifact_shares import mint_share
    from app.db.workspaces import upsert_workspace_member

    outsider = "outsider-" + uuid.uuid4().hex[:8]
    _add_plain_member(ctx.company_id, outsider)
    upsert_workspace_member(ws2["id"], outsider, "member")  # the WRONG workspace

    share = mint_share(
        artifact_type="prd",
        artifact_id=prd_id,
        owner_company_id=ctx.company_id,
        owner_workspace_id=default_ws["id"],
        created_by_user_id=ctx.user_id,
    )

    r = ctx.client.get(
        f"/v1/artifact-share/{share['token']}/resolve",
        headers=supabase_bearer(outsider),
    )

    assert r.status_code == 200
    assert r.json()["outcome"] == "guest_view"


def test_company_member_with_no_workspace_row_at_all_gets_guest_view(
    isolated_settings, monkeypatch
):
    """The fresh domain-matched signup shape: a company_members row exists
    (auto-join grants it) but no workspace_members row does yet."""
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    db = isolated_settings["db"]
    prd_id, _ = _seed_prd(db, "acme")

    from app.db.artifact_shares import mint_share

    fresh = "fresh-" + uuid.uuid4().hex[:8]
    _add_plain_member(ctx.company_id, fresh)  # company only, no workspace grant

    share = mint_share(
        artifact_type="prd",
        artifact_id=prd_id,
        owner_company_id=ctx.company_id,
        owner_workspace_id=default_ws["id"],
        created_by_user_id=ctx.user_id,
    )

    r = ctx.client.get(
        f"/v1/artifact-share/{share['token']}/resolve", headers=supabase_bearer(fresh)
    )

    assert r.status_code == 200
    assert r.json()["outcome"] == "guest_view"


def test_joining_from_guest_view_then_resolves_to_member(
    isolated_settings, monkeypatch
):
    """The escalation path end to end: guest_view -> /join -> member. This is
    what makes guest_view a waypoint rather than a dead end."""
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    db = isolated_settings["db"]
    prd_id, _ = _seed_prd(db, "acme")

    from app.db.artifact_shares import mint_share

    fresh = "fresh-" + uuid.uuid4().hex[:8]
    _add_plain_member(ctx.company_id, fresh)
    share = mint_share(
        artifact_type="prd",
        artifact_id=prd_id,
        owner_company_id=ctx.company_id,
        owner_workspace_id=default_ws["id"],
        created_by_user_id=ctx.user_id,
    )
    headers = supabase_bearer(fresh)

    before = ctx.client.get(
        f"/v1/artifact-share/{share['token']}/resolve", headers=headers
    )
    assert before.json()["outcome"] == "guest_view"

    joined = ctx.client.post(f"/v1/artifact-share/{share['token']}/join", headers=headers)
    assert joined.status_code == 200

    after = ctx.client.get(
        f"/v1/artifact-share/{share['token']}/resolve", headers=headers
    )
    assert after.json()["outcome"] == "member"


# ── Cross-COMPANY isolation must NOT move ─────────────────────────────────


def test_a_different_company_is_still_blocked_on_share_resolve(
    isolated_settings, monkeypatch
):
    """The boundary this change must never widen: company B's member gets
    `blocked` on company A's share — never `member`, never `guest_view`."""
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    db = isolated_settings["db"]
    prd_id, _ = _seed_prd(db, "acme")

    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd",
        artifact_id=prd_id,
        owner_company_id=ctx.company_id,
        owner_workspace_id=default_ws["id"],
        created_by_user_id=ctx.user_id,
    )

    rival_user = "rival-" + uuid.uuid4().hex[:8]
    seed_company(user_id=rival_user, slug="rivalco")
    rival = _client_for(monkeypatch, rival_user)

    r = rival.get(f"/v1/artifact-share/{share['token']}/resolve")
    assert r.status_code == 200
    assert r.json() == {"outcome": "blocked", "reason": "different_company"}

    # And the content behind it stays unreadable — `member` widening the
    # /content gate must not have opened a cross-company hole.
    assert rival.get(f"/v1/artifact-share/{share['token']}/content").status_code == 404
    assert rival.post(f"/v1/artifact-share/{share['token']}/join").status_code == 403


def test_a_different_company_is_still_blocked_on_bare_link_resolve(
    isolated_settings, monkeypatch
):
    """Same boundary on the token-less sibling."""
    ctx = company_client(monkeypatch)
    ctx.client.get("/v1/workspaces")
    db = isolated_settings["db"]
    _, public_id = _seed_prd(db, "acme")

    rival_user = "rival-" + uuid.uuid4().hex[:8]
    seed_company(user_id=rival_user, slug="rivalco2")
    rival = _client_for(monkeypatch, rival_user)

    r = rival.get(f"/v1/prd-access/{public_id}/resolve")
    assert r.status_code == 200
    assert r.json() == {"outcome": "blocked", "reason": "different_company"}
    assert rival.get(f"/v1/prd-access/{public_id}/content").status_code == 404
    assert rival.post(f"/v1/prd-access/{public_id}/join").status_code == 403


def test_a_foreign_workspace_id_on_the_share_fails_closed(
    isolated_settings, monkeypatch
):
    """user_can_act_in_workspace must not accept a workspace belonging to
    ANOTHER company just because the caller's own org role is owner. A share
    row pointing at a foreign workspace is corrupt data, and the safe answer
    is the read-only viewer, not the app."""
    ctx = company_client(monkeypatch)
    ctx.client.get("/v1/workspaces")
    db = isolated_settings["db"]
    prd_id, _ = _seed_prd(db, "acme")

    rival_user = "rival-" + uuid.uuid4().hex[:8]
    rival_company = seed_company(user_id=rival_user, slug="rivalco3")
    from app.db.workspaces import ensure_default_workspace

    rival_ws = ensure_default_workspace(rival_company)

    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd",
        artifact_id=prd_id,
        owner_company_id=ctx.company_id,
        owner_workspace_id=rival_ws["id"],
        created_by_user_id=ctx.user_id,
    )

    r = ctx.client.get(f"/v1/artifact-share/{share['token']}/resolve")
    assert r.status_code == 200
    assert r.json()["outcome"] == "guest_view"


# ── Deploy-window compatibility ───────────────────────────────────────────


def test_content_still_serves_a_member_outcome(isolated_settings, monkeypatch):
    """A browser on the pre-`member` bundle renders the guest viewer for a
    `member` caller, and that viewer's only data source is /content. It must
    keep working, or the deploy window breaks the page it is fixing."""
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    db = isolated_settings["db"]
    prd_id, public_id = _seed_prd(db, "acme", title="Still readable")

    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd",
        artifact_id=prd_id,
        owner_company_id=ctx.company_id,
        owner_workspace_id=default_ws["id"],
        created_by_user_id=ctx.user_id,
    )

    assert ctx.client.get(f"/v1/artifact-share/{share['token']}/resolve").json()[
        "outcome"
    ] == "member"

    r = ctx.client.get(f"/v1/artifact-share/{share['token']}/content")
    assert r.status_code == 200
    assert r.json()["prd"]["title"] == "Still readable"

    r2 = ctx.client.get(f"/v1/prd-access/{public_id}/content")
    assert r2.status_code == 200
    assert r2.json()["prd"]["title"] == "Still readable"


def test_join_still_succeeds_for_a_member_outcome(isolated_settings, monkeypatch):
    """Same reason: the old bundle still offers the Join button to a `member`
    caller. Clicking it must not 403."""
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    db = isolated_settings["db"]
    prd_id, public_id = _seed_prd(db, "acme")

    from app.db.artifact_shares import mint_share

    share = mint_share(
        artifact_type="prd",
        artifact_id=prd_id,
        owner_company_id=ctx.company_id,
        owner_workspace_id=default_ws["id"],
        created_by_user_id=ctx.user_id,
    )

    r = ctx.client.post(f"/v1/artifact-share/{share['token']}/join")
    assert r.status_code == 200
    assert r.json()["workspace_id"] == default_ws["id"]

    # The bare-link sibling 409s here — but on its OWN "unbound legacy
    # dataset, nothing to join into" guard, which fires after the outcome
    # gate and predates this change. 403 would mean the outcome gate itself
    # rejected a `member`, which is the regression this test exists to catch.
    r2 = ctx.client.post(f"/v1/prd-access/{public_id}/join")
    assert r2.status_code == 409

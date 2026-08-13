"""DB-helper unit coverage for the artifact share-grant primitive.

Route-level (HTTP) coverage lives in tests/test_routes_artifact_share.py;
this file exercises the app.db.artifact_shares helpers directly (mint,
lookup, domain resolution, the resolve-outcome decision function).
"""
from __future__ import annotations

import ast
import pathlib
import re
import uuid

import pytest

from app.db.artifact_shares import (
    auto_join_company_on_domain_match,
    get_or_mint_canonical_share,
    get_share_by_token,
    mint_share,
    owning_company_domain,
    resolve_share_access,
)
from app.db.client import require_client

from tests._company_helpers import seed_company


def _seed_profile(user_id: str, email: str) -> None:
    require_client().table("profiles").insert(
        {"id": user_id, "email": email, "first_name": "T", "last_name": "U"}
    ).execute()


def _seed_member(company_id: str, user_id: str, *, role: str = "member", created_at: str) -> None:
    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": company_id,
            "user_id": user_id,
            "role": role,
            "created_at": created_at,
        }
    ).execute()


def _seed_company_row(slug: str) -> str:
    company_id = uuid.uuid4().hex
    require_client().table("companies").insert(
        {"id": company_id, "slug": slug, "display_name": slug.title()}
    ).execute()
    return company_id


# ── Creation ──────────────────────────────────────────────────────────────


def test_mint_share_creates_row_with_uuid_token(isolated_settings):
    row1 = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id="co-1",
        owner_workspace_id="ws-1", created_by_user_id="user-1",
    )
    row2 = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id="co-1",
        owner_workspace_id="ws-1", created_by_user_id="user-1",
    )
    for row in (row1, row2):
        parsed = uuid.UUID(row["token"])
        assert parsed.version == 4
    assert row1["token"] != row2["token"]


# ── Retrieval ─────────────────────────────────────────────────────────────


def test_get_share_by_token_roundtrip(isolated_settings):
    minted = mint_share(
        artifact_type="prd", artifact_id=42, owner_company_id="co-1",
        owner_workspace_id="ws-1", created_by_user_id="user-1",
    )
    fetched = get_share_by_token(minted["token"])
    assert fetched == minted


def test_get_share_by_token_not_found_for_random_token(isolated_settings):
    assert get_share_by_token(str(uuid.uuid4())) is None


# ── Domain resolution (revision 2026-08-02: role-based, not earliest-created) ──


def test_owning_company_domain_resolves_owner_regardless_of_creation_order(isolated_settings):
    """The owner is NOT the earliest-created member — role, not creation
    order, must win."""
    company_id = _seed_company_row("acme")
    early_member_id = "early-" + uuid.uuid4().hex[:8]
    later_owner_id = "owner-" + uuid.uuid4().hex[:8]
    _seed_member(company_id, early_member_id, role="member", created_at="2020-01-01T00:00:00")
    _seed_member(company_id, later_owner_id, role="owner", created_at="2021-01-01T00:00:00")
    _seed_profile(early_member_id, "early@other.com")
    _seed_profile(later_owner_id, "owner@acme.com")

    assert owning_company_domain(company_id) == "acme.com"


def test_owning_company_domain_falls_back_to_earliest_admin_when_no_owner(isolated_settings):
    company_id = _seed_company_row("acme-admins")
    early_admin_id = "early-admin-" + uuid.uuid4().hex[:8]
    late_admin_id = "late-admin-" + uuid.uuid4().hex[:8]
    _seed_member(company_id, late_admin_id, role="admin", created_at="2021-01-01T00:00:00")
    _seed_member(company_id, early_admin_id, role="admin", created_at="2020-01-01T00:00:00")
    _seed_profile(early_admin_id, "early-admin@acme.com")
    _seed_profile(late_admin_id, "late-admin@other.com")

    assert owning_company_domain(company_id) == "acme.com"


def test_owning_company_domain_ignores_plain_members_with_no_owner_or_admin(isolated_settings):
    company_id = _seed_company_row("acme-members-only")
    member_id = "member-" + uuid.uuid4().hex[:8]
    _seed_member(company_id, member_id, role="member", created_at="2020-01-01T00:00:00")
    _seed_profile(member_id, "member@acme.com")

    # A plain member is never the resolution source, even as the sole /
    # earliest-created row — unresolvable (fail closed), not "no domain".
    assert owning_company_domain(company_id) is None


def test_owning_company_domain_returns_none_when_no_profile_row(isolated_settings):
    company_id = _seed_company_row("acme2")
    member_id = "member-" + uuid.uuid4().hex[:8]
    _seed_member(company_id, member_id, role="owner", created_at="2020-01-01T00:00:00")
    # Deliberately no profiles row for member_id.

    assert owning_company_domain(company_id) is None


# ── Resolve outcomes ──────────────────────────────────────────────────────


def test_resolve_same_company_different_workspace_returns_guest_view(isolated_settings):
    user_id = "user-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=user_id, slug="acme3")
    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        # A workspace id distinct from any workspace the caller is
        # necessarily "in" — resolve is company-scoped only.
        owner_workspace_id="some-other-workspace", created_by_user_id="sharer-1",
    )

    result = resolve_share_access(token=share["token"], user_id=user_id, user_email=None)

    assert result["outcome"] == "guest_view"
    assert result["same_company"] is True


def test_resolve_different_company_returns_blocked(isolated_settings):
    owner_user = "owner-" + uuid.uuid4().hex[:8]
    owner_company = seed_company(user_id=owner_user, slug="acme4")
    other_user = "other-" + uuid.uuid4().hex[:8]
    seed_company(user_id=other_user, slug="rival4")
    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=owner_company,
        owner_workspace_id="ws-1", created_by_user_id=owner_user,
    )

    result = resolve_share_access(token=share["token"], user_id=other_user, user_email=None)

    assert result["outcome"] == "blocked"
    assert result["reason"] == "different_company"


def test_resolve_zero_company_matching_domain_now_blocked(isolated_settings):
    """Revision 2026-08-02: a zero-membership caller is ALWAYS blocked now,
    even with a perfectly matching email domain — sign-in never grants NEW
    membership; only auto_join_company_on_domain_match (run once, at signup
    time) does. This is the exact SIGN-IN scenario tonight's decision calls
    out: an existing account with zero company memberships hitting a share
    link must never be auto-granted access just by having a matching
    domain."""
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme5")
    _seed_profile(creator_id, "creator@acme5.com")
    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id="ws-1", created_by_user_id=creator_id,
    )
    fresh_user = "fresh-" + uuid.uuid4().hex[:8]

    result = resolve_share_access(
        token=share["token"], user_id=fresh_user, user_email="joiner@acme5.com"
    )

    assert result["outcome"] == "blocked"
    assert result["reason"] == "different_company"


def test_resolve_domain_mismatched_zero_company_returns_blocked(isolated_settings):
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme6")
    _seed_profile(creator_id, "creator@acme6.com")
    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id="ws-1", created_by_user_id=creator_id,
    )
    fresh_user = "fresh-" + uuid.uuid4().hex[:8]

    result = resolve_share_access(
        token=share["token"], user_id=fresh_user, user_email="joiner@other.com"
    )

    assert result["outcome"] == "blocked"
    assert result["reason"] == "different_company"


def test_resolve_unresolvable_domain_still_fails_closed(isolated_settings):
    """Domain is no longer part of the decision at all for a zero-membership
    caller, but the fail-closed OUTCOME (blocked, never guest_view) must
    hold regardless — this proves it holds even when owning_company_domain
    itself can't resolve anything."""
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme7")
    # Deliberately no profiles row for creator_id -> domain unresolvable.
    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id="ws-1", created_by_user_id=creator_id,
    )
    fresh_user = "fresh-" + uuid.uuid4().hex[:8]

    result = resolve_share_access(
        token=share["token"], user_id=fresh_user, user_email="anyone@acme7.com"
    )

    assert result["outcome"] == "blocked"
    assert result["reason"] == "different_company"


def test_resolve_same_company_guest_view_always_has_same_company_true(isolated_settings):
    """Mutation-proof for the retired same_company=False branch: the ONLY
    way resolve_share_access can return guest_view is via a caller who
    already has a matching company_members row — same_company is therefore
    always True on a guest_view outcome now (never reachable as False)."""
    user_id = "user-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=user_id, slug="acme-always-same")
    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id="ws-1", created_by_user_id="sharer-1",
    )

    result = resolve_share_access(token=share["token"], user_id=user_id, user_email=None)

    assert result["outcome"] == "guest_view"
    assert result["same_company"] is True


# ── auto_join_company_on_domain_match (signup-time company grant) ────────


def test_auto_join_grants_company_membership_on_matching_domain(isolated_settings):
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme-auto1")
    _seed_profile(creator_id, "owner@acme-auto1.com")
    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id="ws-1", created_by_user_id=creator_id,
    )
    fresh_user = "fresh-" + uuid.uuid4().hex[:8]

    granted = auto_join_company_on_domain_match(
        token=share["token"], user_id=fresh_user, user_email="joiner@acme-auto1.com"
    )

    assert granted == company_id
    members = (
        require_client().table("company_members").select("*")
        .eq("company_id", company_id).eq("user_id", fresh_user).execute().data
    )
    assert len(members) == 1
    assert members[0]["role"] == "member"


def test_auto_join_grants_no_workspace_membership(isolated_settings):
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme-auto2")
    _seed_profile(creator_id, "owner@acme-auto2.com")
    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id="ws-1", created_by_user_id=creator_id,
    )
    fresh_user = "fresh-" + uuid.uuid4().hex[:8]

    auto_join_company_on_domain_match(
        token=share["token"], user_id=fresh_user, user_email="joiner@acme-auto2.com"
    )

    ws_members = (
        require_client().table("workspace_members").select("*")
        .eq("user_id", fresh_user).execute().data
    )
    assert ws_members == []


def test_auto_join_no_op_on_mismatched_domain(isolated_settings):
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme-auto3")
    _seed_profile(creator_id, "owner@acme-auto3.com")
    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id="ws-1", created_by_user_id=creator_id,
    )
    fresh_user = "fresh-" + uuid.uuid4().hex[:8]

    granted = auto_join_company_on_domain_match(
        token=share["token"], user_id=fresh_user, user_email="joiner@other.com"
    )

    assert granted is None
    members = (
        require_client().table("company_members").select("*")
        .eq("user_id", fresh_user).execute().data
    )
    assert members == []


def test_auto_join_no_op_when_caller_already_has_a_company(isolated_settings):
    """Sign-in never grants NEW membership — a caller who already has ANY
    company (even a different one) is a no-op here, not a second company
    membership."""
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme-auto4")
    _seed_profile(creator_id, "owner@acme-auto4.com")
    share = mint_share(
        artifact_type="prd", artifact_id=1, owner_company_id=company_id,
        owner_workspace_id="ws-1", created_by_user_id=creator_id,
    )
    existing_user = "existing-" + uuid.uuid4().hex[:8]
    seed_company(user_id=existing_user, slug="rival-auto4")

    granted = auto_join_company_on_domain_match(
        token=share["token"], user_id=existing_user, user_email="whoever@acme-auto4.com"
    )

    assert granted is None


def test_auto_join_no_op_on_invalid_token(isolated_settings):
    granted = auto_join_company_on_domain_match(
        token=str(uuid.uuid4()), user_id="whoever", user_email="anyone@acme.com"
    )
    assert granted is None


def test_resolve_invalid_token_returns_not_found_outcome(isolated_settings):
    result = resolve_share_access(
        token=str(uuid.uuid4()), user_id="whoever", user_email=None
    )
    assert result == {"outcome": "not_found"}


# ── get_or_mint_canonical_share (canonical single-link primitive) ───────


def test_get_or_mint_canonical_share_mints_when_absent(isolated_settings):
    row = get_or_mint_canonical_share(
        artifact_type="prd", artifact_id=100, owner_company_id="co-A",
        owner_workspace_id="ws-A", created_by_user_id="user-A",
    )
    assert row["token"]
    rows = (
        require_client().table("artifact_shares").select("*")
        .eq("artifact_type", "prd").eq("artifact_id", 100).execute().data
    )
    assert len(rows) == 1
    assert rows[0]["token"] == row["token"]


def test_get_or_mint_canonical_share_returns_existing_without_minting(isolated_settings):
    seeded = mint_share(
        artifact_type="prd", artifact_id=101, owner_company_id="co-A",
        owner_workspace_id="ws-A", created_by_user_id="user-A",
    )

    row = get_or_mint_canonical_share(
        artifact_type="prd", artifact_id=101, owner_company_id="co-A",
        owner_workspace_id="ws-A", created_by_user_id="user-A",
    )

    assert row["token"] == seeded["token"]
    rows = (
        require_client().table("artifact_shares").select("*")
        .eq("artifact_type", "prd").eq("artifact_id", 101).execute().data
    )
    assert len(rows) == 1  # no new row minted


def test_get_or_mint_canonical_share_is_idempotent(isolated_settings):
    first = get_or_mint_canonical_share(
        artifact_type="prd", artifact_id=102, owner_company_id="co-A",
        owner_workspace_id="ws-A", created_by_user_id="user-A",
    )
    second = get_or_mint_canonical_share(
        artifact_type="prd", artifact_id=102, owner_company_id="co-A",
        owner_workspace_id="ws-A", created_by_user_id="user-A",
    )
    third = get_or_mint_canonical_share(
        artifact_type="prd", artifact_id=102, owner_company_id="co-A",
        owner_workspace_id="ws-A", created_by_user_id="user-A",
    )

    assert first["token"] == second["token"] == third["token"]
    rows = (
        require_client().table("artifact_shares").select("*")
        .eq("artifact_type", "prd").eq("artifact_id", 102).execute().data
    )
    assert len(rows) == 1


def test_get_or_mint_canonical_share_returns_earliest_of_multiple(isolated_settings):
    first = mint_share(
        artifact_type="prd", artifact_id=103, owner_company_id="co-A",
        owner_workspace_id="ws-A", created_by_user_id="user-A",
    )
    mint_share(
        artifact_type="prd", artifact_id=103, owner_company_id="co-A",
        owner_workspace_id="ws-A", created_by_user_id="user-A",
    )

    row = get_or_mint_canonical_share(
        artifact_type="prd", artifact_id=103, owner_company_id="co-A",
        owner_workspace_id="ws-A", created_by_user_id="user-A",
    )

    assert row["id"] == first["id"]
    assert row["token"] == first["token"]
    rows = (
        require_client().table("artifact_shares").select("*")
        .eq("artifact_type", "prd").eq("artifact_id", 103).execute().data
    )
    assert len(rows) == 2  # nothing minted by the get-or-create call


def test_get_or_mint_canonical_share_ignores_other_company_share(isolated_settings):
    other_company_share = mint_share(
        artifact_type="prd", artifact_id=104, owner_company_id="co-A",
        owner_workspace_id="ws-A", created_by_user_id="user-A",
    )

    row = get_or_mint_canonical_share(
        artifact_type="prd", artifact_id=104, owner_company_id="co-B",
        owner_workspace_id="ws-B", created_by_user_id="user-B",
    )

    assert row["token"] != other_company_share["token"]
    assert row["owner_company_id"] == "co-B"
    rows = (
        require_client().table("artifact_shares").select("*")
        .eq("artifact_type", "prd").eq("artifact_id", 104).execute().data
    )
    assert len(rows) == 2  # A's original share plus a fresh one minted for B


def test_canonical_helper_never_writes_revoked_at():
    """Mutation-proofed guard, scoped to the new helper itself: no `.update`
    node anywhere inside `get_or_mint_canonical_share`'s body ever sets
    `revoked_at`. Adding such a call turns this RED. `test_revoked_at_never_
    written` above already scans the whole module (and the route file); this
    test isolates the assertion to the new function so the mutation-proof
    holds even if the module grows other functions later."""
    backend_app = pathlib.Path(__file__).resolve().parent.parent / "app"
    tree = ast.parse((backend_app / "db" / "artifact_shares.py").read_text())
    func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "get_or_mint_canonical_share"
    )
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    keys = [k.value for k in arg.keys if isinstance(k, ast.Constant)]
                    assert "revoked_at" not in keys, (
                        "get_or_mint_canonical_share contains an .update() call "
                        "setting revoked_at"
                    )


# ── Non-disclosure guard (AC17) ──────────────────────────────────────────


def test_revoked_at_never_written():
    """Static AST scan (per the ticket's own "grep-style or runtime spy"
    option): no `.update({...})` call anywhere in the new module or route
    file ever sets `revoked_at` — this ticket ships no revoke endpoint."""
    backend_app = pathlib.Path(__file__).resolve().parent.parent / "app"
    files = [
        backend_app / "db" / "artifact_shares.py",
        backend_app / "routes" / "artifact_share.py",
    ]
    for path in files:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "update"
            ):
                for arg in node.args:
                    if isinstance(arg, ast.Dict):
                        keys = [
                            k.value for k in arg.keys if isinstance(k, ast.Constant)
                        ]
                        assert "revoked_at" not in keys, (
                            f"{path}: an .update() call sets revoked_at"
                        )


# ── Idempotent migration (AC18) ───────────────────────────────────────────


def test_migration_is_idempotent_by_construction():
    """Builder-lane proxy for AC18: every CREATE TABLE / CREATE INDEX in the
    new migration uses IF NOT EXISTS, so a second apply is a no-op rather
    than an error. The literal "apply twice against a live instance" proof
    is a ship-gate live-Supabase check — this environment has neither
    psql nor psycopg2, and a builder unit test must not touch a
    potentially-shared local Supabase instance."""
    migration_path = (
        pathlib.Path(__file__).resolve().parent.parent.parent
        / "supabase" / "migrations" / "20260801130000_artifact_share_links.sql"
    )
    sql = migration_path.read_text()
    creates = re.findall(r"CREATE\s+(TABLE|INDEX)\s+(IF NOT EXISTS)?", sql, re.IGNORECASE)
    assert creates, "migration defines no CREATE statements"
    for kind, guard in creates:
        assert guard, f"CREATE {kind} without IF NOT EXISTS in the new migration"


# ── require_shared_prd (company-scoped, not workspace-scoped) ────────────


def test_require_shared_prd_denies_different_company(isolated_settings):
    from app.db import save_brief, start_prd
    from app.db.artifact_shares import require_shared_prd

    owner_company = _seed_company_row("acme10")
    brief_id = save_brief("acme10", "W", {"insights": []}, schema_version=1)
    prd_id = start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1, variant="v2"
    )

    # Owner resolves.
    assert require_shared_prd(prd_id, owner_company)["id"] == prd_id

    # Foreign company -> 404 (no workspace involved at all — company-scoped only).
    with pytest.raises(Exception) as ei:
        require_shared_prd(prd_id, "some-other-company")
    assert getattr(ei.value, "status_code", None) == 404

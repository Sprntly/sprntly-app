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


# ── Domain resolution ─────────────────────────────────────────────────────


def test_owning_company_domain_returns_earliest_member_email_domain(isolated_settings):
    company_id = _seed_company_row("acme")
    early_id = "early-" + uuid.uuid4().hex[:8]
    late_id = "late-" + uuid.uuid4().hex[:8]
    _seed_member(company_id, early_id, role="owner", created_at="2020-01-01T00:00:00")
    _seed_member(company_id, late_id, role="member", created_at="2021-01-01T00:00:00")
    _seed_profile(early_id, "early@acme.com")
    _seed_profile(late_id, "late@other.com")

    assert owning_company_domain(company_id) == "acme.com"


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


def test_resolve_domain_matched_zero_company_returns_guest_view(isolated_settings):
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

    assert result["outcome"] == "guest_view"
    assert result["same_company"] is False


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
    assert result["reason"] == "domain_mismatch"


def test_resolve_unresolvable_domain_fails_closed(isolated_settings):
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

    # An unresolvable domain must NEVER become "no domain requirement".
    assert result["outcome"] == "blocked"
    assert result["reason"] == "domain_mismatch"


def test_resolve_invalid_token_returns_not_found_outcome(isolated_settings):
    result = resolve_share_access(
        token=str(uuid.uuid4()), user_id="whoever", user_email=None
    )
    assert result == {"outcome": "not_found"}


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

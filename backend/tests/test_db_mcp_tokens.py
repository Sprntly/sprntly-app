"""Tests for app/db/mcp_tokens.py — customer-issued MCP token persistence.

Covers: the raw token is never stored (only its hash), list never leaks the
hash/raw token, revoke is company-scoped, and resolve fails closed on a
revoked token OR a token whose user no longer belongs to that company (the
live-membership-recheck property — see resolve_mcp_token's docstring).
"""
from __future__ import annotations

import uuid

import pytest

import app.auth  # noqa: F401 — ensure app.config/app.auth in sys.modules

from app.db.mcp_tokens import (
    create_mcp_token,
    list_mcp_tokens,
    resolve_mcp_token,
    revoke_mcp_token,
)


def _seed_company_and_member(client, *, company_id: str, user_id: str, role: str = "owner") -> None:
    client.table("companies").insert(
        {"id": company_id, "slug": f"slug-{company_id}", "display_name": "Acme"}
    ).execute()
    client.table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": company_id,
            "user_id": user_id,
            "role": role,
        }
    ).execute()


def test_create_mcp_token_returns_raw_token_once_and_hashes_at_rest(isolated_settings):
    client = isolated_settings["supabase"]
    cid, uid = uuid.uuid4().hex, "user-1"
    _seed_company_and_member(client, company_id=cid, user_id=uid)

    row = create_mcp_token(company_id=cid, user_id=uid, name="Claude Desktop")

    assert row["token"].startswith("sprn_mcp_")
    assert row["token_prefix"] == row["token"][:20]

    stored = client.table("mcp_tokens").select("*").eq("id", row["id"]).execute().data[0]
    assert stored["token_hash"] != row["token"]
    assert row["token"] not in stored.values()


def test_list_mcp_tokens_selects_only_safe_columns(isolated_settings, monkeypatch):
    """The test fake always runs `SELECT *` under the hood (unlike real
    PostgREST, it doesn't enforce column projection), so asserting the
    hash's absence from a fake-backed result would test the fake, not the
    code. Instead, spy on the query builder to verify list_mcp_tokens
    itself never asks for token_hash — the guarantee that actually holds
    in production, where PostgREST DOES honor the column list."""
    from tests._fake_supabase import _Query

    requested_cols: list[str] = []
    orig_select = _Query.select

    def _spy_select(self, cols="*", count=None):
        if self.table == "mcp_tokens":
            requested_cols.append(cols)
        return orig_select(self, cols, count)

    monkeypatch.setattr(_Query, "select", _spy_select)

    client = isolated_settings["supabase"]
    cid, uid = uuid.uuid4().hex, "user-1"
    _seed_company_and_member(client, company_id=cid, user_id=uid)
    create_mcp_token(company_id=cid, user_id=uid, name="Claude Desktop")

    tokens = list_mcp_tokens(cid)
    assert len(tokens) == 1
    assert tokens[0]["name"] == "Claude Desktop"

    assert requested_cols, "expected list_mcp_tokens to call .select(...) on mcp_tokens"
    assert all("token_hash" not in c for c in requested_cols)


def test_resolve_mcp_token_round_trips(isolated_settings):
    client = isolated_settings["supabase"]
    cid, uid = uuid.uuid4().hex, "user-1"
    _seed_company_and_member(client, company_id=cid, user_id=uid, role="admin")
    created = create_mcp_token(company_id=cid, user_id=uid, name="t")

    resolved = resolve_mcp_token(created["token"])
    assert resolved is not None
    assert resolved["company_id"] == cid
    assert resolved["user_id"] == uid
    assert resolved["role"] == "admin"


def test_create_mcp_token_defaults_to_pm_role(isolated_settings):
    """Unspecified role -> 'pm' (full tool set), matching the column default
    that grandfathers pre-role tokens."""
    client = isolated_settings["supabase"]
    cid, uid = uuid.uuid4().hex, "user-1"
    _seed_company_and_member(client, company_id=cid, user_id=uid)

    created = create_mcp_token(company_id=cid, user_id=uid, name="t")

    assert created["token_role"] == "pm"
    assert resolve_mcp_token(created["token"])["token_role"] == "pm"


def test_create_mcp_token_persists_developer_role_through_resolve(isolated_settings):
    client = isolated_settings["supabase"]
    cid, uid = uuid.uuid4().hex, "user-1"
    _seed_company_and_member(client, company_id=cid, user_id=uid)

    created = create_mcp_token(
        company_id=cid, user_id=uid, name="t", token_role="developer"
    )

    assert created["token_role"] == "developer"
    resolved = resolve_mcp_token(created["token"])
    assert resolved["token_role"] == "developer"


def test_create_mcp_token_rejects_unknown_role(isolated_settings):
    client = isolated_settings["supabase"]
    cid, uid = uuid.uuid4().hex, "user-1"
    _seed_company_and_member(client, company_id=cid, user_id=uid)

    with pytest.raises(ValueError):
        create_mcp_token(company_id=cid, user_id=uid, name="t", token_role="root")


def test_resolve_mcp_token_rejects_unknown_token(isolated_settings):
    assert resolve_mcp_token("sprn_mcp_not-a-real-token") is None


def test_resolve_mcp_token_rejects_revoked_token(isolated_settings):
    client = isolated_settings["supabase"]
    cid, uid = uuid.uuid4().hex, "user-1"
    _seed_company_and_member(client, company_id=cid, user_id=uid)
    created = create_mcp_token(company_id=cid, user_id=uid, name="t")

    assert revoke_mcp_token(cid, created["id"]) is True
    assert resolve_mcp_token(created["token"]) is None


def test_resolve_mcp_token_rejects_token_after_membership_removed(isolated_settings):
    """The live-recheck property: removing a user from a company kills their
    MCP access immediately, even without explicit token revocation."""
    client = isolated_settings["supabase"]
    cid, uid = uuid.uuid4().hex, "user-1"
    _seed_company_and_member(client, company_id=cid, user_id=uid)
    created = create_mcp_token(company_id=cid, user_id=uid, name="t")

    client.table("company_members").delete().eq("company_id", cid).eq("user_id", uid).execute()

    assert resolve_mcp_token(created["token"]) is None


def test_revoke_mcp_token_is_company_scoped(isolated_settings):
    """A cross-tenant revoke attempt (right token id, wrong company) matches
    zero rows rather than revoking someone else's token."""
    client = isolated_settings["supabase"]
    cid_a, uid_a = uuid.uuid4().hex, "user-a"
    cid_b, uid_b = uuid.uuid4().hex, "user-b"
    _seed_company_and_member(client, company_id=cid_a, user_id=uid_a)
    _seed_company_and_member(client, company_id=cid_b, user_id=uid_b)
    created = create_mcp_token(company_id=cid_a, user_id=uid_a, name="t")

    assert revoke_mcp_token(cid_b, created["id"]) is False
    # Still resolvable — company B's revoke attempt did not touch it.
    assert resolve_mcp_token(created["token"]) is not None

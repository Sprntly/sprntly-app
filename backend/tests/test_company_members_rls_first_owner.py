"""Migration-presence test for the company_members first-owner RLS fix.

Migration: supabase/migrations/20260608130000_company_members_rls_first_owner.sql

The fake-Supabase test harness (tests/_fake_supabase) runs on SQLite and does
NOT enforce Postgres RLS policies, so the policy itself cannot be exercised in
a unit test. Instead we pin the migration's SQL so the security-critical policy
can't silently regress (e.g. someone re-adding the permissive insert-self
policy). Live RLS verification is performed post-merge against Supabase.

The previous permissive policy (`company_members_insert_self`) let any
authenticated user insert themselves into ANY company as owner, seizing the
tenant. The new policy only permits the first-owner insert into a company the
caller created (created_by = self) that has 0 members; all later members are
added via the service-role backend invite path, which bypasses RLS.
"""
from __future__ import annotations

import re
from pathlib import Path

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase"
    / "migrations"
    / "20260608130000_company_members_rls_first_owner.sql"
)


def _sql() -> str:
    assert _MIGRATION_PATH.exists(), f"missing migration: {_MIGRATION_PATH}"
    return _MIGRATION_PATH.read_text()


def test_migration_file_exists():
    assert _MIGRATION_PATH.exists()


def test_old_permissive_policy_is_dropped():
    sql = _sql()
    assert re.search(
        r"drop policy if exists\s+\"?company_members_insert_self\"?\s+on company_members",
        sql,
    ), "migration must drop the permissive company_members_insert_self policy"


def test_new_policy_is_an_insert_check_for_authenticated():
    sql = _sql()
    assert re.search(
        r"create policy\s+\"?company_members_insert_first_owner\"?\s+on company_members",
        sql,
    )
    assert "for insert to authenticated" in sql
    assert "with check" in sql


def test_new_policy_constrains_self_company_and_first_member():
    """The three security clauses must all be present:
      1. self-insert:        user_id = auth.uid()
      2. own-company:         companies.created_by = auth.uid()
      3. first-member only:   not exists (... company_members for this company)
    """
    sql = _sql()
    # 1. self
    assert "user_id = auth.uid()" in sql
    # 2. the company being joined must have been created by the caller
    assert "created_by = auth.uid()" in sql
    assert re.search(r"from companies\s+\w*\s*where", sql)
    # 3. no existing membership rows for that company (first owner only)
    assert "not exists" in sql
    assert re.search(r"from company_members\s+\w*\s*where", sql)

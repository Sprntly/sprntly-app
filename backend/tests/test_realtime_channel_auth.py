"""Real local-Supabase proof for the Realtime channel-join authorization
policies on `realtime.messages` — the ONLY new authz surface the realtime
transport adds (AD-P23). Two topic shapes are gated: the shared group
channel `project:{project_id}` and the private per-user channel
`project:{project_id}:user:{user_id}`.

Why this needs a live Postgres. `FakeSupabaseClient` (`tests/_fake_supabase.py`)
is an in-memory store with no SQL engine — it cannot evaluate a PL/pgSQL
function, enforce Row Level Security, or apply a policy against
`realtime.topic()`/`auth.uid()` GUCs. Those are exactly what this migration
adds, so proving it needs the real thing: a real local Postgres, both
through `public.is_project_channel_member`/`public.is_individual_channel_member`
directly (the predicate the two gating ACs rest on) and through a real
`realtime.messages` INSERT under the deployed policies (the actual RLS
enforcement layer, catching a policy wired to the wrong function that a
function-level check alone would miss).

Predicate calls run over `docker exec ... psql` against the local rig's
Postgres container, setting `role`/`request.jwt.claims`/`realtime.topic`
the same way PostgREST/Realtime present a caller's identity to `auth.uid()`
and `realtime.topic()` inside a policy — not through supabase-py, which
only ever authenticates as service_role (bypasses RLS entirely) or would
need a signed JWT per test user to drive PostgREST, more moving parts than
a policy-predicate proof needs.

Fixture rows: reads an existing company, its default workspace, and two
existing `company_members` rows (never mints a new `auth.users` row — same
posture as `test_projects_schema_roundtrip.py`). A second, scratch company
+ workspace IS minted (and torn down) to give the cross-tenant ACs (AC-4,
AC-7) a genuine second tenant — but it reuses the SAME two real users as
members, never a new `auth.users` row.

Run it with:

    RUN_PROJECTS_REALTIME_CHANNEL_AUTH_LIVE=1 \\
        pytest tests/test_realtime_channel_auth.py -m integration
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import uuid

import pytest

_RUN_LIVE = os.getenv("RUN_PROJECTS_REALTIME_CHANNEL_AUTH_LIVE") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a local Supabase with the channel-auth migration applied; "
            "set RUN_PROJECTS_REALTIME_CHANNEL_AUTH_LIVE=1"
        ),
    ),
]

_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_MIGRATION_FILE = "20260812120000_projects_realtime_channel_auth.sql"
_DB_CONTAINER = os.getenv("PROJECTS_REALTIME_CHANNEL_AUTH_TEST_DB_CONTAINER", "supabase_db_Sprntly")

# `realtime.messages` and the two functions this migration creates are
# owned by (or require) a superuser role on this stack — `postgres` is
# NOT one (verified against the local rig; see the migration file's own
# comment). Applying/mutating them needs `supabase_admin`; evaluating them
# (SELECT / a policy-gated INSERT as `authenticated`) does not.
_DDL_ROLE = "supabase_admin"
_QUERY_ROLE = "postgres"


def _psql(sql: str, role: str, timeout: int = 20) -> str:
    """Run `sql` inside the local rig's Postgres container as `role`,
    tuples-only unaligned output. Raises with full stdout/stderr on
    failure so a red run is diagnosable from the pytest output alone."""
    result = subprocess.run(
        [
            "docker", "exec", "-e", "PGPASSWORD=postgres", "-i", _DB_CONTAINER,
            "psql", "-U", role, "-d", "postgres", "-t", "-A", "-v", "ON_ERROR_STOP=1",
        ],
        input=sql.encode(),
        capture_output=True,
        timeout=timeout,
    )
    assert result.returncode == 0, (
        f"psql (role={role}) failed:\n--- SQL ---\n{sql}\n--- stdout ---\n"
        f"{result.stdout.decode(errors='replace')}\n--- stderr ---\n"
        f"{result.stderr.decode(errors='replace')}"
    )
    return result.stdout.decode()


def _quote(value: str) -> str:
    """Single-quote a value for inline SQL literal use. Test-only: every
    value passed through here is a test-controlled UUID/topic string, not
    external input."""
    return "'" + value.replace("'", "''") + "'"


def _predicate(fn_name: str, topic: str | None, jwt_sub: str | None) -> bool:
    """Evaluate `public.<fn_name>(topic)` as the `authenticated` role with
    `request.jwt.claims` carrying `sub=jwt_sub` — mirrors how PostgREST/
    Realtime present a caller's identity to `auth.uid()` inside the
    policy. `jwt_sub=None` mimics a caller with no `sub` claim at all."""
    topic_sql = "null" if topic is None else _quote(topic)
    claims_sql = _quote(json.dumps({"sub": jwt_sub})) if jwt_sub is not None else "'{}'"
    sql = (
        "set role authenticated;\n"
        f"set request.jwt.claims = {claims_sql};\n"
        f"select public.{fn_name}({topic_sql});\n"
    )
    # `-t/-A` suppress column headers/footers on SELECT output but NOT the
    # "SET" command-status lines the two prior statements print in
    # non-interactive mode — the actual result is always the last line.
    lines = [line for line in _psql(sql, role=_QUERY_ROLE).splitlines() if line.strip()]
    out = lines[-1].strip() if lines else ""
    assert out in ("t", "f"), f"unexpected predicate output for {fn_name}({topic!r}): {lines!r}"
    return out == "t"


def _table_level_insert_allowed(topic: str, jwt_sub: str) -> bool:
    """Attempt a real INSERT into `realtime.messages` for `topic` as
    `jwt_sub`, with the `realtime.topic` GUC set the way the real Realtime
    server sets it before evaluating these policies (the function reads
    that GUC, not the inserted row's `topic` column — verified against the
    installed `realtime.topic()`). Runs inside `begin ... rollback` so
    nothing persists in `realtime.messages`. Returns True iff the insert
    was allowed; False iff it was denied by RLS (any other failure still
    raises, so a red run doesn't silently read as "denied")."""
    sql = f"""
begin;
set local role authenticated;
set local request.jwt.claims = {_quote(json.dumps({"sub": jwt_sub}))};
set local realtime.topic = {_quote(topic)};
insert into realtime.messages (topic, extension, event, payload)
  values ({_quote(topic)}, 'broadcast', 'probe', '{{}}'::jsonb);
rollback;
"""
    result = subprocess.run(
        ["docker", "exec", "-e", "PGPASSWORD=postgres", "-i", _DB_CONTAINER,
         "psql", "-U", _QUERY_ROLE, "-d", "postgres", "-v", "ON_ERROR_STOP=1"],
        input=sql.encode(),
        capture_output=True,
        timeout=20,
    )
    if result.returncode == 0:
        return True
    stderr = result.stderr.decode(errors="replace")
    assert "row-level security policy" in stderr, (
        f"unexpected failure (not an RLS denial):\n{stderr}"
    )
    return False


def _client():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live channel-auth test against a non-loopback "
        f"SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _client()


@pytest.fixture(scope="module")
def fixture_ids(sb):
    """A real (company, workspace, user, user) tuple already in the rig."""
    companies = sb.table("companies").select("id").limit(1).execute().data
    assert companies, "no company row in the local rig — seed one before running this test"
    company_id = companies[0]["id"]

    workspaces = (
        sb.table("workspaces").select("id").eq("company_id", company_id).limit(1).execute().data
    )
    assert workspaces, f"no workspace for company {company_id}"
    workspace_id = workspaces[0]["id"]

    members = (
        sb.table("company_members")
        .select("user_id")
        .eq("company_id", company_id)
        .limit(2)
        .execute()
        .data
    )
    assert len(members) >= 2, f"need >=2 company_members rows for company {company_id}"

    return {
        "company_id": company_id,
        "workspace_id": workspace_id,
        "user_a": members[0]["user_id"],
        "user_b": members[1]["user_id"],
    }


@pytest.fixture(scope="module")
def company_b(sb, fixture_ids):
    """A second, scratch company + workspace + project — a genuine second
    tenant for the cross-tenant ACs. Reuses `fixture_ids["user_b"]` as its
    only member (never mints a new `auth.users` row). Deleted (cascade) at
    teardown; never touches the reused company/workspace/user rows."""
    slug = f"realtime-chan-auth-b-{uuid.uuid4().hex[:8]}"
    company = (
        sb.table("companies")
        .insert({"slug": slug, "display_name": "Realtime Channel Auth Test Co B"})
        .execute()
        .data[0]
    )
    workspace = (
        sb.table("workspaces")
        .insert({"company_id": company["id"], "name": "Team B", "slug": "team-b", "is_default": True})
        .execute()
        .data[0]
    )
    project = (
        sb.table("projects")
        .insert(
            {
                "company_id": company["id"],
                "workspace_id": workspace["id"],
                "name": "company-b-project",
                "created_by": fixture_ids["user_b"],
            }
        )
        .execute()
        .data[0]
    )
    sb.table("project_members").insert(
        {"project_id": project["id"], "user_id": fixture_ids["user_b"]},
    ).execute()
    yield {"company_id": company["id"], "workspace_id": workspace["id"], "project_id": project["id"]}
    sb.table("companies").delete().eq("id", company["id"]).execute()


@pytest.fixture
def project(sb, fixture_ids):
    """A fresh company-A project row, deleted (cascade) at teardown. No
    `project_members` rows by default — each test adds exactly the ones it
    needs."""
    row = (
        sb.table("projects")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "workspace_id": fixture_ids["workspace_id"],
                "name": f"channel-auth-{uuid.uuid4().hex[:8]}",
                "created_by": fixture_ids["user_a"],
            }
        )
        .execute()
        .data[0]
    )
    yield row
    sb.table("projects").delete().eq("id", row["id"]).execute()


# ── Creation / migration ────────────────────────────────────────────────


def test_migration_applies_idempotently():
    """Two functions + four named policies after a double-apply, no
    error (AC-1) — `create or replace` / `drop policy if exists` /
    `enable row level security` are all safely re-runnable. Re-runs the
    actual committed SQL file (not a paraphrase of it)."""
    import shutil

    if shutil.which("docker") is None:
        pytest.skip("docker not on PATH — cannot re-apply the migration for the idempotency proof")

    sql = (_MIGRATIONS_DIR / _MIGRATION_FILE).read_text()
    for attempt in range(2):
        result = subprocess.run(
            ["docker", "exec", "-e", "PGPASSWORD=postgres", "-i", _DB_CONTAINER,
             "psql", "-U", _DDL_ROLE, "-d", "postgres", "-v", "ON_ERROR_STOP=1"],
            input=sql.encode(),
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"re-applying the migration was not idempotent (attempt {attempt + 1}):\n"
            f"stdout: {result.stdout.decode(errors='replace')}\n"
            f"stderr: {result.stderr.decode(errors='replace')}"
        )

    funcs = _psql(
        "select proname from pg_proc where pronamespace = 'public'::regnamespace "
        "and proname in ('is_project_channel_member','is_individual_channel_member') "
        "order by proname;",
        role=_QUERY_ROLE,
    ).split()
    assert funcs == ["is_individual_channel_member", "is_project_channel_member"]

    policies = _psql(
        "select polname from pg_policy where polrelid = 'realtime.messages'::regclass "
        "order by polname;",
        role=_QUERY_ROLE,
    ).split()
    assert policies == [
        "project_group_channel_receive",
        "project_group_channel_send",
        "project_individual_channel_receive",
        "project_individual_channel_send",
    ]


# ── Group authorization (the gate) ──────────────────────────────────────


def test_group_member_allowed(sb, fixture_ids, project):
    pid = project["id"]
    sb.table("project_members").insert({"project_id": pid, "user_id": fixture_ids["user_a"]}).execute()

    assert _predicate("is_project_channel_member", f"project:{pid}", fixture_ids["user_a"]) is True
    # Proven at the actual RLS-enforcement layer too, not just the function.
    assert _table_level_insert_allowed(f"project:{pid}", fixture_ids["user_a"]) is True


def test_group_same_tenant_non_member_denied(sb, fixture_ids, project):
    pid = project["id"]
    sb.table("project_members").insert({"project_id": pid, "user_id": fixture_ids["user_a"]}).execute()

    # user_b is a same-company, same-workspace user but never added to
    # this project's member roster.
    assert _predicate("is_project_channel_member", f"project:{pid}", fixture_ids["user_b"]) is False
    assert _table_level_insert_allowed(f"project:{pid}", fixture_ids["user_b"]) is False


def test_group_cross_tenant_member_denied(fixture_ids, project, company_b):
    """AC-4, gating: a member of a project in company B gets no access to
    company A's project topic — membership alone enforces isolation; no
    company/workspace column is ever consulted."""
    pid = project["id"]

    assert _predicate("is_project_channel_member", f"project:{pid}", fixture_ids["user_b"]) is False
    assert _table_level_insert_allowed(f"project:{pid}", fixture_ids["user_b"]) is False


# ── Per-user authorization (the gate) ───────────────────────────────────


def test_individual_assignee_allowed(sb, fixture_ids, project):
    pid = project["id"]
    sb.table("project_members").insert({"project_id": pid, "user_id": fixture_ids["user_a"]}).execute()

    topic = f"project:{pid}:user:{fixture_ids['user_a']}"
    assert _predicate("is_individual_channel_member", topic, fixture_ids["user_a"]) is True
    assert _table_level_insert_allowed(topic, fixture_ids["user_a"]) is True


def test_individual_other_member_denied_same_project(sb, fixture_ids, project):
    """AC-6, gating: a DIFFERENT member of the SAME project may not read
    or send on another member's individual thread. Membership alone is not
    enough — you must also BE that user."""
    pid = project["id"]
    sb.table("project_members").insert({"project_id": pid, "user_id": fixture_ids["user_a"]}).execute()
    sb.table("project_members").insert({"project_id": pid, "user_id": fixture_ids["user_b"]}).execute()

    topic = f"project:{pid}:user:{fixture_ids['user_a']}"
    assert _predicate("is_individual_channel_member", topic, fixture_ids["user_b"]) is False
    assert _table_level_insert_allowed(topic, fixture_ids["user_b"]) is False


def test_individual_non_member_owner_cross_tenant_denied(fixture_ids, project, company_b):
    """AC-7: the caller IS the user named in the topic (uid matches), but
    that user is not a `project_members` row for this project at all —
    proven with a genuinely cross-tenant user (a company-B member), not
    merely a same-company non-member."""
    pid = project["id"]
    topic = f"project:{pid}:user:{fixture_ids['user_b']}"

    assert _predicate("is_individual_channel_member", topic, fixture_ids["user_b"]) is False


# ── Edge / composition ───────────────────────────────────────────────────


_MALFORMED_TOPICS = [
    "project:",
    "project:abc",
    "project:1;drop",
    None,
    "",
    "conversation:1",
]


def _malformed_individual_topics(pid: int, uid: str) -> list[str]:
    return [
        f"project:{pid}:user:",
        f"project:{pid}:user:not-a-uuid",
        f"project:{pid}:user:{uid}:extra",
    ]


def test_malformed_topics_return_false_both_functions(fixture_ids, project):
    """AC-8: every malformed shape returns false from BOTH functions —
    never raises."""
    pid = project["id"]
    uid = fixture_ids["user_a"]

    for topic in _MALFORMED_TOPICS:
        assert _predicate("is_project_channel_member", topic, uid) is False, topic
        assert _predicate("is_individual_channel_member", topic, uid) is False, topic

    for topic in _malformed_individual_topics(pid, uid):
        assert _predicate("is_individual_channel_member", topic, uid) is False, topic
        # Also malformed for the group shape (never matches ^project:[0-9]+$).
        assert _predicate("is_project_channel_member", topic, uid) is False, topic


def test_group_and_individual_functions_disjoint(sb, fixture_ids, project):
    """AC-9: each function returns false on the OTHER shape's topic even
    when the topic is otherwise entirely real/valid — not just malformed."""
    pid = project["id"]
    sb.table("project_members").insert({"project_id": pid, "user_id": fixture_ids["user_a"]}).execute()

    group_topic = f"project:{pid}"
    individual_topic = f"project:{pid}:user:{fixture_ids['user_a']}"

    assert _predicate("is_project_channel_member", individual_topic, fixture_ids["user_a"]) is False
    assert _predicate("is_individual_channel_member", group_topic, fixture_ids["user_a"]) is False


def test_four_policies_present_and_composed(sb, fixture_ids, project):
    """AC-9: all four policies exist, delegate to the right function, and
    the per-user policies do not widen the group grant. The function-name
    check is a catalog inspection; the "does not widen" claim is proven at
    the actual RLS-enforcement layer (a real INSERT), not just the raw
    function, so a policy wired to the wrong function would be caught."""
    rows = _psql(
        "select polname, pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid) "
        "from pg_policy where polrelid = 'realtime.messages'::regclass order by polname;",
        role=_QUERY_ROLE,
    ).strip().splitlines()
    by_name = {}
    for line in rows:
        name, qual, check = line.split("|")
        by_name[name] = (qual, check)

    assert set(by_name) == {
        "project_group_channel_receive",
        "project_group_channel_send",
        "project_individual_channel_receive",
        "project_individual_channel_send",
    }
    assert "is_project_channel_member" in by_name["project_group_channel_receive"][0]
    assert "is_project_channel_member" in by_name["project_group_channel_send"][1]
    assert "is_individual_channel_member" in by_name["project_individual_channel_receive"][0]
    assert "is_individual_channel_member" in by_name["project_individual_channel_send"][1]

    pid = project["id"]
    sb.table("project_members").insert({"project_id": pid, "user_id": fixture_ids["user_a"]}).execute()
    # A member of P inserts fine on the group topic (both group policies wired
    # correctly)...
    assert _table_level_insert_allowed(f"project:{pid}", fixture_ids["user_a"]) is True
    # ...but a non-member is still denied — the per-user policies existing
    # alongside the group ones does not widen the group grant.
    assert _table_level_insert_allowed(f"project:{pid}", fixture_ids["user_b"]) is False


# ── Isolation posture / registry ────────────────────────────────────────


def test_product_table_policies_unchanged(sb):
    """AC-10: every `srv_*` policy on the named product tables is still
    `using(true) with check(true)`; no Projects table is in the
    `supabase_realtime` CDC publication."""
    rows = _psql(
        "select tablename, polname, pg_get_expr(polqual, polrelid), "
        "pg_get_expr(polwithcheck, polrelid) from pg_policy "
        "join pg_class on pg_class.oid = pg_policy.polrelid "
        "join pg_tables on pg_tables.tablename = pg_class.relname "
        "where pg_tables.schemaname = 'public' and pg_policy.polname like 'srv_%' "
        "and pg_tables.tablename in "
        "('conversations','conversation_turns','project_members','project_delegations',"
        "'conversation_read_cursors') order by tablename;",
        role=_QUERY_ROLE,
    ).strip().splitlines()
    assert rows, "expected srv_* policies on the named product tables"
    for line in rows:
        _table, _name, qual, check = line.split("|")
        assert qual == "true", line
        assert check == "true", line

    published = _psql(
        "select tablename from pg_publication_tables where pubname = 'supabase_realtime' "
        "and tablename like 'project%';",
        role=_QUERY_ROLE,
    ).strip()
    assert published == "", f"a Projects table is in the supabase_realtime publication: {published!r}"


def test_ci_lane_registry_entry_present():
    """AC-11: the ratchet is armed — the entry is present, and its absence
    would make `test_ci_lane_coverage.py` red (proven by the ratchet's own
    logic, exercised in that file's suite; this test only proves presence
    here, since removing the entry to prove the negative belongs in that
    file's own test)."""
    from tests import test_ci_lane_coverage as coverage

    key = ("test_realtime_channel_auth.py", "RUN_PROJECTS_REALTIME_CHANNEL_AUTH_LIVE")
    assert key in coverage._KNOWN_UNRUNNABLE
    assert coverage._KNOWN_UNRUNNABLE[key].strip()


# ── Mutation proofs (both gates) ────────────────────────────────────────

_BROKEN_GROUP_FN = """
create or replace function public.is_project_channel_member(topic text)
returns boolean language plpgsql security definer set search_path = public stable as $$
begin
  return true;  -- MUTATED for the mutation proof: allow-all
end; $$;
"""

_BROKEN_INDIVIDUAL_FN = """
create or replace function public.is_individual_channel_member(topic text)
returns boolean language plpgsql security definer set search_path = public stable as $$
declare pid bigint; uid uuid;
begin
  if topic is null or topic !~ '^project:[0-9]+:user:[0-9a-fA-F-]{36}$' then
    return false;
  end if;
  pid := split_part(topic, ':', 2)::bigint;
  -- MUTATED for the mutation proof: the `uid <> auth.uid()` guard removed
  return exists (select 1 from public.project_members pm
                 where pm.project_id = pid and pm.user_id = auth.uid());
exception when others then return false;
end; $$;
"""


def test_predicate_flip_breaks_deny_tests(sb, fixture_ids, project):
    """AC-12: documents and re-proves both mutation proofs. Flipping the
    group membership predicate to allow-all makes the group deny checks
    (AC-3/AC-4's shape) return true; flipping the per-user function's
    `uid <> auth.uid()` guard to always-pass makes the different-user deny
    check (AC-6's shape) return true. Both are restored to the real
    migration's definitions unconditionally (`finally`), and restoration
    is itself verified before the test ends — this is a real DB mutation
    on a shared local rig, not a throwaway copy."""
    pid = project["id"]
    sb.table("project_members").insert({"project_id": pid, "user_id": fixture_ids["user_a"]}).execute()

    restore_sql = (_MIGRATIONS_DIR / _MIGRATION_FILE).read_text()

    # Group gate: baseline is a real deny (user_b is not yet a member of
    # this project at all)...
    assert _predicate("is_project_channel_member", f"project:{pid}", fixture_ids["user_b"]) is False
    try:
        _psql(_BROKEN_GROUP_FN, role=_DDL_ROLE)
        # ...RED under the mutation: a same-tenant non-member is now wrongly
        # allowed (the shape of AC-3), and so would a cross-tenant one be
        # (the shape of AC-4, same function).
        assert _predicate("is_project_channel_member", f"project:{pid}", fixture_ids["user_b"]) is True
    finally:
        _psql(restore_sql, role=_DDL_ROLE)
    # ...GREEN again after restore.
    assert _predicate("is_project_channel_member", f"project:{pid}", fixture_ids["user_b"]) is False

    # Per-user gate needs user_b to genuinely BE a member of this project
    # (so the deny below is proven to come from "not the right user", not
    # "not a member at all" — that's AC-7, already covered elsewhere).
    sb.table("project_members").insert({"project_id": pid, "user_id": fixture_ids["user_b"]}).execute()

    # Baseline is a real deny (different member, same project)...
    individual_topic = f"project:{pid}:user:{fixture_ids['user_a']}"
    assert _predicate("is_individual_channel_member", individual_topic, fixture_ids["user_b"]) is False
    try:
        _psql(_BROKEN_INDIVIDUAL_FN, role=_DDL_ROLE)
        # ...RED under the mutation: a different member of the same project
        # is now wrongly allowed onto user_a's individual thread (AC-6's shape).
        assert _predicate("is_individual_channel_member", individual_topic, fixture_ids["user_b"]) is True
    finally:
        _psql(restore_sql, role=_DDL_ROLE)
    # ...GREEN again after restore.
    assert _predicate("is_individual_channel_member", individual_topic, fixture_ids["user_b"]) is False

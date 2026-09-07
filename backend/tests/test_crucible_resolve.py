"""`crucible.resolve` — turning a cited claim id back into where it came from.

WHAT THIS SURFACE MUST NEVER DO. A resolver that only proves the happy path
proves nothing about the reason it exists: distinguishing "never existed"
from "dropped for space" from "found, but nothing to point at", and refusing
to leak `properties` (which carries account/customer names) even when asked
about a signal that has them. Every test below either asserts one of those
distinct states or asserts the allowlist held under pressure.
"""
from __future__ import annotations

import dataclasses
import uuid

import pytest

from tests import _fake_supabase

CO = "co-resolve-test"
OTHER_CO = "co-resolve-other-tenant"

# SQLite mirror of the two tables `crucible.resolve` reads that are not in
# conftest's shared `_FAKE_SCHEMA`: `crucible_runs` (mirrors
# `test_routes_crucible.py`'s own local DDL for the same reason — these
# tables are newer than the shared schema) and `call_index`
# (`supabase/migrations/20260802160000_call_index.sql`, trimmed to the
# columns this resolver actually selects).
_DDL = """
CREATE TABLE IF NOT EXISTS crucible_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id         TEXT NOT NULL,
    conversation_id    INTEGER,
    goal_definition_id INTEGER,
    goal_text          TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'draft',
    error_code         TEXT,
    error              TEXT,
    coverage_notes     TEXT NOT NULL DEFAULT '[]',
    prioritisation     TEXT NOT NULL DEFAULT '{}',
    claim_count        INTEGER NOT NULL DEFAULT 0,
    tokens_spent       INTEGER NOT NULL DEFAULT 0,
    started_at         TEXT,
    finished_at        TEXT,
    heartbeat_at       TEXT,
    created_by         TEXT,
    artifact_id        INTEGER,
    report_body_hash   TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS call_index (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id   TEXT NOT NULL,
    provider     TEXT NOT NULL DEFAULT 'fireflies',
    external_id  TEXT NOT NULL DEFAULT '',
    title        TEXT,
    call_date    TEXT
);
"""


@pytest.fixture
def resolve_env(isolated_settings):
    _fake_supabase.get_fake_db().executescript(_DDL)
    yield isolated_settings["supabase"]


def _run(company_id=CO, prioritisation=None):
    from app.db import crucible_runs as runs_db

    row = runs_db.create(company_id, goal_text="raise net revenue retention")
    if prioritisation is not None:
        runs_db.update(row["id"], company_id, prioritisation=prioritisation)
    return row["id"]


def _signal(client, *, id=None, enterprise_id=CO, content="the export is slow",
            source_type="call_transcript", valid_at="2026-08-01T00:00:00+00:00",
            properties=None, provenance=None, source_call_id=None):
    sid = id or str(uuid.uuid4())
    client.table("kg_signal").insert({
        "id": sid, "enterprise_id": enterprise_id, "kind": "pain_point",
        "source_type": source_type, "content": content,
        "properties": properties or {}, "provenance": provenance or {},
        "valid_at": valid_at, "transaction_at": valid_at,
        "source_call_id": source_call_id,
    }).execute()
    return sid


def _call(client, *, company_id=CO, title="Acme + Vendor Quarterly Review",
          call_date="2026-07-15T00:00:00+00:00"):
    res = client.table("call_index").insert({
        "company_id": company_id, "provider": "fireflies",
        "external_id": str(uuid.uuid4()), "title": title, "call_date": call_date,
    }).execute()
    return res.data[0]["id"]


def _as_dict(result):
    return dataclasses.asdict(result)


# ── the two resolved shapes ──────────────────────────────────────────────────

def test_resolves_a_call_pointer(resolve_env):
    """The good pointer: a signal linked to a catalogued call resolves to
    that call's title and date, not a UUID."""
    from app.crucible.resolve import resolve_claim_source

    client = resolve_env
    call_id = _call(client, title="Fictive Corp + Vendor Renewal Call")
    sid = _signal(client, source_call_id=call_id, content="pricing came up")
    run_id = _run()

    result = resolve_claim_source(CO, run_id, sid)

    assert result.status == "resolved"
    assert result.pointer == {
        "kind": "call", "title": "Fictive Corp + Vendor Renewal Call",
        "call_date": "2026-07-15T00:00:00+00:00",
    }
    assert result.content == "pricing came up"
    assert result.source_type == "call_transcript"


def test_resolves_a_human_meaningful_doc_pointer(resolve_env):
    """No linked call, but a real document label — still a usable pointer."""
    from app.crucible.resolve import resolve_claim_source

    client = resolve_env
    sid = _signal(
        client, provenance={"doc": "slack/#mvp-product (part 2/3)"})
    run_id = _run()

    result = resolve_claim_source(CO, run_id, sid)

    assert result.status == "resolved"
    assert result.pointer == {"kind": "doc", "label": "slack/#mvp-product (part 2/3)"}


# ── the three unresolved states ──────────────────────────────────────────────

def test_sync_batch_label_is_not_a_pointer(resolve_env):
    """THE TRAP THIS TICKET EXISTS TO CATCH. A non-empty `provenance.doc` that
    is a connector sync-batch counter (`kg_ingest.runner`'s
    `f"{provider}-sync-batch-{i}"`) names no document a human could open —
    it must resolve as found-with-no-pointer, not as a citation."""
    from app.crucible.resolve import resolve_claim_source

    client = resolve_env
    sid = _signal(client, provenance={"doc": "github-sync-batch-0"})
    run_id = _run()

    result = resolve_claim_source(CO, run_id, sid)

    assert result.status == "no_pointer"
    assert result.pointer is None
    # STILL the allowlisted content — "no pointer" is not "no data".
    assert result.content == "the export is slow"


@pytest.mark.parametrize("doc", [
    "jira-sync-batch-39", "clickup-sync-batch-11", "fireflies-sync-batch-0",
])
def test_every_measured_sync_batch_shape_is_rejected(resolve_env, doc):
    from app.crucible.resolve import resolve_claim_source

    client = resolve_env
    sid = _signal(client, provenance={"doc": doc})
    run_id = _run()

    assert resolve_claim_source(CO, run_id, sid).status == "no_pointer"


def test_a_human_report_title_with_slashes_and_digits_still_resolves(resolve_env):
    """Guards against an over-eager regex: a real title that merely CONTAINS
    digits and dashes must not be mistaken for the batch shape."""
    from app.crucible.resolve import resolve_claim_source

    client = resolve_env
    sid = _signal(client, provenance={
        "doc": "Competitive Intelligence report · September 2026 (part 8/10)"})
    run_id = _run()

    result = resolve_claim_source(CO, run_id, sid)
    assert result.status == "resolved"
    assert result.pointer["kind"] == "doc"


def test_not_found_anywhere(resolve_env):
    """Neither `kg_signal` nor this run's prose claims — the plain miss."""
    from app.crucible.resolve import resolve_claim_source

    client = resolve_env
    run_id = _run()

    result = resolve_claim_source(CO, run_id, "claim-that-never-existed")

    assert result.status == "not_found"
    assert result.content is None
    assert result.pointer is None


def test_dropped_for_space_is_distinct_from_not_found(resolve_env):
    """A citation the run once knew about but could not fit under
    `prose.rows_for_recovery`'s cap. Must NOT collapse into `not_found` —
    that is precisely the distinction this module exists to preserve.

    Written against the persisted meta SHAPE directly
    (`prose.TRUNCATED_KEY`/`prose_claims_truncated`) rather than through
    `prose.rows_for_recovery` itself, so this test does not depend on
    whichever side of the sibling fix's merge happens to be checked out —
    `resolve._truncated_ids` reads the same key either way.
    """
    from app.crucible.resolve import resolve_claim_source

    client = resolve_env
    dropped_id = str(uuid.uuid4())
    run_id = _run(prioritisation={
        "prose_claims": [],
        "prose_claims_truncated": [dropped_id],
    })

    result = resolve_claim_source(CO, run_id, dropped_id)

    assert result.status == "dropped_for_space"
    assert result.content is None
    assert result.pointer is None
    # AND a genuinely-unknown id on the SAME run still reports not_found —
    # the two states are not conflated in either direction.
    other = resolve_claim_source(CO, run_id, "some-other-id")
    assert other.status == "not_found"


# ── the run-scoped prose space ───────────────────────────────────────────────

def test_prose_claim_resolves_via_doc_pointer(resolve_env):
    """A claim id that only ever lived in `crucible_runs.prioritisation`
    (never `kg_signal`, by `crucible.prose`'s own design) still resolves."""
    from app.crucible.resolve import resolve_claim_source

    client = resolve_env
    prose_id = str(uuid.uuid4())
    run_id = _run(prioritisation={"prose_claims": [{
        "id": prose_id, "kind": "pain_point", "source_type": "prose",
        "content": "the onboarding flow confused three testers",
        "properties": {}, "provenance": {"doc": "notes.txt"},
        "valid_at": "2026-08-10T00:00:00+00:00",
        "created_at": "2026-08-10T00:00:00+00:00", "source_id": None,
    }]})

    result = resolve_claim_source(CO, run_id, prose_id)

    assert result.status == "resolved"
    assert result.pointer == {"kind": "doc", "label": "notes.txt"}
    assert result.content == "the onboarding flow confused three testers"


# ── the allowlist ─────────────────────────────────────────────────────────────

def test_properties_never_appear_in_the_output(resolve_env):
    """The row carries an account name in `properties`; the result must not,
    under any key, at any depth this dataclass could reach."""
    from app.crucible.resolve import resolve_claim_source

    client = resolve_env
    sid = _signal(client, properties={
        "account": "Northwind Traders", "customer": "Northwind Traders",
        "organization": "Northwind Traders", "company": "Northwind Traders",
    }, provenance={"doc": "calls/northwind-q3.txt"})
    run_id = _run()

    result = resolve_claim_source(CO, run_id, sid)
    payload = _as_dict(result)

    assert "properties" not in payload
    serialized = str(payload)
    assert "Northwind Traders" not in serialized


# ── tenant scoping — the security-relevant assertion ─────────────────────────

def test_a_claim_from_another_enterprise_does_not_resolve(resolve_env):
    """Company B must never be able to resolve Company A's `kg_signal` row,
    even holding the exact id and a run of its own to ask through."""
    from app.crucible.resolve import resolve_claim_source

    client = resolve_env
    other_sid = _signal(
        client, enterprise_id=OTHER_CO, content="Other Co's private detail")
    my_run = _run(company_id=CO)

    result = resolve_claim_source(CO, my_run, other_sid)

    assert result.status == "not_found"
    assert result.content is None
    # AND the same id genuinely does resolve for its own tenant, proving the
    # miss above is the tenant filter and not a fixture mistake.
    other_run = _run(company_id=OTHER_CO)
    assert resolve_claim_source(
        OTHER_CO, other_run, other_sid).status in ("resolved", "no_pointer")


def test_a_prose_claim_from_another_companys_run_does_not_resolve(resolve_env):
    """Prose claims live on the RUN, not on a global table — so the tenant
    boundary here is "is this your run", not a column filter. A claim id
    minted for company A's run must not resolve when asked through a run
    that belongs to company B, even if B somehow learns the id."""
    from app.crucible.resolve import resolve_claim_source

    client = resolve_env
    prose_id = str(uuid.uuid4())
    a_run = _run(company_id=CO, prioritisation={"prose_claims": [{
        "id": prose_id, "kind": "pain_point", "source_type": "prose",
        "content": "a fact only Company A's run ever read",
        "properties": {}, "provenance": {"doc": "a-private-file.txt"},
        "valid_at": "2026-08-10T00:00:00+00:00",
        "created_at": "2026-08-10T00:00:00+00:00", "source_id": None,
    }]})
    b_run = _run(company_id=OTHER_CO)

    # B asks about A's run id directly. `runs_db.get(a_run, OTHER_CO)` is
    # None (tenant filter in the query), so the resolver must not read A's
    # prioritisation blob at all.
    result = resolve_claim_source(OTHER_CO, a_run, prose_id)
    assert result.status == "not_found"

    # And through B's OWN run, the id is simply unknown to it.
    assert resolve_claim_source(OTHER_CO, b_run, prose_id).status == "not_found"

    # Sanity: A can still resolve its own claim.
    assert resolve_claim_source(CO, a_run, prose_id).status == "resolved"

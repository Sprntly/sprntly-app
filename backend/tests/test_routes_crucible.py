"""/v1/crucible — the Goal Analysis run surface.

Four things this surface can get wrong in ways a user or a customer would feel:

  1. THE GATE. `crucible` is an allowlist feature and the entitlement fails
     CLOSED, unlike its grandfathered siblings. A company without the flag must
     be refused at the ROUTE, because the UI gate is cosmetic — the client
     decides what to render, the server decides what runs.
  2. TENANCY. A run belongs to a company. Another company's member must not
     read it, confirm it, or prove it exists — every cross-tenant case asserts
     404 specifically, since a 403 is itself a disclosure.
  3. I9. Analysis never runs on a definition no human confirmed. Not on a miss,
     and NOT ON A CANDIDATE either: finding the metric in the company's own KPI
     tree is a strong proposal, but adopting it is still the user's act, and
     once a run has produced confident output the difference is invisible.
  4. THE ROW IS THE JOB. A failure has to LIST, with a code safe to render. A
     run that vanishes is the shape that makes a feature look broken.
"""
from __future__ import annotations

import uuid

import pytest

from tests import _fake_supabase
from tests._company_helpers import company_client, seed_company, supabase_bearer

# SQLite translation of the crucible tables in
# supabase/migrations/20260819100000_crucible_core.sql. Only the columns these
# routes touch; the defaults matter, because the create path relies on them.
_DDL = """
CREATE TABLE IF NOT EXISTS crucible_goal_definitions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id            TEXT NOT NULL,
    raw_goal_text         TEXT NOT NULL DEFAULT '',
    metric_name           TEXT NOT NULL DEFAULT '',
    definition_text       TEXT NOT NULL DEFAULT '',
    definition_source_ref TEXT,
    source_ref            TEXT,
    currency              TEXT NOT NULL,
    direction             TEXT NOT NULL DEFAULT 'increase',
    status                TEXT NOT NULL DEFAULT 'unresolved',
    origin                TEXT,
    target_value          REAL,
    horizon_weeks         INTEGER,
    population            TEXT NOT NULL DEFAULT '{}',
    conflicts_found       TEXT NOT NULL DEFAULT '[]',
    confirmed_by_user_at  TEXT,
    confirmed_by_user_id  TEXT,
    definition_hash       TEXT NOT NULL DEFAULT '',
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
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
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS crucible_findings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL,
    company_id     TEXT NOT NULL,
    statement      TEXT NOT NULL DEFAULT '',
    claim_ids      TEXT NOT NULL DEFAULT '[]',
    adjudication   TEXT,
    impact_value   REAL,
    currency       TEXT,
    confidence_band TEXT,
    surfaced_by    TEXT NOT NULL DEFAULT '[]',
    assumed_params TEXT NOT NULL DEFAULT '[]',
    impact         TEXT NOT NULL DEFAULT '{}',
    confidence     TEXT NOT NULL DEFAULT '{}',
    tier           TEXT
);
CREATE TABLE IF NOT EXISTS crucible_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL,
    company_id      TEXT NOT NULL,
    label           TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    stopped_at_stage TEXT,
    claim_ids       TEXT NOT NULL DEFAULT '[]'
);
"""


def _enable(company_id: str, on: bool = True) -> None:
    from app.db.client import require_client

    require_client().table("companies").update(
        {"feature_flags": {"crucible": on}}
    ).eq("id", company_id).execute()


@pytest.fixture
def crucible_env(isolated_settings):
    _fake_supabase.get_fake_db().executescript(_DDL)
    yield


@pytest.fixture
def ctx(crucible_env, monkeypatch):
    c = company_client(monkeypatch)
    _enable(c.company_id)
    return c


def _start(ctx, goal="raise net revenue retention", **kw):
    return ctx.client.post("/v1/crucible", json={"goal_text": goal, **kw})


# ─── 1. The gate is the server's, not the UI's ───────────────────────────────

def test_a_company_without_the_flag_is_refused_at_the_route(crucible_env, monkeypatch):
    """The composer chip is hidden for this company, but hiding a control is
    cosmetic. A direct POST has to be refused on its own."""
    c = company_client(monkeypatch)          # no _enable
    assert _start(c).status_code == 403


def test_the_gate_fails_closed_rather_than_grandfathering(crucible_env, monkeypatch):
    """Its siblings default ON for companies predating them. This one must not:
    an unfinished experimental engine reaching every tenant by default is the
    failure mode the allowlist exists to prevent."""
    c = company_client(monkeypatch)
    _enable(c.company_id, on=False)
    assert _start(c).status_code == 403
    assert c.client.get("/v1/crucible").status_code == 403


def test_an_enabled_company_gets_through(ctx):
    assert _start(ctx).status_code == 200


# ─── 2. Tenancy ──────────────────────────────────────────────────────────────

def test_another_companys_run_is_404_not_403(ctx, monkeypatch):
    """403 would confirm the run exists. A foreign id must be indistinguishable
    from an id that was never issued."""
    run_id = _start(ctx).json()["id"]

    other_user = "other-" + uuid.uuid4().hex[:8]
    other_company = seed_company(user_id=other_user, slug="other")
    _enable(other_company)
    from fastapi.testclient import TestClient
    import app.main as main_mod

    intruder = TestClient(main_mod.app, headers=supabase_bearer(other_user))
    assert intruder.get(f"/v1/crucible/{run_id}").status_code == 404
    assert intruder.post(
        f"/v1/crucible/{run_id}/confirm", json={"definition_text": "mine now"}
    ).status_code == 404


def test_the_listing_shows_only_this_companys_runs(ctx, monkeypatch):
    _start(ctx, goal="ours")
    other_user = "other-" + uuid.uuid4().hex[:8]
    other_company = seed_company(user_id=other_user, slug="other")
    _enable(other_company)
    from fastapi.testclient import TestClient
    import app.main as main_mod

    intruder = TestClient(main_mod.app, headers=supabase_bearer(other_user))
    intruder.post("/v1/crucible", json={"goal_text": "theirs"})

    mine = [r["goal_text"] for r in ctx.client.get("/v1/crucible").json()["runs"]]
    assert mine == ["ours"]


# ─── 3. I9 — a human confirms, always ────────────────────────────────────────

def test_a_new_run_stops_at_confirmation_and_analyses_nothing(ctx):
    """No KPI tree here, so Stage 0 misses and asks. The point is that it STOPS
    — an unconfirmed goal must not spend a run."""
    body = _start(ctx).json()
    assert body["status"] == "awaiting_confirmation"
    assert body["claim_count"] == 0


def test_a_candidate_from_the_companys_own_kpi_tree_still_waits_for_a_human(
    ctx, monkeypatch
):
    """THE ONE THAT MATTERS. A match in the company's own tree is a strong
    proposal, and running on it would be an inference wearing adoption's
    clothes. It is prefilled, not accepted."""
    from app.kpi_tree import KpiTree, NorthStar

    tree = KpiTree(
        north_star=NorthStar(
            metric="Net Revenue Retention (NRR)",
            description="expansion minus churn across renewing accounts",
        ),
        primary_metrics=[],
        secondary_signals=[],
    )
    monkeypatch.setattr("app.kpi_tree.load_kpi_tree", lambda cid: tree)

    body = _start(ctx, goal="improve net revenue retention").json()
    assert body["status"] == "awaiting_confirmation"

    detail = ctx.client.get(f"/v1/crucible/{body['id']}").json()
    proposed = _prioritisation(body["id"])["proposed_definition"]
    assert "expansion minus churn" in proposed
    assert detail["findings"] == []


def test_confirming_locks_the_definition_with_who_and_when(ctx):
    """I9 at the storage layer: `locked` is the state that authorises spending,
    so it must carry the user who authorised it."""
    run_id = _start(ctx).json()["id"]
    ctx.client.post(
        f"/v1/crucible/{run_id}/confirm",
        json={"definition_text": "renewal-cohort revenue, net of churn"},
    )
    rows = _table("crucible_goal_definitions")
    assert len(rows) == 1
    assert rows[0]["status"] == "locked"
    assert rows[0]["confirmed_by_user_id"] == ctx.user_id
    assert rows[0]["confirmed_by_user_at"]
    # The user typed these words, so they are theirs — not their system's.
    assert rows[0]["origin"] == "elicited"
    assert rows[0]["definition_hash"]


def test_the_locked_definition_is_the_users_words_verbatim(ctx):
    """A paraphrase is a different metric, asserted by us. "revenue" tidied into
    "recognised revenue" answers a question nobody asked."""
    run_id = _start(ctx).json()["id"]
    words = "revenue, as finance books it, excluding one-offs"
    ctx.client.post(f"/v1/crucible/{run_id}/confirm", json={"definition_text": words})
    assert _table("crucible_goal_definitions")[0]["definition_text"] == words


def test_confirming_a_run_that_is_not_waiting_is_refused(ctx):
    """The confirm path is the only door from awaiting_confirmation onward.
    Re-posting it must not start a second analysis on the same row."""
    run_id = _start(ctx).json()["id"]
    ctx.client.post(f"/v1/crucible/{run_id}/confirm", json={"definition_text": "x"})
    again = ctx.client.post(
        f"/v1/crucible/{run_id}/confirm", json={"definition_text": "y"}
    )
    assert again.status_code == 409
    assert len(_table("crucible_goal_definitions")) == 1


# ─── 4. The row is the job ───────────────────────────────────────────────────

def test_a_run_with_no_signals_fails_visibly_rather_than_vanishing(ctx):
    """An empty knowledge graph is a legitimate answer, but it has to be an
    ANSWER. A run that disappears is why a feature looks broken."""
    run_id = _start(ctx).json()["id"]
    ctx.client.post(f"/v1/crucible/{run_id}/confirm", json={"definition_text": "d"})
    row = ctx.client.get(f"/v1/crucible/{run_id}").json()
    assert row["status"] == "failed"
    assert row["error_code"] == "no_evidence"
    assert run_id in [r["id"] for r in ctx.client.get("/v1/crucible").json()["runs"]]


def test_raw_error_text_is_never_returned_to_the_client(ctx, monkeypatch):
    """`error` holds whatever the exception said — a transport error carries
    URLs, a provider error carries the provider's own message. The closed-set
    code is the part that is safe to render."""
    import app.routes.crucible as mod

    monkeypatch.setattr(
        mod, "_load_signals",
        lambda cid: (_ for _ in ()).throw(RuntimeError("https://internal.host/secret")),
    )
    run_id = _start(ctx).json()["id"]
    ctx.client.post(f"/v1/crucible/{run_id}/confirm", json={"definition_text": "d"})
    body = ctx.client.get(f"/v1/crucible/{run_id}").json()
    assert body["error_code"] == "internal"
    assert "error" not in body
    assert "internal.host" not in str(body)


def test_a_created_run_is_durable_before_any_work_starts(ctx):
    """The row is created first so the panel has an id to poll and a process
    death is recoverable by the sweep rather than invisible."""
    body = _start(ctx).json()
    assert body["id"] >= 1
    assert body["created_at"]
    assert _table("crucible_runs")[0]["heartbeat_at"]


def test_the_sweep_fails_runs_whose_worker_stopped_reporting(ctx):
    """Recurring, not startup-only: a process that dies at 03:00 must not leave
    a row spinning until the next deploy. `custom_artifacts` shipped this
    startup-only and had to be fixed later."""
    from app.db import crucible_runs as runs_db

    run_id = _start(ctx).json()["id"]
    runs_db.update(run_id, ctx.company_id, status="running",
                   heartbeat_at="2020-01-01T00:00:00+00:00")
    assert runs_db.sweep_orphans() == 1
    row = ctx.client.get(f"/v1/crucible/{run_id}").json()
    assert row["status"] == "failed" and row["error_code"] == "interrupted"


def test_the_sweep_leaves_a_live_run_alone(ctx):
    from app.db import crucible_runs as runs_db

    run_id = _start(ctx).json()["id"]
    runs_db.update(run_id, ctx.company_id, status="running")
    runs_db.heartbeat(run_id, ctx.company_id)
    assert runs_db.sweep_orphans() == 0


def test_an_unlocked_definition_is_refused_by_the_db_layer(ctx):
    """Stated where the caller can see it, so a mistake is a readable error
    rather than a Postgres constraint violation at 3am."""
    from app.db import crucible_runs as runs_db
    from app.crucible.types import GoalDefinition

    unlocked = GoalDefinition(
        id="", raw_goal_text="g", metric_name="m", definition_text="d",
        currency="accounts", direction="increase", status="candidate",
    )
    with pytest.raises(ValueError, match="I9"):
        runs_db.save_definition(ctx.company_id, unlocked)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _table(name: str) -> list[dict]:
    from app.db.client import require_client

    return require_client().table(name).select("*").execute().data or []


def _prioritisation(run_id: int) -> dict:
    import json

    raw = [r for r in _table("crucible_runs") if r["id"] == run_id][0]["prioritisation"]
    return json.loads(raw) if isinstance(raw, str) else raw

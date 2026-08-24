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
    -- 20260820120000_crucible_run_report_document.sql. The report as an
    -- editable document, and the fingerprint that says whether it still is
    -- what the run rendered.
    artifact_id        INTEGER,
    report_body_hash   TEXT,
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
    # Confirming settles the DEFINITION; approving the plan is what spends a
    # run. Both gates, then the failure.
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})
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
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})
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


# ─── The two the reviewer caught: both invisible under pytest ────────────────

def test_confirm_is_async_so_it_does_not_die_on_the_worker_thread(ctx):
    """A sync `def` handler runs on FastAPI's anyio worker thread, where
    `get_event_loop()` raises — every confirm would 500 in PRODUCTION while
    passing here, and the row would already be flipped to `running`, bricked
    behind its own 409. Asserted on the function, because the pytest branch is
    exactly what hides the difference at runtime."""
    import inspect

    import app.routes.crucible as mod

    assert inspect.iscoroutinefunction(mod.confirm), (
        "confirm must be `async def`: a sync handler has no running loop"
    )
    assert inspect.iscoroutinefunction(mod.start)


def test_the_orphan_sweep_is_actually_wired_to_the_scheduler(ctx):
    """A sweep nobody calls heals nothing. `custom_artifacts` shipped exactly
    this and had to be fixed later — the row spun until the next deploy, which
    on prod is days."""
    import inspect

    import app.scheduler as scheduler

    body = inspect.getsource(scheduler._run_orphan_ask_job_sweep)
    assert "crucible_runs import sweep_orphans" in body


def test_a_double_confirm_cannot_start_two_analyses(ctx):
    """Read-then-write let both requests see `awaiting_confirmation` and both
    proceed — two locked definitions and two sets of findings on one row, which
    is invisible afterwards because each half looks correct. A double-click is
    the ordinary way to produce it."""
    from app.db import crucible_runs as runs_db

    run_id = _start(ctx).json()["id"]
    first = runs_db.claim_for_confirmation(run_id, ctx.company_id)
    second = runs_db.claim_for_confirmation(run_id, ctx.company_id)
    assert first is not None
    assert second is None


def test_the_client_can_see_what_stage_0_is_asking_for(ctx, monkeypatch):
    """A run that reports `awaiting_confirmation` without saying what it is
    waiting FOR leaves the panel a blank box. The proposal was written to the
    row and returned by nothing."""
    from app.kpi_tree import KpiTree, NorthStar

    tree = KpiTree(
        north_star=NorthStar(metric="Net Revenue Retention (NRR)",
                             description="expansion minus churn"),
        primary_metrics=[], secondary_signals=[],
    )
    monkeypatch.setattr("app.kpi_tree.load_kpi_tree", lambda cid: tree)
    run_id = _start(ctx, goal="improve net revenue retention").json()["id"]

    body = ctx.client.get(f"/v1/crucible/{run_id}").json()
    assert "expansion minus churn" in body["prioritisation"]["proposed_definition"]
    assert body["prioritisation"]["ask"] or body["prioritisation"]["resolution"]


def test_the_signal_read_is_ordered_because_it_is_paged(ctx):
    """Postgres may return an unordered query in any order, so `range()` without
    `order()` can repeat one row across pages and drop another — the corpus
    would differ run to run and reproducibility is the whole claim."""
    import inspect

    import app.routes.crucible as mod

    src = inspect.getsource(mod._load_signals)
    assert '.order(' in src, "a paged read must be ordered"


def test_the_ranking_survives_the_round_trip(ctx):
    """THE RANK IS THE INSERTION ORDER, and nothing else can reconstruct it.

    `_rank` puts an authoritative CONFLICT first regardless of size, because
    two sources that may both speak disagreeing is worth more than either
    claim. Re-reading the rows ordered by `impact_value` threw that away and
    sent an unsized conflict to the bottom — while the `tier` written at rank
    time still said `deep`, so the row claimed a standing its position
    contradicted.
    """
    from app.db import crucible_runs as runs_db

    run_id = _start(ctx).json()["id"]
    # Saved in the order `_rank` produced: conflict first, then by size, with
    # the unsizeable one last.
    runs_db.save_findings(run_id, ctx.company_id, [
        {"statement": "conflict", "claim_ids": ["c"], "impact_value": None,
         "adjudication": "conflict", "currency": "accounts",
         "confidence_band": "low", "tier": "deep"},
        {"statement": "big", "claim_ids": ["b"], "impact_value": 9.0,
         "currency": "accounts", "confidence_band": "low", "tier": "deep"},
        {"statement": "small", "claim_ids": ["s"], "impact_value": 1.0,
         "currency": "accounts", "confidence_band": "low", "tier": "shallow"},
    ], [])

    findings = ctx.client.get(f"/v1/crucible/{run_id}").json()["findings"]
    assert [f["statement"] for f in findings] == ["conflict", "big", "small"]
    # And the tier still matches the position it was written for.
    assert [f["tier"] for f in findings] == ["deep", "deep", "shallow"]


def test_an_unsized_finding_is_never_rendered_as_zero(ctx):
    """I3 at the transport layer: `impact_value` must arrive as null, not 0.
    They read almost the same and lead to opposite decisions."""
    from app.db import crucible_runs as runs_db

    run_id = _start(ctx).json()["id"]
    runs_db.save_findings(run_id, ctx.company_id, [
        {"statement": "unsized", "claim_ids": ["u"], "impact_value": None,
         "currency": "accounts", "confidence_band": "low"},
    ], [])
    findings = ctx.client.get(f"/v1/crucible/{run_id}").json()["findings"]
    assert findings[0]["impact_value"] is None


def test_the_ledger_comes_back_with_its_claim_ids(ctx):
    """A rejection you cannot reopen is a dismissal. The considered list is
    what makes the ranking credible, so the ids have to survive the round
    trip."""
    from app.db import crucible_runs as runs_db

    run_id = _start(ctx).json()["id"]
    runs_db.save_findings(run_id, ctx.company_id, [], [
        {"label": "onboarding", "reason": "one conversation echoing",
         "stopped_at_stage": "verification", "claim_ids": ["c1", "c2"]},
    ])
    considered = ctx.client.get(f"/v1/crucible/{run_id}").json()["considered"]
    assert considered[0]["claim_ids"] == ["c1", "c2"]
    assert considered[0]["stopped_at_stage"] == "verification"


# ─── End to end through execute_run, which is where the last fixes failed ────

def _signal(company_id: str, i: int, *, doc: str | None = "doc-a",
            embedding=None, kind: str = "finding") -> dict:
    from app.db.client import require_client

    row = {
        "id": f"sig-{i:04d}", "enterprise_id": company_id, "kind": kind,
        "source_type": "customer_voice", "content": f"signal {i}",
        "properties": {"customer": f"Acct{i % 5}"},
        "provenance": {"doc": doc} if doc else {},
        "valid_at": f"2026-0{1 + i % 6}-1{i % 9}T00:00:00+00:00",
        "created_at": "2026-08-19T00:00:00+00:00",
        "transaction_at": "2026-08-19T00:00:00+00:00",
    }
    if embedding is not None:
        row["embedding"] = embedding
    require_client().table("kg_signal").insert(row).execute()
    return row


def test_the_signal_read_actually_selects_what_the_code_reads(ctx):
    """THE ONE THE UNIT TESTS COULD NOT SEE. `_artifact_id` reads `provenance`
    while `_load_signals` never selected it, so every claim came out
    unattributed and every run shipped a coverage note saying no signal
    recorded its document — false, and invisible because the fake Supabase
    used to ignore column projection entirely. Asserted THROUGH the route."""
    import app.routes.crucible as mod

    for i in range(4):
        _signal(ctx.company_id, i, doc="slack/#demos",
                embedding=str([0.1 * (i + 1)] * 4))

    rows = mod._load_signals(ctx.company_id)
    assert rows, "no signals read"
    assert all("provenance" in r for r in rows), "provenance is not selected"
    # And deliberately NOT the embeddings: 1536 floats per row alongside
    # everything else times the statement out on a real tenant. They come from
    # a separate, much smaller-paged read.
    assert all("embedding" not in r for r in rows)

    vectors = mod._load_embeddings(ctx.company_id, {str(r["id"]) for r in rows})
    assert len(vectors) == len(rows)


def test_a_timed_out_embedding_page_does_not_cost_the_rest(ctx, monkeypatch):
    """Bailing out on the first failure threw away every page after it:
    measured against a real tenant, ONE slow page cost 577 of 2,777 vectors,
    and the run then reported those signals as ungroupable when the only thing
    wrong was one slow request."""
    import app.routes.crucible as mod

    for i in range(6):
        _signal(ctx.company_id, i, embedding=str([0.1 * (i + 1)] * 4))
    monkeypatch.setattr(mod, "_EMBED_PAGE", 2)

    real = mod.require_client if hasattr(mod, "require_client") else None
    calls = {"n": 0}
    from app.db import client as client_mod

    original = client_mod.require_client

    class Flaky:
        def __init__(self, inner):
            self._inner = inner

        def table(self, name):
            if name == "kg_signal":
                calls["n"] += 1
                if calls["n"] == 2:          # the SECOND page explodes
                    raise RuntimeError("canceling statement due to timeout")
            return self._inner.table(name)

    monkeypatch.setattr(client_mod, "require_client",
                        lambda: Flaky(original()))
    ids = {str(r["id"]) for r in mod._load_signals(ctx.company_id)}
    monkeypatch.setattr(client_mod, "require_client",
                        lambda: Flaky(original()))
    calls["n"] = 0
    vectors = mod._load_embeddings(ctx.company_id, ids)
    # Two lost to the failed page, the remaining four still fetched.
    assert 0 < len(vectors) < len(ids)


def test_a_tenant_with_no_embeddings_gets_no_taxonomy_findings(ctx):
    """The missing-API-key shape, end to end. Skipping the clustering call
    entirely left every cluster id unset, so the claims were pooled by kind and
    the run reported "40 claims concern finding" with no coverage note."""
    for i in range(8):
        _signal(ctx.company_id, i, kind="finding")          # no embedding

    run_id = _start(ctx).json()["id"]
    ctx.client.post(f"/v1/crucible/{run_id}/confirm",
                    json={"definition_text": "renewal revenue"})
    body = ctx.client.get(f"/v1/crucible/{run_id}").json()

    statements = " ".join(f["statement"] for f in body["findings"])
    assert "concern “finding”" not in statements, statements
    assert any("could not be grouped" in n["reason"]
               or "no usable embedding" in n["actual"]
               for n in body["coverage_notes"]) or body["findings"] == []


def test_the_ledger_does_not_get_one_row_per_ungroupable_signal(ctx):
    """A real tenant would write 2,777 identical rows, burying every genuine
    rejection underneath them."""
    for i in range(12):
        _signal(ctx.company_id, i)                          # no embedding

    run_id = _start(ctx).json()["id"]
    ctx.client.post(f"/v1/crucible/{run_id}/confirm",
                    json={"definition_text": "renewal revenue"})
    considered = ctx.client.get(f"/v1/crucible/{run_id}").json()["considered"]
    assert len(considered) <= 2, [c["reason"][:60] for c in considered]


# ─── Stage 1: the plan gate ──────────────────────────────────────────────────

def _confirm(ctx, run_id, text="renewal-cohort revenue net of churn"):
    return ctx.client.post(f"/v1/crucible/{run_id}/confirm",
                           json={"definition_text": text})


def test_confirming_a_goal_produces_a_plan_and_stops(ctx):
    """A run reads the whole corpus and takes minutes. Confirming what the goal
    MEANS is not the same decision as approving HOW it will be answered, and
    collapsing the two is how a user ends up having agreed to something they
    never saw."""
    for i in range(3):
        _signal(ctx.company_id, i)
    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)

    body = ctx.client.get(f"/v1/crucible/{run_id}").json()
    assert body["status"] == "awaiting_approval"
    assert body["findings"] == [], "nothing may be analysed before approval"
    plan = body["prioritisation"]["plan"]
    assert plan["goal_text"] and plan["definition_text"]


def test_the_plan_says_where_it_will_look_and_how_much_is_there(ctx):
    for i in range(5):
        _signal(ctx.company_id, i)
    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    plan = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["plan"]
    assert plan["total_signals"] == 5
    assert plan["sources"], "a plan with no inventory tells the user nothing"
    assert all(s["witnesses"] for s in plan["sources"])


def test_the_plan_names_what_it_CANNOT_answer_and_how_to_fix_it(ctx):
    """THE POINT OF THE STEP. These facts used to surface as coverage notes at
    the bottom of finished output — after the wait, phrased as an apology.
    Beforehand they are a decision, and Sprntly can ingest every one of these,
    so a gap must carry its remedy rather than being a shrug."""
    for i in range(3):
        _signal(ctx.company_id, i)          # customer_voice only: no numbers
    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    plan = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["plan"]

    gaps = plan["cannot_answer"]
    assert gaps, "a prose-only corpus cannot state a point estimate"
    assert any("points" in g["question"] or "move" in g["question"] for g in gaps)
    for g in gaps:
        assert g["remedy"], f"gap with no way to close it: {g['question']}"


def test_approving_the_plan_is_what_starts_the_analysis(ctx):
    for i in range(4):
        _signal(ctx.company_id, i)
    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    assert ctx.client.post(f"/v1/crucible/{run_id}/approve", json={}).status_code == 200
    assert ctx.client.get(f"/v1/crucible/{run_id}").json()["status"] in ("ready", "failed")


def test_a_double_approval_cannot_start_two_analyses(ctx):
    """Same race as double-confirm, same fix: the expected status is in the
    WHERE clause, so the second click loses the claim."""
    from app.db import crucible_runs as runs_db

    for i in range(3):
        _signal(ctx.company_id, i)
    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    assert runs_db.claim_for_approval(run_id, ctx.company_id) is not None
    assert runs_db.claim_for_approval(run_id, ctx.company_id) is None


def test_approving_a_run_that_has_no_plan_is_refused(ctx):
    run_id = _start(ctx).json()["id"]           # still awaiting_confirmation
    r = ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})
    assert r.status_code == 409


def test_the_user_can_drop_a_source_and_the_run_honours_it(ctx):
    for i in range(4):
        _signal(ctx.company_id, i)
    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(f"/v1/crucible/{run_id}/approve",
                    json={"excluded_sources": ["customer_voice"]})
    body = ctx.client.get(f"/v1/crucible/{run_id}").json()
    # Everything seeded is customer_voice, so excluding it leaves nothing.
    assert body["status"] == "failed" and body["error_code"] == "no_evidence"


def test_the_approved_plan_records_what_the_user_decided(ctx):
    """THE REPORT READS THIS. `build_plan` runs BEFORE the user sees it, so the
    stored plan still describes the run they were OFFERED. Left alone, the
    finished report lists a source the user dropped among the ones it read, and
    loses the hypotheses they typed entirely — a document that misstates its own
    inputs is worse than one that shows fewer of them."""
    from app.db.client import require_client

    for i in range(3):
        _signal(ctx.company_id, i)
    # A second source type, so excluding one still leaves the run something to
    # read and the assertion is about the RECORD, not about failing empty.
    require_client().table("kg_signal").insert({
        "id": "sig-9001", "enterprise_id": ctx.company_id, "kind": "finding",
        "source_type": "project_mgmt", "content": "tracker signal",
        "properties": {"customer": "Vandelay Industries"},
        "provenance": {"doc": "NW-2140"},
        "valid_at": "2026-03-01T00:00:00+00:00",
        "created_at": "2026-08-19T00:00:00+00:00",
        "transaction_at": "2026-08-19T00:00:00+00:00",
    }).execute()

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(
        f"/v1/crucible/{run_id}/approve",
        json={"excluded_sources": ["project_mgmt"],
              "hypotheses": ["pricing is the blocker"]},
    )

    plan = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["plan"]
    assert plan["excluded_sources"] == ["project_mgmt"]
    assert plan["hypotheses"] == ["pricing is the blocker"]
    kept = [s["source_type"] for s in plan["sources"]]
    assert "project_mgmt" not in kept, (
        "the report would list a dropped source among the ones it read"
    )
    assert plan["total_signals"] == sum(s["signal_count"] for s in plan["sources"])


def test_the_plan_does_not_promise_a_number_the_engine_cannot_produce(ctx):
    """The plan step exists to stop a user discovering a limit at the bottom of
    finished output. A plan that itself overpromises reintroduces the problem —
    and it nearly did: connecting an analytics source flipped the wording to
    "sizes stated in the goal's own unit" while the engine still had only a
    reach-based path."""
    for i in range(3):
        _signal(ctx.company_id, i)
    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    plan = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["plan"]

    promised = " ".join(plan["will_produce"]).lower()
    assert "reach" in promised
    assert "goal's own unit" not in promised or "cannot" in promised
    # And the limit is always stated as a gap, connected sources or not.
    assert any("points" in g["question"] for g in plan["cannot_answer"])


def test_the_plan_does_not_promise_to_adjudicate_hypotheses(ctx):
    """Second overpromise found in this step, and by a reviewer rather than by
    me. Nothing in the engine tests a user's stated hypothesis against the
    evidence; the plan said it would return "a verdict on each". A plan that
    overpromises reintroduces exactly the problem the plan step was added to
    remove — a user discovering a limit at the bottom of the output."""
    for i in range(3):
        _signal(ctx.company_id, i)
    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(f"/v1/crucible/{run_id}/approve",
                    json={"hypotheses": ["onboarding is too long"]})

    from app.crucible.plan import build_plan

    plan = build_plan(company_id=ctx.company_id, goal_text="g",
                      definition_text="d",
                      hypotheses=("onboarding is too long",)).to_json()
    promised = " ".join(plan["will_produce"]).lower()
    assert "verdict" not in promised
    assert "does not yet test them" in promised


# ─── The stranded-document sweep ─────────────────────────────────────────────

def _stranded_doc(company_id: str, *, version: int = 1, minutes_old: int = 120,
                  title: str = "Goal Analysis — improve revenue") -> int:
    """A `goal_analysis` artifact that no run points at — what a process death
    between `create_artifact` and `link_document` leaves behind."""
    from datetime import datetime, timedelta, timezone

    from app.crucible.report import ARTIFACT_KIND
    from app.db.client import require_client

    created = (datetime.now(timezone.utc)
               - timedelta(minutes=minutes_old)).isoformat()
    row = (
        require_client().table("custom_artifacts").insert({
            "company_id": company_id, "kind": ARTIFACT_KIND, "title": title,
            "body_html": "<p>a rendered report</p>", "status": "ready",
            "version": version, "created_at": created, "updated_at": created,
        }).execute()
    ).data[0]
    return int(row["id"])


def test_a_stranded_document_is_swept(ctx):
    """`link_document`'s compare-and-set handles the double-click race — the
    loser deletes its own document. It cannot handle a process death BETWEEN
    create and link, because the process that would clean up is gone."""
    from app.db import crucible_runs as runs_db

    artifact_id = _stranded_doc(ctx.company_id)
    result = runs_db.sweep_stranded_documents()

    assert result["deleted"] == 1
    from app.db.custom_artifacts import get_artifact

    assert get_artifact(ctx.company_id, artifact_id) is None


def test_a_stranded_document_someone_EDITED_is_never_deleted(ctx):
    """THE ONE THAT MATTERS. A stranded document is not invisible — it appears
    in the Artifacts library, so somebody can open and edit it. Destroying that
    is a far bigger failure than leaving a stray row in a list, so the sweep
    deletes only what is provably untouched (`version == 1`)."""
    from app.db import crucible_runs as runs_db
    from app.db.custom_artifacts import get_artifact

    edited = _stranded_doc(ctx.company_id, version=3, title="my notes")
    result = runs_db.sweep_stranded_documents()

    assert result["deleted"] == 0
    assert result["kept_edited"] == 1
    assert get_artifact(ctx.company_id, edited) is not None


def test_a_document_still_linked_to_its_run_is_left_alone(ctx):
    """Not stranded — it is doing its job."""
    from app.db import crucible_runs as runs_db
    from app.db.custom_artifacts import get_artifact

    run_id = _start(ctx).json()["id"]
    artifact_id = _stranded_doc(ctx.company_id)
    runs_db.link_document(run_id, ctx.company_id,
                          artifact_id=artifact_id, body_hash="abc")

    assert runs_db.sweep_stranded_documents()["deleted"] == 0
    assert get_artifact(ctx.company_id, artifact_id) is not None


def test_a_document_younger_than_the_grace_period_is_left_alone(ctx):
    """Its link may still be in flight. The window this covers is milliseconds
    wide, so a generous gate costs nothing and removes any chance of deleting a
    document mid-creation on a slow box."""
    from app.db import crucible_runs as runs_db
    from app.db.custom_artifacts import get_artifact

    fresh = _stranded_doc(ctx.company_id, minutes_old=1)
    assert runs_db.sweep_stranded_documents()["deleted"] == 0
    assert get_artifact(ctx.company_id, fresh) is not None


def test_an_ordinary_team_document_is_never_touched(ctx):
    """The sweep is scoped to `goal_analysis` artifacts. A team document is
    unlinked BY DESIGN — it belongs to a thread, not a run — so a sweep that
    keyed on "no run points at it" alone would delete the whole library."""
    from app.db import crucible_runs as runs_db
    from app.db.client import require_client
    from app.db.custom_artifacts import get_artifact

    doc = (
        require_client().table("custom_artifacts").insert({
            "company_id": ctx.company_id, "kind": "leadership update",
            "title": "Q3 update", "body_html": "<p>hi</p>",
            "status": "ready", "version": 1,
            "created_at": "2020-01-01T00:00:00+00:00",
        }).execute()
    ).data[0]

    assert runs_db.sweep_stranded_documents()["deleted"] == 0
    assert get_artifact(ctx.company_id, int(doc["id"])) is not None


def test_the_sweep_is_actually_wired_to_the_scheduler(ctx):
    """A sweep nobody calls heals nothing — the exact mistake `sweep_orphans`
    shipped with earlier in this feature."""
    import inspect

    import app.scheduler as scheduler

    assert "sweep_stranded_documents" in inspect.getsource(
        scheduler._run_orphan_ask_job_sweep)


# ─── The 413 path, which shipped untested ────────────────────────────────────

def _ready_run(ctx) -> int:
    """A run in `ready` — the only state the document routes act on."""
    from app.db import crucible_runs as runs_db

    run_id = _start(ctx).json()["id"]
    runs_db.update(run_id, ctx.company_id, status="ready")
    return run_id


def test_an_oversized_report_gets_a_413_not_a_dead_connection(ctx, monkeypatch):
    """WHAT THE BUG LOOKED LIKE. `custom_artifacts` refused a 421,696-char body,
    the route had no handler, the unhandled exception took the worker down
    mid-request, and the browser reported "Failed to fetch" — an outage, for
    what was really a refused write.

    The cap should mean this never fires. It is tested because "should not" is
    not "cannot", and an unhandled path is exactly what shipped last time."""
    import app.routes.crucible as mod

    run_id = _ready_run(ctx)
    monkeypatch.setattr(mod, "_render_document_html",
                        lambda *a, **k: "<p>" + ("x" * 500_000) + "</p>")
    r = ctx.client.post(f"/v1/crucible/{run_id}/document")

    assert r.status_code == 413
    # The copy has to say the run survived — otherwise the reader assumes the
    # analysis is gone, not just the document.
    assert "run itself is unaffected" in r.json()["detail"]


def test_the_fork_route_is_guarded_too_not_just_the_create_route(ctx, monkeypatch):
    """THE ONE THE REVIEW CAUGHT. The first fix guarded `_create_document` and
    left `_fork_document` eighty lines below calling `create_artifact` with the
    same unbounded body — same file, same exception, same dropped connection.
    Both writers now share one guard."""
    import app.routes.crucible as mod

    run_id = _ready_run(ctx)
    monkeypatch.setattr(mod, "_render_document_html",
                        lambda *a, **k: "<p>" + ("x" * 500_000) + "</p>")
    r = ctx.client.post(f"/v1/crucible/{run_id}/document/fork")

    # EXACTLY 413. Accepting 404/409 as well is how this test stops testing
    # anything: the fixture drifts, the run is not found or not claimable, the
    # route returns before it ever renders a body, and the assertion still
    # passes while the guard goes unexercised.
    assert r.status_code == 413, r.status_code
    assert "too large" in (r.json().get("detail") or "").lower()
    if r.status_code == 413:
        assert "run itself is unaffected" in r.json()["detail"]


def test_both_writers_go_through_the_same_guard(ctx):
    """Structural, so a THIRD writer cannot quietly skip it: the guard is one
    function and both call sites reach it."""
    import inspect

    import app.routes.crucible as mod

    assert "_body_or_413" in inspect.getsource(mod._fork_document)
    assert hasattr(mod, "_body_or_413")


def test_the_panel_and_the_api_agree_on_the_hypothesis_cap():
    """A cap copied by hand into two languages, made loud when it drifts.

    The panel refuses an over-long hypothesis so the user learns the rule where
    the offending line can be named, instead of hitting a 422 that leaves the
    run in `awaiting_approval` and reads as "we could not tell whether that
    started". That only works while the two numbers are the same one — and
    nothing else in the build would notice if they stopped being.
    """
    import re
    from pathlib import Path

    tsx = (Path(__file__).resolve().parents[2] / "web" / "app" / "components"
           / "shared" / "GoalAnalysisPlan.tsx")
    assert tsx.exists(), f"panel moved: {tsx}"
    m = re.search(r"MAX_HYPOTHESIS_CHARS\s*=\s*([\d_]+)", tsx.read_text())
    assert m, "MAX_HYPOTHESIS_CHARS is gone from the panel"
    panel_cap = int(m.group(1).replace("_", ""))

    api = Path(__file__).resolve().parents[1] / "app" / "routes" / "crucible.py"
    m2 = re.search(r"hypotheses: list\[Annotated\[str, StringConstraints\("
                   r"max_length=([\d_]+)\)\]\]", api.read_text())
    assert m2, "the API's hypothesis constraint moved; update this contract test"
    api_cap = int(m2.group(1).replace("_", ""))

    assert panel_cap == api_cap, (
        f"the panel refuses at {panel_cap} but the API refuses at {api_cap} — "
        f"whichever is larger produces the unrecoverable 422 this guards"
    )


# ─── The run narrates itself ─────────────────────────────────────────────────
#
# `running` used to be one state showing "Reading N claims…" for minutes. The
# funnel is now published as the run decides, and two things can go wrong in
# ways a reader would feel:
#
#   1. CLOBBERING THE PLAN. Progress rides in `prioritisation`, the same blob
#      that holds Stage 0's ask and the approved plan. A write that replaces
#      instead of merging erases the record the report has to reprint.
#   2. NARRATING A CHECK THAT DID NOT RUN. When the corpus is dated by ingest
#      the echo rule is skipped; publishing "0 dropped" would claim a check
#      passed that could not see.

def test_a_finished_run_publishes_the_funnel_it_actually_applied(ctx):
    for i in range(6):
        _signal(ctx.company_id, i, embedding=str([0.1 * (i + 1)] * 4))

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})

    meta = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]
    progress = meta.get("progress")
    assert progress, "a finished run published no funnel"

    assert progress["step"] == "done"
    assert progress["claims"] > 0
    assert progress["sources"] >= 1
    # Every rule present, even at zero — the panel distinguishes "dropped
    # nothing" from "did not run", and a missing key cannot carry that.
    from app.crucible.pipeline import NARRATED_DROPS

    assert set(progress["dropped"]) == set(NARRATED_DROPS)
    # The funnel has to ADD UP against what the run stored, or it is decoration.
    findings = ctx.client.get(f"/v1/crucible/{run_id}").json()["findings"]
    assert progress["findings"] == len(findings)


def test_publishing_progress_never_erases_the_approved_plan(ctx):
    """The blob is shared. `_progress` read-modify-writes for the same reason
    `_meta_of` exists — a replacing write would lose the plan mid-run, and the
    report would then be unable to say what it read."""
    for i in range(4):
        _signal(ctx.company_id, i, embedding=str([0.1 * (i + 1)] * 4))

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(
        f"/v1/crucible/{run_id}/approve",
        json={"hypotheses": ["onboarding is where they drop off"]},
    )

    meta = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]
    assert meta.get("progress"), "no progress written"
    assert meta.get("plan"), "the plan was erased by a progress write"
    assert meta["plan"]["hypotheses"] == ["onboarding is where they drop off"]


def test_a_skipped_echo_check_is_published_as_skipped_not_as_zero(ctx):
    """`valid_at == created_at` means the corpus is dated by when we read it,
    so the one-conversation rule cannot see and is skipped. Publishing a zero
    there would state that a check ran and found nothing."""
    from app.db.client import require_client

    for i in range(4):
        require_client().table("kg_signal").insert({
            "id": f"sig-ing-{i}", "enterprise_id": ctx.company_id,
            "kind": "finding", "source_type": "customer_voice",
            "content": f"signal {i}", "properties": {"customer": f"Acct{i}"},
            "provenance": {"doc": "doc-a"},
            # The ingest clock: identical to created_at, which is what the
            # detector keys on.
            "valid_at": "2026-08-19T00:00:00+00:00",
            "created_at": "2026-08-19T00:00:00+00:00",
            "transaction_at": "2026-08-19T00:00:00+00:00",
            "embedding": str([0.1 * (i + 1)] * 4),
        }).execute()

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})

    progress = (
        ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["progress"]
    )
    assert progress["echo_check_skipped"] is True
    assert progress["dropped"]["echo"] == 0, (
        "a skipped check must not also report drops"
    )


def test_a_progress_write_failure_never_fails_a_good_run(ctx, monkeypatch):
    """Narration is display. A run that produced real findings must not die
    because a progress write did.

    THE STORE IS BROKEN ONLY FOR PROGRESS, not for every meta write. Patching
    `_meta_of` wholesale also breaks the plan-record write — which is a
    different, unguarded path — and the test then passes for the wrong reason
    while proving nothing about this guard."""
    import app.routes.crucible as mod

    for i in range(4):
        _signal(ctx.company_id, i, embedding=str([0.1 * (i + 1)] * 4))

    real_update = mod.runs_db.update

    def update(run_id, company_id, **fields):
        meta = fields.get("prioritisation")
        if isinstance(meta, dict) and "progress" in meta:
            raise RuntimeError("progress store unavailable")
        return real_update(run_id, company_id, **fields)

    monkeypatch.setattr(mod.runs_db, "update", update)

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})

    row = ctx.client.get(f"/v1/crucible/{run_id}").json()
    assert row["status"] == "ready", f"run died on a display write: {row}"
    # And the guard is what saved it, not a progress write that never happened.
    assert not row["prioritisation"].get("progress")
    assert row["prioritisation"].get("plan"), "the plan write was collateral"


# ─── The funnel has to BALANCE, and on a MIXED tenant ────────────────────────
#
# Both review passes found the same defect and neither test in this file could
# have: `assign_clusters` returns its own `"clusters"` key counting only the
# graph-unthemed leftovers, and merging it clobbered `build_findings`' total,
# so the panel's headline became the leftover count. It is invisible in exactly
# two states — the graph themes everything, or it themes nothing — and every
# fixture here was the second one (`grep theme` in this file was zero hits).
#
# So this seeds a MIXED corpus, which is the production shape, and asserts the
# identity a PM would check on screen:
#
#     groups == findings + group-level drops + ungroupable
#
# One line, and it fails on the old code.

def _theme(company_id: str, entity_id: str, label: str, signal_ids: list[str]) -> None:
    """Join signals to a graph theme, the way the extractor does."""
    from app.db.client import require_client

    c = require_client()
    stamp = "2026-08-19T00:00:00+00:00"
    c.table("kg_entity").insert({
        "id": entity_id, "enterprise_id": company_id, "type": "theme",
        "canonical_label": label,
        "valid_at": stamp, "transaction_at": stamp,
    }).execute()
    for sid in signal_ids:
        # `id` is autoincrement here, so it is left to the table.
        c.table("kg_relationship").insert({
            "enterprise_id": company_id,
            "source_id": sid, "source_kind": "signal",
            "target_id": entity_id, "target_kind": "entity", "type": "about",
            "valid_at": stamp, "transaction_at": stamp,
        }).execute()


def test_the_published_funnel_balances_on_a_mixed_tenant(ctx):
    """THE ONE BOTH REVIEWERS ASKED FOR. Some claims themed by the graph, some
    left to embeddings — the shape where the headline used to be the leftover
    count instead of the total."""
    from app.crucible.pipeline import NARRATED_DROPS

    # Six signals the graph themes into two themes, plus four it does not.
    for i in range(10):
        _signal(ctx.company_id, i, embedding=str([0.1 * (i + 1)] * 4))
    _theme(ctx.company_id, "ent-a", "Exports", ["sig-0000", "sig-0001", "sig-0002"])
    _theme(ctx.company_id, "ent-b", "Billing", ["sig-0003", "sig-0004", "sig-0005"])

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})

    row = ctx.client.get(f"/v1/crucible/{run_id}").json()
    progress = row["prioritisation"]["progress"]

    # The corpus really was mixed, or this test proves nothing.
    assert progress["claims_themed"] > 0, "no claim was themed by the graph"
    assert progress["claims_unthemed"] > 0, "nothing was left for embeddings"

    group_drops = sum(
        progress["dropped"][c] for c in NARRATED_DROPS if c != "ungroupable"
    )
    # `_cluster` keys each ungroupable claim as its own cluster, so it counts
    # once here despite being a claim count elsewhere.
    assert progress["groups"] == (
        progress["findings"] + group_drops + progress["dropped"]["ungroupable"]
    ), f"funnel does not balance: {progress}"

    # And the headline is the TOTAL, never the leftovers-only count.
    assert progress["groups"] >= progress["findings"]


def test_the_claim_split_sums_to_claims_not_to_groups(ctx):
    """`claims_themed`/`claims_unthemed` are CLAIM counts. Publishing them as
    the parts of a THEME count invited an arithmetic that can never hold."""
    for i in range(8):
        _signal(ctx.company_id, i, embedding=str([0.1 * (i + 1)] * 4))
    _theme(ctx.company_id, "ent-a", "Exports", ["sig-0000", "sig-0001"])

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})

    p = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["progress"]
    assert p["claims_themed"] + p["claims_unthemed"] == p["claims"]


def test_signals_dropped_before_projection_are_attributed_per_reason(ctx):
    """`seen - claims` is retired PLUS undated. Publishing one number under the
    date rule made the panel contradict the run's own coverage note.

    THE FIXTURE HAS TO CONTAIN THE CONDITION. An earlier version of this test
    seeded four ordinary signals, so both counts were 0 and the identity
    reduced to `0 == 0` — it passed with the two fields SWAPPED and passed with
    both hardcoded to zero. Guards-that-are-not-guards #2. So: exactly one
    retired signal, exactly two undated, and the counts are pinned by value."""
    from app.db.client import require_client

    for i in range(4):
        _signal(ctx.company_id, i, embedding=str([0.1 * (i + 1)] * 4))

    c = require_client()
    # Retired: the repo's own definition (`superseded_by` / `expired_at`).
    c.table("kg_signal").insert({
        "id": "sig-retired", "enterprise_id": ctx.company_id, "kind": "finding",
        "source_type": "customer_voice", "content": "a superseded bet",
        "properties": {"customer": "Initech", "superseded_by": "sig-0000"},
        "provenance": {"doc": "doc-a"},
        "valid_at": "2026-03-01T00:00:00+00:00",
        "created_at": "2026-08-19T00:00:00+00:00",
        "transaction_at": "2026-08-19T00:00:00+00:00",
    }).execute()
    # Undated: no usable timestamp, so `project_signal` returns None.
    for j in range(2):
        c.table("kg_signal").insert({
            "id": f"sig-undated-{j}", "enterprise_id": ctx.company_id,
            "kind": "finding", "source_type": "customer_voice",
            "content": "no date", "properties": {"customer": "Globex"},
            "provenance": {"doc": "doc-b"},
            # `project_signal` drops on an UNPARSEABLE `valid_at`, which is
            # what "no usable timestamp" means to it. The fake's DDL is NOT
            # NULL (production's column is looser), so an empty string is the
            # faithful stand-in — `_parse_ts` returns None for both.
            "valid_at": "",
            "created_at": "2026-08-19T00:00:00+00:00",
            "transaction_at": "2026-08-19T00:00:00+00:00",
        }).execute()

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})

    p = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["progress"]
    # PINNED BY VALUE, so swapping the two fields fails. The identity alone
    # cannot tell them apart.
    assert p["retired"] == 1, p
    assert p["undated"] == 2, p
    assert p["signals_read"] - p["claims"] == p["retired"] + p["undated"]


def test_the_headline_is_themes_not_the_balancing_total(ctx):
    """`_cluster` keys every ungroupable claim as its own cluster, so `groups`
    counts one pseudo-group per unembeddable claim. Rendering THAT as a theme
    count put "Grouped into N themes" directly above "N claims never grouped at
    all" — the same screen asserting both."""
    from app.db.client import require_client

    # No embeddings at all, so every claim is ungroupable.
    for i in range(5):
        require_client().table("kg_signal").insert({
            "id": f"sig-noemb-{i}", "enterprise_id": ctx.company_id,
            "kind": "finding", "source_type": "customer_voice",
            "content": f"signal {i}", "properties": {"customer": f"Acct{i}"},
            "provenance": {"doc": "doc-a"},
            "valid_at": f"2026-0{1 + i}-11T00:00:00+00:00",
            "created_at": "2026-08-19T00:00:00+00:00",
            "transaction_at": "2026-08-19T00:00:00+00:00",
        }).execute()

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})

    p = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["progress"]
    ungroupable = p["dropped"]["ungroupable"]
    assert ungroupable > 0, f"fixture produced no ungroupable claims: {p}"
    # The whole point: a corpus that grouped into NOTHING must not report
    # itself as having produced themes.
    assert p["themes"] == 0, p
    assert p["groups"] == p["themes"] + ungroupable, p


def test_the_balance_identity_is_exercised_with_real_drops(ctx):
    """THE PRODUCTION SHAPE: real themes AND ungroupable claims AND a real
    group-level drop, all non-zero at once.

    The two earlier tests for this arithmetic sat at the degenerate ends — one
    all-ungroupable, one with none — and between them `themes` was green under
    `max(0, groups - 2 * ungroupable)`, which on a real corpus publishes 414
    where the truth is 622. The only shape that pins the coefficient is the one
    where both terms are non-zero, and it existed in no test. Third round of
    "the fixture does not contain the condition" on this PR."""
    from app.crucible.pipeline import NARRATED_DROPS
    # Themed and embeddable -> real findings.
    for i in range(6):
        _signal(ctx.company_id, i, embedding=str([0.1 * (i + 1)] * 4))
    _theme(ctx.company_id, "ent-a", "Exports", ["sig-0000", "sig-0001", "sig-0002"])
    # A lone claim on its own theme -> an anecdote drop.
    _signal(ctx.company_id, 90, embedding=str([0.9] * 4))
    _theme(ctx.company_id, "ent-solo", "Billing", ["sig-0090"])
    # UNEMBEDDABLE and unthemed -> ungroupable, so the subtraction is real.
    # `_signal` already defaults `embedding=None`; hand-rolling the row here
    # would drift from the helper the next time a column is added.
    for j in (91, 92):
        _signal(ctx.company_id, j, doc="doc-z")

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})

    p = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["progress"]
    group_drops = sum(
        p["dropped"][c] for c in NARRATED_DROPS if c != "ungroupable"
    )
    # ALL THREE non-zero, or this test is back at a degenerate end and proves
    # nothing about the coefficient.
    assert group_drops > 0, f"no group-level drop exercised: {p}"
    assert p["dropped"]["ungroupable"] > 0, f"no ungroupable claim: {p}"
    assert p["themes"] > 0, f"no real theme survived: {p}"

    # The pin. `themes` must be the theme count, not the balancing total and
    # not any multiple of the correction.
    assert p["themes"] == p["findings"] + group_drops, p
    assert p["groups"] > p["themes"], (
        "groups must exceed themes when claims were ungroupable", p)


def test_every_engine_drop_code_has_panel_copy():
    """CROSS-BOUNDARY. `NARRATED_DROPS` is declared "for the client", but the
    client hardcodes its own parallel list — so a rule added to the engine
    renders as a raw code. The fallback makes drift visible; this makes it
    fail. Precedent: test_the_panel_and_the_api_agree_on_the_hypothesis_cap."""
    import pathlib
    import re

    from app.crucible.pipeline import NARRATED_DROPS

    path = (pathlib.Path(__file__).resolve().parents[2]
            / "web/app/components/shared/GoalRunNarration.tsx")
    # FAIL AS A CONTRACT BREAK, not as a stack trace. A moved file or a renamed
    # constant would otherwise surface as FileNotFoundError/ValueError from a
    # test named "every engine drop code has panel copy", which reads as a
    # broken test — and the cheapest way to green a broken test is to delete it.
    if not path.exists():
        pytest.fail(f"panel copy lives at {path}, which no longer exists — "
                    f"move this test with it rather than dropping the contract")
    src = path.read_text()
    # MATCHED AS DECLARATIONS, not sliced between anchors. Slicing coupled this
    # to the ORDER the constants appear in: hoisting the `n` helper above
    # DROP_ORDER emptied the slice and this failed with "missing from
    # DROP_ORDER" while DROP_ORDER was intact — a failure that lies about its
    # cause is one someone deletes instead of fixing.
    copy_m = re.search(r"const DROP_COPY[^=]*=\s*\{(.*?)\n\}", src, re.S)
    order_m = re.search(r"const DROP_ORDER[^=]*=\s*\[(.*?)\]", src, re.S)
    if not copy_m:
        pytest.fail(f"`const DROP_COPY` is gone from {path.name}; the drop-copy "
                    f"contract cannot be checked — update this test")
    if not order_m:
        pytest.fail(f"`const DROP_ORDER` is gone from {path.name}; the funnel "
                    f"order contract cannot be checked — update this test")
    copy_block, order_block = copy_m.group(1), order_m.group(1)
    for code in NARRATED_DROPS:
        # ANCHORED AS A KEY, not merely present. `\bcode\b` also matched the
        # word inside a comment, so commenting an entry OUT left this green
        # while the panel rendered the raw code.
        assert re.search(rf"^\s*{code}:", copy_block, re.M), (
            f"{code} has no panel copy KEY — it would render as a raw code"
        )
        assert re.search(rf'"{code}"', order_block), (
            f"{code} is missing from DROP_ORDER — it would render out of funnel order"
        )


def test_an_unknown_drop_code_cannot_fail_a_run():
    """`drops[code] += 1` on a plain dict raises KeyError, which escapes
    build_findings into execute_run's catch-all and fails the run for every
    tenant. Narration must never outrank the answer."""
    from datetime import datetime, timezone

    from app.crucible import pipeline as mod

    real = mod._refute

    def refute_with_a_new_code(*a, **kw):
        return mod.Refutation("a_rule_nobody_declared", "invented for this test")

    mod._refute = refute_with_a_new_code
    try:
        from tests.test_crucible_pipeline import claim

        out = mod.build_findings(
            [claim("x1", subject="exports", accounts=("Northwind",)),
             claim("x2", subject="exports", accounts=("Initech",), days_ago=40)],
            currency="accounts", now=datetime(2026, 8, 19, tzinfo=timezone.utc),
        )
    finally:
        mod._refute = real

    assert out.stats["dropped"]["a_rule_nobody_declared"] == 1
    # And the declared set is still all present, at zero.
    for code in mod.NARRATED_DROPS:
        assert code in out.stats["dropped"]


def test_the_route_derives_themes_from_GROUPS_not_from_CLAIMS(ctx, monkeypatch):
    """THE LINE THE LAST TWO COMMITS EXIST TO PRODUCE, pinned at the route.

    `themes = groups - ungroupable_GROUPS`. Reverting that to the ungroupable
    CLAIM count left all 91 crucible tests green, because no route fixture can
    reach the shape where they differ — `_cluster` keys each ungroupable claim
    by its own id, so one cluster holds one claim and the two counts coincide.

    So the pin is structural rather than data-driven: hand the route a
    PipelineResult whose two ungroupable counts genuinely disagree, and assert
    the published `themes` used the group one. Same patch-and-restore idiom
    this file already uses for `_refute`."""
    import app.routes.crucible as mod

    for i in range(4):
        _signal(ctx.company_id, i, embedding=str([0.1 * (i + 1)] * 4))

    real = mod.build_findings

    def build_with_disagreeing_counts(*a, **kw):
        result = real(*a, **kw)
        # 10 clusters, of which 2 are ungroupable GROUPS holding 5 claims
        # between them. themes must be 10 - 2 = 8, never 10 - 5 = 5.
        result.stats["clusters"] = 10
        result.stats["ungroupable_groups"] = 2
        result.stats["dropped"] = {**result.stats["dropped"], "ungroupable": 5}
        return result

    monkeypatch.setattr(mod, "build_findings", build_with_disagreeing_counts)

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})

    p = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["progress"]
    assert p["groups"] == 10, p
    assert p["themes"] == 8, (
        "themes must be groups minus ungroupable GROUPS (2), not minus "
        "ungroupable CLAIMS (5)", p)
    # And the payload carries the number the identity actually needs, so an
    # auditor is not left computing it from the claim count.
    assert p["ungroupable_groups"] == 2, p
    assert p["groups"] == p["themes"] + p["ungroupable_groups"], p


# ─── GOAL-RESOLUTION §5: the ask arrives after effort ────────────────────────
#
# Candidates come from `metric_points`, the real registry — NOT from
# `kg_signal.properties.metric`, which the first version read and which is the
# DS anomaly log plus unfiltered LLM extraction. Fixtures are synthetic per
# CONVENTIONS' public-repo hygiene.

def _point(company_id: str, metric: str, period: str, value: float,
           source: str = "amplitude") -> None:
    from app.db.client import require_client

    require_client().table("metric_points").insert({
        "enterprise_id": company_id, "metric": metric,
        "period_start": period, "value": value, "source": source,
    }).execute()


def test_the_ask_carries_the_search_and_candidates_with_live_numbers(ctx):
    """§5's four requirements. The shipped ask met NONE of them: it opened with
    what it could not find, asked an open question, named no consequence, and
    handed over an empty box."""
    for i, p in enumerate(["2026-02-02", "2026-03-02", "2026-04-06", "2026-05-04"]):
        _point(ctx.company_id, "weekly_signups_count", p, 4000 + i * 32)

    run_id = _start(ctx).json()["id"]
    meta = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]

    # 1. the search — the DEFINITION ladder's rungs, not the corpus inventory.
    rungs = meta.get("searched") or []
    assert rungs, "the ask does not say where it looked"
    labels = [r["rung"] for r in rungs]
    assert "your KPI tree" in labels and "your measured metrics" in labels

    # 2. candidates carrying a live value
    cands = meta.get("candidates") or []
    assert cands, "the ask offers nothing to point at"
    top = cands[0]
    assert top["key"] == "weekly_signups_count"
    assert top["current_value"] == 4000 + 3 * 32
    assert top["current_period"] == "2026-05-04"
    assert top["points"] == 4
    # 3. the consequence, and not the old broken prose
    assert top["consequence"]
    assert "recorded, never counted" not in top["consequence"]
    # §6: the calculation, stated in the same step.
    assert meta.get("method_note"), "identity without method is F4"


def test_the_ask_still_works_when_the_registry_is_empty(ctx):
    """§5 requirement 4 is unconditional, and the registry is empty on every
    real tenant today — so this is the NORMAL path, not the edge case."""
    for i in range(3):
        _signal(ctx.company_id, i)

    run_id = _start(ctx).json()["id"]
    row = ctx.client.get(f"/v1/crucible/{run_id}").json()
    assert row["status"] == "awaiting_confirmation"
    assert (row["prioritisation"].get("candidates") or []) == []
    # The rungs still report — an empty rung is the evidence of looking.
    assert row["prioritisation"].get("searched")
    assert row["prioritisation"].get("method_note")
    # And confirming by hand still locks.
    ctx.client.post(f"/v1/crucible/{run_id}/confirm",
                    json={"definition_text": "my own sentence"})
    assert ctx.client.get(f"/v1/crucible/{run_id}").json()["status"] != "awaiting_confirmation"


def test_a_clean_adoption_is_not_handed_a_pick_list(ctx, monkeypatch):
    """§7: when Step 2 found a definition the confirmation "is not a question,
    it is a statement with an escape hatch, and it should take two seconds to
    clear". A six-item pick-list beside an adopted verbatim definition invites
    the user to replace it with our paraphrase — which §10 forbids, and the
    panel's pick used to be a REPLACE with no undo.

    DRIVEN THROUGH THE REAL RESOLVER, not a stub. An earlier version of this
    test monkeypatched `resolve` to return a fake object, which `_remember`
    then stored in the module-level `_pending_definitions` dict — keyed by
    `run_id`, which restarts at 1 whenever the test DB resets. A LATER test
    picked the fake up and died in `confirm()` on `definition.id`. The fake
    was the bug; the real ladder is also the better test.
    """
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

    # A registry with something in it, so an empty list cannot pass vacuously.
    for i, p in enumerate(["2026-02-02", "2026-03-02"]):
        _point(ctx.company_id, "weekly_signups_count", p, 10 + i)

    run_id = _start(ctx, goal="improve net revenue retention").json()["id"]
    meta = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]

    assert "expansion minus churn" in meta["proposed_definition"], (
        "the fixture did not actually reach an adoption")
    assert (meta.get("candidates") or []) == [], (
        "an adopted definition must not be offered a replacement list")


def test_a_registry_metric_is_still_offered_when_nothing_was_adopted(ctx):
    """The other half of the test above: with no KPI-tree match the SAME
    registry does produce candidates, so the assertion there is about the
    adoption and not about an empty registry."""
    for i, p in enumerate(["2026-02-02", "2026-03-02"]):
        _point(ctx.company_id, "weekly_signups_count", p, 10 + i)

    run_id = _start(ctx, goal="improve net revenue retention").json()["id"]
    meta = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]
    assert not meta.get("proposed_definition")
    assert [c["key"] for c in (meta.get("candidates") or [])] == [
        "weekly_signups_count"]


def test_a_failed_registry_read_never_blocks_the_ask(ctx, monkeypatch):
    import app.crucible.metric_candidates as mod

    monkeypatch.setattr(mod, "candidates_for_goal",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))

    run_id = _start(ctx).json()["id"]
    row = ctx.client.get(f"/v1/crucible/{run_id}").json()
    assert row["status"] == "awaiting_confirmation", "the ask died with the scan"
    assert not (row["prioritisation"].get("candidates") or [])


def test_the_candidates_are_scoped_to_this_company(ctx):
    """Tenancy, at the ask. Another company's metric names are a disclosure."""
    for p in ("2026-01-05", "2026-01-12"):
        _point("some-other-company", "their_secret_metric", p, 1)

    run_id = _start(ctx).json()["id"]
    meta = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]
    keys = {c["key"] for c in (meta.get("candidates") or [])}
    assert "their_secret_metric" not in keys


# ─── The report lands in the Artifacts panel by itself ───────────────────────

def test_a_finished_run_files_its_report_as_an_editable_document(ctx):
    """The report was only ever a document if somebody pressed a button — so
    the one artifact a PM circulates was the one thing the feature did not
    produce."""
    for i in range(6):
        _signal(ctx.company_id, i, embedding=str([0.1 * (i + 1)] * 4))

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})

    row = ctx.client.get(f"/v1/crucible/{run_id}").json()
    assert row["status"] == "ready"
    assert row["artifact_id"], "the finished report is not in the library"

    doc = ctx.client.get(f"/v1/crucible/{run_id}/document")
    assert doc.status_code == 200, doc.text
    body = doc.json()
    assert body.get("body_html"), "the filed document has no body"
    assert not body.get("detached"), "a freshly filed report reads as edited"


def test_the_autosave_does_not_fight_a_manual_save(ctx):
    """`link_document` claims with `artifact_id IS NULL` in the WHERE clause, so
    only one of the two can win and the loser deletes its orphan. Without that
    a double-save leaves a stray report in the shared library."""
    from app.db.custom_artifacts import list_artifacts_for_company

    for i in range(4):
        _signal(ctx.company_id, i, embedding=str([0.1 * (i + 1)] * 4))
    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})

    # The autosave already filed one; ask for another by hand.
    again = ctx.client.post(f"/v1/crucible/{run_id}/document")
    assert again.status_code in (200, 409), again.text

    from app.crucible.report import ARTIFACT_KIND

    reports = [a for a in (list_artifacts_for_company(ctx.company_id) or [])
               if a.get("kind") == ARTIFACT_KIND]
    assert len(reports) == 1, f"duplicate reports in the library: {len(reports)}"


def test_an_autosave_failure_never_fails_a_finished_run(ctx, monkeypatch):
    import app.routes.crucible as mod

    def boom(*a, **kw):
        raise RuntimeError("artifact store unavailable")

    monkeypatch.setattr(mod, "_render_document_html", boom)

    for i in range(4):
        _signal(ctx.company_id, i, embedding=str([0.1 * (i + 1)] * 4))
    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})

    row = ctx.client.get(f"/v1/crucible/{run_id}").json()
    assert row["status"] == "ready", f"a convenience write failed the run: {row}"
    assert not row.get("artifact_id")


def test_the_method_note_does_not_promise_what_the_engine_does_not_do(ctx):
    """§6's sentence has to be TRUE of the run.

    The first version said "I will use your own recorded numbers for whichever
    metric you name, exactly as they are stored". It is false — `execute_run`
    reads `kg_signal` and nothing in the pipeline reads `metric_points`, so no
    registry number enters the sizing. A method note that misstates the method
    is the overpromise `plan.py` has been burned by twice, one gate earlier."""
    for i, p in enumerate(["2026-02-02", "2026-03-02"]):
        _point(ctx.company_id, "weekly_signups_count", p, 10 + i)

    run_id = _start(ctx).json()["id"]
    note = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["method_note"]

    assert note
    # It must not claim the run consumes the registry's numbers.
    assert "recorded numbers" not in note
    assert "exactly as they are stored" not in note
    # It must say what the run DOES read, and what it reports instead.
    assert "reads your documents" in note
    assert "how much of your book" in note


def test_the_method_note_is_the_same_whether_or_not_a_registry_exists(ctx):
    """There is only one honest sentence, because nothing in the pipeline reads
    the registry either way. Branching on its presence implied the run behaved
    differently when it does not."""
    empty = ctx.client.get(
        f"/v1/crucible/{_start(ctx).json()['id']}"
    ).json()["prioritisation"]["method_note"]

    for i, p in enumerate(["2026-02-02", "2026-03-02"]):
        _point(ctx.company_id, "weekly_signups_count", p, 10 + i)
    filled = ctx.client.get(
        f"/v1/crucible/{_start(ctx).json()['id']}"
    ).json()["prioritisation"]["method_note"]

    assert empty == filled


def test_the_panel_and_the_api_agree_on_the_definition_cap():
    """CROSS-BOUNDARY, and the precedent is two tests above this one. The panel
    refuses an append past its own constant; if that constant drifts from
    `ConfirmGoal.definition_text`'s `max_length`, the panel starts posting text
    the API rejects with a 422 it cannot recover from."""
    import pathlib
    import re

    panel = (pathlib.Path(__file__).resolve().parents[2]
             / "web/app/components/shared/GoalMetricCandidates.tsx")
    if not panel.exists():
        pytest.fail(f"{panel} is gone — move this contract test with it")
    m = re.search(r"MAX_DEFINITION_CHARS\s*=\s*([\d_]+)", panel.read_text())
    if not m:
        pytest.fail("MAX_DEFINITION_CHARS is gone from the panel — update this test")

    from app.routes.crucible import ConfirmGoal

    # The field carries MinLen and MaxLen as separate metadata entries, in no
    # guaranteed order — pick the one that actually declares a maximum.
    caps = [getattr(c, "max_length", None)
            for c in ConfirmGoal.model_fields["definition_text"].metadata]
    api_cap = next((c for c in caps if c is not None), None)
    assert api_cap is not None, "ConfirmGoal.definition_text declares no maximum"
    assert int(m.group(1).replace("_", "")) == api_cap, (
        f"the panel's cap ({m.group(1)}) and the API's max_length ({api_cap}) "
        f"have drifted")


def test_a_definition_conflict_is_not_offered_a_way_around_itself(ctx, monkeypatch):
    """`goal.py`: "two authoritative systems disagreeing about what a metric
    means is worth more than either answer", and "picking one silently is the
    failure". A pick-list of unrelated registry metrics beside the conflict ask
    is a one-click route past it — the two conflicting sides are never
    referenced again — so a conflict gets the ask alone.

    `definition is None` is true on THREE statuses, which is how the conflict
    path acquired a pick-list by accident."""
    from app.crucible.types import DefinitionConflict

    class _Conflicted:
        status = "conflict"
        ask = "Two systems define this differently. Which one governs?"
        definition = None
        conflicts = [DefinitionConflict(
            metric_name="tasks_open",
            source_a="clickup", definition_a="open tasks in ClickUp",
            source_b="jira", definition_b="unresolved issues in Jira",
        )]

    import app.routes.crucible as mod

    monkeypatch.setattr(mod, "resolve", lambda **kw: _Conflicted())
    # A registry with something in it, so an empty list cannot pass vacuously.
    for i, p in enumerate(["2026-02-02", "2026-03-02"]):
        _point(ctx.company_id, "weekly_signups_count", p, 10 + i)

    run_id = _start(ctx).json()["id"]
    meta = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]

    assert meta["conflicts"], "the fixture did not reach a conflict"
    assert (meta.get("candidates") or []) == [], (
        "a conflict must not be offered a way around itself")
    # The conflict question itself still renders.
    assert meta["ask"]

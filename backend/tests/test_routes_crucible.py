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


from dataclasses import replace  # noqa: E402
from app.crucible.types import DefinitionConflict  # noqa: E402


def _start(ctx, goal="raise net revenue retention", **kw):
    return ctx.client.post("/v1/crucible", json={"goal_text": goal, **kw})


#: A goal naming NO metric family, so `_convention_definition` returns nothing
#: and — with no KPI tree either — there is no honest proposal to fold into the
#: plan. This is the case that still stops at its own gate to ask, and it is
#: what every legacy `/confirm` test below now uses to reach that gate.
NO_METRIC = "make the roadmap easier to explain"


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

def test_a_new_run_stops_before_it_analyses_anything(ctx):
    """THE INVARIANT, WHICH DID NOT MOVE: an unadopted goal must not spend a
    run. What moved is where it stops. Stage 0 used to halt at a clarification
    gate of its own; it now resolves a proposal and halts at the plan, where
    that proposal is shown and the reader can change it. Either way nothing is
    read and nothing is locked until a person says yes."""
    body = _start(ctx).json()
    assert body["status"] == "awaiting_approval"
    assert body["claim_count"] == 0
    # Nothing locked. `crucible_goal_definitions` is the table that authorises
    # spending, and a proposal nobody has agreed to must not be in it.
    assert _table("crucible_goal_definitions") == []


def test_a_goal_with_nothing_to_propose_still_stops_to_ask(ctx):
    """The half of Stage 0 that did NOT fold. With no KPI tree and no metric
    family we hold a convention for, there is no proposal to put in front of
    anyone — and writing one anyway is the inference I9 forbids. So this case
    keeps its own gate."""
    body = _start(ctx, goal=NO_METRIC).json()
    assert body["status"] == "awaiting_confirmation"
    assert body["claim_count"] == 0


def test_a_recognised_metric_is_proposed_in_the_plan_rather_than_asked_cold(ctx):
    """THE FOLD. A goal naming a metric family arrives at the plan with a
    definition already written, attributed, and editable — instead of a bare
    question one screen earlier. The question asked cold is what produced a run
    whose recorded definition was the literal words "that is accurate"."""
    body = _start(ctx, goal="reduce churn this quarter").json()
    assert body["status"] == "awaiting_approval"
    plan = _prioritisation(body["id"])["plan"]
    assert "LOGO churn" in plan["definition_text"]
    # WHERE IT CAME FROM, said on the plan. A proposal that cannot be
    # attributed is indistinguishable from an assertion.
    assert plan["definition_source"]
    # AND THAT NOBODY HAS AGREED TO IT YET.
    assert plan["definition_adopted"] is False
    assert _table("crucible_goal_definitions") == []


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
    # It waits at the PLAN now rather than at a gate of its own — but it still
    # waits, which is the whole of the claim. The tree's own wording is
    # prefilled and nothing has been adopted.
    assert body["status"] == "awaiting_approval"

    detail = ctx.client.get(f"/v1/crucible/{body['id']}").json()
    plan = _prioritisation(body["id"])["plan"]
    assert "expansion minus churn" in plan["definition_text"]
    assert plan["definition_adopted"] is False
    assert _table("crucible_goal_definitions") == []
    assert detail["findings"] == []


def test_confirming_locks_the_definition_with_who_and_when(ctx):
    """I9 at the storage layer: `locked` is the state that authorises spending,
    so it must carry the user who authorised it.

    THE LOCK MOVED TO APPROVE. It used to fire the moment `/confirm` returned,
    which was right while that was the only human act in the flow. Now that the
    plan carries a PROPOSED definition, locking on the way to the plan would
    stamp a user id onto words nobody had agreed to yet. So both doors —
    `/confirm` for a goal with nothing to propose, and the folded path for one
    with a proposal — lock at the same place: the click that says go. Still
    before anything is spent, which is all the invariant ever asked."""
    run_id = _start(ctx, goal=NO_METRIC).json()["id"]
    ctx.client.post(
        f"/v1/crucible/{run_id}/confirm",
        json={"definition_text": "renewal-cohort revenue, net of churn"},
    )
    # Not yet: the plan is on screen and nobody has said go.
    assert _table("crucible_goal_definitions") == []
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})
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
    run_id = _start(ctx, goal=NO_METRIC).json()["id"]
    words = "revenue, as finance books it, excluding one-offs"
    ctx.client.post(f"/v1/crucible/{run_id}/confirm", json={"definition_text": words})
    ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})
    assert _table("crucible_goal_definitions")[0]["definition_text"] == words


def test_confirming_a_run_that_is_not_waiting_is_refused(ctx):
    """The confirm path is the only door from awaiting_confirmation onward.
    Re-posting it must not start a second analysis on the same row."""
    run_id = _start(ctx, goal=NO_METRIC).json()["id"]
    ctx.client.post(f"/v1/crucible/{run_id}/confirm", json={"definition_text": "x"})
    again = ctx.client.post(
        f"/v1/crucible/{run_id}/confirm", json={"definition_text": "y"}
    )
    assert again.status_code == 409
    # And the second definition never reached the plan, which is the thing the
    # 409 is protecting: one row, one set of words, whatever the client does.
    plan = _prioritisation(run_id)["plan"]
    assert plan["definition_text"] == "x"


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

    run_id = _start(ctx, goal=NO_METRIC).json()["id"]
    first = runs_db.claim_for_confirmation(run_id, ctx.company_id)
    second = runs_db.claim_for_confirmation(run_id, ctx.company_id)
    assert first is not None
    assert second is None


def test_the_client_can_see_what_stage_0_is_asking_for(ctx):
    """A run that reports `awaiting_confirmation` without saying what it is
    waiting FOR leaves the panel a blank box.

    REWRITTEN FOR THE CASE THAT STILL REACHES THAT GATE. This used to seed a
    KPI tree and check the prefilled proposal came back; a tree match now folds
    straight into the plan, so that scenario cannot arrive here any more — the
    proposal is checked at the plan instead. What still stops here is the goal
    with NOTHING to propose, and it is the one that most needs the question to
    be legible: there is no prefill to lean on, so the ask and the assumed
    method are all the reader has."""
    run_id = _start(ctx, goal=NO_METRIC).json()["id"]

    body = ctx.client.get(f"/v1/crucible/{run_id}").json()
    assert body["status"] == "awaiting_confirmation"
    meta = body["prioritisation"]
    # The question itself, and what will be done with the answer.
    assert meta["ask"]
    assert meta["method_note"]
    # And no invented prefill: there was nothing to propose, which is the
    # entire reason this run is sitting here rather than at a plan.
    assert not meta["proposed_definition"]


def test_the_signal_read_is_ordered_because_it_is_paged(ctx):
    """Postgres may return an unordered query in any order, so `range()` without
    `order()` can repeat one row across pages and drop another — the corpus
    would differ run to run and reproducibility is the whole claim."""
    # ASSERTED ON BEHAVIOUR, NOT ON SOURCE TEXT. This used to
    # `inspect.getsource(_load_signals)` and grep for ".order(", which broke the
    # moment the paging moved into a helper — while the ordering itself was
    # untouched. A test that reads the source passes or fails on where the code
    # lives rather than on what it does; the same shape let a ContentPanel test
    # "cover" a component it never rendered.
    from app.routes.crucible import _signal_page

    ordered = []

    class _Probe:
        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def order(self, col, *_a, **_k):
            ordered.append(col)
            return self
        def range(self, *_a, **_k): return self
        def execute(self): return type("R", (), {"data": []})()

    class _Client:
        def table(self, _n): return _Probe()

    _signal_page(_Client(), "co", 0)
    assert ordered == ["id"], "a paged read must be ordered, and by the key"


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
    run_id = _start(ctx, goal=NO_METRIC).json()["id"]  # awaiting_confirmation
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


def test_excluding_a_numeric_source_flips_the_gap_the_run_states(ctx):
    """AND THE GAPS AND PROMISES, which are DERIVED from the kept set.

    Approval narrowed `sources` and `total_signals` in place and left
    `cannot_answer` / `will_produce` alone, so a reader who unticked analytics
    still got "your numeric sources are connected and will be read" in the same
    document that said analytics was excluded — and lost the gap that had just
    become TRUE, with the remedy that would close it, handed "no action needed
    from you" instead.

    Excludes a NUMERIC source deliberately: those are the only ones the
    derivation branches on, so excluding anything else cannot show the defect —
    the first version of this assertion excluded `project_mgmt` and could never
    have failed."""
    from app.db.client import require_client

    for i in range(3):
        _signal(ctx.company_id, i)
    require_client().table("kg_signal").insert({
        "id": "sig-9100", "enterprise_id": ctx.company_id, "kind": "finding",
        "source_type": "analytics", "content": "activation rate moved",
        "properties": {"customer": "Globex"},
        "provenance": {"doc": "NW-3001"},
        "valid_at": "2026-03-01T00:00:00+00:00",
        "created_at": "2026-08-19T00:00:00+00:00",
        "transaction_at": "2026-08-19T00:00:00+00:00",
    }).execute()

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)

    offered = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["plan"]
    assert any("connected and will be read" in p
               for p in (offered.get("will_produce") or [])), (
        "fixture is wrong: analytics must be present BEFORE the exclusion"
    )

    ctx.client.post(f"/v1/crucible/{run_id}/approve",
                    json={"excluded_sources": ["analytics"], "hypotheses": []})

    plan = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["plan"]
    assert not any("connected and will be read" in p
                   for p in (plan.get("will_produce") or [])), (
        "the run still promised to read the source the reader dropped"
    )
    becauses = " ".join((g.get("because") or "")
                        for g in (plan.get("cannot_answer") or []))
    assert "nothing connected here carries numbers" in becauses, (
        "the reader lost the gap that became true when they dropped analytics"
    )


# ─── AC-2: the framework is chosen from the inventory, not hardcoded ────────


def test_the_plan_chooses_moscow_when_nothing_carries_a_number(ctx):
    """The corpus this ticket exists for. RICE cannot derive Reach or Impact
    from three customer_voice signals — measured on a real 1,275-signal
    corpus, this is exactly the shape that scores 26/26 findings `None`."""
    for i in range(3):
        _signal(ctx.company_id, i)
    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    plan = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["plan"]
    assert plan["framework"] == "moscow"
    assert plan["framework_reason"]
    assert "account_value" not in [q["id"] for q in plan.get("questions") or []]


def test_the_plan_chooses_rice_when_a_numeric_source_is_connected(ctx):
    from app.db.client import require_client

    for i in range(3):
        _signal(ctx.company_id, i)
    require_client().table("kg_signal").insert({
        "id": "sig-9200", "enterprise_id": ctx.company_id, "kind": "finding",
        "source_type": "analytics", "content": "activation rate moved",
        "properties": {}, "provenance": {"doc": "NW-3002"},
        "valid_at": "2026-03-01T00:00:00+00:00",
        "created_at": "2026-08-19T00:00:00+00:00",
        "transaction_at": "2026-08-19T00:00:00+00:00",
    }).execute()

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    plan = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["plan"]
    assert plan["framework"] == "rice"
    assert "account_value" in [q["id"] for q in plan.get("questions") or []]


def test_dropping_the_only_numeric_source_at_approval_downgrades_the_framework(ctx):
    """Framework selection is RE-DERIVED from the KEPT inventory at approve
    time, same fix as the gaps re-derivation above and for the same reason: a
    reader who unticks the one source that made RICE derivable must not keep
    a RICE table where every row scores `None` — this ticket's whole reason
    for existing, one step later than the bug the gaps fix already covers."""
    from app.db.client import require_client

    for i in range(3):
        _signal(ctx.company_id, i)
    require_client().table("kg_signal").insert({
        "id": "sig-9201", "enterprise_id": ctx.company_id, "kind": "finding",
        "source_type": "analytics", "content": "activation rate moved",
        "properties": {}, "provenance": {"doc": "NW-3003"},
        "valid_at": "2026-03-01T00:00:00+00:00",
        "created_at": "2026-08-19T00:00:00+00:00",
        "transaction_at": "2026-08-19T00:00:00+00:00",
    }).execute()

    run_id = _start(ctx).json()["id"]
    _confirm(ctx, run_id)
    offered = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["plan"]
    assert offered["framework"] == "rice", "fixture is wrong: RICE must be chosen BEFORE the exclusion"

    ctx.client.post(f"/v1/crucible/{run_id}/approve",
                    json={"excluded_sources": ["analytics"], "hypotheses": []})

    plan = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["plan"]
    assert plan["framework"] == "moscow", (
        "the run kept ranking by RICE after the reader dropped the only "
        "source that made it derivable"
    )
    assert "account_value" not in [q["id"] for q in plan.get("questions") or []]


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



# ─── §6: the calculation, stated in the same step ───────────────────────────

def test_the_method_note_does_not_promise_what_the_engine_does_not_do(ctx):
    """§6's sentence has to be TRUE of the run.

    A first version said "I will use your own recorded numbers for whichever
    metric you name, exactly as they are stored". That is false — `execute_run`
    reads `kg_signal`, and nothing in the pipeline reads `metric_points`, so no
    registry number enters the sizing. A method note that misstates the method
    is the overpromise `plan.py` has been burned by twice, one gate earlier.

    The note is a CONSTANT for every company and goal, deliberately: the
    mechanism it describes does not vary, so branching on anything would imply
    the run behaves differently when it does not."""
    run_id = _start(ctx, goal=NO_METRIC).json()["id"]
    note = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["method_note"]

    assert note
    # It must not claim the run consumes stored metric numbers.
    assert "recorded numbers" not in note
    assert "exactly as they are stored" not in note
    # It must say what the run DOES read, and what it reports instead.
    assert "reads your documents" in note
    assert "how much of your book" in note


def test_nothing_in_the_run_reads_the_metric_registry():
    """THE CANARY FOR `_method_note`.

    The note tells the user "the analysis reads your documents, tickets and
    conversations against it, not a metric series". That is true only while
    nothing in the run reads `metric_points` — and `metric_candidates`' own
    docstring says the registry "lights up the moment it is populated", so
    somebody wiring it into sizing is expected. The existing tests assert
    substrings of the note and would keep passing on a stale string.

    This fails instead, and points at the note."""
    import pathlib
    import re

    # THE WHOLE PIPELINE, not one function's body. Greping
    # `inspect.getsource(execute_run)` was trivially defeated by ordinary
    # refactoring: a `_size_with_registry(...)` helper defined elsewhere and
    # called from `execute_run` puts none of these names in `execute_run`'s own
    # source, and this file already factors work out that way — so the guard
    # would have been silently defeated by whoever does that work next rather
    # than by anyone trying to dodge it.
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    watched = [root / "crucible" / f for f in
               ("pipeline.py", "claims.py", "scoring.py", "cluster.py",
                "kg_themes.py", "report.py")]
    watched.append(root / "routes" / "crucible.py")

    offenders = []
    for path in watched:
        if not path.exists():
            continue
        text = path.read_text()
        if path.name == "crucible.py" and path.parent.name == "routes":
            # `_method_note` and the ask's candidate scan legitimately read the
            # registry; the ANALYSIS must not. Strip the two known-good readers
            # before looking.
            text = re.sub(r"def (_method_note|_autosave_document)\b.*?(?=\ndef )",
                          "", text, flags=re.S)
            text = text.replace("from app.crucible.metric_candidates import", "")
        for forbidden in ("metric_points", "list_metric_points",
                          "distinct_metrics"):
            if forbidden in text:
                offenders.append(f"{path.name}: {forbidden}")

    assert not offenders, (
        f"the analysis now touches the metric registry ({offenders}). "
        f"`_method_note` promises the run reads your documents, tickets and "
        f"conversations rather than a metric series — rewrite that sentence "
        f"before wiring the registry into sizing."
    )



def test_the_ask_and_the_method_note_do_not_repeat_each_other(ctx):
    """They render back to back on the same screen.

    Both originally ended with "what is counted, over what population, over
    what window" — the identical clause in consecutive paragraphs, which is the
    exact redundancy the ask rewrite exists to remove, reintroduced one element
    lower. The ask asks for the parts; the note says what will be done with
    them."""
    run_id = _start(ctx, goal=NO_METRIC).json()["id"]
    meta = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]
    ask, note = meta["ask"], meta["method_note"]

    # The ask owns the instruction.
    assert "what is counted" in ask.lower()
    # The note must not restate it.
    assert "what is counted" not in note.lower()

    # And no long phrase should appear in both. Cheap n-gram overlap check, so
    # this catches the next accidental echo rather than only this one.
    def grams(text: str, n: int = 6) -> set[str]:
        words = [w.strip(".,:;—\"'") for w in text.lower().split()]
        return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}

    shared = grams(ask) & grams(note)
    assert not shared, f"ask and method note share phrasing: {sorted(shared)[:3]}"


# ─── §6: the convention, stated per metric and correctable ───────────────────

def test_the_method_note_states_the_convention_for_the_metric_named(ctx):
    """§6: "If no computation is found, state the common convention you are
    assuming for that metric, in one sentence, and let them change it."

    An earlier version answered a different question — what the ANALYSIS reads
    — while citing §6 and F4 ("two teams both say revenue and mean recognised
    versus booked") as its justification. True sentence, wrong question, §6's
    citation on it."""
    run_id = _start(ctx, goal="reduce customer churn").json()["id"]
    plan = _prioritisation(run_id)["plan"]

    # STATED AS THE DEFINITION, not as a note about one. That is where the
    # convention moved when the clarification gate folded into the plan: it is
    # the sentence in the editable field, which is a stronger reading of §6
    # than a paragraph underneath a question — you can change it in place.
    stated = plan["definition_text"].lower()
    # It names the fork it is choosing, because the fork is what resizes every
    # recommendation.
    assert "logo churn" in stated
    assert "revenue churn" in stated
    # And it is offered for correction, not asserted: nobody has adopted it.
    assert plan["definition_adopted"] is False
    # The note underneath must NOT restate it — repeating the convention below
    # the field holding it is the redundancy this change was asked to cut.
    assert "logo churn" not in plan["definition_note"].lower()


def test_the_convention_follows_the_goal_not_the_company(ctx):
    """Keyed on the GOAL's words. A per-company convention would be the
    cross-customer contamination README F11 bars."""
    churn = _prioritisation(
        _start(ctx, goal="reduce churn").json()["id"]
    )["plan"]["definition_text"]
    revenue = _prioritisation(
        _start(ctx, goal="grow revenue").json()["id"]
    )["plan"]["definition_text"]

    assert "logo churn" in churn.lower()
    assert "recognised" in revenue.lower() and "booked" in revenue.lower()
    assert churn != revenue, "the convention must follow the metric named"


def test_an_unrecognised_goal_gets_no_invented_convention(ctx):
    """§10 forbids inferring a definition. Where the goal names no metric family
    there is no convention to state, and making one up would be exactly that."""
    run_id = _start(ctx, goal="make the widget frobnicate better").json()["id"]
    note = ctx.client.get(f"/v1/crucible/{run_id}").json()["prioritisation"]["method_note"]

    assert "I will read" not in note, f"invented a convention: {note}"
    # It still says what the run does, which is what it always was.
    assert "reads your documents" in note


def test_the_convention_never_reaches_a_definition_on_its_own(ctx):
    """It is an assumption offered for correction. Nothing here may end up in
    `crucible_goal_definitions` unless the user leaves it in their own text."""
    run_id = _start(ctx, goal="reduce customer churn").json()["id"]
    ctx.client.post(f"/v1/crucible/{run_id}/confirm",
                    json={"definition_text": "accounts that cancel in a quarter"})

    from app.db.client import require_client

    rows = (require_client().table("crucible_goal_definitions")
            .select("definition_text").execute()).data or []
    for r in rows:
        assert "I will read" not in (r.get("definition_text") or ""), (
            "the convention leaked into a stored definition")

# ── A slow page must not kill the run ────────────────────────────────────────

class _FakePage:
    """Stands in for the PostgREST builder chain, counting what was asked for."""
    def __init__(self, outer, size, offset):
        self.outer, self.size, self.offset = outer, size, offset
    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def range(self, lo, hi):
        self.offset, self.size = lo, hi - lo + 1
        return self
    def execute(self):
        return self.outer._serve(self.offset, self.size)


class _FakeClient:
    def __init__(self, total, fail_sizes=()):
        self.total, self.fail_sizes = total, set(fail_sizes)
        self.asked = []
    def table(self, _name): return _FakePage(self, 0, 0)
    def _serve(self, offset, size):
        self.asked.append((offset, size))
        if size in self.fail_sizes:
            raise RuntimeError("canceling statement due to statement timeout")
        rows = [{"id": f"s{i:05d}"} for i in range(offset, min(offset + size, self.total))]
        return type("R", (), {"data": rows})()


def test_a_timed_out_page_is_retried_in_smaller_slices():
    """MEASURED, NOT GUESSED: on a 3,364-signal staging tenant three runs went
    ready, failed, failed — all Postgres 57014, "canceling statement due to
    statement timeout". One slow page killed the whole run, and the reader got
    "Something went wrong on our side partway through this run" for a corpus
    that was merely large."""
    from app.routes.crucible import _PAGE, _PAGE_RETRY, _signal_page
    client = _FakeClient(total=10_000, fail_sizes={_PAGE})
    rows = _signal_page(client, "co", 0)
    assert len(rows) == _PAGE, "the page came back short after the retry"
    assert {sz for _, sz in client.asked} == {_PAGE, _PAGE_RETRY}


def test_the_retry_returns_exactly_the_page_it_was_asked_for():
    """No gaps, no overlaps: the slices must reconstruct the page byte for
    byte, or the run reads a different corpus than it thinks it did."""
    from app.routes.crucible import _PAGE, _signal_page
    direct = _signal_page(_FakeClient(total=10_000), "co", 2)
    retried = _signal_page(_FakeClient(total=10_000, fail_sizes={_PAGE}), "co", 2)
    assert [r["id"] for r in direct] == [r["id"] for r in retried]


def test_a_page_that_fails_even_when_small_raises_rather_than_shrinking():
    """IT MUST NOT SILENTLY SHRINK THE CORPUS. Swallowing this the way the
    embedding loader does would hand back a partial book with nothing saying
    so — the one thing every coverage note exists to prevent. A run that cannot
    read its evidence has to fail loudly, not quietly read less."""
    import pytest as _pytest
    from app.routes.crucible import _PAGE, _PAGE_RETRY, _signal_page
    client = _FakeClient(total=10_000, fail_sizes={_PAGE, _PAGE_RETRY})
    with _pytest.raises(Exception):
        _signal_page(client, "co", 0)


def test_the_last_page_still_ends_the_outer_loop_after_a_retry():
    """A short page is how `_load_signals` knows to stop. If the retry path
    padded or repeated, the loader would spin or duplicate rows."""
    from app.routes.crucible import _PAGE, _signal_page
    client = _FakeClient(total=_PAGE + 30, fail_sizes={_PAGE})
    rows = _signal_page(client, "co", 1)
    assert len(rows) == 30
    assert len(rows) < _PAGE


# ─── The definition is adopted at the plan, and only there ──────────────────


def test_approving_locks_the_definition_the_reader_was_shown(ctx):
    """A body carrying no definition means "yes, as written". The server must
    then lock the exact proposal that was on screen — locking an empty string,
    or re-deriving one, would settle the run on words nobody saw."""
    body = _start(ctx, goal="reduce churn this quarter").json()
    shown = _prioritisation(body["id"])["plan"]["definition_text"]
    ctx.client.post(f"/v1/crucible/{body['id']}/approve", json={})
    rows = _table("crucible_goal_definitions")
    assert len(rows) == 1
    assert rows[0]["definition_text"] == shown
    assert rows[0]["status"] == "locked"
    assert rows[0]["confirmed_by_user_id"] == ctx.user_id
    assert rows[0]["confirmed_by_user_at"]


def test_an_edited_definition_is_locked_verbatim_over_the_proposal(ctx):
    """The proposal is a starting point, not a decision. Editing it in the plan
    and approving must lock the reader's words — a paraphrase, or a silent
    fallback to the proposal, is a different metric asserted by us."""
    body = _start(ctx, goal="reduce churn this quarter").json()
    mine = "churn means seats lost, not accounts, counted at renewal"
    ctx.client.post(
        f"/v1/crucible/{body['id']}/approve", json={"definition_text": mine}
    )
    rows = _table("crucible_goal_definitions")
    assert len(rows) == 1
    assert rows[0]["definition_text"] == mine
    assert "LOGO churn" not in rows[0]["definition_text"]


def test_a_blank_edit_falls_back_to_the_proposal_rather_than_locking_nothing(ctx):
    """A cleared textarea posts "" or whitespace. Treating that as a definition
    strands the run — `confirm_goal` refuses to lock nothing, on a row already
    claimed for approval. It means "no change"."""
    body = _start(ctx, goal="reduce churn this quarter").json()
    shown = _prioritisation(body["id"])["plan"]["definition_text"]
    r = ctx.client.post(
        f"/v1/crucible/{body['id']}/approve", json={"definition_text": "   "}
    )
    assert r.status_code == 200
    assert _table("crucible_goal_definitions")[0]["definition_text"] == shown


def test_two_systems_disagreeing_is_a_decision_and_keeps_its_own_screen(ctx, monkeypatch):
    """The other half that did NOT fold. When two authoritative systems define
    the same metric differently, the disagreement is worth more than either
    answer and picking one silently is the failure. That is a decision, not a
    confirmation, so it does not get folded into a plan the reader can approve
    without noticing."""
    import app.routes.crucible as mod

    real = mod.resolve

    def conflicted(**kw):
        out = real(**kw)
        return replace(out, conflicts=(
            DefinitionConflict(
                metric_name="churn", source_a="finance", definition_a="revenue churn",
                source_b="the tracker", definition_b="logo churn",
            ),
        ))

    monkeypatch.setattr(mod, "resolve", conflicted)
    body = _start(ctx, goal="reduce churn this quarter").json()
    assert body["status"] == "awaiting_confirmation"
    assert _prioritisation(body["id"])["conflicts"]


def test_a_folded_run_never_advertises_the_gate_it_skips(ctx, monkeypatch):
    """A RACE, not a tidiness point. The first version of the fold wrote
    `awaiting_confirmation` and only then built the plan — so for the second or
    so the inventory query takes, the row advertised a gate this run was never
    going to stop at. The chat polls for either gate, so a poll landing in that
    window rendered the definition card for a run that had already moved on:
    exactly the screen the fold exists to remove, now appearing at random.

    Caught by watching every status the row is ever written with."""
    import app.db.crucible_runs as db

    seen: list[str] = []
    real = db.update

    def watched(run_id, company_id, **kw):
        if kw.get("status"):
            seen.append(kw["status"])
        return real(run_id, company_id, **kw)

    monkeypatch.setattr(db, "update", watched)
    monkeypatch.setattr("app.routes.crucible.runs_db.update", watched)

    body = _start(ctx, goal="reduce churn this quarter").json()
    assert body["status"] == "awaiting_approval"
    assert "awaiting_confirmation" not in seen


def test_an_edited_definition_reaches_the_report_not_just_the_lock(ctx):
    """THE REPORT SAID "IN YOUR OWN WORDS" OVER WORDS THAT WERE NOT THEIRS.

    Approve folded the dropped sources and the hypotheses into the stored plan
    and left `definition_text` at whatever was PROPOSED. The definition ROW was
    correct — `test_an_edited_definition_is_locked_verbatim_over_the_proposal`
    passed throughout — but the document renders `plan.definition_text`, so a
    reader who corrected the proposal read the sentence they had just rejected,
    attributed to themselves.

    A test that checks the write and not the read is how this survived."""
    body = _start(ctx, goal="reduce churn this quarter").json()
    proposed = _prioritisation(body["id"])["plan"]["definition_text"]
    mine = "churn means seats lost, not accounts, counted at renewal"
    assert mine != proposed

    ctx.client.post(f"/v1/crucible/{body['id']}/approve",
                    json={"definition_text": mine})

    plan = _prioritisation(body["id"])["plan"]
    assert plan["definition_text"] == mine
    assert proposed not in plan["definition_text"]
    # And it is adopted: a definition the reader typed is not still a proposal.
    assert plan["definition_adopted"] is True


def test_editing_only_the_definition_still_updates_the_plan(ctx):
    """THE GATE THAT SKIPPED IT. The fold was keyed on
    `excluded_sources or hypotheses`, so a reader who changed ONLY the
    definition — dropping no source, typing no hypothesis — skipped the block
    entirely. That is exactly the shape that produced the bug."""
    body = _start(ctx, goal="reduce churn this quarter").json()
    mine = "churn means seats lost at renewal"
    ctx.client.post(f"/v1/crucible/{body['id']}/approve",
                    json={"definition_text": mine})
    assert _prioritisation(body["id"])["plan"]["definition_text"] == mine


def test_approving_unchanged_leaves_the_proposal_exactly_as_shown(ctx):
    """A body carrying no definition means "yes, as written", and rewriting the
    plan then would change the record of what was offered."""
    body = _start(ctx, goal="reduce churn this quarter").json()
    shown = _prioritisation(body["id"])["plan"]["definition_text"]
    ctx.client.post(f"/v1/crucible/{body['id']}/approve", json={})
    assert _prioritisation(body["id"])["plan"]["definition_text"] == shown


def test_the_report_is_published_before_any_model_is_asked_anything():
    """THE BUG THAT ACTUALLY BIT, and it is about ORDER rather than errors.

    The relevance gate and the recommendations ran ABOVE `save_findings`, so
    everything the deterministic pipeline computed in seconds sat unsaved and
    invisible behind four sequential model calls. On staging a 149-finding run
    hung thirteen minutes past its last narration line showing nothing, with no
    error to show — because there was no error. Both layers caught exceptions
    and neither had any notion of time.

    Asserted on the SOURCE ORDER, because that is the property: findings saved
    and the run marked ready before either enrichment is reached.
    """
    import ast
    import inspect

    from app.routes import crucible as routes

    tree = ast.parse(inspect.cleandoc(inspect.getsource(routes.execute_run)))
    marks: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        src = ast.unparse(node)
        if "save_findings" in src:
            marks.append(("save", node.lineno))
        elif 'status="ready"' in src or "status='ready'" in src:
            marks.append(("ready", node.lineno))
        elif "judge_relevance" in src:
            marks.append(("gate", node.lineno))
        elif "build_recommendations" in src:
            marks.append(("recommend", node.lineno))

    order = [name for name, _ in sorted(marks, key=lambda m: m[1])]
    for required in ("save", "ready", "gate", "recommend"):
        assert required in order, f"{required} not found in execute_run"

    assert order.index("save") < order.index("gate"), (
        "the findings must be saved before the relevance gate is asked "
        f"anything — order was {order}"
    )
    assert order.index("ready") < order.index("gate"), (
        "the run must be READY before the gate runs; the panel polls on it, so "
        f"enriching first keeps the reader on a spinner — order was {order}"
    )
    assert order.index("ready") < order.index("recommend"), (
        f"the run must be READY before recommendations — order was {order}"
    )


def test_the_run_announces_that_enrichment_is_still_coming():
    """THE HANDSHAKE, AND THE REGRESSION IT CLOSES.

    Publishing the report before the gate and the recommendations is what stops
    the reader waiting on four model calls. But `ready` is TERMINAL for the
    panel's poller, so that same fix stopped the client listening before the
    results landed — they were written to a row nobody read again. The analysis
    appeared and the suggestions never did, which is exactly what Apurva saw.

    Asserted on source order: the flag must go UP before `ready`, so there is no
    window where a panel can see a ready run without knowing more is coming, and
    DOWN in the write that publishes the results rather than in one of its own.
    """
    import ast
    import inspect

    from app.routes import crucible as routes

    src = inspect.cleandoc(inspect.getsource(routes.execute_run))
    tree = ast.parse(src)

    up = down = ready = results = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            t = ast.unparse(node)
            flat = t.replace('"', "'")
            if "enrichment_pending'] = True" in flat:
                up = node.lineno
            elif "enrichment_pending'] = False" in flat:
                down = node.lineno
            elif "set_aside_by_rank'] =" in flat:
                results = node.lineno
        if isinstance(node, ast.Call):
            # `ast.unparse` normalises quotes, so match on the normalised form
            # rather than on how the source happens to be written.
            if "status='ready'" in ast.unparse(node).replace('"', "'"):
                ready = node.lineno

    assert up and down and ready and results, (up, down, ready, results)
    assert up < ready, "the flag must be raised before the run is marked ready"
    assert down > ready, "the flag comes down after the enrichment, not before"
    # Cleared in the same write that publishes the verdicts: clearing it
    # separately leaves a window where the panel stopped and the results are
    # not there yet.
    assert abs(down - results) <= 3, (
        f"the flag is cleared {abs(down - results)} lines from the results; "
        "it must be the same write"
    )

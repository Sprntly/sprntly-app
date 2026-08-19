"""Monthly scheduled intelligence reports (app.monthly_reports).

Covers the three layers separately, mirroring the brief's test split:

  - the PURE due decision (`due_specs`): monthly cadence forced over the
    company's brief schedule, the 24h window, and BOTH once-per-cycle
    ledgers — the durable saved-report row and the in-memory attempt guard;
  - the RUN (`run_and_deliver`): a real payload becomes a `reports` row with
    the canonical question marker and is ingested into the KG; a degraded
    payload saves nothing, ingests nothing, and is not retried this cycle;
  - the SCHEDULER SHELL (`_run_monthly_reports_tick`): per-company gating on
    the notification-settings toggle and per-company error isolation.

The engines themselves (competitive_intel & co) are never invoked — specs
carry injected runners, exactly the seam the module exposes for this.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app import monthly_reports as mr

UTC = timezone.utc

# 2026-06-01 is the first Monday of June 2026 — with the default schedule
# (Monday 06:00, UTC fallback timezone) the monthly fire instant is 06:00 UTC
# that day.
FIRE = datetime(2026, 6, 1, 6, 0, tzinfo=UTC)

COMPANY = {"id": "co-mr-1", "slug": "acme", "owner_timezone": None,
           "notification_settings": {}}

DEFAULT_SCHEDULE = {"weekday": 0, "hour": 6, "minute": 0,
                    "frequency": "weekly", "anchor": None}


@pytest.fixture(autouse=True)
def _db(isolated_settings):
    """Every path here reads the reports table (the durable ledger), so the
    whole file runs against the per-test fake Supabase."""
    yield


@pytest.fixture(autouse=True)
def _reset_attempt_ledger():
    """Isolate the in-memory once-per-cycle attempt guard between tests."""
    mr._last_attempt.clear()
    yield
    mr._last_attempt.clear()


def _spec(runner, skill="competitive-intelligence-review",
          question="Scheduled monthly competitive intelligence scan"):
    return mr.ReportSpec(skill=skill, question=question,
                         label="Competitive Intelligence report",
                         runner=runner)


def _payload(answer="## Competitive review\n\nAcme shipped X.",
             skill="competitive-intelligence-review") -> dict:
    """An Ask-shaped success payload, as the engines return it — `_report`
    included, which is what marks it a finished document rather than one of
    the same engine's apologies."""
    return {"answer": answer, "key_points": [], "citations": [],
            "confidence": 0.6, "_skill": skill, "_report": True}


def _due_skills(now, tz=None, schedule=None) -> list[str]:
    """The skills due right now. Asserted per-skill rather than as "the list
    is empty": the roster holds several INDEPENDENT specs, so suppressing one
    (a saved row, a spent attempt) must leave the others due — an emptiness
    check would pass today only because the roster happened to be short."""
    return [s.skill for s in mr.due_specs(
        COMPANY["id"], now, tz or ZoneInfo("UTC"),
        schedule or DEFAULT_SCHEDULE,
    )]


# ─── due_specs: the pure monthly decision ────────────────────────────────────


def test_due_inside_window_with_no_prior_run():
    due = mr.due_specs(COMPANY["id"], FIRE + timedelta(hours=2),
                       ZoneInfo("UTC"), DEFAULT_SCHEDULE)
    assert [s.skill for s in due] == [s.skill for s in mr.MONTHLY_REPORT_SPECS]


def _save_report_at(spec, when: datetime, *, question=None):
    """Seed a scheduled report row with an EXPLICIT created_at.

    `db.save_report` stamps the wall clock, which would make every cadence
    assertion below depend on the real date. Writing the row directly is what
    lets these tests pin "this company is up to date as of <instant>".
    """
    from app.db.client import require_client

    require_client().table("reports").insert({
        "company_id": COMPANY["id"],
        "skill": spec.skill,
        "title": "t",
        "html": "## body",
        "question": question or spec.question,
        "created_at": when.isoformat(),
    }).execute()


def test_a_company_with_no_report_at_all_is_due_immediately():
    """A tenant that has never had this report does not wait for a fire
    instant — it is inside the previous cycle, which nothing satisfied.

    This is what makes onboarding and backfill work: a company added
    mid-month gets its reports on the next tick instead of waiting weeks for
    a calendar boundary it happens to have missed.
    """
    assert [s.skill for s in mr.due_specs(
        COMPANY["id"], FIRE - timedelta(hours=1),
        ZoneInfo("UTC"), DEFAULT_SCHEDULE,
    )] == [s.skill for s in mr.MONTHLY_REPORT_SPECS]


def test_an_up_to_date_company_waits_for_the_next_fire_instant():
    """Timing still governs a tenant in good standing: with this cycle's
    report saved, the next run waits for the next fire instant rather than
    happening the moment the tick notices a new month is near."""
    spec = mr.MONTHLY_REPORT_SPECS[0]
    _save_report_at(spec, FIRE + timedelta(hours=1))

    # Deep inside the satisfied cycle, and right up to the next fire.
    assert spec.skill not in _due_skills(FIRE + timedelta(days=20))
    next_fire = datetime(2026, 7, 6, 6, 0, tzinfo=UTC)  # first Monday of July
    assert spec.skill not in _due_skills(next_fire - timedelta(hours=1))
    # ...and due again once the new cycle opens.
    assert spec.skill in _due_skills(next_fire + timedelta(hours=1))


def test_still_due_days_after_the_fire_instant():
    """There is NO window cliff — the whole cycle counts as due.

    This is the property the 24h window got wrong. Tenants cluster on two fire
    slots, the tick walks them sequentially, and a single report costs tens of
    minutes; whoever sorted last would fall out of a one-day window and get
    nothing at all that month. Being due until the next fire instant is what
    lets a slow pass, an outage, or a restart catch up.
    """
    for days in (2, 9, 20, 27):
        assert [s.skill for s in mr.due_specs(
            COMPANY["id"], FIRE + timedelta(days=days),
            ZoneInfo("UTC"), DEFAULT_SCHEDULE,
        )] == [s.skill for s in mr.MONTHLY_REPORT_SPECS], f"day {days}"


def test_a_saved_report_still_ends_the_cycle_however_wide_the_window():
    """The wide window is only safe because the durable ledger closes the
    cycle — catching up must never become double-firing."""
    from app import db

    spec = mr.MONTHLY_REPORT_SPECS[0]
    db.save_report(COMPANY["id"], skill=spec.skill, title="t",
                   html="## body", question=spec.question)
    assert spec.skill not in _due_skills(FIRE + timedelta(days=21))


def test_monthly_cadence_is_forced_over_the_brief_frequency():
    """A company on a DAILY brief cadence still gets reports monthly.

    Stated against a satisfied cycle, because that is where the two cadences
    actually diverge: with this month's report saved, the second Monday is a
    brief day but not a report day. (With nothing saved the company is due on
    any day — that is the catch-up rule, not the daily cadence leaking in.)
    """
    schedule = dict(DEFAULT_SCHEDULE, frequency="daily_weekdays")
    spec = mr.MONTHLY_REPORT_SPECS[0]
    _save_report_at(spec, FIRE + timedelta(hours=1))

    second_monday = datetime(2026, 6, 8, 7, 0, tzinfo=UTC)
    assert spec.skill not in [s.skill for s in mr.due_specs(
        COMPANY["id"], second_monday, ZoneInfo("UTC"), schedule)]
    # ...and the next month's first Monday fires as usual.
    july_fire = datetime(2026, 7, 6, 7, 0, tzinfo=UTC)
    assert spec.skill in [s.skill for s in mr.due_specs(
        COMPANY["id"], july_fire, ZoneInfo("UTC"), schedule)]


def test_fire_instant_tracks_the_company_timezone():
    """Monday 06:00 in New York is 10:00 UTC (EDT), so 07:00 UTC is still the
    previous cycle there while a UTC company has already fired.

    Seeded with May's report so the tenant is up to date going in — otherwise
    the catch-up rule makes it due at any hour and the timezone has nothing to
    show.
    """
    ny = ZoneInfo("America/New_York")
    spec = mr.MONTHLY_REPORT_SPECS[0]
    _save_report_at(spec, datetime(2026, 5, 4, 10, 30, tzinfo=UTC))

    early = datetime(2026, 6, 1, 7, 0, tzinfo=UTC)
    assert spec.skill not in [s.skill for s in mr.due_specs(
        COMPANY["id"], early, ny, DEFAULT_SCHEDULE)]
    at_fire = datetime(2026, 6, 1, 10, 30, tzinfo=UTC)
    assert spec.skill in [s.skill for s in mr.due_specs(
        COMPANY["id"], at_fire, ny, DEFAULT_SCHEDULE)]


def test_saved_scheduled_report_suppresses_the_cycle():
    """The durable ledger: a reports row carrying the spec's canonical
    question marker, saved after this cycle's fire, means not due."""
    from app import db

    spec = mr.MONTHLY_REPORT_SPECS[0]
    db.save_report(COMPANY["id"], skill=spec.skill, title="t",
                   html="## body", question=spec.question)
    due = _due_skills(FIRE + timedelta(hours=2))
    assert spec.skill not in due
    # ...and only that spec: each report keeps its own ledger.
    assert [s.skill for s in mr.MONTHLY_REPORT_SPECS[1:]] == due


def test_a_human_chat_report_does_not_suppress_the_scheduled_cycle():
    """A row for the SAME skill whose question is the human's own words is
    not the scheduler's — the monthly run still happens."""
    from app import db

    spec = mr.MONTHLY_REPORT_SPECS[0]
    db.save_report(COMPANY["id"], skill=spec.skill, title="t",
                   html="## body", question="where do we stand vs Acme?")
    # Named explicitly, not `!= []`: with several specs on the roster an
    # emptiness check would pass on a sibling still being due, proving nothing
    # about the one whose human-authored row is under test.
    assert spec.skill in _due_skills(FIRE + timedelta(hours=2))


def test_a_spent_attempt_backs_off_rather_than_forfeiting_the_month():
    """The attempt ledger throttles cost; it does not end the cycle.

    Folding it into the durable ledger (the old shape) made one failed sweep
    as final as a delivered report — survivable under a 24h window, but with a
    month-wide window it would mean a single early hiccup costs the month,
    which is the exact failure the wide window exists to prevent.
    """
    spec = mr.MONTHLY_REPORT_SPECS[0]
    attempt = FIRE + timedelta(hours=1)
    mr._last_attempt[(COMPANY["id"], spec.skill)] = attempt

    # Inside the backoff: held off, so the paid sweep isn't re-bought hourly.
    due = _due_skills(attempt + timedelta(hours=3))
    assert spec.skill not in due
    # A spent attempt on one report never stands in for another's.
    assert [s.skill for s in mr.MONTHLY_REPORT_SPECS[1:]] == due

    # Past the backoff, still inside the cycle: it gets another go.
    assert spec.skill in _due_skills(attempt + mr.RETRY_BACKOFF
                                     + timedelta(hours=1))


def test_a_degraded_report_gets_several_tries_a_month_not_hundreds():
    """The backoff has to sit between two real costs: a tenant whose report
    legitimately cannot be built (no public footprint to find) must not re-buy
    a multi-minute sweep every hour, and a transient outage must not cost the
    month. Roughly weekly is that middle."""
    per_cycle = timedelta(days=31) / mr.RETRY_BACKOFF
    assert 3 <= per_cycle <= 6, f"{per_cycle:.1f} attempts per cycle"


# ─── run_and_deliver: generate → save → ingest ───────────────────────────────


def test_run_saves_the_report_and_ingests_it(monkeypatch):
    ingested: list[dict] = []
    monkeypatch.setattr(
        mr, "_ingest_into_kg",
        lambda cid, spec, **kw: ingested.append({"cid": cid, "skill": spec.skill, **kw}),
    )
    spec = _spec(lambda company: _payload())

    now = datetime(2026, 6, 1, 6, 30, tzinfo=UTC)
    report_id = mr.run_and_deliver(COMPANY, spec, now=now)
    assert report_id is not None

    from app import db

    row = db.get_report(report_id, COMPANY["id"])
    assert row["skill"] == spec.skill
    assert row["question"] == spec.question
    assert row["html"] == "## Competitive review\n\nAcme shipped X."
    assert row["title"] == "Competitive Intelligence report · June 2026"

    # The ingest gets the report's own text, its month, and the saved row's id
    # — the id is what lets an answer grounded on a finding point back at the
    # artifact it came from.
    assert ingested == [{
        "cid": COMPANY["id"],
        "skill": spec.skill,
        "text": "## Competitive review\n\nAcme shipped X.",
        "period": "June 2026",
        "report_id": report_id,
    }]
    # ...and the saved row is what makes this spec's next due-check a no-op.
    assert spec.skill not in _due_skills(now + timedelta(hours=2))


def test_degraded_payload_saves_and_ingests_nothing(monkeypatch):
    """An apology is not a monthly report. Nothing is saved, nothing
    ingested, and the attempt is still recorded so the sweep isn't re-bought
    this cycle.

    Ingesting one would be worse than filing it: the apology's sentences would
    enter the graph as findings and answer other people's questions.

    The payload here is the engines' REAL degraded shape: `_plain_payload`
    stamps `_skill` on its apologies exactly as the success return does, so
    this is precisely the case a `_skill`-only check would wave through —
    filing "finish onboarding and I'll…" as the month's report AND stamping
    the durable ledger, suppressing the real one until next month.
    """
    ingested: list = []
    monkeypatch.setattr(mr, "_ingest_into_kg",
                        lambda cid, spec, **kw: ingested.append(cid))
    spec = _spec(lambda company: {
        "answer": "I can review your competitors, but I don't have your "
                  "company profile yet — finish onboarding…",
        "key_points": [], "citations": [], "confidence": 0.0,
        "_skill": "competitive-intelligence-review",
        "_skill_action": "Competitive intelligence",
        "_skill_source": "competitive-intel",
    })

    now = FIRE + timedelta(minutes=30)
    assert mr.run_and_deliver(COMPANY, spec, now=now) is None
    assert ingested == []
    assert (COMPANY["id"], spec.skill) in mr._last_attempt
    assert spec.skill not in _due_skills(now + timedelta(hours=2))


def test_query_mode_followup_is_not_a_report(monkeypatch):
    """Query mode answers a follow-up off a STORED run and stamps `_skill`
    too. It is an answer about a past report, not this month's — saving it
    would file a one-line reply as the monthly artifact."""
    ingested: list = []
    monkeypatch.setattr(mr, "_ingest_into_kg",
                        lambda cid, spec, **kw: ingested.append(cid))
    spec = _spec(lambda company: {
        "answer": "Two competitors shipped pricing changes.",
        "key_points": [], "citations": [], "confidence": 0.5,
        "_skill": "competitive-intelligence-review",
        "_skill_action": "Competitive intelligence · from the 2026-05-04 review",
        "_skill_source": "competitive-intel-query",
    })

    assert mr.run_and_deliver(COMPANY, spec,
                              now=FIRE + timedelta(minutes=30)) is None
    assert ingested == []


def test_a_payload_from_the_wrong_engine_is_rejected(monkeypatch):
    """`_report` alone is not enough: a runner wired to the wrong engine
    returns a real document under another skill's name, and filing it under
    this spec would mislabel the artifact and stamp the wrong ledger."""
    monkeypatch.setattr(mr, "_ingest_into_kg", lambda cid, spec, **kw: None)
    spec = _spec(lambda company: _payload(skill="public-feedback-report"))
    assert mr.run_and_deliver(COMPANY, spec,
                              now=FIRE + timedelta(minutes=30)) is None


def test_ingest_failure_never_loses_the_saved_report(monkeypatch):
    """The extractor is a paid model call over the network. When it falls over
    the month's report must still be in the library to read — losing the
    artifact as well would turn a retrievability problem into a data loss."""
    def _boom(cid, spec, **kw):
        raise RuntimeError("extractor fell over")

    monkeypatch.setattr(mr, "_ingest_into_kg", _boom)
    spec = _spec(lambda company: _payload())
    report_id = mr.run_and_deliver(COMPANY, spec,
                                   now=FIRE + timedelta(minutes=30))
    assert report_id is not None  # the artifact survived the failed ingest


# ─── the ingest repair pass ──────────────────────────────────────────────────
#
# Saving the report and extracting it are two failures, and only the save is
# recorded in the durable ledger. Without a repair pass a raised ingest leaves
# the cycle marked done with nothing in the graph — a whole month of
# retrievability lost to a transient network error. This is the leg that
# noticed a real production failure: a stale Supabase connection killed an
# extraction mid-run after the report had already been saved.


def _write_ledger_row(spec, text, report_id=1):
    """Stand in for a SUCCESSFUL ingest by writing the row it would leave."""
    from app.graph.facade import GraphFacade
    from app.graph.types import Source
    from app.kg_ingest import monthly_report as mrk

    sha = mrk.content_sha(COMPANY["id"], spec.skill, text.strip())
    GraphFacade().create_source(COMPANY["id"], Source(
        id=mrk._ledger_id(COMPANY["id"], sha),
        enterprise_id=COMPANY["id"],
        source_type=mrk.LEDGER_SOURCE_TYPE,
        label=f"{spec.label} · June 2026",
        config={"content_sha": sha, "report_skill": spec.skill,
                "report_id": report_id},
    ))


def _save_a_report_whose_ingest_failed(monkeypatch):
    """Drive a real run whose ingest raises — the production shape."""
    def _boom(cid, spec, **kw):
        raise RuntimeError("Server disconnected without sending a response")

    monkeypatch.setattr(mr, "_ingest_into_kg", _boom)
    spec = _spec(lambda company: _payload())
    report_id = mr.run_and_deliver(COMPANY, spec,
                                   now=FIRE + timedelta(minutes=30))
    assert report_id is not None, "the artifact must survive the failed ingest"
    return spec, report_id


def test_a_failed_ingest_leaves_the_report_pending(monkeypatch):
    _, report_id = _save_a_report_whose_ingest_failed(monkeypatch)

    pending = mr.pending_ingests(COMPANY["id"])
    assert [s.skill for s, _ in pending] == [mr.CIR_SPEC.skill]
    assert [r["id"] for _, r in pending] == [report_id]


def test_a_successful_ingest_leaves_nothing_pending(monkeypatch):
    """Once the ledger row exists the repair is a permanent no-op — it must not
    re-extract the same document on every tick for the rest of the month."""
    spec, _ = _save_a_report_whose_ingest_failed(monkeypatch)
    _write_ledger_row(spec, "## Competitive review\n\nAcme shipped X.")

    assert mr.pending_ingests(COMPANY["id"]) == []


def test_a_degraded_cycle_has_nothing_to_reconcile(monkeypatch):
    """A degraded run saves no report, so there is nothing to re-ingest — the
    repair must not invent work where no document exists."""
    monkeypatch.setattr(mr, "_ingest_into_kg", lambda cid, spec, **kw: None)
    spec = _spec(lambda company: {
        "answer": "finish onboarding…", "key_points": [], "citations": [],
        "confidence": 0.0, "_skill": "competitive-intelligence-review",
    })
    assert mr.run_and_deliver(COMPANY, spec,
                              now=FIRE + timedelta(minutes=30)) is None
    assert mr.pending_ingests(COMPANY["id"]) == []


def test_the_repair_re_ingests_without_re_running_the_sweep(monkeypatch):
    """The whole point: the document is already bought and saved, so the repair
    pays the extraction calls and NOT a second multi-minute web sweep.

    The spec handed to `ingest_saved_report` carries a runner that raises if
    called — reaching the engine again would silently double the cost of every
    transient ingest failure.
    """
    _, report_id = _save_a_report_whose_ingest_failed(monkeypatch)
    pending = mr.pending_ingests(COMPANY["id"])
    (found_spec, row), = pending

    seen: list[dict] = []
    monkeypatch.setattr(
        mr, "_ingest_into_kg",
        lambda cid, spec, **kw: seen.append({"cid": cid, "skill": spec.skill, **kw}),
    )

    def _never(company):
        raise AssertionError("the repair must not re-run the engine")

    mr.ingest_saved_report(
        COMPANY["id"],
        mr.ReportSpec(skill=found_spec.skill, question=found_spec.question,
                      label=found_spec.label, runner=_never),
        row,
    )

    expected_period = mr._period_for(row)
    assert seen == [{
        "cid": COMPANY["id"],
        "skill": mr.CIR_SPEC.skill,
        "text": "## Competitive review\n\nAcme shipped X.",
        "period": expected_period,
        "report_id": report_id,
    }]


def test_the_period_comes_from_the_timestamp_not_the_title():
    """The title is display copy and could be reworded; the timestamp is the
    fact. A period parsed back out of the title would silently mislabel every
    signal's provenance the day someone edits the title format."""
    row = {"id": 1, "title": "Something Else Entirely",
           "created_at": "2026-06-01T06:30:00+00:00"}
    assert mr._period_for(row) == "June 2026"


# ─── the scheduler shell ─────────────────────────────────────────────────────


def _run_tick(now, companies, due, ran):
    from app import scheduler as sched_mod

    def _fake_due(company_id, when, tz, schedule):
        return due.get(company_id, [])

    def _fake_run(company, spec, when=None):
        ran.append((company["id"], spec.skill))
        return 1

    with patch.object(sched_mod, "list_companies", return_value=companies), \
         patch.object(mr, "due_specs", side_effect=_fake_due), \
         patch.object(mr, "run_and_deliver", side_effect=_fake_run):
        asyncio.run(sched_mod._run_monthly_reports_tick(now=now))


def test_tick_runs_due_reports_and_honours_the_company_toggle():
    spec = mr.MONTHLY_REPORT_SPECS[0]
    companies = [
        {"id": "co-on", "slug": "acme", "owner_timezone": None,
         "notification_settings": {}},
        {"id": "co-off", "slug": "globex", "owner_timezone": None,
         "notification_settings": {"monthly_reports_enabled": False}},
    ]
    ran: list[tuple[str, str]] = []
    _run_tick(FIRE + timedelta(hours=1), companies,
              {"co-on": [spec], "co-off": [spec]}, ran)
    assert ran == [("co-on", spec.skill)]


def test_tick_repairs_a_report_that_was_saved_but_never_ingested():
    """The tick must drive the repair, not just the generation.

    A company with nothing due still has work to do when a previous cycle's
    report reached the library but not the graph — that is exactly the state a
    mid-extraction disconnect leaves behind, and it is invisible to `due_specs`
    because the saved row makes the cycle look finished.
    """
    from app import scheduler as sched_mod

    spec = mr.MONTHLY_REPORT_SPECS[0]
    companies = [{"id": "co-pending", "slug": "acme", "owner_timezone": None,
                  "notification_settings": {}}]
    report = {"id": 99, "html": "## Review\n\nfindings",
              "created_at": "2026-06-01T06:30:00+00:00"}
    repaired: list[tuple[str, str, int]] = []

    with patch.object(sched_mod, "list_companies", return_value=companies), \
         patch.object(mr, "due_specs",
                      side_effect=lambda cid, when, tz, sched: []), \
         patch.object(mr, "run_and_deliver",
                      side_effect=AssertionError("no sweep may run")), \
         patch.object(mr, "pending_ingests",
                      side_effect=lambda cid: [(spec, report)]), \
         patch.object(mr, "ingest_saved_report",
                      side_effect=lambda cid, s, r: repaired.append(
                          (cid, s.skill, r["id"]))):
        asyncio.run(sched_mod._run_monthly_reports_tick(
            now=FIRE + timedelta(hours=1)))

    assert repaired == [("co-pending", spec.skill, 99)]


def test_tick_isolates_a_failing_repair():
    """A re-ingest raising must not stop the rest of the tick, exactly as a
    failing generation doesn't."""
    from app import scheduler as sched_mod

    spec = mr.MONTHLY_REPORT_SPECS[0]
    companies = [
        {"id": "co-a", "slug": "acme", "owner_timezone": None,
         "notification_settings": {}},
        {"id": "co-b", "slug": "globex", "owner_timezone": None,
         "notification_settings": {}},
    ]
    report = {"id": 7, "html": "## Review\n\nfindings",
              "created_at": "2026-06-01T06:30:00+00:00"}
    repaired: list[str] = []

    def _repair(company_id, s, r):
        if company_id == "co-a":
            raise RuntimeError("Server disconnected without sending a response")
        repaired.append(company_id)

    with patch.object(sched_mod, "list_companies", return_value=companies), \
         patch.object(mr, "due_specs",
                      side_effect=lambda cid, when, tz, sched: []), \
         patch.object(mr, "pending_ingests",
                      side_effect=lambda cid: [(spec, report)]), \
         patch.object(mr, "ingest_saved_report", side_effect=_repair):
        asyncio.run(sched_mod._run_monthly_reports_tick(
            now=FIRE + timedelta(hours=1)))

    assert repaired == ["co-b"]


def test_tick_isolates_a_failing_company():
    """One company's engine raising must not stop the rest of the tick."""
    from app import scheduler as sched_mod

    spec = mr.MONTHLY_REPORT_SPECS[0]
    companies = [
        {"id": "co-a", "slug": "acme", "owner_timezone": None,
         "notification_settings": {}},
        {"id": "co-b", "slug": "globex", "owner_timezone": None,
         "notification_settings": {}},
    ]
    ran: list[str] = []

    def _fake_run(company, spec, when=None):
        if company["id"] == "co-a":
            raise RuntimeError("sweep exploded")
        ran.append(company["id"])
        return 1

    with patch.object(sched_mod, "list_companies", return_value=companies), \
         patch.object(mr, "due_specs",
                      side_effect=lambda cid, when, tz, sched: [spec]), \
         patch.object(mr, "run_and_deliver", side_effect=_fake_run):
        asyncio.run(sched_mod._run_monthly_reports_tick(
            now=FIRE + timedelta(hours=1)))
    assert ran == ["co-b"]


# ─── the toggle default ──────────────────────────────────────────────────────


def test_monthly_reports_on_defaults_and_toggle():
    assert mr.monthly_reports_on(None) is True
    assert mr.monthly_reports_on({}) is True
    assert mr.monthly_reports_on({"monthly_reports_enabled": "no"}) is True
    assert mr.monthly_reports_on({"monthly_reports_enabled": False}) is False


# ─── the roster's contract with the engines ──────────────────────────────────


def test_every_spec_skill_matches_its_engine_constant():
    """The spec skills are not free text — they must equal the constants the
    engines stamp on their payloads, or `_is_report` rejects every real run
    and the company silently gets no reports at all."""
    from app.competitive_intel import CIR_SKILL
    from app.market_intel import MI_SKILL
    from app.public_feedback import PF_SKILL

    assert mr.CIR_SPEC.skill == CIR_SKILL
    assert mr.PF_SPEC.skill == PF_SKILL
    assert mr.MI_SPEC.skill == MI_SKILL
    assert [s.skill for s in mr.MONTHLY_REPORT_SPECS] == [
        CIR_SKILL, PF_SKILL, MI_SKILL,
    ]


def test_spec_questions_are_unique_per_skill():
    """The question doubles as the durable ledger marker, so two specs sharing
    one would have their cycles suppress each other."""
    markers = [(s.skill, s.question) for s in mr.MONTHLY_REPORT_SPECS]
    assert len(set(markers)) == len(markers)
    assert all(q.strip() for _, q in markers)


def test_the_feedback_question_asks_for_a_fresh_report_not_a_followup():
    """`public_feedback.answer` routes to query mode — a cheap answer off the
    LAST stored run — unless the question is report-shaped. If this marker
    ever stopped matching, the monthly job would quietly re-serve last
    month's captured records as this month's report."""
    from app.public_feedback import is_followup_query

    assert is_followup_query(mr.PF_SPEC.question) is False

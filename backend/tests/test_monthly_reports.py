"""Scheduled intelligence reports (app.monthly_reports).

Covers the three layers separately, mirroring the brief's test split:

  - the PURE due decision (`due_specs`): the per-spec calendar (quarterly for
    competitive + market intelligence, monthly for 3P feedback), the absence
    of a window cliff, and BOTH once-per-period ledgers — the durable
    saved-report row and the in-memory attempt guard;
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

# A mid-period instant, used by the run/ingest tests below where the calendar
# is irrelevant. The cadence tests state their own dates.
FIRE = datetime(2026, 6, 1, 6, 0, tzinfo=UTC)

COMPANY = {"id": "co-mr-1", "slug": "acme", "owner_timezone": None,
           "notification_settings": {}}

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


def _due_skills(now, tz=None) -> list[str]:
    """The skills due right now. Asserted per-skill rather than as "the list
    is empty": the roster holds several INDEPENDENT specs, so suppressing one
    (a saved row, a spent attempt) must leave the others due — an emptiness
    check would pass today only because the roster happened to be short."""
    return [s.skill for s in mr.due_specs(
        COMPANY["id"], now, tz or ZoneInfo("UTC"))]


# ─── due_specs: the pure calendar decision ───────────────────────────────────
#
# Two cadences now. Competitive + market intelligence run QUARTERLY (1 Jan /
# 1 Apr / 1 Jul / 1 Oct); 3P feedback runs MONTHLY (the 1st). Assertions are
# per-skill rather than "the due list is empty" — with specs on different
# calendars an emptiness check passes on a sibling that simply is not due yet
# and proves nothing.

QUARTERLY = [s.skill for s in mr.MONTHLY_REPORT_SPECS
             if s.cadence == mr.CADENCE_QUARTERLY]
MONTHLY = [s.skill for s in mr.MONTHLY_REPORT_SPECS
           if s.cadence == mr.CADENCE_MONTHLY]


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


def test_the_roster_covers_both_cadences():
    """Guards the split itself: if a spec loses its cadence the tests below
    would silently assert nothing."""
    assert QUARTERLY and MONTHLY
    assert len(QUARTERLY) + len(MONTHLY) == len(mr.MONTHLY_REPORT_SPECS)


def test_period_start_pins_the_quarter_and_the_month():
    """The whole calendar, stated once. Every month maps to the quarter that
    contains it, not to the nearest boundary."""
    utc = ZoneInfo("UTC")
    for month, quarter in ((1, 1), (2, 1), (3, 1), (4, 4), (6, 4),
                           (7, 7), (9, 7), (10, 10), (12, 10)):
        now = datetime(2026, month, 15, 12, 0, tzinfo=UTC)
        assert mr.period_start(now, utc, mr.CADENCE_QUARTERLY).month == quarter
        assert mr.period_start(now, utc, mr.CADENCE_MONTHLY).month == month


def test_a_company_with_no_report_at_all_is_due_immediately():
    """A tenant that has never had this report does not wait for a boundary.

    THIS IS THE JOIN RUN. A company that finishes onboarding mid-period has no
    report for that period, so the next tick runs every spec once — no
    onboarding hook, no separate first-run path. It is also what backfills a
    tenant the scheduler never reached.
    """
    mid_quarter = datetime(2026, 5, 12, 9, 0, tzinfo=UTC)
    assert [s.skill for s in mr.due_specs(
        COMPANY["id"], mid_quarter, ZoneInfo("UTC"))] == [s.skill for s in mr.MONTHLY_REPORT_SPECS]


def test_a_quarterly_report_is_satisfied_for_the_whole_quarter():
    spec = mr.MONTHLY_REPORT_SPECS[0]
    assert spec.cadence == mr.CADENCE_QUARTERLY
    _save_report_at(spec, datetime(2026, 4, 1, 7, 0, tzinfo=UTC))

    # Deep inside Q2, and right up to the last hour of it.
    assert spec.skill not in _due_skills(datetime(2026, 5, 20, 9, 0, tzinfo=UTC))
    assert spec.skill not in _due_skills(datetime(2026, 6, 30, 23, 0, tzinfo=UTC))
    # ...and due again the moment Q3 opens.
    assert spec.skill in _due_skills(datetime(2026, 7, 1, 16, 0, tzinfo=UTC))


def test_the_monthly_report_reruns_while_the_quarterly_one_waits():
    """The point of the split: in the second month of a quarter, 3P feedback
    is due again and the quarterly pair is not."""
    for spec in mr.MONTHLY_REPORT_SPECS:
        _save_report_at(spec, datetime(2026, 4, 1, 7, 0, tzinfo=UTC))

    may = _due_skills(datetime(2026, 5, 1, 16, 0, tzinfo=UTC))
    assert sorted(may) == sorted(MONTHLY)


def test_no_window_cliff_inside_a_period():
    """Missing the 1st must not cost the period.

    The tick walks companies SEQUENTIALLY and one report costs roughly 25
    minutes of sweep plus extraction, so 40 tenants x 3 reports cannot fit in
    any narrow window — whoever sorts last would get nothing at all. Being due
    until a saved row closes the period is what lets a slow pass, an outage or
    a restart catch up.
    """
    for day in (2, 9, 20, 27):
        now = datetime(2026, 5, day, 9, 0, tzinfo=UTC)
        assert [s.skill for s in mr.due_specs(
            COMPANY["id"], now, ZoneInfo("UTC"))] == [s.skill for s in mr.MONTHLY_REPORT_SPECS], f"day {day}"


def test_a_saved_report_closes_the_period_however_open_the_window():
    """The open window is only safe because the durable ledger closes the
    period — catching up must never become double-firing."""
    from app import db

    spec = mr.MONTHLY_REPORT_SPECS[0]
    db.save_report(COMPANY["id"], skill=spec.skill, title="t",
                   html="## body", question=spec.question)
    assert spec.skill not in _due_skills(datetime.now(UTC) + timedelta(days=3))


def test_the_opening_day_waits_for_each_spec_s_own_hour():
    """On the 1st every report holds until ITS hour, and they are staggered.

    The stagger is the load control: the tick walks companies sequentially and
    one report costs tens of minutes, so three reports opening together would
    queue a tenant's whole set behind one sweep. Asserted as a sequence rather
    than per-spec, because what matters is that they do not coincide.

    Only the first day is gated — a period whose opening tick was missed is
    due at any hour after it (see the no-cliff test), or the gate would become
    the window cliff it replaced.
    """
    for spec in mr.MONTHLY_REPORT_SPECS:
        _save_report_at(spec, datetime(2026, 4, 1, 7, 0, tzinfo=UTC))

    day = lambda h: datetime(2026, 7, 1, h, 0, tzinfo=UTC)  # noqa: E731
    assert _due_skills(day(7)) == []
    assert _due_skills(day(9)) == [mr.CIR_SPEC.skill]
    assert sorted(_due_skills(day(12))) == sorted(
        [mr.CIR_SPEC.skill, mr.MI_SPEC.skill])
    # 3P feedback is monthly, so July opens its period too — by 16:00 all three.
    assert sorted(_due_skills(day(16))) == sorted(
        [s.skill for s in mr.MONTHLY_REPORT_SPECS])


def test_the_report_hours_are_distinct():
    """Guards the stagger itself: if two specs drift onto the same hour the
    test above still passes on one of them, but the load control is gone."""
    hours = [s.hour for s in mr.MONTHLY_REPORT_SPECS]
    assert len(set(hours)) == len(hours), hours


def test_the_period_boundary_tracks_the_company_timezone():
    """10:00 on 1 July in Auckland (UTC+12) is 22:00 UTC on 30 June, so a New
    Zealand tenant opens its quarter while a UTC one is still in the old
    period — the boundary is local, not a single global instant."""
    nz = ZoneInfo("Pacific/Auckland")
    spec = mr.MONTHLY_REPORT_SPECS[0]
    _save_report_at(spec, datetime(2026, 4, 1, 7, 0, tzinfo=UTC))

    # 30 June 20:30 UTC == 1 July 08:30 in Auckland: past CIR's hour there.
    crossed = datetime(2026, 6, 30, 20, 30, tzinfo=UTC)
    assert spec.skill in [s.skill for s in mr.due_specs(
        COMPANY["id"], crossed, nz)]
    # Same instant, a UTC tenant is still inside Q2.
    assert spec.skill not in [s.skill for s in mr.due_specs(
        COMPANY["id"], crossed, ZoneInfo("UTC"))]

def test_saved_scheduled_report_suppresses_the_cycle():
    """The durable ledger: a reports row carrying the spec's canonical
    question marker, saved after this cycle's fire, means not due."""
    from app import db

    spec = mr.MONTHLY_REPORT_SPECS[0]
    db.save_report(COMPANY["id"], skill=spec.skill, title="t",
                   html="## body", question=spec.question)
    due = _due_skills(FIRE + timedelta(hours=10))
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
    assert spec.skill in _due_skills(FIRE + timedelta(hours=10))


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
    # Read after the LAST spec's hour, so the siblings below are gated only by
    # their own ledgers and not by the opening-day clock.
    due = _due_skills(attempt + timedelta(hours=9))
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

    def _fake_due(company_id, when, tz):
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
                      side_effect=lambda cid, when, tz: []), \
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
                      side_effect=lambda cid, when, tz: []), \
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
                      side_effect=lambda cid, when, tz: [spec]), \
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

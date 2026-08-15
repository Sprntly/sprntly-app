"""Monthly scheduled intelligence reports (app.monthly_reports).

Covers the three layers separately, mirroring the brief's test split:

  - the PURE due decision (`due_specs`): monthly cadence forced over the
    company's brief schedule, the 24h window, and BOTH once-per-cycle
    ledgers — the durable saved-report row and the in-memory attempt guard;
  - the RUN (`run_and_deliver`): a real payload becomes a `reports` row with
    the canonical question marker and is announced; a degraded payload saves
    nothing, announces nothing, and is not retried this cycle;
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


def test_not_due_before_fire_or_after_window():
    # An hour before the monthly fire instant — nothing is due.
    assert mr.due_specs(COMPANY["id"], FIRE - timedelta(hours=1),
                        ZoneInfo("UTC"), DEFAULT_SCHEDULE) == []
    # Past the 24h window — the cycle was missed, wait for next month.
    assert mr.due_specs(COMPANY["id"], FIRE + timedelta(hours=25),
                        ZoneInfo("UTC"), DEFAULT_SCHEDULE) == []


def test_monthly_cadence_is_forced_over_the_brief_frequency():
    """A company on a DAILY brief cadence still gets reports monthly: the
    second Monday of the month is a brief day but NOT a report day."""
    schedule = dict(DEFAULT_SCHEDULE, frequency="daily_weekdays")
    second_monday = datetime(2026, 6, 8, 7, 0, tzinfo=UTC)
    assert mr.due_specs(COMPANY["id"], second_monday,
                        ZoneInfo("UTC"), schedule) == []
    # ...while the first Monday fires as usual.
    assert mr.due_specs(COMPANY["id"], FIRE + timedelta(hours=1),
                        ZoneInfo("UTC"), schedule) != []


def test_fire_instant_tracks_the_company_timezone():
    """Monday 06:00 in New York is 10:00 UTC (EDT) — 07:00 UTC is too early
    there, while a UTC company is already inside its window."""
    ny = ZoneInfo("America/New_York")
    early = datetime(2026, 6, 1, 7, 0, tzinfo=UTC)
    assert mr.due_specs(COMPANY["id"], early, ny, DEFAULT_SCHEDULE) == []
    at_fire = datetime(2026, 6, 1, 10, 30, tzinfo=UTC)
    assert mr.due_specs(COMPANY["id"], at_fire, ny, DEFAULT_SCHEDULE) != []


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


def test_attempt_ledger_suppresses_within_the_cycle():
    """A failed (unsaved) attempt this cycle is not retried by later ticks —
    the paid sweep is bought at most once per cycle per process."""
    spec = mr.MONTHLY_REPORT_SPECS[0]
    mr._last_attempt[(COMPANY["id"], spec.skill)] = FIRE + timedelta(hours=1)
    due = _due_skills(FIRE + timedelta(hours=3))
    assert spec.skill not in due
    # A spent attempt on one report never stands in for another's.
    assert [s.skill for s in mr.MONTHLY_REPORT_SPECS[1:]] == due


# ─── run_and_deliver: generate → save → announce ─────────────────────────────


def test_run_saves_the_report_and_announces(monkeypatch):
    announced: list[tuple[str, str]] = []
    monkeypatch.setattr(mr, "_announce",
                        lambda cid, spec: announced.append((cid, spec.skill)))
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
    assert announced == [(COMPANY["id"], spec.skill)]
    # ...and the saved row is what makes this spec's next due-check a no-op.
    assert spec.skill not in _due_skills(now + timedelta(hours=2))


def test_degraded_payload_saves_and_announces_nothing(monkeypatch):
    """An apology is not a monthly report. Nothing is saved, nothing
    announced, and the attempt is still recorded so the sweep isn't re-bought
    this cycle.

    The payload here is the engines' REAL degraded shape: `_plain_payload`
    stamps `_skill` on its apologies exactly as the success return does, so
    this is precisely the case a `_skill`-only check would wave through —
    filing "finish onboarding and I'll…" as the month's report AND stamping
    the durable ledger, suppressing the real one until next month.
    """
    announced: list = []
    monkeypatch.setattr(mr, "_announce",
                        lambda cid, spec: announced.append(cid))
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
    assert announced == []
    assert (COMPANY["id"], spec.skill) in mr._last_attempt
    assert spec.skill not in _due_skills(now + timedelta(hours=2))


def test_query_mode_followup_is_not_a_report(monkeypatch):
    """Query mode answers a follow-up off a STORED run and stamps `_skill`
    too. It is an answer about a past report, not this month's — saving it
    would file a one-line reply as the monthly artifact."""
    announced: list = []
    monkeypatch.setattr(mr, "_announce",
                        lambda cid, spec: announced.append(cid))
    spec = _spec(lambda company: {
        "answer": "Two competitors shipped pricing changes.",
        "key_points": [], "citations": [], "confidence": 0.5,
        "_skill": "competitive-intelligence-review",
        "_skill_action": "Competitive intelligence · from the 2026-05-04 review",
        "_skill_source": "competitive-intel-query",
    })

    assert mr.run_and_deliver(COMPANY, spec,
                              now=FIRE + timedelta(minutes=30)) is None
    assert announced == []


def test_a_payload_from_the_wrong_engine_is_rejected(monkeypatch):
    """`_report` alone is not enough: a runner wired to the wrong engine
    returns a real document under another skill's name, and filing it under
    this spec would mislabel the artifact and stamp the wrong ledger."""
    monkeypatch.setattr(mr, "_announce", lambda cid, spec: None)
    spec = _spec(lambda company: _payload(skill="public-feedback-report"))
    assert mr.run_and_deliver(COMPANY, spec,
                              now=FIRE + timedelta(minutes=30)) is None


def test_announcement_failure_never_loses_the_saved_report(monkeypatch):
    def _boom(cid, spec):
        raise RuntimeError("slack fell over")

    monkeypatch.setattr(mr, "_announce", _boom)
    spec = _spec(lambda company: _payload())
    report_id = mr.run_and_deliver(COMPANY, spec,
                                   now=FIRE + timedelta(minutes=30))
    assert report_id is not None  # the artifact survived the failed ping


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
    from app.public_feedback import PF_SKILL

    assert mr.CIR_SPEC.skill == CIR_SKILL
    assert mr.PF_SPEC.skill == PF_SKILL
    assert [s.skill for s in mr.MONTHLY_REPORT_SPECS] == [CIR_SKILL, PF_SKILL]


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

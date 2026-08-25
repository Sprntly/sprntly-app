"""Scheduled intelligence reports — run, save into the library, ingest.

The intelligence reports (competitive intelligence, 3P feedback and market
intelligence) have always been PULL: someone remembers
to ask, a multi-minute web sweep runs, the answer lands in that chat and
nowhere else. This module runs them on a calendar cadence instead: once a
quarter or once a month, per company, each registered report runs headlessly, the finished document is
saved into the `reports` artifacts library, and its contents are extracted into
the knowledge graph — so the report exists whether or not anyone remembered to
ask for it, and answers a question the moment someone does.

Three deliberate reuses, so this stays a thin layer over proven parts:

  CADENCE   — the calendar, per spec. Competitive and market intelligence
              are QUARTERLY (1 Jan / 1 Apr / 1 Jul / 1 Oct); 3P feedback is
              MONTHLY (the 1st), each at its own staggered local hour
              (08:00 / 11:00 / 15:00) so they never start together — the
              report's own calendar and clock, no longer borrowed from the
              brief's weekday schedule.
  ENGINE    — `competitive_intel.answer` exactly as chat and Slack call it
              (the Slack events path proved it runs headlessly). A scheduled
              run is a Scan against stored state — the cheap concurrent
              sweep — never a forced staged Review.
  INGEST    — `kg_ingest.monthly_report`, the same shared extractor every
              other source writes through, with replace semantics per report
              so the graph holds the CURRENT month's picture.

REACH, not notification. This deliberately does NOT ping Slack or email when a
report lands (it did until 2026-08-18). A ready-ping reached whoever read the
channel that morning and left the findings sealed inside one artifact row; the
KG ingest reaches anyone who later asks a question the report answers, which is
the point of paying for the sweep. `ask_runner` already retrieves from the
graph generically, so no routing or prompt work was needed to make the reports
answerable — and the artifact is still in the library to open and read.

Two ledgers, for two different failure costs:

  DURABLE   — the `reports` row itself. A saved scheduled report (matched by
              company + skill + the canonical `question` marker) IS the
              "already ran this cycle" record, so a process restart can never
              double-send a month's report. No new table, no migration.
  IN-MEMORY — `_last_attempt`, marked BEFORE the runner spends anything. A
              degraded run (web search down, synthesis error) produces no
              reports row, and without this guard the tick would re-buy the
              paid sweep every hour. It throttles retries to one per
              RETRY_BACKOFF — it does NOT mark the cycle done, which is the
              durable ledger's job alone.

THE TICK'S REAL QUESTION IS "does this company have THIS PERIOD's report yet?"
The period is also the window: a company is due from the moment its quarter or
month opens until a saved row closes it, so a run missed on the 1st is picked
up on the 2nd, or the 9th. That matters because the tick walks companies
SEQUENTIALLY and one report costs roughly 25 minutes of sweep plus extraction
— 40 tenants x 3 reports cannot fit in any narrow window, and a cliff would
silently starve whoever sorted last. The durable ledger is what makes an open
window safe: it can catch up indefinitely, but a saved row ends the period, so
it can never double-fire.

IT ALSO MAKES THE JOIN RUN FREE. A tenant that finishes onboarding mid-period
has no report for that period, so the next tick finds it due, runs each report
once, and the saved rows carry it to the next boundary. No onboarding hook, no
backfill job, no separate first-run path to keep in sync with this one.

Ingest is best-effort by contract — the report is already saved, and losing the
artifact as well would turn a retrievability problem into data loss. But
best-effort must not mean give-up-for-a-month: saving and extracting are two
failures, and only the save is recorded in the durable ledger, so a raised
ingest would otherwise leave the cycle marked done with nothing in the graph.
`pending_ingests` + `ingest_saved_report` are the repair — the next tick
re-extracts the already-saved document, paying the extraction calls but not a
second web sweep, and going permanently quiet once its ledger row lands.

A company with no
competitor set / profile degrades inside the engine to a plain chat message
("finish onboarding and I'll…"), and those runs must save nothing and ingest
nothing: an apology is not a monthly report, and saving one would ALSO stamp
the durable ledger and so suppress the real report for the rest of the month —
and would put the apology's sentences into the graph as if they were findings.

What separates the two is the engines' explicit `_report: True`, set only on
the one return in each that is a finished document. `_skill` cannot carry that
meaning — the engines stamp it on their degraded `_plain_payload` apologies
and on query-mode follow-ups too — so a `_skill`-only test accepts exactly the
runs this module must reject.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo


logger = logging.getLogger(__name__)

CADENCE_QUARTERLY = "quarterly"
CADENCE_MONTHLY = "monthly"

# The report's own calendar, no longer borrowed from the brief's schedule.
#
# The brief fires on a weekday the company picks; a report fires on a date.
# Reusing `should_run_brief` meant a report's cadence was expressed as "the
# first configured weekday of the month", which is not a thing anyone asked
# for and which put every tenant on whatever weekday they had chosen for an
# unrelated surface. The period is now the calendar quarter or month, and the
# hour of day still comes from the brief settings so Sprntly things keep
# arriving at the time the company chose.
#
# THE PERIOD IS ALSO THE WINDOW. There is no separate due-window constant any
# more: a company is due from the moment its period opens until it has a saved
# report for that period, and the saved row is what closes it. That is what
# makes the join case free — a tenant that onboards on 12 February has no
# report for February, so the next tick treats it as due, runs once, and the
# saved row carries it to 1 March. No onboarding hook, no backfill job.
_QUARTER_START_MONTHS = (1, 4, 7, 10)

# Each report has its OWN hour, and the gaps are the point.
#
# The tick walks companies sequentially and a single report costs roughly 25
# minutes of web sweep plus extraction, so three reports opening at the same
# instant would queue every tenant's whole set behind one another and pile the
# paid sweeps into one window. Staggering them by hours means a company's
# quarterly pair is already finished before its monthly report starts, and the
# load spreads across the day rather than landing at once.
#
# Local to the company, so the spread holds per tenant rather than globally.

# How long a SPENT attempt suppresses the next one, when that attempt produced
# no report (the engine degraded, the sweep raised).
#
# The attempt ledger can no longer mean "once per cycle": with the window now
# spanning the whole month, one early failure would cost the month, which is
# the exact failure the wide window exists to prevent. But the opposite —
# retrying every tick — re-buys a multi-minute paid sweep hourly for a tenant
# whose report legitimately cannot be built (Sprntly's 3P feedback has no
# public footprint to find, and would burn ~700 sweeps a month discovering
# that). A week of backoff gives roughly four attempts per cycle: enough to
# ride out an outage, cheap enough to be harmless when the answer is a
# permanent "not enough data".
#
# In-memory, like the ledger it guards, so a restart forgives the backoff and
# earns one more attempt. That errs toward running the report, which is the
# right direction to err in.
RETRY_BACKOFF = timedelta(days=7)


@dataclass(frozen=True)
class ReportSpec:
    """One scheduled monthly report.

    `question` doubles as the DURABLE LEDGER MARKER: it is stored on the
    saved reports row, and `latest_report_at(skill=..., question=...)`
    matching on it is how a scheduled row is told apart from the same skill
    run by a human in chat (whose row carries their own words). Changing it
    orphans the ledger for one cycle — treat it as an identifier, not copy.

    `runner` takes the company row and returns the engine's Ask-shaped
    payload (or None). A payload is accepted as a real report only when it
    carries `_report: True` AND its `_skill` equals `skill` — see
    `_is_report`; the `_skill` half alone would also accept the engines'
    degraded apologies and their query-mode follow-ups.

    `cadence` is how often the report repeats: "quarterly" fires on 1 Jan /
    1 Apr / 1 Jul / 1 Oct, "monthly" on the 1st. `hour` is the local hour it
    opens at — staggered per spec so three reports never start together and
    queue a tenant's whole set behind one sweep.
    """
    skill: str
    question: str
    label: str
    runner: Callable[[dict], dict | None]
    cadence: str = CADENCE_MONTHLY
    hour: int = 10


def _run_cir(company: dict) -> dict | None:
    """Run the competitive-intelligence engine headlessly for one company.

    Lazy import: competitive_intel pulls in qa_agent, and this module is
    imported by the scheduler at startup — keep that cost (and any import
    cycle) out of process boot, matching how routes import the engines.
    """
    from app import competitive_intel

    return competitive_intel.answer(
        enterprise_id=company["id"],
        question=CIR_SPEC.question,
    )


# "scan" keeps choose_mode off the staged Review path: a scheduled run is the
# cheap concurrent sweep over stored state (a first-ever run is a baseline
# Review at scan depth, exactly as in chat). Not "monthly review" — "review"
# phrasing risks reading as a deliberate full-depth ask.
CIR_SPEC = ReportSpec(
    skill="competitive-intelligence-review",
    question="Scheduled monthly competitive intelligence scan",
    label="Competitive Intelligence report",
    runner=_run_cir,
    cadence=CADENCE_QUARTERLY,
    hour=8,
)

def _run_pf(company: dict) -> dict | None:
    """Run the public-feedback (3P feedback) engine headlessly for one company.

    Lazy import for the same reason as `_run_cir`.
    """
    from app import public_feedback

    return public_feedback.answer(
        enterprise_id=company["id"],
        question=PF_SPEC.question,
    )


# The wording is load-bearing twice over. As with CIR it is the durable ledger
# marker, so it must stay stable. It must ALSO stay report-shaped:
# `public_feedback.answer` routes a question to query mode — a cheap follow-up
# answered off the LAST stored run — unless `_REPORT_SHAPED` matches first.
# "public feedback" satisfies that pattern, so this asks for a fresh capture.
# A phrasing that missed it would re-serve last month's records as this
# month's report, and only when a stored run happened to exist.
PF_SPEC = ReportSpec(
    skill="public-feedback-report",
    question="Scheduled monthly public feedback report",
    label="3P Feedback report",
    runner=_run_pf,
    hour=15,
)

def _run_mi(company: dict) -> dict | None:
    """Run the market-intelligence engine headlessly for one company.

    Lazy import for the same reason as `_run_cir`.
    """
    from app import market_intel

    return market_intel.answer(
        enterprise_id=company["id"],
        question=MI_SPEC.question,
    )


# Unlike the other two this question has no routing contract to satisfy — the
# module has no query mode to be diverted into — but it is still the durable
# ledger marker, so it is an identifier and must stay stable.
MI_SPEC = ReportSpec(
    skill="market-intelligence-report",
    question="Scheduled monthly market intelligence report",
    label="Market Intelligence report",
    runner=_run_mi,
    cadence=CADENCE_QUARTERLY,
    hour=11,
)

# The monthly roster. Each entry is independent: one spec degrading, raising,
# or being suppressed never touches another, and the tick, both ledgers and
# delivery are all written for N.
MONTHLY_REPORT_SPECS: tuple[ReportSpec, ...] = (CIR_SPEC, PF_SPEC, MI_SPEC)


# In-memory once-per-cycle ATTEMPT ledger, (company_id, skill) → aware UTC
# instant, marked before the runner spends anything. The durable ledger only
# records success; this one exists so a failed sweep isn't re-bought every
# tick inside the 24h window. Process-local by design: a restart re-attempts
# once, which is the retry budget we want.
_last_attempt: dict[tuple[str, str], datetime] = {}


def monthly_reports_on(notification_settings: dict | None) -> bool:
    """Company-level toggle, read from `notification_settings`.

    `monthly_reports_enabled` missing (every pre-existing company) or
    non-bool ⇒ ON — the feature ships enabled, the key exists to opt out.
    """
    ns = notification_settings if isinstance(notification_settings, dict) else {}
    v = ns.get("monthly_reports_enabled")
    return v if isinstance(v, bool) else True


def _parse_created_at(raw: str | None) -> datetime | None:
    """Parse a reports-row `created_at` into an aware UTC datetime.

    Unparseable ⇒ None (treated as "no prior run"): the failure mode of a
    bad timestamp is one extra due-check, and the attempt ledger caps what
    that can cost — whereas treating it as "already ran" would silently stop
    a company's reports forever.
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("monthly-reports: unparseable created_at %r", raw)
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def has_current_report(
    company_id: str,
    spec: ReportSpec,
    now: datetime | None = None,
    tz: ZoneInfo | None = None,
    *,
    entity: str | None = None,
) -> bool:
    """Does a scheduled report for THIS period already exist AND cover the
    subject the question named?

    Read by `qa_agent` before it hands a routed question to a report engine.
    The engines answer by buying a multi-minute web sweep, which was the only
    option while the KG held first-party signal alone — but a saved report is
    now extracted into the graph, so the same question can be answered from
    signals that already carry the report's own provenance. This is the check
    that tells those two situations apart.

    Deliberately the SAME period the scheduler uses, rather than a separate
    freshness constant: "there is a report for this quarter" is exactly the
    condition under which the graph holds the current picture, and a second
    notion of recency would be one more thing to keep in step with the
    cadence.

    `entity` is the subject the planner extracted from the question
    (`plan.constraints["entity"]` — a specific company, product or account).
    Freshness alone was never the whole condition: a current report makes the
    graph authoritative about WHAT THAT REPORT COVERED, and nothing else. A
    company whose quarterly competitive review covers Acme and Globex, asked
    for a review of a fourth company they have never tracked, was answered
    from a graph that holds nothing about it — the sweep that would have gone
    and looked was suppressed by a report that had never heard of the subject.
    So a named subject the saved document does not mention does not count as
    covered, and the engine goes to the web for it.

    The coverage test is a substring scan of the saved report's own body,
    which is the only record of what it looked at that every spec shares —
    CIR has a competitor set, MI and PF have nothing comparable.

    UTC by default. A period boundary is a date, and shifting it by a
    company's timezone changes the answer only for a few hours either side of
    a quarter opening — during which the worst case is buying the sweep the
    scheduler was about to run anyway.

    FAILS CLOSED, to the sweep. This runs on the answer path for every routed
    report question, so a Supabase blip must not take the answer down with it
    — an unreadable ledger degrades to "no current report", which is exactly
    the behaviour this branch had before the check existed. The cost of being
    wrong that way is one sweep; the cost of raising is no answer at all.
    """
    from app import db

    subject = " ".join((entity or "").split())
    try:
        if subject:
            # The row, not just its timestamp — coverage needs the body. Read
            # only when a subject was named, so the common no-entity question
            # still pays for one `created_at` rather than a whole document.
            row = db.latest_scheduled_report(
                company_id, skill=spec.skill, question=spec.question,
            ) or {}
            # ponytail: substring scan, so a mention in passing reads as
            # covered. Enough while the failure being fixed is a subject that
            # appears nowhere in the document at all.
            if subject.lower() not in (row.get("html") or "").lower():
                return False
            saved = _parse_created_at(row.get("created_at"))
        else:
            saved = _parse_created_at(db.latest_report_at(
                company_id, skill=spec.skill, question=spec.question,
            ))
    except Exception:  # noqa: BLE001 — never break the answer path
        logger.exception(
            "monthly-reports: freshness read failed for %s / %s — sweeping",
            company_id, spec.skill,
        )
        return False
    if saved is None:
        return False
    now = now or datetime.now(timezone.utc)
    return saved >= period_start(now, tz or ZoneInfo("UTC"), spec.cadence)


def period_start(now: datetime, tz: ZoneInfo, cadence: str) -> datetime:
    """The instant this report's current period opened, as aware UTC.

    Quarterly → 1 Jan / 1 Apr / 1 Jul / 1 Oct; monthly → the 1st. Computed in
    the company's timezone so a tenant in Auckland and one in Los Angeles each
    get their period boundary at their own local midnight-plus-brief-hour,
    then converted back to UTC because that is what `created_at` is stored in.
    """
    local = now.astimezone(tz)
    month = (
        max(m for m in _QUARTER_START_MONTHS if m <= local.month)
        if cadence == CADENCE_QUARTERLY
        else local.month
    )
    return local.replace(
        month=month, day=1, hour=0, minute=0, second=0, microsecond=0,
    ).astimezone(timezone.utc)


def due_specs(
    company_id: str,
    now: datetime,
    tz: ZoneInfo,
) -> list[ReportSpec]:
    """The specs due for this company right now.

    Due = this company has no saved report for the CURRENT PERIOD (the
    calendar quarter for CIR/MI, the month for 3P feedback) AND no attempt was
    spent in the last RETRY_BACKOFF.

    The brief's schedule is no longer consulted at all: the period boundary is
    a date, not a weekday, and each spec opens at its own staggered `hour`.

    The two ledgers answer different questions and are deliberately no longer
    folded into one `last_run`. The SAVED REPORT is the durable, permanent
    answer to "has this month's report been produced?" — once a row exists the
    cycle is over for good. The ATTEMPT is a cost guard, not a record of
    success: it only holds the next try off for RETRY_BACKOFF, so a run that
    degraded or raised gets several more chances inside the same cycle instead
    of forfeiting the month.

    Folding them together (the previous shape) made any spent attempt as final
    as a delivered report, which was survivable only because the window was
    24h. With a month-wide window it would mean one early hiccup costs the
    whole month — so the distinction now has to be explicit.
    """
    from app import db

    due: list[ReportSpec] = []
    for spec in MONTHLY_REPORT_SPECS:
        last_saved = _parse_created_at(
            db.latest_report_at(
                company_id, skill=spec.skill, question=spec.question,
            )
        )
        opened = period_start(now, tz, spec.cadence)
        # Hold the opening day's run until this spec's hour. Only the first
        # day is gated: a tenant that onboards mid-period, or a period whose
        # opening tick was missed, is due immediately rather than waiting for
        # a clock that has already gone past.
        local = now.astimezone(tz)
        if local.day == 1 and local.hour < spec.hour:
            continue
        if last_saved is not None and last_saved >= opened:
            continue
        attempted = _last_attempt.get((company_id, spec.skill))
        if attempted is not None and (now - attempted) < RETRY_BACKOFF:
            continue
        due.append(spec)
    return due


def pending_ingests(company_id: str) -> list[tuple[ReportSpec, dict]]:
    """Specs whose newest scheduled report was SAVED but never reached the KG.

    Saving the report and extracting it are two failures with two costs, and
    only one of them is recorded. The durable ledger keys on the `reports` row,
    so once the document is saved the cycle counts as done — if the ingest then
    raised (a stale Supabase connection mid-extraction, a provider outage), the
    tick would never revisit it and the month's findings would stay
    unretrievable until the next cycle overwrote them. That is a whole month
    lost to a transient network error, which is not a trade worth making.

    This is the cheap repair: the report is already bought and saved, so
    re-ingesting it costs only the extraction calls — no second web sweep. It
    reads one ledger query per company plus one report lookup per spec, both
    small, and once a report is ingested its ledger row makes this a permanent
    no-op.

    No time bound is needed. Only the NEWEST scheduled report per spec is ever
    considered, so this can only ever ingest the most recent picture — exactly
    what replace semantics wants — and a spec whose generation keeps failing
    simply has nothing newer to reconcile.
    """
    from app import db
    from app.kg_ingest.monthly_report import is_ingested, ledger_rows

    rows = ledger_rows(company_id)
    out: list[tuple[ReportSpec, dict]] = []
    for spec in MONTHLY_REPORT_SPECS:
        report = db.latest_scheduled_report(
            company_id, skill=spec.skill, question=spec.question,
        )
        if not report:
            continue
        text = str(report.get("html") or "").strip()
        if not text:
            continue
        if is_ingested(company_id, report_skill=spec.skill, text=text,
                       ledger_rows=rows):
            continue
        out.append((spec, report))
    return out


def ingest_saved_report(company_id: str, spec: ReportSpec, report: dict) -> None:
    """Re-run the KG ingest for a report already saved to the library.

    The repair half of `pending_ingests`. Deliberately NOT wrapped in a
    try/except here — the scheduler isolates it per report, exactly as it
    isolates a generation run.
    """
    _ingest_into_kg(
        company_id, spec,
        text=str(report.get("html") or ""),
        period=_period_for(report),
        report_id=report.get("id"),
    )


def _period_for(report: dict) -> str:
    """The report's month, taken from `created_at` rather than parsed back out
    of its title — the title is display copy and could be reworded, the
    timestamp is the fact."""
    created = _parse_created_at(report.get("created_at"))
    return (created or datetime.now(timezone.utc)).strftime("%B %Y")


def _is_report(payload: object, spec: ReportSpec) -> bool:
    """True only for a payload that is a finished report document.

    Both halves are required and neither is redundant. `_report` is the
    engines' explicit "this is a document" flag, set on the single success
    return in each — without it, the degraded `_plain_payload` apologies and
    the query-mode follow-ups (which carry `_skill` too) would be saved as
    that month's report and stamp the ledger, suppressing the real one.
    `_skill` then confirms the payload came from the engine this spec asked
    for, so a runner wired to the wrong engine fails loudly rather than
    filing its output under the wrong report's name.
    """
    return (
        isinstance(payload, dict)
        and payload.get("_report") is True
        and payload.get("_skill") == spec.skill
    )


def run_and_deliver(company: dict, spec: ReportSpec,
                    now: datetime | None = None) -> int | None:
    """Run one scheduled report for one company: generate → save → ingest.

    Returns the saved report id, or None when the run degraded (no profile,
    no competitor set, web search down — the engine's plain-message cases) or
    the save yielded no row. Marks the attempt ledger FIRST so a failure
    can't be re-bought this cycle. Ingest failures are logged and swallowed
    — the artifact is already in the library.
    """
    company_id = company["id"]
    now = now or datetime.now(timezone.utc)
    _last_attempt[(company_id, spec.skill)] = now

    payload = spec.runner(company)
    if not _is_report(payload, spec):
        logger.info(
            "monthly-reports: %s for company %s degraded — nothing saved",
            spec.skill, company_id,
        )
        return None
    answer = str(payload.get("answer") or "").strip()
    if not answer:
        return None

    from app import db

    # "Competitive Intelligence report · August 2026" — the artifacts-row
    # label. Month named in UTC: the fire instant is within hours of the
    # month boundary only for extreme timezones, and a stable label beats a
    # per-tenant one. The same period string rides the ingest, so a signal's
    # provenance names the month its finding came from.
    period = now.strftime("%B %Y")
    title = f"{spec.label} · {period}"
    report_id = db.save_report(
        company_id,
        skill=spec.skill,
        title=title,
        html=answer,
        question=spec.question,
    )
    if report_id is None:
        logger.warning(
            "monthly-reports: save yielded no row for %s / %s",
            company_id, spec.skill,
        )
        return None
    logger.info(
        "monthly-reports: saved %s for company %s as report %s",
        spec.skill, company_id, report_id,
    )

    try:
        _ingest_into_kg(
            company_id, spec, text=answer, period=period, report_id=report_id,
        )
    except Exception:  # noqa: BLE001 — the artifact is saved; ingest is extra
        logger.exception(
            "monthly-reports: KG ingest failed for %s / %s",
            company_id, spec.skill,
        )
    return report_id


def _ingest_into_kg(
    company_id: str, spec: ReportSpec, *,
    text: str, period: str, report_id: int | None,
) -> None:
    """Extract the finished report into the knowledge graph.

    This is what makes a report reachable by question instead of only by
    browsing the library — see `kg_ingest.monthly_report` for the replace
    semantics, the per-report expiry scoping, and why report signals are pinned
    non-evidence. Called only for a run that produced a real document, so the
    graph never takes in a degraded run's apology.

    The `reports` row id rides along, so an answer grounded on a finding can
    point back at the artifact the finding came from.
    """
    from app.kg_ingest.monthly_report import ingest_report

    result = ingest_report(
        company_id,
        report_skill=spec.skill,
        report_label=spec.label,
        period=period,
        text=text,
        report_id=report_id,
    )
    logger.info(
        "monthly-reports: KG ingest for %s / %s → %s",
        company_id, spec.skill, result,
    )

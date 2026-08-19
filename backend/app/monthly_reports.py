"""Monthly scheduled intelligence reports — run, save into the library, ingest.

The intelligence reports (competitive intelligence, 3P feedback and market
intelligence) have always been PULL: someone remembers
to ask, a multi-minute web sweep runs, the answer lands in that chat and
nowhere else. This module runs them on a monthly cadence instead: once a month,
per company, each registered report runs headlessly, the finished document is
saved into the `reports` artifacts library, and its contents are extracted into
the knowledge graph — so the report exists whether or not anyone remembered to
ask for it, and answers a question the moment someone does.

Three deliberate reuses, so this stays a thin layer over proven parts:

  CADENCE   — the pure `app.brief_schedule` decisions with the frequency
              FORCED to MONTHLY: the report fires the first configured brief
              weekday of each month at the company's configured brief time,
              in the company's timezone. One mental model ("my Sprntly
              things arrive on my brief day"), zero new date logic.
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

THE TICK'S REAL QUESTION IS "does this company have this month's report yet?"
The window spans the whole cycle (REPORT_DUE_WINDOW), so a company is due from
its fire instant until the next one, and anything missed on the fire day is
picked up the next day. That matters because the tenants are not spread out:
they cluster on two fire slots, the tick walks them sequentially, and one
report costs tens of minutes — so a narrow window silently starved whoever
sorted last. The durable ledger is what makes a wide window safe: it can catch
up indefinitely, but a saved row ends the cycle, so it can never double-fire.

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

from app.brief_schedule import FREQ_MONTHLY, should_run_brief

logger = logging.getLogger(__name__)

# The whole cycle counts as due — there is deliberately NO window cliff.
#
# This was 24h, which quietly meant "miss the day, miss the month". The
# arithmetic that killed it: 39 of 40 tenants sit on the same fire slot (first
# Monday, 06:00 or 09:00), the tick walks companies SEQUENTIALLY, and one
# report costs roughly 25 minutes of sweep plus extraction. 25 companies x 3
# reports is already ~31 hours of work aimed at a 24-hour window, so the
# tenants at the back of the list would have silently received nothing — no
# error, no report, no signals, and no second chance until September.
#
# 35 days covers the longest possible gap between two monthly fire instants,
# so a company stays due from its fire instant until the next one. The
# question the tick effectively asks each hour is the durable one — "does this
# company have this month's report yet?" — and anything missed on the fire day
# is simply picked up the next day, or the day after. The reports-row ledger
# is what makes a wide window safe: it can catch up, but it can never
# double-fire, because a saved row for this cycle ends it.
REPORT_DUE_WINDOW = timedelta(days=35)

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
    """
    skill: str
    question: str
    label: str
    runner: Callable[[dict], dict | None]


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


def due_specs(
    company_id: str,
    now: datetime,
    tz: ZoneInfo,
    schedule: dict,
) -> list[ReportSpec]:
    """The specs due for this company right now.

    `schedule` is the company's resolved brief schedule dict (weekday, hour,
    minute, frequency, anchor) — the FREQUENCY IS OVERRIDDEN to monthly here,
    deliberately: a company on a daily or weekly brief cadence still gets its
    reports once a month, on the first configured weekday.

    Due = we are at or past this month's fire instant AND this company has no
    saved report for this cycle AND no attempt was spent in the last
    RETRY_BACKOFF.

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
        if not should_run_brief(
            now, tz, last_saved,
            weekday=schedule.get("weekday", 0),
            hour=schedule.get("hour", 6),
            minute=schedule.get("minute", 0),
            frequency=FREQ_MONTHLY,
            window=REPORT_DUE_WINDOW,
        ):
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

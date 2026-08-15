"""Monthly scheduled intelligence reports — run, save into the library, announce.

The intelligence reports (competitive-intelligence-review today; market
intelligence and 3P feedback follow) have always been PULL: someone remembers
to ask, a multi-minute web sweep runs, the answer lands in that chat and
nowhere else. This module makes them PUSH on a monthly cadence: once a month,
per company, each registered report runs headlessly, the finished document is
saved into the `reports` artifacts library, and a short ready-ping (Slack +
email, the brief's channels) announces it with a link — so the report exists
whether or not anyone remembered to ask for it.

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
  DELIVERY  — the brief's fan-outs: per-user Slack connections and the
              company's email recipients, via the same gates
              (`email_enabled`, explicit recipients, Resend key).

Two ledgers, for two different failure costs:

  DURABLE   — the `reports` row itself. A saved scheduled report (matched by
              company + skill + the canonical `question` marker) IS the
              "already ran this cycle" record, so a process restart can never
              double-send a month's report. No new table, no migration.
  IN-MEMORY — `_last_attempt`, marked BEFORE the runner spends anything. A
              degraded run (web search down, synthesis error) produces no
              reports row, and without this guard the tick would retry the
              paid sweep every tick for the whole due window. One attempt
              per cycle per process; a restart earns exactly one retry.

The due window is 24h (vs the brief's 1h): a monthly artifact arriving hours
late beats not arriving until next month, and the durable ledger makes the
wide window safe — it can catch up, never double-fire.

Delivery is best-effort by contract (the report is already saved; a delivery
failure costs the announcement, not the artifact). A company with no
competitor set / profile degrades inside the engine to a plain chat message,
which carries no `_skill` marker — those runs save nothing and announce
nothing, by design: an apology is not a monthly report.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from app.brief_schedule import FREQ_MONTHLY, should_run_brief

logger = logging.getLogger(__name__)

# How wide a window after the monthly fire instant still counts as due. The
# brief's 1h window exists to hit an exact delivery time; a monthly report
# only has to arrive that day, and the durable reports-row ledger prevents a
# double-run however wide this is.
REPORT_DUE_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class ReportSpec:
    """One scheduled monthly report.

    `question` doubles as the DURABLE LEDGER MARKER: it is stored on the
    saved reports row, and `latest_report_at(skill=..., question=...)`
    matching on it is how a scheduled row is told apart from the same skill
    run by a human in chat (whose row carries their own words). Changing it
    orphans the ledger for one cycle — treat it as an identifier, not copy.

    `runner` takes the company row and returns the engine's Ask-shaped
    payload (or None). A payload is accepted as a real report only when its
    `_skill` equals `skill` — the engines mark success that way and their
    degraded plain-text apologies don't.
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

# The monthly roster. Market-intelligence and 3P-feedback reports join here
# as their engines land — the tick, ledgers, and delivery already handle N.
MONTHLY_REPORT_SPECS: tuple[ReportSpec, ...] = (CIR_SPEC,)


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

    Due = inside the 24h window after this month's fire instant, AND no
    scheduled report saved for this cycle (durable ledger), AND no attempt
    already made this cycle by this process (paid-retry guard).
    """
    from app import db

    due: list[ReportSpec] = []
    for spec in MONTHLY_REPORT_SPECS:
        last_saved = _parse_created_at(
            db.latest_report_at(
                company_id, skill=spec.skill, question=spec.question,
            )
        )
        attempted = _last_attempt.get((company_id, spec.skill))
        last_run = max(
            (d for d in (last_saved, attempted) if d is not None),
            default=None,
        )
        if should_run_brief(
            now, tz, last_run,
            weekday=schedule.get("weekday", 0),
            hour=schedule.get("hour", 6),
            minute=schedule.get("minute", 0),
            frequency=FREQ_MONTHLY,
            window=REPORT_DUE_WINDOW,
        ):
            due.append(spec)
    return due


def run_and_deliver(company: dict, spec: ReportSpec,
                    now: datetime | None = None) -> int | None:
    """Run one scheduled report for one company: generate → save → announce.

    Returns the saved report id, or None when the run degraded (no profile,
    no competitor set, web search down — the engine's plain-message cases) or
    the save yielded no row. Marks the attempt ledger FIRST so a failure
    can't be re-bought this cycle. Delivery failures are logged and swallowed
    — the artifact is already in the library.
    """
    company_id = company["id"]
    now = now or datetime.now(timezone.utc)
    _last_attempt[(company_id, spec.skill)] = now

    payload = spec.runner(company)
    if not isinstance(payload, dict) or payload.get("_skill") != spec.skill:
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
    # per-tenant one.
    title = f"{spec.label} · {now.strftime('%B %Y')}"
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
        _announce(company_id, spec)
    except Exception:  # noqa: BLE001 — the artifact is saved; announcement is extra
        logger.exception(
            "monthly-reports: announcement failed for %s / %s",
            company_id, spec.skill,
        )
    return report_id


def _announce(company_id: str, spec: ReportSpec) -> None:
    """Push the short ready-ping (Slack + email). Mirrors the brief's
    ready-ping split: static copy, a CTA into the app, per-channel gates
    owned by the delivery modules. Best-effort per channel; only programming
    errors escape (the caller logs them)."""
    from app.synthesis.delivery import deliver_ping_to_slack, report_ping_slack_blocks
    from app.synthesis.email_delivery import deliver_report_ready_to_email

    text, blocks = report_ping_slack_blocks(spec.label)
    slack = deliver_ping_to_slack(company_id, text=text, blocks=blocks)
    if not slack.get("delivered") and slack.get("reason") not in (
        "slack_not_connected", "no_channel_configured"
    ):
        logger.warning("monthly-reports slack ping: %s", slack)

    email = deliver_report_ready_to_email(company_id, report_label=spec.label)
    if not email.get("delivered") and email.get("reason") not in (
        "email_disabled", "no_recipients", "resend_not_configured"
    ):
        logger.warning("monthly-reports email ping: %s", email)

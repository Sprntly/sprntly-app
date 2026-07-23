"""Onboarding drip / nudge emails (v0 checklist 2.1).

A recurring sequence of onboarding emails sent to newly-joined company
members on a cadence (default: day-1 / day-3 / day-7 after they join).
The scheduler (app/scheduler.py) runs `run_drip_cycle` periodically; this
module owns the cadence definition, the per-step copy, and the Resend send
path.

Design mirrors app/team_email.py:
  - Resolve config (settings / supabase client) at call time so the test
    suite's config reload + monkeypatched client win.
  - Best-effort: a send failure is caught and surfaced as a bool; one
    recipient failing never aborts the cycle.

Email transport is Resend (env RESEND_API_KEY) called over httpx (already a
dependency — no new package). When RESEND_API_KEY is unset the send is a
no-op that returns False; the scheduler still records the step as "skipped"
so flipping the key on later does not retro-blast historical steps.

User-facing copy says "company" (never "dataset"), per the product naming
rule.
"""
from __future__ import annotations

import html as html_mod
import logging
from dataclasses import dataclass

import httpx

from app import config as config_mod

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
_HTTP_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class DripStep:
    """One step of the onboarding drip sequence.

    `key`        — stable id persisted in drip_email_sends.step_key.
    `day_offset` — send once the member is at least this many days old.
    `subject` / `body_text` — rendered with the recipient/company context.
    """

    key: str
    day_offset: int
    subject: str
    body_text: str


# Default cadence. Overridable per-company via
# companies.notification_settings.drip (see resolve_cadence) and globally via
# DRIP_CADENCE_DAYS (e.g. "1,3,7"). Subjects/bodies use {company} / {name}
# placeholders filled by render_step. Copy uses "company", never "dataset".
DEFAULT_CADENCE: tuple[DripStep, ...] = (
    DripStep(
        key="day_1",
        day_offset=1,
        subject="Welcome to Sprntly — let's set up {company}",
        body_text=(
            "Hi {name},\n\n"
            "Welcome to Sprntly! You're one step away from turning {company}'s "
            "product signals into a weekly brief.\n\n"
            "To get the most out of Sprntly, connect your first data source "
            "(Slack, Linear, Zendesk, or Amplitude) so we can start building "
            "your knowledge graph.\n\n"
            "— The Sprntly team"
        ),
    ),
    DripStep(
        key="day_3",
        day_offset=3,
        subject="Get your first brief for {company}",
        body_text=(
            "Hi {name},\n\n"
            "Once {company} has a connected source, Sprntly generates a weekly "
            "brief that surfaces the highest-leverage product opportunities.\n\n"
            "Haven't connected a source yet? It takes about a minute and "
            "everything updates automatically after that.\n\n"
            "— The Sprntly team"
        ),
    ),
    DripStep(
        key="day_7",
        day_offset=7,
        subject="Your first week with Sprntly at {company}",
        body_text=(
            "Hi {name},\n\n"
            "It's been a week since {company} joined Sprntly. Have you tried "
            "asking the agent a question or drilling into an insight into a PRD?\n\n"
            "Reply to this email if there's anything we can help you ship faster.\n\n"
            "— The Sprntly team"
        ),
    ),
)


def _from_address() -> str:
    """The From: header for drip emails. Overridable via DRIP_FROM_EMAIL;
    defaults to the onboarding sender on the Resend-verified mail.sprntly.ai
    domain — the API key is scoped to that domain, so a bare-sprntly.ai
    sender is rejected with a 403. Resolved at call time so test config
    reloads apply."""
    return getattr(config_mod.settings, "drip_from_email", "") or (
        "Sprntly <onboarding@mail.sprntly.ai>"
    )


def resolve_cadence(notification_settings: dict | None) -> list[DripStep]:
    """Resolve the drip cadence for a company.

    Resolution order (highest precedence first):
      1. Per-company override: notification_settings["drip"]["cadence"] — a
         list of {key, day_offset, subject, body_text}. Partial entries fall
         back to the matching DEFAULT_CADENCE step by key (or day_offset →
         "day_N") for any missing field, so a company can retune only the
         days/copy it cares about.
      2. Global day offsets: settings.drip_cadence_days (e.g. "1,3,7") —
         reshapes DEFAULT_CADENCE day offsets while keeping default copy.
      3. DEFAULT_CADENCE.

    A company can disable drips entirely with
    notification_settings["drip"]["enabled"] = false → returns []."""
    ns = notification_settings or {}
    drip_cfg = ns.get("drip") if isinstance(ns, dict) else None
    drip_cfg = drip_cfg if isinstance(drip_cfg, dict) else {}

    if drip_cfg.get("enabled") is False:
        return []

    # 1. Explicit per-company cadence list.
    raw = drip_cfg.get("cadence")
    if isinstance(raw, list) and raw:
        defaults_by_key = {s.key: s for s in DEFAULT_CADENCE}
        steps: list[DripStep] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            day_offset = entry.get("day_offset")
            key = entry.get("key") or (
                f"day_{day_offset}" if day_offset is not None else None
            )
            if key is None or day_offset is None:
                continue
            base = defaults_by_key.get(key)
            steps.append(
                DripStep(
                    key=str(key),
                    day_offset=int(day_offset),
                    subject=entry.get("subject")
                    or (base.subject if base else "An update for {company}"),
                    body_text=entry.get("body_text")
                    or (base.body_text if base else "Hi {name},\n\n— The Sprntly team"),
                )
            )
        if steps:
            return steps

    # 2. Global day-offset override, default copy.
    days_raw = getattr(config_mod.settings, "drip_cadence_days", "") or ""
    days = [d.strip() for d in days_raw.split(",") if d.strip()]
    if days:
        steps = []
        for d in days:
            try:
                offset = int(d)
            except ValueError:
                continue
            key = f"day_{offset}"
            base = next((s for s in DEFAULT_CADENCE if s.key == key), None)
            base = base or DEFAULT_CADENCE[0]
            steps.append(
                DripStep(
                    key=key,
                    day_offset=offset,
                    subject=base.subject,
                    body_text=base.body_text,
                )
            )
        if steps:
            return steps

    # 3. Built-in default.
    return list(DEFAULT_CADENCE)


def render_step(step: DripStep, *, company: str, name: str) -> tuple[str, str]:
    """Fill the {company} / {name} placeholders. Returns (subject, body)."""
    safe_company = company or "your company"
    safe_name = name or "there"
    subject = step.subject.format(company=safe_company, name=safe_name)
    body = step.body_text.format(company=safe_company, name=safe_name)
    return subject, body


# Branded shell tokens — mirrors supabase/templates/*.html and the
# weekly-brief email (app/synthesis/email_delivery.py): paper background,
# white card, serif headline, green CTA.
_SERIF = "'Spectral',Georgia,'Times New Roman',serif"
_SANS = "'Inter',-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def render_drip_html(*, subject: str, body_text: str) -> str:
    """Wrap a plain-text drip body in the branded HTML shell (paper
    background, white card, serif headline, green 'Open Sprntly' CTA).
    The text body stays in the Resend payload as the plain-text fallback.

    Pure + deterministic: paragraphs are split on blank lines and escaped;
    the '— The Sprntly team' sign-off renders muted."""
    base = (config_mod.settings.frontend_url or "https://app.sprntly.ai").rstrip("/")
    paragraphs = [p.strip() for p in body_text.split("\n\n") if p.strip()]

    body_html = ""
    for p in paragraphs:
        escaped = html_mod.escape(p).replace("\n", "<br>")
        if p.startswith("—"):
            body_html += (
                f'<p style="margin:24px 0 0;font-family:{_SANS};font-size:13.5px;'
                f'line-height:1.6;color:#80838d">{escaped}</p>'
            )
        else:
            body_html += (
                f'<p style="margin:0 0 16px;font-family:{_SANS};font-size:15px;'
                f'line-height:1.65;color:#41444f">{escaped}</p>'
            )

    heading = html_mod.escape(subject)
    return f"""\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f6f5f1;margin:0;padding:0">
  <tr>
    <td align="center" style="padding:44px 16px 36px">
      <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;max-width:520px">
        <tr>
          <td align="center" style="padding:0 0 20px;font-family:{_SERIF};font-size:25px;font-weight:600;color:#15171c;letter-spacing:-0.02em">
            Sprntly<span style="color:#1a8a52">.</span>
          </td>
        </tr>
        <tr>
          <td style="background-color:#ffffff;border:1px solid #e9e8e4;border-radius:14px;padding:40px 40px 34px">
            <h1 style="margin:0 0 14px;font-family:{_SERIF};font-size:23px;line-height:1.3;font-weight:600;color:#15171c">{heading}</h1>
            {body_html}
            <table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:28px">
              <tr>
                <td align="center" style="border-radius:10px;background-color:#1a8a52">
                  <a href="{base}" style="display:inline-block;padding:13px 28px;font-family:{_SANS};font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:10px">Open Sprntly</a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding:20px 8px 0;font-family:{_SANS};font-size:12px;line-height:1.7;color:#a9aab1">
            Sprntly — product intelligence for product teams<br>
            <a href="{base}" style="color:#80838d;text-decoration:none">sprntly.ai</a>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


def send_drip_email(*, to_email: str, subject: str, body_text: str) -> bool:
    """Send one drip email via Resend. Returns True iff Resend accepted it.

    Best-effort: every failure (missing key, network, non-2xx) is caught and
    returned as False so the scheduler can record/skip and move on. Mirrors
    the team_email.send_invite_email contract."""
    api_key = getattr(config_mod.settings, "resend_api_key", "") or ""
    if not api_key:
        logger.info(
            "send_drip_email skipped: RESEND_API_KEY not configured (to=%s)",
            to_email,
        )
        return False

    try:
        resp = httpx.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": _from_address(),
                "to": [to_email],
                "subject": subject,
                "text": body_text,
                "html": render_drip_html(subject=subject, body_text=body_text),
            },
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Resend drip send failed for %s: %s %s",
                to_email, resp.status_code, resp.text[:200],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("Resend drip send raised for %s: %s", to_email, exc)
        return False


def run_drip_cycle() -> dict:
    """One pass of the onboarding drip scheduler.

    For every active company, resolve its cadence, find members who have
    crossed each step's day_offset and haven't received that step yet, send
    the email, and record the step in drip_email_sends. Per-company and
    per-recipient error isolation: a raise for one tenant/member is logged
    and the loop continues.

    Returns a small summary dict (sent / skipped / steps_considered) for
    logging + tests. Safe to call repeatedly — already-sent steps are
    filtered out by drip_email_sends.

    Gated by settings.drip_emails_enabled at the scheduler level (see
    app/scheduler.py); callable directly in tests regardless of that flag.
    """
    # Imported here (not at module load) so the test config reload + the
    # fake-supabase monkeypatch are in effect before db helpers resolve a
    # client, exactly like team_email/scheduler do.
    from app.db import drip as drip_db
    from app.db.companies import list_companies

    summary = {"companies": 0, "sent": 0, "skipped": 0, "steps_considered": 0}

    try:
        companies = list_companies() or []
    except Exception:
        logger.exception("drip-cycle: failed to list companies")
        return summary

    for company in companies:
        company_id = company.get("id")
        if not company_id:
            continue
        summary["companies"] += 1
        company_name = company.get("display_name") or company.get("slug") or ""
        try:
            notif = drip_db.get_notification_settings(company_id)
            cadence = resolve_cadence(notif)
            if not cadence:
                continue
            members = drip_db.list_members_with_email(company_id)
            already = drip_db.sent_steps_for_company(company_id)
        except Exception:
            logger.exception(
                "drip-cycle: setup failed for company %s", company_id
            )
            continue

        for member in members:
            user_id = member.get("user_id")
            email = (member.get("email") or "").strip()
            age_days = member.get("age_days")
            if not user_id or not email or age_days is None:
                continue
            name = member.get("name") or ""
            for step in cadence:
                if age_days < step.day_offset:
                    continue
                if (user_id, step.key) in already:
                    continue
                summary["steps_considered"] += 1
                subject, body = render_step(
                    step, company=company_name, name=name
                )
                ok = send_drip_email(
                    to_email=email, subject=subject, body_text=body
                )
                status = "sent" if ok else "skipped"
                try:
                    drip_db.record_drip_sent(
                        company_id=company_id,
                        user_id=user_id,
                        step_key=step.key,
                        email=email,
                        status=status,
                    )
                    already.add((user_id, step.key))
                    summary[status] += 1
                except Exception:
                    logger.exception(
                        "drip-cycle: failed to record step %s for %s/%s",
                        step.key, company_id, user_id,
                    )

    logger.info(
        "drip-cycle: %d companies, %d sent, %d skipped (%d steps considered)",
        summary["companies"], summary["sent"], summary["skipped"],
        summary["steps_considered"],
    )
    return summary

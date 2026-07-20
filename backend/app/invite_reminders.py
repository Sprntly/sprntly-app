"""Invite reminder drip — the Day-1 / Day-3 follow-up emails.

When someone is invited to a workspace, Day 0 fires immediately at invite time
(routes/team.py → team_email.send_invite_email). This module owns the two
FOLLOW-UP nudges to an invitee who still hasn't accepted:

  - Day 1: "Your invitation from {inviter} is waiting"
  - Day 3: "Your invitation from {inviter} is expiring"
  - Day 7: "{inviter} is waiting on you to join {workspace}"

Timing rules (product spec) — each step is CHAINED off the previous SEND, not
off the invite date:
  - Day 1 target = invite created_at + 1 day.
  - Day 3 target = the Day-1 send + 3 days.
  - Day 7 target = the Day-3 send + 7 days.
  - If a target lands on a weekend it moves to the next workday (Monday). All
    dates are evaluated in UTC.

Stop conditions — a reminder is NOT sent when:
  - the invite was accepted or revoked (both DELETE the workspace_invites row,
    so it simply isn't in the pending set, and invite_reminder_sends rows
    cascade away with it),
  - the recipient is already a member of the company, or
  - the invite is past its expiry (created_at + INVITE_EXPIRY_DAYS).

Transport is Resend (env RESEND_API_KEY) over httpx, best-effort: a send that
can't go out is recorded as "skipped" (never raises, never blocks the sweep),
mirroring app/drip_email.py. Each step is recorded per invite in
invite_reminder_sends so it never double-sends.

Copy says "workspace" (the invitee's word for the company), never "dataset".
"""
from __future__ import annotations

import html as html_mod
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from app import config as config_mod

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
_HTTP_TIMEOUT_SECONDS = 10.0

# Fallbacks when a name can't be resolved (a brand-new invitee has no profile;
# an inviter profile may be missing a name).
_DEFAULT_FIRST = "there"
_DEFAULT_INVITER = "a teammate"
_DEFAULT_WORKSPACE = "your workspace"


@dataclass(frozen=True)
class ReminderStep:
    """One step of the invite reminder drip.

    `key`         — stable id persisted in invite_reminder_sends.step_key.
    `day_offset`  — days after the anchor (Day-1: after created_at; Day-3:
                    after the Day-1 send) before this step is due.
    `subject`     — subject template ({inviter_first_name}).
    `cta_label`   — the green button's label.
    `html_paras`  — prose paragraphs (before the CTA); placeholders filled +
                    HTML-escaped by render_reminder.
    `body_text`   — plain-text fallback (carries {accept_link} inline).
    """

    key: str
    day_offset: int
    subject: str
    cta_label: str
    html_paras: tuple[str, ...]
    body_text: str


STEP_DAY_1 = ReminderStep(
    key="day_1",
    day_offset=1,
    subject="Your invitation from {inviter_first_name} is waiting",
    cta_label="Accept your invitation",
    html_paras=(
        "Hi {first_name}, You were invited by {inviter_first_name} to join "
        "Sprntly where you can collaborate on PRD, tickets, prototypes. We "
        "realized you haven't joined yet.",
        "It takes under 60 seconds.",
    ),
    body_text=(
        "Hi {first_name}, You were invited by {inviter_first_name} to join "
        "Sprntly where you can collaborate on PRD, tickets, prototypes. We "
        "realized you haven't joined yet.\n\n"
        "Here's the link again: {accept_link}\n\n"
        "It takes under 60 seconds.\n\n"
        "Best,\nThe Sprntly Team"
    ),
)

STEP_DAY_3 = ReminderStep(
    key="day_3",
    day_offset=3,
    subject="Your invitation from {inviter_first_name} is expiring",
    cta_label="Accept before it expires",
    html_paras=(
        "Hi {first_name},",
        "Your invitation from {inviter_first_name} to join {workspace_name} is "
        "expiring soon.",
    ),
    body_text=(
        "Hi {first_name},\n\n"
        "Your invitation from {inviter_first_name} to join {workspace_name} is "
        "expiring soon.\n\n"
        "Accept the invitation here before it expires {accept_link}\n\n"
        "Best,\nThe Sprntly Team"
    ),
)

STEP_DAY_7 = ReminderStep(
    key="day_7",
    day_offset=7,
    subject="{inviter_first_name} is waiting on you to join {workspace_name}",
    cta_label="Set up your account",
    html_paras=(
        "Hi {first_name},",
        "Most of your team has set up their accounts on {workspace_name}. "
        "Yours is still open.",
        "Once you're in, you can work with them on PRDs, tickets, and "
        "prototypes in the same place.",
    ),
    body_text=(
        "Hi {first_name},\n\n"
        "Most of your team has set up their accounts on {workspace_name}. "
        "Yours is still open.\n\n"
        "Once you're in, you can work with them on PRDs, tickets, and "
        "prototypes in the same place.\n\n"
        "Set up your account: {accept_link}\n\n"
        "Best,\nThe Sprntly Team"
    ),
)

# Ordered — the sweep sends the earliest not-yet-sent, now-due step. Each step
# is anchored to the PREVIOUS step's send time (Day-1 to created_at); see
# _due_step.
REMINDER_STEPS: tuple[ReminderStep, ...] = (STEP_DAY_1, STEP_DAY_3, STEP_DAY_7)


# ── time helpers ────────────────────────────────────────────────────────


def _parse_ts(value) -> datetime | None:
    """Parse an ISO-8601 timestamp (with or without 'Z') to aware UTC."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def next_workday(dt: datetime) -> datetime:
    """Shift `dt` to the next workday if it lands on a weekend (UTC), keeping
    the time of day. Saturday → Monday (+2), Sunday → Monday (+1); weekdays
    unchanged. Monday=0 … Sunday=6."""
    wd = dt.weekday()
    if wd == 5:      # Saturday
        return dt + timedelta(days=2)
    if wd == 6:      # Sunday
        return dt + timedelta(days=1)
    return dt


def due_at(anchor: datetime, day_offset: int) -> datetime:
    """The send time for a step: `anchor` + day_offset days, shifted off any
    weekend to the next workday."""
    return next_workday(anchor + timedelta(days=day_offset))


# ── rendering ───────────────────────────────────────────────────────────

_SERIF = "'Spectral',Georgia,'Times New Roman',serif"
_SANS = "'Inter',-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _accept_link() -> str:
    """Where a reminder sends the invitee: the sign-in page. Acceptance is by
    email match on sign-in (there is no per-invite token) — a new invitee
    already has an auth account from the Day-0 invite, so /sign-in works for
    both new and existing invitees. Resolved at call time."""
    base = (config_mod.settings.frontend_url or "").rstrip("/") or (
        "http://localhost:3000"
    )
    return f"{base}/sign-in"


def render_reminder(
    step: ReminderStep,
    *,
    first_name: str,
    inviter_first_name: str,
    workspace_name: str,
    accept_link: str | None = None,
) -> tuple[str, str, str]:
    """Fill a step's placeholders. Returns (subject, body_text, body_html).

    Empty names degrade to friendly fallbacks so a missing profile never yields
    "Hi ,". Deterministic given the config resolved at call time."""
    ctx = {
        "first_name": (first_name or "").strip() or _DEFAULT_FIRST,
        "inviter_first_name": (inviter_first_name or "").strip() or _DEFAULT_INVITER,
        "workspace_name": (workspace_name or "").strip() or _DEFAULT_WORKSPACE,
        "accept_link": accept_link or _accept_link(),
    }
    subject = step.subject.format(**ctx)
    body_text = step.body_text.format(**ctx)
    body_html = _render_html(step, ctx)
    return subject, body_text, body_html


def _render_html(step: ReminderStep, ctx: dict) -> str:
    """Branded HTML: paper background, white card, serif headline, prose
    paragraphs, a green CTA to the accept link. Prose is filled then escaped
    (names are user data)."""
    base = (config_mod.settings.frontend_url or "").rstrip("/") or (
        "https://app.sprntly.ai"
    )
    accept = html_mod.escape(ctx["accept_link"], quote=True)
    heading = html_mod.escape(step.subject.format(**ctx))

    paras_html = ""
    for tpl in step.html_paras:
        para = html_mod.escape(tpl.format(**ctx))
        paras_html += (
            f'<p style="margin:0 0 16px;font-family:{_SANS};font-size:15px;'
            f'line-height:1.65;color:#41444f">{para}</p>'
        )

    cta_label = html_mod.escape(step.cta_label)
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
            <h1 style="margin:0 0 18px;font-family:{_SERIF};font-size:23px;line-height:1.3;font-weight:600;color:#15171c">{heading}</h1>
            {paras_html}
            <table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:24px">
              <tr>
                <td align="center" style="border-radius:10px;background-color:#1a8a52">
                  <a href="{accept}" style="display:inline-block;padding:13px 28px;font-family:{_SANS};font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:10px">{cta_label}</a>
                </td>
              </tr>
            </table>
            <p style="margin:24px 0 0;font-family:{_SANS};font-size:13px;line-height:1.6;color:#80838d">
              Or paste this link into your browser:<br>
              <a href="{accept}" style="color:#1a8a52;word-break:break-all">{accept}</a>
            </p>
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


# ── send ────────────────────────────────────────────────────────────────


def _from_address() -> str:
    """From: header. Overridable via INVITE_FROM_EMAIL; falls back to
    brief_email_from (the verified sender the Day-0 existing-user notification
    already uses)."""
    return (
        getattr(config_mod.settings, "invite_from_email", "")
        or getattr(config_mod.settings, "brief_email_from", "")
        or "Sprntly <briefs@mail.sprntly.ai>"
    )


def send_reminder_email(
    *,
    to_email: str,
    step: ReminderStep,
    first_name: str,
    inviter_first_name: str,
    workspace_name: str,
) -> bool:
    """Send one invite reminder via Resend. Returns True iff Resend accepted it.

    Best-effort: every failure (missing key, network, non-2xx) is caught and
    returned as False. Mirrors drip_email.send_drip_email."""
    api_key = getattr(config_mod.settings, "resend_api_key", "") or ""
    if not api_key:
        logger.info(
            "send_reminder_email skipped: RESEND_API_KEY not configured "
            "(to=%s, step=%s)",
            to_email, step.key,
        )
        return False

    subject, body_text, body_html = render_reminder(
        step,
        first_name=first_name,
        inviter_first_name=inviter_first_name,
        workspace_name=workspace_name,
    )
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
                "html": body_html,
            },
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Resend invite reminder failed for %s (%s): %s %s",
                to_email, step.key, resp.status_code, resp.text[:200],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "Resend invite reminder raised for %s (%s): %s",
            to_email, step.key, exc,
        )
        return False


# ── the sweep ───────────────────────────────────────────────────────────


def _due_step(
    *, created_at: datetime, sends: dict[str, str], now: datetime
) -> tuple[ReminderStep, datetime] | None:
    """The single reminder step that is due for this invite right now, or None.

    Steps are CHAINED: each is anchored to the PREVIOUS step's send time, and
    Day-1 to the invite's created_at (spec: Day 1 = invite + 1, Day 3 = Day-1
    send + 3, Day 7 = Day-3 send + 7). Walks the steps in order; the first
    not-yet-sent step is the candidate — returned only once its (weekend-
    shifted) target has passed. A prior step whose send time can't be parsed
    breaks the chain (returns None) rather than guessing an anchor."""
    anchor = created_at
    for step in REMINDER_STEPS:
        if step.key not in sends:
            target = due_at(anchor, step.day_offset)
            return (step, target) if now >= target else None
        # Already sent → its send time anchors the next step in the chain.
        sent_at = _parse_ts(sends.get(step.key))
        if sent_at is None:
            return None
        anchor = sent_at
    return None  # all steps already sent


def run_invite_reminder_cycle() -> dict:
    """One pass of the invite-reminder sweep.

    For every pending invite, find the due reminder step (respecting the
    already-member / expired stop conditions), send it, and record it. Per-
    invite error isolation. Safe to call repeatedly — sent steps are filtered
    out by invite_reminder_sends. Gated by settings.invite_reminders_enabled at
    the scheduler level; callable directly in tests regardless.

    Returns a small summary dict for logging + tests.
    """
    # Imported here (not at module load) so the test config reload + fake
    # supabase monkeypatch are in effect before db helpers resolve a client,
    # exactly like drip_email/scheduler do.
    from app.db import invite_reminders as inv_db
    from app.db.team import member_exists_for_email

    summary = {"invites": 0, "sent": 0, "skipped": 0, "steps_considered": 0}

    try:
        invites = inv_db.list_pending_invites_all_companies()
    except Exception:
        logger.exception("invite-reminder: failed to list pending invites")
        return summary
    if not invites:
        return summary

    expiry_days = getattr(config_mod.settings, "invite_expiry_days", 14) or 14
    now = datetime.now(timezone.utc)

    invite_ids = [inv.get("id") for inv in invites if inv.get("id")]
    try:
        sends_by_invite = inv_db.reminder_sends_by_invite(invite_ids)
        inviter_names = inv_db.first_names_for_user_ids(
            [inv.get("invited_by") for inv in invites]
        )
        invitee_names = inv_db.first_names_for_emails(
            [inv.get("email") for inv in invites]
        )
        workspace_names = inv_db.display_names_for_company_ids(
            [inv.get("company_id") for inv in invites]
        )
    except Exception:
        logger.exception("invite-reminder: enrichment lookup failed")
        return summary

    for inv in invites:
        invite_id = inv.get("id")
        company_id = inv.get("company_id")
        email = (inv.get("email") or "").strip()
        if not invite_id or not company_id or not email:
            continue
        summary["invites"] += 1

        try:
            # Stop: recipient already joined this company by some other path
            # (accept deletes the invite, so this catches non-invite joins).
            if member_exists_for_email(company_id=company_id, email=email):
                continue

            created = _parse_ts(inv.get("created_at"))
            if created is None:
                continue
            # Stop: past expiry — no more reminders once the invite is stale.
            if created + timedelta(days=expiry_days) < now:
                continue

            due = _due_step(
                created_at=created,
                sends=sends_by_invite.get(invite_id, {}),
                now=now,
            )
            if due is None:
                continue
            step, _target = due
            summary["steps_considered"] += 1

            ok = send_reminder_email(
                to_email=email,
                step=step,
                first_name=invitee_names.get(email.lower(), ""),
                inviter_first_name=inviter_names.get(inv.get("invited_by"), ""),
                workspace_name=workspace_names.get(company_id, ""),
            )
            status = "sent" if ok else "skipped"
            inv_db.record_reminder_sent(
                invite_id=invite_id,
                company_id=company_id,
                email=email,
                step_key=step.key,
                status=status,
            )
            summary[status] += 1
        except Exception:
            logger.exception(
                "invite-reminder: failed for invite %s", invite_id
            )
            continue

    logger.info(
        "invite-reminder cycle: %d invites, %d sent, %d skipped (%d considered)",
        summary["invites"], summary["sent"], summary["skipped"],
        summary["steps_considered"],
    )
    return summary

"""One-way transactional email nudges for task delegation.

Three siblings, one shared shape:
  - `send_followup_email` — the autonomous task follow-up sweep's
    escalation-to-assignee channel (spec §2 channel step 2, only reached
    after >=2 unanswered DM cycles). Pre-existing.
  - `send_assignment_email` — fires once per delegation, to the ASSIGNEE,
    right after a task is handed to them.
  - `send_completion_email` — fires once per completed delegation, to the
    ASSIGNER, from all three completion paths (the inbound explicit-done
    classifier, the outbound soft-done finalize, and the deterministic
    `complete_task` tool).

Copies `invite_reminders.send_reminder_email`'s transport shape verbatim
(`httpx.post(RESEND_API_URL, ...)`, `resend_api_key` from settings,
`_from_address()` falling back to `brief_email_from`, best-effort —
returns `bool`, never raises, logs `skipped` when no key). There is no
single shared Resend-send primitive in this codebase today (each drip
module — `invite_reminders.py`, `drip_email.py` — carries its own
`httpx.post`); matching that existing pattern is the DRY-consistent
choice rather than introducing a third shape. `_send_via_resend` below is
the one shared POST call all three senders route through — still local
to this module, not a codebase-wide primitive.

One-way: no inbound parsing, no reply-to expectation. Every CTA deep-links
to the recipient's own project's private (individual) chat — the real,
stable route (`web/app/lib/routes.ts::projectPath(id, {chat:"individual"})`
renders `/projects?id=<id>&chat=individual`), not a placeholder.

NAME/identifier only in every body — never the task_summary/task text
(observability/PII rule; the task content lives in-app, never in an
email a third-party transport handles). None of the three render
functions below accepts a task-text argument at all, by construction."""
from __future__ import annotations

import html as html_mod
import logging

import httpx

from app import config as config_mod

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
_HTTP_TIMEOUT_SECONDS = 10.0

_DEFAULT_FIRST = "there"

_SERIF = "'Spectral',Georgia,'Times New Roman',serif"
_SANS = "'Inter',-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _from_address() -> str:
    """From: header. Overridable via INVITE_FROM_EMAIL (the shared drip
    sender override); falls back to brief_email_from — the same verified
    `mail.sprntly.ai` sender every other drip module uses."""
    return (
        getattr(config_mod.settings, "invite_from_email", "")
        or getattr(config_mod.settings, "brief_email_from", "")
        or "Sprntly <briefs@mail.sprntly.ai>"
    )


def project_chat_link(project_id: int) -> str:
    """The CTA deep-link: the project's private individual chat. Mirrors
    `projectPath(id, {chat: "individual"})` (`web/app/lib/routes.ts`) —
    `?id=<id>&chat=individual`, resolved against `frontend_url`."""
    base = (config_mod.settings.frontend_url or "").rstrip("/") or (
        "http://localhost:3000"
    )
    return f"{base}/projects?id={project_id}&chat=individual"


def render_followup_email(*, first_name: str, project_id: int) -> tuple[str, str, str]:
    """Fill the nudge template. Returns (subject, body_text, body_html).
    NAME only — never task_summary or any chat content."""
    name = (first_name or "").strip() or _DEFAULT_FIRST
    link = project_chat_link(project_id)
    subject = "You have an update waiting in Sprntly"
    body_text = (
        f"Hi {name},\n\n"
        "You have an update waiting for you in Sprntly.\n\n"
        f"Open it here: {link}\n\n"
        "Best,\nThe Sprntly Team"
    )
    name_esc = html_mod.escape(name)
    body_html = _render_html(
        headline="You have an update waiting",
        body_html=f"Hi {name_esc}, you have an update waiting for you in Sprntly.",
        cta_text="Open Sprntly",
        link=link,
    )
    return subject, body_text, body_html


def render_assignment_email(
    *, assigner_name: str, project_id: int, project_name: str
) -> tuple[str, str, str]:
    """Fill the assignment-nudge template — locked copy. Fires once
    per delegation, to the ASSIGNEE. NAME/project only — this function
    takes no task-text argument at all, so the raw `task_summary` can
    never reach the email body."""
    assigner = (assigner_name or "").strip() or "A teammate"
    project = (project_name or "").strip() or "Sprntly"
    link = project_chat_link(project_id)
    subject = f"{assigner} assigned you a task in {project}"
    body = (
        f"{assigner} assigned you a task in {project}. Open your chat to "
        "see what they need and pick it up."
    )
    body_text = f"{body}\n\nOpen your chat: {link}\n\nBest,\nThe Sprntly Team"
    body_html = _render_html(
        headline="You've got a new task",
        body_html=(
            f"{html_mod.escape(assigner)} assigned you a task in "
            f"{html_mod.escape(project)}. Open your chat to see what they "
            "need and pick it up."
        ),
        cta_text="Open project chat",
        link=link,
    )
    return subject, body_text, body_html


def render_completion_email(
    *, assignee_name: str, project_id: int, project_name: str
) -> tuple[str, str, str]:
    """Fill the completion-notice template — locked copy. Fires once
    per completed delegation, to the ASSIGNER, from all three completion
    paths. NAME/project only — this function takes no task-text argument
    at all, so the raw `task_summary` can never reach the email body."""
    assignee = (assignee_name or "").strip() or "Someone"
    project = (project_name or "").strip() or "Sprntly"
    link = project_chat_link(project_id)
    subject = f"{assignee} completed the task you assigned in {project}"
    body = (
        f"{assignee} finished the task you assigned in {project}. Open "
        "your chat to see their update."
    )
    body_text = f"{body}\n\nOpen your chat: {link}\n\nBest,\nThe Sprntly Team"
    body_html = _render_html(
        headline="Task completed",
        body_html=(
            f"{html_mod.escape(assignee)} finished the task you assigned "
            f"in {html_mod.escape(project)}. Open your chat to see their "
            "update."
        ),
        cta_text="View in Sprntly",
        link=link,
    )
    return subject, body_text, body_html


def _render_html(*, headline: str, body_html: str, cta_text: str, link: str) -> str:
    """The shared visual shell for every task-delegation transactional
    email (paper bg, white card, Spectral serif headline, green #1a8a52
    CTA). `body_html` is pre-built, already-escaped inner HTML supplied by
    the caller (every caller in this module escapes its own dynamic
    values before interpolating them into the paragraph it passes in) —
    `headline`/`cta_text`/`link` are escaped here."""
    headline_esc = html_mod.escape(headline)
    cta_esc = html_mod.escape(cta_text)
    link_esc = html_mod.escape(link, quote=True)
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
            <h1 style="margin:0 0 18px;font-family:{_SERIF};font-size:23px;line-height:1.3;font-weight:600;color:#15171c">{headline_esc}</h1>
            <p style="margin:0 0 16px;font-family:{_SANS};font-size:15px;line-height:1.65;color:#41444f">{body_html}</p>
            <table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:24px">
              <tr>
                <td align="center" style="border-radius:10px;background-color:#1a8a52">
                  <a href="{link_esc}" style="display:inline-block;padding:13px 28px;font-family:{_SANS};font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:10px">{cta_esc}</a>
                </td>
              </tr>
            </table>
            <p style="margin:24px 0 0;font-family:{_SANS};font-size:13px;line-height:1.6;color:#80838d">
              Or paste this link into your browser:<br>
              <a href="{link_esc}" style="color:#1a8a52;word-break:break-all">{link_esc}</a>
            </p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


def _send_via_resend(
    *, to_email: str, subject: str, body_text: str, body_html: str, log_label: str
) -> bool:
    """The one shared Resend POST all three senders below route through.
    Returns True iff Resend accepted it. Best-effort: every failure
    (network, non-2xx) is caught and returned as False, never raised."""
    api_key = getattr(config_mod.settings, "resend_api_key", "") or ""
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
                "Resend task-%s email failed for %s: %s %s",
                log_label, to_email, resp.status_code, resp.text[:200],
            )
            return False
        message_id = None
        try:
            message_id = resp.json().get("id")
        except Exception:  # noqa: BLE001 — a success is still a success without an id
            message_id = None
        logger.info(
            "resend_sent label=%s to=%s message_id=%s", log_label, to_email, message_id,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("Resend task-%s email raised for %s: %s", log_label, to_email, exc)
        return False


def send_followup_email(*, to_email: str, first_name: str, project_id: int) -> bool:
    """Send one follow-up nudge via Resend. Returns True iff Resend accepted
    it. Best-effort: every failure (missing key, network, non-2xx) is
    caught and returned as False, never raised — mirrors
    `invite_reminders.send_reminder_email`."""
    api_key = getattr(config_mod.settings, "resend_api_key", "") or ""
    if not api_key:
        logger.info(
            "send_followup_email skipped: RESEND_API_KEY not configured (to=%s)",
            to_email,
        )
        return False

    subject, body_text, body_html = render_followup_email(
        first_name=first_name, project_id=project_id
    )
    return _send_via_resend(
        to_email=to_email, subject=subject, body_text=body_text, body_html=body_html,
        log_label="followup",
    )


def send_assignment_email(
    *, to_email: str, assigner_name: str, project_id: int, project_name: str
) -> bool:
    """Send one assignment nudge via Resend to the ASSIGNEE. Returns
    True iff Resend accepted it. Best-effort, same posture as
    `send_followup_email`."""
    api_key = getattr(config_mod.settings, "resend_api_key", "") or ""
    if not api_key:
        logger.info(
            "send_assignment_email skipped: RESEND_API_KEY not configured (to=%s)",
            to_email,
        )
        return False

    subject, body_text, body_html = render_assignment_email(
        assigner_name=assigner_name, project_id=project_id, project_name=project_name
    )
    return _send_via_resend(
        to_email=to_email, subject=subject, body_text=body_text, body_html=body_html,
        log_label="assignment",
    )


def send_completion_email(
    *, to_email: str, assignee_name: str, project_id: int, project_name: str
) -> bool:
    """Send one completion notice via Resend to the ASSIGNER. Returns
    True iff Resend accepted it. Best-effort, same posture as
    `send_followup_email`."""
    api_key = getattr(config_mod.settings, "resend_api_key", "") or ""
    if not api_key:
        logger.info(
            "send_completion_email skipped: RESEND_API_KEY not configured (to=%s)",
            to_email,
        )
        return False

    subject, body_text, body_html = render_completion_email(
        assignee_name=assignee_name, project_id=project_id, project_name=project_name
    )
    return _send_via_resend(
        to_email=to_email, subject=subject, body_text=body_text, body_html=body_html,
        log_label="completion",
    )

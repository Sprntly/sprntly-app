"""In-app feedback / feature-request route (June 20 #13 + #A).

Users open a lightweight form from the left nav (next to sign-out): a free-text
message + an optional type (Bug / Feature request / New connector request). On
submit the frontend POSTs here. We:

  1. store the submission in the `feedback` table (app/db/feedback.py), and
  2. email it to the team via Resend (app/synthesis/email_delivery._send_via_resend).

Storing is the source of truth; the email is best-effort context for the team
and never blocks (or fails) the request. Recipient resolution: FEEDBACK_ALERT_EMAIL
wins; if unset we fall back to SIGNIN_MONITOR_ALERT_EMAIL. Both empty (or no
RESEND_API_KEY) ⇒ email is a clean no-op (logged), submission is still stored.

Tenancy: `require_company` resolves the active company + user from the JWT, so
every submission is attributed to its submitter for follow-up.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, field_validator

from app.auth import CompanyContext, require_company
from app.config import settings
from app.db.feedback import FEEDBACK_TYPES, record_feedback

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/feedback", tags=["feedback"])

_TYPE_LABEL = {
    "bug": "Bug",
    "feature_request": "Feature request",
    "connector_request": "New connector request",
    "other": "General feedback",
}


class FeedbackIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)
    type: str = "other"

    @field_validator("message")
    @classmethod
    def _strip_message(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be empty")
        return v

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        v = (v or "other").strip()
        if v not in FEEDBACK_TYPES:
            raise ValueError(f"type must be one of {', '.join(FEEDBACK_TYPES)}")
        return v


def _resolve_recipient() -> str:
    """Team address for feedback email. FEEDBACK_ALERT_EMAIL wins; else fall
    back to the existing ops alert address. Empty ⇒ no email."""
    return (settings.feedback_alert_email or settings.signin_monitor_alert_email or "").strip()


def _email_feedback(*, type_label: str, message: str, company_id: str,
                    user_email: str | None) -> bool:
    """Best-effort: email the submission to the team. Returns True iff sent.
    Never raises — a failed/disabled send never breaks the submission."""
    api_key = settings.resend_api_key
    to = _resolve_recipient()
    if not api_key or not to:
        logger.info("feedback email skipped (no RESEND_API_KEY / recipient); stored only")
        return False
    from app.synthesis.email_delivery import _send_via_resend

    who = user_email or "unknown user"
    subject = f"[Sprntly feedback] {type_label} from {who}"
    text = (
        f"Type: {type_label}\n"
        f"From: {who}\n"
        f"Company: {company_id}\n\n"
        f"{message}\n"
    )
    html_body = (
        f"<p><strong>Type:</strong> {type_label}<br/>"
        f"<strong>From:</strong> {who}<br/>"
        f"<strong>Company:</strong> {company_id}</p>"
        f"<hr/><p style='white-space:pre-wrap'>{message}</p>"
    )
    try:
        _send_via_resend(api_key, to=to, subject=subject,
                         html_body=html_body, text_body=text)
        logger.info("feedback email sent to %s", to)
        return True
    except Exception:  # noqa: BLE001 — email never breaks the submission
        logger.exception("feedback email delivery failed")
        return False


@router.post("", status_code=status.HTTP_201_CREATED)
def post_feedback(
    body: FeedbackIn,
    company: CompanyContext = Depends(require_company),
):
    row = record_feedback(
        company_id=company.company_id,
        user_id=company.user_id,
        user_email=company.user_email,
        type=body.type,
        message=body.message,
    )
    email_sent = _email_feedback(
        type_label=_TYPE_LABEL.get(body.type, body.type),
        message=body.message,
        company_id=company.company_id,
        user_email=company.user_email,
    )
    return {"id": row.get("id"), "type": body.type, "email_sent": email_sent}

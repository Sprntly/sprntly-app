"""Feedback / feature-request persistence (June 20 #13 + #A).

Stores in-app feedback submissions (a short message + a type) into the
`feedback` table, capturing the submitting user + company for context. The
route layer (app/routes/feedback.py) calls `record_feedback` and then emails
the submission to the team via Resend.

All access is via require_client() (service-role; the route runs server-side).
"""
from __future__ import annotations

import logging
import uuid

from app.db.client import require_client, retry_on_disconnect, utc_now

logger = logging.getLogger(__name__)

# Allowed submission types. Mirrors the CHECK constraint in
# supabase/migrations/20260622130000_feedback.sql.
FEEDBACK_TYPES = ("bug", "feature_request", "connector_request", "other")


@retry_on_disconnect
def record_feedback(
    *,
    company_id: str | None,
    user_id: str | None,
    user_email: str | None,
    type: str,
    message: str,
) -> dict:
    """Insert one feedback row and return it.

    `type` is assumed already validated by the route (one of FEEDBACK_TYPES);
    we default to 'other' defensively so a bad value never trips the DB CHECK.
    """
    if type not in FEEDBACK_TYPES:
        type = "other"
    client = require_client()
    row = {
        "id": str(uuid.uuid4()),
        "company_id": company_id,
        "user_id": user_id,
        "user_email": user_email,
        "type": type,
        "message": message,
        "created_at": utc_now(),
    }
    result = client.table("feedback").insert(row).execute()
    inserted = (result.data or [row])[0]
    logger.info(
        "feedback recorded: id=%s type=%s company=%s",
        inserted.get("id"), type, company_id,
    )
    return inserted

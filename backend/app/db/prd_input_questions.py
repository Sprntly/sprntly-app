"""DB helpers for `prd_input_questions` — the structured, answerable form of a
PRD's "User input needed" section.

The prd-author skill writes that section as decorative HTML inside the PRD
document; a lightweight extraction pass (app.prd_questions.extract_input_questions)
lifts each item into a row here so the PRD's chat can render it as a message with
answer buttons, and answering one can patch only the affected part of the PRD.

Rows are keyed to `prd_id` (a regenerated PRD is a new prds row → a fresh set of
questions, mirroring the whole PRD family). These helpers are SYNCHRONOUS and use
`require_client()` + `utc_now()`, mirroring db/prd_patches.py — supabase-py is a
synchronous client and the async routes call these sync helpers directly.

Observability: log identifiers only — never `prompt`/`answer` (they can embed PRD
product detail), mirroring the prd_patches Rule #24 discipline.
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)

_TABLE = "prd_input_questions"
_LEGAL_TAG = {"escalate", "need"}
_LEGAL_STATUS = {"pending", "answered", "dismissed"}


def replace_questions(prd_id: int, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace ALL questions for a PRD with a fresh set (delete-then-insert).

    Idempotent by construction: re-running extraction for the same prd_id yields
    the same rows. Each incoming question is `{tag, prompt, owner?, options?}`;
    `ordinal` is assigned from list order. Questions with an empty/whitespace
    prompt are skipped (an extraction hiccup must not persist a blank item).
    Returns the inserted rows (empty list when nothing valid was supplied).
    """
    c = require_client()
    # Clear any prior extraction for this PRD first so a re-run never duplicates.
    c.table(_TABLE).delete().eq("prd_id", prd_id).execute()

    rows: list[dict[str, Any]] = []
    for i, q in enumerate(questions or []):
        prompt = (q.get("prompt") or "").strip()
        if not prompt:
            continue
        tag = q.get("tag") if q.get("tag") in _LEGAL_TAG else "need"
        options = q.get("options") or []
        # Only ESCALATE (product-decision) items carry answer buttons; a NEED item
        # is missing data → free text, so its options are always empty.
        if tag != "escalate":
            options = []
        rows.append({
            "prd_id": prd_id,
            "ordinal": i,
            "tag": tag,
            "prompt": prompt,
            "owner": (q.get("owner") or None),
            "options": options,
            "status": "pending",
        })
    if not rows:
        logger.info("prd_input_questions_replaced prd_id=%s count=0", prd_id)
        return []
    resp = c.table(_TABLE).insert(rows).execute()
    logger.info("prd_input_questions_replaced prd_id=%s count=%s", prd_id, len(rows))
    return resp.data or []


def list_questions(prd_id: int) -> list[dict[str, Any]]:
    """All questions for a PRD, ordinal-ascending (pending + resolved).

    The frontend renders pending ones as chat messages and can show resolved ones
    as answered; returning the whole set keeps the chat consistent on reopen.
    """
    c = require_client()
    resp = (
        c.table(_TABLE)
        .select("*")
        .eq("prd_id", prd_id)
        .order("ordinal", desc=False)
        .execute()
    )
    return resp.data or []


def get_question(question_id: int) -> dict[str, Any] | None:
    """One question row by id, or None."""
    c = require_client()
    resp = c.table(_TABLE).select("*").eq("id", question_id).limit(1).execute()
    return resp.data[0] if resp.data else None


def answer_question(
    question_id: int, answer: str, answered_by: str | None = None
) -> dict[str, Any] | None:
    """Flip a question to status='answered', recording the answer + who answered.

    Returns the updated row, or None if it does not exist. Idempotent: answering
    an already-answered question overwrites the answer (the last resolution wins,
    consistent with the user re-choosing before the PRD edit lands).
    """
    c = require_client()
    c.table(_TABLE).update({
        "status": "answered",
        "answer": answer,
        "answered_by": answered_by,
        "answered_at": utc_now(),
    }).eq("id", question_id).execute()
    resp = c.table(_TABLE).select("*").eq("id", question_id).limit(1).execute()
    if not resp.data:
        return None
    logger.info("prd_input_question_answered question_id=%s", question_id)
    return resp.data[0]

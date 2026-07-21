"""Internal transcript viewer — read customer chats to QA the AI's answers.

A read-only window onto `conversations` + `conversation_turns` across ALL
tenants, so the team can skim what people asked and check the AI came back with
something sensible. It replaces the "email ourselves the transcripts every
morning" idea: same information, filterable, and no customer conversation
content sitting in anyone's inbox.

Auth is a single shared access code (TRANSCRIPTS_ACCESS_CODE_HASH, argon2id)
→ a 12h JWT with aud=sprntly-transcripts (see app.auth.require_transcripts).
It is deliberately NOT the staff credential: reading transcripts shouldn't also
grant the ability to edit entitlements. Unset env ⇒ every route, login
included, 404s — the surface is invisible, not merely forbidden.

  POST /v1/transcripts/login                  → access-code login → JWT
  GET  /v1/transcripts/companies              → companies that have chats (filter list)
  GET  /v1/transcripts/conversations          → filter by date range + company
  GET  /v1/transcripts/conversations/{id}     → one conversation + all its turns

Scope note: these routes intentionally bypass the per-user ownership gate that
`/v1/conversations` enforces (`_get_owned_conversation`). That gate is what
keeps teammates out of each other's chats; this surface exists precisely to
read across tenants, which is why it sits behind its own credential.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import (
    TRANSCRIPTS_TOKEN_TTL_HOURS,
    make_transcripts_token,
    require_transcripts,
    transcripts_surface_enabled,
    verify_transcripts_code,
)
from app.db.client import require_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/transcripts", tags=["transcripts"])

# Page size for the conversation list. The viewer is for skimming a day or two
# at a time, not bulk export, so this stays modest and the date filter does the
# real narrowing.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


class TranscriptsLoginIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=1000)


@router.post("/login")
def transcripts_login(body: TranscriptsLoginIn):
    """Shared-access-code login. 404 when the surface is disabled (an
    unconfigured box shows nothing); 401 for a wrong code."""
    if not transcripts_surface_enabled():
        raise HTTPException(404, "Not found")
    if not verify_transcripts_code(body.code):
        raise HTTPException(401, "Invalid access code")
    return {
        "token": make_transcripts_token(),
        "token_type": "bearer",
        "expires_in": TRANSCRIPTS_TOKEN_TTL_HOURS * 3600,
    }


def _company_names(company_ids: list[str]) -> dict[str, str]:
    """company_id → display_name for the ids given. Best-effort: a missing
    name renders as the raw id rather than 500ing the list."""
    ids = [cid for cid in {c for c in company_ids if c}]
    if not ids:
        return {}
    try:
        rows = (
            require_client()
            .table("companies")
            .select("id, display_name, slug")
            .in_("id", ids)
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001 — labels are display-only
        logger.warning("transcripts: company name lookup failed", exc_info=True)
        return {}
    return {
        r["id"]: (r.get("display_name") or r.get("slug") or r["id"]) for r in rows
    }


def _user_labels(user_ids: list[str]) -> dict[str, str]:
    """user_id → best human label (full name, else email). Best-effort."""
    ids = [uid for uid in {u for u in user_ids if u}]
    if not ids:
        return {}
    try:
        rows = (
            require_client()
            .table("profiles")
            .select("id, full_name, first_name, last_name, email")
            .in_("id", ids)
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001 — labels are display-only
        logger.warning("transcripts: profile lookup failed", exc_info=True)
        return {}
    out: dict[str, str] = {}
    for r in rows:
        name = (r.get("full_name") or "").strip()
        if not name:
            name = " ".join(
                p for p in [(r.get("first_name") or "").strip(),
                            (r.get("last_name") or "").strip()] if p
            )
        out[r["id"]] = name or (r.get("email") or "").strip() or r["id"]
    return out


def _turn_counts(conversation_ids: list[int]) -> dict[int, int]:
    """conversation_id → number of turns. Counted in-process (the fake test
    client has no group-by), which is fine at page scale."""
    if not conversation_ids:
        return {}
    try:
        rows = (
            require_client()
            .table("conversation_turns")
            .select("conversation_id")
            .in_("conversation_id", conversation_ids)
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001 — counts are display-only
        logger.warning("transcripts: turn count lookup failed", exc_info=True)
        return {}
    counts: dict[int, int] = {}
    for r in rows:
        cid = r.get("conversation_id")
        if cid is not None:
            counts[cid] = counts.get(cid, 0) + 1
    return counts


@router.get("/companies")
def list_transcript_companies(_: dict = Depends(require_transcripts)):
    """Companies that have at least one conversation, for the filter dropdown.

    Derived from `conversations` rather than `companies` so the dropdown only
    offers tenants that actually have something to read.
    """
    c = require_client()
    rows = (
        c.table("conversations").select("company_id").execute().data or []
    )
    ids = sorted({r["company_id"] for r in rows if r.get("company_id")})
    names = _company_names(ids)
    companies = [
        {"id": cid, "display_name": names.get(cid, cid)} for cid in ids
    ]
    companies.sort(key=lambda x: x["display_name"].lower())
    return {"companies": companies}


@router.get("/conversations")
def list_transcript_conversations(
    _: dict = Depends(require_transcripts),
    date_from: str | None = Query(
        default=None, description="Inclusive start date, YYYY-MM-DD (UTC)."
    ),
    date_to: str | None = Query(
        default=None, description="Inclusive end date, YYYY-MM-DD (UTC)."
    ),
    company_id: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
):
    """Conversations across all tenants, newest first, filtered by date and
    company. Returns previews and counts only — turn content comes from the
    detail route when a row is opened."""
    c = require_client()
    q = c.table("conversations").select("*")

    if company_id:
        q = q.eq("company_id", company_id)
    if date_from:
        q = q.gte("created_at", f"{_parse_date(date_from, 'date_from')}T00:00:00Z")
    if date_to:
        # `date_to` is INCLUSIVE, so filter strictly below the following
        # midnight — otherwise a conversation at 14:00 on the end date would
        # be dropped by a naive `<= {date}T00:00:00`.
        end = _parse_date(date_to, "date_to") + timedelta(days=1)
        q = q.lt("created_at", f"{end.isoformat()}T00:00:00Z")

    # Fetch one extra to tell the caller there's more without a count query.
    rows = (
        q.order("created_at", desc=True).limit(limit + 1).execute().data or []
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    names = _company_names([r.get("company_id") for r in rows])
    users = _user_labels([r.get("user_id") for r in rows])
    counts = _turn_counts([r["id"] for r in rows])

    return {
        "conversations": [_summary(r, names, users, counts) for r in rows],
        "has_more": has_more,
    }


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as e:
        raise HTTPException(422, f"{field} must be YYYY-MM-DD") from e


def _summary(
    row: dict[str, Any],
    names: dict[str, str],
    users: dict[str, str],
    counts: dict[int, int],
) -> dict[str, Any]:
    cid = row.get("company_id")
    uid = row.get("user_id")
    return {
        "id": row["id"],
        "company_id": cid,
        "company_name": names.get(cid, cid),
        "user_id": uid,
        # Legacy rows predate user stamping; label them rather than blank.
        "user_label": users.get(uid) if uid else None,
        "title": row.get("title") or "",
        "preview": row.get("preview") or "",
        "agent_type": row.get("agent_type") or "",
        "prd_id": row.get("prd_id"),
        "turn_count": counts.get(row["id"], 0),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get("/conversations/{conversation_id}")
def get_transcript(
    conversation_id: int,
    _: dict = Depends(require_transcripts),
):
    """One conversation plus every turn, oldest first — the sidebar payload.

    Turns are individual messages (`role` = user | assistant), not user+AI
    pairs; the reader renders them in `created_at` order.
    """
    c = require_client()
    conv = (
        c.table("conversations")
        .select("*")
        .eq("id", conversation_id)
        .limit(1)
        .execute()
        .data
    )
    if not conv:
        raise HTTPException(404, "Conversation not found")
    row = conv[0]

    turns = (
        c.table("conversation_turns")
        .select("*")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
        .data
        or []
    )

    names = _company_names([row.get("company_id")])
    users = _user_labels([row.get("user_id")])
    summary = _summary(row, names, users, {row["id"]: len(turns)})

    return {
        "conversation": {
            **summary,
            # The legacy single-shot shape: some older rows carry the whole
            # exchange here and have no turn rows at all. The reader falls
            # back to these when `turns` is empty.
            "query": row.get("query") or "",
            "reply": row.get("reply") or "",
        },
        "turns": [
            {
                "id": t.get("id"),
                "role": t.get("role") or "user",
                "content": t.get("content") or "",
                "created_at": t.get("created_at"),
            }
            for t in turns
        ],
    }

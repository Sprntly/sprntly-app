"""reports — durable skill-generated HTML report documents.

One row per captured report (voice-of-customer-report,
competitive-intelligence-review, public-feedback-report, …). The chat answer is
already rendered by the time capture runs, so `save_report` is BEST-EFFORT by
contract: a failed save costs the artifacts library one entry, it must never
break the answer the user is reading. Callers swallow its exceptions.

Reads filter by `company_id` (all workspaces in a company share one report
library — mirrors db/custom_skills.py); `workspace_id` records which workspace
generated the report and may be NULL on paths without workspace context.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)

# The share vocabulary is deliberately the SAME as the prototype surface's — one
# concept, one implementation. `hash_share_passcode` / `verify_share_passcode` are
# pure argon2id helpers with no prototype coupling, so they are reused rather than
# reimplemented (a second password-hashing path is exactly the kind of thing that
# drifts).
from app.db.prototypes import hash_share_passcode  # noqa: E402

SHARE_MODES = frozenset({"private", "public", "passcode"})

# Columns a report read returns. `share_passcode_hash` is deliberately ABSENT:
# nothing outside the verification path may see it, so it is fetched explicitly
# where it is needed rather than riding along on every read.
_READ_COLUMNS = (
    "id, company_id, workspace_id, skill, title, html, question, "
    "ask_id, conversation_id, prd_id, created_at, share_mode, share_token, shared_at"
)

# Columns a LISTING returns. `html` is deliberately absent — a list of N reports
# must not carry N full documents (same posture as db/artifacts.py); the body is
# fetched by id once the reader opens one.
_LIST_COLUMNS = (
    "id, skill, title, question, created_at, conversation_id, prd_id, share_mode"
)

# A chat thread accumulates reports one ask at a time, so this ceiling is far
# above any real thread — it exists so a pathological row count can't turn one
# panel open into an unbounded response.
_CONVERSATION_LIST_CAP = 100


@retry_on_disconnect
def save_report(
    company_id: str,
    *,
    skill: str,
    title: str,
    html: str,
    question: str = "",
    workspace_id: str | None = None,
    ask_id: int | None = None,
    conversation_id: int | None = None,
    prd_id: int | None = None,
) -> int | None:
    """Persist a captured report and return its id (None when the insert yields
    no row).

    `conversation_id` / `prd_id` are the report's ATTACHMENT: pass whatever the
    originating ask carried and nothing more — a present id means the report
    hangs off that chat room or PRD, absent means it stands alone.
    """
    c = require_client()
    resp = c.table("reports").insert({
        "company_id": company_id,
        "workspace_id": workspace_id,
        "skill": skill,
        "title": title or "",
        "html": html or "",
        "question": question or "",
        "ask_id": ask_id,
        "conversation_id": conversation_id,
        "prd_id": prd_id,
    }).execute()
    return resp.data[0]["id"] if resp.data else None


@retry_on_disconnect
def get_report(report_id: int, company_id: str) -> dict | None:
    """One report by id, scoped to its company — the caller's tenant gate.

    Returns None for a missing row AND for a row owned by another company, so
    the route 404s either way and never discloses cross-tenant existence.
    """
    c = require_client()
    resp = (
        c.table("reports")
        .select(_READ_COLUMNS)
        .eq("id", report_id)
        .eq("company_id", company_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


@retry_on_disconnect
def list_reports_for_conversation(conversation_id: int, company_id: str) -> list[dict]:
    """Every report captured in one chat thread, newest first.

    This is what the chat panel's Reports tab reads: a report's ATTACHMENT
    (`conversation_id`) is what makes it belong to a thread, and the column is
    indexed for exactly this query (`reports_conversation_idx`).

    Company-filtered like every other read here, so a conversation id guessed
    from another tenant returns an empty list rather than their reports. Bodies
    are omitted (see `_LIST_COLUMNS`).
    """
    c = require_client()
    resp = (
        c.table("reports")
        .select(_LIST_COLUMNS)
        .eq("conversation_id", conversation_id)
        .eq("company_id", company_id)
        .order("id", desc=True)
        .limit(_CONVERSATION_LIST_CAP)
        .execute()
    )
    return resp.data or []


@retry_on_disconnect
def set_report_share_config(
    *,
    report_id: int,
    company_id: str,
    share_mode: str,
    passcode: str | None = None,
) -> dict | None:
    """Set a report's share mode; return the updated row (None if not owned).

    Mirrors db/prototypes.set_share_config, including its STATIC-URL invariant:

      - 'private'  → mode=private; share_token PRESERVED; passcode hash cleared
      - 'public'   → mode=public;  token minted only if none exists; hash cleared
      - 'passcode' → mode=passcode; token minted only if none exists;
                     hash=argon2(passcode)

    Preserving the token across public→private→public means a link already handed
    to someone keeps working when sharing is paused and resumed, instead of
    silently becoming a different URL. Leaving passcode mode clears the hash so a
    later re-share never retains a stale gate.

    Company-filtered: a report belonging to another company returns None (the
    route turns that into the same 404 a missing id gets).
    """
    if share_mode not in SHARE_MODES:
        raise ValueError(f"set_report_share_config: unknown share_mode={share_mode!r}")
    if share_mode == "passcode" and not passcode:
        raise ValueError("set_report_share_config: passcode-mode requires a passcode")

    row = get_report(report_id, company_id)
    if not row:
        return None

    patch: dict[str, Any] = {
        "share_mode": share_mode,
        "shared_at": datetime.now(timezone.utc).isoformat(),
    }
    if share_mode == "private":
        patch["share_passcode_hash"] = None
    else:
        patch["share_token"] = row.get("share_token") or str(uuid.uuid4())
        patch["share_passcode_hash"] = (
            hash_share_passcode(passcode) if share_mode == "passcode" else None
        )

    c = require_client()
    (
        c.table("reports")
        .update(patch)
        .eq("id", report_id)
        .eq("company_id", company_id)
        .execute()
    )
    # Log the mode and id only — NEVER the passcode plaintext and never the
    # share_token (the token IS the access primitive, so logging it is equivalent
    # to logging the share URL).
    logger.info("report_share_configured report_id=%s mode=%s", report_id, share_mode)
    return get_report(report_id, company_id)


@retry_on_disconnect
def find_report_by_share_token(token: str) -> dict | None:
    """Resolve a share token → report row, ACROSS tenants.

    This is the one legitimate cross-company read in this module: the token is the
    access primitive for the unauthenticated viewer, so there is no company to
    filter by (mirrors db/prototypes.find_prototype_by_share_token).

    Returns None for an unknown token AND for a report whose sharing is off, so a
    revoked link is indistinguishable from one that never existed. Includes
    `share_passcode_hash` because the passcode gate verifies against it — the
    caller must never return this row to a client verbatim.
    """
    if not token:
        return None
    c = require_client()
    resp = (
        c.table("reports")
        .select(_READ_COLUMNS + ", share_passcode_hash")
        .eq("share_token", token)
        .limit(1)
        .execute()
    )
    row = resp.data[0] if resp.data else None
    if not row or row.get("share_mode") not in ("public", "passcode"):
        return None
    return row

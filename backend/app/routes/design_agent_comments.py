"""Design-Agent comment routes — the first domain slice split out of the
oversized `design_agent.py` route module.

`design_agent.py` had grown to accumulate every Design-Agent HTTP surface on one
`APIRouter`. This module carries the most self-contained slice of that surface —
the anchored-comment routes — onto its OWN `APIRouter` at the SAME
`/v1/design-agent` prefix, registered alongside the primary router in `main.py`.
It is the first of several planned domain extractions; every other route/domain
(generation, locate, iterate, manual-edit, PRD-patches, export, share, events)
stays in `design_agent.py` for now.

Routes served here (identical paths, methods, and behavior as before the split):

    POST   /v1/design-agent/{prototype_id}/comments              (authed — create)
    GET    /v1/design-agent/{prototype_id}/comments              (authed — list)
    PATCH  /v1/design-agent/{prototype_id}/comments/{cid}/resolve (authed — resolve)
    DELETE /v1/design-agent/{prototype_id}/comments/{cid}        (authed — delete)
    POST   /v1/design-agent/by-token/{token}/comments           (public, no auth — create)
    GET    /v1/design-agent/by-token/{token}/comments           (public, no auth — list)
    POST   /v1/design-agent/{prototype_id}/clarify-comment       (authed — LLM pre-flight)

TWO ROUTERS AT ONE PREFIX — route-ordering safety. A bundle-proxy router already
mounts at this same prefix and is registered before the primary router because
one of ITS paths would otherwise be shadowed by the primary router's
single-segment `GET /{prototype_id}` catch-all. This module follows the same
placement (registered immediately before the primary router in `main.py`) purely
for consistency with that established convention — it is NOT load-bearing here:
every route in this module is a 2-or-3-segment path
(`/{prototype_id}/comments`, `/by-token/{token}/comments`,
`/{prototype_id}/clarify-comment`, …), and FastAPI/Starlette only matches a route
when the method AND the path-segment shape match, so a 1-segment catch-all can
never capture these regardless of registration order. That safety is asserted
empirically by a reachability test, not just by this note.

MINIMUM-SURFACE DEPENDENCIES. `_require_feature_enabled`, `_share_token_hash`, and
the `PUBLIC_COMMENT_LIMITER` instance all remain in `design_agent.py` and are
imported here rather than relocated or duplicated — they are shared with routes
that stay in that module (and `design_agent.py` never imports back from this
module, so the dependency is one-directional with no circular-import risk).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from app.auth import CompanyContext, require_company
from app.design_agent.client import get_design_agent_client
from app.design_agent.csrf import require_same_origin  # server-side CSRF/Origin gate
from app.db.prototype_comments import (
    delete_comment,
    insert_comment,
    list_comments,
    resolve_comment,
)
from app.db.prototypes import find_prototype_by_share_token, get_prototype
from app.routes.design_agent import (
    PUBLIC_COMMENT_LIMITER,
    _require_feature_enabled,
    _share_token_hash,
)

# Log under the ORIGINAL route module's name, not __name__. These handlers used
# to live in `app.routes.design_agent`; keeping their log records attributed to
# that logger means the physical file split changes nothing observable for log
# aggregation or the existing log-hygiene assertions — a pure relocation.
logger = logging.getLogger("app.routes.design_agent")

router = APIRouter(prefix="/v1/design-agent", tags=["design-agent"])


# ─── Anchored comments ──────────────────────────────────────────────────────
#
# Anyone with the share URL can comment; spec §4 Stage 2 splits write access
# by surface, not by capability gate — internal users act through the authed app
# routes, external viewers through the public `/p/<token>` variant. This block
# mounts the HTTP surface over the `db.prototype_comments` helpers:
#
#   POST  /{prototype_id}/comments              (authed — create)
#   GET   /{prototype_id}/comments              (authed — list, all statuses)
#   PATCH /{prototype_id}/comments/{cid}/resolve (authed — resolve)
#   POST  /by-token/{token}/comments            (public, NO auth — create)
#   GET   /by-token/{token}/comments            (public, NO auth — list)
#
# The internal routes reuse the authed-route gates verbatim (feature flag +
# require_app_session + workspace filter via get_prototype). The public routes
# mirror get_by_token's posture exactly: the token IS the access primitive,
# so NO auth dependency and NO session workspace claim — workspace_id is taken
# from the RESOLVED prototype row. Per spec §4 Stage 2 ("only internal users with
# credentials can act"), external viewers create + read only; there is NO public
# resolve route. Public-write rate limiting is OUT of scope here — it lands
# later.


class CommentCreate(BaseModel):
    # None = a general (unpinned) comment -- prototype-level feedback with no
    # element anchor. A pinned/anchored comment always sends a non-empty string;
    # an empty STRING is rejected below (distinct invalid input, not "no anchor").
    anchor_id: str | None = Field(default=None, max_length=64)
    body: str = Field(..., min_length=1, max_length=4000)

    @field_validator("anchor_id")
    @classmethod
    def _anchor_id_not_empty_string(cls, v: str | None) -> str | None:
        if v == "":
            raise ValueError("anchor_id must be omitted/null (general comment) or a non-empty string")
        return v
    pin_x_pct: float | None = Field(default=None, ge=0, le=100)
    pin_y_pct: float | None = Field(default=None, ge=0, le=100)
    resolved_anchor_id: str | None = Field(default=None, max_length=64)
    # Public-surface only: the anonymous viewer's self-supplied display name,
    # mapped onto the EXISTING `author` column (no new column / no migration).
    # The authed route ignores it — internal authors come from the session
    # identity. Length-capped so it can't be used as an oversized log/store vector.
    viewer_name: str | None = Field(default=None, max_length=80)


class CommentOut(BaseModel):
    id: int
    anchor_id: str | None = None
    body: str
    author: str
    status: str           # 'open' | 'resolved' | 'orphaned'
    created_at: str
    resolved_at: str | None = None
    pin_x_pct: float | None = None
    pin_y_pct: float | None = None
    resolved_anchor_id: str | None = None


def _comment_to_out(row: dict[str, Any]) -> dict[str, Any]:
    """Project a DB row to the CommentOut shape (ISO-string timestamps).

    Timestamps are stringified defensively: Postgres returns timestamptz objects
    via supabase, the SQLite fake returns TEXT — `str()` normalises both to the
    ISO string CommentOut expects without leaking driver-specific types."""
    return {
        "id": row["id"],
        "anchor_id": row.get("anchor_id"),
        "body": row["body"],
        "author": row["author"],
        "status": row["status"],
        "created_at": str(row["created_at"]),
        "resolved_at": str(row["resolved_at"]) if row.get("resolved_at") else None,
        "pin_x_pct": row.get("pin_x_pct"),
        "pin_y_pct": row.get("pin_y_pct"),
        "resolved_anchor_id": row.get("resolved_anchor_id"),
    }


# ─── Internal (authed) comment routes ─────────────────────────────────────


@router.post(
    "/{prototype_id}/comments",
    response_model=CommentOut,
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
def post_comment(
    prototype_id: int,
    body: CommentCreate,
    company: CompanyContext = Depends(require_company),
) -> CommentOut:
    """Create a comment as an internal user. Workspace-filtered: 404 if the
    prototype is not in the caller's workspace (cross-tenant existence is not
    disclosed). Attributed to the internal author label."""
    _require_feature_enabled()
    workspace_id = company.company_id
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not proto:
        raise HTTPException(status_code=404, detail="Prototype not found")
    row = insert_comment(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        anchor_id=body.anchor_id,
        body=body.body,
        author=company.user_name or company.user_email or company.user_id,
        user_id=company.user_id,
        pin_x_pct=body.pin_x_pct,
        pin_y_pct=body.pin_y_pct,
        resolved_anchor_id=body.resolved_anchor_id,
    )
    return CommentOut(**_comment_to_out(row))


@router.get("/{prototype_id}/comments", response_model=list[CommentOut])
def get_comments(
    prototype_id: int,
    company: CompanyContext = Depends(require_company),
) -> list[CommentOut]:
    """List every comment for a prototype (all statuses, created_at-ascending).
    Workspace-filtered: 404 if the prototype is not in the caller's workspace."""
    _require_feature_enabled()
    workspace_id = company.company_id
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not proto:
        raise HTTPException(status_code=404, detail="Prototype not found")
    return [
        CommentOut(**_comment_to_out(r))
        for r in list_comments(prototype_id=prototype_id, workspace_id=workspace_id)
    ]


@router.patch(
    "/{prototype_id}/comments/{cid}/resolve",
    response_model=CommentOut,
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
def patch_resolve_comment(
    prototype_id: int,
    cid: int,
    company: CompanyContext = Depends(require_company),
) -> CommentOut:
    """Resolve a comment (internal only — external viewers cannot resolve, per
    spec §4 Stage 2 'only internal users with credentials can act'). Returns 404
    when the comment is not in the caller's workspace OR belongs to a different
    prototype than the one in the path (no cross-prototype resolve)."""
    _require_feature_enabled()
    workspace_id = company.company_id
    row = resolve_comment(comment_id=cid, workspace_id=workspace_id)
    if not row or row["prototype_id"] != prototype_id:
        raise HTTPException(status_code=404, detail="Comment not found")
    return CommentOut(**_comment_to_out(row))


@router.delete("/{prototype_id}/comments/{cid}", status_code=204, dependencies=[Depends(require_same_origin)])
def delete_comment_route(
    prototype_id: int,
    cid: int,
    company: CompanyContext = Depends(require_company),
) -> Response:
    _require_feature_enabled()
    workspace_id = company.company_id
    delete_comment(comment_id=cid, workspace_id=workspace_id)
    return Response(status_code=204)


# ─── Public (token-resolved, NO auth) comment routes ──────────────────────
#
# "Anyone with the URL can comment." The token IS the access primitive.
# Workspace is taken from the RESOLVED prototype row (not a session claim).
# External viewers may CREATE + READ comments but NOT resolve them. The
# resolution posture matches get_by_token exactly (404 for missing / private /
# not-ready) so brute-force scanning discloses nothing.


@router.post("/by-token/{token}/comments", response_model=CommentOut)
def post_comment_public(token: str, body: CommentCreate, request: Request) -> CommentOut:
    """Public comment write. Resolves token → prototype; rejects when the
    prototype is private or not ready (404, matching get_by_token's posture).
    The comment is attributed to the viewer's self-supplied name (or
    "Anonymous"), and the workspace_id is taken from the resolved row — never a
    session claim.

    Anonymous public comment WRITES are ENABLED: the share token IS the access
    primitive (anyone with the URL can comment). This is an unauthenticated
    write endpoint by design; the abuse controls are unchanged and load-bearing:
      - the feature-flag gate (`_require_feature_enabled`) — invisible when off;
      - the resolution 404-posture (missing / private / not-ready all 404,
        indistinguishable from each other, so brute-force scanning discloses
        nothing);
      - the per-IP `PUBLIC_COMMENT_LIMITER` (10/hour/IP), mounted after the 404
        resolution and before the write;
      - log hygiene: the token is hashed (never raw) and neither the comment body
        nor the viewer name (PII) is ever logged."""
    _require_feature_enabled()
    proto = find_prototype_by_share_token(token)
    if not proto or proto.get("share_mode") == "private" or proto.get("status") != "ready":
        raise HTTPException(status_code=404, detail="Not found")
    # Per-IP public-comment rate limit (10/hour/IP). Mounted AFTER the 404
    # resolution (a private/missing/not-ready prototype 404s first, so the limiter
    # never discloses a hidden prototype's existence) and BEFORE insert_comment (the
    # spend-meaningful write). Keyed by client IP — the same machine can spam across
    # many tokens, so per-IP, not per-token, is the spam boundary. Null-guard mirrors
    # the passcode route's `request.client.host if request.client else "0.0.0.0"`.
    client_ip = request.client.host if request.client else "0.0.0.0"
    if not PUBLIC_COMMENT_LIMITER.check(client_ip):
        retry_after = PUBLIC_COMMENT_LIMITER.retry_after(client_ip)
        logger.info(
            "public_comment_rate_limited ip_present=%s retry_after_seconds=%s",
            request.client is not None, retry_after,
        )
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limit", "retry_after_seconds": retry_after},
        )
    PUBLIC_COMMENT_LIMITER.register(client_ip)
    # Viewer-supplied display name → the existing `author` column. Trimmed and
    # falling back to "Anonymous" for blank/omitted names. NEVER logged (PII).
    author = (body.viewer_name or "").strip() or "Anonymous"
    row = insert_comment(
        prototype_id=proto["id"],
        workspace_id=proto["workspace_id"],   # from the resolved row, not a session
        anchor_id=body.anchor_id,
        body=body.body,
        author=author,
        pin_x_pct=body.pin_x_pct,
        pin_y_pct=body.pin_y_pct,
        resolved_anchor_id=body.resolved_anchor_id,
    )
    # Token hashed, never raw (the token is the access primitive); no
    # comment body in the log line (PII). insert_comment emits its own
    # `comment_created` line; this adds the public-surface correlation marker.
    logger.info(
        "comment_created_public token_hash=%s prototype_id=%s comment_id=%s",
        _share_token_hash(token), proto["id"], row["id"],
    )
    return CommentOut(**_comment_to_out(row))


@router.get("/by-token/{token}/comments", response_model=list[CommentOut])
def get_comments_public(token: str) -> list[CommentOut]:
    """Public comment read. All viewers can read existing comments (spec §4).
    Same 404 posture as the public write for missing / private / not-ready."""
    _require_feature_enabled()
    proto = find_prototype_by_share_token(token)
    if not proto or proto.get("share_mode") == "private" or proto.get("status") != "ready":
        raise HTTPException(status_code=404, detail="Not found")
    return [
        CommentOut(**_comment_to_out(r))
        for r in list_comments(prototype_id=proto["id"], workspace_id=proto["workspace_id"])
    ]


# ─── Comment clarify ────────────────────────────────────────────────────────────
#
# POST /{prototype_id}/clarify-comment
#
# Lightweight LLM call (claude-haiku-4-5-20251001, max_tokens=200) that
# generates a single clarifying question for a comment body before the Apply
# flow commits an iterate. Backed by the shared `get_design_agent_client()`
# factory. Uses the design agent API key.
# Not in the iterate queue — this is a synchronous pre-flight, fast enough
# (<1s on Haiku) to sit in the request path without a background task.


class ClarifyCommentRequest(BaseModel):
    comment_body: str = Field(..., min_length=1, max_length=4000)


class ClarifyCommentResponse(BaseModel):
    question: str


@router.post("/{prototype_id}/clarify-comment", response_model=ClarifyCommentResponse, dependencies=[Depends(require_same_origin)])
def clarify_comment_route(
    prototype_id: int,
    body: ClarifyCommentRequest,
    company: CompanyContext = Depends(require_company),
) -> ClarifyCommentResponse:
    """Return a single clarifying question for a comment before Apply is confirmed.

    Workspace-isolated (require_company) and feature-flag-gated. Uses the shared
    Design Agent Anthropic client with a lightweight Haiku call so the
    dialog loads in <1s without touching the iterate queue.
    """
    _require_feature_enabled()
    workspace_id = company.company_id
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if proto is None:
        raise HTTPException(status_code=404, detail="Prototype not found")
    client = get_design_agent_client()
    FALLBACK_QUESTION = "Looks good — any additional context to add?"
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            timeout=10.0,
            messages=[{
                "role": "user",
                "content": (
                    f'You are reviewing a design feedback comment about to be applied to a UI prototype.\n'
                    f'Comment: "{body.comment_body}"\n'
                    f'Ask exactly ONE brief, specific clarifying question to understand the designer\'s intent before applying this change. '
                    f'Be concise (one sentence max). Do not explain yourself, just ask the question.'
                ),
            }],
        )
        text_blocks = [b for b in (msg.content or []) if hasattr(b, "text")]
        question = text_blocks[0].text.strip() if text_blocks else FALLBACK_QUESTION
    except Exception:
        question = FALLBACK_QUESTION
    return ClarifyCommentResponse(question=question)

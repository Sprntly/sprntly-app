"""Share an artifact into the company's Slack, from the chat — the I/O half.

POST /v1/share/slack/preview — resolve WHAT and WHERE, compose the message,
                               and hand it back WITHOUT posting anything.
POST /v1/share/slack/send    — re-resolve, re-compose, and post it.

TWO ROUTES RATHER THAN ONE, and the split is the feature. Posting into a team
channel is public and effectively irreversible: the wrong channel or a
half-formed note is not something the user can take back. So nothing is ever
posted on the strength of a model's reading of a sentence — `/preview` shows
the exact message and the exact channel, and `/send` runs only after the person
has looked at both.

THE CLIENT NEVER SUPPLIES THE MESSAGE BODY. `/send` takes the same target and
channel identifiers `/preview` took and rebuilds the text from the database, so
(a) what was approved is what goes out, and (b) the browser cannot hand our bot
token an arbitrary body to post into the company's Slack. The one thing it does
carry over is `note` — those are the user's own words, which they may have
edited in the preview, and they are posted verbatim.

Tenancy is enforced here, not in `app.slack_share` (which is pure): every
target read goes through the same company-scoped getter its own surface uses,
and a foreign id 404s rather than 403s, so a share request cannot be used to
probe another tenant's library.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import slack_share
from app.auth import CompanyContext
from app.config import settings
from app.connectors import slack_oauth
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json
from app.deps.ownership import require_owned_prd
from app.entitlements import require_agents_module

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/share/slack", tags=["share"])


def _app_base_url() -> str:
    """The origin every share link is built against — the same
    `frontend_url or app.sprntly.ai` pattern every other outbound link in the
    product uses (brief_nudge, delivery, design_agent.notify)."""
    return (settings.frontend_url or "https://app.sprntly.ai").rstrip("/")


# ── Slack connection ─────────────────────────────────────────────────────────


def _bot_token(company: CompanyContext) -> str:
    """The bot token to post as: THIS user's own Slack install, falling back to
    the company's shared connection.

    Exactly the resolution order `GET /v1/connectors/slack/channels` uses, and
    deliberately so — the channels the user picked from must be the channels the
    post is sent through. Resolving the picker against one token and the post
    against another is how you get a valid-looking channel id that the sending
    token cannot see.
    """
    from app import db
    from app.connectors.slack_company import CompanySlackError, company_slack_token

    row = db.get_slack_connection(company.company_id, company.user_id)
    if row:
        try:
            token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
        except (TokenEncryptionError, json.JSONDecodeError) as e:
            logger.error("slack token unreadable for user %s: %s", company.user_id, e)
            raise HTTPException(500, "Slack token is unreadable — reconnect Slack.") from e
        token = token_json.get("access_token")
        if token:
            return token
    try:
        resolved = company_slack_token(company.company_id)
    except CompanySlackError as e:
        raise HTTPException(500, str(e)) from e
    if not resolved:
        raise HTTPException(404, "Slack is not connected")
    return resolved[0]


# ── target resolution ────────────────────────────────────────────────────────


class ShareTargetIn(BaseModel):
    """WHICH artifact. Two ways in, and the caller may send both.

    The EXPLICIT ids are the client's own context — the PRD on the tab, the
    ticket set or report this thread produced — and they win, because "share
    this PRD" means the one in front of the user, not the best title match for
    a phrase they never typed.

    `artifact_type` / `artifact_query` are the planner's reading of a message
    that named a document ("share the checkout PRD"), used only when no
    explicit id is given.
    """

    prd_id: Optional[int] = Field(default=None, ge=1)
    report_id: Optional[int] = Field(default=None, ge=1)
    ticket_set_id: Optional[int] = Field(default=None, ge=1)
    custom_artifact_id: Optional[int] = Field(default=None, ge=1)
    artifact_type: Optional[str] = Field(default=None, max_length=64)
    artifact_query: Optional[str] = Field(default=None, max_length=500)


def _resolve_explicit(company: CompanyContext, body: ShareTargetIn) -> Optional[dict]:
    """The artifact named by an explicit id, tenant-checked, or None when the
    caller gave none.

    Each branch uses the SAME company-scoped getter that artifact's own surface
    uses, so this route grants no read it did not already have. A missing or
    foreign id is a 404 in every branch — never a 403, so a foreign tenant
    cannot tell "exists but not yours" from "doesn't exist".
    """
    base = _app_base_url()

    if body.prd_id is not None:
        row = require_owned_prd(body.prd_id, company.company_id, company.workspace_id)
        return {
            "type": "prd",
            "id": body.prd_id,
            "title": (row.get("title") or "").strip() or "Untitled",
            "kind_label": slack_share.kind_label("prd"),
            "url": slack_share.share_link(
                base, artifact_type="prd", artifact_id=body.prd_id
            ),
        }

    if body.report_id is not None:
        from app.db.reports import get_report

        row = get_report(body.report_id, company.company_id)
        if not row:
            raise HTTPException(404, "Report not found")
        return {
            "type": "report",
            "id": body.report_id,
            "title": (row.get("title") or "").strip() or "Report",
            "kind_label": slack_share.kind_label("report"),
            "url": slack_share.share_link(
                base, artifact_type="report", artifact_id=body.report_id
            ),
        }

    if body.ticket_set_id is not None:
        from app.db.ticket_sets import get_set

        row = get_set(company.company_id, body.ticket_set_id)
        if not row:
            raise HTTPException(404, "Ticket set not found")
        return {
            "type": "ticket_set",
            "id": body.ticket_set_id,
            "title": (row.get("title") or "").strip() or "Tickets",
            "kind_label": slack_share.kind_label("ticket_set"),
            "url": slack_share.share_link(
                base, artifact_type="ticket_set", artifact_id=body.ticket_set_id
            ),
        }

    if body.custom_artifact_id is not None:
        from app.db.custom_artifacts import get_artifact

        row = get_artifact(company.company_id, body.custom_artifact_id)
        if not row:
            raise HTTPException(404, "Document not found")
        return {
            "type": "custom_artifact",
            "id": body.custom_artifact_id,
            "title": (row.get("title") or "").strip() or "Document",
            "kind_label": slack_share.kind_label("custom_artifact"),
            "url": slack_share.share_link(
                base,
                artifact_type="custom_artifact",
                artifact_id=body.custom_artifact_id,
            ),
        }

    return None


def _resolve_by_query(company: CompanyContext, body: ShareTargetIn) -> dict:
    """The artifact named by a PHRASE, matched against the caller's own library.

    `list_artifacts_for_company` IS the tenant scope (dataset + company id),
    the same read the Artifacts screen and the chat's listing use, so a phrase
    can only ever resolve to something this caller already owns.
    """
    from app.routes.chat import _dataset_for

    # Imported rather than re-derived: this is tenant-scoping logic (the
    # workspace→dataset resolution, with its documented legacy fallback), and a
    # second copy of it is a second place for the scope to drift wrong.
    dataset = _dataset_for(company)
    if not dataset:
        return {"status": "not_found", "artifact": None, "candidates": []}
    try:
        from app.db.artifacts import list_artifacts_for_company

        items = list_artifacts_for_company(
            dataset=dataset, company_id=company.company_id
        )
    except Exception:  # noqa: BLE001 — a lookup hiccup asks, it never posts
        logger.exception("share target lookup failed; reporting not_found")
        return {"status": "not_found", "artifact": None, "candidates": []}

    return slack_share.resolve_share_target(
        items,
        artifact_type=body.artifact_type,
        artifact_query=body.artifact_query,
        base_url=_app_base_url(),
    )


def _summary_for(company: CompanyContext, artifact: dict) -> str:
    """The teaser line under the document link.

    Best-effort by contract: a body that will not load costs the message its
    summary, never the share itself. Each read is company-scoped exactly like
    the resolution above — this runs AFTER ownership is established, and re-uses
    the same getters rather than widening anything.
    """
    kind = artifact.get("type")
    art_id = artifact.get("id")
    try:
        if kind == "prd":
            from app.db.prds import get_prd_rendered

            row = get_prd_rendered(art_id) or {}
            return slack_share.summarize(row.get("payload_md"))

        if kind == "report":
            from app.db.reports import get_report

            row = get_report(art_id, company.company_id) or {}
            return slack_share.summarize(row.get("html"))

        if kind == "custom_artifact":
            from app.db.custom_artifacts import get_artifact

            row = get_artifact(company.company_id, art_id) or {}
            return slack_share.summarize(row.get("body_html"))

        if kind == "ticket_set":
            from app.db.ticket_sets import get_set

            row = get_set(company.company_id, art_id) or {}
            stories = row.get("stories")
            if isinstance(stories, str):
                stories = json.loads(stories)
            if not isinstance(stories, list) or not stories:
                return ""
            # A ticket set has no prose to excerpt, so the summary IS the
            # inventory: how many, and the first few by name. That is what a
            # reader needs to decide whether to open it.
            titles = [
                str((s or {}).get("title") or "").strip()
                for s in stories[:3]
                if isinstance(s, dict) and (s or {}).get("title")
            ]
            count = len(stories)
            head = f"{count} ticket{'s' if count != 1 else ''}"
            return f"{head}: {', '.join(titles)}" if titles else head
    except Exception:  # noqa: BLE001 — a missing summary is not a failed share
        logger.warning("share summary failed for %s %s", kind, art_id, exc_info=True)
    return ""


# ── preview ──────────────────────────────────────────────────────────────────


class SharePreviewIn(ShareTargetIn):
    #: The channel the user named, without the '#'. None → the picker.
    channel: Optional[str] = Field(default=None, max_length=80)
    #: Their own words to post alongside the document.
    note: Optional[str] = Field(default=None, max_length=slack_share.NOTE_CHARS)


@router.post("/preview")
def preview_share(
    body: SharePreviewIn,
    company: CompanyContext = Depends(require_agents_module),
):
    """Everything needed to ask "post this?" — and nothing posted.

    Returns {status, target, candidates, channel, channels, message, warning}
    where `status` is the thing the client branches on:

      * "ready"            — target and channel both resolved; show the preview
      * "needs_target"     — nothing matched; ask which document
      * "ambiguous_target" — several matched; `candidates` are the choices
      * "unsupported_type" — they named a kind that cannot be shared
      * "needs_channel"    — target is set, channel is not; `channels` are the
                             choices (this is also the plain "share this on
                             slack" case, where no channel was ever named)
      * "blocked"          — a private channel Sprntly cannot join; `warning`
                             says what to do about it

    Read-only in every branch, including the Slack call: `conversations.list`
    reads, it does not write.
    """
    base = _app_base_url()

    # ── WHAT ──
    artifact = _resolve_explicit(company, body)
    if artifact is None:
        resolved = _resolve_by_query(company, body)
        if resolved["status"] == "unsupported_type":
            return {
                "status": "unsupported_type",
                "named_type": resolved.get("named_type"),
                "target": None,
                "candidates": [],
            }
        if resolved["status"] == "ambiguous":
            return {
                "status": "ambiguous_target",
                "target": None,
                "candidates": resolved["candidates"],
            }
        if resolved["status"] != "resolved":
            return {"status": "needs_target", "target": None, "candidates": []}
        artifact = resolved["artifact"]

    # ── WHERE ──
    token = _bot_token(company)
    channels = slack_oauth.list_channels(token)
    matched = slack_share.match_channel(channels, body.channel)

    # The message is composed even when the channel is still open, so the
    # picker shows what is about to be sent rather than asking the user to
    # choose a destination for something they cannot see.
    summary = _summary_for(company, artifact)
    text, blocks = slack_share.compose_share(
        note=body.note,
        artifact=artifact,
        summary=summary,
        sharer_name=company.user_name,
    )
    # `summary` is handed back ALONGSIDE the composed message, not only inside
    # it, because the card splits the preview into the part the user can still
    # edit (their note) and the part Sprntly asserts (the document, its teaser,
    # its link). Rendering the fixed part from these fields keeps the preview
    # truthful while the note is being typed — echoing `message.text` instead
    # would show a stale composition the moment a character changed.
    message = {"text": text, "blocks": blocks, "summary": summary}

    if matched["status"] != "resolved":
        return {
            "status": "needs_channel",
            "target": artifact,
            "channel": None,
            # `not_found` hands back every channel rather than none: the user
            # named something that does not exist, and a picker is a better
            # answer than an error they have to retype their way out of.
            "channels": matched["candidates"],
            "channel_query": (body.channel or "").strip() or None,
            "channel_status": matched["status"],
            "message": message,
        }

    channel = matched["channel"]
    warning = slack_share.channel_warning(channel)
    if slack_share.channel_is_blocked(channel):
        return {
            "status": "blocked",
            "target": artifact,
            "channel": channel,
            "channels": [],
            "message": message,
            "warning": warning,
        }
    return {
        "status": "ready",
        "target": artifact,
        "channel": channel,
        "channels": [],
        "message": message,
        "warning": warning,
    }


# ── send ─────────────────────────────────────────────────────────────────────


class ShareSendIn(ShareTargetIn):
    #: The channel ID the user confirmed — an id, not a name, because this is
    #: the value the preview handed back. A name here would mean re-running the
    #: match at send time and posting to whatever it resolved to THEN.
    channel_id: str = Field(..., min_length=1, max_length=64)
    note: Optional[str] = Field(default=None, max_length=slack_share.NOTE_CHARS)


@router.post("/send")
def send_share(
    body: ShareSendIn,
    company: CompanyContext = Depends(require_agents_module),
):
    """Post the share. The only route here that writes anything.

    The target is re-resolved and the message re-composed from the database
    rather than taken from the request — see the module docstring. The user's
    `note` is the sole piece of caller-supplied text that reaches Slack.

    `auto_join=True` matches brief delivery: the overwhelmingly common failure
    is a public channel the bot was never invited to, and self-joining recovers
    it. A private channel still cannot be self-joined, and `post_message` turns
    that into the actionable "invite @Sprntly" error — which the preview has
    already tried to prevent the user from reaching.
    """
    artifact = _resolve_explicit(company, body)
    if artifact is None:
        resolved = _resolve_by_query(company, body)
        if resolved["status"] != "resolved":
            # Nothing to post. A 404 rather than a silent no-op: the client
            # asked to send a specific thing, and "we could not find it" must
            # never render as "sent".
            raise HTTPException(404, "Couldn't find that document to share.")
        artifact = resolved["artifact"]

    summary = _summary_for(company, artifact)
    text, blocks = slack_share.compose_share(
        note=body.note,
        artifact=artifact,
        summary=summary,
        sharer_name=company.user_name,
    )

    token = _bot_token(company)
    result = slack_oauth.post_message(
        token,
        channel=body.channel_id.strip(),
        text=text,
        blocks=blocks,
        auto_join=True,
    )
    return {
        "ok": True,
        "channel": result.get("channel") or body.channel_id.strip(),
        "ts": result.get("ts"),
        "target": artifact,
    }

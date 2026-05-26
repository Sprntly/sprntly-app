"""HTTP layer for competitor profiles + signals.

  POST   /v1/research/competitors                       — create
  GET    /v1/research/competitors?dataset=<slug>         — list
  PUT    /v1/research/competitors/{id}                   — update
  DELETE /v1/research/competitors/{id}                   — delete
  GET    /v1/research/competitors/{id}/signals
            ?since=<iso>&source=<source>                 — list signals
  POST   /v1/research/competitors/{id}/refresh           — run all
            monitors, returns count of new signals

`workspace_id` derivation: the session JWT carries an `aud` claim
("app" or "demo"); we map that 1:1 to a workspace today. Multi-tenant
isolation per-user lands when we wire real auth — until then the
audience IS the tenant key, which gives us a real boundary for tests
while keeping the demo single-tenant.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import require_session
from app.research import profile_service
from app.research.monitors import default_monitors
from app.research.profile import (
    CompetitorProfile,
    CompetitorProfileCreate,
    CompetitorProfileUpdate,
    CompetitorSignal,
)
from app.research.profile_service import ProfileNotFound, SignalRejected

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/research/competitors", tags=["research"])


def _workspace_id(session: dict) -> str:
    """Derive a workspace_id from the session payload.

    Today: one workspace per audience ("app" or "demo"). When real
    multi-tenant auth lands this maps to the user's workspace id from
    the JWT — same call site, no route change.
    """
    aud = session.get("aud") or session.get("scope") or "app"
    return f"ws_{aud}"


class RefreshResponse(BaseModel):
    profile_id: str
    new_signal_count: int
    per_monitor: dict[str, int]


@router.post("", response_model=CompetitorProfile)
def create(
    body: CompetitorProfileCreate,
    session: dict = Depends(require_session),
):
    workspace_id = _workspace_id(session)
    return profile_service.create_profile(workspace_id, body)


@router.get("", response_model=list[CompetitorProfile])
def list_(
    # `dataset` is accepted for forward-compat with the brief/PRD routes,
    # but profiles are workspace-scoped not dataset-scoped — we ignore
    # the value today. Documenting it as accepted keeps the frontend
    # contract stable.
    dataset: Optional[str] = Query(default=None),  # noqa: ARG001
    session: dict = Depends(require_session),
):
    workspace_id = _workspace_id(session)
    return profile_service.list_profiles(workspace_id)


@router.put("/{profile_id}", response_model=CompetitorProfile)
def update(
    profile_id: str,
    body: CompetitorProfileUpdate,
    session: dict = Depends(require_session),
):
    workspace_id = _workspace_id(session)
    try:
        return profile_service.update_profile(workspace_id, profile_id, body)
    except ProfileNotFound as e:
        raise HTTPException(404, str(e))


@router.delete("/{profile_id}")
def delete(
    profile_id: str,
    session: dict = Depends(require_session),
):
    workspace_id = _workspace_id(session)
    try:
        profile_service.delete_profile(workspace_id, profile_id)
    except ProfileNotFound as e:
        raise HTTPException(404, str(e))
    return {"deleted": True, "id": profile_id}


@router.get("/{profile_id}/signals", response_model=list[CompetitorSignal])
def list_signals(
    profile_id: str,
    since: Optional[datetime] = Query(default=None),
    source: Optional[str] = Query(default=None),
    session: dict = Depends(require_session),
):
    workspace_id = _workspace_id(session)
    # Resolve the profile first so we 404 cleanly on cross-tenant access.
    try:
        profile_service.get_profile(workspace_id, profile_id)
    except ProfileNotFound as e:
        raise HTTPException(404, str(e))
    return profile_service.list_signals(profile_id, since=since, source=source)


@router.post("/{profile_id}/refresh", response_model=RefreshResponse)
def refresh(
    profile_id: str,
    session: dict = Depends(require_session),
):
    """Run every default monitor against this profile, persist new signals,
    return the per-monitor breakdown.

    Synchronous on purpose — the App Store + changelog fetches each have
    a 10s timeout and a single profile won't hit more than ~30s total. If
    we add expensive monitors later we'll move this off the request thread.
    """
    workspace_id = _workspace_id(session)
    try:
        profile = profile_service.get_profile(workspace_id, profile_id)
    except ProfileNotFound as e:
        raise HTTPException(404, str(e))

    per_monitor: dict[str, int] = {}
    total = 0
    for monitor in default_monitors():
        try:
            signals = monitor.check_for_new_signals(profile)
        except Exception:
            logger.exception(
                "Monitor %s failed for profile %s", monitor.name, profile.id
            )
            per_monitor[monitor.name] = 0
            continue
        recorded = 0
        for sig in signals:
            try:
                profile_service.record_signal(profile.id, sig)
                recorded += 1
            except SignalRejected as e:
                logger.info(
                    "Rejected signal from %s for %s: %s",
                    monitor.name,
                    profile.id,
                    e,
                )
        per_monitor[monitor.name] = recorded
        total += recorded
    return RefreshResponse(
        profile_id=profile.id,
        new_signal_count=total,
        per_monitor=per_monitor,
    )

"""The ready-notification hook in the background generation runner.

`_run_generation_bg` calls `_notify_prototype_ready` ONLY after a successful
stage (staged_ok). This covers the helper: it resolves the PRD title, dispatches
the delivery off the event loop, and never lets a delivery failure escape (a
notification must never fail the generation).
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_notify_resolves_title_and_dispatches(monkeypatch):
    from app.routes import design_agent as da

    monkeypatch.setattr(da, "get_prd_rendered",
                        lambda prd_id: {"title": "Offline sync retry"})

    captured = {}

    def fake_deliver(company_id, *, prd_id, prd_title):
        captured.update(company_id=company_id, prd_id=prd_id, prd_title=prd_title)
        return {"slack": {"delivered": True}, "email": {"delivered": False}}

    monkeypatch.setattr(
        "app.synthesis.prototype_delivery.deliver_prototype_ready", fake_deliver)

    await da._notify_prototype_ready("co-1", 7, 99)
    assert captured == {"company_id": "co-1", "prd_id": 7,
                        "prd_title": "Offline sync retry"}


@pytest.mark.asyncio
async def test_notify_defaults_title_when_prd_missing(monkeypatch):
    from app.routes import design_agent as da

    monkeypatch.setattr(da, "get_prd_rendered", lambda prd_id: None)
    captured = {}
    monkeypatch.setattr(
        "app.synthesis.prototype_delivery.deliver_prototype_ready",
        lambda cid, *, prd_id, prd_title: captured.update(prd_title=prd_title))

    await da._notify_prototype_ready("co-1", 7, 99)
    assert captured["prd_title"] == "your PRD"


@pytest.mark.asyncio
async def test_notify_swallows_delivery_errors(monkeypatch):
    from app.routes import design_agent as da

    monkeypatch.setattr(da, "get_prd_rendered",
                        lambda prd_id: {"title": "T"})

    def boom(*a, **k):
        raise RuntimeError("resend exploded")

    monkeypatch.setattr(
        "app.synthesis.prototype_delivery.deliver_prototype_ready", boom)

    # Must NOT raise — a notification failure can never fail the generation.
    await da._notify_prototype_ready("co-1", 7, 99)


@pytest.mark.asyncio
async def test_notify_swallows_title_fetch_errors(monkeypatch):
    from app.routes import design_agent as da

    def boom(prd_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(da, "get_prd_rendered", boom)
    # A failing title fetch is swallowed too — no dispatch, no raise.
    await da._notify_prototype_ready("co-1", 7, 99)

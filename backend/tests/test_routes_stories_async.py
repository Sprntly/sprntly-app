"""Async story-generation route: POST is fire-and-forget (returns a job id),
GET /jobs/{id} polls until ready/failed. Replaces the old synchronous POST that
blocked the Tickets tab on a multi-minute LLM call.
"""
from __future__ import annotations

import asyncio

import pytest

from app.auth import CompanyContext
from app.routes import stories
from app.stories.generate import PRDNotFoundError, Story


def _ctx(cid: str = "ent-A") -> CompanyContext:
    return CompanyContext(company_id=cid, role="owner", user_id="u")


async def _drain(job_id: int, tries: int = 100) -> None:
    """Wait for the background task to leave the 'generating' state."""
    for _ in range(tries):
        if stories._jobs[job_id]["status"] != "generating":
            return
        await asyncio.sleep(0.02)


def test_generate_returns_job_id_then_polls_ready(isolated_settings, monkeypatch):
    monkeypatch.setattr(
        stories, "generate_user_stories",
        lambda cid, prd_id=None, insight=None: [
            Story(title="Wire SSO", body="As a PM…"),
            Story(title="Add audit log", body="As an admin…"),
        ],
    )

    async def _flow():
        resp = await stories.generate(stories.GenerateIn(insight="Users want SSO"), _ctx())
        assert resp["status"] == "generating" and isinstance(resp["job_id"], int)
        await _drain(resp["job_id"])
        return resp["job_id"]

    job_id = asyncio.run(_flow())
    got = stories.get_job(job_id, _ctx())
    assert got["status"] == "ready"
    assert [s["title"] for s in got["stories"]] == ["Wire SSO", "Add audit log"]


def test_poll_reports_failure_instead_of_hanging(isolated_settings, monkeypatch):
    def _boom(cid, prd_id=None, insight=None):
        raise PRDNotFoundError("prd 999 not found")
    monkeypatch.setattr(stories, "generate_user_stories", _boom)

    async def _flow():
        resp = await stories.generate(stories.GenerateIn(prd_id=999), _ctx())
        await _drain(resp["job_id"])
        return resp["job_id"]

    job_id = asyncio.run(_flow())
    got = stories.get_job(job_id, _ctx())
    assert got["status"] == "failed"
    assert "not found" in got["error"]


def test_get_job_is_tenant_scoped(isolated_settings, monkeypatch):
    monkeypatch.setattr(
        stories, "generate_user_stories",
        lambda cid, prd_id=None, insight=None: [Story(title="X", body="b")],
    )

    async def _flow():
        resp = await stories.generate(stories.GenerateIn(insight="x"), _ctx("ent-A"))
        await _drain(resp["job_id"])
        return resp["job_id"]

    job_id = asyncio.run(_flow())
    # Owner sees it…
    assert stories.get_job(job_id, _ctx("ent-A"))["status"] == "ready"
    # …a foreign tenant gets 404 (job ids are sequential — no existence leak).
    with pytest.raises(Exception) as ei:
        stories.get_job(job_id, _ctx("ent-B"))
    assert getattr(ei.value, "status_code", None) == 404
    # Unknown id → 404.
    with pytest.raises(Exception) as ei2:
        stories.get_job(999999, _ctx("ent-A"))
    assert getattr(ei2.value, "status_code", None) == 404


def test_generate_requires_exactly_one_source(isolated_settings):
    async def _flow():
        with pytest.raises(Exception) as ei:
            await stories.generate(stories.GenerateIn(), _ctx())
        assert getattr(ei.value, "status_code", None) == 400
        with pytest.raises(Exception) as ei2:
            await stories.generate(stories.GenerateIn(prd_id=1, insight="x"), _ctx())
        assert getattr(ei2.value, "status_code", None) == 400
    asyncio.run(_flow())

"""Server-side enforcement of the staff-panel module flags.

companies.feature_flags (written by /v1/staff) is now ENFORCED:

  * `agents` off      → the chat surface 403s: POST /v1/ask, POST
                        /v1/ask/extract-file, POST /v1/agent/chat-with-tools.
  * `weekly_brief` off → the scheduler skips the company (weekly tick AND the
                        brief-generating synthesis cycle) and the on-demand
                        generation endpoints 403 (/v1/brief/generate,
                        /regenerate, /regenerate-all, /v1/synthesis/brief).
                        Read endpoints stay open; existing briefs remain
                        visible.

Missing keys are grandfathered ON (matrix unit-tested in
test_entitlements.py); staff routes themselves are NEVER gated by these
flags (they authenticate via require_staff, not a tenant).
"""
from __future__ import annotations

import asyncio
import io
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.entitlements import (
    AGENTS_DISABLED_DETAIL,
    WEEKLY_BRIEF_DISABLED_DETAIL,
)

UTC = timezone.utc


def _set_flags(company_id: str, flags: dict) -> None:
    from app.db.client import require_client

    require_client().table("companies").update({"feature_flags": flags}).eq(
        "id", company_id
    ).execute()


def _seed_corpus(data_dir, dataset, body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


_ASK_PAYLOAD = {
    "answer": "ok",
    "key_points": [],
    "citations": [],
    "confidence": 0.5,
    "unanswered": "",
}


# ---- agents module: chat/ask surface ----------------------------------------

def test_ask_403_when_agents_module_off(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    _set_flags(t.company_id, {"agents": False})
    resp = t.client.post(
        "/v1/ask", json={"question": "What drives churn?", "dataset": "acme"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == AGENTS_DISABLED_DETAIL


def test_ask_403_when_legacy_flags_all_off(tenant_client, isolated_settings):
    """No modern `agents` key, but both legacy chat capabilities explicitly
    off → the chat surface is off (mirrors the staff panel's mapping)."""
    t = tenant_client.make(slug="acme")
    _set_flags(
        t.company_id, {"on_demand_analysis": False, "auto_prd_generation": False}
    )
    resp = t.client.post(
        "/v1/ask", json={"question": "What drives churn?", "dataset": "acme"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == AGENTS_DISABLED_DETAIL


def test_ask_allowed_when_agents_key_missing(
    tenant_client, isolated_settings, fake_llm
):
    """Grandfathering: irrelevant/legacy-on keys (no explicit off) → normal 200
    fire-and-forget contract."""
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    fake_llm["payload"] = dict(_ASK_PAYLOAD)
    _set_flags(t.company_id, {"weekly_brief": False, "on_demand_analysis": True})
    resp = t.client.post(
        "/v1/ask", json={"question": "What drives churn?", "dataset": "acme"}
    )
    assert resp.status_code == 200
    assert isinstance(resp.json()["ask_id"], int)


def test_ask_allowed_when_flags_empty(tenant_client, isolated_settings, fake_llm):
    """The default {} row (every pre-staff-panel company) stays fully ON."""
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    fake_llm["payload"] = dict(_ASK_PAYLOAD)
    resp = t.client.post(
        "/v1/ask", json={"question": "What drives churn?", "dataset": "acme"}
    )
    assert resp.status_code == 200


def test_extract_file_403_when_agents_module_off(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    _set_flags(t.company_id, {"agents": False})
    resp = t.client.post(
        "/v1/ask/extract-file",
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == AGENTS_DISABLED_DETAIL


def test_agent_chat_with_tools_403_when_agents_module_off(
    tenant_client, isolated_settings
):
    t = tenant_client.make(slug="acme")
    _set_flags(t.company_id, {"agents": False})
    resp = t.client.post(
        "/v1/agent/chat-with-tools",
        json={"message": "hi", "installation_id": 1},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == AGENTS_DISABLED_DETAIL


def test_agent_chat_with_tools_passes_gate_when_agents_on(
    tenant_client, isolated_settings
):
    """With the module on, the request reaches the (pre-LLM) installation
    ownership guard — 404, not the module 403."""
    t = tenant_client.make(slug="acme")
    _set_flags(t.company_id, {"agents": True})
    resp = t.client.post(
        "/v1/agent/chat-with-tools",
        json={"message": "hi", "installation_id": 999},
    )
    assert resp.status_code == 404


# ---- weekly_brief module: on-demand endpoints --------------------------------

@pytest.mark.parametrize(
    "path",
    ["/v1/brief/generate", "/v1/brief/regenerate", "/v1/brief/regenerate-all"],
)
def test_brief_generation_endpoints_403_when_weekly_brief_off(
    tenant_client, isolated_settings, path
):
    t = tenant_client.make(slug="acme")
    _set_flags(t.company_id, {"weekly_brief": False})
    resp = t.client.post(path, params={"dataset": "acme"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == WEEKLY_BRIEF_DISABLED_DETAIL


def test_synthesis_brief_403_when_weekly_brief_off(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    _set_flags(t.company_id, {"weekly_brief": False})
    resp = t.client.post("/v1/synthesis/brief")
    assert resp.status_code == 403
    assert resp.json()["detail"] == WEEKLY_BRIEF_DISABLED_DETAIL


def test_brief_regenerate_allowed_when_flag_missing(
    tenant_client, isolated_settings, monkeypatch
):
    """Missing weekly_brief key (agents-off is irrelevant here) → the
    fire-and-forget regenerate kicks off normally."""
    from app.routes import brief as brief_route

    async def _noop(dataset):
        return None

    monkeypatch.setattr(brief_route, "_synthesis_generate_bg", _noop)
    t = tenant_client.make(slug="acme")
    _set_flags(t.company_id, {"agents": False})
    resp = t.client.post("/v1/brief/regenerate", params={"dataset": "acme"})
    assert resp.status_code == 200
    assert resp.json()["started"] is True


def test_brief_reads_stay_open_when_weekly_brief_off(
    tenant_client, isolated_settings
):
    """Toggling the module off must not hide existing briefs — only new
    generation/delivery stops. (404 'no brief yet' here, never the module 403.)"""
    t = tenant_client.make(slug="acme")
    _set_flags(t.company_id, {"weekly_brief": False})
    resp = t.client.get("/v1/brief/current", params={"dataset": "acme"})
    assert resp.status_code == 404  # no brief seeded — but NOT a 403
    status = t.client.get("/v1/brief/status", params={"dataset": "acme"})
    assert status.status_code == 200


# ---- weekly_brief module: scheduler skips -------------------------------------

@pytest.fixture(autouse=True)
def _reset_ledger():
    from app import scheduler as sched_mod

    sched_mod._last_brief_run.clear()
    yield
    sched_mod._last_brief_run.clear()


def _run_weekly_tick(now, companies):
    """Drive _run_weekly_brief_tick with a fixed company list; return the slugs
    that got a brief generated (mirrors test_scheduler_weekly_brief.py)."""
    from app import scheduler as sched_mod

    generated: list[str] = []

    async def _fake_gen(company_id, slug):
        generated.append(slug)

    with patch.object(sched_mod, "list_companies", return_value=companies), \
         patch.object(sched_mod, "_generate_weekly_brief_for_company",
                      side_effect=_fake_gen):
        asyncio.run(sched_mod._run_weekly_brief_tick(now=now))
    return generated


def test_weekly_tick_skips_company_with_weekly_brief_off():
    """Two UTC companies inside the Monday-06:00 window: the explicitly-off one
    is skipped, the missing-key one still fires (grandfathered ON)."""
    companies = [
        {"id": "co-off", "slug": "gated", "feature_flags": {"weekly_brief": False}},
        {"id": "co-on", "slug": "open", "feature_flags": {}},
    ]
    # Monday 2026-06-08 06:00 UTC — both companies' (default-UTC) window is open.
    generated = _run_weekly_tick(datetime(2026, 6, 8, 6, 0, tzinfo=UTC), companies)
    assert generated == ["open"]


def test_weekly_tick_agents_flag_is_irrelevant_to_brief():
    """agents:false alone must not stop the weekly brief."""
    companies = [
        {"id": "co-1", "slug": "chatless", "feature_flags": {"agents": False}},
    ]
    generated = _run_weekly_tick(datetime(2026, 6, 8, 6, 0, tzinfo=UTC), companies)
    assert generated == ["chatless"]


def test_synthesis_cycle_skips_company_with_weekly_brief_off():
    """The scheduled synthesis cycle GENERATES briefs, so it honors the flag
    too. (KG ingestion runs in the connector-refresh job, untouched here.)"""
    from app import scheduler as sched_mod

    companies = [
        {"id": "co-off", "slug": "gated", "feature_flags": {"weekly_brief": False}},
        {"id": "co-on", "slug": "open", "feature_flags": {"agents": False}},
    ]
    generated: list[str] = []

    with patch("app.db.companies.list_companies", return_value=companies), \
         patch("app.synthesis_brief.generate_brief_for",
               side_effect=lambda slug: generated.append(slug)), \
         patch("app.brief_runner.warm_synthesis_drilldowns", lambda slug: None):
        asyncio.run(sched_mod._run_synthesis_for_all_companies())

    assert generated == ["open"]


# ---- staff routes are NEVER gated by module flags ------------------------------

def test_staff_routes_unaffected_by_module_flags(isolated_settings, monkeypatch):
    """A staff admin whose own company has every module off can still list and
    patch companies — /v1/staff/* authenticates via require_staff, not a
    tenant, so feature_flags never apply."""
    from argon2 import PasswordHasher

    from tests._company_helpers import company_client

    ctx = company_client(monkeypatch)
    _set_flags(ctx.company_id, {"agents": False, "weekly_brief": False})

    # Configure the staff credential AFTER company_client (it reloads
    # app.config/app.auth — mirrors test_staff_admin._enable_staff_surface).
    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod.settings, "staff_admin_id", "staff-id")
    monkeypatch.setattr(
        auth_mod.settings,
        "staff_admin_password_hash",
        PasswordHasher().hash("staff-pw"),
    )
    login = ctx.client.post(
        "/v1/staff/login", json={"id": "staff-id", "password": "staff-pw"}
    )
    assert login.status_code == 200, login.text
    staff_headers = {"Authorization": f"Bearer {login.json()['token']}"}

    listed = ctx.client.get("/v1/staff/companies", headers=staff_headers)
    assert listed.status_code == 200
    row = next(
        c for c in listed.json()["companies"] if c["id"] == ctx.company_id
    )
    assert row["feature_flags"] == {"agents": False, "weekly_brief": False}

    patched = ctx.client.patch(
        f"/v1/staff/companies/{ctx.company_id}",
        headers=staff_headers,
        json={"feature_flags": {"agents": True}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["feature_flags"]["agents"] is True

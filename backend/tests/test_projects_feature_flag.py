"""Tests for the `PROJECTS_ENABLED` request-time master switch on
`app/routes/projects.py`'s router.

The gate is a SINGLE router-level dependency
(`router = APIRouter(..., dependencies=[Depends(_require_projects_enabled)])`)
so every route under `/v1/projects` — the 29 that exist today and any future
one — 404s when the flag is off, before any auth/membership resolves.

`company_client(monkeypatch)` (via `setup_supabase_auth`) sets
`PROJECTS_ENABLED=1` as part of its standard setup, so every off-test below
explicitly `delenv`s it back off to exercise the dark path.
"""
from __future__ import annotations

import os

import pytest

from app.routes.projects import _projects_enabled
from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member


def _create_project(ctx, *, name: str = "Flag test project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


# ── AC1/AC2: off ⇒ 404 across a representative route set ──────────────────


def test_projects_list_404_when_flag_off(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    monkeypatch.delenv("PROJECTS_ENABLED", raising=False)
    resp = ctx.client.get("/v1/projects")
    assert resp.status_code == 404


def test_projects_list_404_when_flag_explicit_false(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    monkeypatch.setenv("PROJECTS_ENABLED", "false")
    resp = ctx.client.get("/v1/projects")
    assert resp.status_code == 404


def test_project_create_404_when_flag_off(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    monkeypatch.delenv("PROJECTS_ENABLED", raising=False)
    resp = ctx.client.post("/v1/projects", json={"name": "x"})
    assert resp.status_code == 404


def test_project_members_404_when_flag_off(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    monkeypatch.delenv("PROJECTS_ENABLED", raising=False)
    resp = ctx.client.post(
        f"/v1/projects/{project['id']}/members", json={"email": "someone@example.com"}
    )
    assert resp.status_code == 404


def test_project_group_turns_404_when_flag_off(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    monkeypatch.delenv("PROJECTS_ENABLED", raising=False)
    resp = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns", json={"content": "hi"}
    )
    assert resp.status_code == 404


def test_project_candidates_404_when_flag_off(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    monkeypatch.delenv("PROJECTS_ENABLED", raising=False)
    resp = ctx.client.get(f"/v1/projects/{project['id']}/candidates")
    assert resp.status_code == 404


# ── AC3: non-member and member both get the identical 404 when off ────────


def test_non_member_and_member_both_404_when_off(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _non_member_user_id, non_member_headers = seed_same_tenant_non_member(ctx)

    monkeypatch.delenv("PROJECTS_ENABLED", raising=False)

    member_resp = ctx.client.get(f"/v1/projects/{project['id']}")
    non_member_resp = ctx.client.get(
        f"/v1/projects/{project['id']}", headers=non_member_headers
    )
    assert member_resp.status_code == 404
    assert non_member_resp.status_code == 404


# ── AC4: on ⇒ works, unchanged behaviour ───────────────────────────────────


def test_projects_list_200_when_flag_on(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    monkeypatch.setenv("PROJECTS_ENABLED", "1")
    resp = ctx.client.get("/v1/projects")
    assert resp.status_code == 200


# ── AC7: request-time read, same running app, no re-import ────────────────


def test_request_time_toggle_flips_status(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    monkeypatch.setenv("PROJECTS_ENABLED", "1")
    on_resp = ctx.client.get("/v1/projects")
    assert on_resp.status_code == 200

    monkeypatch.delenv("PROJECTS_ENABLED", raising=False)
    off_resp = ctx.client.get("/v1/projects")
    assert off_resp.status_code == 404


# ── AC5: closed-world — every /v1/projects route carries the gate ─────────


def test_every_v1_projects_route_is_gated(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    app = ctx.client.app

    from app.routes.projects import _require_projects_enabled

    checked = 0
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/v1/projects"):
            continue
        dependant = getattr(route, "dependant", None)
        assert dependant is not None, f"route {path} has no dependant to inspect"
        dep_calls = [d.call for d in dependant.dependencies]
        assert _require_projects_enabled in dep_calls, (
            f"route {path} ({getattr(route, 'methods', None)}) is missing the "
            "_require_projects_enabled gate"
        )
        checked += 1

    # Sanity: the closed-world check actually iterated a non-trivial set of
    # routes, so a future refactor that renamed the prefix can't silently
    # make this test vacuously pass.
    assert checked >= 29


# ── AC6: truthy matrix ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", True),
        ("true", True),
        ("True", True),
        ("YES", True),
        (" true ", True),
        ("yes", True),
        (None, False),
        ("", False),
        ("0", False),
        ("false", False),
        ("no", False),
        ("nope", False),
    ],
)
def test_projects_enabled_truthy_matrix(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("PROJECTS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("PROJECTS_ENABLED", value)
    assert _projects_enabled() is expected

"""Tests for POST /v1/design-agent/locate.

Covers the wiring layer: workspace isolation → installation resolver →
build_map → locate_screen → decide_gate → response serialization.
No new map/locate/gate logic is tested here — that belongs in the unit
suites for their respective modules.

/locate is now an accept + poll contract: POST returns a job id immediately and
the pipeline runs in a background task; the client polls GET /locate/jobs/{id}
for the result. These tests drive that flow end-to-end via ``_post_and_poll``,
which POSTs then runs the background task to completion and returns the polled
result — so the response-shape assertions below exercise the SAME LocateResponse
payload the synchronous endpoint used to return inline. (The dedicated async
contract behaviours — immediate return, job store, cross-workspace 404, drain
registration — live in test_design_agent_locate_async.py.)
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _TEST_COMPANY_ID


def _post_and_poll(client: TestClient, routes_mod, json_body: dict):
    """POST /locate (auth + validation + job mint), run the background pipeline to
    completion on a fresh loop, then GET the job. Returns the poll Response.

    The POST kicks ``_run_locate_bg`` as a fire-and-forget task; rather than race
    the sync TestClient's portal loop we drive the same coroutine deterministically
    here (the heavy calls are patched via asyncio.to_thread, so it completes
    synchronously) so the response-shape assertions see the SAME LocateResponse the
    synchronous endpoint used to return inline. The dedicated immediate-return and
    drain-registration behaviours are proven in test_design_agent_locate_async.py."""
    async def _noop_bg(**_kw):
        return None

    # Mint the job but suppress the fire-and-forget task's real work, so the only
    # execution of the pipeline is the deterministic asyncio.run below (no portal
    # loop race, no double telemetry emission).
    with patch.object(routes_mod, "_run_locate_bg", new=_noop_bg):
        accepted = client.post("/v1/design-agent/locate", json=json_body)
    assert accepted.status_code == 202, accepted.text
    payload = accepted.json()
    assert payload["status"] == "running"
    job_id = payload["job_id"]

    rec = routes_mod._locate_jobs[job_id]
    # The mint stored the workspace + running status; run the pipeline coroutine
    # to populate the terminal record (running→done/error), then poll it.
    asyncio.run(
        routes_mod._run_locate_bg(
            job_id=job_id,
            workspace_id=rec["workspace_id"],
            github_repo=json_body["github_repo"],
            ref=json_body.get("ref"),
            prd_text="",
            installation_id=routes_mod._resolve_github_installation_id_for_repo(
                rec["workspace_id"], json_body["github_repo"]
            ),
        )
    )
    return client.get(f"/v1/design-agent/locate/jobs/{job_id}")

# ── env fixture ──────────────────────────────────────────────────────────────


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """Feature flag ON + DA route stack reloaded in dependency order."""
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    return SimpleNamespace(routes=routes_mod, main=main_mod)


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company). workspace_id == _TEST_COMPANY_ID."""
    return company_client


# ── seed helpers ─────────────────────────────────────────────────────────────

def _seed_prd(
    *,
    prd_id: int = 1,
    brief_id: int = 1,
    workspace_slug: str = f"slug-{_TEST_COMPANY_ID}",
    payload_md: str = "Login screen for the test product",
) -> None:
    """Seed a brief + PRD row so require_owned_prd resolves to the test workspace."""
    from tests import _fake_supabase
    db = _fake_supabase.get_fake_db()
    db.execute(
        "INSERT INTO briefs (id, dataset, payload, is_current) VALUES (?, ?, '{}', 1)",
        (brief_id, workspace_slug),
    )
    db.execute(
        "INSERT INTO prds (id, brief_id, insight_index, title, payload_md, status)"
        " VALUES (?, ?, 0, 'Test PRD', ?, 'ready')",
        (prd_id, brief_id, payload_md),
    )
    db.commit()


def _seed_cross_workspace_prd(*, prd_id: int = 99) -> None:
    """Seed a PRD belonging to a different company (for workspace-isolation tests)."""
    from tests import _fake_supabase
    db = _fake_supabase.get_fake_db()
    db.execute(
        "INSERT INTO companies (id, slug, display_name) VALUES ('other-co', 'slug-other-co', 'Other Co')"
    )
    db.execute(
        "INSERT INTO briefs (id, dataset, payload, is_current) VALUES (200, 'slug-other-co', '{}', 1)"
    )
    db.execute(
        "INSERT INTO prds (id, brief_id, insight_index, title, payload_md, status)"
        " VALUES (?, 200, 0, 'Other PRD', 'Other workspace content', 'ready')",
        (prd_id,),
    )
    db.commit()


def _mock_installation(monkeypatch, installation_id: int = 42) -> None:
    """Patch installation resolver to return a fixed installation id."""
    monkeypatch.setattr(
        "app.routes.design_agent._resolve_github_installation_id_for_repo",
        lambda *a, **kw: installation_id,
    )


def _make_map_result(
    route: str = "/home",
    entry_component: str = "HomeScreen",
    confidence: int = 90,
    composed_components: list | None = None,
    posture: str = "CLEAN",
):
    """Build a minimal MapResult + LocateResult for happy-path tests."""
    from app.design_agent.codebase_map.types import MapResult, ScreenNode, ShellModel
    from app.design_agent.codebase_map.locate import LocateResult, LocateCandidate

    node = ScreenNode(
        route=route,
        entry_component=entry_component,
        composed_components=composed_components or ["Header", "Footer"],
    )
    map_result = MapResult(
        repo="org/repo",
        posture=posture,  # type: ignore[arg-type]
        nodes=[node],
        shell=ShellModel(),
    )
    candidate = LocateCandidate(
        route=route,
        entry_component=entry_component,
        confidence=confidence,
        rationale="Main screen",
        ambiguous=False,
    )
    locate_result = LocateResult(candidates=[candidate])
    return map_result, locate_result


# ── flag / auth ───────────────────────────────────────────────────────────────


def test_locate_404_when_flag_off(client, env, monkeypatch):
    """Feature flag off → 404 (invisible, not 401/422)."""
    _seed_prd()
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "0")
    resp = client.post(
        "/v1/design-agent/locate",
        json={"prd_id": 1, "github_repo": "org/repo"},
    )
    assert resp.status_code == 404


def test_locate_cross_workspace_prd_404(client, env):
    """PRD belonging to another workspace returns 404, not a job id."""
    _seed_cross_workspace_prd(prd_id=99)
    resp = client.post(
        "/v1/design-agent/locate",
        json={"prd_id": 99, "github_repo": "org/repo"},
    )
    assert resp.status_code == 404


# ── happy paths ───────────────────────────────────────────────────────────────


def test_locate_auto_proceed_response(client, env, monkeypatch):
    """Happy path: valid PRD + connected repo + CLEAN map + high confidence → auto_proceed."""
    _seed_prd()
    _mock_installation(monkeypatch)
    fake_map, fake_locate = _make_map_result(confidence=90, composed_components=["Header", "Hero", "Footer"])

    async def _fake_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return fake_map
        return fake_locate

    with patch("asyncio.to_thread", new=_fake_to_thread):
        resp = _post_and_poll(
            client, env.routes,
            {"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200, resp.text
    poll = resp.json()
    assert poll["status"] == "done"
    body = poll["result"]
    assert body["decision"] == "auto_proceed"
    assert len(body["chosen"]) == 1
    assert body["chosen"][0]["route"] == "/home"
    # component_count populated from ScreenNode.composed_components.
    assert body["chosen"][0]["component_count"] == 3
    assert len(body["ranked"]) == 1
    assert body["posture"] == "CLEAN"
    assert body["unmapped"] is False
    assert body["repo"] == "org/repo"


def test_locate_ranked_confirm_response(client, env, monkeypatch):
    """Low confidence → ranked_confirm, chosen is empty, ranked carries candidates."""
    _seed_prd()
    _mock_installation(monkeypatch)
    # confidence 40 < default threshold 80 → ranked_confirm
    fake_map, fake_locate = _make_map_result(confidence=40)

    async def _fake_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return fake_map
        return fake_locate

    with patch("asyncio.to_thread", new=_fake_to_thread):
        resp = _post_and_poll(
            client, env.routes,
            {"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200
    body = resp.json()["result"]
    assert body["decision"] == "ranked_confirm"
    assert body["chosen"] == []
    assert len(body["ranked"]) == 1
    assert body["unmapped"] is False


# ── steerable re-search: optional `hint` threading ───────────────────────────
#
# These exercise TWO seams independently:
#   1. endpoint → _run_locate_bg : the POST body's hint is trimmed (blank→None)
#      and forwarded as the hint kwarg. Captured by patching _run_locate_bg.
#   2. _run_locate_bg → locate_screen : the bg task forwards its hint kwarg into
#      the locate call. Driven directly (the _post_and_poll harness reconstructs
#      the bg call itself, so it cannot cover seam 1).


def _capture_run_locate_bg(routes_mod):
    """Patch _run_locate_bg to record the kwargs the endpoint hands it, without
    running the pipeline. Returns the captured dict (populated after the POST)."""
    captured: dict = {}

    async def _spy(**kwargs):
        captured.update(kwargs)
        return None

    return patch.object(routes_mod, "_run_locate_bg", new=_spy), captured


def test_endpoint_forwards_hint_to_bg_task(client, env, monkeypatch):
    """A `hint` in the POST body is forwarded to _run_locate_bg verbatim."""
    _seed_prd()
    _mock_installation(monkeypatch)
    patcher, captured = _capture_run_locate_bg(env.routes)

    with patcher:
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo", "hint": "the settings page"},
        )

    assert resp.status_code == 202, resp.text
    assert captured.get("hint") == "the settings page"


def test_endpoint_forwards_none_when_hint_absent(client, env, monkeypatch):
    """No hint in the body → _run_locate_bg receives hint=None (unsteered)."""
    _seed_prd()
    _mock_installation(monkeypatch)
    patcher, captured = _capture_run_locate_bg(env.routes)

    with patcher:
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 202, resp.text
    assert captured.get("hint") is None


def test_endpoint_trims_blank_hint_to_none(client, env, monkeypatch):
    """A whitespace-only hint is trimmed to None before the bg task sees it."""
    _seed_prd()
    _mock_installation(monkeypatch)
    patcher, captured = _capture_run_locate_bg(env.routes)

    with patcher:
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo", "hint": "   "},
        )

    assert resp.status_code == 202, resp.text
    assert captured.get("hint") is None


def test_overlong_hint_rejected_by_request_validation(client, env, monkeypatch):
    """LocateRequest caps hint length; an over-long hint is a 422, not silently
    truncated at the route (the prompt layer caps defensively below that)."""
    _seed_prd()
    _mock_installation(monkeypatch)
    resp = client.post(
        "/v1/design-agent/locate",
        json={"prd_id": 1, "github_repo": "org/repo", "hint": "x" * 301},
    )
    assert resp.status_code == 422, resp.text


def test_bg_task_forwards_hint_to_locate_screen(client, env, monkeypatch):
    """_run_locate_bg forwards its hint kwarg into the locate_screen call."""
    _seed_prd()
    _mock_installation(monkeypatch)
    fake_map, fake_locate = _make_map_result(confidence=90)
    captured: dict = {}

    async def _fake_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return fake_map
        if func.__name__ == "locate_screen":
            captured["hint"] = kwargs.get("hint", "__absent__")
        return fake_locate

    with patch("asyncio.to_thread", new=_fake_to_thread):
        env.routes._locate_jobs["jobX"] = {
            "status": "running",
            "workspace_id": _TEST_COMPANY_ID,
            "created_at": 0.0,
        }
        asyncio.run(
            env.routes._run_locate_bg(
                job_id="jobX",
                workspace_id=_TEST_COMPANY_ID,
                github_repo="org/repo",
                ref=None,
                prd_text="login PRD",
                installation_id=42,
                hint="the settings page",
            )
        )

    assert captured.get("hint") == "the settings page"


# ── image-as-steer: optional `image` threading ───────────────────────────────


def _data_url(raw: bytes = b"screenshot-bytes", mime: str = "image/png") -> str:
    import base64
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def test_endpoint_forwards_image_to_bg_task(client, env, monkeypatch):
    """An `image` in the POST body is forwarded to _run_locate_bg verbatim."""
    _seed_prd()
    _mock_installation(monkeypatch)
    patcher, captured = _capture_run_locate_bg(env.routes)
    data_url = _data_url()

    with patcher:
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo", "image": data_url},
        )

    assert resp.status_code == 202, resp.text
    assert captured.get("image") == data_url


def test_endpoint_forwards_none_image_when_absent(client, env, monkeypatch):
    """No image in the body → _run_locate_bg receives image=None."""
    _seed_prd()
    _mock_installation(monkeypatch)
    patcher, captured = _capture_run_locate_bg(env.routes)

    with patcher:
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 202, resp.text
    assert captured.get("image") is None


def test_oversized_image_body_rejected_413_and_not_queued(client, env, monkeypatch):
    """A body whose image exceeds the accept-step char cap is rejected with 413
    BEFORE any background job is scheduled — the abusive upload never reaches the
    vision model. A within-cap-but-undecodable image is a different path (it falls
    open to text-only inside the job); this guard is purely about transport size."""
    _seed_prd()
    _mock_installation(monkeypatch)
    patcher, captured = _capture_run_locate_bg(env.routes)

    # Cheap oversized body: a valid-looking data-URL prefix + a padded base64 body
    # one char past the cap. No real image bytes needed — only the length matters.
    cap = env.routes._MAX_LOCATE_IMAGE_CHARS
    oversized = "data:image/png;base64," + ("A" * (cap + 1))

    with patcher:
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo", "image": oversized},
        )

    assert resp.status_code == 413, resp.text
    # The background task was never scheduled: the spy captured nothing and no job
    # record was minted in the process-local store.
    assert captured == {}
    assert env.routes._locate_jobs == {}


def test_within_cap_image_body_still_accepted_and_queued(client, env, monkeypatch):
    """A normal small image is under the accept-step cap, so it still queues as
    before (202) and reaches the background task — the guard only trips on abuse."""
    _seed_prd()
    _mock_installation(monkeypatch)
    patcher, captured = _capture_run_locate_bg(env.routes)
    data_url = _data_url()

    with patcher:
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo", "image": data_url},
        )

    assert resp.status_code == 202, resp.text
    assert captured.get("image") == data_url


def test_bg_task_forwards_image_to_locate_screen(client, env, monkeypatch):
    """_run_locate_bg forwards its image kwarg into the locate_screen call."""
    _seed_prd()
    _mock_installation(monkeypatch)
    fake_map, fake_locate = _make_map_result(confidence=90)
    captured: dict = {}
    data_url = _data_url()

    async def _fake_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return fake_map
        if func.__name__ == "locate_screen":
            captured["image"] = kwargs.get("image", "__absent__")
        return fake_locate

    with patch("asyncio.to_thread", new=_fake_to_thread):
        env.routes._locate_jobs["jobImg"] = {
            "status": "running",
            "workspace_id": _TEST_COMPANY_ID,
            "created_at": 0.0,
        }
        asyncio.run(
            env.routes._run_locate_bg(
                job_id="jobImg",
                workspace_id=_TEST_COMPANY_ID,
                github_repo="org/repo",
                ref=None,
                prd_text="login PRD",
                installation_id=42,
                image=data_url,
            )
        )

    assert captured.get("image") == data_url


def test_response_includes_read_cues_and_image_status(client, env, monkeypatch):
    """The LocateResponse exposes the new read_cues + image_status fields."""
    _seed_prd()
    _mock_installation(monkeypatch)
    fake_map, fake_locate = _make_map_result(confidence=90)

    async def _fake_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return fake_map
        return fake_locate

    with patch("asyncio.to_thread", new=_fake_to_thread):
        resp = _post_and_poll(
            client, env.routes,
            {"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()["result"]
    # No image was sent → defaults surface, never absent from the payload.
    assert body["read_cues"] == []
    assert body["image_status"] == "absent"


# ── degradation / errors ─────────────────────────────────────────────────────


def test_no_installation_returns_unmapped_no_llm_call(client, env, monkeypatch):
    """No GitHub installation → unmapped=True, 200, locate_screen never called."""
    _seed_prd()
    # Resolver returns None → short-circuit before LLM call.
    monkeypatch.setattr(
        "app.routes.design_agent._resolve_github_installation_id_for_repo",
        lambda *a, **kw: None,
    )
    locate_call_count = 0

    async def _spy_to_thread(func, *args, **kwargs):
        nonlocal locate_call_count
        if func.__name__ == "locate_screen":
            locate_call_count += 1
        return None  # build_map path; locate_screen should never be reached

    with patch("asyncio.to_thread", new=_spy_to_thread):
        resp = _post_and_poll(
            client, env.routes,
            {"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200
    body = resp.json()["result"]
    assert body["unmapped"] is True
    assert body["decision"] == "ranked_confirm"
    assert body["chosen"] == []
    assert locate_call_count == 0, "locate_screen must not be called when installation is None"


def test_empty_map_returns_unmapped(client, env, monkeypatch):
    """build_map returns None (no snapshot) → unmapped=True, 200."""
    _seed_prd()
    _mock_installation(monkeypatch)

    async def _fake_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return None
        return None

    with patch("asyncio.to_thread", new=_fake_to_thread):
        resp = _post_and_poll(
            client, env.routes,
            {"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200
    body = resp.json()["result"]
    assert body["unmapped"] is True
    assert body["decision"] == "ranked_confirm"


def test_map_failure_fails_open_no_502(client, env, monkeypatch, caplog):
    """build_map raises → 200 unmapped=True, no 502; map_failed log emitted."""
    _seed_prd()
    _mock_installation(monkeypatch)

    async def _raise_on_build(func, *args, **kwargs):
        if func.__name__ == "build_map":
            raise RuntimeError("network error")
        return None

    with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
        with patch("asyncio.to_thread", new=_raise_on_build):
            resp = _post_and_poll(
                client, env.routes,
                {"prd_id": 1, "github_repo": "org/repo"},
            )

    assert resp.status_code == 200
    poll = resp.json()
    # Map failure fails OPEN to a done unmapped result, NOT an error status.
    assert poll["status"] == "done"
    body = poll["result"]
    assert body["unmapped"] is True
    assert body["decision"] == "ranked_confirm"
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "locate.map_failed" in log_text


def test_locate_llm_failure_polls_error(client, env, monkeypatch):
    """locate_screen raises → poll returns status "error" with a message; the
    endpoint never fabricates a screen. (Was a synchronous 502 before the async
    contract; now the failure is surfaced through the poll, not the request.)"""
    _seed_prd()
    _mock_installation(monkeypatch)
    fake_map, _ = _make_map_result()

    async def _fake_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return fake_map
        # locate_screen path — simulate an API error
        raise RuntimeError("Anthropic API error")

    with patch("asyncio.to_thread", new=_fake_to_thread):
        resp = _post_and_poll(
            client, env.routes,
            {"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200
    poll = resp.json()
    assert poll["status"] == "error"
    assert poll["result"] is None
    # Sanitized: the job record carries the safe class, never the raw provider text.
    assert poll["error"] == "INTERNAL"
    assert "Anthropic API error" not in (poll["error"] or "")


# ── concurrency / observability ───────────────────────────────────────────────


def test_blocking_calls_offloaded_to_thread(client, env, monkeypatch):
    """build_map and locate_screen are both dispatched via asyncio.to_thread."""
    _seed_prd()
    _mock_installation(monkeypatch)
    fake_map, fake_locate = _make_map_result()

    calls: list[str] = []

    async def _spy_to_thread(func, *args, **kwargs):
        calls.append(func.__name__)
        if func.__name__ == "build_map":
            return fake_map
        return fake_locate

    with patch("asyncio.to_thread", new=_spy_to_thread):
        resp = _post_and_poll(
            client, env.routes,
            {"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
    assert "build_map" in calls, f"build_map not dispatched via to_thread; calls={calls}"
    assert "locate_screen" in calls, f"locate_screen not dispatched via to_thread; calls={calls}"


def test_request_log_no_prd_leak(client, env, monkeypatch, caplog):
    """Request log carries identifiers only; PRD body and installation token never logged."""
    _seed_prd(payload_md="CONFIDENTIAL_PRD_BODY_SENTINEL")
    _mock_installation(monkeypatch, installation_id=12345)

    async def _fake_to_thread(func, *args, **kwargs):
        # short-circuit so we only check the request log line, not the full flow
        if func.__name__ == "build_map":
            return None
        return None

    with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
        with patch("asyncio.to_thread", new=_fake_to_thread):
            resp = _post_and_poll(
                client, env.routes,
                {"prd_id": 1, "github_repo": "org/repo"},
            )

    assert resp.status_code == 200
    all_log = " ".join(r.getMessage() for r in caplog.records)
    assert "locate.request" in all_log, "request-start log line missing"
    assert "prd_id=1" in all_log
    assert "org/repo" in all_log
    assert _TEST_COMPANY_ID in all_log
    # No PRD body or token in logs.
    assert "CONFIDENTIAL_PRD_BODY_SENTINEL" not in all_log
    assert "12345" not in all_log  # installation id must not appear


# ── integrity ─────────────────────────────────────────────────────────────────


def test_no_existing_route_broken_imports_clean(env):
    """Reloading the route module succeeds; python -c import is clean."""
    # The reload in env already proves the module imports cleanly.
    import app.routes.design_agent as routes_mod

    # Assert every existing route path is still registered.
    paths = {route.path for route in routes_mod.router.routes}  # type: ignore[attr-defined]
    assert "/v1/design-agent/generate" in paths
    assert "/v1/design-agent/prd-patches" in paths
    assert "/v1/design-agent/figma-files" in paths
    assert "/v1/design-agent/locate" in paths
    assert "/v1/design-agent/locate/jobs/{job_id}" in paths


# ── app-shell signal threading + candidate id carry ───────────────────────────
#
# The locate handler must (a) forward the map's app-shell signal into the gate so
# the spans-routing rescue can fire, and (b) carry each candidate's stable id out
# in the response so the picker can forward it on generate.


def _shell_map_result(*, with_shell: bool = True, shell_id: str = "app-shell"):
    """A MapResult with a routed screen, optionally fronted by a kind='shell'
    app-shell node — mirrors what build_map promotes for a repo with a shell."""
    from app.design_agent.codebase_map.types import MapResult, ScreenNode, ShellModel

    nodes = [
        ScreenNode(
            route="/home", entry_component="HomeScreen",
            composed_components=["Header", "Footer"],
        ),
    ]
    if with_shell:
        nodes.insert(0, ScreenNode(
            route="", entry_component="AppShell", id=shell_id, kind="shell",
            composed_components=["Sidebar", "Topbar"],
        ))
    return MapResult(repo="org/repo", posture="CLEAN", nodes=nodes, shell=ShellModel())  # type: ignore[arg-type]


def _spanning_locate_result(shell_id: str = "app-shell"):
    """A LocateResult whose only no-host candidate spans surfaces, plus an echoed
    app-shell host above the routing-classification floor. The exact case the
    spans-routing rescue exists to admit. Leading confidence stays below the
    auto-proceed threshold so the gate reaches the rescue branch."""
    from app.design_agent.codebase_map.locate import LocateResult, LocateCandidate

    decline = LocateCandidate(
        route="", id="", entry_component="",
        confidence=70, ambiguous=False,
        classification="no-host-decline", spans_multi_surface=True,
        classification_confidence=70,
    )
    shell_host = LocateCandidate(
        route="", id=shell_id, entry_component="AppShell",
        confidence=60, ambiguous=False,
        classification="attach-to-host", spans_multi_surface=False,
        classification_confidence=90,
    )
    return LocateResult(candidates=[decline, shell_host])


def _to_thread_for(map_result, locate_result):
    """Build an asyncio.to_thread replacement that returns the given map for
    build_map and the given locate result for everything else."""
    async def _fake(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return map_result
        return locate_result
    return _fake


def test_locate_passes_app_shell_signal_when_shell_node_present(client, env, monkeypatch):
    """When the map carries a kind='shell' node, the handler calls decide_gate
    with has_app_shell=True and app_shell_node_id == the shell node's id."""
    _seed_prd()
    _mock_installation(monkeypatch)
    from app.design_agent.codebase_map import gate as gate_mod

    captured: dict = {}

    def _spy(result, **kwargs):
        captured.update(kwargs)
        return gate_mod.GateResult(
            decision="ranked_confirm", chosen=[], ranked=[],
            threshold=kwargs.get("threshold") or 80, top_confidence=0,
        )

    monkeypatch.setattr(gate_mod, "decide_gate", _spy)
    fake_map = _shell_map_result(with_shell=True, shell_id="app-shell")
    fake_locate = _spanning_locate_result()

    with patch("asyncio.to_thread", new=_to_thread_for(fake_map, fake_locate)):
        resp = _post_and_poll(
            client, env.routes,
            {"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200, resp.text
    assert captured["has_app_shell"] is True
    assert captured["app_shell_node_id"] == "app-shell"


def test_locate_passes_no_app_shell_when_absent(client, env, monkeypatch):
    """No shell node in the map → has_app_shell=False; app_shell_node_id falls
    back to the module default id."""
    _seed_prd()
    _mock_installation(monkeypatch)
    from app.design_agent.codebase_map import gate as gate_mod
    from app.design_agent.codebase_map.shell import APP_SHELL_NODE_ID

    captured: dict = {}

    def _spy(result, **kwargs):
        captured.update(kwargs)
        return gate_mod.GateResult(
            decision="ranked_confirm", chosen=[], ranked=[],
            threshold=80, top_confidence=0,
        )

    monkeypatch.setattr(gate_mod, "decide_gate", _spy)
    fake_map = _shell_map_result(with_shell=False)
    fake_locate = _spanning_locate_result()

    with patch("asyncio.to_thread", new=_to_thread_for(fake_map, fake_locate)):
        resp = _post_and_poll(
            client, env.routes,
            {"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200, resp.text
    assert captured["has_app_shell"] is False
    assert captured["app_shell_node_id"] == APP_SHELL_NODE_ID


def test_spans_routing_fires_through_route(client, env, monkeypatch):
    """With a shell node present (has_app_shell derived True), the real gate
    rescues the spanning decline → proceed_with_note attached to the app-shell
    host. The SAME locate result with NO shell node (has_app_shell False) declines
    to ranked_confirm — proving the route now forwards the signal it used to drop."""
    _seed_prd()
    _mock_installation(monkeypatch)
    fake_locate = _spanning_locate_result(shell_id="app-shell")

    # Shell present → rescued to the app-shell host.
    with patch(
        "asyncio.to_thread",
        new=_to_thread_for(_shell_map_result(with_shell=True), fake_locate),
    ):
        resp = _post_and_poll(
            client, env.routes,
            {"prd_id": 1, "github_repo": "org/repo"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()["result"]
    assert body["decision"] == "proceed_with_note"
    assert len(body["chosen"]) == 1
    assert body["chosen"][0]["id"] == "app-shell"
    assert body["chosen"][0]["route"] == ""

    # Control: no shell node → has_app_shell False → genuine decline (pre-fix
    # behaviour, which the route always produced because it never forwarded the signal).
    with patch(
        "asyncio.to_thread",
        new=_to_thread_for(_shell_map_result(with_shell=False), fake_locate),
    ):
        resp2 = _post_and_poll(
            client, env.routes,
            {"prd_id": 1, "github_repo": "org/repo"},
        )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["result"]["decision"] == "ranked_confirm"


def test_locate_response_carries_candidate_id(client, env, monkeypatch):
    """/locate's chosen + ranked candidates each surface the stable id from the
    underlying candidate (routed id, section id, app-shell id), and component_count
    is resolved BY ID so non-route hosts (empty/shared route) count correctly."""
    _seed_prd()
    _mock_installation(monkeypatch)
    from app.design_agent.codebase_map.types import MapResult, ScreenNode, ShellModel
    from app.design_agent.codebase_map.locate import LocateResult, LocateCandidate

    nodes = [
        ScreenNode(
            route="", entry_component="AppShell", id="app-shell", kind="shell",
            composed_components=["Sidebar"],  # 1
        ),
        ScreenNode(
            route="/inbox", entry_component="InboxScreen",
            composed_components=["List", "Row", "Toolbar"],  # 3
        ),
        ScreenNode(
            route="", entry_component="InboxArchived", id="inbox#archived", kind="section",
            composed_components=["List", "ArchiveBanner"],  # 2 — distinct from the shell's 1
        ),
    ]
    fake_map = MapResult(repo="org/repo", posture="CLEAN", nodes=nodes, shell=ShellModel())  # type: ignore[arg-type]
    cands = [
        LocateCandidate(route="/inbox", id="/inbox", entry_component="InboxScreen",
                        confidence=90, classification_confidence=90),
        LocateCandidate(route="", id="inbox#archived", entry_component="InboxArchived",
                        confidence=85, classification_confidence=85),
        LocateCandidate(route="", id="app-shell", entry_component="AppShell",
                        confidence=82, classification_confidence=82),
    ]
    fake_locate = LocateResult(candidates=cands)

    with patch("asyncio.to_thread", new=_to_thread_for(fake_map, fake_locate)):
        resp = _post_and_poll(
            client, env.routes,
            {"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()["result"]
    ranked_ids = [c["id"] for c in body["ranked"]]
    assert ranked_ids == ["/inbox", "inbox#archived", "app-shell"]
    by_id = {c["id"]: c for c in body["ranked"]}
    # By-id match: the section (route="") counts its own 2 components, NOT the
    # shell's 1 — a route-only match would have collided on the empty route.
    assert by_id["inbox#archived"]["component_count"] == 2
    assert by_id["app-shell"]["component_count"] == 1
    assert by_id["/inbox"]["component_count"] == 3
    # Leading high-confidence single node → auto_proceed → chosen carries its id.
    assert body["chosen"][0]["id"] == "/inbox"


def test_no_prohibited_tokens_in_route_changes():
    """No internal coordinates in this fix's new regression code (this module +
    the generate-wiring module it extends)."""
    pattern = re.compile("|".join([
        r"C[0-9]+-[0-9]+", "C" + "-series", r"H[0-9]+-[0-9]+", r"P[0-9]+-[0-9]+",
        r"\bAD[0-9]+", r"\bF[0-9]{1,2}\b", "D" + "BD", "Babaj" + "ide",
    ]))
    here = Path(__file__).resolve()
    targets = [here, here.parent / "test_design_agent_generate_locate_wiring.py"]
    for path in targets:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            assert not pattern.search(line), (
                f"prohibited token in {path.name}:{lineno}: {line}"
            )


def test_no_prohibited_tokens_in_appended_lines():
    """Appended lines in route/api files and new test files contain no internal coordinates."""
    _PATTERN = re.compile("|".join([
        r"C[0-9]+-[0-9]+", "C" + "-series", r"H[0-9]+-[0-9]+", r"P[0-9]+-[0-9]+",
        r"\bAD[0-9]+", r"\bF[0-9]{1,2}\b", "D" + "BD", "Babaj" + "ide", r"\bspike\b",
    ]))
    repo_root = Path(__file__).parents[2]  # backend/tests → backend → repo root
    files_to_check = [
        # New files: check entire content.
        Path(__file__),  # this test file
        repo_root / "web" / "app" / "lib" / "__tests__" / "designAgentLocate.test.ts",
    ]
    for path in files_to_check:
        content = path.read_text()
        matches = _PATTERN.findall(content)
        assert not matches, f"{path.name}: prohibited tokens found: {matches}"

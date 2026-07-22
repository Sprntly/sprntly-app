"""Tests for the iterate spine (P3-05):

    DESIGN_AGENT_ITERATE_SYSTEM + render_iterate_user   (prompts.py)
    iterate_prototype                                   (runner.py)
    POST /v1/design-agent/{id}/iterate + _run_iterate_bg + _stage_iterate_run
                                                        (routes/design_agent.py)

Three test layers, matching the ticket's Unit Tests section:

- PROMPT — pure assertions on the iterate system prompt + the cache-breakpoint
  discipline of render_iterate_user (no fixtures).
- RUNNER — iterate_prototype against the recording fake Anthropic client (reused
  shape from test_design_agent_runner.py): virtual_fs pre-population, the iterate
  cost-log, cache-read accounting, max_iters bound, execute-mode passthrough.
- ROUTE — the HTTP surface + the background task + the iterate staging path
  against the in-memory FakeSupabaseClient (same fixture shape as
  test_design_agent_comment_routes.py).

Load-bearing assertions: AC3/AC4 (cache_control at the END of the stable prefix,
never on the volatile prompt block), AC6a (the iterate staging path NEVER calls
complete_prototype), AC6b (source loaded from current_checkpoint via positional
read_source_files_for_checkpoint; mode threaded as 'execute', not 'iterate').
"""
from __future__ import annotations

import asyncio
import base64
import copy
import importlib
import logging
import types
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.design_agent import runner
from app.design_agent.prompts import (
    DESIGN_AGENT_ITERATE_SYSTEM,
    DESIGN_AGENT_SCAFFOLD_SYSTEM,
    DESIGN_AGENT_TEMPLATE_VERSION,
    render_iterate_user,
)

from tests.conftest import _TEST_COMPANY_ID
from tests._fake_anthropic import _FakeStream

TELEMETRY_LOGGER = "app.llm_telemetry"


# ═══════════════════════════════════════════════════════════════════════════
# Layer 1 — Prompt + render_iterate_user (pure)
# ═══════════════════════════════════════════════════════════════════════════


def test_iterate_system_distinct_from_scaffold():
    # AD8 / AC1: the iterate prompt is a SEPARATE prompt, not a copy of scaffold.
    assert DESIGN_AGENT_ITERATE_SYSTEM != DESIGN_AGENT_SCAFFOLD_SYSTEM
    assert DESIGN_AGENT_ITERATE_SYSTEM.strip()  # non-empty


def test_iterate_system_renders_shadcn_inventory():
    # AC1: the {shadcn_inventory} placeholder was rendered (not left literal).
    assert "shadcn/ui components" in DESIGN_AGENT_ITERATE_SYSTEM
    assert "{shadcn_inventory}" not in DESIGN_AGENT_ITERATE_SYSTEM
    # Iterate-specific framing distinguishes it from scaffold.
    assert "ITERATING" in DESIGN_AGENT_ITERATE_SYSTEM


def test_template_version_is_current():
    # Now 9 — the mobile-capability platform directives (template-invalidating).
    assert DESIGN_AGENT_TEMPLATE_VERSION == 9


def test_render_iterate_user_cache_on_last_stable_block():
    # AC3: cache_control ephemeral 1h on the LAST cacheable block; NONE on volatile.
    cacheable, volatile = render_iterate_user(
        current_source={"src/App.tsx": "export default function App(){}"},
        open_comments=[{"anchor_id": "a1", "body": "make it bold", "author": "demo"}],
        iterate_prompt="change the title",
        applied_comment=None,
    )
    assert cacheable[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "cache_control" not in volatile


def test_render_iterate_user_prompt_only_in_volatile_block():
    # AC3: the iterate prompt appears ONLY below the breakpoint; the bundle source
    # + open comments appear ONLY above it.
    cacheable, volatile = render_iterate_user(
        current_source={"src/App.tsx": "SOURCE_MARKER_XYZ"},
        open_comments=[{"anchor_id": "a1", "body": "COMMENT_MARKER_QRS", "author": "demo"}],
        iterate_prompt="PROMPT_MARKER_ABC",
        applied_comment=None,
    )
    cache_text = " ".join(b["text"] for b in cacheable)
    assert "PROMPT_MARKER_ABC" not in cache_text       # prompt NOT cached
    assert "SOURCE_MARKER_XYZ" in cache_text            # source IS cached
    assert "COMMENT_MARKER_QRS" in cache_text           # open comment IS cached
    assert "PROMPT_MARKER_ABC" in volatile["text"]      # prompt IS in volatile suffix
    assert "SOURCE_MARKER_XYZ" not in volatile["text"]  # source not duplicated below


def test_render_iterate_user_applied_comment_threads_anchor_and_body():
    # F10 / AC9: an applied comment's anchor + body ride in the volatile block.
    _cacheable, volatile = render_iterate_user(
        current_source={},
        open_comments=[],
        iterate_prompt="and make it bigger",
        applied_comment={"anchor_id": "deadbeef", "body": "APPLIED_BODY_123"},
    )
    assert "APPLIED_BODY_123" in volatile["text"]
    assert "deadbeef" in volatile["text"]
    assert "and make it bigger" in volatile["text"]


def test_apply_general_renders_prototype_level_prompt():
    # Applying a general (null-anchor) comment must steer the agent as
    # whole-prototype feedback, never a bogus element reference. Before this
    # branch existed, a null anchor_id rendered the literal
    # data-anchor-id="None" here.
    _cacheable, volatile = render_iterate_user(
        current_source={},
        open_comments=[],
        iterate_prompt="ship it",
        applied_comment={"anchor_id": None, "body": "GENERAL_FEEDBACK_456"},
    )
    assert "GENERAL_FEEDBACK_456" in volatile["text"]
    assert "entire prototype" in volatile["text"]
    assert "data-anchor-id" not in volatile["text"]
    assert "None" not in volatile["text"]


def test_apply_pinned_still_element_targeted():
    # Regression: a real-anchor applied comment keeps the exact prior
    # element-anchored wording -- applying a pinned comment must not regress
    # toward the new whole-prototype branch.
    _cacheable, volatile = render_iterate_user(
        current_source={},
        open_comments=[],
        iterate_prompt="tweak spacing",
        applied_comment={"anchor_id": "fb3007b5", "body": "PINNED_FEEDBACK_789"},
    )
    assert 'data-anchor-id="fb3007b5"' in volatile["text"]
    assert "PINNED_FEEDBACK_789" in volatile["text"]
    assert "entire prototype" not in volatile["text"]


def test_iterate_prefix_includes_screenshot_block_cache_stable():
    # The reference-screenshot image block joins the CACHEABLE prefix as the
    # LAST stable block, and the cache breakpoint MOVES onto it (a breakpoint
    # left mid-prefix would silently re-bill the image every turn).
    shot = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "QUJDRA=="},
    }
    kwargs = dict(
        current_source={"src/App.tsx": "export default function App(){}"},
        open_comments=[],
        iterate_prompt="change the title",
        applied_comment=None,
    )
    c1, v1 = render_iterate_user(**kwargs, screenshot_block=shot)
    c2, v2 = render_iterate_user(**kwargs, screenshot_block=shot)

    assert c1[-1]["type"] == "image"
    assert c1[-1]["source"]["media_type"] == "image/png"
    assert c1[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # The breakpoint moved OFF the text block — exactly one breakpoint, at the
    # END of the stable prefix; none on the volatile suffix.
    assert all("cache_control" not in b for b in c1[:-1])
    assert "cache_control" not in v1
    # Prefix-stable: two renders are byte-identical (the cache hit across turns).
    assert c1 == c2 and v1 == v2
    # The caller's block is never mutated.
    assert "cache_control" not in shot

    # No screenshot → the single text block keeps the breakpoint, byte-identical
    # to the pre-screenshot shape.
    c3, _v3 = render_iterate_user(**kwargs)
    assert [b["type"] for b in c3] == ["text"]
    assert c3[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


# ═══════════════════════════════════════════════════════════════════════════
# Layer 2 — iterate_prototype runner entry (recording fake client)
# ═══════════════════════════════════════════════════════════════════════════


class _FakeBlock:
    def __init__(self, data: dict):
        self._data = data

    def model_dump(self) -> dict:
        return copy.deepcopy(self._data)


class _FakeMessage:
    def __init__(self, stop_reason, blocks, usage):
        self.stop_reason = stop_reason
        self.content = [_FakeBlock(b) for b in blocks]
        self.usage = usage


class _RecordingClient:
    """Sync messages.create replaying a list of responses; last entry replays."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = types.SimpleNamespace(create=self._create, stream=self._stream)

    def _create(self, **kwargs):
        self.calls.append({
            "system": kwargs.get("system"),
            "messages": copy.deepcopy(kwargs.get("messages")),
            "model": kwargs.get("model"),
            "max_tokens": kwargs.get("max_tokens"),
            "tools": kwargs.get("tools"),
        })
        i = len(self.calls) - 1
        resp = self._responses[i] if i < len(self._responses) else self._responses[-1]
        if isinstance(resp, BaseException):
            raise resp
        return resp

    def _stream(self, **kwargs):
        return _FakeStream(self._create(**kwargs))


def _usage(cache_creation=0, cache_read=0, inp=0, out=0):
    return types.SimpleNamespace(
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        input_tokens=inp,
        output_tokens=out,
    )


def _msg(stop_reason, blocks=None, usage=None):
    return _FakeMessage(stop_reason, blocks or [], usage or _usage())


def _text(s: str) -> dict:
    return {"type": "text", "text": s}


def _tool_use(id: str, name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": id, "name": name, "input": inp}


def _system():
    return [
        {"type": "text", "text": "You are the Design Agent, iterating."},
        {
            "type": "text",
            "text": "<iterate system + tool defs — the stable prefix>",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    ]


def _user(text: str = "Make the CTA blue."):
    return {"role": "user", "content": [_text(text)]}


def _install_client(monkeypatch, responses) -> _RecordingClient:
    client = _RecordingClient(responses)
    monkeypatch.setattr(runner, "get_design_agent_client", lambda: client)
    return client


def _run(coro):
    return asyncio.run(coro)


def test_iterate_prototype_prepopulates_virtual_fs(monkeypatch):
    # AC5: a `view` of an existing (seeded) file returns content, not a not-found
    # is_error.
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "src/App.tsx"})]),
        _msg("end_turn", [_text("done")]),
    ])
    result, vfs = _run(runner.iterate_prototype(
        prototype_id=1, workspace_id=_TEST_COMPANY_ID,
        system_blocks=_system(), user_message=_user(),
        current_source={"src/App.tsx": "export default function App(){ return <div>HELLO</div> }"},
        figma_file_key=None,
    ))
    assert result.status == "complete"
    tr = client.calls[1]["messages"][-1]["content"][0]
    assert tr["type"] == "tool_result"
    assert tr.get("is_error") is not True          # the file was found
    assert "HELLO" in tr["content"]                 # its content surfaced through view
    assert "src/App.tsx" in vfs                      # the seed survived in the returned fs


def test_iterate_prototype_threads_execute_mode_and_seeds_ctx(monkeypatch):
    # AC6b: mode threaded to agent_loop as the canonical 'execute' (NOT 'iterate');
    # ctx.virtual_fs pre-seeded with current_source (AC5 at the ctx level).
    captured: dict = {}

    async def fake_loop(*, system_blocks, user_message, ctx, scenario, mode):
        captured["mode"] = mode
        captured["ctx"] = ctx
        return runner.RunResult(status="complete", iters=1, usage=runner.RunUsage(),
                                duration_ms=1, final_content=[])

    monkeypatch.setattr(runner, "agent_loop", fake_loop)
    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda key, ws: None)
    _run(runner.iterate_prototype(
        prototype_id=1, workspace_id=_TEST_COMPANY_ID, system_blocks=_system(), user_message=_user(),
        current_source={"src/App.tsx": "x"}, figma_file_key=None,
    ))
    assert captured["mode"] == "execute"
    assert captured["mode"] != "iterate"
    assert captured["ctx"].virtual_fs == {"src/App.tsx": "x"}


def test_iterate_prototype_emits_iterate_cost_log(monkeypatch, caplog):
    # AC6: cost-summary via log_llm_run with operation iterate + mode=iterate.
    _install_client(monkeypatch, [
        _msg("end_turn", [_text("done")], usage=_usage(cache_read=10, inp=100, out=50)),
    ])
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        result, _vfs = _run(runner.iterate_prototype(
            prototype_id=77, workspace_id=_TEST_COMPANY_ID, system_blocks=_system(), user_message=_user(),
            current_source={}, figma_file_key=None, scenario="A",
        ))
    assert result.status == "complete"
    records = [r for r in caplog.records if r.name == TELEMETRY_LOGGER]
    assert len(records) == 1
    msg = records[0].getMessage()
    assert "design_agent.run.iterate" in msg
    assert "prototype_id=77" in msg
    assert "scenario=A" in msg
    assert "mode=iterate" in msg
    for field in ("cached_input_tokens=", "input_tokens=", "output_tokens=",
                  "duration_ms=", "est_cost_usd=", "status=complete", "iters="):
        assert field in msg, f"missing {field!r}"


def test_iterate_cost_log_carries_no_pii_or_content(monkeypatch, caplog):
    # AC12: the cost-summary line carries identifiers + token counts only — never
    # the system prompt body, the user/comment content, or the source.
    _install_client(monkeypatch, [_msg("end_turn", [_text("done")])])
    system_blocks = [
        {"type": "text", "text": "SECRET_ITERATE_SYSTEM_BODY"},
        {"type": "text", "text": "tools", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]
    user_message = {"role": "user", "content": [_text("COMMENT_PII jane.doe@example.com")]}
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        _run(runner.iterate_prototype(
            prototype_id=1, workspace_id=_TEST_COMPANY_ID, system_blocks=system_blocks,
            user_message=user_message, current_source={"f.tsx": "SECRET_SOURCE_BODY"},
            figma_file_key=None,
        ))
    msg = next(r.getMessage() for r in caplog.records if r.name == TELEMETRY_LOGGER)
    assert "SECRET_ITERATE_SYSTEM_BODY" not in msg
    assert "jane.doe@example.com" not in msg
    assert "SECRET_SOURCE_BODY" not in msg


def test_iterate_prototype_cache_read_nonzero_on_second_call(monkeypatch):
    # AC4: the second create call within the window reports cache_read tokens,
    # accumulated into RunUsage.
    _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "src/A.tsx"})], usage=_usage(inp=500, out=100)),
        _msg("end_turn", [_text("done")], usage=_usage(cache_read=120, inp=10, out=20)),
    ])
    result, _vfs = _run(runner.iterate_prototype(
        prototype_id=1, workspace_id=_TEST_COMPANY_ID, system_blocks=_system(), user_message=_user(),
        current_source={"src/A.tsx": "x"}, figma_file_key=None,
    ))
    assert result.usage.cache_read_input_tokens >= 120


def test_iterate_prototype_honours_max_iters(monkeypatch):
    # AC10: a runaway (always tool_use) exits at the loop cap with status=max_iters,
    # no exception.
    _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})]),  # replayed forever
    ])
    result, _vfs = _run(runner.iterate_prototype(
        prototype_id=1, workspace_id=_TEST_COMPANY_ID, system_blocks=_system(), user_message=_user(),
        current_source={}, figma_file_key=None,
    ))
    assert result.status == "max_iters"
    assert result.iters == runner.DEFAULT_MAX_ITERS


# ═══════════════════════════════════════════════════════════════════════════
# Layer 3 — Route + background task + staging (FakeSupabaseClient)
# ═══════════════════════════════════════════════════════════════════════════

# SQLite-compatible end-state of prototypes (P1-06 + P2-06 sharing/lock columns) +
# prototype_checkpoints + prototype_comments (P3-01). Mirrors
# test_design_agent_comment_routes.py.
_DDL = """
CREATE TABLE prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    variant                TEXT NOT NULL DEFAULT 'v1',
    template_version       INTEGER NOT NULL,
    instructions           TEXT,
    target_platform        TEXT NOT NULL DEFAULT 'both',
    figma_file_key         TEXT,
    website_url            TEXT,
    github_installation_id INTEGER,
    screenshot_key         TEXT,
    bundle_url             TEXT,
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT,
    is_complete            INTEGER NOT NULL DEFAULT 0,
    complete_checkpoint_id INTEGER
);
CREATE TABLE prototype_checkpoints (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id      INTEGER NOT NULL,
    workspace_id      TEXT NOT NULL,
    bundle_url        TEXT,
    prd_revision_hash TEXT,
    figma_frame_hash  TEXT,
    prompt_history    TEXT NOT NULL DEFAULT '[]',
    comment_state     TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE prototype_comments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id  INTEGER NOT NULL,
    workspace_id  TEXT NOT NULL,
    anchor_id     TEXT NOT NULL,
    body          TEXT NOT NULL,
    author        TEXT NOT NULL DEFAULT 'demo',
    status        TEXT NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open', 'resolved', 'orphaned')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT,
    user_id        TEXT
);
-- P3-06: POST /iterate now enqueues into the message queue, so the route tests
-- below need this table in the fake schema (the handler no longer fires a raw
-- bg task). The _run_iterate_bg unit tests call the body directly and don't.
CREATE TABLE prototype_pending_iterations (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id       INTEGER NOT NULL,
    workspace_id       TEXT NOT NULL,
    prompt             TEXT NOT NULL,
    applied_comment_id INTEGER,
    mode               TEXT NOT NULL DEFAULT 'execute',
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'running', 'done', 'failed')),
    error              TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    started_at         TEXT,
    finished_at        TEXT
);
"""


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototype tables (incl. comments) + feature flag ON,
    with the design agent module stack reloaded in dependency order."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.prototype_comments as comments_mod
    importlib.reload(comments_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    return SimpleNamespace(proto=proto_mod, comments=comments_mod, routes=routes_mod, main=main_mod)


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company) — see conftest.company_client."""
    return company_client


@pytest.fixture
def unauth(env) -> TestClient:
    return TestClient(env.main.app)


# ─── helpers ────────────────────────────────────────────────────────────────


def _seed_ready(env, *, workspace_id: str = _TEST_COMPANY_ID, current_checkpoint_id=None) -> int:
    """Insert a ready, unlocked prototype (status='ready', is_complete=0)."""
    pid = env.proto.start_prototype(prd_id=1, workspace_id=workspace_id, template_version=1)
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id=workspace_id,
        bundle_url="https://bundle/original", current_checkpoint_id=current_checkpoint_id,
    )
    return pid


def _seed_locked(env, *, workspace_id: str = _TEST_COMPANY_ID) -> int:
    """A ready prototype that has been Marked Complete (is_complete=1, F14 lock)."""
    pid = _seed_ready(env, workspace_id=workspace_id, current_checkpoint_id=7)
    env.proto.mark_complete(prototype_id=pid, workspace_id=workspace_id)
    return pid


def _stub_iterate(monkeypatch, routes_mod):
    """Patch routes.iterate_prototype (+ the source read) so a fired bg task does
    no real LLM/storage work. Returns a non-complete result so no staging runs."""
    async def _fake(**kwargs):
        return SimpleNamespace(status="error", iters=1, error_message=None, error_class=None), {}

    async def _fake_read(prototype_id, checkpoint_id):
        return {}

    monkeypatch.setattr(routes_mod, "iterate_prototype", _fake)
    monkeypatch.setattr(routes_mod, "read_source_files_for_checkpoint", _fake_read)


def _stub_iterate_capture(monkeypatch, routes_mod) -> dict:
    """Patch routes.iterate_prototype to record its kwargs; return the dict."""
    captured: dict = {}

    async def _fake(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status="error", iters=1, error_message=None, error_class=None), {}

    monkeypatch.setattr(routes_mod, "iterate_prototype", _fake)
    return captured


# ─── Route status codes (AC7, AC8) ─────────────────────────────────────────


def test_post_iterate_ready_returns_generating(env, client, monkeypatch):
    # AC7: ready + unlocked → {prototype_id, status: 'generating'} + bg task fired.
    _stub_iterate(monkeypatch, env.routes)
    pid = _seed_ready(env)
    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "make the button blue"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prototype_id"] == pid
    assert body["status"] == "generating"


def test_post_iterate_locked_returns_409(env, client):
    # AC8: a Marked-Complete (locked) prototype → 409 "locked; Resume first".
    pid = _seed_locked(env)
    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "x"})
    assert resp.status_code == 409
    assert "locked" in resp.json()["detail"].lower()


def test_post_iterate_not_ready_returns_409(env, client):
    # AC8: a generating prototype (status != ready) → 409.
    pid = env.proto.start_prototype(prd_id=1, workspace_id=_TEST_COMPANY_ID, template_version=1)  # 'generating'
    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "x"})
    assert resp.status_code == 409


def test_post_iterate_accepts_failed_prototype_with_bundle(env, client, monkeypatch):
    # AC9: a 'failed' row that still has a bundle (a LATER iterate/manual-edit
    # failed, not the first generation) is recoverable — the ordinary composer's
    # resubmit must enqueue normally (200), not 409, mirroring get_active_by_prd's
    # reveal condition.
    _stub_iterate(monkeypatch, env.routes)
    pid = _seed_ready(env)
    env.proto.fail_prototype(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        error="iterate agent_loop ended with status=error iters=1 | error_class=PROVIDER_BILLING",
    )
    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "try again"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prototype_id"] == pid
    assert body["status"] == "generating"


def test_post_iterate_still_rejects_failed_prototype_without_bundle(env, client):
    # AC10: a 'failed' row that never succeeded at all (no bundle_url) is NOT
    # recoverable — still 409s, unchanged from today.
    pid = env.proto.start_prototype(prd_id=1, workspace_id=_TEST_COMPANY_ID, template_version=1)
    env.proto.fail_prototype(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        error="build agent_loop ended with status=error iters=1 | error_class=ViteBuildError",
    )
    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "x"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_recovery_iterate_success_resets_status_to_ready(env, monkeypatch):
    # AC11: a recovery iterate (started from a 'failed'-with-bundle row) that
    # SUCCEEDS must flip status back to 'ready' — the row fully exits the
    # "revealed via the broadened active-lookup" branch and becomes a normal
    # ready row again, not stuck at 'failed' forever despite succeeding.
    async def fake_vite(vfs):
        return {"index.html": "<html></html>"}

    async def fake_stage(*, prototype_id, checkpoint_id, files, sub_prefix=None):
        return "https://bundle/recovered"

    monkeypatch.setattr(env.routes, "vite_build", fake_vite)
    monkeypatch.setattr(env.routes, "stage_bundle", fake_stage)
    monkeypatch.setattr(env.routes, "create_checkpoint", lambda **k: 777)

    pid = _seed_ready(env)
    env.proto.fail_prototype(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        error="iterate agent_loop ended with status=error iters=1 | error_class=PROVIDER_BILLING",
    )
    assert env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)["status"] == "failed"

    await env.routes._stage_iterate_run(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        virtual_fs={"a.tsx": "x"}, iterate_prompt="try again",
        recovering_from_failure=True,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["status"] == "ready"
    assert row["current_checkpoint_id"] == 777


@pytest.mark.asyncio
async def test_ordinary_iterate_success_does_not_rewrite_status(env, monkeypatch):
    # AC12: an ORDINARY iterate (started from an already-'ready' row,
    # recovering_from_failure left at its default False) must NOT redundantly
    # rewrite status — pinned so this fix cannot regress the overwhelmingly
    # common case. Spies on advance_current_checkpoint's own kwargs to prove
    # recovered_from_failure=False travelled all the way from the default.
    async def fake_vite(vfs):
        return {"index.html": "<html></html>"}

    async def fake_stage(*, prototype_id, checkpoint_id, files, sub_prefix=None):
        return "https://bundle/iterated"

    advance_calls: list = []
    real_advance = env.proto.advance_current_checkpoint

    def spy_advance(**kwargs):
        advance_calls.append(kwargs)
        return real_advance(**kwargs)

    monkeypatch.setattr(env.routes, "vite_build", fake_vite)
    monkeypatch.setattr(env.routes, "stage_bundle", fake_stage)
    monkeypatch.setattr(env.routes, "create_checkpoint", lambda **k: 888)
    monkeypatch.setattr(env.routes, "advance_current_checkpoint", spy_advance)

    pid = _seed_ready(env)

    await env.routes._stage_iterate_run(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        virtual_fs={"a.tsx": "x"}, iterate_prompt="make it blue",
    )
    assert advance_calls[0]["recovered_from_failure"] is False
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["status"] == "ready"


def test_post_iterate_wrong_workspace_returns_404(env, client):
    # AC8: a prototype in a foreign workspace is invisible (404, not 403).
    pid = _seed_ready(env, workspace_id="demo")
    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "x"})
    assert resp.status_code == 404


def test_post_iterate_requires_session(env, unauth):
    # AC8: no app session → 401.
    resp = unauth.post("/v1/design-agent/1/iterate", json={"prompt": "x"})
    assert resp.status_code == 401


def test_post_iterate_empty_prompt_returns_422(env, client):
    # Pydantic min_length=1 on prompt.
    pid = _seed_ready(env)
    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": ""})
    assert resp.status_code == 422


def test_post_iterate_feature_flag_off_returns_404(env, client, monkeypatch):
    # Gate parity with /generate: invisible (404) when the flag is off.
    pid = _seed_ready(env)
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "x"})
    assert resp.status_code == 404


def test_post_iterate_emits_started_log(env, client, monkeypatch, caplog):
    # AC12: a prototype_iterate_started INFO line at kickoff.
    _stub_iterate(monkeypatch, env.routes)
    pid = _seed_ready(env)
    with caplog.at_level(logging.INFO):
        resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "x"})
    assert resp.status_code == 200, resp.text
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert f"prototype_iterate_started prototype_id={pid}" in blob


# ─── Background task: source load + applied-comment merge (S2, AC9, AC6b) ───


@pytest.mark.asyncio
async def test_run_iterate_bg_loads_source_from_current_checkpoint(env, monkeypatch):
    # S2 / AC6b: get_prototype FIRST → read_source_files_for_checkpoint(pid, cid)
    # POSITIONAL → current_source threaded to iterate_prototype; mode='execute'.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    captured = _stub_iterate_capture(monkeypatch, env.routes)
    read_calls: list[tuple] = []

    async def fake_read(prototype_id, checkpoint_id):
        read_calls.append((prototype_id, checkpoint_id))
        return {"src/App.tsx": "export default function App(){}"}

    monkeypatch.setattr(env.routes, "read_source_files_for_checkpoint", fake_read)
    pid = _seed_ready(env, current_checkpoint_id=42)

    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        body=env.routes.IterateRequest(prompt="tweak the header"),
    )
    assert read_calls == [(pid, 42)]  # positional (prototype_id, checkpoint_id)
    assert captured["current_source"] == {"src/App.tsx": "export default function App(){}"}
    assert captured["mode"] == "execute"


@pytest.mark.asyncio
async def test_run_iterate_bg_merges_applied_comment(env, monkeypatch):
    # AC9: applied_comment_id → comment body merged into the agent prompt +
    # anchor_id passed to the agent (inspect the assembled user_message).
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    captured = _stub_iterate_capture(monkeypatch, env.routes)
    pid = _seed_ready(env)  # no current_checkpoint → source read skipped
    c = env.comments.insert_comment(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        anchor_id="a1b2c3d4", body="Make this CTA larger", author="demo",
    )

    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        body=env.routes.IterateRequest(prompt="and center it", applied_comment_id=c["id"]),
    )
    user_message = captured["user_message"]
    blob = " ".join(b["text"] for b in user_message["content"])
    assert "Make this CTA larger" in blob   # comment body merged
    assert "a1b2c3d4" in blob               # anchor passed to the agent
    assert "and center it" in blob          # the iterate prompt itself


@pytest.mark.asyncio
async def test_run_iterate_bg_open_comments_in_cacheable_prefix(env, monkeypatch):
    # The open comment threads ride in the cacheable prefix; a resolved comment is
    # excluded (only status='open' feeds the prompt).
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    captured = _stub_iterate_capture(monkeypatch, env.routes)
    pid = _seed_ready(env)
    env.comments.insert_comment(prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
                                anchor_id="open1", body="OPEN_COMMENT_BODY", author="demo")
    resolved = env.comments.insert_comment(prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
                                           anchor_id="res1", body="RESOLVED_COMMENT_BODY", author="demo")
    env.comments.resolve_comment(comment_id=resolved["id"], workspace_id=_TEST_COMPANY_ID)

    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        body=env.routes.IterateRequest(prompt="do the thing"),
    )
    blocks = captured["user_message"]["content"]
    # The cacheable prefix is every block except the last (volatile) one.
    cache_blob = " ".join(b["text"] for b in blocks[:-1])
    assert "OPEN_COMMENT_BODY" in cache_blob
    assert "RESOLVED_COMMENT_BODY" not in cache_blob


# ─── Screenshot design-reference re-entry ──────────────────────────────────


def _seed_ready_with_screenshot(env, key: str) -> int:
    """A ready prototype whose row carries a stored reference-screenshot key."""
    pid = env.proto.start_prototype(
        prd_id=1, workspace_id=_TEST_COMPANY_ID, template_version=1,
        screenshot_key=key,
    )
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        bundle_url="https://bundle/original", current_checkpoint_id=None,
    )
    return pid


@pytest.mark.asyncio
async def test_iterate_reentry_attaches_screenshot_in_cacheable_prefix(env, monkeypatch):
    # Re-entry: an execute iterate on a screenshot-carrying prototype re-attaches
    # the SAME stored image inside the cacheable prefix; the breakpoint rides the
    # image block (the last stable block), never the volatile prompt.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    key = f"uploads/{_TEST_COMPANY_ID}/cafebabe.png"
    pid = _seed_ready_with_screenshot(env, key)
    captured = _stub_iterate_capture(monkeypatch, env.routes)
    reads: list[tuple] = []

    # Stub read_screenshot SEPARATELY from the seed-source read (convention:
    # never overload the seed stub with the screenshot read).
    async def _fake_shot_read(*, key, workspace_id):
        reads.append((key, workspace_id))
        return b"\x89PNG-fake-bytes", "image/png"

    monkeypatch.setattr(env.routes, "read_screenshot", _fake_shot_read)

    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        body=env.routes.IterateRequest(prompt="tweak the header"),
    )
    assert reads == [(key, _TEST_COMPANY_ID)]
    blocks = captured["user_message"]["content"]
    # Volatile prompt block last + uncached; the image is the LAST STABLE block.
    assert blocks[-1]["type"] == "text" and "cache_control" not in blocks[-1]
    image = blocks[-2]
    assert image["type"] == "image"
    assert image["source"]["media_type"] == "image/png"
    assert image["source"]["data"] == base64.b64encode(b"\x89PNG-fake-bytes").decode("ascii")
    assert image["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # Exactly one breakpoint in the user content — on the image.
    assert sum(1 for b in blocks if "cache_control" in b) == 1


@pytest.mark.asyncio
async def test_plan_run_attaches_screenshot_too(env, monkeypatch):
    # Plan runs get the same design reference (the plan should be grounded in
    # what the prototype is supposed to look like).
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    key = f"uploads/{_TEST_COMPANY_ID}/0badf00d.png"
    pid = _seed_ready_with_screenshot(env, key)
    captured = _stub_iterate_capture(monkeypatch, env.routes)

    async def _fake_shot_read(*, key, workspace_id):
        return b"plan-shot", "image/jpeg"

    monkeypatch.setattr(env.routes, "read_screenshot", _fake_shot_read)

    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        body=env.routes.IterateRequest(prompt="how would you restructure this?", mode="plan"),
    )
    assert captured["mode"] == "plan"
    images = [b for b in captured["user_message"]["content"] if b.get("type") == "image"]
    assert len(images) == 1
    assert images[0]["source"]["media_type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_iterate_proceeds_without_missing_screenshot(env, monkeypatch, caplog):
    # FAIL-OPEN reference: a lost/unreadable stored screenshot logs ONE WARNING
    # (identifiers only) and the run proceeds image-less — the prototype is
    # never failed by a missing reference image.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    key = f"uploads/{_TEST_COMPANY_ID}/deadbeef.png"
    pid = _seed_ready_with_screenshot(env, key)

    captured: dict = {}

    async def _fake_iterate(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            status="complete", iters=1, error_message=None, error_class=None, usage=None,
        ), {"src/App.tsx": "export default function App(){ return null }"}

    monkeypatch.setattr(env.routes, "iterate_prototype", _fake_iterate)

    staged: list[dict] = []

    async def _fake_stage(**kwargs):
        staged.append(kwargs)
        return True

    monkeypatch.setattr(env.routes, "_stage_iterate_run", _fake_stage)

    async def _raising_read(*, key, workspace_id):
        raise FileNotFoundError(key)

    monkeypatch.setattr(env.routes, "read_screenshot", _raising_read)

    with caplog.at_level(logging.WARNING):
        await env.routes._run_iterate_bg(
            prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
            body=env.routes.IterateRequest(prompt="tweak"),
        )

    # The run proceeded WITHOUT the image (no image block anywhere).
    assert all(b.get("type") != "image" for b in captured["user_message"]["content"])
    warnings = [
        r for r in caplog.records
        if "screenshot_context_unavailable" in r.getMessage()
    ]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "deadbeef.png" in msg                # key suffix — enough to triage
    assert _TEST_COMPANY_ID not in msg          # never the workspace prefix
    # Prototype NOT failed by the missing reference; the run staged normally.
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["status"] == "ready"
    assert row["error"] is None
    assert staged


# ─── Iterate staging path (B2 / AC6a) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_stage_iterate_run_does_not_call_complete_prototype(env, monkeypatch):
    # AC6a (load-bearing): the iterate staging path reaches a NEW checkpoint
    # (threading the iterate prompt into prompt_history) WITHOUT invoking
    # complete_prototype — no completed_at re-stamp, no prototype_completed emit.
    complete_calls: list = []
    monkeypatch.setattr(env.routes, "complete_prototype",
                        lambda **k: complete_calls.append(k))

    async def fake_vite(vfs):
        return {"index.html": "<html></html>"}

    staged_prefixes: list = []

    async def fake_stage(*, prototype_id, checkpoint_id, files, sub_prefix=None):
        staged_prefixes.append(sub_prefix)
        return "https://bundle/iterated"

    monkeypatch.setattr(env.routes, "vite_build", fake_vite)
    monkeypatch.setattr(env.routes, "stage_bundle", fake_stage)

    ckpt_args: dict = {}

    def fake_ckpt(**k):
        ckpt_args.update(k)
        return 999

    monkeypatch.setattr(env.routes, "create_checkpoint", fake_ckpt)
    pid = _seed_ready(env)

    await env.routes._stage_iterate_run(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        virtual_fs={"src/App.tsx": "x"}, iterate_prompt="make it blue",
    )
    # AC6a: complete_prototype NEVER called on the iterate path.
    assert complete_calls == []
    # Reached the new checkpoint; the iterate prompt is threaded into prompt_history.
    assert ckpt_args["prompt_history"] == [{"kind": "iterate", "prompt": "make it blue"}]
    # Both the built dist/ (sub_prefix None) and the raw _source/ were staged.
    assert None in staged_prefixes
    assert "_source" in staged_prefixes


@pytest.mark.asyncio
async def test_stage_iterate_run_advances_current_checkpoint(env, monkeypatch, caplog):
    # P3-12 filled the seam: _stage_iterate_run now calls advance_current_checkpoint
    # at the tail, so current_checkpoint_id + bundle_url move to the new checkpoint
    # and the `prototype_checkpoint_advanced` INFO line is emitted (AC5).
    async def fake_vite(vfs):
        return {"index.html": "<html></html>"}

    async def fake_stage(*, prototype_id, checkpoint_id, files, sub_prefix=None):
        return "https://bundle/iterated"

    monkeypatch.setattr(env.routes, "vite_build", fake_vite)
    monkeypatch.setattr(env.routes, "stage_bundle", fake_stage)
    monkeypatch.setattr(env.routes, "create_checkpoint", lambda **k: 555)
    pid = _seed_ready(env)

    with caplog.at_level(logging.INFO):
        await env.routes._stage_iterate_run(
            prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
            virtual_fs={"a.tsx": "x"}, iterate_prompt="p",
        )
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "prototype_checkpoint_advanced" in blob
    # The advance actually moved the row to the new checkpoint + staged bundle.
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["current_checkpoint_id"] == 555
    # No-bypass migration: the iterate path stores the authed proxy URL for the
    # prototype id, not whatever stage_bundle returned.
    assert f"/_da-bundle/v1/design-agent/{pid}/bundle/index.html" in row["bundle_url"]


@pytest.mark.asyncio
async def test_run_iterate_bg_failure_marks_prototype_failed(env, monkeypatch):
    # A non-complete runner result fails the row in the existing Sprntly format
    # (status=... + structured error), preserving the prior bundle_url.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    async def _fake(**kwargs):
        return SimpleNamespace(
            status="error", iters=2,
            error_message="BadRequestError: boom", error_class="BadRequestError",
        ), {}

    monkeypatch.setattr(env.routes, "iterate_prototype", _fake)
    pid = _seed_ready(env)

    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        body=env.routes.IterateRequest(prompt="x"),
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["status"] == "failed"
    assert "status=error" in row["error"]
    assert "error_class=BadRequestError" in row["error"]
    # The original bundle is preserved (iterate failure does not erase it).
    assert row["bundle_url"] == "https://bundle/original"


# ─── Empty-seed fail-closed guard ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_iterate_empty_seed_with_checkpoint_fails_closed(env, monkeypatch):
    # A checkpointed prototype whose staged source reads back EMPTY must fail
    # loudly BEFORE any agent call: an empty seed renders as a fresh build, and
    # the execute run would then replace the whole prototype with only the
    # requested change.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    agent_calls: list = []

    async def fake_agent(**kwargs):
        agent_calls.append(kwargs)
        return SimpleNamespace(
            status="complete", iters=1, error_message=None, error_class=None,
        ), {"src/App.tsx": "only the change"}

    async def fake_read(prototype_id, checkpoint_id):
        return {}

    monkeypatch.setattr(env.routes, "iterate_prototype", fake_agent)
    monkeypatch.setattr(env.routes, "read_source_files_for_checkpoint", fake_read)
    pid = _seed_ready(env, current_checkpoint_id=42)

    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        body=env.routes.IterateRequest(prompt="make the button blue"),
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["status"] == "failed"
    assert row["error"].startswith("source_read_empty:")
    assert agent_calls == []                                # agent never invoked
    assert row["bundle_url"] == "https://bundle/original"   # prior bundle preserved
    assert row["current_checkpoint_id"] == 42               # no checkpoint change


@pytest.mark.asyncio
async def test_iterate_without_checkpoint_skips_guard(env, monkeypatch):
    # current_checkpoint_id IS NULL → nothing staged to wipe; the guard stays
    # silent and the run reaches the agent with the fresh-build (empty) seed.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    captured = _stub_iterate_capture(monkeypatch, env.routes)

    async def fake_read(prototype_id, checkpoint_id):
        return {}

    monkeypatch.setattr(env.routes, "read_source_files_for_checkpoint", fake_read)
    pid = _seed_ready(env)  # no current checkpoint

    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        body=env.routes.IterateRequest(prompt="build it"),
    )
    assert captured["current_source"] == {}   # agent WAS invoked with the fresh seed
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert not (row["error"] or "").startswith("source_read_empty:")


@pytest.mark.asyncio
async def test_plan_mode_empty_seed_does_not_fail_prototype(env, monkeypatch):
    # Plan mode is exempt from the guard: a plan run stages nothing and advances
    # no checkpoint, so an empty seed cannot wipe anything; the destructive
    # execute run re-reads the source and hits the guard itself.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    async def fake_agent(**kwargs):
        return SimpleNamespace(
            status="complete", iters=1, error_message=None, error_class=None,
            final_content=[{"type": "text", "text": "- shrink the logo"}],
        ), {}

    async def fake_read(prototype_id, checkpoint_id):
        return {}

    monkeypatch.setattr(env.routes, "iterate_prototype", fake_agent)
    monkeypatch.setattr(env.routes, "read_source_files_for_checkpoint", fake_read)
    monkeypatch.setattr(env.routes, "set_iteration_plan", lambda **k: None)
    pid = _seed_ready(env, current_checkpoint_id=42)

    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        body=env.routes.IterateRequest(prompt="rethink the header", mode="plan"),
        iteration_id=1,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["status"] == "ready"           # NOT failed
    assert row["error"] is None
    assert row["bundle_url"] == "https://bundle/original"


@pytest.mark.asyncio
async def test_empty_seed_guard_logs_identifiers_only(env, monkeypatch, caplog):
    # The guard emits exactly one WARNING carrying prototype_id + checkpoint_id
    # and no prompt / staged-source content.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    async def fake_read(prototype_id, checkpoint_id):
        return {}

    monkeypatch.setattr(env.routes, "read_source_files_for_checkpoint", fake_read)
    pid = _seed_ready(env, current_checkpoint_id=42)

    with caplog.at_level(logging.WARNING):
        await env.routes._run_iterate_bg(
            prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
            body=env.routes.IterateRequest(prompt="SECRET_PROMPT_BODY"),
        )
    warns = [
        r.getMessage() for r in caplog.records
        if r.getMessage().startswith("prototype_iterate_source_read_empty")
    ]
    assert warns == [
        f"prototype_iterate_source_read_empty prototype_id={pid} checkpoint_id=42"
    ]
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRET_PROMPT_BODY" not in blob   # never the prompt / content

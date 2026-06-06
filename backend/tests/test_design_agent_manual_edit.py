"""Tests for the manual-edit commit-back spine (P4-02, AD23):

    DESIGN_AGENT_MANUAL_EDIT_SYSTEM + render_manual_edit_user   (prompts.py)
    manual_edit_prototype                                       (runner.py)
    tools_for_mode("manual")                                    (tools.py)
    POST /v1/design-agent/{id}/manual-edit + _run_manual_edit_bg
                                                                (routes/design_agent.py)

Four test layers, matching the ticket's Unit Tests section:

- PROMPT     — pure assertions on the manual-edit system prompt (Tailwind-swap
               directive) + the cache-breakpoint discipline of render_manual_edit_user.
- TOOL BUDGET— tools_for_mode("manual") = 6 action, 0 sentinel; AD17 budget unchanged.
- RUNNER     — manual_edit_prototype against the recording fake Anthropic client
               (reused shape from test_design_agent_iterate.py): the 4-iter cap
               (P4-11: raised 2→4 so a realistic search→view→batched-edit→self-correct
               multi-anchor edit can commit), the cost-log (mode=manual), cache-read
               accounting, client routing.
- ROUTE      — the HTTP surface + the background task + the iterate-staging reuse +
               the stale-anchor fail-closed path. Auth is INJECTED via
               app.dependency_overrides (NOT the live auth.py path — P4-02 mitigation).

Load-bearing assertions: AC1 (200ms generating, no Anthropic in request path),
AC5 (max_iters=4 hard cap — P4-11), AC7 (6 action / 0 sentinel), AC8 (Tailwind-class-swap
prompt + the §3b turn-sequence/batching contract — P4-11), AC9 (stages via
_stage_iterate_run, never complete_prototype), AC10
(stale-anchor → fail_prototype, no advance), AC11 (cache_control at end of stable
prefix only), AC13 (route reachable, not catch-all-shadowed).
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import logging
import types
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.design_agent import runner, tools
from app.design_agent.prompts import (
    DESIGN_AGENT_ITERATE_SYSTEM,
    DESIGN_AGENT_MANUAL_EDIT_SYSTEM,
    DESIGN_AGENT_SCAFFOLD_SYSTEM,
    DESIGN_AGENT_TEMPLATE_VERSION,
    render_manual_edit_user,
)

from app.auth import CompanyContext
from tests.conftest import _TEST_COMPANY_ID, _TEST_USER_ID

TELEMETRY_LOGGER = "app.llm_telemetry"


# ═══════════════════════════════════════════════════════════════════════════
# Layer 1 — Prompt + render_manual_edit_user (pure)
# ═══════════════════════════════════════════════════════════════════════════


def test_manual_edit_system_distinct_from_siblings():
    # AD8: the manual-edit prompt is a SEPARATE prompt, not a copy of scaffold/iterate.
    assert DESIGN_AGENT_MANUAL_EDIT_SYSTEM != DESIGN_AGENT_SCAFFOLD_SYSTEM
    assert DESIGN_AGENT_MANUAL_EDIT_SYSTEM != DESIGN_AGENT_ITERATE_SYSTEM
    assert DESIGN_AGENT_MANUAL_EDIT_SYSTEM.strip()


def test_manual_edit_system_renders_shadcn_inventory():
    # The {shadcn_inventory} placeholder was rendered (not left literal).
    assert "shadcn/ui components" in DESIGN_AGENT_MANUAL_EDIT_SYSTEM
    assert "{shadcn_inventory}" not in DESIGN_AGENT_MANUAL_EDIT_SYSTEM


def test_template_version_is_4():
    # AC2: bumped to 4 by P4-02 (manual-edit commit-back family).
    assert DESIGN_AGENT_TEMPLATE_VERSION == 4


def test_manual_edit_system_prompt_prefers_tailwind_class_swap():
    # AC8: the prompt teaches Tailwind-class-swap-preferred-over-inline-style with
    # line_replace, and inline-style-as-LAST-resort negative space.
    p = DESIGN_AGENT_MANUAL_EDIT_SYSTEM
    assert "Tailwind class" in p
    assert "line_replace" in p
    # inline-style-as-last-resort negative space
    assert "LAST resort" in p
    assert "style={{" in p
    # commit-only framing (AD23): not redesigning, just making the source match.
    assert "COMMIT" in p.upper() or "commit" in p


def test_manual_edit_system_prompt_never_asks():
    # AC7/§8: manual-edit mode has NO clarifying_question — the prompt says never ask.
    p = DESIGN_AGENT_MANUAL_EDIT_SYSTEM
    assert "Never." in p or "never" in p.lower()
    # multi-match (AD4 collision) directive present.
    assert "ALL of them" in p


def test_manual_edit_system_prompt_names_turn_sequence_and_keeps_batching():
    # AC3 (P4-11): §3b must reconcile the (now 4-turn) budget with the prompt's own
    # workflow — it must NAME the expected turn shape (Turn 1 locate via search/view,
    # Turn 2 batched edits), KEEP the batching directive, advertise the 4-turn safety
    # rail, and bias toward finishing in 2–3. This is a contract/property test guarding
    # prompt drift: if a future edit drops the batching directive or the turn sequence,
    # this fails loudly rather than silently regressing the cost/turn behaviour.
    p = DESIGN_AGENT_MANUAL_EDIT_SYSTEM

    # 1. Batching directive is still present (the writes go in ONE turn).
    assert "ONE batched turn" in p
    assert "single assistant turn" in p

    # 2. The explicit search/view-then-edit turn sequence is named.
    assert "Turn 1" in p
    assert "Turn 2" in p
    assert "LOCATE" in p or "locate" in p
    # the locate pass uses search/view; the edit pass uses line_replace.
    assert "search" in p
    assert "line_replace" in p

    # 3. The 4-turn safety rail is advertised AND the model is biased toward 2–3.
    assert "AT MOST 4 turns" in p
    assert "GOAL" in p.upper()
    assert ("2–3" in p) or ("2-3" in p)  # en-dash or hyphen — "finishing in 2–3 is the goal"

    # 4. The old hard "AT MOST 2 turns" framing is gone (it was the bug — zero slack).
    assert "AT MOST 2 turns" not in p


def test_render_manual_edit_user_cache_breakpoint_on_prefix():
    # AC11: cache_control ephemeral 1h on the LAST cacheable block; NONE on volatile.
    cacheable, volatile = render_manual_edit_user(
        current_source={"src/App.tsx": "export default function App(){}"},
        edits=[{"anchor_id": "a1", "property": "color",
                "old_value": "text-blue-600", "new_value": "text-red-600"}],
    )
    assert cacheable[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "cache_control" not in volatile


def test_render_manual_edit_user_source_in_prefix_edits_in_volatile():
    # AC11: source ONLY above the breakpoint; the edit triples ONLY below it.
    cacheable, volatile = render_manual_edit_user(
        current_source={"src/App.tsx": "SOURCE_MARKER_XYZ"},
        edits=[{"anchor_id": "ANCHOR_MARKER", "property": "padding",
                "old_value": "p-4", "new_value": "p-6"}],
    )
    cache_text = " ".join(b["text"] for b in cacheable)
    assert "SOURCE_MARKER_XYZ" in cache_text          # source IS cached
    assert "ANCHOR_MARKER" not in cache_text          # edits NOT in the cached prefix
    assert "ANCHOR_MARKER" in volatile["text"]        # edits ARE in the volatile suffix
    assert "p-4" in volatile["text"] and "p-6" in volatile["text"]
    assert "SOURCE_MARKER_XYZ" not in volatile["text"]  # source not duplicated below


# ═══════════════════════════════════════════════════════════════════════════
# Layer 2 — Tool budget (AD17)
# ═══════════════════════════════════════════════════════════════════════════


def test_manual_edit_mode_registers_six_action_zero_sentinel():
    # AC7: tools_for_mode("manual") → 6 action, 0 sentinel; registry lengths unchanged.
    registry = tools.tools_for_mode("manual")
    actions = [t for t in registry if t.category == "action"]
    sentinels = [t for t in registry if t.category == "sentinel"]
    assert len(actions) == 6
    assert len(sentinels) == 0
    # line_replace is REUSED (no 7th action tool added).
    names = {t.name for t in actions}
    assert "line_replace" in names
    assert names == {"view", "write", "line_replace", "search", "fetch_figma", "read_console"}
    # AD17 global budget unchanged.
    assert len(tools.ACTION_TOOLS) == 6
    assert len(tools.SENTINEL_TOOLS) == 2


def test_manual_edit_mode_excludes_both_sentinels():
    # AC7: neither clarifying_question nor propose_prd_patch in manual mode.
    names = {t.name for t in tools.tools_for_mode("manual")}
    assert "clarifying_question" not in names
    assert "propose_prd_patch" not in names


def test_manual_edit_tool_definitions_serialise():
    # tool_definitions_for_mode("manual") returns the API-shape for all 6 action tools.
    defs = tools.tool_definitions_for_mode("manual")
    assert len(defs) == 6
    assert all("name" in d and "input_schema" in d for d in defs)


# ═══════════════════════════════════════════════════════════════════════════
# Layer 3 — manual_edit_prototype runner entry (recording fake client)
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
        self.messages = types.SimpleNamespace(create=self._create)

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
        {"type": "text", "text": "You are the Design Agent, committing manual edits."},
        {
            "type": "text",
            "text": "<manual-edit system + tool defs — the stable prefix>",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    ]


def _user(text: str = "Commit the color change."):
    return {"role": "user", "content": [_text(text)]}


def _install_client(monkeypatch, responses) -> _RecordingClient:
    client = _RecordingClient(responses)
    monkeypatch.setattr(runner, "get_design_agent_client", lambda: client)
    return client


def _run(coro):
    return asyncio.run(coro)


def test_manual_edit_prototype_threads_manual_mode_and_iter_cap(monkeypatch):
    # AC5 (P4-11): manual_edit_prototype calls agent_loop with mode='manual' AND an
    # explicit max_iters=MANUAL_EDIT_MAX_ITERS (now 4, NOT the inherited
    # DEFAULT_MAX_ITERS of 40). The cap is threaded explicitly, never inherited.
    captured: dict = {}

    async def fake_loop(*, system_blocks, user_message, ctx, max_iters, scenario, mode):
        captured["mode"] = mode
        captured["max_iters"] = max_iters
        captured["ctx"] = ctx
        return runner.RunResult(status="complete", iters=1, usage=runner.RunUsage(),
                                duration_ms=1, final_content=[])

    monkeypatch.setattr(runner, "agent_loop", fake_loop)
    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda key, ws: None)
    _run(runner.manual_edit_prototype(
        prototype_id=1, workspace_id=_TEST_COMPANY_ID, system_blocks=_system(), user_message=_user(),
        current_source={"src/App.tsx": "x"}, figma_file_key=None,
    ))
    assert captured["mode"] == "manual"
    assert captured["max_iters"] == runner.MANUAL_EDIT_MAX_ITERS
    assert captured["max_iters"] == 4
    assert captured["max_iters"] != runner.DEFAULT_MAX_ITERS
    assert captured["ctx"].virtual_fs == {"src/App.tsx": "x"}


def test_manual_edit_runs_four_iter_max(monkeypatch):
    # AC2/AC5 (P4-11): a forced-runaway model (always tool_use) exits at EXACTLY
    # MANUAL_EDIT_MAX_ITERS=4 iters with status='max_iters' and does not exceed the
    # cap — no exception. The cap was raised 2→4 so the prompt's own
    # search→view→batched-edit→self-correct workflow can actually complete; this test
    # guards that the rail still bounds a genuine runaway at 4 (not 40).
    _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})]),  # replayed forever
    ])
    result, _vfs = _run(runner.manual_edit_prototype(
        prototype_id=1, workspace_id=_TEST_COMPANY_ID, system_blocks=_system(), user_message=_user(),
        current_source={}, figma_file_key=None,
    ))
    assert result.status == "max_iters"
    assert result.iters == 4
    assert result.iters == runner.MANUAL_EDIT_MAX_ITERS


def test_manual_edit_emits_cost_summary_mode_manual_under_5c(monkeypatch, caplog):
    # AC4/AC6: cost-summary via log_llm_run with operation manual_edit + mode=manual,
    # iters<=MANUAL_EDIT_MAX_ITERS (P4-11: now 4), est_cost_usd<=0.05 on a representative
    # stubbed usage (independent of the live DEFAULT_MAX_TOKENS value — the cost asserts
    # on the STUBBED usage; the real-LLM cold-cache cost is reconciled by AC1/AC4 at the
    # live gate, not here).
    _install_client(monkeypatch, [
        _msg("end_turn", [_text("committed")], usage=_usage(cache_read=200, inp=300, out=120)),
    ])
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        result, _vfs = _run(runner.manual_edit_prototype(
            prototype_id=55, workspace_id=_TEST_COMPANY_ID, system_blocks=_system(), user_message=_user(),
            current_source={"src/App.tsx": "x"}, figma_file_key=None, scenario="A",
        ))
    assert result.status == "complete"
    assert result.iters <= runner.MANUAL_EDIT_MAX_ITERS
    records = [r for r in caplog.records if r.name == TELEMETRY_LOGGER]
    assert len(records) == 1
    msg = records[0].getMessage()
    assert "design_agent.run.manual_edit" in msg
    assert "prototype_id=55" in msg
    assert "mode=manual" in msg
    for field in ("cached_input_tokens=", "input_tokens=", "output_tokens=",
                  "duration_ms=", "est_cost_usd=", "status=complete", "iters="):
        assert field in msg, f"missing {field!r}"
    # AC6: the computed est_cost_usd on this stubbed usage is well under the $0.05 cap.
    assert result.usage.est_cost_usd(runner.MODEL) <= 0.05


def test_manual_edit_cost_log_carries_no_source_content(monkeypatch, caplog):
    # AC14 (observability): the cost-summary line carries identifiers + token counts
    # only — never the system prompt body, the edit values, or the source.
    _install_client(monkeypatch, [_msg("end_turn", [_text("done")])])
    system_blocks = [
        {"type": "text", "text": "SECRET_MANUAL_SYSTEM_BODY"},
        {"type": "text", "text": "tools", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]
    user_message = {"role": "user", "content": [_text("EDIT_VALUE_SECRET")]}
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        _run(runner.manual_edit_prototype(
            prototype_id=1, workspace_id=_TEST_COMPANY_ID, system_blocks=system_blocks,
            user_message=user_message, current_source={"f.tsx": "SECRET_SOURCE_BODY"},
            figma_file_key=None,
        ))
    msg = next(r.getMessage() for r in caplog.records if r.name == TELEMETRY_LOGGER)
    assert "SECRET_MANUAL_SYSTEM_BODY" not in msg
    assert "EDIT_VALUE_SECRET" not in msg
    assert "SECRET_SOURCE_BODY" not in msg


def test_manual_edit_cache_read_nonzero_on_second_call(monkeypatch):
    # Cache-verification: the second create call within the window reports cache_read
    # tokens, accumulated into RunUsage (the breakpoint is honoured).
    _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "src/A.tsx"})], usage=_usage(inp=500, out=100)),
        _msg("end_turn", [_text("done")], usage=_usage(cache_read=120, inp=10, out=20)),
    ])
    result, _vfs = _run(runner.manual_edit_prototype(
        prototype_id=1, workspace_id=_TEST_COMPANY_ID, system_blocks=_system(), user_message=_user(),
        current_source={"src/A.tsx": "x"}, figma_file_key=None,
    ))
    assert result.usage.cache_read_input_tokens >= 120
    assert result.iters <= runner.MANUAL_EDIT_MAX_ITERS


def test_manual_edit_uses_design_agent_client(monkeypatch):
    # AC12: the run routes through get_design_agent_client() (the injected client is
    # the one called — no direct ANTHROPIC_API_KEY read on this path).
    client = _install_client(monkeypatch, [_msg("end_turn", [_text("done")])])
    _run(runner.manual_edit_prototype(
        prototype_id=1, workspace_id=_TEST_COMPANY_ID, system_blocks=_system(), user_message=_user(),
        current_source={}, figma_file_key=None,
    ))
    assert len(client.calls) >= 1  # the run went through the injected design-agent client


# ═══════════════════════════════════════════════════════════════════════════
# Layer 4 — Route + background task + staging / stale-anchor (FakeSupabaseClient)
# ═══════════════════════════════════════════════════════════════════════════

# Mirrors test_design_agent_iterate.py's _DDL (prototypes + checkpoints + comments).
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
"""


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototype tables + feature flag ON, with the design agent
    module stack reloaded in dependency order."""
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
def client(env):
    """TestClient with require_company INJECTED (P4-02 mitigation: do NOT rely
    on the live auth.py path — override the dependency to a fixed company context;
    workspace_id resolves to _TEST_COMPANY_ID)."""
    c = TestClient(env.main.app)
    env.main.app.dependency_overrides[env.routes.require_company] = lambda: CompanyContext(
        company_id=_TEST_COMPANY_ID, role="owner", user_id=_TEST_USER_ID
    )
    yield c
    env.main.app.dependency_overrides.clear()


@pytest.fixture
def unauth(env) -> TestClient:
    """No dependency override — the REAL require_company runs (401 without a bearer)."""
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


def _stub_run(monkeypatch, routes_mod):
    """Patch routes.manual_edit_prototype (+ the source read) so a fired bg task does
    no real LLM/storage work. Returns a non-complete result so no staging runs."""
    async def _fake(**kwargs):
        return SimpleNamespace(status="error", iters=1, error_message=None, error_class=None), {}

    async def _fake_read(prototype_id, checkpoint_id):
        return {}

    monkeypatch.setattr(routes_mod, "manual_edit_prototype", _fake)
    monkeypatch.setattr(routes_mod, "read_source_files_for_checkpoint", _fake_read)


_GOOD_BODY = {"edits": [
    {"anchor_id": "a1b2c3d4", "property": "color",
     "old_value": "text-blue-600", "new_value": "text-red-600"},
]}


# ─── Route status codes + reachability (AC1, AC2, AC3, AC13) ────────────────


def test_manual_edit_route_returns_generating(env, client, monkeypatch):
    # AC1 + AC13 reachability: a valid body REACHES the handler (NOT 422-shadowed by
    # the GET /{prototype_id} catch-all) and returns {status:'generating', queue:0}.
    _stub_run(monkeypatch, env.routes)
    pid = _seed_ready(env)
    resp = client.post(f"/v1/design-agent/{pid}/manual-edit", json=_GOOD_BODY)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prototype_id"] == pid
    assert body["status"] == "generating"
    assert body["queue_position"] == 0


def test_manual_edit_empty_edits_422(env, client):
    # AC2: empty edits → 422 (min_length=1). Reaches validation, not the catch-all.
    pid = _seed_ready(env)
    resp = client.post(f"/v1/design-agent/{pid}/manual-edit", json={"edits": []})
    assert resp.status_code == 422


def test_manual_edit_too_many_edits_422(env, client):
    # AC2: >50 edits → 422 (max_length=50).
    pid = _seed_ready(env)
    big = {"edits": [
        {"anchor_id": f"a{i}", "property": "padding", "old_value": "p-4", "new_value": "p-6"}
        for i in range(51)
    ]}
    resp = client.post(f"/v1/design-agent/{pid}/manual-edit", json=big)
    assert resp.status_code == 422


def test_manual_edit_bad_property_422(env, client):
    # AC2: a property outside the closed set → 422 (Literal validation).
    pid = _seed_ready(env)
    bad = {"edits": [{"anchor_id": "a1", "property": "border",
                      "old_value": "x", "new_value": "y"}]}
    resp = client.post(f"/v1/design-agent/{pid}/manual-edit", json=bad)
    assert resp.status_code == 422


def test_manual_edit_locked_409(env, client):
    # AC3: a Marked-Complete (locked) prototype → 409.
    pid = _seed_locked(env)
    resp = client.post(f"/v1/design-agent/{pid}/manual-edit", json=_GOOD_BODY)
    assert resp.status_code == 409
    assert "locked" in resp.json()["detail"].lower()


def test_manual_edit_not_ready_409(env, client):
    # AC3: a generating prototype (status != ready) → 409.
    pid = env.proto.start_prototype(prd_id=1, workspace_id=_TEST_COMPANY_ID, template_version=1)
    resp = client.post(f"/v1/design-agent/{pid}/manual-edit", json=_GOOD_BODY)
    assert resp.status_code == 409


def test_manual_edit_cross_workspace_404(env, client):
    # AC3: a prototype in a foreign workspace is invisible (404, not 403).
    pid = _seed_ready(env, workspace_id="demo")
    resp = client.post(f"/v1/design-agent/{pid}/manual-edit", json=_GOOD_BODY)
    assert resp.status_code == 404


def test_manual_edit_requires_session(env, unauth):
    # AC3: no bearer → 401 (the REAL require_company runs here).
    resp = unauth.post("/v1/design-agent/1/manual-edit", json=_GOOD_BODY)
    assert resp.status_code == 401


def test_manual_edit_feature_flag_off_404(env, client, monkeypatch):
    # AC3: invisible (404) when the flag is off.
    pid = _seed_ready(env)
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    resp = client.post(f"/v1/design-agent/{pid}/manual-edit", json=_GOOD_BODY)
    assert resp.status_code == 404


def test_manual_edit_emits_started_log(env, client, monkeypatch, caplog):
    # AC14: a prototype_manual_edit_started INFO line at kickoff (identifiers only).
    _stub_run(monkeypatch, env.routes)
    pid = _seed_ready(env)
    with caplog.at_level(logging.INFO):
        resp = client.post(f"/v1/design-agent/{pid}/manual-edit", json=_GOOD_BODY)
    assert resp.status_code == 200, resp.text
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert f"prototype_manual_edit_started prototype_id={pid}" in blob


# ─── Background task: source load + staging reuse + stale-anchor (AC9, AC10) ─


@pytest.mark.asyncio
async def test_run_manual_edit_bg_loads_source_and_threads_edits(env, monkeypatch):
    # S2: get_prototype FIRST → read_source_files_for_checkpoint(pid, cid) POSITIONAL
    # → current_source threaded; the edit triples ride in the assembled user_message.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    captured: dict = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status="error", iters=1, error_message=None, error_class=None), {}

    read_calls: list[tuple] = []

    async def fake_read(prototype_id, checkpoint_id):
        read_calls.append((prototype_id, checkpoint_id))
        return {"src/App.tsx": "export default function App(){}"}

    monkeypatch.setattr(env.routes, "manual_edit_prototype", fake_run)
    monkeypatch.setattr(env.routes, "read_source_files_for_checkpoint", fake_read)
    pid = _seed_ready(env, current_checkpoint_id=42)

    body = env.routes.ManualEditRequest(**_GOOD_BODY)
    await env.routes._run_manual_edit_bg(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, body=body)

    assert read_calls == [(pid, 42)]  # positional (prototype_id, checkpoint_id)
    assert captured["current_source"] == {"src/App.tsx": "export default function App(){}"}
    blob = " ".join(b["text"] for b in captured["user_message"]["content"])
    assert "a1b2c3d4" in blob              # anchor in the assembled prompt
    assert "text-red-600" in blob          # new value in the assembled prompt


@pytest.mark.asyncio
async def test_run_manual_edit_bg_stages_via_iterate_on_change(env, monkeypatch):
    # AC9: a successful run with a CHANGED source stages via _stage_iterate_run and
    # NEVER calls complete_prototype (a manual edit is a checkpoint ADVANCE).
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    stage_calls: list[dict] = []
    complete_calls: list = []

    async def fake_run(**kwargs):
        # return a source DIFFERENT from the seed → a real commit happened.
        return (SimpleNamespace(status="complete", iters=1, error_message=None, error_class=None),
                {"src/App.tsx": "CHANGED"})

    async def fake_read(prototype_id, checkpoint_id):
        return {"src/App.tsx": "ORIGINAL"}

    async def fake_stage(**kwargs):
        stage_calls.append(kwargs)

    monkeypatch.setattr(env.routes, "manual_edit_prototype", fake_run)
    monkeypatch.setattr(env.routes, "read_source_files_for_checkpoint", fake_read)
    monkeypatch.setattr(env.routes, "_stage_iterate_run", fake_stage)
    monkeypatch.setattr(env.routes, "complete_prototype", lambda **k: complete_calls.append(k))

    pid = _seed_ready(env, current_checkpoint_id=9)
    body = env.routes.ManualEditRequest(**_GOOD_BODY)
    await env.routes._run_manual_edit_bg(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, body=body)

    assert len(stage_calls) == 1
    assert stage_calls[0]["virtual_fs"] == {"src/App.tsx": "CHANGED"}
    assert stage_calls[0]["iterate_prompt"] == "<manual edit>"
    assert complete_calls == []  # AC9: NEVER complete_prototype on the manual-edit path


@pytest.mark.asyncio
async def test_manual_edit_stale_anchor_fails_not_silent(env, monkeypatch):
    # AC10: the run ends 'complete' but commits NO source change (agent could not
    # resolve a triple) → fail_prototype with a loud manual_edit/not-found error and
    # NO checkpoint advance (no _stage_iterate_run). Does not silently succeed.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    fail_calls: list[dict] = []
    stage_calls: list = []

    async def fake_run(**kwargs):
        # return the source UNCHANGED (== seed) → no edit was committed.
        return (SimpleNamespace(status="complete", iters=1, error_message=None, error_class=None),
                {"src/App.tsx": "ORIGINAL"})

    async def fake_read(prototype_id, checkpoint_id):
        return {"src/App.tsx": "ORIGINAL"}

    async def fake_stage(**kwargs):
        stage_calls.append(kwargs)

    monkeypatch.setattr(env.routes, "manual_edit_prototype", fake_run)
    monkeypatch.setattr(env.routes, "read_source_files_for_checkpoint", fake_read)
    monkeypatch.setattr(env.routes, "_stage_iterate_run", fake_stage)
    monkeypatch.setattr(env.routes, "fail_prototype", lambda **k: fail_calls.append(k))

    pid = _seed_ready(env, current_checkpoint_id=9)
    body = env.routes.ManualEditRequest(**_GOOD_BODY)
    await env.routes._run_manual_edit_bg(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, body=body)

    assert stage_calls == []                      # NO checkpoint advance
    assert len(fail_calls) == 1
    err = fail_calls[0]["error"]
    assert "manual_edit" in err
    assert "not found" in err
    assert "a1b2c3d4" in err                       # the unresolved anchor is named


@pytest.mark.asyncio
async def test_manual_edit_run_status_failure_marks_failed(env, monkeypatch):
    # A non-complete RunResult (e.g. the 4-iter max_iters cap, or an Anthropic error)
    # → structured fail_prototype, no staging.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    fail_calls: list[dict] = []
    stage_calls: list = []

    async def fake_run(**kwargs):
        return (SimpleNamespace(status="max_iters", iters=2,
                                error_message=None, error_class=None), {"src/App.tsx": "x"})

    async def fake_read(prototype_id, checkpoint_id):
        return {}

    async def fake_stage(**kwargs):
        stage_calls.append(kwargs)

    monkeypatch.setattr(env.routes, "manual_edit_prototype", fake_run)
    monkeypatch.setattr(env.routes, "read_source_files_for_checkpoint", fake_read)
    monkeypatch.setattr(env.routes, "_stage_iterate_run", fake_stage)
    monkeypatch.setattr(env.routes, "fail_prototype", lambda **k: fail_calls.append(k))

    pid = _seed_ready(env)
    body = env.routes.ManualEditRequest(**_GOOD_BODY)
    await env.routes._run_manual_edit_bg(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, body=body)

    assert stage_calls == []
    assert len(fail_calls) == 1
    assert "status=max_iters" in fail_calls[0]["error"]


# ─── Non-breakage (AC13) ────────────────────────────────────────────────────


def test_routes_design_agent_still_compiles_and_route_registered(env):
    # AC13: the module imports cleanly and the new route is registered on the router
    # alongside the existing ones (existing routes untouched — append-only).
    paths = {r.path for r in env.routes.router.routes}
    assert "/v1/design-agent/{prototype_id}/manual-edit" in paths
    # Existing sibling routes still present (not clobbered by the append).
    assert "/v1/design-agent/{prototype_id}/iterate" in paths
    assert "/v1/design-agent/{prototype_id}" in paths
    assert "/v1/design-agent/generate" in paths

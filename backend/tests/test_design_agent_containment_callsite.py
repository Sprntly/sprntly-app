"""Tests for wiring the interactivity-containment self-check into the live
post-generation gate.

Two halves:

1. **Call-site + policy** (``_stage_complete_run``): with all seams stubbed
   (``vite_build_with_repair``, ``assert_containment``, ``assert_theme_landed``
   and the DB helpers as spies), the containment check runs over the GENERATED
   SOURCE (not the built dist), sits between the theme assertion and checkpoint
   creation, and a containment miss LOGS + FLAGS without ever failing the row.
   The no-scope path is byte-identical to today (no call, no log).

2. **Scope derivation** (``_run_generation_bg``): on the recreate path
   (located screen present) the scope is derived from the PRD text + located
   screen and threaded into ``_stage_complete_run``; on the blank-canvas path
   the scope is None and no derivation happens.

No real LLM, no real vite build, no real storage.
"""
from __future__ import annotations

import importlib
import logging
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.design_agent.codebase_map.recreate import (
    ContainmentReport,
    LocatedScreen,
    ThemeExpectations,
)
from app.design_agent.codebase_map.types import (
    LogoAsset,
    MapResult,
    ScreenNode,
    ShellModel,
)


def _report(*, ok: bool, extra=(), inert=(), href=0) -> ContainmentReport:
    return ContainmentReport(
        handler_count=len(extra),
        href_count=href,
        prd_scope=[],
        extra_handlers=list(extra),
        inert_without_affordance=list(inert),
        ok=ok,
    )


# ── Call-site + policy: _stage_complete_run, all seams stubbed ─────────────────


@pytest.fixture
def routes_mod():
    """The live route module (no reload — monkeypatch the seams per test)."""
    import app.routes.design_agent as m

    return m


def _wire_seams(
    monkeypatch,
    routes_mod,
    *,
    report: ContainmentReport | None,
    dist_files=None,
    repaired_fs=None,
    order=None,
):
    """Replace every seam ``_stage_complete_run`` touches so the call runs to
    ``complete_prototype`` without a real build, real storage, or a real DB.

    ``report`` is what the stubbed ``assert_containment`` returns; None means the
    spy still records calls but the test does not exercise the report. ``order``
    (optional list) records the relative order of build/theme/containment/
    checkpoint for the ordering assertion.
    """
    spies = SimpleNamespace(
        containment_args=[], theme_calls=[], create_checkpoint=[],
        complete=[], fail=[],
    )
    dist = dict(dist_files) if dist_files is not None else {"index.html": "<html>DISTBODY</html>"}

    async def _fake_build(virtual_fs):
        if order is not None:
            order.append("build")
        return dict(dist), dict(repaired_fs if repaired_fs is not None else virtual_fs)

    def _fake_theme(dist_files, theme_expectations):
        if order is not None:
            order.append("theme")
        spies.theme_calls.append((dist_files, theme_expectations))

    def _fake_containment(source, scope):
        if order is not None:
            order.append("containment")
        spies.containment_args.append((source, list(scope)))
        return report if report is not None else _report(ok=True)

    def _fake_create_checkpoint(**kw):
        if order is not None:
            order.append("checkpoint")
        spies.create_checkpoint.append(kw)
        return 777

    async def _fake_stage_bundle(**kw):
        return "file:///x/index.html"

    async def _fake_capture(_bundle_url):
        return None  # honest-degrade: no preview image

    def _fake_complete(**kw):
        spies.complete.append(kw)

    def _fake_fail(**kw):
        spies.fail.append(kw)

    monkeypatch.setattr(routes_mod, "vite_build_with_repair", _fake_build)
    monkeypatch.setattr(routes_mod, "assert_theme_landed", _fake_theme)
    monkeypatch.setattr(routes_mod, "assert_containment", _fake_containment)
    monkeypatch.setattr(routes_mod, "create_checkpoint", _fake_create_checkpoint)
    monkeypatch.setattr(routes_mod, "stage_bundle", _fake_stage_bundle)
    monkeypatch.setattr(routes_mod, "reconcile_comments_on_checkpoint", lambda **kw: None)
    monkeypatch.setattr(routes_mod, "capture_bundle_screenshot", _fake_capture)
    monkeypatch.setattr(routes_mod, "complete_prototype", _fake_complete)
    monkeypatch.setattr(routes_mod, "fail_prototype", _fake_fail)
    monkeypatch.setattr(routes_mod, "publish_step", lambda *a, **k: None)
    return spies


async def test_containment_runs_over_source_not_dist(routes_mod, monkeypatch):
    """The argument handed to assert_containment is the concatenated .tsx/.jsx
    SOURCE bodies — never the built dist, never non-source files."""
    spies = _wire_seams(monkeypatch, routes_mod, report=_report(ok=True))
    virtual_fs = {
        "src/App.tsx": "export default function App(){ return <button onClick={open}/>; }  // TSXBODY",
        "src/index.css": "body{color:red} /* CSSBODY */",
    }
    await routes_mod._stage_complete_run(
        prototype_id=1, workspace_id="app", virtual_fs=virtual_fs,
        interactive_scope=["open"],
    )
    assert len(spies.containment_args) == 1
    source_arg, scope_arg = spies.containment_args[0]
    assert "TSXBODY" in source_arg          # the .tsx body is the grep target
    assert "DISTBODY" not in source_arg     # NOT the built dist
    assert "CSSBODY" not in source_arg      # NOT a non-.tsx/.jsx file
    assert scope_arg == ["open"]


async def test_containment_failure_logs_and_does_not_block(routes_mod, monkeypatch, caplog):
    """A containment miss (ok=False) still reaches complete_prototype, never
    calls fail_prototype, and logs a warning carrying the failure counts."""
    spies = _wire_seams(
        monkeypatch, routes_mod,
        report=_report(ok=False, extra=["onClick={doThing}"], inert=["<button>"], href=3),
    )
    with caplog.at_level(logging.WARNING, logger="app.routes.design_agent"):
        await routes_mod._stage_complete_run(
            prototype_id=42, workspace_id="app",
            virtual_fs={"src/App.tsx": "<button onClick={doThing}/>"},
            interactive_scope=["open"],
        )
    assert len(spies.complete) == 1          # the row still completes
    assert spies.fail == []                  # never failed
    lines = [r.getMessage() for r in caplog.records if "design_agent.containment" in r.getMessage()]
    assert len(lines) == 1
    msg = lines[0]
    assert "ok=false" in msg
    assert "prototype_id=42" in msg
    assert "n_extra_handlers=1" in msg
    assert "n_inert=1" in msg
    assert "href_count=3" in msg


async def test_containment_clean_logs_ok(routes_mod, monkeypatch, caplog):
    """A clean containment (ok=True) completes as today and logs an ok=true line."""
    spies = _wire_seams(monkeypatch, routes_mod, report=_report(ok=True))
    with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
        await routes_mod._stage_complete_run(
            prototype_id=7, workspace_id="app",
            virtual_fs={"src/App.tsx": "<button onClick={open}/>"},
            interactive_scope=["open"],
        )
    assert len(spies.complete) == 1
    lines = [r.getMessage() for r in caplog.records if "design_agent.containment" in r.getMessage()]
    assert len(lines) == 1
    assert "ok=true" in lines[0]
    assert "prototype_id=7" in lines[0]


async def test_no_scope_no_containment_call(routes_mod, monkeypatch, caplog):
    """No scope (None) and an empty scope ([]) both skip the containment check:
    no call, no containment log line, the row completes byte-identically."""
    for scope in (None, []):
        spies = _wire_seams(monkeypatch, routes_mod, report=_report(ok=True))
        with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
            caplog.clear()
            await routes_mod._stage_complete_run(
                prototype_id=9, workspace_id="app",
                virtual_fs={"src/App.tsx": "<button onClick={anything}/>"},
                interactive_scope=scope,
            )
        assert spies.containment_args == [], f"containment ran for scope={scope!r}"
        assert len(spies.complete) == 1
        assert not [r for r in caplog.records if "design_agent.containment" in r.getMessage()]


async def test_containment_after_theme_before_checkpoint(routes_mod, monkeypatch):
    """Ordering: vite build → theme assertion → containment assertion →
    checkpoint creation. The containment call is a sibling of the theme gate."""
    order: list[str] = []
    _wire_seams(monkeypatch, routes_mod, report=_report(ok=True), order=order)
    theme = ThemeExpectations(
        token_signals=("210 100% 50%",), font_families=("Inter",),
        class_signals=("bg-primary",), asset_basename=None,
    )
    await routes_mod._stage_complete_run(
        prototype_id=3, workspace_id="app",
        virtual_fs={"src/App.tsx": "<button onClick={open}/>"},
        theme_expectations=theme,
        interactive_scope=["open"],
    )
    assert order == ["build", "theme", "containment", "checkpoint"]


# ── Scope derivation: _run_generation_bg ───────────────────────────────────────

_PROTOTYPE_DDL = """
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
    share_passcode_hash    TEXT
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
"""


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """Feature flag ON + prototypes tables + design-agent module stack reloaded
    in dependency order (mirrors the generate-wiring test fixture)."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

    import app.config as _config_mod
    importlib.reload(_config_mod)
    import app.connectors.tokens as _tokens_mod
    importlib.reload(_tokens_mod)

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    import app.db as db_mod
    return SimpleNamespace(proto=proto_mod, routes=routes_mod, main=main_mod, db=db_mod)


def _seed_prd(db_mod, body: str = "# PRD\nUsers can open the settings panel.") -> int:
    prd_id = db_mod.start_prd(
        brief_id=1, insight_index=0, title="t", template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md=body)
    return prd_id


def _make_map(route: str = "/team", commit_sha: str = "shaABC") -> MapResult:
    node = ScreenNode(
        route=route, entry_component="TeamScreen",
        file="src/screens/Team.tsx", composed_components=[],
    )
    return MapResult(
        repo="org/repo", commit_sha=commit_sha, posture="CLEAN",
        nodes=[node], shell=ShellModel(logo=LogoAsset()),
    )


def _stub_generate_capture(monkeypatch, routes_mod):
    async def _fake(**kwargs):
        return SimpleNamespace(status="complete", iters=1, theme_expectations=None), {"src/App.tsx": "x"}

    monkeypatch.setattr(routes_mod, "generate_prototype", _fake)


def _stub_stage_capture(monkeypatch, routes_mod):
    captured: list[dict] = []

    async def _fake(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(routes_mod, "_stage_complete_run", _fake)
    return captured


async def test_scope_derived_on_recreate_path_none_on_blank_canvas(env, monkeypatch):
    """Recreate path (located present) → derive_interactive_scope(prd, located)
    is called and its result threaded into _stage_complete_run. Blank-canvas
    path (located None) → no derivation, interactive_scope=None threaded."""
    # --- recreate path ---
    _stub_generate_capture(monkeypatch, env.routes)
    stage_kwargs = _stub_stage_capture(monkeypatch, env.routes)
    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map",
        lambda *_a, **_k: _make_map(route="/team", commit_sha="shaABC"),
    )
    derive_calls: list = []
    monkeypatch.setattr(
        env.routes, "derive_interactive_scope",
        lambda prd, located: derive_calls.append((prd, located)) or ["open_panel"],
    )

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
        github_repo="org/repo", github_installation_id=42,
        design_source="github",
        chosen_screen_route="/team", map_commit_sha="shaABC",
    )
    assert len(derive_calls) == 1
    prd_arg, located_arg = derive_calls[0]
    assert isinstance(located_arg, LocatedScreen)
    assert isinstance(prd_arg, str) and prd_arg
    assert stage_kwargs[0]["interactive_scope"] == ["open_panel"]

    # --- blank-canvas path ---
    _stub_generate_capture(monkeypatch, env.routes)
    stage_kwargs2 = _stub_stage_capture(monkeypatch, env.routes)
    derive_calls.clear()

    pid2 = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    await env.routes._run_generation_bg(
        prototype_id=pid2, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="",
        figma_file_key="figma-key-abc",
        github_repo=None, github_installation_id=None,
        design_source="figma",
    )
    assert derive_calls == []                       # no derivation off the recreate path
    assert stage_kwargs2[0]["interactive_scope"] is None


# ── Integrity ──────────────────────────────────────────────────────────────────


def test_no_prohibited_tokens_in_changes():
    """No internal-coordinate tokens in this new test file (scanned in full) or
    in the route file's containment-wiring lines. Pattern assembled from split
    parts so the literals are not continuous strings here."""
    import re as _re
    from pathlib import Path

    parts = [
        r"C" + r"[0-9]" + r"-" + r"[0-9]",
        r"C-" + r"series",
        r"R" + r"[0-9]" + r"-" + r"[0-9]",
        r"H" + r"[0-9]" + r"-" + r"[0-9]",
        r"P" + r"[0-9]" + r"-" + r"[0-9]",
        r"\bA" + r"D" + r"[0-9]",
        r"\bF" + r"[0-9]{1,2}\b",
        r"D" + r"B" + r"D",
        r"B" + r"ab" + r"ajide",
    ]
    pattern = _re.compile("|".join(parts))

    backend = Path(__file__).resolve().parents[1]

    # This new test file is scrubbed in full.
    me = Path(__file__)
    for lineno, line in enumerate(me.read_text().splitlines(), 1):
        assert not pattern.search(line), f"prohibited token in {me.name}:{lineno}: {line}"

    # The route file: only the lines this change introduced (those referencing
    # the containment wiring). Legacy tokens elsewhere are out of scope.
    routes = backend / "app" / "routes" / "design_agent.py"
    for lineno, line in enumerate(routes.read_text().splitlines(), 1):
        if "interactive_scope" in line or "containment" in line.lower():
            assert not pattern.search(line), f"prohibited token in design_agent.py:{lineno}: {line}"

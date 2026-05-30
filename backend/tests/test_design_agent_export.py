"""Tests for the P2-08 markdown export serialiser.

`app.design_agent.export.render_export_markdown` is a PURE deterministic
function: (prototype_id, checkpoint_id) → markdown brief. No LLM, no network,
no subprocess. Same inputs + same DB state → byte-identical output (modulo the
single `generated_at` line, which the determinism test freezes).

Three layers:

1. **Pure helper units** (sync): `_extract_design_block`, `_strip_design_block`,
   `_language_for`, and `_assemble` driven with synthesised dicts — these lock
   down the output SHAPE without touching the DB (the ticket's recommended
   approach for ordering / fallback / placeholder ACs).
2. **Integration** (async, fake-Supabase DB): seed a prototype + checkpoint +
   PRD, then `await render_export_markdown(...)` end-to-end. Source files are
   injected by patching `storage.read_source_files_for_checkpoint` (the P2-04
   helper this module consumes) so the serialiser stays the unit under test.
3. **Purity** (static): the module imports no LLM / network / subprocess
   surface (AC #11, #12).

The `env` fixture mirrors `test_design_agent_source_staging.py`: it seeds the
prototype DDL into the fake DB, registers the jsonb columns, and reloads the
db + export modules in dependency order so their bindings point at the
freshly-wired fake client.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from app.design_agent import export

# ─── pure-helper fixtures ─────────────────────────────────────────────────────

_PRD_WITH_DESIGN = (
    "# Title\n"
    "body line one\n"
    "body line two\n"
    ":::design\n"
    "platform_hint: both\n"
    "notes: keep the dashboard above the fold\n"
    ":::\n"
    "footer line"
)

_DESIGN_BODY = "platform_hint: both\nnotes: keep the dashboard above the fold"


def _prd(*, title: str = "My Feature", md: str = _PRD_WITH_DESIGN) -> dict:
    return {"id": 1, "title": title, "payload_md": md}


def _prototype(*, pid: int = 1, bundle_url: str | None = "https://x.example/p/1/index.html") -> dict:
    return {"id": pid, "prd_id": 1, "bundle_url": bundle_url}


def _checkpoint(*, cid: int = 7, prototype_id: int = 1, prompt_history=None) -> dict:
    return {
        "id": cid,
        "prototype_id": prototype_id,
        "prompt_history": [] if prompt_history is None else prompt_history,
    }


def _assemble(**overrides) -> str:
    kwargs = {
        "prototype": _prototype(),
        "checkpoint": _checkpoint(),
        "prd": _prd(),
        "source_files": {},
        "generated_at": "2026-05-29T12:00:00+00:00",
    }
    kwargs.update(overrides)
    return export._assemble(**kwargs)


# ─── _extract_design_block / _strip_design_block (AC #6) ──────────────────────


def test_extract_design_block_returns_body():
    """AC #6: extraction returns the key:value body of the first :::design block."""
    assert export._extract_design_block(_PRD_WITH_DESIGN) == _DESIGN_BODY


def test_strip_design_block_removes_block_preserving_surroundings():
    """AC #6: strip removes the whole block, keeping content before AND after."""
    out = export._strip_design_block(_PRD_WITH_DESIGN)
    assert ":::design" not in out
    assert "# Title" in out
    assert "body line one" in out
    assert "footer line" in out


def test_extract_design_block_with_only_marker_returns_empty():
    """`:::design\\n:::` (empty body) → empty string."""
    assert export._extract_design_block(":::design\n:::") == ""


def test_extract_design_block_absent_returns_empty():
    assert export._extract_design_block("# Title\nno design here") == ""


def test_strip_design_block_absent_returns_input_rstripped():
    assert export._strip_design_block("# Title\nbody\n") == "# Title\nbody"


def test_extract_design_block_handles_multiple_blocks_returns_first():
    """Defensive: only the FIRST :::design block is extracted (PRDs never have two)."""
    md = ":::design\nfirst: a\n:::\nmiddle\n:::design\nsecond: b\n:::"
    assert export._extract_design_block(md) == "first: a"


# ─── _language_for ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path,lang",
    [
        ("src/App.tsx", "tsx"),
        ("src/widget.jsx", "tsx"),
        ("src/util.ts", "ts"),
        ("vite.config.js", "js"),
        ("src/index.css", "css"),
        ("index.html", "html"),
        ("package.json", "json"),
        ("README.md", "markdown"),
        ("docs/guide.mdx", "markdown"),
    ],
)
def test_language_hint_known_extensions(path, lang):
    assert export._language_for(path) == lang


def test_language_hint_for_unknown_returns_empty():
    assert export._language_for("Dockerfile") == ""


# ─── _assemble: section presence (AC #1) ──────────────────────────────────────


def test_assemble_includes_all_sections():
    """AC #1: every required H2 marker is present, plus the H1."""
    out = _assemble()
    assert out.startswith("# Design Brief: My Feature")
    for marker in (
        "## PRD Reference",
        "## Design Spec",
        "## Live Prototype",
        "## Generated Prototype Source",
        "## Iteration History",
    ):
        assert marker in out


def test_assemble_includes_title_in_h1():
    assert _assemble(prd=_prd(title="Checkout Flow")).startswith("# Design Brief: Checkout Flow")


def test_assemble_title_falls_back_to_prototype_id_when_blank():
    out = _assemble(prd=_prd(title=""), prototype=_prototype(pid=42))
    assert out.startswith("# Design Brief: Prototype 42")


def test_assemble_strips_design_block_from_prd_reference():
    """AC #1: the PRD Reference body must NOT contain the :::design marker."""
    out = _assemble()
    prd_section = out.split("## PRD Reference", 1)[1].split("## Design Spec", 1)[0]
    assert ":::design" not in prd_section
    assert "body line one" in prd_section


def test_assemble_includes_design_block_in_design_spec():
    out = _assemble()
    spec_section = out.split("## Design Spec", 1)[1].split("## Live Prototype", 1)[0]
    assert "platform_hint: both" in spec_section
    assert "notes: keep the dashboard above the fold" in spec_section


def test_assemble_includes_bundle_url_in_live_prototype():
    """AC #1: bundle URL rendered in <url> angle-bracket form."""
    out = _assemble(prototype=_prototype(bundle_url="https://demo.example/p/9/index.html"))
    assert "<https://demo.example/p/9/index.html>" in out


def test_assemble_bundle_url_placeholder_when_absent():
    out = _assemble(prototype=_prototype(bundle_url=None))
    assert "<(no bundle staged)>" in out


# ─── _assemble: iteration history (AC #1, #7) ─────────────────────────────────


def test_assemble_includes_iteration_history_when_present():
    history = [
        {"role": "user", "content": "make the header bigger"},
        {"role": "assistant", "content": "done — bumped to text-2xl"},
    ]
    out = _assemble(checkpoint=_checkpoint(prompt_history=history))
    assert "### Turn 1 (user)" in out
    assert "make the header bigger" in out
    assert "### Turn 2 (assistant)" in out
    assert "done — bumped to text-2xl" in out


def test_assemble_empty_prompt_history_shows_placeholder():
    """AC #7: empty prompt_history → placeholder line."""
    out = _assemble(checkpoint=_checkpoint(prompt_history=[]))
    assert "_No iteration history recorded for this checkpoint._" in out


def test_assemble_iteration_history_flattens_block_list_content():
    """Defensive: Anthropic block-list content is flattened to a text summary."""
    history = [
        {"role": "user", "content": [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]},
    ]
    out = _assemble(checkpoint=_checkpoint(prompt_history=history))
    assert "first second" in out


# ─── _assemble: design-block + source fallbacks (AC #3, #4) ───────────────────


def test_assemble_when_design_block_absent_shows_placeholder():
    """AC #4: a PRD with no :::design block → placeholder, no raise."""
    out = _assemble(prd=_prd(md="# Title\njust a body, no design block"))
    spec_section = out.split("## Design Spec", 1)[1].split("## Live Prototype", 1)[0]
    assert "_(no `:::design` block in the PRD)_" in spec_section


def test_assemble_when_no_source_files_shows_fallback():
    """Empty source dict → fallback message that references the bundle URL."""
    out = _assemble(
        source_files={},
        prototype=_prototype(bundle_url="https://b.example/p/1/index.html"),
    )
    assert "_Source files not staged for this checkpoint." in out
    assert "https://b.example/p/1/index.html" in out


def test_source_files_render_in_alphabetical_order():
    """AC #3: ### <path> headers appear in sorted(paths) order regardless of dict order."""
    out = _assemble(source_files={
        "src/B.tsx": "export const B = 1;",
        "src/A.tsx": "export const A = 1;",
        "package.json": '{"name":"p"}',
    })
    pos_pkg = out.index("### package.json")
    pos_a = out.index("### src/A.tsx")
    pos_b = out.index("### src/B.tsx")
    assert pos_pkg < pos_a < pos_b  # sorted: 'package.json' < 'src/A.tsx' < 'src/B.tsx'


def test_source_files_render_with_language_fence():
    out = _assemble(source_files={"src/App.tsx": "export default () => null;"})
    assert "### src/App.tsx" in out
    assert "```tsx" in out
    assert "export default () => null;" in out


# ─── _assemble: markdown lint (AC #13) ────────────────────────────────────────


def test_assemble_output_is_markdown_clean():
    """AC #13: exactly one structural H1; every H2 at column 0; no trailing whitespace;
    trailing newline. The PRD body here deliberately has no embedded `# ` heading so the
    only H1 is the serialiser's own `# Design Brief:` line (embedded PRD content is opaque
    and may legitimately contain its own headings)."""
    prd_no_h1 = "## Overview\nbody line\n:::design\nplatform_hint: both\n:::\nmore body"
    out = _assemble(
        prd=_prd(md=prd_no_h1),
        checkpoint=_checkpoint(prompt_history=[{"role": "user", "content": "hi"}]),
        source_files={"src/App.tsx": "export default () => null;"},
    )
    lines = out.split("\n")
    h1s = [ln for ln in lines if ln.startswith("# ")]
    assert len(h1s) == 1
    for ln in lines:
        if ln.startswith("## "):
            assert not ln.startswith(" "), f"H2 not at column 0: {ln!r}"
        assert ln == ln.rstrip(), f"trailing whitespace on line: {ln!r}"
    assert out.endswith("\n")
    assert not out.endswith("\n\n")


# ─── integration fixture (fake-Supabase DB) ───────────────────────────────────

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
    completed_at           TEXT
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
    """Fake-Supabase DB seeded with the prototype DDL + export module reloaded."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS, "prototype_checkpoints",
        {"prompt_history", "comment_state"},
    )

    import app.db.prds as prds_mod
    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    importlib.reload(export)  # re-bind get_prd / get_prototype to the reloaded modules
    import app.design_agent.storage as storage_mod

    return SimpleNamespace(prds=prds_mod, proto=proto_mod, export=export, storage=storage_mod)


def _seed(env, *, workspace_id="app", title="My Feature", md=_PRD_WITH_DESIGN,
          bundle_url="https://x.example/p/1/index.html", prompt_history=None):
    """Seed a PRD + prototype + checkpoint; return (prototype_id, checkpoint_id, prd_id)."""
    prd_id = env.prds.save_prd(brief_id=1, insight_index=0, title=title, md=md)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id=workspace_id, template_version=1)
    cid = env.proto.create_checkpoint(
        prototype_id=pid, workspace_id=workspace_id, bundle_url=bundle_url,
        prd_revision_hash=None, figma_frame_hash=None,
        prompt_history=prompt_history if prompt_history is not None else [],
    )
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id=workspace_id, bundle_url=bundle_url,
        current_checkpoint_id=cid,
    )
    return pid, cid, prd_id


def _patch_source(env, monkeypatch, files: dict):
    async def _fake(prototype_id, checkpoint_id):  # noqa: ARG001
        return files
    monkeypatch.setattr(env.storage, "read_source_files_for_checkpoint", _fake)


# ─── integration: render_export_markdown happy path (AC #1, #5) ───────────────


async def test_render_includes_all_sections(env, monkeypatch):
    """AC #1: end-to-end render contains every section + H1 title."""
    _patch_source(env, monkeypatch, {})
    pid, cid, _ = _seed(env)
    out = await env.export.render_export_markdown(pid, cid, workspace_id="app")
    assert out.startswith("# Design Brief: My Feature")
    for marker in (
        "## PRD Reference", "## Design Spec", "## Live Prototype",
        "## Generated Prototype Source", "## Iteration History",
    ):
        assert marker in out


async def test_render_includes_staged_source_files(env, monkeypatch):
    """AC #5: a post-P2-04 prototype renders one fenced block per staged source file."""
    files = {"src/App.tsx": "export default function App(){ return <div/>; }",
             "src/index.css": "body { margin: 0; }"}
    _patch_source(env, monkeypatch, files)
    pid, cid, _ = _seed(env)
    out = await env.export.render_export_markdown(pid, cid, workspace_id="app")
    src_section = out.split("## Generated Prototype Source", 1)[1]
    assert "### src/App.tsx" in src_section
    assert "```tsx" in src_section
    assert "### src/index.css" in src_section
    assert "```css" in src_section
    assert src_section.index("### src/App.tsx") < src_section.index("### src/index.css")


async def test_render_no_source_files_shows_fallback(env, monkeypatch):
    """AC #5 (historical pre-P2-04 path): empty source dict → fallback message."""
    _patch_source(env, monkeypatch, {})
    pid, cid, _ = _seed(env, bundle_url="https://hist.example/p/1/index.html")
    out = await env.export.render_export_markdown(pid, cid, workspace_id="app")
    assert "_Source files not staged for this checkpoint." in out
    assert "https://hist.example/p/1/index.html" in out


async def test_render_includes_iteration_history(env, monkeypatch):
    _patch_source(env, monkeypatch, {})
    history = [{"role": "user", "content": "tighten the spacing"}]
    pid, cid, _ = _seed(env, prompt_history=history)
    out = await env.export.render_export_markdown(pid, cid, workspace_id="app")
    assert "### Turn 1 (user)" in out
    assert "tighten the spacing" in out


# ─── integration: determinism (AC #2) ─────────────────────────────────────────


async def test_render_is_deterministic_with_frozen_clock(env, monkeypatch):
    """AC #2: two renders with a frozen clock are byte-identical."""
    from datetime import datetime as _real_dt

    class _FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return _real_dt(2026, 5, 29, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(env.export, "datetime", _FrozenDateTime)
    _patch_source(env, monkeypatch, {"src/App.tsx": "export default () => null;"})
    pid, cid, _ = _seed(env, prompt_history=[{"role": "user", "content": "x"}])
    first = await env.export.render_export_markdown(pid, cid, workspace_id="app")
    second = await env.export.render_export_markdown(pid, cid, workspace_id="app")
    assert first == second
    assert "2026-05-29T12:00:00+00:00" in first


# ─── integration: error handling (AC #8, #9, #10) + missing PRD ───────────────


async def test_render_raises_on_missing_prototype(env, monkeypatch):
    """AC #8: unknown prototype id → ValueError."""
    _patch_source(env, monkeypatch, {})
    _, cid, _ = _seed(env)
    with pytest.raises(ValueError, match="prototype 99999 not found"):
        await env.export.render_export_markdown(99999, cid, workspace_id="app")


async def test_render_raises_on_missing_checkpoint(env, monkeypatch):
    """A valid prototype but unknown checkpoint id → ValueError."""
    _patch_source(env, monkeypatch, {})
    pid, _, _ = _seed(env)
    with pytest.raises(ValueError, match="does not belong to prototype"):
        await env.export.render_export_markdown(pid, 99999, workspace_id="app")


async def test_render_raises_on_checkpoint_mismatch(env, monkeypatch):
    """AC #9: checkpoint belongs to prototype B but caller passes prototype A → ValueError."""
    _patch_source(env, monkeypatch, {})
    pid_a, _cid_a, _ = _seed(env)
    pid_b, cid_b, _ = _seed(env)
    assert pid_a != pid_b
    with pytest.raises(ValueError, match="does not belong to prototype"):
        await env.export.render_export_markdown(pid_a, cid_b, workspace_id="app")


async def test_render_workspace_isolated(env, monkeypatch):
    """AC #10: a checkpoint seeded under 'app' is invisible to workspace 'demo'."""
    _patch_source(env, monkeypatch, {})
    pid, cid, _ = _seed(env, workspace_id="app")
    # prototype itself is workspace-filtered first → raises on prototype lookup
    with pytest.raises(ValueError, match="not found"):
        await env.export.render_export_markdown(pid, cid, workspace_id="demo")


async def test_render_raises_on_missing_prd(env, monkeypatch):
    """An orphan prototype (prd_id points nowhere) → ValueError."""
    _patch_source(env, monkeypatch, {})
    # Seed a prototype whose prd_id does not resolve.
    pid = env.proto.start_prototype(prd_id=4242, workspace_id="app", template_version=1)
    cid = env.proto.create_checkpoint(
        prototype_id=pid, workspace_id="app", bundle_url="https://x/index.html",
        prd_revision_hash=None, figma_frame_hash=None, prompt_history=[],
    )
    with pytest.raises(ValueError, match="PRD 4242 not found"):
        await env.export.render_export_markdown(pid, cid, workspace_id="app")


# ─── purity (AC #11, #12) ─────────────────────────────────────────────────────


def test_export_module_has_no_forbidden_imports():
    """AC #11: no subprocess / anthropic / httpx / requests anywhere in the source."""
    import inspect
    src = inspect.getsource(export)
    for forbidden in ("subprocess", "anthropic", "httpx", "requests"):
        assert forbidden not in src, f"forbidden symbol present: {forbidden}"


def test_export_module_does_not_import_llm():
    """AC #12: the module never imports app.llm or app.design_agent.client."""
    import inspect
    src = inspect.getsource(export)
    assert "app.llm" not in src
    assert "app.design_agent.client" not in src

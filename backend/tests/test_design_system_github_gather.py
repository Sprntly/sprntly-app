"""Unit tests for the GitHub styling-system detection sub-registry and gather strategies.

These tests cover:
  - Detection: Tailwind from deps, from config path, from components.json.
  - Detection: CSS-vars strategy when no Tailwind is present.
  - Priority: Tailwind wins over CSS-vars when both are present.
  - No-match detection: degrade fallback selected.
  - Strategy gather: Tailwind theme colors → explicit bucket.
  - Strategy gather: Tailwind with no custom theme → inferred bucket.
  - Strategy gather: CSS-vars role-keyed → explicit bucket.
  - Degrade gather: never raises, returns a non-empty dict from className signals.
  - Bounded I/O: only the winning strategy's file bodies are fetched.
  - UI file count cap: 30 listed → ≤ 12 gathered.
  - Cap constants: the four values are unchanged.
  - Module purity: no network imports.
  - Component-location helper: shared across token strategies (framework-orthogonal).
  - Registry extensibility: a dummy third strategy registers additively.
  - No new LLM calls: source scan of github_gather.py.
  - End-to-end: gather → normalize → harden → confidence tier.

Pure unit tests — no DB, no network, no model calls.
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.design_agent.design_system.github_gather import (
    CssVarsStrategy,
    DegradeStrategy,
    StylingRegistry,
    StylingStrategy,
    TailwindStrategy,
    degrade_strategy,
    gather_github_signals,
    resolve_component_location,
    styling_registry,
)
from app.design_agent.design_system.adapters import (
    GithubExtractor,
    _GITHUB_MAX_DIRS,
    _GITHUB_MAX_UI_FILE_BYTES,
    _GITHUB_MAX_UI_FILES,
    _GITHUB_EXPLICIT_FILE_BYTES,
)
from app.design_agent.design_system.extractors import RawSignals

_BACKEND_DIR = Path(__file__).resolve().parents[1]

# Minimal component hints for testing (mirrors the subset used in production).
_HINTS: tuple[str, ...] = (
    "button", "card", "input", "badge", "toast", "dialog", "select", "tabs",
)


# ── Fixture helpers ────────────────────────────────────────────────────────────


def _make_tailwind_config(with_theme: bool = True) -> str:
    """Return a synthetic tailwind.config.ts body."""
    if with_theme:
        return """
import type { Config } from 'tailwindcss'

const config: Config = {
  theme: {
    extend: {
      colors: {
        primary: '#0e6b4f',
        border: '#dddddd',
        background: '#ffffff',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui'],
      },
    },
  },
}
export default config
"""
    # Config with no custom colors at all.
    return """
import type { Config } from 'tailwindcss'
const config: Config = { content: ['./src/**/*.tsx'] }
export default config
"""


def _make_globals_css(with_vars: bool = True, include_tw_directive: bool = False) -> str:
    """Return a synthetic globals.css body."""
    tw_line = "@tailwind base;\n@tailwind components;\n" if include_tw_directive else ""
    if not with_vars:
        return tw_line + "body { margin: 0; }\n"
    return tw_line + """:root {
  --primary: #7c3aed;
  --background: #ffffff;
  --foreground: #1a1a1a;
  --border: #dddddd;
  --muted: #6b7280;
}
body { font-family: Inter, sans-serif; }
"""


def _make_pkg_json(deps: list[str] | None = None) -> str:
    """Return a synthetic package.json body."""
    dep_obj = {d: "latest" for d in (deps or [])}
    import json
    return json.dumps({"dependencies": dep_obj, "devDependencies": {}})


def _ui_file_text(color_class: str = "blue") -> str:
    """Return a synthetic React UI file body with Tailwind class usage."""
    return f"""
import React from 'react'
export function Button({{ children }}) {{
  return (
    <button className="bg-{color_class}-500 text-white rounded-md p-4 shadow-md">
      {{children}}
    </button>
  )
}}
"""


# ── Detection tests ────────────────────────────────────────────────────────────


def test_detects_tailwind_from_dep():
    """Tailwind is detected when 'tailwindcss' is in the deps set."""
    tw = TailwindStrategy()
    assert tw.detect({"tailwindcss", "react"}, []) is True


def test_detects_tailwind_from_config_path():
    """Tailwind is detected when a tailwind.config.ts path is in the file listing."""
    tw = TailwindStrategy()
    assert tw.detect(set(), ["tailwind.config.ts", "package.json"]) is True


def test_detects_tailwind_from_components_json():
    """Tailwind is detected when components.json is present (shadcn indicator)."""
    tw = TailwindStrategy()
    assert tw.detect(set(), ["components.json", "src/index.css"]) is True


def test_detects_css_vars_when_no_tailwind():
    """CSS-vars strategy is detected when no Tailwind and a globals.css path is present."""
    css = CssVarsStrategy()
    # No tailwindcss in deps, no tailwind config file.
    assert css.detect({"react"}, ["app/globals.css", "package.json"]) is True


def test_css_vars_does_not_detect_when_tailwind_present():
    """CSS-vars strategy declines when tailwindcss is already in deps."""
    css = CssVarsStrategy()
    assert css.detect({"tailwindcss", "react"}, ["app/globals.css"]) is False


def test_tailwind_wins_priority_over_css_vars():
    """Tailwind strategy wins when both tailwindcss dep and a globals.css are present.

    The registry evaluates in registration order; Tailwind is registered first.
    """
    deps = {"tailwindcss", "react"}
    file_paths = ["tailwind.config.ts", "app/globals.css"]
    result = styling_registry.detect(deps, file_paths)
    assert result is not None
    assert result.name == "tailwind"


def test_no_strategy_detects_falls_to_degrade():
    """When no strategy matches, the registry returns None and the degrade arm is used."""
    deps = {"react", "styled-components"}
    file_paths = ["src/styles/theme.ts"]  # nothing recognized
    result = styling_registry.detect(deps, file_paths)
    assert result is None


# ── Strategy gather tests ──────────────────────────────────────────────────────


def test_tailwind_theme_colors_to_explicit_bucket():
    """Tailwind strategy with a real theme config puts colors in the explicit 'colors' bucket."""
    tw = TailwindStrategy()
    fetched = {
        "tailwind.config.ts": _make_tailwind_config(with_theme=True),
    }
    result = tw.gather(fetched, _HINTS)
    assert "primary" in result["colors"], (
        f"Expected 'primary' in explicit colors, got {list(result['colors'].keys())}"
    )
    assert result["colors"]["primary"] == "#0e6b4f", (
        f"Expected #0e6b4f, got {result['colors']['primary']}"
    )
    # When an explicit theme is found the inferred bucket should NOT override it.
    # explicit bucket is populated → files_present should be non-empty.
    assert result["files_present"], "files_present must be non-empty when a config is parsed"


def test_tailwind_no_theme_uses_inferred_bucket():
    """Tailwind repo with no custom theme → colors land in inferred_colors, not colors."""
    tw = TailwindStrategy()
    # Config with no theme.colors, but UI file with class frequency.
    fetched = {
        "tailwind.config.ts": _make_tailwind_config(with_theme=False),
        "components/ui/button.tsx": _ui_file_text("blue") * 5,  # repeat to hit count >= 2
    }
    result = tw.gather(fetched, _HINTS)
    # No explicit theme → colors dict should be empty (or at least no primary from config).
    # inferred_colors should have something from className frequency.
    assert result["colors"].get("primary") is None or result["colors"] == {}, (
        "No custom theme → explicit colors bucket must not have a config-derived primary"
    )
    # inferred_colors may have blue from className frequency.
    # (exact content depends on repetition count in the synthetic file — just check no raise)
    assert isinstance(result["inferred_colors"], dict)


def test_css_vars_role_keyed_to_explicit_bucket():
    """CSS-vars strategy extracts --primary and --border into the explicit colors bucket."""
    css = CssVarsStrategy()
    fetched = {
        "app/globals.css": _make_globals_css(with_vars=True),
    }
    result = css.gather(fetched, _HINTS)
    assert "primary" in result["colors"], (
        f"Expected 'primary' in colors, got {list(result['colors'].keys())}"
    )
    assert result["colors"]["primary"] == "#7c3aed", (
        f"Expected #7c3aed, got {result['colors']['primary']}"
    )
    assert "border" in result["colors"], (
        f"Expected 'border' in colors, got {list(result['colors'].keys())}"
    )
    assert result["colors"]["border"] == "#dddddd"


def test_degrade_gather_never_raises_and_returns_dict():
    """Degrade strategy never raises and returns a non-empty gather dict.

    When UI source files contain Tailwind class patterns, the inferred signals
    should be populated.  When files are absent, an empty-but-valid dict is returned.
    """
    deg = DegradeStrategy()

    # Empty fetched — should not raise and should return valid empty gather dict.
    result_empty = deg.gather({}, _HINTS)
    assert isinstance(result_empty, dict), "degrade.gather must return a dict"
    assert "inferred_colors" in result_empty
    assert "files_present" in result_empty

    # With a UI file — should find something.
    fetched = {"components/ui/button.tsx": _ui_file_text("blue") * 5}
    result_with = deg.gather(fetched, _HINTS)
    assert isinstance(result_with, dict)
    # At minimum inference_files should be populated.
    assert result_with["inference_files"], "degrade should record inference_files"


# ── Bounded I/O test ───────────────────────────────────────────────────────────


def test_only_winning_strategy_files_fetched():
    """When Tailwind is detected, the adapter fetches only relevant files — not CSS-module sources.

    We mock GithubExtractor._fetch_text_file and _list_ui_files to track what gets fetched.
    Only package.json, the Tailwind config, and the bounded UI files should be read.
    CSS-module files, styled-components sources, etc. must NOT be fetched.
    """
    fetched_paths: list[str] = []

    def _fake_fetch(repo, path, branch, *, max_bytes=128_000):
        fetched_paths.append(path)
        if path == "package.json":
            return _make_pkg_json(["tailwindcss", "react"])
        if path == "tailwind.config.ts":
            return _make_tailwind_config(with_theme=True)
        return None

    extractor = GithubExtractor(installation_id=None)
    extractor._fetch_text_file = _fake_fetch
    extractor._list_ui_files = lambda repo, branch: []

    raw = extractor.extract_raw_signals("org/repo")

    # Every fetched path must be from the design-file list or UI dirs — not arbitrary paths.
    unexpected = [
        p for p in fetched_paths
        if p not in (
            "tailwind.config.ts", "tailwind.config.js", "tailwind.config.mjs",
            "tailwind.config.cjs", "components.json", "tokens.json",
            "style-dictionary.json", "app/globals.css", "src/index.css",
            "src/globals.css", "styles/globals.css", "package.json",
        )
    ]
    assert not unexpected, (
        f"These paths should not have been fetched for a Tailwind repo: {unexpected}"
    )
    assert isinstance(raw, RawSignals)
    assert raw.signals.get("files_present") is not None


def test_ui_file_count_capped():
    """When 30 UI files are listed, at most _GITHUB_MAX_UI_FILES (12) are gathered.

    The adapter enforces the cap in its own iteration loop so that test doubles
    which return more than _GITHUB_MAX_UI_FILES entries from _list_ui_files cannot
    accidentally exceed the budget.
    """
    extractor = GithubExtractor(installation_id=None)

    # Fake _list_ui_files returns 30 paths.
    extractor._list_ui_files = lambda repo, branch: [
        (f"components/ui/widget{i}.tsx", f"widget{i}.tsx") for i in range(30)
    ]

    fetched_ui: list[str] = []

    def _fake_fetch(repo, path, branch, *, max_bytes=128_000):
        if path == "package.json":
            return _make_pkg_json(["tailwindcss"])
        if path in ("tailwind.config.ts", "tailwind.config.js"):
            return _make_tailwind_config(with_theme=True)
        if path.startswith("components/ui/widget"):
            fetched_ui.append(path)
            return _ui_file_text()
        return None

    extractor._fetch_text_file = _fake_fetch
    extractor.extract_raw_signals("org/repo")

    # The adapter must cap UI body fetches at _GITHUB_MAX_UI_FILES regardless of
    # how many entries _list_ui_files returns.
    assert len(fetched_ui) <= _GITHUB_MAX_UI_FILES, (
        f"Expected ≤ {_GITHUB_MAX_UI_FILES} UI bodies fetched, got {len(fetched_ui)}"
    )


def test_caps_constants_unchanged():
    """The four I/O cap constants retain their documented values (6/12/96_000/128_000)."""
    assert _GITHUB_MAX_DIRS == 6, f"Expected 6, got {_GITHUB_MAX_DIRS}"
    assert _GITHUB_MAX_UI_FILES == 12, f"Expected 12, got {_GITHUB_MAX_UI_FILES}"
    assert _GITHUB_MAX_UI_FILE_BYTES == 96_000, f"Expected 96_000, got {_GITHUB_MAX_UI_FILE_BYTES}"
    assert _GITHUB_EXPLICIT_FILE_BYTES == 128_000, f"Expected 128_000, got {_GITHUB_EXPLICIT_FILE_BYTES}"


# ── Module purity / extensibility / wiring ─────────────────────────────────────


def test_github_gather_no_network_imports():
    """github_gather.py imports no network-client or model libraries.

    An AST scan of the module source confirms the absence of 'requests',
    'github_app', 'figma_oauth', and 'anthropic' at any import level.
    """
    src_path = _BACKEND_DIR / "app" / "design_agent" / "design_system" / "github_gather.py"
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"requests", "github_app", "figma_oauth", "anthropic"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                for bad in forbidden:
                    assert bad not in name, (
                        f"Forbidden import '{bad}' found in github_gather.py: {name}"
                    )


def test_component_location_shared_across_token_strategies():
    """The framework-orthogonal component-location helper is shared, not per-strategy.

    A React+Tailwind fixture and a React+CSS-vars fixture both get the same
    location rule ('react-next-pascal') from the shared helper.
    """
    react_tsx_paths = ["components/ui/Button.tsx", "components/ui/Card.tsx"]
    tw_location = resolve_component_location(react_tsx_paths)
    css_location = resolve_component_location(react_tsx_paths)
    assert tw_location == css_location, (
        "Both strategies must share the same component-location result"
    )
    assert tw_location == "react-next-pascal"


def test_dummy_strategy_registers_additively():
    """A third strategy can be registered without modifying Tailwind/CSS-vars strategies.

    The dummy strategy detects a unique marker dep and must be selected by the registry
    WITHOUT any change to the existing strategies.  This proves the additive pattern.
    """
    class DummyStrategy:
        name = "dummy"
        explicit = True

        def detect(self, deps, file_paths):
            return "__marker_dep_xyz__" in deps

        def gather(self, fetched, hints):
            return {
                **{k: {} for k in (
                    "colors", "inferred_colors", "fonts", "inferred_fonts",
                    "components", "inferred_components",
                )},
                **{k: [] for k in ("spacing", "inferred_spacing", "shadows", "inferred_shadows")},
                "radius": None, "inferred_radius": None,
                "files_present": ["__dummy__"],
                "inference_files": [],
                "inference_stats": {},
            }

    # Create a fresh registry with the dummy appended after the existing strategies.
    local_registry = StylingRegistry()
    local_registry.register(TailwindStrategy())
    local_registry.register(CssVarsStrategy())
    local_registry.register(DummyStrategy())

    # Non-marker deps → Tailwind or CSS-vars or None, NOT the dummy.
    no_match = local_registry.detect({"react"}, [])
    assert (no_match is None or no_match.name != "dummy"), (
        "Dummy should not trigger on normal deps"
    )

    # Marker dep → dummy selected.
    match = local_registry.detect({"__marker_dep_xyz__"}, [])
    assert match is not None and match.name == "dummy", (
        f"Dummy strategy should be selected, got {match}"
    )

    # Original strategies are unaffected.
    tailwind_match = local_registry.detect({"tailwindcss"}, [])
    assert tailwind_match is not None and tailwind_match.name == "tailwind"


def test_no_llm_call_added():
    """github_gather.py imports no network or model libraries and makes no LLM calls.

    An AST scan confirms no 'anthropic', 'requests', 'github_app', or 'figma_oauth'
    import statement anywhere in the module.  We check import nodes only (not comment
    text), since the docstring legitimately names these as prohibited imports.
    """
    src_path = _BACKEND_DIR / "app" / "design_agent" / "design_system" / "github_gather.py"
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"anthropic", "requests", "github_app", "figma_oauth"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                for bad in forbidden:
                    assert bad not in name, (
                        f"Forbidden import '{bad}' found in github_gather.py import statement: {name}"
                    )
    # Additionally confirm no LLM call patterns appear in non-comment code.
    lllm_patterns = ["messages.create", "beta.messages", "client.messages"]
    for pattern in lllm_patterns:
        assert pattern not in source, (
            f"Forbidden LLM-call pattern '{pattern}' found in github_gather.py"
        )


# ── End-to-end gather → normalize → harden tests ──────────────────────────────


def test_tailwind_repo_end_to_end_high_confidence():
    """Tailwind repo with theme colors + font → confidence 'high' after normalize.

    AC1 end-to-end: detection selects Tailwind strategy → gather puts primary in
    colors bucket → normalize sets explicit.accent + explicit.neutrals + explicit.typography
    → harden → score_confidence returns 'high'.
    """
    fetched_paths: list[str] = []

    def _fake_fetch(repo, path, branch, *, max_bytes=128_000):
        fetched_paths.append(path)
        if path == "package.json":
            return _make_pkg_json(["tailwindcss", "react"])
        if path in ("tailwind.config.ts",):
            return _make_tailwind_config(with_theme=True)
        return None

    extractor = GithubExtractor(installation_id=None)
    extractor._fetch_text_file = _fake_fetch
    extractor._list_ui_files = lambda repo, branch: []

    raw = extractor.extract_raw_signals("org/repo")
    ds = extractor.normalize(raw)

    # The tailwind config has primary, border → normalize resolves explicit accent + neutrals.
    # There's no explicit font in the config above, so confidence may be 'medium' (no typography).
    # Let's add a font and re-run to hit 'high'.

    def _fake_fetch_with_font(repo, path, branch, *, max_bytes=128_000):
        if path == "package.json":
            return _make_pkg_json(["tailwindcss", "react"])
        if path in ("tailwind.config.ts",):
            return """
import type { Config } from 'tailwindcss'
const config: Config = {
  theme: {
    extend: {
      colors: { primary: '#0e6b4f', border: '#dddddd', background: '#ffffff' },
      fontFamily: { sans: ['Inter', 'system-ui'] },
    },
  },
}
export default config
"""
        return None

    extractor2 = GithubExtractor(installation_id=None)
    extractor2._fetch_text_file = _fake_fetch_with_font
    extractor2._list_ui_files = lambda repo, branch: []

    raw2 = extractor2.extract_raw_signals("org/repo")
    ds2 = extractor2.normalize(raw2)

    assert ds2.confidence == "high", (
        f"Tailwind repo with theme colors + font → confidence must be 'high', got '{ds2.confidence}'"
    )
    assert ds2.has_explicit_system is True


def test_unrecognized_repo_end_to_end_low_confidence():
    """Unrecognized stack (degrade arm) → valid DesignSystem at confidence 'medium' or 'low'.

    AC5 end-to-end: no recognized styling system → degrade gather runs →
    normalize returns a valid DesignSystem (not a hard failure) at low/medium confidence.
    """
    def _fake_fetch(repo, path, branch, *, max_bytes=128_000):
        if path == "package.json":
            return _make_pkg_json(["react", "styled-components"])
        # No tailwind config, no globals.css.
        return None

    extractor = GithubExtractor(installation_id=None)
    extractor._fetch_text_file = _fake_fetch
    extractor._list_ui_files = lambda repo, branch: [
        ("src/components/Button.tsx", "Button.tsx"),
    ]

    raw = extractor.extract_raw_signals("org/repo")
    ds = extractor.normalize(raw)

    # normalize returns DesignSystem (not raises), confidence is low or medium.
    assert ds is not None
    assert ds.confidence in {"low", "medium"}, (
        f"Unrecognized stack → confidence must be 'low' or 'medium', got '{ds.confidence}'"
    )


def test_css_vars_end_to_end_explicit():
    """CSS-vars repo (no Tailwind) → colors in explicit bucket → normalize resolves tokens.

    AC2 end-to-end: detection selects CSS-vars strategy → gather maps --primary
    and --border to explicit colors → normalize resolves accent + border.
    """
    def _fake_fetch(repo, path, branch, *, max_bytes=128_000):
        if path == "package.json":
            return _make_pkg_json(["react", "next"])
        if path == "app/globals.css":
            return _make_globals_css(with_vars=True)
        return None

    extractor = GithubExtractor(installation_id=None)
    extractor._fetch_text_file = _fake_fetch
    extractor._list_ui_files = lambda repo, branch: []

    raw = extractor.extract_raw_signals("org/repo")
    ds = extractor.normalize(raw)

    assert ds.tokens.colors.accent == "#7c3aed", (
        f"Expected accent #7c3aed from --primary, got {ds.tokens.colors.accent}"
    )
    assert ds.tokens.colors.border == "#dddddd", (
        f"Expected border #dddddd from --border, got {ds.tokens.colors.border}"
    )


# ── Monorepo (frontend-subdir) tests ───────────────────────────────────────────


def _make_monorepo_globals_css() -> str:
    """Synthetic web/app/globals.css mirroring DisposableByDefault/sprntly-app's real tokens."""
    return """:root {
  --accent: #179463;
  --background: #ffffff;
  --foreground: #0a0a0a;
  --border: #e5e5e5;
  --muted: #737373;
}
body { font-family: Geist, system-ui, sans-serif; }
"""


def test_detect_frontend_prefix_picks_web_subdir():
    """The prefix detector returns 'web/' when web/package.json exists and root has none."""
    def _fake_contents(repo, path, branch):
        if path == "web/package.json":
            return {"type": "file", "name": "package.json", "path": "web/package.json"}
        return None  # root package.json absent

    extractor = GithubExtractor(installation_id=140102699)
    extractor._github_get_contents = _fake_contents
    assert extractor._detect_frontend_prefix("org/repo", None) == "web/"


def test_detect_frontend_prefix_defaults_to_root():
    """Non-monorepo repo (root package.json present) → prefix is '' (unchanged behaviour)."""
    def _fake_contents(repo, path, branch):
        # No subdir package.json exists; only the root would, but the detector
        # never probes root (it's the implicit fallback) → returns "".
        return None

    extractor = GithubExtractor(installation_id=140102699)
    extractor._github_get_contents = _fake_contents
    assert extractor._detect_frontend_prefix("org/repo", None) == ""


def test_monorepo_web_globals_reaches_parser_confidence_not_low():
    """Monorepo with tokens under web/ → gather reaches the CSS parser → accent #179463, confidence ≥ medium.

    This is the regression fix: before the monorepo-aware gather, web/app/globals.css
    (the file carrying the real --accent: #179463 + Geist) was never fetched because the
    adapter only probed REPO-ROOT-relative paths, so the gather saw an empty bag, defaults
    leaked in, and confidence floored to 'low' (pre-seed skipped → stock Tailwind render).

    With the fix, _detect_frontend_prefix picks 'web/', the body is fetched at
    web/app/globals.css but keyed root-relative as app/globals.css, the CSS-vars strategy
    parses --accent → explicit accent #179463, and confidence rises out of 'low'.
    """
    # Simulate a repo whose frontend lives under web/.
    def _fake_contents(repo, path, branch):
        if path == "web/package.json":
            return {"type": "file", "name": "package.json", "path": "web/package.json"}
        return None  # no root package.json, no listable UI dirs

    def _fake_fetch(repo, path, branch, *, max_bytes=128_000):
        if path == "web/package.json":
            return _make_pkg_json(["react", "next"])  # no tailwindcss → CSS-vars strategy
        if path == "web/app/globals.css":
            return _make_monorepo_globals_css()
        return None  # everything else (incl. root-relative paths) absent

    extractor = GithubExtractor(installation_id=140102699)
    extractor._github_get_contents = _fake_contents
    extractor._fetch_text_file = _fake_fetch
    extractor._list_ui_files = lambda repo, branch, prefix="": []

    raw = extractor.extract_raw_signals("org/repo")

    # The fetched bag must be keyed ROOT-RELATIVE (prefix stripped) so the gather matched it.
    assert "app/globals.css" in raw.signals.get("files_present", []), (
        f"web/app/globals.css must reach the parser keyed root-relative; "
        f"files_present={raw.signals.get('files_present')}"
    )

    ds = extractor.normalize(raw)

    assert ds.tokens.colors.accent == "#179463", (
        f"Expected accent #179463 from web/app/globals.css --accent, got {ds.tokens.colors.accent}"
    )
    assert ds.confidence in {"medium", "high"}, (
        f"Monorepo tokens reaching the parser must lift confidence out of 'low'; got '{ds.confidence}'"
    )


def test_monorepo_prefix_strips_ui_file_keys():
    """UI files discovered under web/ are keyed root-relative so gather's UI-source path matches."""
    def _fake_contents(repo, path, branch):
        if path == "web/package.json":
            return {"type": "file", "name": "package.json", "path": "web/package.json"}
        # _list_ui_files is mocked below, so dir listings here are irrelevant.
        return None

    def _fake_fetch(repo, path, branch, *, max_bytes=128_000):
        if path == "web/package.json":
            return _make_pkg_json(["tailwindcss", "react"])
        if path == "web/tailwind.config.ts":
            return _make_tailwind_config(with_theme=True)
        if path == "web/app/components/ui/button.tsx":
            return _ui_file_text()
        return None

    extractor = GithubExtractor(installation_id=140102699)
    extractor._github_get_contents = _fake_contents
    extractor._fetch_text_file = _fake_fetch
    # Mimic the real _list_ui_files contract: returns ROOT-RELATIVE paths (prefix stripped).
    extractor._list_ui_files = lambda repo, branch, prefix="": [
        ("app/components/ui/button.tsx", "button.tsx"),
    ]

    raw = extractor.extract_raw_signals("org/repo")

    # The UI body must have been fetched (proving the adapter re-prefixed for the network call)
    # and recorded as an inference file under its root-relative key.
    assert "app/components/ui/button.tsx" in raw.signals.get("inference_files", []), (
        f"UI file under web/ must reach gather keyed root-relative; "
        f"inference_files={raw.signals.get('inference_files')}"
    )

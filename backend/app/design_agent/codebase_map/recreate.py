"""Deterministic recreate-pre-seed sources for the Design Agent run.

The locate gate hands the runner a single ``LocatedScreen`` identifying which
real customer screen this PRD targets. This module reads that screen's source
(plus its direct child components, the app shell, the theme files, and the
brand logo asset) deterministically from the bounded repo reader, and shapes
the bytes for injection into the agent's virtual filesystem + user prompt.

Scope: deterministic READ + SHAPING. No LLM calls. No free-roam tree walks —
every path read is derived from the MapResult's already-known nodes or from a
small enumerated set of conventional candidates (the underlying shell + theme
file paths are discarded during map extraction, so this layer re-discovers
them by fetching focused candidates and letting the reader silently drop the
ones that do not exist).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .repo_reader import read_repo
from .types import MapResult, ScreenNode

logger = logging.getLogger(__name__)


# Conventional locations for the app shell component. MapResult.shell carries
# the structured nav/brand/logo data but discards the underlying file path
# during extraction; we re-fetch a focused candidate set and let the reader
# silently drop non-existent paths.
SHELL_CANDIDATES: tuple[str, ...] = (
    # src/components/ — both .tsx and .jsx (top-level variants)
    "src/components/Sidebar.tsx",
    "src/components/Sidebar.jsx",
    "src/components/AppShell.tsx",
    "src/components/AppShell.jsx",
    "src/components/Shell.tsx",
    "src/components/Shell.jsx",
    "src/components/Navigation.tsx",
    "src/components/Navigation.jsx",
    "src/components/Layout.tsx",
    "src/components/Layout.jsx",
    # src/components/layout/ — nested layout subdir (.jsx first, then .tsx)
    "src/components/layout/Sidebar.jsx",
    "src/components/layout/Sidebar.tsx",
    "src/components/layout/AppLayout.jsx",
    "src/components/layout/AppLayout.tsx",
    "src/components/layout/TopBar.jsx",
    "src/components/layout/TopBar.tsx",
    # src/layout/ — flat layout directory alternative
    "src/layout/Sidebar.jsx",
    "src/layout/Sidebar.tsx",
    "src/layout/AppLayout.jsx",
    "src/layout/AppLayout.tsx",
    # app/components/ — both .tsx and .jsx
    "app/components/Sidebar.tsx",
    "app/components/Sidebar.jsx",
    "app/components/AppShell.tsx",
    "app/components/AppShell.jsx",
    "app/components/Shell.tsx",
    "app/components/Shell.jsx",
    "app/components/Navigation.tsx",
    "app/components/Navigation.jsx",
    "app/components/Layout.tsx",
    "app/components/Layout.jsx",
    # app/layout/ — flat layout directory alternative
    "app/layout/Sidebar.jsx",
    "app/layout/Sidebar.tsx",
    "app/layout/AppLayout.jsx",
    "app/layout/AppLayout.tsx",
    # components/ — top-level (both .tsx and .jsx)
    "components/Sidebar.tsx",
    "components/Sidebar.jsx",
    "components/Layout.tsx",
    "components/Layout.jsx",
)

# Conventional locations for the global CSS + Tailwind config. Same pattern:
# enumerate, let the reader silently drop the ones that are not present.
THEME_CANDIDATES: tuple[str, ...] = (
    "src/index.css",
    "app/globals.css",
    "src/styles/globals.css",
    "styles/globals.css",
    "tailwind.config.ts",
    "tailwind.config.js",
    "tailwind.config.cjs",
    "tailwind.config.mjs",
)


@dataclass(frozen=True)
class LocatedScreen:
    """The locate gate's hand-off to the recreate pre-seed.

    Carries the chosen screen IDENTITY plus the map it was resolved from,
    never the source bytes — the bytes are read here, deterministically,
    pinned to the map's commit SHA. A multi-screen journey is carried by
    placing the primary screen in ``node`` and any additional screens in
    ``also``; the single-screen case (``also == ()``) is the default.
    """

    map_result: MapResult
    node: ScreenNode
    also: tuple[ScreenNode, ...] = ()
    confidence: int = 0


@dataclass(frozen=True)
class RecreateSources:
    """The shaped output of ``read_located_sources``: the bytes the agent will
    re-express, plus the metadata the prompt builder needs to identify them.

    ``files`` carries only paths that resolved to a real body — missing
    candidates are silently dropped, so the prompt simply lists fewer
    reference files rather than failing the recreate path.
    """

    repo: str
    commit_sha: str
    files: dict[str, str]
    screen_path: str
    also_screen_paths: tuple[str, ...]


def _component_paths(m: MapResult, names: list[str]) -> set[str]:
    """Resolve a screen's ``composed_components`` (component names) to
    repo-relative file paths via the existing node table.

    Names that do not resolve to a known node are silently skipped — a
    missing child is a recognizability gap, not a failure of the recreate
    pre-seed.
    """
    by_name: dict[str, str] = {}
    for n in m.nodes:
        if n.entry_component and n.file and n.entry_component not in by_name:
            by_name[n.entry_component] = n.file
    return {by_name[name] for name in names if name in by_name}


def _shell_paths(_m: MapResult) -> set[str]:
    """Conventional shell file candidates."""
    return set(SHELL_CANDIDATES)


def _theme_paths(_m: MapResult) -> set[str]:
    """Conventional theme file candidates (global CSS + Tailwind config)."""
    return set(THEME_CANDIDATES)


def _asset_paths(m: MapResult) -> set[str]:
    """The brand logo file when the shell renders an image asset.

    ``img_src`` and ``imported_asset`` carry a path the agent must
    reproduce; ``inline_svg``, ``text``, and ``absent`` carry no asset
    file. Lightly normalize the leading ``/`` and ``./`` so the path joins
    with the repo's tree listing rather than failing as an absolute URL.
    """
    logo = m.shell.logo
    if logo.render_kind not in ("img_src", "imported_asset"):
        return set()
    ref = (logo.asset_ref or "").strip()
    if not ref:
        return set()
    if ref.startswith("./"):
        ref = ref[2:]
    elif ref.startswith("/"):
        ref = ref[1:]
    return {ref} if ref else set()


def read_located_sources(
    located: LocatedScreen,
    installation_id: int,
) -> "RecreateSources | None":
    """Read EXACTLY the map-known + conventional-candidate paths for the
    located screen, the shell, and the theme.

    One bounded ``read_repo`` call — no per-file fetch loop, no second tree
    walk. ``ref`` is pinned to ``located.map_result.commit_sha`` so the
    bytes the agent sees match the map it was located against.

    Returns ``None`` when ``read_repo`` returns ``None`` (no installation,
    SHA resolution failed, empty tree). The caller degrades to the
    token/primitive pre-seed in that case.
    """
    m = located.map_result
    paths: set[str] = set()
    if located.node.file:
        paths.add(located.node.file)
    paths |= _component_paths(m, list(located.node.composed_components))
    for extra_node in located.also:
        if extra_node.file:
            paths.add(extra_node.file)
        paths |= _component_paths(m, list(extra_node.composed_components))
    paths |= _shell_paths(m)
    paths |= _theme_paths(m)
    paths |= _asset_paths(m)

    extra = sorted(paths)
    snapshot = read_repo(
        installation_id,
        m.repo,
        m.commit_sha,
        extra_paths=extra,
    )
    if snapshot is None:
        return None

    requested = set(extra)
    files = {p: snapshot.files[p] for p in requested if p in snapshot.files}
    return RecreateSources(
        repo=m.repo,
        commit_sha=m.commit_sha,
        files=files,
        screen_path=located.node.file,
        also_screen_paths=tuple(n.file for n in located.also if n.file),
    )


def recreate_pre_seed(
    virtual_fs: dict[str, str],
    located_screen: LocatedScreen,
    installation_id: int | None,
    prototype_id: int,
) -> "RecreateSources | None":
    """Inject the located screen's deterministic sources into ``virtual_fs``.

    Calls ``read_located_sources`` for one bounded repo read, then writes each
    fetched body into the virtual filesystem under the ``__reference__/<path>``
    prefix so the agent can view long files via the ``view`` tool rather than
    bloating the prompt. The prefix is stripped before the build-facing
    virtual_fs is returned by the caller, so the staged prototype never
    contains reference bytes.

    Returns the ``RecreateSources`` (for downstream prompt-block rendering),
    or ``None`` when the read fails or yields no usable files — the caller
    leaves the token / primitive pre-seed in place and skips the prompt
    rewrite.
    """
    if not installation_id:
        return None
    sources = read_located_sources(located_screen, installation_id)
    if sources is None:
        logger.warning(
            "design_agent.recreate_pre_seed_unreadable prototype_id=%s repo=%s",
            prototype_id,
            located_screen.map_result.repo,
        )
        return None
    if not sources.files:
        return None
    for path, body in sources.files.items():
        virtual_fs[f"__reference__/{path}"] = body
    screen_label = located_screen.node.entry_component or located_screen.node.file
    logger.info(
        "design_agent.recreate_pre_seed prototype_id=%s repo=%s sha=%s screen=%s n_reference_files=%d posture=%s",
        prototype_id,
        sources.repo,
        sources.commit_sha,
        screen_label,
        len(sources.files),
        located_screen.map_result.posture,
    )
    return sources


# ── Theme/font bridge ────────────────────────────────────────────────────────
# Ordered by likelihood of presence in real projects.
_CSS_GLOBALS_KEYS: tuple[str, ...] = (
    "app/globals.css",
    "src/styles/globals.css",
    "styles/globals.css",
    "src/index.css",
)

_TAILWIND_CONFIG_KEYS: tuple[str, ...] = (
    "tailwind.config.ts",
    "tailwind.config.js",
    "tailwind.config.cjs",
    "tailwind.config.mjs",
)

# Matches @import url(...) lines that must be hoisted above @tailwind directives.
# The CSS spec requires @import to precede all other rules (except @charset).
_FONT_IMPORT_RE = re.compile(
    r"@import\s+url\s*\([^)]+\)[^;]*;\s*",
    re.IGNORECASE,
)

# Matches a Tailwind v4 @theme { ... } block.
_V4_THEME_BLOCK_RE = re.compile(r"@theme\s*\{")


def _find_globals_css(sources: RecreateSources) -> str:
    for key in _CSS_GLOBALS_KEYS:
        body = sources.files.get(key, "")
        if body:
            return body
    return ""


def _find_tailwind_config(sources: RecreateSources) -> str:
    for key in _TAILWIND_CONFIG_KEYS:
        body = sources.files.get(key, "")
        if body:
            return body
    return ""


def _is_tailwind_v4_with_tokens(css: str) -> bool:
    """Return True when the CSS contains a @theme {} block with custom tokens."""
    m = _V4_THEME_BLOCK_RE.search(css)
    if not m:
        return False
    start = m.end()
    depth, i = 1, start
    while i < len(css) and depth > 0:
        ch = css[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    block = css[start : i - 1]
    return bool(re.search(r"--[\w-]+\s*:", block))


def _extract_font_imports(css: str) -> str:
    """Return all @import url(...) lines joined by newlines (for hoisting)."""
    return "\n".join(m.group(0).strip() for m in _FONT_IMPORT_RE.finditer(css))


def _strip_font_imports(css: str) -> str:
    """Remove @import url(...) lines from a CSS string (they are hoisted)."""
    return _FONT_IMPORT_RE.sub("", css).strip()


def _extract_theme_extend(config_src: str) -> str:
    """Extract the content of theme.extend { ... } from a Tailwind config string."""
    m = re.search(r"extend\s*:\s*\{", config_src)
    if not m:
        return ""
    start = m.end()
    depth, i = 1, start
    while i < len(config_src) and depth > 0:
        ch = config_src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return config_src[start : i - 1].strip()


def _summarize_extend(extend_block: str) -> str:
    """Return a compact summary of theme.extend keys and their top-level names."""
    key_pattern = re.compile(r"(\w+)\s*:\s*\{")
    lines: list[str] = []
    for m in key_pattern.finditer(extend_block):
        key = m.group(1)
        block_start = m.end()
        depth, i = 1, block_start
        while i < len(extend_block) and depth > 0:
            ch = extend_block[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        block_content = extend_block[block_start : i - 1]
        names = re.findall(r"""['"]?([\w-]+)['"]?\s*:""", block_content)
        if names:
            unique = list(dict.fromkeys(names[:8]))
            lines.append(f"  {key}: {', '.join(unique)}")
    return "\n".join(lines)


def bridge_theme(
    scaffold_index_css: str,
    sources: RecreateSources,
    *,
    prototype_id: int = 0,
) -> str:
    """Inline the real globals.css body into the scaffold index.css AFTER the
    @tailwind directives. NEVER @import — PostCSS silently drops a top-level
    @import of a stylesheet that itself uses @layer/@tailwind, shipping unstyled.

    Font @import url(...) lines from the real globals are hoisted above the
    @tailwind directives (CSS spec: @import must precede all other rules).
    The real :root/@layer base tokens follow the scaffold shadcn default block
    so they win the cascade. Returns the scaffold unchanged when no real globals
    are found in sources.
    """
    globals_css = _find_globals_css(sources)
    if not globals_css:
        logger.info(
            "design_agent.theme_bridge prototype_id=%s repo=%s"
            " has_globals=false has_tailwind_extend=false n_font_imports=0 is_v4=false",
            prototype_id,
            sources.repo,
        )
        return scaffold_index_css

    is_v4 = _is_tailwind_v4_with_tokens(globals_css)
    tailwind_src = _find_tailwind_config(sources)
    has_tailwind_extend = bool(tailwind_src and _extract_theme_extend(tailwind_src))
    font_import_matches = _FONT_IMPORT_RE.findall(globals_css)
    n_font_imports = len(font_import_matches)
    font_imports = _extract_font_imports(globals_css)
    globals_body = _strip_font_imports(globals_css)

    logger.info(
        "design_agent.theme_bridge prototype_id=%s repo=%s"
        " has_globals=true has_tailwind_extend=%s n_font_imports=%d is_v4=%s",
        prototype_id,
        sources.repo,
        str(has_tailwind_extend).lower(),
        n_font_imports,
        str(is_v4).lower(),
    )

    parts: list[str] = []
    if font_imports:
        parts.append(font_imports)
    parts.append(scaffold_index_css.strip())
    if globals_body:
        parts.append(globals_body)
    return "\n\n".join(p for p in parts if p)


def port_tailwind_extend(scaffold_config: str, sources: RecreateSources) -> str:
    """Return a compact summary of the real tailwind.config theme.extend for the
    recreate reference block.

    Does NOT produce a config file — tailwind.config.ts is agent-immutable.
    The scaffold config already maps shadcn slots to hsl(var(--token)); the
    inlined :root block from bridge_theme redefines those tokens, so utilities
    resolve to real colours without a config replacement. Custom theme.extend
    names that have no CSS-variable backing are surfaced here so the agent can
    use scaffold-supported equivalents.
    """
    tailwind_src = _find_tailwind_config(sources)
    if not tailwind_src:
        return ""
    extend_block = _extract_theme_extend(tailwind_src)
    if not extend_block:
        return ""
    summary = _summarize_extend(extend_block)
    if not summary:
        return ""
    return f"tailwind.config theme.extend (from {sources.repo}):\n{summary}"


def render_recreate_task_block(
    located: LocatedScreen,
    sources: RecreateSources,
) -> str:
    """Render the user-message task block for the recreate path.

    Identifies the located screen, lists the reference paths the agent must
    view (as ``__reference__/<path>`` entries that match the keys injected
    into the virtual filesystem), and frames the re-express + apply-PRD
    pivot. Discipline wording (no gold-plate, on-theme, etc.) is owned by
    the prompts module and applied alongside this block.
    """
    node = located.node
    ref_paths = sorted(sources.files.keys())
    ref_lines = [f"  - __reference__/{p}" for p in ref_paths] or [
        "  (no reference files resolved)"
    ]
    return (
        "RECREATE TARGET (from the connected codebase)\n"
        "You are re-expressing a REAL product screen, not generating from blank canvas.\n"
        f"Located screen: {node.entry_component} (route {node.route}) "
        f"from {sources.repo}@{sources.commit_sha}.\n"
        "Real source you must re-express (view these reference files):\n"
        + "\n".join(ref_lines)
        + "\nApply the requirements change ON TOP of the re-expressed screen."
    )

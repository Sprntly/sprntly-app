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
import os
import re
from dataclasses import dataclass

from ..prompts import DESIGN_AGENT_RECREATE_DISCIPLINE
from ..storage import ThemeBridgeError
from .repo_reader import read_repo
from .types import LogoAsset, MapResult, ScreenNode

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


# CSS variable VALUES the scaffold ships by default (both :root and .dark).
# Values that appear in a real customer's globals but are NOT in this set are
# discriminating build-gate signals: a scaffold-only bundle (theme not bridged)
# would carry only the defaults, while a bridged bundle carries the real values.
_SCAFFOLD_DEFAULT_VALUES: frozenset[str] = frozenset({
    "0 0% 100%",
    "222.2 84% 4.9%",
    "222.2 47.4% 11.2%",
    "210 40% 98%",
    "210 40% 96.1%",
    "215.4 16.3% 46.9%",
    "0 84.2% 60.2%",
    "214.3 31.8% 91.4%",
    "0.5rem",
})


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


@dataclass
class BrandAssetCarry:
    """Result of carrying the shell brand logo into the recreate virtual filesystem.

    ``virtual_fs_keys`` holds the path→content entries to merge into ``virtual_fs``
    before the agent run (empty for inline_svg / text / absent render kinds).
    ``shell_render_ref`` is the verbatim markup or import line the agent should
    re-use when expressing the shell logo.  ``carried`` is True when file bytes
    were injected via ``virtual_fs_keys``.
    """

    virtual_fs_keys: dict[str, str]
    shell_render_ref: str
    deployed_url: str
    render_kind: str
    carried: bool


# Regex helpers for brand-asset carry — shared by the three render-case functions.
_IMG_TAG_RE = re.compile(r"<img\b[^>]*/?>", re.IGNORECASE)
_SVG_BLOCK_RE = re.compile(r"<svg\b[^>]*>.*?</svg>", re.IGNORECASE | re.DOTALL)


def _normalize_asset_ref(ref: str) -> str:
    """Strip a leading ``/`` or ``./`` so the path matches ``sources.files`` keys."""
    ref = ref.strip()
    if ref.startswith("./"):
        return ref[2:]
    if ref.startswith("/"):
        return ref[1:]
    return ref


def _find_img_tag_in_shell(
    sources: RecreateSources, src_variants: tuple[str, ...]
) -> str:
    """Return the first ``<img>`` tag in any shell candidate that contains a src variant."""
    for key in SHELL_CANDIDATES:
        body = sources.files.get(key, "")
        if not body:
            continue
        for m in _IMG_TAG_RE.finditer(body):
            tag = m.group(0)
            if any(v and v in tag for v in src_variants):
                return tag
    return ""


def _find_inline_svg_in_shell(sources: RecreateSources) -> str:
    """Return the first ``<svg>...</svg>`` block found in any shell candidate."""
    for key in SHELL_CANDIDATES:
        body = sources.files.get(key, "")
        if not body:
            continue
        m = _SVG_BLOCK_RE.search(body)
        if m:
            return m.group(0)
    return ""


def _carry_img_src(
    logo: LogoAsset,
    sources: RecreateSources,
    *,
    prototype_id: int,
) -> "BrandAssetCarry":
    raw_ref = logo.asset_ref or ""
    norm_ref = _normalize_asset_ref(raw_ref)
    basename = os.path.basename(norm_ref) if norm_ref else ""
    vfs_key = f"public/{basename}" if basename else ""

    src_variants = (raw_ref, f"/{norm_ref}", norm_ref, basename)
    img_tag = _find_img_tag_in_shell(sources, src_variants)
    if not img_tag:
        alt = logo.alt_text or ""
        img_tag = f'<img src="{raw_ref}" alt="{alt}" />'

    file_body = sources.files.get(norm_ref, "")
    carried = bool(file_body and vfs_key)
    vfs_keys: dict[str, str] = {vfs_key: file_body} if carried else {}

    logger.info(
        "design_agent.brand_asset prototype_id=%s render_kind=%s carried=%s fallback=%s",
        prototype_id, "img_src", carried, False,
    )
    return BrandAssetCarry(
        virtual_fs_keys=vfs_keys,
        shell_render_ref=img_tag,
        deployed_url="",
        render_kind="img_src",
        carried=carried,
    )


def _carry_imported_asset(
    logo: LogoAsset,
    sources: RecreateSources,
    *,
    prototype_id: int,
) -> "BrandAssetCarry":
    raw_ref = logo.asset_ref or ""
    norm_ref = _normalize_asset_ref(raw_ref)
    file_body = sources.files.get(norm_ref, "")
    carried = bool(file_body and norm_ref)
    vfs_keys: dict[str, str] = {norm_ref: file_body} if carried else {}
    import_ref = raw_ref or norm_ref
    shell_render_ref = f'import logo from "{import_ref}"' if import_ref else ""

    logger.info(
        "design_agent.brand_asset prototype_id=%s render_kind=%s carried=%s fallback=%s",
        prototype_id, "imported_asset", carried, False,
    )
    return BrandAssetCarry(
        virtual_fs_keys=vfs_keys,
        shell_render_ref=shell_render_ref,
        deployed_url="",
        render_kind="imported_asset",
        carried=carried,
    )


def _carry_inline_svg(
    logo: LogoAsset,
    sources: RecreateSources,
    *,
    prototype_id: int,
) -> "BrandAssetCarry":
    svg_markup = _find_inline_svg_in_shell(sources)

    logger.info(
        "design_agent.brand_asset prototype_id=%s render_kind=%s carried=%s fallback=%s",
        prototype_id, "inline_svg", False, False,
    )
    return BrandAssetCarry(
        virtual_fs_keys={},
        shell_render_ref=svg_markup,
        deployed_url="",
        render_kind="inline_svg",
        carried=False,
    )


def _carry_text_or_absent(
    logo: LogoAsset,
    *,
    prototype_id: int,
) -> "BrandAssetCarry":
    wordmark = logo.asset_ref or logo.alt_text or ""

    logger.info(
        "design_agent.brand_asset prototype_id=%s render_kind=%s carried=%s fallback=%s",
        prototype_id, logo.render_kind, False, False,
    )
    return BrandAssetCarry(
        virtual_fs_keys={},
        shell_render_ref=wordmark,
        deployed_url="",
        render_kind=logo.render_kind,
        carried=False,
    )


def carry_brand_asset(
    logo: LogoAsset,
    sources: RecreateSources,
    *,
    prototype_id: int = 0,
) -> BrandAssetCarry:
    """Carry the shell brand logo into the recreate virtual filesystem.

    Deterministic — no LLM calls. Selects the carry strategy from the logo's
    render kind: file-copy for img_src and imported_asset, markup extraction
    for inline_svg, wordmark passthrough for text/absent.

    The returned ``BrandAssetCarry.virtual_fs_keys`` should be merged into
    ``virtual_fs`` before the agent run; ``shell_render_ref`` is appended to
    the recreate task block so the agent knows the exact logo markup to use.
    """
    if logo.render_kind == "img_src":
        return _carry_img_src(logo, sources, prototype_id=prototype_id)
    if logo.render_kind == "imported_asset":
        return _carry_imported_asset(logo, sources, prototype_id=prototype_id)
    if logo.render_kind == "inline_svg":
        return _carry_inline_svg(logo, sources, prototype_id=prototype_id)
    return _carry_text_or_absent(logo, prototype_id=prototype_id)


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
    brand_carry: BrandAssetCarry | None = None,
) -> str:
    """Render the user-message task block for the recreate path.

    Identifies the located screen, lists the reference paths the agent must
    view (as ``__reference__/<path>`` entries that match the keys injected
    into the virtual filesystem), and frames the re-express + apply-PRD
    pivot. Discipline wording (no gold-plate, on-theme, etc.) is owned by
    the prompts module and applied alongside this block.

    When ``brand_carry`` is provided, the shell logo render reference is
    appended so the agent knows the exact markup to reproduce.
    """
    node = located.node
    ref_paths = sorted(sources.files.keys())
    ref_lines = [f"  - __reference__/{p}" for p in ref_paths] or [
        "  (no reference files resolved)"
    ]
    block = (
        "RECREATE TARGET (from the connected codebase)\n"
        "You are re-expressing a REAL product screen, not generating from blank canvas.\n"
        f"Located screen: {node.entry_component} (route {node.route}) "
        f"from {sources.repo}@{sources.commit_sha}.\n"
        "Real source you must re-express (view these reference files):\n"
        + "\n".join(ref_lines)
        + "\nApply the requirements change ON TOP of the re-expressed screen."
    )
    if brand_carry and brand_carry.shell_render_ref:
        block += (
            f"\nBrand logo ({brand_carry.render_kind}): {brand_carry.shell_render_ref}"
        )
    block += f"\n\n{DESIGN_AGENT_RECREATE_DISCIPLINE.strip()}"
    return block


# ── Theme-bridge build gate ───────────────────────────────────────────────────

_VAR_VALUE_RE = re.compile(r"--[\w-]+\s*:\s*([^;}\n]+)")
_FONT_IMPORT_RE_FAMILY = re.compile(
    r"@import\s+url\([^)]*[?&]family=([A-Za-z][^&+):]+)",
    re.IGNORECASE,
)
_FONT_DECL_RE = re.compile(r"font-family\s*:\s*['\"]?([A-Za-z][A-Za-z0-9 _-]+)")


@dataclass(frozen=True)
class ThemeExpectations:
    """Discriminating build-gate signals derived from the real bridged globals.

    token_signals: real CSS variable VALUES (not names) that differ from the
    scaffold defaults — present in the dist only when the bridge succeeded.
    font_families: font-family name strings that must appear in the built CSS.
    class_signals: at least ONE of these Tailwind utility strings must appear
    in the built dist (the agent used brand-colour utilities).
    asset_basename: the carried logo file basename; None when no file asset.
    """

    token_signals: tuple[str, ...]
    font_families: tuple[str, ...]
    class_signals: tuple[str, ...]
    asset_basename: str | None


def build_theme_expectations(
    sources: RecreateSources,
    brand_carry: "BrandAssetCarry | None" = None,
) -> "ThemeExpectations | None":
    """Derive discriminating build-gate signals from the real globals.

    Extracts CSS variable values that differ from the scaffold defaults and
    font-family names. Returns None when no globals are present in sources
    (no assertion can be made without a real baseline), or when no
    discriminating signals are found.
    """
    globals_css = _find_globals_css(sources)
    if not globals_css:
        return None

    raw_values = [m.group(1).strip() for m in _VAR_VALUE_RE.finditer(globals_css)]
    token_signals = tuple(
        v for v in raw_values
        if v and v not in _SCAFFOLD_DEFAULT_VALUES and len(v) > 2
    )[:4]

    font_imports = [
        f.split("+")[0].strip()
        for f in _FONT_IMPORT_RE_FAMILY.findall(globals_css)
    ]
    font_declarations = _FONT_DECL_RE.findall(globals_css)
    raw_fonts = font_imports + font_declarations
    font_families = tuple(dict.fromkeys(f for f in raw_fonts if f))[:3]

    if not token_signals and not font_families:
        return None

    class_signals = ("bg-primary", "text-primary", "text-foreground", "bg-background")

    asset_basename: str | None = None
    if brand_carry is not None and brand_carry.carried:
        vfs_keys = list(brand_carry.virtual_fs_keys.keys())
        if vfs_keys:
            asset_basename = os.path.basename(vfs_keys[0])

    return ThemeExpectations(
        token_signals=token_signals,
        font_families=font_families,
        class_signals=class_signals,
        asset_basename=asset_basename,
    )


def assert_theme_landed(
    dist_files: dict[str, str],
    expected: ThemeExpectations,
) -> None:
    """Grep the built dist for real theme signals. Raise ThemeBridgeError when
    the theme did NOT bridge — a green build that shipped unstyled.

    Checks the full concatenated dist blob (all built CSS/JS assets) for:
    1. Each discriminating token VALUE (not name) from the real globals.
    2. Each font-family name.
    3. At least one semantic class string (purge-tolerant: any-one-of).
    4. The carried logo file basename in blob or dist file keys (when set).
    """
    blob = "\n".join(dist_files.values())
    missing: list[tuple[str, str]] = []

    for token_val in expected.token_signals:
        if token_val not in blob:
            missing.append(("token", token_val))

    for font in expected.font_families:
        if font not in blob:
            missing.append(("font", font))

    if expected.class_signals and not any(c in blob for c in expected.class_signals):
        missing.append(("class", expected.class_signals[0]))

    if expected.asset_basename:
        in_blob = expected.asset_basename in blob
        in_keys = any(expected.asset_basename in k for k in dist_files)
        if not in_blob and not in_keys:
            missing.append(("asset", expected.asset_basename))

    if missing:
        raise ThemeBridgeError(f"theme did not bridge to dist: {missing}")

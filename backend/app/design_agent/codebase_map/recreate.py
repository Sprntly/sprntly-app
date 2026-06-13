"""Deterministic recreate-pre-seed sources for the Design Agent run.

The locate gate hands the runner a single ``LocatedScreen`` identifying which
real customer screen this PRD targets. This module reads that screen's source
(plus its direct child components, the app shell, the theme files, and the
brand logo asset) deterministically from the bounded repo reader, and shapes
the bytes for injection into the agent's virtual filesystem + user prompt.

Scope: deterministic READ + SHAPING. No LLM calls. No free-roam tree walks —
every path read is derived from the MapResult's already-known nodes or from a
small enumerated set of conventional candidates. The real shell file path is
carried on the shell model when extraction found one and is read directly; the
conventional candidate set (and, as a last resort, a layout import-graph walk)
remains the fallback for repos where no real path was recorded, with the reader
silently dropping paths that do not exist.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from ..prompts import DESIGN_AGENT_RECREATE_DISCIPLINE
from ..storage import ThemeBridgeError
from .repo_reader import read_repo
from .types import LogoAsset, MapResult, ScreenNode, ShellModel

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
    # src/components/sidebar/ — dedicated sidebar subdir
    "src/components/sidebar/Sidebar.tsx",
    "src/components/sidebar/Sidebar.jsx",
    "src/components/sidebar/AppSidebar.tsx",
    "src/components/sidebar/AppSidebar.jsx",
    # src/components/nav/ — dedicated nav subdir
    "src/components/nav/Nav.tsx",
    "src/components/nav/Navigation.tsx",
    # App-Router route-group layouts
    "app/(app)/layout.tsx",
    "app/(dashboard)/layout.tsx",
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
    # The app-package prefix in front of the conventional app/ src/ pages/ root
    # (e.g. "web/" for a monorepo where the app lives under web/app/...). Empty
    # for a single-package, repo-root app. Used by the read-side finders to look
    # up prefixed theme/shell keys, not just the bare repo-root candidates.
    app_root_prefix: str = ""
    # The real located shell file path, carried from the map's shell model when
    # extraction found one. The shell finders look this up FIRST (the real nav),
    # before falling back to the conventional candidate keys. Empty when no real
    # shell path was recorded.
    shell_file_path: str = ""


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


def _prefixed_shell_keys(sources: RecreateSources) -> tuple[str, ...]:
    """Shell candidate lookup keys: the carried REAL shell path first (when set),
    then app-prefixed (monorepo) candidates, then bare repo-root candidates."""
    prefix = sources.app_root_prefix
    if prefix:
        candidates = tuple(prefix + c for c in SHELL_CANDIDATES) + SHELL_CANDIDATES
    else:
        candidates = SHELL_CANDIDATES
    real = sources.shell_file_path
    if real and real not in candidates:
        return (real,) + candidates
    return candidates


def _find_img_tag_in_shell(
    sources: RecreateSources, src_variants: tuple[str, ...]
) -> str:
    """Return the first ``<img>`` tag in any shell candidate that contains a src variant."""
    for key in _prefixed_shell_keys(sources):
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
    for key in _prefixed_shell_keys(sources):
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


def _app_root_prefix(located: LocatedScreen) -> str:
    """Derive the app-package prefix in front of the conventional source root.

    The shell/theme candidates are repo-root-relative (``app/``, ``src/``,
    ``pages/``). In a monorepo the app lives under a package dir (e.g.
    ``web/app/(app)/sources/page.tsx``), so those bare candidates never match.
    This returns everything BEFORE the first of ``app/``/``src/``/``pages/`` in
    the located screen's file path (``"web/"`` for the example; ``""`` for a
    repo-root app or when no marker is found).
    """
    file = located.node.file or ""
    for marker in ("app/", "src/", "pages/"):
        idx = file.find(marker)
        if idx != -1:
            return file[:idx]
    return ""


def _shell_paths(_m: MapResult, prefix: str = "") -> set[str]:
    """Conventional shell file candidates (bare + prefixed for monorepos)."""
    out = set(SHELL_CANDIDATES)
    if prefix:
        out |= {prefix + c for c in SHELL_CANDIDATES}
    return out


def _theme_paths(_m: MapResult, prefix: str = "") -> set[str]:
    """Conventional theme file candidates (bare + prefixed for monorepos)."""
    out = set(THEME_CANDIDATES)
    if prefix:
        out |= {prefix + c for c in THEME_CANDIDATES}
    return out


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


# ── Re-export / thin-wrapper following + composed-child resolution ─────────────
# A located page.tsx is frequently a thin re-export of the real screen impl
# (e.g. `export { SourcesScreen } from "../../components/screens/app/SourcesScreen"`
# or an `import { X } from "<rel>"` whose body just renders <X/>). The map's node
# carries no entry_component/composed_components for these wrappers, so the real
# screen file is never read and the recreate sees ~159 bytes of indirection.
# Separately, a screen's rendered children (composed_components NAMES) only
# resolve to files via the existing route-node table when the child is itself a
# route node — a menu / icon-set / panel is not, so its real body is never read.
# Both are closed here by resolving relative / alias module specifiers from the
# located screen body. Bounded: one level of follow, capped target counts.
_MAX_REEXPORT_TARGETS = 3
# Mirrors nodes._MAX_COMPOSED (20): the same magnitude bound on a screen's
# resolvable rendered children, applied here at read time.
_MAX_COMPOSED_PATHS = 20

# `export { Foo } from "<rel>"`, `export * from "<rel>"`, `export { default } from "<rel>"`
_REEXPORT_FROM_RE = re.compile(
    r"""export\s+(?:\*|\{[^}]*\})\s+from\s+['"]([^'"]+)['"]""",
)
# `import { Foo } from "<rel>"` / `import Foo from "<rel>"` — candidate wrapper imports.
_IMPORT_FROM_RE = re.compile(
    r"""import\s+(?:\{[^}]*\}|[A-Za-z_$][\w$]*)\s+from\s+['"]([^'"]+)['"]""",
)
# Full import statement with its binding clause + module specifier, for matching
# a rendered child NAME to the module it was imported from.
_IMPORT_BINDINGS_RE = re.compile(
    r"""import\s+(?P<bindings>[^;'"]+?)\s+from\s+['"](?P<spec>[^'"]+)['"]""",
)


def _path_variants(joined: str) -> list[str]:
    """Extension/index candidate paths for a module path without an extension."""
    if os.path.splitext(joined)[1] in (".tsx", ".ts", ".jsx", ".js"):
        return [joined]
    return [
        f"{joined}.tsx",
        f"{joined}.ts",
        f"{joined}/index.tsx",
        f"{joined}/index.ts",
    ]


def _resolve_rel_to_repo_path(located_file: str, rel: str) -> list[str]:
    """Resolve a relative module specifier against the located file's directory
    to a list of candidate repo-relative paths (extension/index variants).

    Only follows TRUE relative specifiers (``./`` or ``../``); bare-package and
    alias (``@/``) imports are skipped — they are not single-file references we
    can resolve from the located file's directory alone. Any path that escapes
    above the repo root is rejected.
    """
    if not (rel.startswith("./") or rel.startswith("../")):
        return []
    base_dir = os.path.dirname(located_file)
    joined = os.path.normpath(os.path.join(base_dir, rel))
    if joined.startswith(".."):
        return []
    joined = joined.replace(os.sep, "/")
    return _path_variants(joined)


def _resolve_alias_to_repo_paths(spec: str, app_prefix: str) -> list[str]:
    """Resolve an ``@/``-aliased specifier against the app-root prefix.

    The alias target convention varies (some repos map ``@/`` to the app root,
    others to ``<root>/src``); both variants are offered and the reader silently
    drops the one that does not exist.
    """
    if not spec.startswith("@/"):
        return []
    rest = spec[2:]
    out: list[str] = []
    for base in (app_prefix + rest, app_prefix + "src/" + rest):
        out.extend(_path_variants(base))
    return out


def _resolve_spec_to_repo_paths(located_file: str, spec: str, app_prefix: str) -> list[str]:
    """Resolve a module specifier (relative or ``@/`` alias) to candidate paths.
    Bare-package specifiers (``react``, ``lucide-react``) resolve to nothing."""
    if spec.startswith("./") or spec.startswith("../"):
        return _resolve_rel_to_repo_path(located_file, spec)
    if spec.startswith("@/"):
        return _resolve_alias_to_repo_paths(spec, app_prefix)
    return []


def _reexport_targets(located_file: str, body: str) -> list[str]:
    """Return repo-relative candidate paths the located file re-exports/wraps.

    Detects ``export ... from "<rel>"`` first (a hard re-export); falls back to
    ``import { X } from "<rel>"`` when the body looks like a thin wrapper (small
    enough to plausibly be one). Bounded to keep the read set small. Returns
    candidates in priority order, deduped.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(rel: str) -> None:
        for cand in _resolve_rel_to_repo_path(located_file, rel):
            if cand not in seen:
                candidates.append(cand)
                seen.add(cand)

    # Hard re-exports are the strongest signal — always follow these.
    reexport_rels = _REEXPORT_FROM_RE.findall(body)
    for rel in reexport_rels:
        _add(rel)

    # Thin-wrapper heuristic: a tiny page body whose only relative imports are
    # screen-like. Applied only when there were no hard re-exports and the file
    # is small enough to plausibly be a wrapper (avoids dragging in the deps of
    # a real screen).
    if not reexport_rels and len(body) <= 2_048:
        for rel in _IMPORT_FROM_RE.findall(body):
            _add(rel)

    return candidates[:_MAX_REEXPORT_TARGETS]


def _parse_binding_names(bindings: str) -> list[str]:
    """Extract the local names bound by an import clause (default, named, ns)."""
    names: list[str] = []
    brace = re.search(r"\{([^}]*)\}", bindings)
    if brace:
        for part in brace.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            local = part.split(" as ")[-1].strip()  # "B as C" -> C; "A" -> A
            if local:
                names.append(local)
    head = bindings[: brace.start()] if brace else bindings
    for tok in head.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok.startswith("* as "):
            names.append(tok[len("* as "):].strip())
        elif re.match(r"^[A-Za-z_$][\w$]*$", tok):
            names.append(tok)
    return names


def _resolve_composed_component_paths(
    located: LocatedScreen, screen_body: str, app_prefix: str,
) -> set[str]:
    """Resolve the located screen's rendered child NAMES to repo paths via the
    import graph.

    For each ``composed_components`` name, find its ``import { Name } from
    "<spec>"`` / ``import Name from "<spec>"`` specifier in the screen body and
    resolve it (``./``/``../`` relative, ``@/`` alias). Bare-package imports are
    skipped. Complements the route-node lookup in ``_component_paths`` so a
    child resolves whether or not it is itself a route node. Capped to bound the
    read set.
    """
    names = set(located.node.composed_components)
    if not names or not screen_body:
        return set()
    name_to_spec: dict[str, str] = {}
    for m in _IMPORT_BINDINGS_RE.finditer(screen_body):
        spec = m.group("spec")
        for name in _parse_binding_names(m.group("bindings")):
            name_to_spec.setdefault(name, spec)

    out: set[str] = set()
    for name in names:
        spec = name_to_spec.get(name)
        if not spec:
            continue
        for cand in _resolve_spec_to_repo_paths(located.node.file, spec, app_prefix):
            out.add(cand)
            if len(out) >= _MAX_COMPOSED_PATHS:
                return set(sorted(out)[:_MAX_COMPOSED_PATHS])
    return out


# Import-name stems that mark a sidebar/nav/shell component when scanning a
# layout body. Kept local to the read layer (no dependency on the shell
# extractor) and matched as a case-insensitive substring of the imported name.
_SHELL_IMPORT_STEMS: tuple[str, ...] = (
    "sidebar", "appshell", "shell", "navigation", "nav", "topbar", "layout",
)


def _is_shell_import_name(name: str) -> bool:
    low = name.lower()
    return any(stem in low for stem in _SHELL_IMPORT_STEMS)


def _layout_candidate_paths(located: LocatedScreen, app_prefix: str) -> list[str]:
    """App-Router layout files that may wrap the located screen, deepest first.

    Walks the located screen's directory chain down to the ``app/`` root
    (so a route-group layout like ``app/(app)/layout.tsx`` is offered before the
    root ``app/layout.tsx``), then the conventional roots as a floor. Bounded.
    """
    file = (located.node.file or "").replace(os.sep, "/")
    out: list[str] = []
    seen: set[str] = set()

    def _add(p: str) -> None:
        if p and p not in seen:
            out.append(p)
            seen.add(p)

    idx = file.find("app/")
    if idx != -1:
        app_root = file[: idx + len("app/")].rstrip("/")  # ".../app"
        d = file.rsplit("/", 1)[0] if "/" in file else ""
        while d and len(d) >= len(app_root):
            _add(d + "/layout.tsx")
            _add(d + "/layout.jsx")
            if d == app_root:
                break
            d = d.rsplit("/", 1)[0] if "/" in d else ""
    for root in (app_prefix + "app", app_prefix + "src/app"):
        _add(root + "/layout.tsx")
        _add(root + "/layout.jsx")
    return out[:8]


def _discover_shell_path_via_layout(
    located: LocatedScreen, app_prefix: str, files: dict[str, str]
) -> str:
    """Discover a nested shell file by following the wrapping layout's imports.

    Last-resort fallback (after the carried path and the conventional
    candidates): read an App-Router layout body already fetched into ``files``,
    find the imported sidebar/nav/shell component, and resolve its specifier
    (``./``/``../`` relative or ``@/`` alias) to a repo path so a nested
    ``app/components/shared/Sidebar.tsx`` is found. Returns ``""`` when no layout
    body or no shell-looking import resolves (honest empty — never invented).
    """
    for layout_path in _layout_candidate_paths(located, app_prefix):
        body = files.get(layout_path, "")
        if not body:
            continue
        for m in _IMPORT_BINDINGS_RE.finditer(body):
            names = _parse_binding_names(m.group("bindings"))
            if not any(_is_shell_import_name(n) for n in names):
                continue
            cands = _resolve_spec_to_repo_paths(layout_path, m.group("spec"), app_prefix)
            if not cands:
                continue
            for c in cands:
                if c in files:
                    return c
            return cands[0]
    return ""


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
    app_prefix = _app_root_prefix(located)

    # Children resolvable from the map's node table (no body needed).
    route_node_paths: set[str] = _component_paths(m, list(located.node.composed_components))
    for extra_node in located.also:
        route_node_paths |= _component_paths(m, list(extra_node.composed_components))
    shell_paths = _shell_paths(m, app_prefix)
    theme_paths = _theme_paths(m, app_prefix)
    asset_paths = _asset_paths(m)

    # Place the must-read set (located screen + its map-resolved children + the
    # theme/shell candidates that carry globals.css) at the FRONT of extra so the
    # extras-first + always_fetch budget in read_repo fetches them even when the
    # full candidate set exceeds the per-build file cap; everything else trails.
    must_read: list[str] = []
    seen_mr: set[str] = set()

    def _push(path: str) -> None:
        if path and path not in seen_mr:
            must_read.append(path)
            seen_mr.add(path)

    _push(located.node.file)
    for extra_node in located.also:
        _push(extra_node.file)
    # The real shell file (when the map recorded one) is read directly — the
    # actual nav, not a guessed candidate.
    carried_shell = m.shell.shell_file_path or ""
    if carried_shell:
        _push(carried_shell)
    for path in sorted(route_node_paths):
        _push(path)
    for path in sorted(theme_paths):
        _push(path)
    for path in sorted(shell_paths):
        _push(path)
    # Force-include globals.css (prefix-aware) so the theme bridge always has the
    # real CSS to inline when it exists — guaranteed, not best-effort. No-op when
    # already present via the theme candidates above.
    for key in _CSS_GLOBALS_KEYS:
        if app_prefix:
            _push(app_prefix + key)
        _push(key)
    # Layout files that may wrap the located screen — read so a nested shell can
    # be discovered via the layout import graph when no candidate resolves.
    for lp in _layout_candidate_paths(located, app_prefix):
        _push(lp)
    rest = [p for p in sorted(asset_paths) if p not in seen_mr]
    extra = must_read + rest

    snapshot = read_repo(
        installation_id,
        m.repo,
        m.commit_sha,
        extra_paths=extra,
        frontend_root=app_prefix,
    )
    if snapshot is None:
        return None

    requested = set(extra)
    files = {p: snapshot.files[p] for p in requested if p in snapshot.files}

    # The located page (and any `also` screens) are often thin re-exports of the
    # real screen impl, and a screen's rendered children may not be route nodes —
    # neither is captured by the map. Inspect the located bodies just read,
    # resolve their re-export target(s) + composed-child specifiers, and do ONE
    # more bounded read for any that are new, merging the real bodies into files
    # so they feed both the prompt reference list AND the theme/shell scan.
    located_files = [located.node.file] + [n.file for n in located.also if n.file]
    followup: list[str] = []
    followup_seen: set[str] = set(requested)

    def _add_followup(path: str) -> None:
        if path and path not in followup_seen:
            followup.append(path)
            followup_seen.add(path)

    primary_body = files.get(located.node.file, "")
    for child_path in _resolve_composed_component_paths(located, primary_body, app_prefix):
        _add_followup(child_path)
    for lf in located_files:
        body = files.get(lf, "")
        if not body:
            continue
        for target in _reexport_targets(lf, body):
            _add_followup(target)

    # Third-priority shell discovery: fires ONLY when the carried real path was
    # not read AND no conventional candidate body landed. The carried path wins,
    # then candidates, then this layout import-graph walk. Folds into the SAME
    # bounded follow-up read below (no extra read_repo call).
    if carried_shell and files.get(carried_shell):
        shell_present = True
    else:
        if app_prefix:
            cand_keys = tuple(app_prefix + c for c in SHELL_CANDIDATES) + SHELL_CANDIDATES
        else:
            cand_keys = SHELL_CANDIDATES
        shell_present = any(files.get(k) for k in cand_keys)
    discovered_shell = ""
    if not shell_present:
        discovered_shell = _discover_shell_path_via_layout(located, app_prefix, files)
        if discovered_shell:
            _add_followup(discovered_shell)

    if followup:
        followup_paths = sorted(set(followup))
        snapshot2 = read_repo(
            installation_id,
            m.repo,
            m.commit_sha,
            extra_paths=followup_paths,
            frontend_root=app_prefix,
        )
        if snapshot2 is not None:
            for p in followup_paths:
                if p in snapshot2.files and p not in files:
                    files[p] = snapshot2.files[p]

    logger.info(
        "design_agent.recreate_shell_source repo=%s shell_real=%s shell_discovered=%s",
        m.repo,
        str(bool(carried_shell and files.get(carried_shell))).lower(),
        str(bool(discovered_shell and files.get(discovered_shell))).lower(),
    )

    return RecreateSources(
        repo=m.repo,
        commit_sha=m.commit_sha,
        files=files,
        screen_path=located.node.file,
        also_screen_paths=tuple(n.file for n in located.also if n.file),
        app_root_prefix=app_prefix,
        shell_file_path=carried_shell or discovered_shell,
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
    prefix = sources.app_root_prefix
    for key in _CSS_GLOBALS_KEYS:
        if prefix:
            body = sources.files.get(prefix + key, "")
            if body:
                return body
        body = sources.files.get(key, "")
        if body:
            return body
    return ""


def _find_tailwind_config(sources: RecreateSources) -> str:
    prefix = sources.app_root_prefix
    for key in _TAILWIND_CONFIG_KEYS:
        if prefix:
            body = sources.files.get(prefix + key, "")
            if body:
                return body
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


# A :root {} block that declares at least one custom --token (the v3-style
# CSS-variable token system, distinct from the Tailwind-v4 @theme{} shape).
_ROOT_CSS_VAR_RE = re.compile(r":root\s*\{[^}]*--[\w-]+\s*:", re.DOTALL)


def _has_root_css_var_tokens(css: str) -> bool:
    """True when a :root {} block declares at least one custom --token."""
    return bool(_ROOT_CSS_VAR_RE.search(css))


def _theme_token_mode(css: str) -> str:
    """Classify the token system: 'tailwind-v4' (@theme{}), 'css-vars-root'
    (:root{--token}), or 'none'. Tailwind-v4 wins when both are present (a v4
    file can also carry a :root block), so the v4 path stays today's behaviour.
    """
    if _is_tailwind_v4_with_tokens(css):
        return "tailwind-v4"
    if _has_root_css_var_tokens(css):
        return "css-vars-root"
    return "none"


def _assert_bridge_mode_landed(bridged_css: str, mode: str, globals_css: str) -> bool:
    """Confirm the bridged output carries the detected mode's tokens (advisory).

    css-vars-root: at least one real ``:root{--token: value}`` declaration from
    the source whose value is NOT a scaffold default survived into bridged_css.
    tailwind-v4: at least one @theme token value from source survived. none:
    trivially True. Returns a bool — the caller logs it. This is a SOURCE-side
    check (the bridged index.css before build); the dist-side
    ``assert_theme_landed`` remains the single hard gate, so a source-mode miss
    is advisory and never contradicts a passing dist assertion.
    """
    if mode == "none":
        return True
    if not bridged_css:
        return False
    # Find a discriminating real --token: value from the source and require it in
    # the bridged output. The value extraction is the same generic --name: value
    # regex the build gate uses; it works for both :root and @theme blocks.
    for m in _VAR_VALUE_RE.finditer(globals_css):
        value = m.group(1).strip()
        if value and value not in _SCAFFOLD_DEFAULT_VALUES and len(value) > 2:
            return value in bridged_css
    # No discriminating token value in source — nothing to assert against.
    return True


def _extract_extend_color_values(extend_block: str) -> dict[str, str]:
    """Return ``family -> concrete colour literal`` pairs from a tailwind
    ``theme.extend`` block. Only LEAF string values that look like a concrete
    colour (``#hex`` / ``rgb()`` / ``hsl()``) are returned — name-only or
    reference families (``colors.gray[500]``) are skipped so nothing is invented.
    A ``DEFAULT`` leaf is skipped (not a useful utility family name).
    """
    out: dict[str, str] = {}
    pair_re = re.compile(
        r"""['"]?([A-Za-z][\w-]*)['"]?\s*:\s*['"]"""
        r"""(#[0-9A-Fa-f]{3,8}|(?:rgb|hsl)a?\([^)]*\))['"]"""
    )
    for m in pair_re.finditer(extend_block):
        family, value = m.group(1), m.group(2)
        if family.lower() == "default" or family in out:
            continue
        out[family] = value
    return out


def _synthesize_extend_shims(tailwind_src: str) -> str:
    """Build CSS-var + utility shims for tailwind ``theme.extend`` colour
    families that have a concrete value but no CSS-variable backing, so
    ``bg-<family>`` / ``text-<family>`` resolve at build time (tailwind.config is
    agent-immutable). Returns ``""`` when there is nothing concrete to shim —
    never invents a colour for a name-only family.
    """
    if not tailwind_src:
        return ""
    extend_block = _extract_theme_extend(tailwind_src)
    if not extend_block:
        return ""
    values = _extract_extend_color_values(extend_block)
    if not values:
        return ""
    lines: list[str] = [":root {"]
    for family, value in list(values.items())[:8]:
        lines.append(f"  --{family}: {value};")
    lines.append("}")
    for family in list(values.keys())[:8]:
        lines.append(f".bg-{family} {{ background-color: var(--{family}); }}")
        lines.append(f".text-{family} {{ color: var(--{family}); }}")
    return "\n".join(lines)


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
            " has_globals=false has_tailwind_extend=false n_font_imports=0"
            " is_v4=false token_mode=none",
            prototype_id,
            sources.repo,
        )
        return scaffold_index_css

    is_v4 = _is_tailwind_v4_with_tokens(globals_css)
    mode = _theme_token_mode(globals_css)
    tailwind_src = _find_tailwind_config(sources)
    has_tailwind_extend = bool(tailwind_src and _extract_theme_extend(tailwind_src))
    font_import_matches = _FONT_IMPORT_RE.findall(globals_css)
    n_font_imports = len(font_import_matches)
    font_imports = _extract_font_imports(globals_css)
    globals_body = _strip_font_imports(globals_css)

    logger.info(
        "design_agent.theme_bridge prototype_id=%s repo=%s"
        " has_globals=true has_tailwind_extend=%s n_font_imports=%d is_v4=%s token_mode=%s",
        prototype_id,
        sources.repo,
        str(has_tailwind_extend).lower(),
        n_font_imports,
        str(is_v4).lower(),
        mode,
    )

    # The whole globals body is appended after the scaffold regardless of mode
    # (no v4-only short-circuit), so a :root{--token} system inlines exactly like
    # a v4 @theme{} block — the value path is mode-agnostic.
    parts: list[str] = []
    if font_imports:
        parts.append(font_imports)
    parts.append(scaffold_index_css.strip())
    if globals_body:
        parts.append(globals_body)
    # Shim custom tailwind.config theme.extend colour families (no CSS-var
    # backing) so bg-/text-<family> resolve at build time. No-op when none.
    extend_shims = _synthesize_extend_shims(tailwind_src)
    if extend_shims:
        parts.append(extend_shims)
    bridged = "\n\n".join(p for p in parts if p)

    landed = _assert_bridge_mode_landed(bridged, mode, globals_css)
    logger.info(
        "design_agent.theme_bridge_mode_check prototype_id=%s token_mode=%s landed=%s",
        prototype_id,
        mode,
        str(landed).lower(),
    )
    return bridged


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


# A class / className attribute's literal string value.
_CLASS_ATTR_RE = re.compile(r"""class(?:Name)?\s*=\s*["']([^"']*)["']""")
# A bg-/text-/border-<family> Tailwind utility within a class string.
_UTILITY_RE = re.compile(r"\b(bg|text|border)-([a-z][\w-]*)")
# The shadcn-slot floor: kept as a union member so a slot-based brand still has a
# discriminating signal even when no custom utilities are detected.
_STATIC_CLASS_SIGNAL_FLOOR: tuple[str, ...] = (
    "bg-primary", "text-primary", "text-foreground", "bg-background",
)


def _derive_class_signals(
    sources: RecreateSources, globals_css: str, tailwind_src: str,
) -> tuple[str, ...]:
    """Derive discriminating brand utilities from the real source, unioned with
    the static shadcn-slot floor.

    Scans the located screen + shell bodies for ``bg-``/``text-``/``border-``
    utilities whose family corresponds to a real ``--token`` name (from globals)
    or a ``theme.extend`` family name (from tailwind config), so a brand built on
    custom utilities (``bg-accent``) gets a discriminating signal while shadcn-
    slot brands still pass via the floor. The floor is always retained; deduped
    and capped so the any-one-of check stays cheap.
    """
    token_names = {n.lower() for n in re.findall(r"--([\w-]+)\s*:", globals_css)}
    extend_block = _extract_theme_extend(tailwind_src) if tailwind_src else ""
    extend_families = {
        f.lower() for f in re.findall(r"""['"]?([A-Za-z][\w-]*)['"]?\s*:""", extend_block)
    }
    families = token_names | extend_families

    derived: list[str] = []
    seen: set[str] = set()
    for body in sources.files.values():
        for attr in _CLASS_ATTR_RE.findall(body):
            for prefix, family in _UTILITY_RE.findall(attr):
                if family.lower() in families:
                    sig = f"{prefix}-{family}"
                    if sig not in seen:
                        derived.append(sig)
                        seen.add(sig)

    signals = list(_STATIC_CLASS_SIGNAL_FLOOR)
    for sig in derived:
        if sig not in signals:
            signals.append(sig)
        if len(signals) >= 6:
            break
    return tuple(signals)


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

    tailwind_src = _find_tailwind_config(sources)
    class_signals = _derive_class_signals(sources, globals_css, tailwind_src)

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

    token_missing: list[tuple[str, str]] = []
    for token_val in expected.token_signals:
        if token_val not in blob:
            token_missing.append(("token", token_val))

    font_missing: list[tuple[str, str]] = []
    for font in expected.font_families:
        if font not in blob:
            font_missing.append(("font", font))

    missing.extend(token_missing)
    missing.extend(font_missing)

    # class_signals are shadcn utility strings (bg-primary, text-foreground, …).
    # A brand built on raw design tokens (e.g. hsl/hex variables) legitimately
    # styles via the bridged token VALUES + font families without ever emitting
    # these shadcn slot utilities — so a class-signal miss alone is NOT proof the
    # theme failed to bridge. Treat class_signals as a SOFT signal: it binds only
    # when neither token nor font signals confirmed the bridge (the last line of
    # defence). When tokens AND fonts both landed, a class miss is advisory
    # (log-only), never a gate failure.
    token_landed = bool(expected.token_signals) and not token_missing
    font_landed = bool(expected.font_families) and not font_missing
    class_landed = (
        not expected.class_signals
        or any(c in blob for c in expected.class_signals)
    )
    if not class_landed:
        if token_landed and font_landed:
            logger.info(
                "design_agent.theme_class_signal_advisory missing=%s"
                " (token+font landed; class signal is soft)",
                expected.class_signals[0],
            )
        else:
            missing.append(("class", expected.class_signals[0]))

    if expected.asset_basename:
        in_blob = expected.asset_basename in blob
        in_keys = any(expected.asset_basename in k for k in dist_files)
        if not in_blob and not in_keys:
            missing.append(("asset", expected.asset_basename))

    if missing:
        raise ThemeBridgeError(f"theme did not bridge to dist: {missing}")


# ── Interactivity-containment self-check ──────────────────────────────────────
# Two independent axes govern a recreate: the screen is RENDERED faithfully, but
# interactivity is SCOPED to exactly the PRD's interactions — every other control
# renders faithfully yet inert. `derive_interactive_scope` derives that scope
# deterministically from the PRD text + the located node (no upstream object
# carries `interactive_scope`; this is its sole producer), and `assert_containment`
# greps the generated source to confirm the agent kept to it. Both are pure
# string/regex — no LLM, no network. `assert_containment` sits beside
# `assert_theme_landed` on the recreate post-gen gate path; unlike the theme gate
# (which greps the built dist) it greps the agent's generated SOURCE, where the
# event handlers live before the build strips them.

# Interaction verbs/affordances a PRD may name. Each is a scope STEM that
# `assert_containment` matches (case-insensitive substring) against the bound
# handler identifier — e.g. "reconnect" attributes a handler bound to
# `handleReconnect`/`onReconnect`; "expand" attributes `toggleExpand`.
_INTERACTION_VERBS: tuple[str, ...] = (
    "reconnect", "connect", "disconnect",
    "expand", "collapse", "toggle",
    "filter", "sort", "search",
    "submit", "save", "send", "add", "create",
    "remove", "delete", "edit", "update",
    "open", "close", "refresh", "retry",
    "select", "upload", "download",
    "approve", "reject", "apply", "cancel",
    "navigate",
    # Common PRD verbs that must be matched explicitly, not by luck of being a
    # substring of an unrelated word ("test" was previously only credited via
    # "connect" ⊂ "Connection").
    "test", "validate", "verify",
    "import", "export", "share", "copy",
    "login", "logout", "sign",
)

# Verbs that EXTEND an already-interactive surface: the PRD interaction must
# drive existing behaviour to work, so the minimal existing behaviour stays live
# (the entangled case). Each maps to the extra behaviour stem(s) that legitimately
# remain in scope — included ONLY when the PRD signals it extends a live surface.
_ENTANGLED_DRIVERS: dict[str, tuple[str, ...]] = {
    "filter": ("render", "list", "results"),
    "sort": ("render", "list", "results"),
    "search": ("render", "list", "results"),
}

# Cues that the PRD feature extends an EXISTING live surface (vs an isolated
# handler on a static screen). Presence gates the entangled-driver expansion so
# an isolated feature does not silently widen its own scope.
_ENTANGLED_CUES: tuple[str, ...] = (
    "existing", "already", "live", "current",
    "this table", "this list", "the table", "the list",
)

# Scope stems that authorise a live href: navigation is a legitimate PRD
# interaction, so a href is only a containment leak on non-navigation chrome.
_NAV_STEMS: frozenset[str] = frozenset({"navigate", "link", "route"})

# A live React event-handler attribute; the JSX expression container opens at the
# trailing brace. Anchored so `onClick`/`onSubmit`/`onChange` are matched but not
# substrings of unrelated identifiers.
_HANDLER_ATTR_RE = re.compile(r"\b(on(?:Click|Submit|Change))\b\s*=\s*\{", re.IGNORECASE)
# A href attribute; the alternatives capture an expression / double- / single-
# quoted target so an empty or "#" target can be treated as inert.
_HREF_RE = re.compile(
    r"\bhref\s*=\s*(?:\{([^}]*)\}|\"([^\"]*)\"|'([^']*)')", re.IGNORECASE
)
# Interactive-looking elements that need a live handler OR a deliberate inert cue.
_BUTTON_TAG_RE = re.compile(r"<button\b[^>]*>", re.IGNORECASE)
_ROLE_BUTTON_TAG_RE = re.compile(
    r"<[A-Za-z][\w.]*\b[^>]*\brole\s*=\s*[\"']button[\"'][^>]*>", re.IGNORECASE
)
# Affordances that mark an inert control as DELIBERATELY non-interactive.
# `disabled` + `cursor-not-allowed` is the shipped default (see below).
_INERT_AFFORDANCES: tuple[str, ...] = (
    "disabled",
    "cursor-not-allowed",
    "aria-disabled",
    "data-inert",
    "data-out-of-scope",
)


@dataclass(frozen=True)
class ContainmentReport:
    """Result of the post-gen interactivity-containment self-check.

    handler_count: live onClick/onSubmit/onChange handlers in the generated source.
    href_count: live href attributes (non-empty, non-"#" target).
    prd_scope: the derived interactive scope this output was checked against.
    extra_handlers: handlers not attributable to any scope stem — containment leaks.
    inert_without_affordance: interactive-looking controls (``<button>`` /
        ``role="button"``) with NO handler AND no deliberate inert cue
        (``disabled`` / ``cursor-not-allowed`` / a scope cue) — silent dead clicks.
    ok: True when extra_handlers is empty AND inert_without_affordance is empty
        AND there is no live href on non-navigation chrome. ``ok=False`` is a loud
        signal — over-interactivity (a containment leak) and silent-broken-inert
        are both failures.
    """

    handler_count: int
    href_count: int
    prd_scope: list[str]
    extra_handlers: list[str]
    inert_without_affordance: list[str]
    ok: bool


def derive_interactive_scope(
    prd_text: str,
    located_screen: LocatedScreen,
) -> list[str]:
    """Derive the PRD's interactive scope as a list of handler-name STEMS.

    Deterministic (regex over the PRD text) — no LLM call, and no upstream
    object carrying ``interactive_scope``: this is the sole producer. The
    returned stems are the named interactions the PRD asks for; for an entangled
    feature — one that extends an already-interactive surface — the scope ALSO
    includes the minimal existing behaviour the PRD interaction must drive, so
    legitimate entanglement is not later mistaken for a containment leak. When a
    PRD names interactions ambiguously we derive the smallest defensible scope
    and let ``assert_containment`` flag over-interactivity loudly rather than
    silently widening.

    ``located_screen`` is accepted (the located node the scope is checked
    against) so the derivation can be sharpened with the target node later;
    today the scope is derived from the PRD verbs alone.
    """
    text = (prd_text or "").lower()
    entangled = any(cue in text for cue in _ENTANGLED_CUES)
    scope: list[str] = []
    for verb in _INTERACTION_VERBS:
        # Word-stem match anchored on a word boundary: "test" matches
        # "test"/"testing"/"tests" but NOT a mere substring of an unrelated word
        # ("contestant" → no match, since the boundary falls mid-word). Each verb
        # is credited only when it appears as its own word, not by accident.
        if re.search(rf"\b{re.escape(verb)}\w*", text) and verb not in scope:
            scope.append(verb)
            if entangled:
                for driver in _ENTANGLED_DRIVERS.get(verb, ()):
                    if driver not in scope:
                        scope.append(driver)
    return scope


def _brace_body(src: str, open_idx: int) -> str:
    """Return the text inside the JSX expression container that opens at
    ``open_idx`` (the index of the ``{``), balancing nested braces so an arrow
    body with its own ``{...}`` is captured whole."""
    depth = 0
    for i in range(open_idx, len(src)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[open_idx + 1:i]
    return src[open_idx + 1:]


def assert_containment(
    generated_source: str,
    interactive_scope: list[str],
) -> ContainmentReport:
    """Confirm the generated source kept interactivity SCOPED to the PRD.

    Deterministic grep over the generated output (no LLM): count live handlers
    and hrefs, attribute each handler to a scope stem by name proximity, and
    flag (a) handlers outside the scope as containment leaks and (b)
    interactive-looking controls left inert WITHOUT a deliberate affordance as
    silent dead clicks. Returns a :class:`ContainmentReport`; ``ok=False`` is the
    loud signal the post-gen gate acts on (it does not raise — the caller decides
    how to fail, mirroring how ``assert_theme_landed`` is wired by the gate).

    The inert-affordance default checked here is "visibly disabled" — see the
    pending-product-decision note on the recreate discipline; it is a DEFAULT,
    not a settled rule.
    """
    src = generated_source or ""
    scope = [s.strip().lower() for s in interactive_scope if s and s.strip()]

    handler_labels: list[str] = []
    extra_handlers: list[str] = []
    for m in _HANDLER_ATTR_RE.finditer(src):
        attr = m.group(1)
        open_idx = m.end() - 1  # the trailing '{' the pattern ends on
        binding = _brace_body(src, open_idx).strip()
        label = f"{attr}={{{binding[:48]}}}" if binding else attr
        handler_labels.append(label)
        if not any(stem in binding.lower() for stem in scope):
            extra_handlers.append(label)

    href_count = 0
    for m in _HREF_RE.finditer(src):
        target = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if target and target != "#":
            href_count += 1
    nav_in_scope = any(stem in _NAV_STEMS for stem in scope)

    inert_without_affordance: list[str] = []
    seen_spans: set[int] = set()
    for tag_re in (_BUTTON_TAG_RE, _ROLE_BUTTON_TAG_RE):
        for m in tag_re.finditer(src):
            if m.start() in seen_spans:
                continue
            seen_spans.add(m.start())
            tag = m.group(0)
            has_handler = bool(_HANDLER_ATTR_RE.search(tag))
            has_affordance = any(a in tag.lower() for a in _INERT_AFFORDANCES)
            if not has_handler and not has_affordance:
                inert_without_affordance.append(tag[:60])

    ok = (
        not extra_handlers
        and not inert_without_affordance
        and (href_count == 0 or nav_in_scope)
    )
    return ContainmentReport(
        handler_count=len(handler_labels),
        href_count=href_count,
        prd_scope=list(scope),
        extra_handlers=extra_handlers,
        inert_without_affordance=inert_without_affordance,
        ok=ok,
    )


# ── Source-grounded structural parity self-check ──────────────────────────────
# After generation, confirm the output actually re-expresses the real source it
# was handed: the brand, the nav labels, and the composed-component names from
# the extracted shell + located node. Pure string/identifier presence over the
# generated SOURCE — no DOM capture, no live URL, no network, no browser. Sits
# beside assert_containment / assert_theme_landed on the post-gen gate as an
# ADVISORY quality signal (log + flag, never hard-block).
_NAV_BLOCK_RE = re.compile(r"<nav\b[^>]*>(.*?)</nav>", re.IGNORECASE | re.DOTALL)
_NAV_ITEM_TEXT_RE = re.compile(
    r"<(?:a|NavLink|Link|button|span|li)\b[^>]*>\s*([A-Za-z][A-Za-z0-9 &/-]{1,29})\s*<",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParityReport:
    """Result of the post-gen source-grounded structural parity self-check.

    matched: source refs the generated output reproduced (brand / nav / component).
    missing: source refs absent from the output (a recognizability gap).
    extra: nav labels rendered in the output with NO basis in the real nav
        (invented navigation).
    ok: True when nothing is missing AND nothing is invented.
    """

    matched: list[str]
    missing: list[str]
    extra: list[str]
    ok: bool


def _assert_structural_parity(
    generated_source: str,
    sources: "RecreateSources | None",
    shell: ShellModel,
    located: LocatedScreen,
) -> ParityReport:
    """Deterministic source-grounded parity self-check (NO DOM, NO live-URL).

    Asserts the generated output reproduces the real source's brand, nav labels,
    and composed-component names that were in hand (from the extracted
    ``ShellModel`` + the located node). Pure string/identifier presence — the same
    grep style as ``assert_containment``; no network, no browser. ``sources`` is
    optional: the gating ground truth is the shell + located node, so the check
    runs even without the read sources object.
    """
    src = generated_source or ""
    matched: list[str] = []
    missing: list[str] = []

    brand = (shell.brand or "").strip()
    if brand:
        (matched if brand in src else missing).append(f"brand:{brand}")

    real_labels: list[str] = []
    for item in shell.nav_items:
        label = (item.label or "").strip()
        if label and label not in real_labels:
            real_labels.append(label)
            (matched if label in src else missing).append(f"nav:{label}")

    seen_components: set[str] = set()
    for node in (located.node, *located.also):
        for name in node.composed_components:
            if name and name not in seen_components:
                seen_components.add(name)
                present = bool(re.search(rf"\b{re.escape(name)}\b", src))
                (matched if present else missing).append(f"component:{name}")

    # Invented nav: labels rendered inside a <nav> block in the output that have
    # no basis in the real nav labels. Only computed when we HAVE a real nav to
    # compare against (no ground truth → cannot call anything invented).
    extra: list[str] = []
    if real_labels:
        real_lower = {label.lower() for label in real_labels}
        for block in _NAV_BLOCK_RE.findall(src):
            for text in _NAV_ITEM_TEXT_RE.findall(block):
                label = text.strip()
                if label and label.lower() not in real_lower and label not in extra:
                    extra.append(label)

    ok = not missing and not extra
    return ParityReport(matched=matched, missing=missing, extra=extra, ok=ok)

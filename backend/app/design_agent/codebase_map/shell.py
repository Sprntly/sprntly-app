"""Shell extraction — identifies the app shell file in a repo snapshot and
extracts the brand text, navigation items, collapse model, and logo asset.

All analysis is 100% deterministic: plain regex and string scanning over the
already-fetched snapshot.files dict.  No network calls, no LLM, no AST parser.
"""
from __future__ import annotations

import logging
import re

from app.design_agent.codebase_map.repo_reader import RepoSnapshot
from app.design_agent.codebase_map.types import (
    LogoAsset,
    NavItem,
    ScreenNode,
    ShellModel,
)

logger = logging.getLogger("codebase_map.shell")

# ── Shell file candidate stems (case-insensitive) ─────────────────────────────
_SHELL_STEMS = frozenset(
    ("sidebar", "nav", "navigation", "appshell", "shell",
     "layout", "topbar", "header", "mainnav")
)

# ── Regex bank ────────────────────────────────────────────────────────────────

# import X from "./path/to/logo.svg"
_IMPORT_ASSET_RE = re.compile(
    r"""import\s+(\w+)\s+from\s+['"]([^'"]+\.(?:svg|png|jpg|jpeg|gif|webp|ico))['"]\s*;?""",
    re.MULTILINE,
)
# <img src="literal" … alt="…"> or <img alt="…" src="literal">
_IMG_TAG_RE = re.compile(
    r"""<img\b[^>]*\bsrc=['"]([^'"\s>{}][^'"\s>{}]*?)['"][^>]*?>""",
    re.DOTALL | re.IGNORECASE,
)
# <img src={varName} …>
_IMG_VAR_RE = re.compile(
    r"""<img\b[^>]*\bsrc=\{(\w+)\}[^>]*?>""",
    re.DOTALL | re.IGNORECASE,
)
# alt attribute anywhere
_ALT_ATTR_RE = re.compile(r"""\balt=['"]([^'"]+)['"]""", re.IGNORECASE)
# inline SVG opening tag
_SVG_TAG_RE = re.compile(r"<svg\b", re.IGNORECASE)
# SVG aria-label or <title> for alt text
_SVG_ARIA_RE = re.compile(r"""\baria-label=['"]([^'"]+)['"]""", re.IGNORECASE)
_SVG_TITLE_RE = re.compile(r"""<title[^>]*>([^<]+)</title>""", re.IGNORECASE)
# text badge: styled div/span with 1-5 char content
_TEXT_BADGE_RE = re.compile(
    r"""<(?:div|span)\b[^>]*\bclassName=['"][^'"]+['"][^>]*>\s*([A-Za-z][A-Za-z0-9]{0,4})\s*</(?:div|span)>""",
    re.DOTALL,
)

# brand text
_BRAND_SPAN_RE = re.compile(
    r"""<(?:span|h[1-6]|p)\b[^>]*>\s*([A-Za-z][A-Za-z0-9 .!_-]{1,50})\s*</(?:span|h[1-6]|p)>""",
    re.IGNORECASE,
)
_BRAND_CONST_RE = re.compile(
    r"""(?:BRAND|APP_NAME|SITE_NAME|APP_TITLE)\s*[=:]\s*['"]([^'"]{2,60})['"]""",
)

# nav-config array: const/let/var NAV[...] = [
# handles optional TypeScript type annotation between name and '='
_NAV_ARRAY_DECL_RE = re.compile(
    r"""(?:const|let|var)\s+\w*[Nn][Aa][Vv]\w*\s*(?::[^=]+)?\s*=\s*\[""",
    re.DOTALL,
)
_NAV_ENTRY_LABEL_RE = re.compile(r"""label\s*:\s*['"]([^'"]+)['"]""")
_NAV_ENTRY_ICON_RE = re.compile(r"""icon\s*:\s*['"]([^'"]+)['"]""")
_NAV_ENTRY_HREF_RE = re.compile(r"""(?:href|to|path)\s*:\s*['"]([^'"]+)['"]""")

# inline JSX nav links
_LINK_TAG_RE = re.compile(
    r"""<(?:Link|NavLink|a)\b[^>]*(?:href|to)=['"]([^'"]*)['"]\s*[^>]*>(.*?)</(?:Link|NavLink|a)>""",
    re.DOTALL | re.IGNORECASE,
)
# icon: PascalCase self-closing component inside a nav entry
_ICON_COMP_RE = re.compile(r"""<([A-Z][A-Za-z0-9]+)\s*/>""")
# strip HTML/JSX tags (for visible text extraction)
_STRIP_TAGS_RE = re.compile(r"<[^>]+>")

# nav-link count heuristic (≥3 → likely a nav component)
_LINK_HEURISTIC_RE = re.compile(r"""<(?:Link|NavLink|a)\b""", re.IGNORECASE)

# collapse model
_COLLAPSE_KEYWORDS = (
    "isCollapsed", "collapsed", "SidebarTrigger", "useSidebar",
    "toggleSidebar", "isSidebarOpen", "sidebarOpen", "sidebarCollapsed",
)
_FIXED_WIDTH_RE = re.compile(
    r"""(?:\bw-\d+\b|width\s*:\s*['"]?\d+px['"]?|minWidth\s*:)""",
    re.IGNORECASE,
)

_MAX_NAV_ITEMS = 30


# ── Internal helpers ──────────────────────────────────────────────────────────

def _stem_of(path: str) -> str:
    """Return lowercase filename stem (no extension, no directory path)."""
    name = path.split("/")[-1]
    stem, _, _ = name.rpartition(".")
    return (stem or name).lower()


def _locate_shell_file(snapshot: RepoSnapshot) -> tuple[str | None, str | None]:
    """Return (path, body) for the most likely shell file in snapshot.files.

    Prefers name-match candidates; within name-matches picks the file with the
    most nav-link elements.  Falls back to content heuristics for files missing
    from the named candidates.  Returns (None, None) when nothing qualifies.
    """
    # Primary: files whose stem matches a known shell name.
    name_matches: list[tuple[str, str]] = [
        (path, body)
        for path, body in snapshot.files.items()
        if _stem_of(path) in _SHELL_STEMS
    ]
    if name_matches:
        # Pick highest nav-link density in case of ties.
        name_matches.sort(
            key=lambda pb: len(_LINK_HEURISTIC_RE.findall(pb[1])),
            reverse=True,
        )
        return name_matches[0]

    # Fallback: scan component files for high nav-link count + a brand/logo region.
    candidates: list[tuple[int, str, str]] = []
    for path, body in snapshot.files.items():
        if not path.endswith((".tsx", ".jsx", ".ts", ".js")):
            continue
        link_count = len(_LINK_HEURISTIC_RE.findall(body))
        if link_count >= 3 and (
            _IMG_TAG_RE.search(body)
            or _SVG_TAG_RE.search(body)
            or _BRAND_SPAN_RE.search(body)
        ):
            candidates.append((link_count, path, body))

    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1], candidates[0][2]

    return None, None


def _extract_logo(body: str) -> LogoAsset:
    """Detect logo render kind, honouring precedence: imported_asset > img_src > inline_svg > text > absent."""
    # Collect asset imports: name → source path.
    imports: dict[str, str] = {
        m.group(1): m.group(2)
        for m in _IMPORT_ASSET_RE.finditer(body)
    }

    # imported_asset: import + <img src={var}> usage.
    if imports:
        for m in _IMG_VAR_RE.finditer(body):
            var_name = m.group(1)
            if var_name in imports:
                # Extract alt from the full img tag span.
                tag_text = body[m.start():m.end()]
                alt_m = _ALT_ATTR_RE.search(tag_text)
                return LogoAsset(
                    render_kind="imported_asset",
                    asset_ref=imports[var_name],
                    alt_text=alt_m.group(1) if alt_m else "",
                )
        # imported_asset: import + <ImportedName /> component usage.
        for name, src in imports.items():
            if name[0].isupper():
                comp_re = re.compile(rf"<{re.escape(name)}\b[^>]*/?>")
                if comp_re.search(body):
                    return LogoAsset(render_kind="imported_asset", asset_ref=src, alt_text="")

    # img_src: literal <img src="…">.
    img_m = _IMG_TAG_RE.search(body)
    if img_m:
        src = img_m.group(1)
        tag_text = body[img_m.start():img_m.end()]
        alt_m = _ALT_ATTR_RE.search(tag_text)
        return LogoAsset(
            render_kind="img_src",
            asset_ref=src,
            alt_text=alt_m.group(1) if alt_m else "",
        )

    # inline_svg: a literal <svg …> block.
    if _SVG_TAG_RE.search(body):
        alt = ""
        al_m = _SVG_ARIA_RE.search(body)
        if al_m:
            alt = al_m.group(1)
        else:
            t_m = _SVG_TITLE_RE.search(body)
            if t_m:
                alt = t_m.group(1)
        return LogoAsset(render_kind="inline_svg", asset_ref="", alt_text=alt)

    # text badge: short text in a styled container, no image.
    tb_m = _TEXT_BADGE_RE.search(body)
    if tb_m:
        return LogoAsset(render_kind="text", asset_ref=tb_m.group(1), alt_text="")

    return LogoAsset()  # absent


def _parse_nav_config_array(body: str) -> list[NavItem]:
    """Parse a nav-config array declaration and return NavItem list.

    Walks character-by-character over the array content to extract each entry
    object deterministically.  Returns an empty list when no valid config-array
    is found.
    """
    decl_m = _NAV_ARRAY_DECL_RE.search(body)
    if not decl_m:
        return []

    # Position of the opening '['.
    start = decl_m.end() - 1
    items: list[NavItem] = []
    depth = 0
    i = start

    while i < len(body) and len(items) < _MAX_NAV_ITEMS:
        ch = body[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                break
        elif ch == "{" and depth == 1:
            # Find the matching closing brace for this entry.
            brace_depth = 1
            j = i + 1
            while j < len(body) and brace_depth > 0:
                if body[j] == "{":
                    brace_depth += 1
                elif body[j] == "}":
                    brace_depth -= 1
                j += 1
            entry_text = body[i:j]
            label_m = _NAV_ENTRY_LABEL_RE.search(entry_text)
            if label_m:
                icon_m = _NAV_ENTRY_ICON_RE.search(entry_text)
                href_m = _NAV_ENTRY_HREF_RE.search(entry_text)
                items.append(NavItem(
                    label=label_m.group(1),
                    order=len(items),
                    icon=icon_m.group(1) if icon_m else "",
                    route=href_m.group(1) if href_m else "",
                ))
            i = j
            continue
        i += 1

    return items


def _extract_nav_items(body: str) -> list[NavItem]:
    """Extract nav items from shell body.

    Prefers a nav-config array declaration (more structured, fewer false
    positives) over inline JSX link scanning when both are present.
    """
    # Strategy A: nav-config array wins when present.
    config_items = _parse_nav_config_array(body)
    if config_items:
        return config_items

    # Strategy B: inline JSX <Link> / <NavLink> / <a> elements.
    items: list[NavItem] = []
    for m in _LINK_TAG_RE.finditer(body):
        if len(items) >= _MAX_NAV_ITEMS:
            break
        href = m.group(1) or ""
        inner = m.group(2)
        icon = ""
        icon_m = _ICON_COMP_RE.search(inner)
        if icon_m:
            icon = icon_m.group(1)
        label = _STRIP_TAGS_RE.sub("", inner).strip()
        if not label:
            continue
        items.append(NavItem(label=label, order=len(items), icon=icon, route=href))

    return items


def _extract_brand(body: str, logo: LogoAsset) -> str:
    """Extract brand text from shell body.

    Prefers rendered wordmark spans/headings; falls back to logo alt text or a
    BRAND/APP_NAME constant.  Returns '' when nothing is found.
    """
    for m in _BRAND_SPAN_RE.finditer(body):
        candidate = m.group(1).strip()
        if 2 <= len(candidate) <= 50:
            return candidate

    if logo.alt_text:
        return logo.alt_text

    bc_m = _BRAND_CONST_RE.search(body)
    if bc_m:
        return bc_m.group(1).strip()

    return ""


def _extract_collapse(body: str) -> str:
    """Return collapse model label: 'collapsible', 'static', or ''."""
    for keyword in _COLLAPSE_KEYWORDS:
        if keyword in body:
            return "collapsible"
    if _FIXED_WIDTH_RE.search(body):
        return "static"
    return ""


# ── Public entry point ────────────────────────────────────────────────────────

def extract_shell(snapshot: RepoSnapshot) -> ShellModel:
    """Extract a ShellModel from a repo snapshot via static string/regex analysis.

    Returns a bare ShellModel() when no shell file is present in the snapshot —
    honest absence, nothing fabricated.
    """
    path, body = _locate_shell_file(snapshot)
    if body is None:
        logger.info(
            "codebase_map.shell repo=%s brand=%s n_nav=%d logo_kind=%s collapse=%s",
            snapshot.repo, "", 0, "absent", "",
        )
        return ShellModel()

    logo = _extract_logo(body)
    nav_items = _extract_nav_items(body)
    brand = _extract_brand(body, logo)
    collapse = _extract_collapse(body)

    logger.info(
        "codebase_map.shell repo=%s brand=%s n_nav=%d logo_kind=%s collapse=%s",
        snapshot.repo, brand, len(nav_items), logo.render_kind, collapse,
    )

    return ShellModel(
        brand=brand,
        nav_items=nav_items,
        collapse_model=collapse,
        logo=logo,
    )


# ── App-shell node (the locatable chrome surface) ──────────────────────────────

APP_SHELL_NODE_ID = "app-shell"
# Stable id of the single chrome node emitted per map.
APP_SHELL_ROUTE = "(app layout — global chrome, not a route)"
# Synthetic route label — never a real path, so it never matches a navigation
# call-site and stays inert in edge resolution (edge keying is route/file-based).


def _component_name_from_path(path: str) -> str:
    """Best-effort component name from a file path stem.

    Returns the filename stem when it already reads as a component name
    (leading uppercase — the React component-file convention); otherwise ""
    (honest unknown). Pure string work — no repo read.
    """
    if not path:
        return ""
    name = path.split("/")[-1]
    stem, _, _ = name.rpartition(".")
    stem = stem or name
    return stem if stem[:1].isupper() else ""


def build_app_shell_node(shell: ShellModel, *, shell_file_path: str = "") -> ScreenNode:
    """Construct the kind="shell" app-shell node from an already-extracted ShellModel.

    The app shell is the frame every screen renders inside (sidebar + topbar +
    persistent global layer: AI bar, toast/notification layer, global modals).
    It has no single-screen route, so it is promoted to one enumerated, locatable
    node with a stable id ("app-shell") and a synthetic route.

    The structural backing is the ShellModel the caller already extracted — this
    builder performs NO repo read / fetch (it takes no snapshot): its fields
    derive only from the ShellModel and the file path it is given. ``composed_components``
    reuses the nav items' icon component names when present.

    ``shell_file_path`` is the located shell file (threaded from the node-assembly
    site, which reuses the path extract_shell computed). When unknown it may be
    "" — the node stays locatable by its stable id.
    """
    composed: list[str] = []
    for item in shell.nav_items:
        if item.icon and item.icon not in composed:
            composed.append(item.icon)
    return ScreenNode(
        id=APP_SHELL_NODE_ID,
        kind="shell",
        route=APP_SHELL_ROUTE,
        entry_component=_component_name_from_path(shell_file_path),
        file=shell_file_path,
        composed_components=composed,
    )

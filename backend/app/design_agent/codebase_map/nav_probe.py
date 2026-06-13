"""Nav-abstraction probe for the codebase map pipeline.

Detection technique: regex and string scanning over the already-fetched
RepoSnapshot.files map and tree_paths list.  No JavaScript or TypeScript AST
parser is used or imported.  All signals — typed screen registries, route-table
objects, filesystem route conventions, nav call-site counts — are detectable
with anchored regexes over source text, matching the convention established by
the design-system extractor.  If a future repo requires true AST resolution
that cannot be satisfied by text regexes, that is a separate dependency-adoption
decision — not silent scope creep here.

Posture rules:
  CLEAN  — a typed screen registry or route-table was found via anchored regex
            (declaration keyword + word boundary).  A comment that merely
            mentions a registry name does NOT flip the posture to CLEAN.
  PARTIAL — no typed registry found; filesystem route convention used as fallback.

When in doubt between CLEAN and PARTIAL, the probe returns PARTIAL.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.design_agent.codebase_map.repo_reader import RepoSnapshot
from app.design_agent.codebase_map.types import Posture

logger = logging.getLogger(__name__)

# ── Registry / route-table detection ──────────────────────────────────────────
# Anchored to a declaration keyword so a comment mention does not match.
# Accepts ScreenId, RouteId, PageId, ScreensId and their plural variants.
_REGISTRY_DECL_RE = re.compile(
    r"(?:^|\s)(?:enum|const|type)\s+(?:Screen(?:s)?Id|RouteId|PageId|Route(?:s)?Id)\b",
    re.MULTILINE,
)

# Route-table: `const ROUTES` / `export const ROUTES` / Record<ScreenId…> annotation
_ROUTE_TABLE_CONST_RE = re.compile(
    r"(?:^|\s)(?:export\s+)?const\s+[A-Z_]*ROUTES?\b",
    re.MULTILINE,
)
_ROUTE_TABLE_RECORD_RE = re.compile(
    r"Record\s*<\s*\w*(?:Screen|Route|Page)\w*Id",
    re.MULTILINE,
)

# Typed nav primitive: declaration of a goTo / navigateTo function
_NAV_DECL_RE = re.compile(
    r"(?:^|\s)(?:function|const|export\s+(?:default\s+)?(?:function|const))\s+"
    r"(?:goTo|navigateTo)\b",
    re.MULTILINE,
)

# ── Nav call-site counters (usage frequency determines nav_primitive) ──────────
# Ordered from most-specific to least-specific; first match wins if tied.
_NAV_CALL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("goTo", re.compile(r"\bgoTo\s*\(")),
    ("navigateTo", re.compile(r"\bnavigateTo\s*\(")),
    ("navigate", re.compile(r"\bnavigate\s*\(")),
    ("router.push", re.compile(r"\brouter\.push\s*\(")),
    ("Link", re.compile(r"<(?:Link|NavLink)\b")),
]

# ── Filesystem route conventions ──────────────────────────────────────────────
_NEXT_APP_RE = re.compile(r"(?:^|/)app/.+/page\.[jt]sx?$")
_NEXT_PAGES_RE = re.compile(r"(?:^|/)pages/.+\.[jt]sx?$")
_REACT_ROUTER_RE = re.compile(r"createBrowserRouter|<Route\s+path=")


@dataclass
class ProbeResult:
    posture: Posture = "PARTIAL"
    registry_file: str = ""
    nav_primitive: str = ""
    route_table_files: list[str] = field(default_factory=list)
    router_convention: str = ""


def _strip_comments_and_strings(body: str) -> str:
    """Remove line comments, block comments, and string literals.

    Posture regexes run on the result so that declaration keywords appearing
    only inside a comment or string literal do not false-positive CLEAN.
    Block comments stripped first so their content cannot seed line-comment
    removal; template literals before single/double quotes.
    """
    # Block comments /* ... */ (may span lines)
    body = re.sub(r"/\*.*?\*/", " ", body, flags=re.DOTALL)
    # Line comments // ...
    body = re.sub(r"//[^\n]*", " ", body)
    # Template literals `...` (may span lines, handle escape sequences)
    body = re.sub(r"`(?:[^`\\]|\\.)*`", " ", body, flags=re.DOTALL)
    # Double-quoted strings (handle escape sequences, no newlines)
    body = re.sub(r'"(?:[^"\\]|\\.)*"', " ", body)
    # Single-quoted strings (handle escape sequences, no newlines)
    body = re.sub(r"'(?:[^'\\]|\\.)*'", " ", body)
    return body


def probe_nav_abstraction(snapshot: RepoSnapshot) -> ProbeResult:
    """Scan snapshot for a typed nav abstraction; return a ProbeResult.

    Reads snapshot.files and snapshot.tree_paths.  Never re-fetches.
    Running this function twice on the same snapshot yields the same result.
    """
    result = ProbeResult()

    registry_file = ""
    route_table_files: list[str] = []
    nav_decl_file = ""

    # -- Pass 1: scan file bodies for registry / route-table / nav-decl signals --
    # Posture regexes run on comment-and-string-stripped text only, so a
    # declaration keyword that appears solely inside a comment or string literal
    # does not flip the posture to CLEAN.
    for path, body in snapshot.files.items():
        stripped = _strip_comments_and_strings(body)

        if _REGISTRY_DECL_RE.search(stripped):
            if not registry_file:
                registry_file = path

        if _ROUTE_TABLE_CONST_RE.search(stripped) or _ROUTE_TABLE_RECORD_RE.search(stripped):
            if path not in route_table_files:
                route_table_files.append(path)

        if _NAV_DECL_RE.search(stripped) and not nav_decl_file:
            nav_decl_file = path

    # -- Determine posture --
    if registry_file or route_table_files:
        result.posture = "CLEAN"
        result.registry_file = registry_file
        result.route_table_files = sorted(route_table_files)
    else:
        result.posture = "PARTIAL"
        result.registry_file = ""
        result.route_table_files = []

    # -- Pass 2: count nav call-site occurrences across all files ---------------
    counts: dict[str, int] = {name: 0 for name, _ in _NAV_CALL_PATTERNS}
    for body in snapshot.files.values():
        for name, pat in _NAV_CALL_PATTERNS:
            counts[name] += len(pat.findall(body))

    best_name = ""
    best_count = 0
    for name, _ in _NAV_CALL_PATTERNS:
        if counts[name] > best_count:
            best_count = counts[name]
            best_name = name
    result.nav_primitive = best_name

    # -- Pass 3: filesystem router convention (used for PARTIAL; logged always) --
    convention = _detect_router_convention(snapshot)
    result.router_convention = convention

    logger.info(
        "codebase_map.nav_probe repo=%s posture=%s primitive=%s convention=%s registry=%s",
        snapshot.repo,
        result.posture,
        result.nav_primitive,
        result.router_convention,
        result.registry_file,
    )

    return result


def _detect_router_convention(snapshot: RepoSnapshot) -> str:
    """Return the filesystem router convention label from tree_paths + file bodies."""
    for path in snapshot.tree_paths:
        if _NEXT_APP_RE.search(path):
            return "next-app"
    for path in snapshot.tree_paths:
        if _NEXT_PAGES_RE.search(path):
            return "next-pages"
    for body in snapshot.files.values():
        if _REACT_ROUTER_RE.search(body):
            return "react-router"
    if snapshot.tree_paths:
        return "filesystem"
    return ""


# ── Shared enumeration heuristics ──────────────────────────────────────────────
# These two utilities are defined ONCE here and called by every enumerator
# adapter (the route-table adapter and the react-router adapter both reuse
# them).  They are JSX-shape heuristics, not stack-specific, so duplicating them
# per adapter would let the two copies drift.  Same regex/string technique as
# the probe: no JS/TS AST parser is used or imported.


@dataclass(frozen=True)
class RouteElement:
    """One ``<Route path=... element={<Comp/>}>`` entry discovered in source."""

    route: str       # the path attribute ("/team", "/users/:id")
    component: str    # the PascalCase element component name ("TeamPage")
    file: str         # repo-relative file the <Route> was declared in


@dataclass(frozen=True)
class TabSection:
    """One in-page tab declared in a ``const tabs = [...]`` style array."""

    section_id: str   # stable slug for the tab ("members", "billing")
    label: str        # human label when present ("Members")
    file: str         # repo-relative file the tab array was declared in


# A <Route> JSX tag, captured up to its first '>' so multi-line / Prettier-wrapped
# attribute lists are tolerated.  path= and element= are then read from the tag
# body in either order.
_ROUTE_TAG_RE = re.compile(r"<Route\b([^>]*?)/?>", re.DOTALL)
_ROUTE_PATH_ATTR_RE = re.compile(r"\bpath\s*=\s*['\"]([^'\"]+)['\"]")
_ROUTE_ELEMENT_ATTR_RE = re.compile(r"\belement\s*=\s*\{\s*<\s*([A-Z]\w*)")

# A tabs-style array declaration.  The name must contain "tab" so an unrelated
# array literal does not false-positive as an in-page section set.
_TAB_ARRAY_RE = re.compile(
    r"\b(?:const|let|var)\s+(\w*[Tt]ab\w*)\s*(?::[^=\[]+)?=\s*\[(.*?)\]",
    re.DOTALL,
)
_TAB_LABEL_RE = re.compile(r"\b(?:label|name|title)\s*:\s*['\"]([^'\"]+)['\"]")
_TAB_ID_RE = re.compile(r"\b(?:id|key|value|slug)\s*:\s*['\"]([^'\"]+)['\"]")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    """Lower-case, hyphenate, and trim a label into a stable id fragment."""
    return _SLUG_STRIP_RE.sub("-", text.strip().lower()).strip("-")


def discover_route_elements(snapshot: RepoSnapshot) -> list[RouteElement]:
    """Discover ``<Route path=... element={<Comp/>}>`` declarations.

    Whole-body scan tolerant of multi-line tags (the react-router edge-hardening
    convention shared by the edge resolver).  Returns one RouteElement per
    path-bearing route, deduplicated by (route, file), sorted by route for
    determinism.  Layout routes with no ``path`` attribute are skipped.
    """
    found: dict[tuple[str, str], RouteElement] = {}
    for path, body in snapshot.files.items():
        for tag in _ROUTE_TAG_RE.finditer(body):
            tag_body = tag.group(1)
            path_m = _ROUTE_PATH_ATTR_RE.search(tag_body)
            if not path_m:
                continue
            route = path_m.group(1)
            comp_m = _ROUTE_ELEMENT_ATTR_RE.search(tag_body)
            component = comp_m.group(1) if comp_m else ""
            key = (route, path)
            if key not in found:
                found[key] = RouteElement(route=route, component=component, file=path)
    return sorted(found.values(), key=lambda r: (r.route, r.file))


def detect_tab_sections(snapshot: RepoSnapshot) -> list[TabSection]:
    """Discover in-page tab arrays (``const tabs = [{ id, label }, …]``).

    Each object entry inside a tabs-named array becomes one TabSection.  The id
    falls back to a slug of the label when no explicit id/key is present, and to
    the label itself when neither yields a slug.  Deduplicated by
    (section_id, file), sorted for determinism.  Returns ``[]`` when no tabs
    array is present — so a route-only screen set adds no section nodes.
    """
    found: dict[tuple[str, str], TabSection] = {}
    for path, body in snapshot.files.items():
        for arr in _TAB_ARRAY_RE.finditer(body):
            array_body = arr.group(2)
            # Split into rough object-entry spans so a label pairs with the id
            # declared in the same entry rather than the next one.
            for entry in array_body.split("}"):
                label_m = _TAB_LABEL_RE.search(entry)
                id_m = _TAB_ID_RE.search(entry)
                if not label_m and not id_m:
                    continue
                label = label_m.group(1) if label_m else ""
                section_id = id_m.group(1) if id_m else _slugify(label)
                if not section_id:
                    continue
                key = (section_id, path)
                if key not in found:
                    found[key] = TabSection(section_id=section_id, label=label, file=path)
    return sorted(found.values(), key=lambda s: (s.file, s.section_id))

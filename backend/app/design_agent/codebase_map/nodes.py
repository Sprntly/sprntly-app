"""Screen-node extractor for the codebase map pipeline.

Detection technique: regex and string scanning over the already-fetched
RepoSnapshot.files map and tree_paths list.  No JavaScript or TypeScript AST
parser is used or imported.  All signals — default export names, composed
component imports, route-table path mappings, query-param route-state entries
— are detectable with anchored regexes over source text, matching the
convention established by the design-system extractor.

Completeness note: on a PARTIAL repo the returned node set is NOT certifiably
complete.  A screen reachable only via a runtime query-param path that the
filesystem does not name will be silently absent.  The posture label is the
caller's signal to budget human curation; this extractor does not assert
completeness on PARTIAL results.
"""
from __future__ import annotations

import logging
import re

from app.design_agent.codebase_map.nav_probe import (
    ProbeResult,
    detect_tab_sections,
    discover_route_elements,
)
from app.design_agent.codebase_map.repo_reader import RepoSnapshot
from app.design_agent.codebase_map.types import ScreenNode, ShellModel

logger = logging.getLogger(__name__)

# ── default-export name detection ─────────────────────────────────────────────
_DEFAULT_EXPORT_NAMED_RE = re.compile(
    r"export\s+default\s+(?:function|class)\s+(\w+)"
)
_DEFAULT_EXPORT_VAR_RE = re.compile(
    r"export\s+default\s+(\w+)\s*[;\n]"
)

# ── composed-component detection ──────────────────────────────────────────────
# PascalCase JSX tag usage
_JSX_TAG_RE = re.compile(r"<([A-Z]\w+)[\s/>]")
# named imports: import { Foo, Bar } from …
_NAMED_IMPORT_RE = re.compile(r"import\s*\{([^}]+)\}")
# default imports: import Foo from …
_DEFAULT_IMPORT_RE = re.compile(r"import\s+(\w+)\s+from\b")

_MAX_COMPOSED = 20

# ── CLEAN path: route-table entry detection ────────────────────────────────────
# Matches: team: "/team"  or  team: "/team?modal=invite"
_ROUTE_ENTRY_RE = re.compile(
    r"""(?:^|\s)(\w+)\s*:\s*["']([^"']+)["']""",
    re.MULTILINE,
)

# ── PARTIAL path: filesystem route derivation ─────────────────────────────────
# next-app: app/.../page.{tsx,ts,jsx,js}
_NEXT_APP_PAGE_RE = re.compile(r"(?:^|/)app/(.*?)/page\.[jt]sx?$")
# next-pages: pages/...{tsx,ts,jsx,js} (not _app, not _document, not api/)
_NEXT_PAGES_PAGE_RE = re.compile(r"(?:^|/)pages/(.+)\.[jt]sx?$")
# dynamic segment: [id] → :id
_DYNAMIC_SEGMENT_RE = re.compile(r"\[([^\]]+)\]")


def extract_nodes(snapshot: RepoSnapshot, probe: ProbeResult) -> list[ScreenNode]:
    """Enumerate ScreenNodes from snapshot via the detected stack's adapter.

    Detection selects one enumerator adapter (the route-table/filesystem adapter
    for Next-shaped repos, the react-router adapter for Vite-shaped repos, a
    low-confidence discovery fallback for an unrecognised JS/TS repo, and a
    no-emit decline for an unreadable non-JS/TS repo).  The adapter owns the
    raw enumeration; this function applies the shared route-sort + identifier
    log so every adapter's output is normalised identically.

    Returns nodes sorted by route for determinism.  Running this function twice
    on the same (snapshot, probe) yields equal results.
    """
    # Local import keeps the detection/registry seam free of an import cycle:
    # the stack module imports the adapters defined below.
    from app.design_agent.codebase_map.stack import detect_stack, select_adapter

    profile = detect_stack(snapshot)
    adapter = select_adapter(profile)
    nodes = adapter.enumerate_nodes(snapshot, probe)

    nodes.sort(key=lambda n: n.route)

    # Promote the app shell to a locatable node: append exactly one kind="shell"
    # node alongside the route/section nodes (after the sort, so the route/section
    # ordering is untouched), reusing the already-extracted ShellModel as its
    # structural backing. Skip when no shell was identified (a bare ShellModel) —
    # a repo with no identifiable chrome gets no synthetic catch-all surface. The
    # local import mirrors the stack import above: it keeps the module-load graph
    # free of an import cycle (stack.py imports this module).
    from app.design_agent.codebase_map.shell import (
        _locate_shell_file,
        build_app_shell_node,
        extract_shell,
    )

    shell = extract_shell(snapshot)
    if shell != ShellModel() and not any(n.kind == "shell" for n in nodes):
        shell_path, _ = _locate_shell_file(snapshot)
        nodes.append(build_app_shell_node(shell, shell_file_path=shell_path or ""))

    n_route_state = sum(1 for n in nodes if n.is_route_state)
    logger.info(
        "codebase_map.nodes repo=%s stack=%s posture=%s n_nodes=%d n_route_state=%d",
        snapshot.repo,
        profile.stack,
        probe.posture,
        len(nodes),
        n_route_state,
    )

    return nodes


# ── CLEAN extraction ───────────────────────────────────────────────────────────

def _extract_clean(snapshot: RepoSnapshot, probe: ProbeResult) -> list[ScreenNode]:
    """Extract nodes from the registry and route-table files."""
    nodes: list[ScreenNode] = []
    seen_routes: set[str] = set()

    route_map = _build_route_map(snapshot, probe)
    if not route_map:
        # No route-table found; fall back to partial extraction
        return _extract_partial(snapshot, probe)

    for route_path in sorted(route_map):
        if route_path in seen_routes:
            continue
        seen_routes.add(route_path)

        is_route_state = "?" in route_path
        file_path, component = _resolve_component_for_route(route_path, snapshot, probe)

        composed = _extract_composed_components(file_path, snapshot) if file_path else []

        nodes.append(ScreenNode(
            route=route_path,
            entry_component=component,
            file=file_path,
            composed_components=composed,
            is_route_state=is_route_state,
            kind="route",
            id=route_path,
        ))

    return nodes


def _build_route_map(snapshot: RepoSnapshot, probe: ProbeResult) -> dict[str, str]:
    """Scan route-table files and return {route_path: key} mapping.

    Includes query-param route-state entries when the route-table names them
    (e.g. "/team?modal=invite").  Only call on CLEAN posture.
    """
    route_paths: dict[str, str] = {}

    candidate_files = list(probe.route_table_files)
    if probe.registry_file and probe.registry_file not in candidate_files:
        candidate_files.append(probe.registry_file)

    for path in candidate_files:
        body = snapshot.files.get(path, "")
        for m in _ROUTE_ENTRY_RE.finditer(body):
            key = m.group(1)
            route = m.group(2)
            # Must look like a path
            if route.startswith("/"):
                route_paths[route] = key

    return route_paths


def _resolve_component_for_route(
    route: str,
    snapshot: RepoSnapshot,
    probe: ProbeResult,
) -> tuple[str, str]:
    """Return (file_path, component_name) for a route on the CLEAN path.

    For next-app convention: map route → app/<segments>/page.tsx.
    For registry-mapped: look for the component reference in the route-table.
    Falls back to ("", "") on failure — an empty file is still useful to emit.
    """
    convention = probe.router_convention

    if convention == "next-app":
        # Strip query-param for filesystem lookup
        base_route = route.split("?")[0].lstrip("/")
        candidate = f"app/{base_route}/page.tsx" if base_route else "app/page.tsx"
        if candidate in snapshot.files:
            body = snapshot.files[candidate]
            return candidate, _parse_default_export(body)
        # Try .jsx fallback
        candidate_jsx = candidate.replace(".tsx", ".jsx")
        if candidate_jsx in snapshot.files:
            body = snapshot.files[candidate_jsx]
            return candidate_jsx, _parse_default_export(body)

    # Generic: scan all files for an import whose path suggests this route
    base_route = route.split("?")[0].lstrip("/")
    for path, body in snapshot.files.items():
        if base_route and base_route.replace("/", "") in path.lower():
            name = _parse_default_export(body)
            if name:
                return path, name

    return "", ""


# ── PARTIAL extraction ─────────────────────────────────────────────────────────

def _extract_partial(snapshot: RepoSnapshot, probe: ProbeResult) -> list[ScreenNode]:
    """Extract nodes via filesystem route convention only.  No route-state nodes."""
    convention = probe.router_convention
    nodes: list[ScreenNode] = []

    if convention == "next-app":
        nodes = _extract_next_app(snapshot)
    elif convention == "next-pages":
        nodes = _extract_next_pages(snapshot)
    elif convention in ("react-router", "filesystem"):
        # Best-effort: use whatever page files we can find
        nodes = _extract_next_app(snapshot) or _extract_next_pages(snapshot)

    # Never emit route-state nodes on PARTIAL — completeness is not certifiable
    return nodes


def _extract_next_app(snapshot: RepoSnapshot) -> list[ScreenNode]:
    nodes: list[ScreenNode] = []
    for path in snapshot.tree_paths:
        m = _NEXT_APP_PAGE_RE.search(path)
        if not m:
            continue
        segments = m.group(1)  # everything between app/ and /page.tsx
        route = "/" + _DYNAMIC_SEGMENT_RE.sub(lambda mm: ":" + mm.group(1), segments)
        route = route.rstrip("/") or "/"

        body = snapshot.files.get(path, "")
        component = _parse_default_export(body) if body else ""
        composed = _extract_composed_components(path, snapshot) if body else []

        nodes.append(ScreenNode(
            route=route,
            entry_component=component,
            file=path,
            composed_components=composed,
            is_route_state=False,
            kind="route",
            id=route,
        ))
    return nodes


def _extract_next_pages(snapshot: RepoSnapshot) -> list[ScreenNode]:
    nodes: list[ScreenNode] = []
    for path in snapshot.tree_paths:
        m = _NEXT_PAGES_PAGE_RE.search(path)
        if not m:
            continue
        rel = m.group(1)
        # Skip special Next.js pages
        if rel.startswith("_") or rel.startswith("api/"):
            continue
        # index → /
        if rel == "index":
            route = "/"
        else:
            route = "/" + _DYNAMIC_SEGMENT_RE.sub(lambda mm: ":" + mm.group(1), rel)

        body = snapshot.files.get(path, "")
        component = _parse_default_export(body) if body else ""
        composed = _extract_composed_components(path, snapshot) if body else []

        nodes.append(ScreenNode(
            route=route,
            entry_component=component,
            file=path,
            composed_components=composed,
            is_route_state=False,
            kind="route",
            id=route,
        ))
    return nodes


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_default_export(body: str) -> str:
    """Return the name of the default export from a file body, or ''."""
    m = _DEFAULT_EXPORT_NAMED_RE.search(body)
    if m:
        return m.group(1)
    m = _DEFAULT_EXPORT_VAR_RE.search(body)
    if m:
        candidate = m.group(1)
        # Avoid matching keywords
        if candidate not in ("function", "class", "const", "let", "var", "async"):
            return candidate
    return ""


def _extract_composed_components(file_path: str, snapshot: RepoSnapshot) -> list[str]:
    """Return PascalCase JSX tags that are also imported in the same file.

    Deduped, capped at _MAX_COMPOSED.
    """
    body = snapshot.files.get(file_path, "")
    if not body:
        return []

    # Collect all PascalCase JSX tags used in the file
    jsx_tags: set[str] = set(_JSX_TAG_RE.findall(body))

    # Collect all imported names
    imported: set[str] = set()
    for m in _NAMED_IMPORT_RE.finditer(body):
        for name in m.group(1).split(","):
            imported.add(name.strip().split(" as ")[0].strip())
    for m in _DEFAULT_IMPORT_RE.finditer(body):
        imported.add(m.group(1).strip())

    # Intersection: only tags that are also imported
    result = sorted(jsx_tags & imported)
    return result[:_MAX_COMPOSED]


# ── import → repo-path resolution (deep-read seam) ─────────────────────────────
# The same arithmetic for every adapter; the alias map differs per repo and is
# supplied by the caller (from the detected stack's tsconfig/jsconfig paths).
# No filesystem reads beyond the in-memory snapshot — pure string resolution.

_RESOLVE_EXTENSIONS = (".tsx", ".ts", ".jsx", ".js")


def _posix_normpath(path: str) -> str:
    """Collapse ``a/b/../c`` → ``a/c`` without touching the real filesystem."""
    import posixpath

    return posixpath.normpath(path).lstrip("./")


def _exists(candidate: str, snapshot: RepoSnapshot | None) -> bool:
    if snapshot is None:
        return False
    return candidate in snapshot.files or candidate in snapshot.tree_paths


def resolve_specifier(
    specifier: str,
    from_file: str,
    alias_roots: dict[str, str],
    snapshot: RepoSnapshot | None = None,
) -> str | None:
    """Resolve a JS/TS import specifier to a repo-relative file path, or None.

    - ``@/x`` style alias imports resolve against the matching ``alias_roots``
      entry (the tsconfig/jsconfig ``paths`` map, e.g. ``@/* -> src/*``).
    - ``./x`` / ``../x`` relative imports resolve against the importing file's
      directory.
    - bare package specifiers (``react``, ``lucide-react``) are NOT app files
      and resolve to None.

    When a snapshot is supplied, the extension list (and ``/index``) is tried
    and the first existing path is returned; otherwise the bare ``.tsx``
    candidate is returned as a best-effort guess.
    """
    if not specifier:
        return None

    base: str | None = None

    if specifier.startswith("."):
        from_dir = from_file.rsplit("/", 1)[0] if "/" in from_file else ""
        base = _posix_normpath(f"{from_dir}/{specifier}" if from_dir else specifier)
    else:
        for alias, target in alias_roots.items():
            prefix = alias.rstrip("/*").rstrip("/")
            if not prefix:
                continue
            if specifier == prefix or specifier.startswith(prefix + "/"):
                target_prefix = target.rstrip("/*").rstrip("/")
                remainder = specifier[len(prefix):].lstrip("/")
                joined = f"{target_prefix}/{remainder}" if remainder else target_prefix
                base = _posix_normpath(joined)
                break

    if base is None:
        # Neither relative nor aliased → a bare package import; not a repo file.
        return None

    if snapshot is not None:
        for ext in _RESOLVE_EXTENSIONS:
            candidate = base + ext
            if _exists(candidate, snapshot):
                return candidate
        for ext in _RESOLVE_EXTENSIONS:
            candidate = f"{base}/index{ext}"
            if _exists(candidate, snapshot):
                return candidate
        return None

    return base + _RESOLVE_EXTENSIONS[0]


# ── shared section-node construction ───────────────────────────────────────────

def _section_nodes(snapshot: RepoSnapshot) -> list[ScreenNode]:
    """Map every discovered in-page tab into a ``kind="section"`` ScreenNode.

    Returns ``[]`` for a route-only screen set (no tabs array present), so a
    Next-shaped app with no in-page tabs gains no section nodes — its route
    enumeration stays exactly as before.
    """
    nodes: list[ScreenNode] = []
    for section in detect_tab_sections(snapshot):
        stem = section.file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        nodes.append(ScreenNode(
            route="",
            entry_component="",
            file=section.file,
            composed_components=[],
            is_route_state=False,
            kind="section",
            id=f"{stem}#{section.section_id}",
        ))
    return nodes


# ── Vite / react-router route enumeration ──────────────────────────────────────

def _extract_vite_routes(snapshot: RepoSnapshot) -> list[ScreenNode]:
    """Enumerate route nodes from a ``<Route path= element={<X/>}>`` table.

    Falls back to the filesystem page conventions when no react-router route
    table is present, so a Vite repo that still uses file-based pages is not
    left empty.
    """
    elements = discover_route_elements(snapshot)
    if not elements:
        return _extract_next_app(snapshot) or _extract_next_pages(snapshot)

    nodes: list[ScreenNode] = []
    seen: set[str] = set()
    for el in elements:
        if el.route in seen:
            continue
        seen.add(el.route)
        # Best-effort: locate the element component's source file by import name.
        file_path = _file_for_component(el.component, snapshot)
        composed = _extract_composed_components(file_path, snapshot) if file_path else []
        nodes.append(ScreenNode(
            route=el.route,
            entry_component=el.component,
            file=file_path,
            composed_components=composed,
            is_route_state=False,
            kind="route",
            id=el.route,
        ))
    return nodes


def _file_for_component(component: str, snapshot: RepoSnapshot) -> str:
    """Best-effort: find the file whose default export is ``component``."""
    if not component:
        return ""
    for path, body in snapshot.files.items():
        if _parse_default_export(body) == component:
            return path
    return ""


# ── Enumerator adapters (the pluggable seam) ───────────────────────────────────
# Each adapter satisfies the EnumeratorAdapter protocol declared in stack.py
# (structural — no inheritance needed).  enumerate_nodes returns UNSORTED nodes;
# extract_nodes applies the shared route-sort.  Both adapters call the SAME
# shared section heuristic (detect_tab_sections, via _section_nodes) and the
# SAME shared import resolver (resolve_specifier) — single definitions, no
# per-adapter copies.


class NextAppRouterAdapter:
    """First-class adapter for Next.js (App Router + Pages Router).

    enumerate_nodes is the pre-existing route-table/filesystem enumeration
    verbatim (CLEAN → route table, PARTIAL → filesystem convention), plus the
    shared in-page section nodes.  For a route-only Next app (no tabs array)
    the section pass yields nothing, so the route node set is byte-identical to
    the pre-adapter enumeration.
    """

    stack = "next-app"
    # shared utilities held as references so the no-duplication invariant is
    # checkable: the Vite adapter holds the SAME objects.
    section_nodes = staticmethod(_section_nodes)
    resolver = staticmethod(resolve_specifier)

    def enumerate_nodes(
        self, snapshot: RepoSnapshot, probe: ProbeResult,
    ) -> list[ScreenNode]:
        if probe.posture == "CLEAN":
            nodes = _extract_clean(snapshot, probe)
        else:
            nodes = _extract_partial(snapshot, probe)
        nodes.extend(self.section_nodes(snapshot))
        return nodes

    def resolve_import(
        self,
        specifier: str,
        from_file: str,
        alias_roots: dict[str, str],
        snapshot: RepoSnapshot | None = None,
    ) -> str | None:
        return self.resolver(specifier, from_file, alias_roots, snapshot)


class ViteReactRouterAdapter:
    """First-class adapter for Vite + react-router repos.

    Promotes the previously best-effort ``react-router``/``filesystem`` branch
    into a real enumerator: route nodes from the ``<Route path= element={<X/>}>``
    table (with a filesystem fallback) plus the shared in-page section nodes.
    """

    stack = "vite-react-router"
    section_nodes = staticmethod(_section_nodes)
    resolver = staticmethod(resolve_specifier)

    def enumerate_nodes(
        self, snapshot: RepoSnapshot, probe: ProbeResult,
    ) -> list[ScreenNode]:
        nodes = _extract_vite_routes(snapshot)
        nodes.extend(self.section_nodes(snapshot))
        return nodes

    def resolve_import(
        self,
        specifier: str,
        from_file: str,
        alias_roots: dict[str, str],
        snapshot: RepoSnapshot | None = None,
    ) -> str | None:
        return self.resolver(specifier, from_file, alias_roots, snapshot)

"""Navigation edge resolution — static regex scan over fetched UI files.

Resolves navigation call-sites in the already-fetched snapshot into two lists:
  * resolved NavEdge objects (literal, path_builder, registry, external)
  * UnresolvedEdge worklist items (dynamic targets whose destinations cannot be
    determined by static analysis)

100% deterministic: regex and string scanning only; no LLM and no AST parser.
Matches the pure-re convention used in github_gather.py.
"""

import logging
import re
from dataclasses import dataclass

from app.design_agent.codebase_map.nav_probe import ProbeResult
from app.design_agent.codebase_map.repo_reader import RepoSnapshot
from app.design_agent.codebase_map.types import NavEdge, ScreenNode, UnresolvedEdge

logger = logging.getLogger(__name__)

# ── call-site detection ────────────────────────────────────────────────────────

# Nav function invocations; captures the first argument (coarse — stops at ) , or newline)
_NAV_CALL_RE = re.compile(
    r"\b(?:goTo|navigate|navigateTo|router\.push|router\.replace)\s*\(([^),\n]*)"
)

# JSX <Link href=...> and <Link to=...> (handles "str", 'str', and {expr} forms)
_JSX_HREF_RE = re.compile(
    r"<Link\b[^>]*\bhref\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|\{([^}]+)\})"
)
_JSX_TO_RE = re.compile(
    r"<Link\b[^>]*\bto\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|\{([^}]+)\})"
)
# <a href=...> scanned to classify external links
_ANCHOR_HREF_RE = re.compile(
    r"<a\b[^>]*\bhref\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|\{([^}]+)\})"
)

# ── arg classification ─────────────────────────────────────────────────────────

# Template-literal interpolations
_INTERP_RE = re.compile(r"\$\{([^}]+)\}")

# Registry prefixes: ScreenId.X, routes.X, ROUTES.X, ROUTES["X"], ROUTES['X']
_REGISTRY_REF_RE = re.compile(
    r"^(?:ScreenId|ROUTES?|routes?)\.([A-Za-z_]\w*)"
    r"|^(?:ScreenId|ROUTES?|routes?)\[\"([A-Za-z_]\w*)\"\]"
    r"|^(?:ScreenId|ROUTES?|routes?)\['([A-Za-z_]\w*)'\]"
)

# Member access: bare_identifier.something — signals a typed variable on CLEAN repos
_MEMBER_ACCESS_RE = re.compile(r"^[a-zA-Z_$][\w$]*\.[a-zA-Z_$][\w$.]*$")

# Presence of a call expression — signals runtime-dispatched routing
_CALL_EXPR_RE = re.compile(r"\(")

# ── route-table extraction ─────────────────────────────────────────────────────

# Matches: [ScreenId.Key]: "/path", Key: "/path", Key = "/path"
_ROUTE_TABLE_ENTRY_RE = re.compile(
    r"\[?(?:ScreenId\.)?(\w+)\]?\s*[=:]\s*[\"']([^\"']+)[\"']"
)

# ── internal records ───────────────────────────────────────────────────────────


@dataclass
class _Site:
    file: str
    line: int
    raw_arg: str
    from_href: bool  # True for href= / to= attribute sources


# ── helpers ────────────────────────────────────────────────────────────────────


def _build_id_to_path(snapshot: RepoSnapshot, probe: ProbeResult) -> dict[str, str]:
    """Scan route-table / registry files and return {identifier_key: route_path}."""
    id_to_path: dict[str, str] = {}
    candidates = list(probe.route_table_files)
    if probe.registry_file and probe.registry_file not in candidates:
        candidates.append(probe.registry_file)
    for path in candidates:
        body = snapshot.files.get(path, "")
        for m in _ROUTE_TABLE_ENTRY_RE.finditer(body):
            key, route = m.group(1), m.group(2)
            if route.startswith("/"):
                id_to_path.setdefault(key, route)
    return id_to_path


def _normalize_template(raw: str) -> str:
    """Convert a template-literal string (with backticks) to a route pattern.

    ${bareIdentifier} -> :identifier  (bare name preserved)
    ${any_other_expr} -> :param       (generic sentinel)
    """

    def _replace(m: re.Match) -> str:
        expr = m.group(1).strip()
        # Bare identifier: letters, digits, underscore, $; no operators or calls
        return f":{expr}" if re.fullmatch(r"[a-zA-Z_$][a-zA-Z0-9_$]*", expr) else ":param"

    return _INTERP_RE.sub(_replace, raw[1:-1])  # strip surrounding backticks


def _extract_jsx_arg(m: re.Match) -> str:
    """Return a normalised raw_arg from a JSX attribute regex match (groups 1-3)."""
    if m.group(1) is not None:
        return f'"{m.group(1)}"'
    if m.group(2) is not None:
        return f"'{m.group(2)}'"
    val = m.group(3)
    return val.strip() if val else ""


def _discover_sites(snapshot: RepoSnapshot) -> list[_Site]:
    """Scan every fetched UI file for navigation call-sites."""
    sites: list[_Site] = []
    for path, body in snapshot.files.items():
        if not any(path.endswith(ext) for ext in (".ts", ".tsx", ".js", ".jsx")):
            continue

        in_block = False
        for lineno, line in enumerate(body.split("\n"), start=1):
            # Coarse block-comment tracking (perfect stripping not required — false
            # positives produce unresolved edges rather than wrong resolved edges)
            if in_block:
                if "*/" in line:
                    in_block = False
                continue
            if "/*" in line:
                in_block = True
                continue

            stripped = line.lstrip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue

            for m in _NAV_CALL_RE.finditer(line):
                sites.append(_Site(path, lineno, m.group(1).strip(), False))

            for m in _JSX_HREF_RE.finditer(line):
                arg = _extract_jsx_arg(m)
                if arg:
                    sites.append(_Site(path, lineno, arg, True))

            for m in _JSX_TO_RE.finditer(line):
                arg = _extract_jsx_arg(m)
                if arg:
                    sites.append(_Site(path, lineno, arg, True))

            for m in _ANCHOR_HREF_RE.finditer(line):
                arg = _extract_jsx_arg(m)
                if arg:
                    sites.append(_Site(path, lineno, arg, True))

    return sites


def _classify(
    raw_arg: str,
    id_to_path: dict[str, str],
    posture: str,
    from_href: bool,
) -> tuple[str, str, bool, str]:
    """Classify a raw call-site argument.

    Returns (to_route, kind, resolved, reason).
    reason is non-empty only when resolved=False.
    """
    s = raw_arg.strip()

    # Determine the inner value for quoted / template forms
    if len(s) >= 2 and s[0] in "\"'`" and s[-1] == s[0]:
        inner = s[1:-1]
    else:
        inner = s

    # External link
    if re.match(r"^(?:https?://|mailto:)", inner):
        return inner, "external", True, ""

    # Template literal with at least one interpolation
    if s.startswith("`") and s.endswith("`") and _INTERP_RE.search(s):
        return _normalize_template(s), "path_builder", True, ""

    # Quoted string path
    if s[:1] in "\"'" and inner.startswith("/"):
        return inner, "literal", True, ""

    # Registry reference
    rm = _REGISTRY_REF_RE.match(s)
    if rm:
        key = next(g for g in rm.groups() if g is not None)
        if key in id_to_path:
            return id_to_path[key], "registry", True, ""
        # Registry miss — goes to the worklist, never a fabricated path
        return "", "dynamic", False, "dynamic variable target"

    # Everything else: dynamic target
    return "", "dynamic", False, _classify_reason(s, posture, from_href)


def _classify_reason(arg: str, posture: str, from_href: bool) -> str:
    """Return a plain-English reason string for an unresolved call-site."""
    if _CALL_EXPR_RE.search(arg):
        return "runtime-dispatched routing"
    # Prop-href indirection: bare identifier in a JSX href attribute on a PARTIAL repo.
    # Target set is unbounded (any consumer may pass any value); do not chase consumers.
    if from_href and posture == "PARTIAL":
        return "prop-href indirection (unbounded)"
    # Member access on a CLEAN repo: most likely a typed enum / registry variable whose
    # candidate set is bounded by the enum definition.
    if _MEMBER_ACCESS_RE.match(arg) and posture == "CLEAN":
        return "typed registry variable (bounded candidates)"
    return "dynamic variable target"


# ── public API ─────────────────────────────────────────────────────────────────


def resolve_edges(
    snapshot: RepoSnapshot,
    probe: ProbeResult,
    nodes: list[ScreenNode],
) -> tuple[list[NavEdge], list[UnresolvedEdge]]:
    """Resolve navigation call-sites into edges and an unresolved worklist.

    Resolved edges are deduped by (from_route, to_route, kind) and sorted by
    (from_route, to_route).  Unresolved edges are sorted by call_site.
    """
    id_to_path = _build_id_to_path(snapshot, probe)
    file_to_route: dict[str, str] = {n.file: n.route for n in nodes if n.file}

    sites = _discover_sites(snapshot)

    resolved: list[NavEdge] = []
    unresolved: list[UnresolvedEdge] = []
    seen: set[tuple[str, str, str]] = set()

    for site in sites:
        from_route = file_to_route.get(site.file, "")
        call_site_id = f"{site.file}:{site.line}"

        to_route, kind, is_resolved, reason = _classify(
            site.raw_arg, id_to_path, probe.posture, site.from_href
        )

        if is_resolved:
            key = (from_route, to_route, kind)
            if key not in seen:
                seen.add(key)
                resolved.append(
                    NavEdge(
                        from_route=from_route,
                        to_route=to_route,
                        kind=kind,
                        resolved=True,
                        call_site=call_site_id,
                    )
                )
        else:
            unresolved.append(
                UnresolvedEdge(
                    from_route=from_route,
                    call_site=call_site_id,
                    reason=reason,
                )
            )

    resolved.sort(key=lambda e: (e.from_route, e.to_route))
    unresolved.sort(key=lambda e: e.call_site)

    logger.info(
        "codebase_map.edges repo=%s posture=%s n_resolved=%d n_unresolved=%d",
        snapshot.repo,
        probe.posture,
        len(resolved),
        len(unresolved),
    )

    return resolved, unresolved

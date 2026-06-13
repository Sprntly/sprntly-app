"""Stack detection + the pluggable enumerator-adapter registry.

The codebase-map "brain" (describe / locate / recreate / self-check) is
stack-general and transfers verbatim across frontend stacks.  Only the screen
enumerator + import resolver are stack-coupled.  This module formalises that
coupling behind a seam:

    detect_stack(snapshot) -> StackProfile        # which stack is this repo?
    select_adapter(profile) -> EnumeratorAdapter  # which enumerator to run?
    ADAPTERS                                       # detected stack -> adapter

Two first-class adapters ship deterministically: Next.js (App + Pages Router)
and Vite + react-router.  An unrecognised JS/TS repo degrades to a low-confidence
LLM-discovery fallback (the only model-touching path here) with the completeness
guarantee explicitly dropped and surfaced.  An unreadable non-JS/TS repo declines
loudly rather than emit a confident-but-wrong screen set.

Detection technique matches the rest of the pipeline: regex/string scanning plus
``package.json`` / ``tsconfig.json`` JSON parsing.  No JavaScript or TypeScript
AST parser is used or imported.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.design_agent.codebase_map.nav_probe import (
    _REACT_ROUTER_RE,
    ProbeResult,
)
from app.design_agent.codebase_map.nodes import (
    NextAppRouterAdapter,
    ViteReactRouterAdapter,
    resolve_specifier,
)
from app.design_agent.codebase_map.repo_reader import RepoSnapshot
from app.design_agent.codebase_map.types import ScreenNode

logger = logging.getLogger(__name__)


# ── profile model ──────────────────────────────────────────────────────────────

class StackProfile(BaseModel):
    """The detected frontend stack of a connected repo plus its capability signal."""

    stack: str = ""
    # "next-app" | "next-pages" | "vite-react-router" | "unknown-js-ts" |
    # "unreadable".  Empty only when detection itself errored (treated as a
    # neutral default that does not force a decline or a posture downgrade).
    confidence: str = "high"
    # "high" = a first-class deterministic adapter was selected;
    # "low"  = the LLM-discovery fallback (completeness NOT certifiable).
    language: str = "ts"
    # "ts" | "js" | "non-js-ts"
    alias_roots: dict[str, str] = Field(default_factory=dict)
    # tsconfig/jsconfig baseUrl-joined ``paths`` map for the import resolver
    # (e.g. {"@/*": "src/*"}).  Empty when no (t/j)sconfig is present.
    reason: str = ""
    # one-line human-readable detection reason, surfaced to the PM as the
    # capability/confidence signal.


# ── adapter protocol (the seam) ────────────────────────────────────────────────

@runtime_checkable
class EnumeratorAdapter(Protocol):
    """Structural contract every enumerator adapter satisfies.

    enumerate_nodes turns a snapshot + probe into the screen-node set for this
    stack; resolve_import maps an import specifier to a repo-relative path for
    the deep-reader.  Adapters are structural — no inheritance is required.
    """

    stack: str

    def enumerate_nodes(
        self, snapshot: RepoSnapshot, probe: ProbeResult,
    ) -> list[ScreenNode]: ...

    def resolve_import(
        self,
        specifier: str,
        from_file: str,
        alias_roots: dict[str, str],
        snapshot: RepoSnapshot | None = ...,
    ) -> str | None: ...


# ── loud-decline signal ────────────────────────────────────────────────────────

class UnreadableStackError(Exception):
    """Raised by the map builder when a repo's stack cannot be enumerated.

    Carries a human-readable reason.  The caller surfaces it as an explicit,
    user-facing decline ("this codebase stack isn't supported for
    codebase-context generation yet") — never a fabricated screen set.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ── detection signals ──────────────────────────────────────────────────────────

_JS_TS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_TS_SUFFIXES = (".ts", ".tsx")
_JS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs")

# App / Pages router file conventions. The intermediate segment is OPTIONAL so
# the App Router ROOT route (app/page.tsx) counts as a Next signal too — the
# probe's stricter route regex requires a nested segment, which would miss a
# root-only app.
_APP_PAGE_RE = re.compile(r"(?:^|/)app/(?:.+/)?page\.[jt]sx?$")
_PAGES_PAGE_RE = re.compile(r"(?:^|/)pages/.+\.[jt]sx?$")

# Non-JS/TS framework markers — presence with no JS/TS app signals the repo is a
# stack the enumerator does not support yet (a loud decline, not a guess).
_UNREADABLE_MARKERS: list[tuple[str, str]] = [
    ("config/routes.rb", "a Rails app"),
    ("Gemfile", "a Ruby/Rails app"),
    ("manage.py", "a Django app"),
    ("pubspec.yaml", "a Flutter app"),
]

# tsconfig/jsconfig comment + trailing-comma tolerance (these files are JSONC).
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _loads_jsonc(text: str) -> dict:
    """Parse a JSON-with-comments config file, tolerating trailing commas."""
    cleaned = _BLOCK_COMMENT_RE.sub(" ", text)
    cleaned = _LINE_COMMENT_RE.sub("", cleaned)
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", cleaned)
    parsed = json.loads(cleaned)
    return parsed if isinstance(parsed, dict) else {}


def _read_package_json(files: dict[str, str]) -> dict:
    body = files.get("package.json")
    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _merged_deps(pkg: dict) -> set[str]:
    names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        block = pkg.get(key)
        if isinstance(block, dict):
            names.update(block.keys())
    return names


def _read_alias_roots(files: dict[str, str]) -> dict[str, str]:
    """Read ``compilerOptions.baseUrl`` + ``paths`` from (t/j)sconfig.

    Returns ``{alias: baseUrl-joined first-target}`` (e.g. {"@/*": "src/*"}).
    Empty when no config or no paths are present.
    """
    body = files.get("tsconfig.json") or files.get("jsconfig.json")
    if not body:
        return {}
    try:
        config = _loads_jsonc(body)
    except (ValueError, TypeError):
        return {}
    opts = config.get("compilerOptions")
    if not isinstance(opts, dict):
        return {}
    base_url = opts.get("baseUrl") or "."
    base_prefix = "" if base_url in (".", "./", "") else base_url.strip("/")
    paths = opts.get("paths")
    if not isinstance(paths, dict):
        return {}
    roots: dict[str, str] = {}
    for alias, targets in paths.items():
        if not isinstance(targets, list) or not targets:
            continue
        target = targets[0]
        if not isinstance(target, str):
            continue
        joined = f"{base_prefix}/{target}".lstrip("/") if base_prefix else target.lstrip("./")
        roots[alias] = joined
    return roots


def _has_js_ts_sources(tree: list[str]) -> bool:
    return any(p.endswith(_JS_TS_SUFFIXES) for p in tree)


def _detect_language(tree: list[str], files: dict[str, str]) -> str:
    if any(p.endswith(_TS_SUFFIXES) for p in tree) or "tsconfig.json" in files:
        return "ts"
    if any(p.endswith(_JS_SUFFIXES) for p in tree) or "jsconfig.json" in files:
        return "js"
    return "non-js-ts"


def _unreadable_reason(tree: list[str], files: dict[str, str]) -> str:
    for marker, label in _UNREADABLE_MARKERS:
        if marker in files or any(p == marker or p.endswith("/" + marker) for p in tree):
            return (
                f"codebase stack isn't supported for codebase-context generation "
                f"yet (looks like {label})"
            )
    return "codebase stack isn't supported for codebase-context generation yet"


# ── detection ──────────────────────────────────────────────────────────────────

def detect_stack(snapshot: RepoSnapshot) -> StackProfile:
    """Classify a repo snapshot into a StackProfile (never raises).

    Deterministic: regex/string + package.json/tsconfig parse only.  On an
    unexpected internal error it degrades to ``unknown-js-ts`` (low confidence)
    rather than raising — the honest default for "stack not certifiable".
    """
    try:
        return _detect_stack(snapshot)
    except Exception:
        logger.warning(
            "codebase_map.stack repo=%s detection error; degrading to unknown-js-ts",
            getattr(snapshot, "repo", "?"), exc_info=True,
        )
        return StackProfile(
            stack="unknown-js-ts",
            confidence="low",
            language="js",
            reason="stack detection error — surfaces discovered heuristically, "
            "completeness not certifiable",
        )


def _detect_stack(snapshot: RepoSnapshot) -> StackProfile:
    files = snapshot.files
    tree = snapshot.tree_paths

    pkg = _read_package_json(files)
    deps = _merged_deps(pkg)
    alias_roots = _read_alias_roots(files)
    language = _detect_language(tree, files)

    has_next = "next" in deps
    has_vite = "vite" in deps
    has_react_router = bool({"react-router", "react-router-dom"} & deps)

    has_app_page = any(_APP_PAGE_RE.search(p) for p in tree)
    has_pages_page = any(_PAGES_PAGE_RE.search(p) for p in tree)
    has_route_jsx = any(_REACT_ROUTER_RE.search(b) for b in files.values())

    # ── first-class: Next.js App Router ──
    if has_app_page and not has_vite:
        signal = "next dependency" if has_next else "app-router file convention"
        return StackProfile(
            stack="next-app", confidence="high", language=language,
            alias_roots=alias_roots,
            reason=f"Next.js App Router detected (app/**/page.* + {signal})",
        )

    # ── first-class: Next.js Pages Router ──
    if has_pages_page and not has_vite:
        signal = "next dependency" if has_next else "pages file convention"
        return StackProfile(
            stack="next-pages", confidence="high", language=language,
            alias_roots=alias_roots,
            reason=f"Next.js Pages Router detected (pages/** + {signal})",
        )

    # ── first-class: Vite + react-router ──
    if (has_vite and has_react_router) or has_route_jsx:
        bits = []
        if has_vite:
            bits.append("vite dependency")
        if has_react_router:
            bits.append("react-router dependency")
        if has_route_jsx:
            bits.append("<Route> table")
        return StackProfile(
            stack="vite-react-router", confidence="high", language=language,
            alias_roots=alias_roots,
            reason="Vite + react-router detected (" + ", ".join(bits) + ")",
        )

    # ── Next dependency present but no filesystem signal yet ──
    if has_next:
        return StackProfile(
            stack="next-app", confidence="high", language=language,
            alias_roots=alias_roots,
            reason="Next.js detected (next dependency; no page files in the sampled tree)",
        )

    # ── recognised JS/TS, no first-class adapter → degraded discovery ──
    if bool(pkg) or _has_js_ts_sources(tree):
        return StackProfile(
            stack="unknown-js-ts", confidence="low",
            language=language if language != "non-js-ts" else "js",
            alias_roots=alias_roots,
            reason="stack not recognized — surfaces discovered heuristically, "
            "completeness not certifiable",
        )

    # ── unreadable non-JS/TS → loud decline ──
    return StackProfile(
        stack="unreadable", confidence="low", language="non-js-ts",
        alias_roots={},
        reason=_unreadable_reason(tree, files),
    )


# ── unknown-JS/TS fallback (the only model-touching adapter) ───────────────────

_FALLBACK_MODEL = "claude-sonnet-4-6"
_FALLBACK_MAX_TOKENS = 1500
_FALLBACK_MAX_NODES = 40
_FALLBACK_TREE_CHAR_CAP = 6000

_FALLBACK_SYSTEM = (
    "You map an unfamiliar web app's source tree to its screen surfaces. You are "
    "given a file-path listing and a few file bodies from a JavaScript/TypeScript "
    "repository whose routing convention was NOT recognised. Propose the distinct "
    "user-facing screens. Respond ONLY with JSON of the shape "
    '{\"screens\": [{\"route\": \"/path\", \"entry_component\": \"Name\", '
    '\"file\": \"repo/relative/path\"}]}. Use \"\" for any field you cannot '
    "determine. Do not invent files that are not in the listing. This is a "
    "best-effort pass; completeness is not guaranteed."
)


def _fallback_tree_text(snapshot: RepoSnapshot) -> str:
    listing = "\n".join(snapshot.tree_paths)
    return listing[:_FALLBACK_TREE_CHAR_CAP]


def _llm_discover_screens(
    snapshot: RepoSnapshot, client: object | None = None,
) -> list[dict]:
    """Single-shot LLM discovery of screen surfaces. Never raises → [] on failure.

    Mirrors the single-call messages.create + JSON-fence-strip pattern used by
    the locate service. The model/api-key client is imported lazily so importing
    this module does not pull the Anthropic SDK into a deterministic import path.
    """
    try:
        if client is None:
            from app.design_agent.client import get_design_agent_client

            client = get_design_agent_client()

        tree_text = _fallback_tree_text(snapshot)
        # Stable prefix = the source listing; carries the cache breakpoint so a
        # retry within the window reuses it.
        system_blocks = [
            {"type": "text", "text": _FALLBACK_SYSTEM},
            {
                "type": "text",
                "text": f"FILE LISTING:\n{tree_text}",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ]
        messages = [{"role": "user", "content": "List the screen surfaces as JSON."}]
        resp = client.messages.create(
            model=_FALLBACK_MODEL,
            max_tokens=_FALLBACK_MAX_TOKENS,
            system=system_blocks,
            messages=messages,
        )
        raw_text = resp.content[0].text
        text = raw_text.strip()
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        parsed = json.loads(text)
        screens = parsed.get("screens", []) if isinstance(parsed, dict) else []
        return [s for s in screens if isinstance(s, dict)]
    except Exception as exc:
        logger.warning(
            "codebase_map.stack repo=%s llm-discovery failed: %s",
            getattr(snapshot, "repo", "?"), type(exc).__name__,
        )
        return []


class LLMDiscoveryFallbackAdapter:
    """Degraded enumerator for an unrecognised JS/TS repo.

    Enumerates screens via a single LLM-discovery pass instead of the AST/regex
    completeness gate. The node set is therefore NOT deterministically
    certifiable: callers tag the build PARTIAL and surface the StackProfile
    reason so the capability downgrade is visible, never silent.

    ``discover`` is injectable so the adapter is unit-testable without a network
    call.
    """

    stack = "unknown-js-ts"
    resolver = staticmethod(resolve_specifier)

    def __init__(self, discover=None) -> None:
        self._discover = discover or _llm_discover_screens

    def enumerate_nodes(
        self, snapshot: RepoSnapshot, probe: ProbeResult,
    ) -> list[ScreenNode]:
        try:
            raw = self._discover(snapshot)
        except Exception:
            logger.warning(
                "codebase_map.stack repo=%s fallback discovery raised; no nodes",
                getattr(snapshot, "repo", "?"), exc_info=True,
            )
            return []
        nodes: list[ScreenNode] = []
        seen: set[str] = set()
        for item in raw[:_FALLBACK_MAX_NODES]:
            route = str(item.get("route", "") or "")
            component = str(item.get("entry_component", "") or "")
            file = str(item.get("file", "") or "")
            node_id = route or component or file
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            nodes.append(ScreenNode(
                route=route,
                entry_component=component,
                file=file,
                composed_components=[],
                is_route_state=False,
                kind="route",
                id=node_id,
            ))
        return nodes

    def resolve_import(
        self,
        specifier: str,
        from_file: str,
        alias_roots: dict[str, str],
        snapshot: RepoSnapshot | None = None,
    ) -> str | None:
        return self.resolver(specifier, from_file, alias_roots, snapshot)


class _DeclineAdapter:
    """No-emit adapter for an unreadable stack.

    Enumerates nothing — the map builder raises UnreadableStackError to surface
    the loud decline. Exposed via the registry so a direct enumerate call on an
    unreadable snapshot also returns no confident nodes.
    """

    stack = "unreadable"
    resolver = staticmethod(resolve_specifier)

    def enumerate_nodes(
        self, snapshot: RepoSnapshot, probe: ProbeResult,
    ) -> list[ScreenNode]:
        return []

    def resolve_import(
        self,
        specifier: str,
        from_file: str,
        alias_roots: dict[str, str],
        snapshot: RepoSnapshot | None = None,
    ) -> str | None:
        return self.resolver(specifier, from_file, alias_roots, snapshot)


# ── registry + selection ───────────────────────────────────────────────────────

_NEXT_ADAPTER = NextAppRouterAdapter()
_VITE_ADAPTER = ViteReactRouterAdapter()
_FALLBACK_ADAPTER = LLMDiscoveryFallbackAdapter()
_DECLINE_ADAPTER = _DeclineAdapter()

# Detected stack → enumerator adapter. The two Next conventions share the one
# Next adapter (its enumerate_nodes already branches on posture + convention).
ADAPTERS: dict[str, EnumeratorAdapter] = {
    "next-app": _NEXT_ADAPTER,
    "next-pages": _NEXT_ADAPTER,
    "vite-react-router": _VITE_ADAPTER,
    "unknown-js-ts": _FALLBACK_ADAPTER,
    "unreadable": _DECLINE_ADAPTER,
}


def select_adapter(profile: StackProfile) -> EnumeratorAdapter:
    """Resolve a StackProfile to its enumerator adapter.

    Falls back to the Next adapter for the empty/neutral sentinel (detection
    errored) — a deterministic best-effort that never invokes the LLM path on a
    detection bug.
    """
    adapter = ADAPTERS.get(profile.stack)
    if adapter is not None:
        return adapter
    return _NEXT_ADAPTER

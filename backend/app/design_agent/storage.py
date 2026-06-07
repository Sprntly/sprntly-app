"""Bundle staging — Vite build → Supabase Storage (primary) → filesystem (dev fallback).

Per AD3: prototype output stack is React + Vite + TS + Tailwind + shadcn/ui.
Per AD4: the P0-02 Vite plugin (`prototype-runtime/vite-plugin-anchor-id.ts`)
         annotates every JSX element with `data-anchor-id` at build time. This
         module MUST invoke `vite build` before staging so the staged bundle
         carries the annotations — the agent NEVER emits `data-anchor-id` itself.
Per AD5: the staged bundle IS the static SPA — no server-side rendering, no
         per-prototype runtime. Storage is a CDN-style read.

Decision (resolved 2026-05-28): Supabase Storage is the PRIMARY destination —
Sprntly's data plane is Supabase end-to-end and `db/client.py` already holds the
service-role key. Filesystem is the dev/test fallback used only when
`SUPABASE_STORAGE_BUCKET` is unset (pytest tmp_path, local dev without Supabase
Storage). It is NOT a parallel production path: a misconfigured bucket in
production should be fixed, not silently routed to disk.

Bundle path layout:
  prototypes/<prototype_id>/<checkpoint_id>/<file_path>

bundle_url shape:
  - Supabase Storage (primary): signed URL (ttl 24h) via create_signed_url
  - Filesystem (fallback): settings.storage_public_url + "/prototypes/.../index.html",
    or a file:// URI when storage_public_url is empty (test-only)

Async surface: `vite_build` and `stage_bundle` are async (the route awaits them
from its background task). The genuinely-blocking work — the `vite build`
subprocess, Supabase uploads, filesystem writes — runs in a worker thread via
`asyncio.to_thread` so a 60s build never stalls the event loop. This mirrors
`design_agent/runner.py`, which wraps the blocking Anthropic call the same way.

The synchronous DB helpers (`create_checkpoint` / `complete_prototype` /
`fail_prototype`) are owned by the route hook, not this module — `storage.py`
stays a pure build+stage primitive with no DB knowledge.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_SIGNED_URL_TTL_SECONDS = 60 * 60 * 24  # 24h — long enough for a demo session,
#                                         short enough a leaked URL expires soon.
# Vite build budget — Vite scaffold ~5-15s typical. The budget is env-configurable
# (P6-21): the single source of truth is settings.design_agent_vite_build_timeout_seconds
# (default 120s, env DESIGN_AGENT_VITE_BUILD_TIMEOUT_SECONDS), read at CALL-TIME inside
# _vite_build_sync so it stays tunable per environment and monkeypatchable in tests.
_TSC_TIMEOUT_SECONDS = 60               # P3-15 — runtime-break type-check budget;
#                                         tsc --noEmit on the small scaffold is a few
#                                         seconds; separate knob, not env-configurable.

# prototype-runtime/ sits at the repo root. This file is at
# backend/app/design_agent/storage.py → parents are [design_agent, app, backend,
# repo-root]. Module-level so tests can monkeypatch it to a missing path.
_RUNTIME_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "prototype-runtime"

# Scaffold entries never copied into the temp build dir: node_modules (symlinked
# instead — AC #3), build artefacts, and test trees (unreferenced by the entry,
# so they don't affect the build; skipping keeps the copy small).
_SCAFFOLD_EXCLUDE = {"node_modules", "dist", "dist-fixture", ".vite", "test", "__tests__"}


class ViteBuildError(RuntimeError):
    """Raised when `vite build` fails (non-zero exit, timeout, or no dist/)."""


# Runtime-BREAKING TS diagnostics: an emitted `ready` bundle with any of these
# blank-screens at runtime (undefined reference / missing module / missing export).
# Everything NOT in this set (implicit any, prop-type mismatch, unused var) is a
# COSMETIC error that still renders — per prototype-runtime/tsconfig.json:10-11 it
# must NOT gate generation. (The scaffold's own shadcn-ui files emit cosmetic
# TS2339/TS2353 noise on every build; a blanket `tsc` gate would fail every
# generation — which is exactly why this set is curated, not blanket.) Expand this
# set ONLY when a new runtime-break class is observed in production; document the
# observation when you do.
_FATAL_TS_CODES = frozenset({
    "TS2304",  # Cannot find name 'X'        (e.g. useState used, not imported) — the #20 bug
    "TS2307",  # Cannot find module 'X'       (bad import path)
    "TS2305",  # Module '"X"' has no exported member 'Y'  (named import → undefined at runtime)
    "TS2552",  # Cannot find name 'X'. Did you mean 'Y'?  (TS2304 variant)
})


class TypeCheckError(RuntimeError):
    """Raised when the built bundle contains a runtime-breaking type diagnostic
    (a code in _FATAL_TS_CODES). Cosmetic type errors do NOT raise. Subclass of
    RuntimeError, so callers that only catch the generic outer except would still
    catch it — the route widens its precise (ViteBuildError, FileNotFoundError)
    tuple to include this (P3-15 B3) so it gets the same fail_prototype handling."""


class TypeCheckRepairExhausted(TypeCheckError):
    """The post-build typecheck-repair loop ran its bounded re-tries and the built
    bundle still carries a runtime-breaking diagnostic. Distinct subclass of
    TypeCheckError, so callers that catch TypeCheckError still catch it, but the
    route can surface a precise error name in the failed row and the logs."""


# ─── Vite build (where the anchor-id plugin runs — AD4) ─────────────────────


async def vite_build(virtual_fs: dict[str, str]) -> dict[str, str]:
    """Build the agent's emitted TSX source into a static dist/ bundle.

    Per AD4 this is where the P0-02 Vite plugin runs and annotates every JSX
    element with `data-anchor-id`. Without this step the staged bundle is raw
    TSX and the F8/F13/F5 stories break.

    Implementation: copy the `prototype-runtime/` scaffold (its `vite.config.ts`,
    the `vite-plugin-anchor-id.ts` it imports, `index.html`, `tsconfig.json`, and
    the baseline `src/`) into a tempdir, symlink `node_modules` from the scaffold
    (so we never `npm install` per build), overlay the agent's `virtual_fs` on
    top, run `npx vite build`, and read every file out of the resulting `dist/`.

    Returns a dict of {dist_relative_path: file_content}. Non-UTF-8 files (rare
    for an SPA bundle) are base64-encoded under a `<path>.b64` key.

    Raises:
        FileNotFoundError: `prototype-runtime/` is missing.
        ViteBuildError: non-zero exit (stderr tail in the message), a build that
            exceeds the configured timeout (settings.design_agent_vite_build_timeout_seconds,
            default 120s), or a build that produced no dist/.
    """
    if not (_RUNTIME_ROOT / "package.json").exists():
        raise FileNotFoundError(
            f"prototype-runtime/ not found at {_RUNTIME_ROOT}; cannot vite build"
        )
    # The build is CPU/IO-bound subprocess work — keep it off the event loop.
    return await asyncio.to_thread(_vite_build_sync, _RUNTIME_ROOT, virtual_fs)


def _vite_build_sync(runtime_root: Path, virtual_fs: dict[str, str]) -> dict[str, str]:
    # P6-21 — read the build budget at CALL-TIME (not import-time) so it stays
    # tunable per environment and monkeypatchable in tests. Single source of truth
    # is config.py (default 120s); env override DESIGN_AGENT_VITE_BUILD_TIMEOUT_SECONDS.
    timeout_s = settings.design_agent_vite_build_timeout_seconds
    with tempfile.TemporaryDirectory(prefix="design-agent-build-") as build_dir:
        build_path = Path(build_dir)
        _copy_scaffold(runtime_root, build_path)
        _symlink_node_modules(runtime_root, build_path)
        # Overlay the agent's emitted files on top of the scaffold baseline.
        for rel_path, content in virtual_fs.items():
            target = build_path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        try:
            result = subprocess.run(
                ["npx", "vite", "build", "--outDir", "dist"],
                cwd=str(build_path),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ViteBuildError(
                f"vite build timed out after {timeout_s}s"
            ) from exc
        if result.returncode != 0:
            stderr_tail = (result.stderr or "")[-1000:]
            raise ViteBuildError(f"vite build exit={result.returncode}: {stderr_tail}")
        dist_dir = build_path / "dist"
        if not dist_dir.exists():
            raise ViteBuildError("vite build succeeded but dist/ was not produced")
        # P3-15 — scoped runtime-break backstop to the P1-10 autofixer. esbuild
        # transpiled cleanly above, but it does no name resolution, so a bundle
        # that references an unimported symbol (#20) staged `ready` and
        # blank-screened. Re-check here, scoped to _FATAL_TS_CODES only, before
        # the dist is read back for staging. Raises TypeCheckError on a fatal code.
        _typecheck_runtime_break(build_path)
        return _read_dist(dist_dir)


def _typecheck_runtime_break(build_path: Path) -> None:
    """Run `tsc --noEmit` in the assembled build tempdir and raise TypeCheckError
    iff any diagnostic line carries a code in _FATAL_TS_CODES.

    Pure backstop to the P1-10 static autofixer; leaves the `vite build` script
    untouched (prototype-runtime/tsconfig.json:10-11 intent — cosmetic type errors
    still render). The gate keys off diagnostic CODES, not message text, so a
    localized/reworded tsc message still triggers on the code.

    Fail-open on a tsc TOOLING failure (binary missing / timeout / any failure to
    RUN): log at WARNING and return WITHOUT raising — we never block a working
    bundle because tsc itself broke. Only an actual fatal-code diagnostic blocks.
    A non-zero exit with no fatal-code line (e.g. the scaffold's cosmetic
    TS2339/TS2353 noise) is therefore NOT fatal — only the curated codes are.
    """
    try:
        result = subprocess.run(
            ["npx", "tsc", "--noEmit", "-p", "tsconfig.json", "--pretty", "false"],
            cwd=str(build_path),
            capture_output=True,
            text=True,
            timeout=_TSC_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        # Fail-open: a tsc tooling problem must not nuke an otherwise-working
        # bundle. Identifier-free, error_class only (Rule #24).
        logger.warning(
            "typecheck_tool_failed error_class=%s (fail-open; bundle not blocked)",
            type(exc).__name__,
        )
        return
    # tsc prints diagnostics as `file(line,col): error TSXXXX: message` (plain,
    # --pretty false). Scan stdout + stderr; key strictly on the diagnostic code.
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    hits = [
        ln for ln in output.splitlines()
        if any(code in ln for code in _FATAL_TS_CODES)
    ]
    if hits:
        # Codes + truncated diagnostic only — no full source dump (Rule #24).
        raise TypeCheckError("runtime-breaking type errors: " + " | ".join(hits[:5]))


def _copy_scaffold(runtime_root: Path, build_path: Path) -> None:
    """Copy the prototype-runtime build inputs into the temp dir.

    Copies the whole scaffold minus `_SCAFFOLD_EXCLUDE` (vs cherry-picking named
    files) so a config file added to prototype-runtime/ in a later phase is
    picked up without editing this module — the `vite.config.ts` import of
    `./vite-plugin-anchor-id` is the load-bearing reason every sibling file must
    travel with it.
    """
    ignore = shutil.ignore_patterns(*_SCAFFOLD_EXCLUDE)
    for entry in runtime_root.iterdir():
        if entry.name in _SCAFFOLD_EXCLUDE:
            continue
        dest = build_path / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dest, ignore=ignore)
        else:
            shutil.copy2(entry, dest)


def _symlink_node_modules(runtime_root: Path, build_path: Path) -> None:
    """Symlink the scaffold's node_modules so Vite resolves deps without install.

    AC #3: no `npm install` subprocess per build. Vite resolves modules from the
    working dir, so a symlink is sufficient and ~free. No-op when the scaffold
    has no node_modules (the build then fails loudly via ViteBuildError, which is
    the correct signal that the deploy step never installed prototype-runtime).

    Falls back to junction (Windows) when os.symlink requires Developer Mode /
    admin privileges that aren't available.
    """
    nm = build_path / "node_modules"
    rt_nm = runtime_root / "node_modules"
    if rt_nm.exists() and not nm.exists():
        try:
            os.symlink(rt_nm, nm, target_is_directory=True)
        except OSError:
            # Windows without Developer Mode: symlink fails. Use a junction
            # (no privilege needed) via subprocess, or copy as last resort.
            if os.name == "nt":
                import subprocess as _sp
                try:
                    _sp.run(
                        ["cmd", "/c", "mklink", "/J", str(nm), str(rt_nm)],
                        check=True, capture_output=True,
                    )
                except Exception:
                    # Last resort: copy (slow but works everywhere).
                    shutil.copytree(rt_nm, nm, symlinks=True)
            else:
                raise


def _read_dist(dist_dir: Path) -> dict[str, str]:
    dist_files: dict[str, str] = {}
    for path in sorted(dist_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(dist_dir))
        try:
            dist_files[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Rare for SPA output (index.html/.js/.css are UTF-8); preserve
            # binary assets (fonts, images) under a .b64 sentinel key.
            dist_files[f"{rel}.b64"] = base64.b64encode(path.read_bytes()).decode("ascii")
    return dist_files


# ─── Build-repair: bounded unresolved-relative-import repair (P6-07) ─────────
#
# Fix #11 (2/2-reproduced 2026-06-04): a multi-screen scaffold gen imports a
# `./screens/*Screen` file into App.tsx that it never wrote (the per-write
# autofixer flags it at write time, but the run ends — degrade-converged — before
# the screen file is written, so the orphan survives into the final virtual_fs).
# The initial-gen FINAL build then dies with `ViteBuildError: Could not resolve
# "./screens/X"` and ships status=failed with no repair attempt.
#
# This is the FINAL-build backstop: across the whole virtual_fs, reconcile orphan
# relative imports (stub a `./screens/*` target so the App route stays navigable,
# strip a non-screen speculative import), then rebuild. Complementary to — not a
# replacement for — the per-write autofixer (first line) and the P3-15 tsc gate
# (which never runs on this class, because Vite/esbuild dies at bundle time
# BEFORE tsc). The candidate semantics MIRROR autofixer.js:46-77 (re-implemented
# in Python; the JS autofixer cannot be called from this pure helper) so the two
# agree on what "resolves".


class UnresolvedImportRepairExhausted(ViteBuildError):
    """Build still fails with unresolved relative modules after max_repairs repair
    passes. Distinct class (subclass of ViteBuildError, so the route's existing
    except tuple still catches it) so the route surfaces a precise error_class the
    P6-08 UI maps to a human 'a referenced screen could not be built' message."""


# Mirror autofixer.js:53-58 `resolvesInVfs` candidate set, in Python.
def _resolves_in_vfs(base: str, vfs_keys: set[str]) -> bool:
    candidates = (
        base, base + ".ts", base + ".tsx",
        base + "/index.ts", base + "/index.tsx",
    )
    return any(c in vfs_keys for c in candidates)


# Extract relative-import specifiers from a TSX/TS file's text. Mirrors the
# autofixer's `src.startsWith(".")` relative branch (autofixer.js:65) — captures
# both `import … from "<rel>"` and side-effect `import "<rel>"`. Pragmatic text
# scan, not a full parser: dynamic `import("<rel>")` / `require("<rel>")` are out
# of scope (the agent does not emit them in scaffold output).
_REL_IMPORT_RE = re.compile(
    r'''import\s+(?:[^'"]*?\s+from\s+)?['"](\.[^'"]+)['"]''',
)

# A `**/screens/*Screen` orphan is the load-bearing 2/2-reproduced case: a real
# navigation target the agent meant to fill → stub it (keeps the App route
# renderable). Everything else (helper/util) → strip the speculative import.
_SCREEN_BASE_RE = re.compile(r'(?:^|/)screens/[^/]*Screen$')


def _relative_imports(text: str) -> list[str]:
    return _REL_IMPORT_RE.findall(text)


def _screen_stub(component_name: str) -> str:
    """A minimal default-exported placeholder component for a stubbed screen.

    Carries NO `data-anchor-id` (AD4 — the Vite plugin applies anchors on rebuild;
    this raw TSX is overlaid before the build, exactly like the agent's own raw
    source). Valid default-export TSX so the P2-08 export serialiser reads it from
    `_source/` without error."""
    return (
        f"export default function {component_name}() {{\n"
        f"  return <div>{component_name}</div>;\n"
        f"}}\n"
    )


def _strip_orphan_import(text: str, src: str) -> str:
    """Drop the single-line import statement(s) for the orphan specifier `src`.

    Conservative: removes only the import line itself (matched by the quoted
    specifier on an `import …` line). Does NOT mutate JSX usage beyond the import
    (per the ticket scope — a bare identifier-only import is what the agent emits
    speculatively)."""
    quoted = (f'"{src}"', f"'{src}'")
    kept = [
        line for line in text.splitlines(keepends=True)
        if not (line.lstrip().startswith("import") and any(q in line for q in quoted))
    ]
    return "".join(kept)


def repair_unresolved_relative_imports(
    virtual_fs: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    """Reconcile orphan relative imports against the virtual_fs at FINAL-build time.

    For EACH .tsx/.ts file `key`, extract its `./…`-relative imports, produce each
    import's base via `os.path.normpath(os.path.join(os.path.dirname(key), src))`
    (the autofixer's dir-join, autofixer.js:51+67), and check
    `_resolves_in_vfs(base, set(virtual_fs))`. For an UNRESOLVED relative import:
    if `base` matches `**/screens/*Screen` write a MINIMAL placeholder component at
    `base + ".tsx"` (keeps the App route navigable) — else STRIP the orphan import
    line. Returns the repaired map + a list of `"stub <path>"` /
    `"strip <key>:<import>"` actions. Pure; no build, no network. Empty action
    list ⇒ nothing to repair (idempotent on a clean / already-repaired map)."""
    repaired = dict(virtual_fs)
    vfs_keys = set(repaired)
    actions: list[str] = []
    for key in list(virtual_fs):
        if not (key.endswith(".ts") or key.endswith(".tsx")):
            continue
        for src in _relative_imports(virtual_fs[key]):
            base = os.path.normpath(os.path.join(os.path.dirname(key), src))
            if _resolves_in_vfs(base, vfs_keys):
                continue
            if _SCREEN_BASE_RE.search(base):
                stub_key = base + ".tsx"
                if stub_key not in vfs_keys:
                    repaired[stub_key] = _screen_stub(os.path.basename(base))
                    vfs_keys.add(stub_key)
                    actions.append(f"stub {stub_key}")
            else:
                stripped = _strip_orphan_import(repaired[key], src)
                if stripped != repaired[key]:
                    repaired[key] = stripped
                    actions.append(f"strip {key}:{src}")
    return repaired, actions


# Real Rollup/esbuild emit is capital-C `Could not resolve "./screens/X" from …`
# (verified against prototype-runtime/node_modules rollup/vite at HEAD). The
# ViteBuildError message is `f"vite build exit={rc}: {stderr_tail}"` — the raw
# stderr tail, so the capital-C survives verbatim. Match CASE-INSENSITIVELY + a
# relative specifier (`./` / `../`); a literal lowercase match would MISS the real
# error and still ship status=failed (the exact bug this fixes).
_COULD_NOT_RESOLVE_RE = re.compile(
    r"could not resolve\s+['\"]?(\.{1,2}/[^'\"\s]+)", re.IGNORECASE,
)


def _is_unresolved_relative_import_error(message: str) -> bool:
    return bool(_COULD_NOT_RESOLVE_RE.search(message))


async def vite_build_with_repair(
    virtual_fs: dict[str, str], *, max_repairs: int = 2,
) -> tuple[dict[str, str], dict[str, str]]:
    """vite_build, with a bounded unresolved-relative-import repair loop.

    Returns `(dist_files, repaired_virtual_fs)` — the route REBINDS its local
    `virtual_fs` to the second element BEFORE the `_source/` staging step so the
    staged source matches the built dist. On a clean build (zero repairs) the
    second element is the original map unchanged.

    Attempt the build; on a ViteBuildError that names an unresolved relative
    module (CASE-INSENSITIVE — real emit is capital-C `Could not resolve`), apply
    `repair_unresolved_relative_imports` and rebuild, up to `max_repairs` times. A
    repair pass that makes NO change (no orphan it can fix — e.g. a dynamic import,
    or the failure is bad JSX / timeout) re-raises the ORIGINAL ViteBuildError
    unchanged, so non-orphan failures are never masked. On exhaustion with residual
    orphans, raise UnresolvedImportRepairExhausted. A non-matching ViteBuildError
    (or TypeCheckError / FileNotFoundError, which are not ViteBuildError) propagates
    untouched. Runs `vite_build` at most `max_repairs + 1` times."""
    current = virtual_fs
    last_error: ViteBuildError | None = None
    for attempt in range(max_repairs + 1):
        try:
            dist_files = await vite_build(current)
            return dist_files, current
        except ViteBuildError as exc:
            last_error = exc
            if not _is_unresolved_relative_import_error(str(exc)):
                raise  # bad JSX / timeout / non-relative — do NOT mask
            if attempt == max_repairs:
                break  # exhausted; fail-closed with the distinct class below
            current, repair_actions = repair_unresolved_relative_imports(current)
            if not repair_actions:
                raise  # no orphan this pass can fix → re-raise the original error
    residual = _COULD_NOT_RESOLVE_RE.findall(str(last_error)) if last_error else []
    raise UnresolvedImportRepairExhausted(
        f"unresolved relative modules after {max_repairs} repair passes: "
        f"{', '.join(residual) or 'unknown'}"
    ) from last_error


# ─── Bundle staging (Supabase primary / filesystem fallback) ────────────────


async def stage_bundle(
    *,
    prototype_id: int,
    checkpoint_id: int,
    files: dict[str, str],
    sub_prefix: str | None = None,
) -> str:
    """Write the files dict to storage; return the served bundle_url.

    `files` is {bundle_relative_path: content} (e.g. 'index.html',
    'assets/index-abc123.js'). The returned URL points at the entry: `index.html`
    when present, else the first file in the dict.

    When `sub_prefix` is set (e.g. '_source'), the storage path becomes
    `prototypes/<pid>/<cid>/<sub_prefix>/<rel_path>` and the returned URL points
    at the sub-prefix entry. Used by `_stage_complete_run` to stage the raw
    `virtual_fs` source alongside the built `dist/`. With `sub_prefix=None`
    (default) the layout is unchanged from before — `prototypes/<pid>/<cid>/<rel_path>`.

    Raises ValueError on an empty dict (a programming error — an empty bundle has
    nothing to serve; the route's success path guards against reaching here with
    no files, so this is a belt-and-braces invariant).
    """
    if not files:
        raise ValueError("stage_bundle: files dict is empty; nothing to stage")

    base = _bundle_prefix(prototype_id, checkpoint_id)
    prefix = f"{base}/{sub_prefix}" if sub_prefix else base
    entry = "index.html" if "index.html" in files else next(iter(files))

    bucket = _bucket_name()
    if bucket:
        url = await asyncio.to_thread(_stage_supabase_sync, bucket, prefix, files, entry)
        backend = "supabase"
    else:
        url = await asyncio.to_thread(_stage_filesystem_sync, prefix, files, entry)
        backend = "filesystem"
    # Identifiers only — no file content / bundle bytes / PII (Rule #24).
    logger.info(
        "bundle_staged prototype_id=%s checkpoint_id=%s sub_prefix=%s backend=%s entry=%s file_count=%s",
        prototype_id, checkpoint_id, sub_prefix or "", backend, entry, len(files),
    )
    return url


def _stage_supabase_sync(bucket: str, prefix: str, files: dict[str, str], entry: str) -> str:
    """Upload every file via the Supabase Storage client; return signed URL to entry.

    Uses the same `require_client()` service-role client as `db/prototypes.py`
    (bypasses RLS — server-trusted). `upsert: "true"` makes a re-stage of the
    same checkpoint_id idempotent rather than 409-ing.
    """
    from app.db.client import require_client

    storage = require_client().storage.from_(bucket)
    for rel_path, content in files.items():
        storage.upload(
            path=f"{prefix}/{rel_path}",
            file=content.encode("utf-8"),
            file_options={"content-type": _content_type(rel_path), "upsert": "true"},
        )
    signed = storage.create_signed_url(
        path=f"{prefix}/{entry}", expires_in=_SIGNED_URL_TTL_SECONDS
    )
    return _extract_signed_url(signed)


def _stage_filesystem_sync(prefix: str, files: dict[str, str], entry: str) -> str:
    """Write every file under settings.storage_dir; return the public URL to entry."""
    target = Path(settings.storage_dir).resolve() / prefix
    target.mkdir(parents=True, exist_ok=True)
    for rel_path, content in files.items():
        path = target / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    public_base = (settings.storage_public_url or "").rstrip("/")
    if not public_base:
        # No public URL configured → file:// URL (test-only / dev fallback).
        return (target / entry).as_uri()
    return f"{public_base}/{prefix}/{entry}"


def _extract_signed_url(signed: Any) -> str:
    """Pull the URL out of create_signed_url's response across supabase-py shapes.

    Different supabase-py versions return {"signedURL": ...}, {"signed_url": ...},
    or {"signedUrl": ...}. Support all three; empty string if none present.
    """
    if isinstance(signed, dict):
        return signed.get("signedURL") or signed.get("signed_url") or signed.get("signedUrl") or ""
    return ""


# ─── Helpers ────────────────────────────────────────────────────────────────


def _bucket_name() -> str | None:
    """Supabase Storage bucket name, or None when unconfigured (→ filesystem).

    Read directly from os.environ (matches connectors/figma_oauth.py optional-
    config pattern) — the bucket is a deployment concern, not a code concern.
    """
    return (os.environ.get("SUPABASE_STORAGE_BUCKET") or "").strip() or None


def _bundle_prefix(prototype_id: int, checkpoint_id: int) -> str:
    return f"prototypes/{prototype_id}/{checkpoint_id}"


_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".tsx": "text/plain; charset=utf-8",
    ".ts": "text/plain; charset=utf-8",
}


def _content_type(rel_path: str) -> str:
    for ext, ct in _CONTENT_TYPES.items():
        if rel_path.endswith(ext):
            return ct
    return "application/octet-stream"


# ─── Source readback (P2-04 — read raw virtual_fs staged under _source/) ──────


async def read_source_files_for_checkpoint(
    prototype_id: int,
    checkpoint_id: int,
) -> dict[str, str]:
    """Read every file staged under `prototypes/<pid>/<cid>/_source/` and return
    {relative_path: content}. Returns {} when the sub-prefix is empty or absent
    (graceful — historical pre-P2-04 prototypes never staged source).

    Supabase path (primary): list the bucket prefix + download each object.
    Filesystem path (fallback): walk the directory under settings.storage_dir.
    """
    sub_prefix = f"{_bundle_prefix(prototype_id, checkpoint_id)}/_source"
    bucket = _bucket_name()
    if bucket:
        return await asyncio.to_thread(_read_source_supabase_sync, bucket, sub_prefix)
    return await asyncio.to_thread(_read_source_filesystem_sync, sub_prefix)


def _read_source_supabase_sync(bucket: str, prefix: str) -> dict[str, str]:
    from app.db.client import require_client

    storage = require_client().storage.from_(bucket)
    try:
        objects = storage.list(prefix) or []
    except Exception:
        return {}
    out: dict[str, str] = {}
    for obj in objects:
        rel = obj.get("name") if isinstance(obj, dict) else getattr(obj, "name", None)
        if not rel:
            continue
        try:
            data = storage.download(f"{prefix}/{rel}")
            out[rel] = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        except Exception:  # includes UnicodeDecodeError
            continue
    return out


def _read_source_filesystem_sync(sub_prefix: str) -> dict[str, str]:
    target = Path(settings.storage_dir).resolve() / sub_prefix
    if not target.exists():
        return {}
    out: dict[str, str] = {}
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(target))
        try:
            out[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return out


# ─── Preview-image staging (BINARY — sibling to stage_bundle) ─────────────────
#
# stage_bundle is text-only: it does content.encode("utf-8") / write_text and so
# cannot carry a PNG. stage_preview_image is the binary sibling — it writes raw
# bytes under a `_preview/preview.png` object alongside the bundle, reusing the
# same Supabase-primary / filesystem-fallback dual path and the same 24h signed
# URL. The text staging path (stage_bundle and its helpers) is untouched.

_PREVIEW_OBJECT = "_preview/preview.png"
_PREVIEW_CONTENT_TYPE = "image/png"


async def stage_preview_image(
    *,
    prototype_id: int,
    checkpoint_id: int,
    png_bytes: bytes,
) -> str:
    """Write the preview PNG to storage; return the served URL.

    The PNG is stored at `prototypes/<pid>/<cid>/_preview/preview.png` with
    content-type `image/png`. Supabase Storage is the primary destination (signed
    URL, same 24h TTL as the bundle); the filesystem is the dev/test fallback when
    no bucket is configured (returns the public URL, or a `file://` URI when no
    public base is set). `upsert` makes a re-stage of the same checkpoint
    idempotent rather than a 409.
    """
    prefix = _bundle_prefix(prototype_id, checkpoint_id)
    object_path = f"{prefix}/{_PREVIEW_OBJECT}"

    bucket = _bucket_name()
    if bucket:
        url = await asyncio.to_thread(_stage_preview_supabase_sync, bucket, object_path, png_bytes)
        backend = "supabase"
    else:
        url = await asyncio.to_thread(_stage_preview_filesystem_sync, object_path, png_bytes)
        backend = "filesystem"
    # Identifiers only — never the PNG bytes or the signed URL value (Rule #24).
    logger.info(
        "preview_image_staged prototype_id=%s checkpoint_id=%s backend=%s byte_count=%s",
        prototype_id, checkpoint_id, backend, len(png_bytes),
    )
    return url


def _stage_preview_supabase_sync(bucket: str, object_path: str, png_bytes: bytes) -> str:
    """Upload the raw PNG bytes via the Supabase Storage client; return signed URL.

    Mirrors `_stage_supabase_sync` but for a single binary object: `file=` takes
    raw bytes (no `.encode`), the content-type is the PNG literal, and `upsert`
    keeps a re-stage of the same checkpoint idempotent. Reuses the same
    `require_client()` service-role client and the same `create_signed_url` /
    `_extract_signed_url` 24h-URL path as the bundle.
    """
    from app.db.client import require_client

    storage = require_client().storage.from_(bucket)
    storage.upload(
        path=object_path,
        file=png_bytes,
        file_options={"content-type": _PREVIEW_CONTENT_TYPE, "upsert": "true"},
    )
    signed = storage.create_signed_url(path=object_path, expires_in=_SIGNED_URL_TTL_SECONDS)
    return _extract_signed_url(signed)


def _stage_preview_filesystem_sync(object_path: str, png_bytes: bytes) -> str:
    """Write the PNG bytes under settings.storage_dir; return the public/file URL.

    Mirrors `_stage_filesystem_sync` but writes raw bytes (`write_bytes`) for the
    binary asset. Returns the configured public URL, or a `file://` URI when no
    public base is set (test-only / dev fallback)."""
    target = Path(settings.storage_dir).resolve() / object_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(png_bytes)
    public_base = (settings.storage_public_url or "").rstrip("/")
    if not public_base:
        return target.as_uri()
    return f"{public_base}/{object_path}"

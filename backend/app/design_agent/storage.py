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
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_SIGNED_URL_TTL_SECONDS = 60 * 60 * 24  # 24h — long enough for a demo session,
#                                         short enough a leaked URL expires soon.
_VITE_BUILD_TIMEOUT_SECONDS = 60        # build budget — Vite scaffold ~5-15s typical.

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
        ViteBuildError: non-zero exit (stderr tail in the message), 60s timeout,
            or a build that produced no dist/.
    """
    if not (_RUNTIME_ROOT / "package.json").exists():
        raise FileNotFoundError(
            f"prototype-runtime/ not found at {_RUNTIME_ROOT}; cannot vite build"
        )
    # The build is CPU/IO-bound subprocess work — keep it off the event loop.
    return await asyncio.to_thread(_vite_build_sync, _RUNTIME_ROOT, virtual_fs)


def _vite_build_sync(runtime_root: Path, virtual_fs: dict[str, str]) -> dict[str, str]:
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
                timeout=_VITE_BUILD_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ViteBuildError(
                f"vite build timed out after {_VITE_BUILD_TIMEOUT_SECONDS}s"
            ) from exc
        if result.returncode != 0:
            stderr_tail = (result.stderr or "")[-1000:]
            raise ViteBuildError(f"vite build exit={result.returncode}: {stderr_tail}")
        dist_dir = build_path / "dist"
        if not dist_dir.exists():
            raise ViteBuildError("vite build succeeded but dist/ was not produced")
        return _read_dist(dist_dir)


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
    """
    nm = build_path / "node_modules"
    rt_nm = runtime_root / "node_modules"
    if rt_nm.exists() and not nm.exists():
        os.symlink(rt_nm, nm, target_is_directory=True)


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

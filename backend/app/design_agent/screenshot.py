"""Best-effort prototype preview screenshot.

Render a staged prototype bundle once in headless Chromium and return the PNG
bytes, so the preview card can show a real, lightweight thumbnail instead of a
heavy live iframe or a neutral placeholder.

The bundle is a Vite-built single-page app: `index.html` references its JS/CSS
under relative `./assets/*` paths and boots via `<script type="module">`. The
capture therefore renders the bundle from a LOCAL LOOPBACK STATIC SERVER (a
short-lived stdlib `http.server` bound to `127.0.0.1:<ephemeral>`), NOT from the
signed Supabase object URL: under a per-object signature the relative
`./assets/*.js` cannot resolve and the module script never executes, so React
never mounts and only the empty `#root` shell paints. Served from a local root,
the relative assets resolve and execute, the SPA hydrates, and the screenshot
captures the rendered app. `file://` is not usable here because ES-module
scripts are blocked over `file://` by CORS.

Reuses the same Playwright dependency the website design-system extractor already
vendors — no new dependency. The Playwright import is deferred behind the
``_resolve_async_playwright`` seam so this module imports cleanly on hosts where
Chromium is not provisioned (tests monkeypatch the seam; the real import runs
only at capture time).

Capture is HONEST-DEGRADE: ``capture_bundle_screenshot`` returns ``None`` on ANY
failure (no Playwright, launch failure, navigation error, timeout, a bundle whose
``#root`` never populates) and NEVER raises to its caller. The caller treats
``None`` as "no thumbnail" and completes the prototype anyway — a flaky or absent
browser, or a render that never mounts, must never block completion, and no
fake/placeholder image is ever substituted. The browser and the local server are
both disposed per call (no pool), on every path.
"""
from __future__ import annotations

import asyncio
import base64
import functools
import http.server
import logging
import socket
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# Navigation wall-clock cap (ms). Matches the website extractor: a slow or hung
# page must not hold the completion hook longer than this before degrading.
_NAV_TIMEOUT_MS = 8000

# Render-wait cap (ms). After navigation we wait for the SPA to mount real DOM
# under #root before screenshotting; a bundle that never hydrates degrades to
# None within this bound rather than capturing the empty shell.
_RENDER_TIMEOUT_MS = 8000

# Short settle after the SPA mounts: a bounded network-idle window so async
# chunks / fonts paint before the screenshot. Capped small so a perpetually
# busy page (polling/websocket) does not stall the capture indefinitely.
_SETTLE_TIMEOUT_MS = 2000

# Selector that proves the SPA has mounted: a child element under #root. The
# un-hydrated index.html shell has an empty <div id="root"></div>; once React
# mounts there is at least one child, so this distinguishes rendered from shell.
_RENDER_SELECTOR = "#root > *"

# Binary dist files are carried in the bundle dict under a `.b64` sentinel key
# (see storage._read_dist). Materialization reverses that to real bytes.
_B64_SUFFIX = ".b64"

# Fixed thumbnail viewport. Viewport-only screenshot (full_page off) keeps the
# PNG small — the card downscales it further client-side.
_VIEWPORT = {"width": 1280, "height": 800}

# Standard headless-CI Chromium flags for bounded container memory, identical to
# the website extractor: --disable-dev-shm-usage avoids /dev/shm exhaustion in
# small containers; --no-sandbox is required when Chromium runs as root in CI/EC2.
_CHROMIUM_ARGS = ["--disable-dev-shm-usage", "--no-sandbox"]


def _resolve_async_playwright():
    """Lazy-import indirection for ``playwright.async_api.async_playwright``.

    Kept as a seam so this module imports cleanly on hosts without Playwright
    (tests monkeypatch this to inject a fake factory; the real import only runs at
    capture time on a host that has the dependency). Mirrors the website
    extractor's seam so the two share the same import posture.
    """
    from playwright.async_api import async_playwright

    return async_playwright


def _materialize_bundle(files: dict[str, str], root: Path) -> None:
    """Write the bundle dict to ``root`` as a real on-disk SPA tree.

    ``files`` is {relative_path: content}, the same shape stage_bundle stages —
    text files (index.html, assets/*.js/css) carried verbatim, binary assets
    (fonts/images) carried base64 under a `.b64` sentinel key. Reverses the b64
    sentinel back to raw bytes so the served tree is byte-identical to the dist/.
    """
    for rel, content in files.items():
        if rel.endswith(_B64_SUFFIX):
            target = root / rel[: -len(_B64_SUFFIX)]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(content))
        else:
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler that does not write to stderr per request."""

    def log_message(self, *args, **kwargs):  # noqa: D401 — silence access log
        return


def _serve_dir(root: Path) -> tuple[http.server.ThreadingHTTPServer, int]:
    """Start a loopback static server over ``root`` on an ephemeral port.

    Binds 127.0.0.1:0 so the OS assigns a free port (no collision), serves the
    directory in a daemon thread, and returns (server, port). The caller MUST
    call ``server.shutdown()`` to tear it down. Loopback-only: nothing is exposed
    off-host.
    """
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.socket.getsockname()[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


async def capture_bundle_screenshot(files: dict[str, str]) -> bytes | None:
    """Render the SPA bundle ``files`` locally in headless Chromium → PNG bytes.

    ``files`` is the built dist/ as {relative_path: content} (the same dict
    ``stage_bundle`` stages): ``index.html`` plus relative ``assets/*`` and
    optional ``*.b64`` binary assets. The bundle is materialized to a temp dir
    and served over a loopback static server so the SPA's relative module scripts
    resolve and React mounts — capturing the rendered app, not the empty shell.

    Returns the screenshot PNG bytes on success, or ``None`` on ANY failure —
    Playwright not installed (ImportError), Chromium launch failure, navigation
    error, navigation timeout, OR a bundle whose ``#root`` never populates within
    the render-wait cap (honest-degrade: a non-hydrating bundle yields None, never
    a shell screenshot). NEVER raises to the caller: capture is best-effort and
    must never block prototype completion. The browser and the local server are
    both disposed per call on every path, including the error path.

    Bundle contents are intentionally not logged here. Observability (with stable
    identifiers) is the caller's job.
    """
    server: http.server.ThreadingHTTPServer | None = None
    try:
        if not files or "index.html" not in files:
            # Nothing renderable — degrade rather than serve a 404 shell.
            return None
        with tempfile.TemporaryDirectory(prefix="design-agent-preview-") as tmp:
            root = Path(tmp)
            _materialize_bundle(files, root)
            server, port = _serve_dir(root)
            url = f"http://127.0.0.1:{port}/index.html"

            async_playwright = _resolve_async_playwright()
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
                context = await browser.new_context(viewport=dict(_VIEWPORT))
                try:
                    page = await context.new_page()
                    await page.goto(url, wait_until="load", timeout=_NAV_TIMEOUT_MS)
                    # Wait for the SPA to actually mount before screenshotting, so
                    # we never capture the un-hydrated #root shell. A bundle that
                    # never hydrates times out here and degrades to None below.
                    await page.wait_for_selector(_RENDER_SELECTOR, timeout=_RENDER_TIMEOUT_MS)
                    # Bounded settle for async chunks / fonts; a perpetually busy
                    # page falls through after the cap rather than stalling.
                    try:
                        await page.wait_for_load_state(
                            "networkidle", timeout=_SETTLE_TIMEOUT_MS
                        )
                    except Exception:  # noqa: BLE001 — settle is best-effort.
                        pass
                    return await page.screenshot()
                finally:
                    # Dispose per call even on the error path (no browser pool).
                    await context.close()
                    await browser.close()
    except Exception:  # noqa: BLE001 — honest-degrade: a capture failure is never fatal.
        # No bundle contents in the log line; error_class is surfaced by the
        # caller alongside the prototype identifiers it owns.
        return None
    finally:
        if server is not None:
            # Tear the loopback server down on every path (success, render miss,
            # exception). shutdown() is blocking; keep it off the event loop.
            await asyncio.to_thread(server.shutdown)
            server.server_close()

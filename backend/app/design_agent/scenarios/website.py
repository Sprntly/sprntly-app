"""Scenario B website design-system extractor (P5-01).

Self-hosted headless extractor: launch Chromium via Playwright, navigate to a
public URL, dismiss common cookie banners, sample computed styles on key
elements via ``page.evaluate()``, and return a typed :class:`WebsiteDesignSystem`.

Scope (P5-01): the extractor ONLY. It does NOT wire into the generate path,
does NOT build the manual color-picker floor, and does NOT touch the scaffold
prompt — that is P5-02. On any error OR below-confidence sampling this returns
``None`` (the FALLBACK SENTINEL); P5-02 treats ``None`` as "show the manual
color-picker".

Per BUILD-PHASES §Phase 5: the browser instance is disposed after each request
(no pool); an 8s navigation wall-clock cap; cookie-banner dismissal is
best-effort and never fatal. A flaky public site must never fail the whole
generation request — the function never raises to its caller.
"""
from __future__ import annotations

import logging
from typing import TypedDict
from urllib.parse import urlsplit

from app.net_guard import UnsafeURLError, assert_public_url
from app.design_agent.design_system.adapters import _css_color_to_hex

logger = logging.getLogger(__name__)

# Navigation wall-clock cap (ms) — AC4. A flaky public site must not block a
# generation request longer than this.
_NAV_TIMEOUT_MS = 8000

# Best-effort cookie-banner dismissal. Each selector is tried in order; the
# first that clicks wins and the loop breaks. Per-selector failures are
# swallowed — a banner left up is acceptable, this only improves sampling and
# never gates it.
_COOKIE_SELECTORS = [
    "[aria-label*='cookie' i] button",
    "[id*='cookie-banner' i] button",
    "[class*='consent' i] button",
    "button:has-text('Accept')",
    "button:has-text('Agree')",
]

# Standard headless-CI Chromium flags for bounded container memory. Not novel
# config: --disable-dev-shm-usage avoids /dev/shm exhaustion in small
# containers; --no-sandbox is required when Chromium runs as root in CI/EC2.
_CHROMIUM_ARGS = ["--disable-dev-shm-usage", "--no-sandbox"]


class WebsiteDesignSystem(TypedDict):
    primary_color: str            # hex or rgb() string sampled from the largest visible button
    background_color: str         # body computed background-color
    heading_font_family: str      # h1 computed font-family (first family in the stack)
    heading_size_scale: str       # h1 computed font-size (px string, e.g. "48px")
    body_font_family: str         # body computed font-family
    border_radius_convention: str  # button computed border-radius (px string)
    spacing_scale_samples: list[str]  # computed padding samples from button + header/nav
    logo_url: str | None          # best-effort: header img src or og:image; None if absent
    surface_color: str            # computed background of a representative raised card/section
    border_color: str             # computed border-color of a visibly-bordered element
    muted_color: str              # computed color of a secondary/muted text element
    elevation_hint: str           # "shadows" / "borders" / "" — how a card separates from the page
    component_counts: dict[str, int]  # type name -> count of likely instances found in the DOM


# Single ``page.evaluate()`` sampler. Returns a raw dict; missing elements yield
# empty strings / null so the Python side maps defensively. Every sampled value
# is a short, non-PII style string (color, font family, px size) — never page
# content, so it is safe under the Rule #24 observability minimum.
_SAMPLER_JS = r"""
() => {
  const cs = (el) => el ? getComputedStyle(el) : null;
  const body = document.body;
  const bodyCs = cs(body);
  const h1 = document.querySelector('h1');
  const h1Cs = cs(h1);

  // Primary action = the most prominent filled call-to-action. Real CTAs are
  // often links styled as buttons (not <button>s), and the biggest <button> can
  // be a transparent icon/ghost button — so search a broader set and skip
  // anything invisible or transparent-filled. A transparent fill would leak a
  // default accent downstream, so it never wins here.
  const isTransparent = (c) =>
    !c || c === 'transparent' || /rgba\([^)]*,\s*0(\.0+)?\s*\)/.test(c);
  // Saturation of a color in [0,1] — how chromatic it is. A brand color is
  // chromatic; a black / white / gray button fill is near-zero. Used so a
  // common monochrome button cannot outrank the real brand color on size.
  const saturationOf = (c) => {
    let r, g, b;
    const m = (c || '').match(/rgba?\(([^)]+)\)/i);
    if (m) {
      [r, g, b] = m[1].split(',').map((x) => parseFloat(x));
    } else if (c && c[0] === '#' && c.length >= 7) {
      r = parseInt(c.slice(1, 3), 16);
      g = parseInt(c.slice(3, 5), 16);
      b = parseInt(c.slice(5, 7), 16);
    } else {
      return 0;
    }
    if ([r, g, b].some((v) => Number.isNaN(v))) return 0;
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    if (max === min) return 0;
    const l = (max + min) / 2;
    return (max - min) / (1 - Math.abs(2 * l - 1));
  };
  const SAT_THRESHOLD = 0.15;
  const ctaSelector =
    'button, [role="button"], a[class*="btn" i], a[class*="cta" i], a[class*="button" i]';
  // Collect every visible, non-transparent candidate with its fill + size.
  const candidates = [];
  for (const el of document.querySelectorAll(ctaSelector)) {
    const rect = el.getBoundingClientRect();
    const area = rect.width * rect.height;
    if (area <= 0) continue;                       // not laid out / zero-area
    const elCs = getComputedStyle(el);
    if (elCs.visibility === 'hidden' || elCs.display === 'none') continue;
    const bg = elCs.backgroundColor;
    if (isTransparent(bg)) continue;               // unfilled / ghost CTA
    candidates.push({ el, area, top: rect.top, sat: saturationOf(bg) });
  }
  // Largest by area, breaking ties toward the candidate higher up the page
  // (likelier the hero CTA).
  const pickLargest = (list) =>
    list.sort((a, b) => (b.area - a.area) || (a.top - b.top))[0] || null;
  // Prefer a chromatic brand color; only if none qualifies fall back to the
  // largest neutral fill, then to the largest plain <button>.
  const chosen =
    pickLargest(candidates.filter((c) => c.sat >= SAT_THRESHOLD)) ||
    pickLargest(candidates);
  let primaryBtn = chosen ? chosen.el : null;
  if (!primaryBtn) {
    let maxArea = 0;
    for (const b of document.querySelectorAll('button')) {
      const area = b.offsetWidth * b.offsetHeight;
      if (area > maxArea) { maxArea = area; primaryBtn = b; }
    }
  }
  const btnCs = cs(primaryBtn);

  // Primary color: the button's fill when it has one, else its text color (a
  // ghost button's text often carries the brand accent).
  let primaryColor = '';
  if (btnCs) {
    primaryColor = (!isTransparent(btnCs.backgroundColor)
      ? btnCs.backgroundColor
      : (btnCs.color || ''));
  }

  // Spacing scale: button padding + header/nav padding.
  const spacing = [];
  if (btnCs && btnCs.padding) spacing.push(btnCs.padding);
  const nav = document.querySelector('header, nav');
  const navCs = cs(nav);
  if (navCs && navCs.padding) spacing.push(navCs.padding);

  // Logo: header img src, else og:image meta. Best-effort, null if absent.
  let logoUrl = null;
  const headerImg = document.querySelector('header img');
  if (headerImg && headerImg.src) {
    logoUrl = headerImg.src;
  } else {
    const og = document.querySelector("meta[property='og:image']");
    if (og && og.content) logoUrl = og.content;
  }

  // Representative raised surface: the first card/section whose background
  // differs from the body's. Recovers a warm/cool surface tone the body color
  // alone misses. Reuses the isTransparent guard defined above.
  let surfaceColor = '';
  const bodyBg = bodyCs ? bodyCs.backgroundColor : '';
  for (const el of document.querySelectorAll('[class*="card" i], section, article')) {
    const elCs = cs(el);
    if (!elCs) continue;
    const bg = elCs.backgroundColor;
    if (bg && bg !== bodyBg && !isTransparent(bg)) { surfaceColor = bg; break; }
  }

  // Border tone: the border-color of the first element with a visible border
  // (non-zero width, non-transparent color).
  let borderColor = '';
  for (const el of document.querySelectorAll(
    '[class*="card" i], section, article, button, input, table, td, th, hr'
  )) {
    const elCs = cs(el);
    if (!elCs) continue;
    const width = parseFloat(elCs.borderTopWidth || elCs.borderWidth || '0') || 0;
    const bc = elCs.borderTopColor || elCs.borderColor || '';
    if (width > 0 && bc && !isTransparent(bc)) { borderColor = bc; break; }
  }

  // Muted text tone: a secondary / muted text element's color.
  let mutedColor = '';
  const mutedEl = document.querySelector('[class*="muted" i], [class*="secondary" i], p');
  const mutedElCs = cs(mutedEl);
  if (mutedElCs && mutedElCs.color) mutedColor = mutedElCs.color;

  // Elevation signal: how the representative card/section separates from the
  // page — a real box-shadow reads as "shadows", a visible border as "borders".
  // Empty when neither is detectable, so the token keeps its default.
  let elevationHint = '';
  const cardEl = document.querySelector('[class*="card" i], section, article');
  const cardCs = cs(cardEl);
  if (cardCs) {
    const shadow = cardCs.boxShadow;
    const bWidth = parseFloat(cardCs.borderTopWidth || cardCs.borderWidth || '0') || 0;
    const bColor = cardCs.borderTopColor || cardCs.borderColor || '';
    if (shadow && shadow !== 'none') {
      elevationHint = 'shadows';
    } else if (bWidth > 0 && bColor && !isTransparent(bColor)) {
      elevationHint = 'borders';
    }
  }

  // Component inventory: count how many of a known set of UI primitive types
  // appear, by tag / role / class heuristics. Counts only — never any element
  // content. Each count is capped so the result object stays small.
  const componentSelectors = {
    accordion: '[class*="accordion" i]',
    alert: '[role="alert"], [class*="alert" i]',
    avatar: '[class*="avatar" i]',
    badge: '[class*="badge" i], [class*="chip" i]',
    button: 'button, [role="button"], [class*="btn" i]',
    card: '[class*="card" i]',
    checkbox: 'input[type="checkbox"], [role="checkbox"]',
    dialog: 'dialog, [role="dialog"]',
    drawer: '[class*="drawer" i]',
    dropdown: '[class*="dropdown" i]',
    form: 'form',
    input: 'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), [class*="input" i]',
    menu: '[role="menu"], [class*="menu" i]',
    modal: '[class*="modal" i]',
    popover: '[class*="popover" i]',
    select: 'select, [role="listbox"], [class*="select" i]',
    sheet: '[class*="sheet" i]',
    table: 'table, [role="table"]',
    tabs: '[role="tablist"], [role="tab"], [class*="tab" i]',
    textarea: 'textarea',
    toast: '[class*="toast" i]',
    tooltip: '[role="tooltip"], [class*="tooltip" i]',
  };
  const componentCounts = {};
  for (const type in componentSelectors) {
    let n = 0;
    try { n = document.querySelectorAll(componentSelectors[type]).length; } catch (e) { n = 0; }
    if (n > 0) componentCounts[type] = Math.min(n, 999);
  }

  return {
    primary_color: primaryColor,
    background_color: bodyCs ? bodyCs.backgroundColor : '',
    heading_font_family: h1Cs ? h1Cs.fontFamily : '',
    heading_size_scale: h1Cs ? h1Cs.fontSize : '',
    body_font_family: bodyCs ? bodyCs.fontFamily : '',
    border_radius_convention: btnCs ? btnCs.borderRadius : '',
    spacing_scale_samples: spacing,
    logo_url: logoUrl,
    surface_color: surfaceColor,
    border_color: borderColor,
    muted_color: mutedColor,
    elevation_hint: elevationHint,
    component_counts: componentCounts,
  };
}
"""


def _first_family(font_family: str) -> str:
    """First family in a computed font-family stack, quotes stripped.

    ``'"Inter", system-ui, sans-serif'`` -> ``'Inter'``. The scaffold prompt
    (P5-02) consumes prose, so a single human-readable family reads better than
    the full CSS stack.
    """
    if not font_family:
        return ""
    first = font_family.split(",")[0].strip()
    return first.strip("\"'")


def _below_confidence(ds: WebsiteDesignSystem) -> bool:
    """Below-confidence = no usable primary color sampled OR no heading font
    family detected. Either alone yields output too generic to justify the
    Chromium cost — the manual color-picker floor is strictly better in that
    case, so the caller is told to fall back via the ``None`` sentinel.

    A primary color that does not convert to a usable hex (transparent or
    unparseable) counts as absent: a ghost/transparent CTA fill must not pass
    the floor and leak a default accent into the tokens downstream. This mirrors
    the convertibility check ``WebExtractor.normalize`` applies, so the sampler's
    own confidence gate and the adapter's stay consistent.
    """
    usable_primary = bool(ds["primary_color"]) and (
        _css_color_to_hex(ds["primary_color"]) is not None
    )
    return (not usable_primary) or (not ds["heading_font_family"])


def _map_sample(raw: dict | None) -> WebsiteDesignSystem:
    """Map the raw ``page.evaluate()`` dict onto the typed 8-field design
    system, defending against missing keys / ``None`` values."""
    raw = raw or {}
    spacing = raw.get("spacing_scale_samples") or []
    return WebsiteDesignSystem(
        primary_color=(raw.get("primary_color") or "").strip(),
        background_color=(raw.get("background_color") or "").strip(),
        heading_font_family=_first_family(raw.get("heading_font_family") or ""),
        heading_size_scale=(raw.get("heading_size_scale") or "").strip(),
        body_font_family=(raw.get("body_font_family") or "").strip(),
        border_radius_convention=(raw.get("border_radius_convention") or "").strip(),
        spacing_scale_samples=[s for s in spacing if s],
        logo_url=raw.get("logo_url") or None,
        surface_color=(raw.get("surface_color") or "").strip(),
        border_color=(raw.get("border_color") or "").strip(),
        muted_color=(raw.get("muted_color") or "").strip(),
        elevation_hint=(raw.get("elevation_hint") or "").strip(),
        component_counts=dict(raw.get("component_counts") or {}),
    )


def _resolve_async_playwright():
    """Lazy-import indirection for ``playwright.async_api.async_playwright``.

    Kept as a seam so this module imports cleanly on hosts where Playwright is
    not installed (CI mocks the browser — no live Chromium). Tests monkeypatch
    this function to inject a fake factory; the real import only runs at request
    time on a host that has the dependency.
    """
    from playwright.async_api import async_playwright

    return async_playwright


def _is_timeout(exc: Exception) -> bool:
    """Name-based classification of a Playwright navigation timeout.

    Playwright raises ``playwright.async_api.TimeoutError`` on nav timeout. We
    classify it by class name rather than importing the symbol so this module
    stays importable on hosts where Playwright is absent (the whole point of the
    ``_resolve_async_playwright`` lazy seam — no top-level playwright import).
    """
    return type(exc).__name__ == "TimeoutError"


async def _dismiss_cookie_banner(page) -> None:
    """Best-effort: click the first matching cookie-consent control, then stop.

    Never fatal — per-selector failures are swallowed so a stubborn banner
    cannot abort sampling.
    """
    for sel in _COOKIE_SELECTORS:
        try:
            await page.locator(sel).first.click(timeout=500)
            break
        except Exception:
            continue


async def extract_website_design_system(url: str) -> WebsiteDesignSystem | None:
    """Launch headless Chromium, sample computed styles, return the 8-field
    design system.

    Returns ``None`` (the FALLBACK SENTINEL) on any error OR when confidence is
    below threshold — the caller (P5-02) then surfaces the manual color-picker
    floor. Never raises to the caller: a flaky public site must not fail the
    whole generation request. The browser is disposed per request (no pool) and
    is ALWAYS closed, including on the error path.
    """
    # HOST only — a full URL can carry query-string PII (Rule #24).
    host = urlsplit(url).hostname or ""
    logger.info("website_extract_started url_host=%s", host)

    confident = False
    error_class = ""
    reason = "ok"
    try:
        # MOVED INSIDE the try (P6-09; was before the try): a missing/broken
        # Playwright now raises ImportError HERE, caught below + routed through
        # the finally so the floor is OBSERVABLE. Before: the ImportError escaped
        # to the caller's `except ImportError` -> silent neutral floor with NO
        # website_extract_complete line. The floor OUTPUT (return None) is
        # unchanged — only the observability is.
        # SSRF guard: reject non-public / non-http(s) website URLs before we
        # spin up Chromium and navigate. A blocked URL floors to None (the
        # FALLBACK SENTINEL) like any other failure, with reason=blocked_url so
        # it is distinguishable in logs. Note: this validates the host as
        # supplied; the headless browser still follows redirects internally,
        # but the only style values we ever return are short non-content
        # strings, never response bodies.
        try:
            assert_public_url(url)
        except UnsafeURLError:
            reason = "blocked_url"
            return None
        async_playwright = _resolve_async_playwright()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
            context = await browser.new_context()
            try:
                page = await context.new_page()
                # K1 fix: `wait_until="load"` (not `networkidle`). Real sites with
                # persistent connections (analytics/sockets/streaming) never reach
                # networkidle → 100% TimeoutError even at 8s/20s; `load` succeeds in
                # ~0.85s and pulls richer HTML. Paired with the `_is_timeout`/`reason=`
                # observability so the floor is debuggable from logs.
                await page.goto(url, wait_until="load", timeout=_NAV_TIMEOUT_MS)
                await _dismiss_cookie_banner(page)
                raw = await page.evaluate(_SAMPLER_JS)
                ds = _map_sample(raw)
                if _below_confidence(ds):
                    reason = "low_confidence"
                    return None
                confident = True
                return ds
            finally:
                # Dispose per request even on the error path (no browser pool).
                await context.close()
                await browser.close()
    except ImportError as exc:
        # Narrow clause MUST precede `except Exception` (ImportError is an
        # Exception subclass; Python evaluates except clauses top-down). A
        # missing/broken Playwright dependency floors loudly here.
        error_class = type(exc).__name__
        reason = "import_unavailable"
        return None
    except Exception as exc:  # noqa: BLE001 — a flaky site must never propagate.
        error_class = type(exc).__name__
        reason = "timeout" if _is_timeout(exc) else "error"
        return None
    finally:
        logger.info(
            "website_extract_complete url_host=%s confident=%s reason=%s error_class=%s",
            host, confident, reason, error_class,
        )

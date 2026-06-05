// P6-12 (UX-2) — structural/behaviour tests for the device-framed viewer.
//
// Node-env vitest (no DOM, no router, no @testing-library), so — following the
// CompletionBar / PostGenerationResult / design-agent-css convention — we
// SSR-render the pure view via renderToStaticMarkup to assert rendered markup,
// drive the desktop↔mobile swap through the `initialPlatform` test seam (a real
// toggle click needs a browser → AC9 tester lane), and read design-agent.css
// from disk for the CSS structural assertions. No `git show <rev>` / `git diff
// <sha>` (CI shallow-clone lacks historical objects) — working-tree invariants.
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses the
// classic runtime, so expose React globally (repo test convention).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  PrototypeViewer,
  stageClass,
  DEFAULT_URL_SLUG,
  type Platform,
} from "../PrototypeViewer"
import { PostGenerationResultView } from "../PostGenerationResult"
import { CompletionBar } from "../CompletionBar"
import { CommentsPanel } from "../CommentsPanel"
import { ManualEditOverlay } from "../ManualEditOverlay"

const BUNDLE = "https://cdn/x/bundle/index.html"

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS_PATH = join(HERE, "..", "design-agent.css")
const CSS = readFileSync(CSS_PATH, "utf8")

function renderViewer(
  over: {
    chrome?: React.ReactNode
    urlSlug?: string
    initialPlatform?: Platform
    isComplete?: boolean
  } = {},
): string {
  return renderToStaticMarkup(
    React.createElement(PrototypeViewer, {
      bundleUrl: BUNDLE,
      isComplete: over.isComplete ?? false,
      chrome: over.chrome,
      urlSlug: over.urlSlug,
      initialPlatform: over.initialPlatform,
    }),
  )
}

/** Extract the single `<iframe …>` opening tag from rendered markup. */
function iframeTag(html: string): string {
  const m = html.match(/<iframe[^>]*>/)
  expect(m).not.toBeNull()
  return m![0]
}

// ── AC1: browser-frame chrome renders ─────────────────────────────────────
describe("browser-frame chrome (AC1)", () => {
  it("test_renders_browser_frame_chrome — proto-frame + head + three dots + url bar", () => {
    const html = renderViewer()
    expect(html).toContain('class="proto-frame"')
    expect(html).toContain('class="proto-frame-head"')
    expect(html).toContain('class="proto-dot r"')
    expect(html).toContain('class="proto-dot y"')
    expect(html).toContain('class="proto-dot g"')
    expect(html).toContain('class="proto-url"')
    // the platform toggle's two buttons are present
    expect(html).toContain("Desktop")
    expect(html).toContain("Mobile")
  })

  it("test_url_bar_shows_slug_then_default — slug prop wins; default fills when omitted", () => {
    const withSlug = renderViewer({
      urlSlug: "sprntly.com/plotline/call-sheet-builder",
    })
    expect(withSlug).toContain(
      ">sprntly.com/plotline/call-sheet-builder</span>",
    )
    const withDefault = renderViewer()
    expect(withDefault).toContain(`>${DEFAULT_URL_SLUG}</span>`)
    expect(DEFAULT_URL_SLUG).toBe("sprntly.com/preview")
  })
})

// ── AC4: chrome slot intact + queryable descendant of the viewer ──────────
describe("chrome slot (AC4)", () => {
  it("test_chrome_slot_renders_passed_node — passed chrome appears in da-prototype-chrome; slot present when undefined", () => {
    const sentinel = React.createElement(
      "div",
      { "data-testid": "sentinel-chrome" },
      "CHROME",
    )
    const html = renderViewer({ chrome: sentinel })
    expect(html).toContain('class="da-prototype-chrome"')
    expect(html).toContain('data-testid="prototype-chrome"')
    expect(html).toContain('data-testid="sentinel-chrome"')
    // slot still rendered when chrome is undefined
    const bare = renderViewer()
    expect(bare).toContain('data-testid="prototype-chrome"')
  })

  it("test_chrome_slot_is_descendant_of_viewer — chrome nests inside the viewer; exactly one iframe", () => {
    const html = renderViewer({
      chrome: React.createElement("span", { "data-testid": "sentinel-chrome" }),
    })
    const viewerIdx = html.indexOf("da-prototype-viewer")
    const chromeIdx = html.indexOf("da-prototype-chrome")
    expect(viewerIdx).toBeGreaterThanOrEqual(0)
    // chrome slot appears AFTER (inside) the viewer root
    expect(chromeIdx).toBeGreaterThan(viewerIdx)
    // exactly one da-prototype-iframe survives the rewrite (mount-test contract)
    const count = (html.match(/da-prototype-iframe/g) ?? []).length
    expect(count).toBe(1)
  })
})

// ── AC2: Desktop/Mobile toggle swaps the stage class ──────────────────────
describe("platform toggle (AC2)", () => {
  it("test_stage_class_pure_mapping — stageClass maps each platform", () => {
    expect(stageClass("desktop")).toBe("proto-stage desktop")
    expect(stageClass("mobile")).toBe("proto-stage mobile")
  })

  it("test_default_stage_is_desktop — default render is desktop + Desktop button active", () => {
    const html = renderViewer()
    expect(html).toContain('class="proto-stage desktop"')
    expect(html).not.toContain('class="proto-stage mobile"')
    // Desktop button is active + aria-pressed true; Mobile is not
    expect(html).toMatch(
      /<button[^>]*class="active"[^>]*aria-pressed="true"[^>]*>Desktop<\/button>/,
    )
    expect(html).toMatch(
      /<button[^>]*class=""[^>]*aria-pressed="false"[^>]*>Mobile<\/button>/,
    )
  })

  it("test_toggle_swaps_stage_to_mobile — mobile state yields proto-stage mobile + Mobile active", () => {
    // Drive the swap through the injectable seam (a real click needs a browser).
    const html = renderViewer({ initialPlatform: "mobile" })
    expect(html).toContain('class="proto-stage mobile"')
    expect(html).not.toContain('class="proto-stage desktop"')
    expect(html).toMatch(
      /<button[^>]*class="active"[^>]*aria-pressed="true"[^>]*>Mobile<\/button>/,
    )
    expect(html).toMatch(
      /<button[^>]*class=""[^>]*aria-pressed="false"[^>]*>Desktop<\/button>/,
    )
    // and back to desktop
    const back = renderViewer({ initialPlatform: "desktop" })
    expect(back).toContain('class="proto-stage desktop"')
  })
})

// ── AC3: iframe attributes unchanged + not re-mounted on toggle ────────────
describe("iframe contract (AC3)", () => {
  it("test_iframe_attributes_unchanged — className/src/title fixed; sandbox carried from baseline", () => {
    const tag = iframeTag(renderViewer())
    expect(tag).toContain('class="da-prototype-iframe"')
    expect(tag).toContain(`src="${BUNDLE}"`)
    expect(tag).toContain('title="Generated prototype"')
    // sandbox now carries allow-forms (landed by P6-17 / UX-7) alongside the
    // baseline scripts+same-origin — seam-fill: P6-12 wrote this assertion at the
    // 2-token state anticipating P6-17's third token. Assert the full token set,
    // NOT a byte-exact string (order-independent).
    const sandbox = tag.match(/sandbox="([^"]*)"/)
    expect(sandbox).not.toBeNull()
    const tokens = sandbox![1].split(/\s+/).filter(Boolean).sort()
    expect(tokens).toEqual([
      "allow-forms",
      "allow-same-origin",
      "allow-scripts",
    ])
  })

  it("test_iframe_not_remounted_on_toggle — iframe tag identical across desktop/mobile", () => {
    // Single element; the toggle only swaps the stage wrapper class, so the
    // iframe markup (src/className/sandbox/title) is invariant to platform.
    const desktop = iframeTag(renderViewer({ initialPlatform: "desktop" }))
    const mobile = iframeTag(renderViewer({ initialPlatform: "mobile" }))
    expect(mobile).toBe(desktop)
  })
})

// ── P6-17 (UX-7) Regression: sandbox grants allow-forms, still blocks parent nav ──
// Fix ticket → opens with a Regression category (TICKET_STANDARD §2). These fail on
// unfixed code (2d6a416 sandbox = "allow-scripts allow-same-origin", no allow-forms).
// Node-env vitest cannot execute iframe sandbox semantics — we assert the rendered
// `sandbox` token STRING only; real form-submit + blocked-parent-nav is tester-verified
// in a browser (AC4).
describe("sandbox allow-forms regression (P6-17 / UX-7)", () => {
  it("test_iframe_sandbox_allows_forms — AC1: sandbox contains allow-forms", () => {
    const tag = iframeTag(renderViewer())
    const sandbox = tag.match(/sandbox="([^"]*)"/)
    expect(sandbox).not.toBeNull()
    const tokens = sandbox![1].split(/\s+/).filter(Boolean)
    expect(tokens).toContain("allow-forms")
  })

  it("test_iframe_sandbox_blocks_parent_navigation — AC2: no parent-nav / popup tokens", () => {
    const tag = iframeTag(renderViewer())
    const sandbox = tag.match(/sandbox="([^"]*)"/)![1]
    expect(sandbox).not.toContain("allow-top-navigation")
    expect(sandbox).not.toContain("allow-top-navigation-by-user-activation")
    expect(sandbox).not.toContain("allow-popups")
  })

  it("test_iframe_other_attributes_unchanged — AC3: class/src/title fixed; only sandbox gained allow-forms", () => {
    const tag = iframeTag(renderViewer())
    expect(tag).toContain('class="da-prototype-iframe"')
    expect(tag).toContain(`src="${BUNDLE}"`)
    expect(tag).toContain('title="Generated prototype"')
    const tokens = tag
      .match(/sandbox="([^"]*)"/)![1]
      .split(/\s+/)
      .filter(Boolean)
      .sort()
    expect(tokens).toEqual([
      "allow-forms",
      "allow-same-origin",
      "allow-scripts",
    ])
  })
})

// ── AC5: both call sites mount the viewer with their chrome ────────────────
describe("call-site propagation (AC5)", () => {
  it("test_post_generation_mounts_viewer_with_chrome — signed-in mounts proto-frame + ManualEditOverlay", () => {
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResultView, {
        prototypeId: 42,
        isComplete: false,
        shareMode: "private" as const,
        shareToken: null,
        bundleUrl: BUNDLE,
      }),
    )
    expect(html).toContain('class="proto-frame"')
    expect(html).toContain('class="da-prototype-iframe"')
    // the editable overlay (numeric prototypeId) mounts in the chrome slot
    expect(html).toContain('data-testid="manual-edit-overlay"')
    // default slug fills (signed-in call site passes no urlSlug)
    expect(html).toContain(`>${DEFAULT_URL_SLUG}</span>`)
  })

  it("test_public_viewer_mounts_viewer_with_chrome — public read-only chrome fragment wraps inside proto-frame", () => {
    // PublicTokenViewer itself is hook-driven (useParams/useEffect) → not
    // SSR-renderable in node-env; render the SAME ready-state fragment it mounts
    // (read-only CompletionBar + CommentsPanel + inert ManualEditOverlay) through
    // PrototypeViewer to prove the device frame wraps it.
    const html = renderToStaticMarkup(
      React.createElement(PrototypeViewer, {
        bundleUrl: BUNDLE,
        isComplete: true,
        chrome: React.createElement(
          React.Fragment,
          null,
          React.createElement(CompletionBar, {
            isComplete: true,
            editable: false,
          }),
          React.createElement(CommentsPanel, { token: "tok-123" }),
          React.createElement(ManualEditOverlay, { isComplete: true }),
        ),
      }),
    )
    expect(html).toContain('class="proto-frame"')
    expect(html).toContain('data-testid="prototype-chrome"')
    // read-only completion bar present; inert overlay (no prototypeId) renders
    // nothing — so no edit toggle leaks onto the public surface
    expect(html).toContain("completion-bar")
    expect(html).not.toContain('data-testid="manual-edit-toggle"')
  })
})

// ── AC6: device-frame CSS appended + scoped (focused; P6-11's suite covers the
//        global scoping + no-new-palette invariants over the whole file) ─────
describe("device-frame CSS (AC6)", () => {
  it("test_css_device_frame_block_appended_and_scoped — scoped frame selectors present; P6-11 share-menu intact", () => {
    for (const sel of [
      ".proto-frame",
      ".proto-frame-head",
      ".proto-url",
      ".platform-toggle",
      ".proto-stage",
    ]) {
      const re = new RegExp(
        `\\.design-agent-surface[^{]*\\${sel.replace(/-/g, "\\-")}[\\s.,{:]`,
      )
      expect(CSS).toMatch(re)
    }
    // stage desktop/mobile variants exist
    expect(CSS).toMatch(/\.proto-stage\.desktop/)
    expect(CSS).toMatch(/\.proto-stage\.mobile/)
    // the device-frame block adds NO new colour literal (bezel uses var(--ink));
    // the three P6-11 dot hexes are the only hexes in the file.
    const stripped = CSS.replace(/\/\*[\s\S]*?\*\//g, "")
    const protoStageBlock = stripped.match(
      /\.proto-stage\.mobile\s+\.da-prototype-iframe\s*\{([^}]*)\}/,
    )
    expect(protoStageBlock).not.toBeNull()
    expect(protoStageBlock![1]).not.toMatch(/#[0-9a-fA-F]{3,8}/)
    expect(protoStageBlock![1]).toMatch(/border:\s*9px solid var\(--ink\)/)
    // P6-11's .share-menu override is untouched (no P6-11 lines removed)
    expect(CSS).toMatch(/\.design-agent-surface\s+\.share-menu\s*\{/)
  })
})

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
    hideChrome?: boolean
    hideToggle?: boolean
    showDesktop?: boolean
    showMobile?: boolean
  } = {},
): string {
  return renderToStaticMarkup(
    React.createElement(PrototypeViewer, {
      bundleUrl: BUNDLE,
      isComplete: over.isComplete ?? false,
      chrome: over.chrome,
      urlSlug: over.urlSlug,
      initialPlatform: over.initialPlatform,
      hideChrome: over.hideChrome,
      hideToggle: over.hideToggle,
      showDesktop: over.showDesktop,
      showMobile: over.showMobile,
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

// ── C2a: optional headControls render in the frame head (additive) ─────────
describe("head controls (C2a)", () => {
  it("test_head_controls_render_in_frame_head — passed headControls appear inside proto-frame-head", () => {
    const controls = React.createElement(
      "button",
      { "data-testid": "sentinel-head-control", type: "button" },
      "Comment",
    )
    const html = renderToStaticMarkup(
      React.createElement(PrototypeViewer, {
        bundleUrl: BUNDLE,
        isComplete: false,
        headControls: controls,
      }),
    )
    expect(html).toContain('class="proto-head-controls"')
    expect(html).toContain('data-testid="sentinel-head-control"')
    // the control nests inside the frame head (after it opens, before the chrome slot)
    const headIdx = html.indexOf("proto-frame-head")
    const ctrlIdx = html.indexOf("sentinel-head-control")
    const chromeIdx = html.indexOf("da-prototype-chrome")
    expect(headIdx).toBeGreaterThanOrEqual(0)
    expect(ctrlIdx).toBeGreaterThan(headIdx)
    expect(ctrlIdx).toBeLessThan(chromeIdx)
  })

  it("test_head_controls_absent_is_byte_for_byte_unchanged — omitting headControls renders nothing extra (signed-in non-regression)", () => {
    const without = renderViewer()
    // no wrapper, no leak — the signed-in PostGenerationResult passes no headControls.
    expect(without).not.toContain("proto-head-controls")
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

// ── single-device prototypes suppress the in-frame toggle ──────────────────
// A prototype targeting one form factor has nothing to toggle to, so the
// Desktop/Mobile group is hidden (mirroring DaControlBar). The stage class still
// tracks the initial platform, so the canvas width stays correct.
describe("single-device toggle suppression", () => {
  it("hides the toggle group for a mobile-only prototype and stages mobile", () => {
    const html = renderViewer({
      showDesktop: false,
      showMobile: true,
      initialPlatform: "mobile",
    })
    // The toggle group (and its buttons) are absent — there is no second device.
    expect(html).not.toContain('class="platform-toggle"')
    expect(html).not.toContain('aria-label="Preview platform"')
    // …but the stage still reflects the single device, so width is correct.
    expect(html).toContain('class="proto-stage mobile"')
  })

  it("hides the toggle group for a desktop-only prototype and stages desktop", () => {
    const html = renderViewer({
      showDesktop: true,
      showMobile: false,
      initialPlatform: "desktop",
    })
    expect(html).not.toContain('class="platform-toggle"')
    expect(html).not.toContain('aria-label="Preview platform"')
    expect(html).toContain('class="proto-stage desktop"')
  })

  it("keeps both toggle buttons when both devices apply (default / legacy)", () => {
    // Defaults are both-true, so every existing caller is unchanged.
    const html = renderViewer()
    expect(html).toContain('class="platform-toggle"')
    expect(html).toContain("Desktop")
    expect(html).toContain("Mobile")
  })

  it("with hideChrome AND a single device, the frame head disappears entirely (fullscreen edge-to-edge)", () => {
    // Fullscreen mobile-only: decoration suppressed by hideChrome, toggle
    // suppressed by the single device → no empty chrome bar remains.
    const html = renderViewer({
      hideChrome: true,
      showDesktop: false,
      showMobile: true,
      initialPlatform: "mobile",
    })
    expect(html).not.toContain('class="proto-frame-head"')
    expect(html).toContain('class="proto-stage mobile"')
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
  it("test_post_generation_mounts_viewer_with_chrome — signed-in mounts proto-frame + inline iframe", () => {
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
    // ManualEditOverlay trigger not rendered on the canvas — mark-and-comment is the path.
    expect(html).not.toContain('data-testid="manual-edit-overlay"')
    // Edge-to-edge signed-in editor preview: the cosmetic browser-frame
    // decoration is suppressed (`hideChrome`), so the URL bar + traffic lights
    // are NOT rendered. The Desktop/Mobile toggle lives in the top control bar.
    expect(html).not.toContain(`>${DEFAULT_URL_SLUG}</span>`)
    expect(html).not.toContain('data-testid="proto-url"')
    expect(html).not.toContain('class="proto-dot r"')
    // the toggle still reaches the user — lifted into the control bar.
    expect(html).toContain('class="platform-toggle da-controlbar-platform"')
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

// ── hideChrome: edge-to-edge signed-in editor preview ─────────────────────
// The signed-in non-fullscreen editor preview suppresses the cosmetic browser-
// frame decoration (traffic-light dots + URL bar) so the iframe is edge-to-edge.
// The functional Desktop/Mobile toggle survives. Public + fullscreen keep the head.
describe("hideChrome edge-to-edge preview", () => {
  it("test_hide_chrome_drops_traffic_lights_and_url_bar — decoration gone; iframe + toggle still render", () => {
    // The signed-in editor lifts the toggle into the control bar (`hideToggle`),
    // so with `hideChrome` the head disappears entirely and the iframe is flush.
    const html = renderViewer({ hideChrome: true, hideToggle: true })
    // cosmetic decoration is gone
    expect(html).not.toContain("proto-frame-head")
    expect(html).not.toContain('class="proto-dot r"')
    expect(html).not.toContain('class="proto-dot y"')
    expect(html).not.toContain('class="proto-dot g"')
    expect(html).not.toContain('data-testid="proto-url"')
    expect(html).not.toContain(DEFAULT_URL_SLUG)
    // the iframe is still rendered (exactly one)
    expect(html).toContain('class="da-prototype-iframe"')
    expect((html.match(/da-prototype-iframe/g) ?? []).length).toBe(1)
  })

  it("test_hide_chrome_keeps_toggle — decoration gone but the in-frame Desktop/Mobile toggle survives", () => {
    // `hideChrome` suppresses ONLY the decoration. A caller that keeps its
    // in-frame toggle (hideToggle absent) still renders it — proving the toggle
    // is not coupled to the cosmetic head.
    const html = renderViewer({ hideChrome: true })
    // decoration suppressed
    expect(html).not.toContain('class="proto-dot r"')
    expect(html).not.toContain('data-testid="proto-url"')
    // functional toggle survives
    expect(html).toContain('class="platform-toggle"')
    expect(html).toContain("Desktop")
    expect(html).toContain("Mobile")
    expect(html).toContain('aria-label="Preview platform"')
    // and the toggle still functions — driving the seam flips the stage class
    const mobile = renderViewer({ hideChrome: true, initialPlatform: "mobile" })
    expect(mobile).toContain('class="proto-stage mobile"')
    expect(mobile).toMatch(
      /<button[^>]*class="active"[^>]*aria-pressed="true"[^>]*>Mobile<\/button>/,
    )
    const desktop = renderViewer({
      hideChrome: true,
      initialPlatform: "desktop",
    })
    expect(desktop).toContain('class="proto-stage desktop"')
  })

  it("test_default_preserves_head — default (public + fullscreen path) keeps the full chrome (regression guard)", () => {
    // hideChrome absent/falsy — the head, traffic lights and URL bar are all
    // present exactly as before. This is the RED guard: it proves the change is
    // non-vacuous (the head exists by default and only `hideChrome` removes it).
    const html = renderViewer()
    expect(html).toContain('class="proto-frame-head"')
    expect(html).toContain('class="proto-dot r"')
    expect(html).toContain('class="proto-dot y"')
    expect(html).toContain('class="proto-dot g"')
    expect(html).toContain('data-testid="proto-url"')
    expect(html).toContain(DEFAULT_URL_SLUG)
    // toggle present by default too
    expect(html).toContain("Desktop")
    expect(html).toContain("Mobile")
  })
})

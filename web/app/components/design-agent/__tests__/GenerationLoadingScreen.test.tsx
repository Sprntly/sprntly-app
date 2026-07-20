// Structural + source tests for the full-screen generation loading screen.
//
// Node-env vitest (no DOM, no router, no @testing-library): SSR-render the pure
// view via renderToStaticMarkup to assert the wrapper classes + structure, and
// read design-agent.css / the component source from disk for the CSS-scoping,
// palette, and marker-free invariants (mirroring design-agent-css.test.tsx).
// The cosmetic step/progress timer only advances client-side, so the SSR frame
// deterministically shows the static placeholder steps. Working-tree content
// invariants only — never `git show <rev>` (CI shallow clone lacks history).
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses
// the classic runtime, so expose React globally (repo test convention).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { GenerationLoadingScreen } from "../GenerationLoadingScreen"

const HERE = dirname(fileURLToPath(import.meta.url))
// __tests__ → design-agent
const CSS_PATH = join(HERE, "..", "design-agent.css")
const COMPONENT_PATH = join(HERE, "..", "GenerationLoadingScreen.tsx")
const CSS = readFileSync(CSS_PATH, "utf8")
const COMPONENT_SRC = readFileSync(COMPONENT_PATH, "utf8")

// The static placeholder step labels the component renders (cosmetic; no live
// events). Mirrors the STEPS array in the component source.
const STEP_LABELS = [
  "Reading the PRD",
  "Analyzing the design source",
  "Planning the layout",
  "Composing components",
  "Wiring interactions",
  "Accessibility pass",
  "Rendering preview",
]

function stripCssComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "")
}

/** The generation-loading-overlay delimited block, sliced between its start/end
 *  delimiter comments so the palette/marker checks see ONLY this block (not the
 *  sibling canvas-overlay block that follows it). */
function overlayBlock(css: string): string {
  const start = css.indexOf("/* === generation loading overlay (start)")
  const end = css.indexOf("/* === generation loading overlay (end)")
  expect(start).toBeGreaterThanOrEqual(0)
  expect(end).toBeGreaterThan(start)
  return css.slice(start, end)
}

/** Part D — the notify-when-ready promotion delimited block, appended AFTER
 *  the generation-loading-overlay block's own end delimiter (its own
 *  start/end pair), sliced the same way. */
function notifyPromotionBlock(css: string): string {
  const start = css.indexOf("/* === notify-when-ready promotion (start)")
  const end = css.indexOf("/* === notify-when-ready promotion (end)")
  expect(start).toBeGreaterThanOrEqual(0)
  expect(end).toBeGreaterThan(start)
  return css.slice(start, end)
}

describe("GenerationLoadingScreen render", () => {
  it("test_loading_screen_closed_renders_null — open=false renders nothing", () => {
    const html = renderToStaticMarkup(
      React.createElement(GenerationLoadingScreen, { open: false }),
    )
    expect(html).toBe("")
  })

  it("test_loading_screen_open_renders_overlay — open=true renders the dual-class overlay + its parts", () => {
    const html = renderToStaticMarkup(
      React.createElement(GenerationLoadingScreen, { open: true }),
    )
    // root carries BOTH scope classes (compound scope: design-agent-surface +
    // proto-gen-overlay on the same element).
    expect(html).toMatch(
      /<div[^>]*class="proto-gen-overlay design-agent-surface"/,
    )
    // orb, headline + blinking cursor, status, progress bar, steps checklist.
    expect(html).toContain("proto-gen-orb")
    expect(html).toContain("proto-gen-h")
    expect(html).toContain("thinking-cursor")
    expect(html).toContain("proto-gen-s")
    expect(html).toContain("proto-gen-progress")
    expect(html).toContain("proto-gen-steps")
  })

  it("test_loading_screen_renders_static_steps_placeholder — the steps are the static cosmetic labels, last keeps its spinner", () => {
    const html = renderToStaticMarkup(
      React.createElement(GenerationLoadingScreen, { open: true }),
    )
    for (const label of STEP_LABELS) {
      expect(html).toContain(label)
    }
    // No step is marked done in the initial frame (doneCount never reaches
    // STEPS.length while waiting) → a spinner is present.
    expect(html).toContain("spin")
  })
})

describe("proto-gen-overlay CSS scoping (own lane)", () => {
  it("test_proto_gen_overlay_selectors_scoped — every proto-gen-overlay selector line begins with .design-agent-surface", () => {
    const selectorLines = stripCssComments(CSS)
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l.length > 0 && !l.startsWith("@"))
      .filter((l) => l.endsWith("{") || l.endsWith(","))
      .filter((l) => l.includes("proto-gen-overlay"))
    // sanity: the block is actually present
    expect(selectorLines.length).toBeGreaterThan(0)
    const offenders = selectorLines.filter(
      (l) => !l.startsWith(".design-agent-surface"),
    )
    expect(offenders).toEqual([])
  })

  it("test_proto_gen_overlay_no_value_line_ends_in_comma — no non-selector line in the block ends in a comma", () => {
    const offenders = stripCssComments(overlayBlock(CSS))
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l.length > 0 && !l.startsWith("@"))
      .filter((l) => l.endsWith(","))
      // selector lines (which legitimately end in `,`) are all scoped; anything
      // else ending in `,` would be an unscoped value-continuation.
      .filter((l) => !l.startsWith(".design-agent-surface"))
    expect(offenders).toEqual([])
  })

  it("test_proto_gen_overlay_palette_clean — only var(--…)/keywords, no rgb()/hsl()/hex literal", () => {
    const stripped = stripCssComments(overlayBlock(CSS))
    expect(stripped).not.toMatch(/rgba?\(/)
    expect(stripped).not.toMatch(/hsla?\(/)
    expect(stripped).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
  })
})

describe("no throwaway markers (regression)", () => {
  it("the component source carries no throwaway marker (test_loading_screen_source_durable)", () => {
    expect(COMPONENT_SRC).not.toContain("UX-EXPLORE")
  })

  it("the overlay CSS block carries no throwaway marker (test_overlay_css_block_durable)", () => {
    expect(overlayBlock(CSS)).not.toContain("UX-EXPLORE")
  })
})

// ─── Part D: "Notify me when ready" promotion (Treatment B) ──────────────

describe("notify-when-ready promotion footer (Part D, AC15)", () => {
  it("test_generation_loading_screen_default_footer_structure — default (non-armed) state shows the microcopy + full-width primary button", () => {
    const html = renderToStaticMarkup(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        onNotifyWhenReady: () => {},
      }),
    )
    expect(html).toContain("This takes a few minutes")
    expect(html).toContain("Notify me when ready")
    // proto-gen-notify-btn present WITHOUT btn-ghost in the same class attr.
    const btnMatch = html.match(/class="([^"]*proto-gen-notify-btn[^"]*)"/)
    expect(btnMatch).not.toBeNull()
    expect(btnMatch![1]).not.toContain("btn-ghost")
    expect(btnMatch![1]).toContain("btn-primary")
    // Not yet armed — no confirmation block.
    expect(html).not.toContain("proto-gen-notify-armed")
  })
})

describe("cancel-only footer unchanged (Part D, AC16)", () => {
  it("test_generation_loading_screen_cancel_only_footer_unchanged — onCancel-only (no onNotifyWhenReady) footer is byte-identical to today", () => {
    const withOnlyCancel = renderToStaticMarkup(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        onCancel: () => {},
      }),
    )
    // The plain (unmodified) .proto-gen-footer row — no --stacked modifier,
    // no notify markup at all.
    expect(withOnlyCancel).toMatch(/class="proto-gen-footer"/)
    expect(withOnlyCancel).not.toContain("proto-gen-footer--stacked")
    expect(withOnlyCancel).not.toContain("proto-gen-notify-block")
    expect(withOnlyCancel).not.toContain("proto-gen-notify-armed")
    expect(withOnlyCancel).not.toContain("Notify me when ready")
    expect(withOnlyCancel).toContain("proto-gen-cancel-btn")
  })
})

describe("Back to Briefs link uses the routes constant (Part D, AC17)", () => {
  it("test_generation_loading_screen_back_to_briefs_uses_screen_path_constant — imports SCREEN_PATH, href equals SCREEN_PATH.ideation, no hardcoded literal", () => {
    expect(COMPONENT_SRC).toContain('import { SCREEN_PATH } from "../../lib/routes"')
    expect(COMPONENT_SRC).toContain("href={SCREEN_PATH.ideation}")
    // The literal "/ideation" string does not appear hardcoded outside the
    // routes.ts import chain (this file never spells it out itself).
    expect(COMPONENT_SRC).not.toContain('"/ideation"')
  })
})

describe("no new icon import (Part D, AC18)", () => {
  it("test_generation_loading_screen_no_new_icon_import — imports IconArrowRight from app-icons, no IconBell", () => {
    expect(COMPONENT_SRC).toContain(
      'import { IconArrowRight } from "../shared/app-icons"',
    )
    expect(COMPONENT_SRC).not.toContain("IconBell")
  })
})

describe("no timer scheduled by handleNotifyClick (Part D, AC22)", () => {
  it("test_handle_notify_click_schedules_no_timer — source-scan: no setTimeout inside handleNotifyClick's body", () => {
    const start = COMPONENT_SRC.indexOf("const handleNotifyClick = ()")
    expect(start).toBeGreaterThan(0)
    // handleNotifyClick is a short arrow function; its body ends at the next
    // top-level `}` on its own line before the next const/function decl.
    const end = COMPONENT_SRC.indexOf("\n  }", start)
    const body = COMPONENT_SRC.slice(start, end)
    expect(body).not.toContain("setTimeout")
  })
})

describe("notify-when-ready promotion CSS (Part D, AC23/AC24)", () => {
  it("test_notify_promotion_css_palette_clean — only var(--…)/keywords, no rgb()/hsl()/hex literal", () => {
    const stripped = stripCssComments(notifyPromotionBlock(CSS))
    expect(stripped).not.toMatch(/rgba?\(/)
    expect(stripped).not.toMatch(/hsla?\(/)
    expect(stripped).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
  })

  it("test_notify_promotion_css_selectors_scoped — every new selector line begins with .design-agent-surface", () => {
    const selectorLines = stripCssComments(notifyPromotionBlock(CSS))
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l.length > 0 && !l.startsWith("@"))
      .filter((l) => l.endsWith("{") || l.endsWith(","))
    expect(selectorLines.length).toBeGreaterThan(0)
    const offenders = selectorLines.filter(
      (l) => !l.startsWith(".design-agent-surface"),
    )
    expect(offenders).toEqual([])
  })
})

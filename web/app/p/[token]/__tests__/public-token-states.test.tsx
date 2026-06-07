// P6-18 (UX-8) — public /p/<token> unstyled states.
//
// The public share viewer's three TRANSIENT states (loading / error /
// passcode-gate) are EARLY returns that fire BEFORE PublicTokenViewer's main
// `<PrototypeViewer>` return — so P6-11's `design-agent-surface` wrapper (which
// only wraps the main return) does NOT reach them. This fix (a) appends the
// passcode-gate rule blocks to the P6-11-owned scoped stylesheet (they had ZERO
// rules before — an unstyled flash for a first-time recipient) and (b) ensures
// every transient root carries `design-agent-surface` so the scoped rules
// resolve. At release HEAD a sibling ticket had already added the wrapper to the
// loading + error roots; this ticket adds it to the passcode-gate form.
//
// Node-env vitest (no DOM, no router, no testing-library — Check 25): CSS-rule
// existence is asserted by reading design-agent.css from disk; wrapper scope is
// asserted as a working-tree content invariant on the shipped JSX (NO historical
// git rev — CI shallow-clone safe) plus a live renderToStaticMarkup of the
// presentational passcode form and the loading initial state.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, beforeAll, vi } from "vitest"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, resolve } from "node:path"

// Classic-runtime transform: expose React globally (the page.test.tsx /
// DesignAgentDrawer convention) rather than touch the shared vitest config.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { PasscodeGateView } from "../PasscodeGate"
import { submitPasscode } from "../PasscodeGate"
import { nextViewerState } from "../PublicTokenViewer"

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS_PATH = resolve(HERE, "../../../components/design-agent/design-agent.css")
const PUBLIC_VIEWER_PATH = resolve(HERE, "../PublicTokenViewer.tsx")
const PASSCODE_GATE_PATH = resolve(HERE, "../PasscodeGate.tsx")

let css = ""
let publicViewerSrc = ""
let passcodeGateSrc = ""
beforeAll(() => {
  css = readFileSync(CSS_PATH, "utf8")
  publicViewerSrc = readFileSync(PUBLIC_VIEWER_PATH, "utf8")
  passcodeGateSrc = readFileSync(PASSCODE_GATE_PATH, "utf8")
})

// Strip CSS block comments so class names mentioned in prose don't get parsed
// as selectors.
function stripCssComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "")
}

// Count the BASE rule blocks for a class: blocks where some comma-separated
// selector segment ENDS in `.<class>` (i.e. the base rule), excluding pseudo-
// class variants (`:focus`/`:hover`/`:disabled`) which legitimately share the
// class but are not competing duplicates. A comma-separated dual selector
// (`.x.cls, .x .cls { … }`) is ONE block, the right granularity for AC6.
function baseRuleBlocksFor(source: string, className: string): number {
  let count = 0
  for (const block of stripCssComments(source).split("}")) {
    const brace = block.indexOf("{")
    if (brace === -1) continue
    const selector = block.slice(0, brace)
    const defines = selector
      .split(",")
      .some((seg) => seg.trim().endsWith(`.${className}`))
    if (defines) count++
  }
  return count
}

const PASSCODE_CLASSES = [
  "da-passcode-gate",
  "da-passcode-label",
  "da-passcode-input",
  "da-passcode-error",
  "da-passcode-submit",
]

describe("P6-18 regression (fails on unfixed code)", () => {
  // AC3 — the passcode-gate classes had ZERO matching rules before this fix.
  it("test_passcode_gate_states_have_rules: design-agent.css defines a scoped rule for every passcode-gate class", () => {
    for (const cls of PASSCODE_CLASSES) {
      expect(css).toContain(`.design-agent-surface .${cls}`)
    }
  })

  // AC4 — all three transient roots are wrapped in a design-agent-surface scope.
  // Working-tree content invariant on the shipped JSX (no historical git rev).
  it("test_public_states_under_surface_scope: loading / error / passcode-gate roots all carry design-agent-surface", () => {
    // Loading + error early-return roots in PublicTokenViewer.
    expect(publicViewerSrc).toMatch(
      /className="design-agent-surface da-public-loading"/,
    )
    expect(publicViewerSrc).toMatch(
      /className="design-agent-surface da-public-error"/,
    )
    // Passcode-gate form root in PasscodeGate (the root this ticket wrapped).
    expect(passcodeGateSrc).toMatch(
      /className="design-agent-surface da-passcode-gate"/,
    )
  })
})

describe("P6-18 state rendering + rules", () => {
  // AC1 — loading branch renders the class under the wrapper; the rule exists.
  // `loading` is PublicTokenViewer's INITIAL state, so it renders directly via
  // SSR (effects don't run in renderToStaticMarkup). The error branch needs the
  // async resolver effect to fire, which SSR can't trigger — its wrapper scope
  // is covered by the content-invariant regression above + the rule check here.
  it("test_loading_state_class_and_rule: loading root renders under the surface scope and a rule exists", async () => {
    vi.resetModules()
    vi.doMock("next/navigation", () => ({
      useParams: () => ({ token: "tok" }),
      notFound: () => {
        throw new Error("notFound() should not run for the loading state")
      },
    }))
    const { PublicTokenViewer } = await import("../PublicTokenViewer")
    const html = renderToStaticMarkup(React.createElement(PublicTokenViewer))
    expect(html).toContain("da-public-loading")
    expect(html).toContain("design-agent-surface")
    expect(html).toContain("Loading prototype")
    expect(css).toContain(".design-agent-surface .da-public-loading")
    vi.doUnmock("next/navigation")
    vi.resetModules()
  })

  // AC2 — error-state rule exists (the error branch's live render is an E2E
  // concern: it requires the resolver promise to reject, which the node-env SSR
  // path can't drive; its wrapper scope is the content-invariant above).
  it("test_error_state_class_and_rule: an error-state rule exists", () => {
    expect(css).toContain(".design-agent-surface .da-public-error")
  })

  // AC3 / AC4 — the passcode form renders, under the surface scope, with the
  // styled field/error/submit classes.
  it("renders the passcode gate form wrapped in design-agent-surface with its styled classes", () => {
    const html = renderToStaticMarkup(
      React.createElement(PasscodeGateView, {
        view: null,
        passcode: "",
        error: "Incorrect passcode.",
        busy: false,
        onPasscodeChange: () => {},
        onSubmit: () => {},
      }),
    )
    expect(html).toContain('class="design-agent-surface da-passcode-gate"')
    expect(html).toContain("da-passcode-label")
    expect(html).toContain("da-passcode-input")
    expect(html).toContain("da-passcode-error")
    expect(html).toContain("da-passcode-submit")
  })
})

describe("P6-18 no-drift / canonical", () => {
  // AC6 — exactly one rule block per public/passcode state class (no duplicate
  // competing rule between a P6-11 seed and this ticket).
  it("test_one_rule_per_state_class: at most one rule block per state class", () => {
    for (const cls of [
      "da-public-loading",
      "da-public-error",
      ...PASSCODE_CLASSES,
    ]) {
      expect(baseRuleBlocksFor(css, cls)).toBe(1)
    }
  })

  // AC7 — the appended passcode block is fully scoped + token-only (no hex
  // literal; the only literals permitted in this file are the .proto-dot
  // traffic-lights P6-12 seeded, which live far above this block).
  it("test_public_state_css_scoped_token_only: the appended passcode block is scoped + has no color literal", () => {
    // Strip comments first so prose (which references the selectors) isn't
    // parsed, then anchor on the real same-element gate selector — the start of
    // this ticket's appended passcode block.
    const cssNoComments = stripCssComments(css)
    const idx = cssNoComments.indexOf(".design-agent-surface.da-passcode-gate")
    expect(idx).toBeGreaterThan(-1)
    const appended = cssNoComments.slice(idx)
    // No hex color literals in the appended block.
    expect(appended).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    // Every selector head in the appended block is scoped under the surface.
    for (const block of appended.split("}")) {
      const brace = block.indexOf("{")
      if (brace === -1) continue
      const selector = block.slice(0, brace)
      // Skip the leading comment text captured before the first selector.
      if (!selector.includes("da-passcode")) continue
      for (const sel of selector.split(",")) {
        const trimmed = sel.trim()
        if (!trimmed) continue
        expect(trimmed.startsWith(".design-agent-surface")).toBe(true)
      }
    }
  })
})

describe("P6-18 behaviour unchanged (AC5 — CSS-only fix, no logic drift)", () => {
  // Canonical coverage of the resolver/passcode contract lives in
  // page.test.tsx; these parity checks re-assert the contract is untouched by
  // this CSS-states fix (the existing fixtures still pass unchanged).
  it("test_resolver_branching_unchanged: passcode-mode (null bundle) → passcode gate", () => {
    expect(
      nextViewerState({
        share_mode: "passcode",
        requires_passcode: true,
        bundle_url: null,
        is_complete: false,
      }),
    ).toEqual({ kind: "passcode" })
  })

  it("test_passcode_submit_unchanged: 429 throttle precedes 401 wrong-passcode", async () => {
    const fetch429 = vi.fn().mockResolvedValue({ status: 429, ok: false, json: async () => ({}) })
    const r429 = await submitPasscode({ token: "t", passcode: "x", fetchImpl: fetch429 })
    expect(r429.ok).toBe(false)
    if (!r429.ok) expect(r429.error).toContain("Too many attempts")

    const fetch401 = vi.fn().mockResolvedValue({ status: 401, ok: false, json: async () => ({}) })
    const r401 = await submitPasscode({ token: "t", passcode: "wrong", fetchImpl: fetch401 })
    expect(r401.ok).toBe(false)
    if (!r401.ok) expect(r401.error).toContain("Incorrect passcode")
  })
})

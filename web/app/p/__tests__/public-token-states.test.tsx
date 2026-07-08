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
import { submitPasscode, passcodeVerifyUrl } from "../PasscodeGate"
import { nextViewerState } from "../PublicTokenViewer"
import { PrototypeViewer } from "../../components/design-agent/PrototypeViewer"
import { CommentsPanel } from "../../components/design-agent/CommentsPanel"
import { ManualEditOverlay } from "../../components/design-agent/ManualEditOverlay"
import {
  MarkOverlay,
  PinLayer,
  PrototypeMarkLayer,
} from "../../components/design-agent/PrototypeMarkLayer"
import { IconMessage, IconPin } from "../../components/shared/app-icons"

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS_PATH = resolve(HERE, "../../components/design-agent/design-agent.css")
const PUBLIC_VIEWER_PATH = resolve(HERE, "../PublicTokenViewer.tsx")
const PUBLIC_CHROME_PATH = resolve(HERE, "../PublicPrototypeChrome.tsx")
const PASSCODE_GATE_PATH = resolve(HERE, "../PasscodeGate.tsx")

let css = ""
let publicViewerSrc = ""
let chromeSrc = ""
let passcodeGateSrc = ""
beforeAll(() => {
  css = readFileSync(CSS_PATH, "utf8")
  publicViewerSrc = readFileSync(PUBLIC_VIEWER_PATH, "utf8")
  chromeSrc = readFileSync(PUBLIC_CHROME_PATH, "utf8")
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

// ── C2a: public-viewer Mark + Comment head controls + writable-anon panel ───
// Node-env vitest can't drive the resolver effect (ready state) nor a real
// button click (commentsOpen toggle), so — mirroring the repo's source-invariant
// + initialPlatform-seam convention — we (a) assert the shipped ready-state JSX
// in PublicTokenViewer.tsx carries the two head buttons + the writable-anon
// CommentsPanel mount, and (b) live-render the EQUIVALENT ready-state fragment
// (headControls + chrome) through PrototypeViewer to prove it composes.
describe("C2a public head controls + writable-anon comments", () => {
  it("test_ready_state_jsx_has_mark_and_comment_buttons: PublicPrototypeChrome renders both head toggles with aria-pressed", () => {
    expect(chromeSrc).toContain('data-testid="public-mark-toggle"')
    expect(chromeSrc).toContain('data-testid="public-comments-toggle"')
    // both buttons reflect their toggle state via aria-pressed. C2b: Mark is now
    // driven by the shared hook (pin.markMode); Comment stays local (commentsOpen).
    expect(chromeSrc).toMatch(/aria-pressed=\{pin\.markMode\}/)
    expect(chromeSrc).toMatch(/aria-pressed=\{commentsOpen\}/)
    // controls are handed to PrototypeViewer via the new additive headControls prop
    expect(chromeSrc).toMatch(/headControls=\{/)
  })

  it("test_public_comments_mount_is_writable_anon: CommentsPanel mounts with canComment + token, NO prototypeId", () => {
    // The panel is gated behind commentsOpen and mounted with canComment (create
    // on) and the token (routes via createCommentByToken). It must NOT receive a
    // prototypeId — that keeps resolve/apply/ignore/delete hidden on the public
    // surface.
    expect(chromeSrc).toMatch(/commentsOpen\s*&&/)
    const mountMatch = chromeSrc.match(/<CommentsPanel[\s\S]*?\/>/)
    expect(mountMatch).not.toBeNull()
    const mount = mountMatch![0]
    expect(mount).toContain("token=")
    expect(mount).toContain("canComment")
    expect(mount).not.toContain("prototypeId")
  })

  it("test_ready_fragment_composes_through_prototype_viewer: head buttons in the frame head + writable-anon panel in the chrome slot", () => {
    // The exact fragment PublicTokenViewer mounts in its ready state.
    const html = renderToStaticMarkup(
      React.createElement(PrototypeViewer, {
        bundleUrl: "https://cdn.example/p/abc/index.html",
        isComplete: true,
        headControls: React.createElement(
          "div",
          { className: "platform-toggle proto-head-controls-group", role: "group" },
          React.createElement(
            "button",
            { type: "button", "data-testid": "public-mark-toggle", "aria-pressed": false },
            React.createElement(IconPin, { size: 14 }),
          ),
          React.createElement(
            "button",
            { type: "button", "data-testid": "public-comments-toggle", "aria-pressed": true },
            React.createElement(IconMessage, { size: 14 }),
          ),
        ),
        chrome: React.createElement(
          React.Fragment,
          null,
          React.createElement(ManualEditOverlay, { isComplete: true }),
          // commentsOpen === true → writable-anon panel
          React.createElement(CommentsPanel, { token: "tok-abc", canComment: true }),
        ),
      }),
    )
    // head buttons land inside the frame head
    expect(html).toContain('class="proto-head-controls"')
    expect(html).toContain('data-testid="public-mark-toggle"')
    expect(html).toContain('data-testid="public-comments-toggle"')
    // the comments panel mounts in the chrome slot
    expect(html).toContain('data-testid="prototype-chrome"')
    expect(html).toContain('data-testid="comments-panel"')
    // writable-anon: NO resolve affordance, NO apply/ignore/delete buttons, and
    // the inert ManualEditOverlay leaks no edit toggle.
    expect(html).not.toContain("comment-resolve-")
    expect(html).not.toContain("comment-apply-")
    expect(html).not.toContain("comment-ignore-")
    expect(html).not.toContain("comment-delete-")
    expect(html).not.toContain('data-testid="manual-edit-toggle"')
  })
})

// ── C2b: public marking via the shared usePinMarking hook ────────────────────
// The pin/mark logic is now ONE implementation (usePinMarking); the public
// surface injects createCommentByToken as the create-fn (NOT the signed-in
// createComment(prototype.id)). Node-env vitest can't drive the resolver effect
// to reach the ready state nor a real overlay click, so — mirroring the repo's
// source-invariant convention — we (a) assert PublicTokenViewer.tsx threads the
// hook with the token-based create-fn and mounts the overlay + read-only mark
// layer, and (b) live-render the EQUIVALENT ready fragment (stageOverlay +
// PrototypeMarkLayer) through PrototypeViewer to prove it composes with the
// Apply / Ignore / resolve affordances hidden.
describe("C2b public marking — shared hook + token create-fn", () => {
  it("test_public_threads_token_create_fn_NOT_authed_create: onCreate routes via createCommentByToken(token), never createComment(prototype.id)", () => {
    // Prove-it-fails-on-the-bug: if someone wires the AUTHED createComment on the
    // public surface, this fails. The public viewer has no prototypeId and must
    // not call the authed create.
    expect(chromeSrc).toContain("usePinMarking({")
    const start = chromeSrc.indexOf("usePinMarking({")
    const call = chromeSrc.slice(start, start + 500)
    // the injected create-fn is the by-token public route
    expect(call).toMatch(/onCreate:\s*\(payload\)\s*=>\s*designAgentApi\.createCommentByToken\(/)
    // and NOT the authed prototype-id create (the wrong-create-fn bug)
    expect(call).not.toContain("createComment(prototype")
    // the whole file must not reach for the authed create on this surface
    expect(chromeSrc).not.toMatch(/designAgentApi\.createComment\(/)
  })

  it("test_public_mark_button_drives_hook: the Mark head toggle is wired to pin.toggleMark / pin.markMode", () => {
    expect(chromeSrc).toMatch(/aria-pressed=\{pin\.markMode\}/)
    expect(chromeSrc).toMatch(/onClick=\{\(\)\s*=>\s*pin\.toggleMark\(\)\}/)
  })

  it("test_public_mounts_overlay_via_stageOverlay: MarkOverlay + PinLayer are handed to PrototypeViewer's stageOverlay", () => {
    expect(chromeSrc).toMatch(/stageOverlay=\{/)
    const start = chromeSrc.indexOf("stageOverlay={")
    const block = chromeSrc.slice(start, start + 400)
    expect(block).toContain("<MarkOverlay")
    expect(block).toContain("onStageClick={pin.handleStageClick}")
    expect(block).toContain("<PinLayer")
  })

  it("test_public_mark_layer_is_read_only: PrototypeMarkLayer mounts with editorMode=false + canResolve=false (Apply/Ignore/resolve hidden)", () => {
    expect(chromeSrc).toContain("<PrototypeMarkLayer")
    const start = chromeSrc.indexOf("<PrototypeMarkLayer")
    const mount = chromeSrc.slice(start, start + 400)
    expect(mount).toContain("editorMode={false}")
    expect(mount).toContain("canResolve={false}")
    expect(mount).toContain("onSubmitComment={pin.handlePinSubmit}")
    // no Apply/Ignore wiring on this surface (those are signed-in only)
    expect(mount).not.toContain("onPinApply")
    expect(mount).not.toContain("onPinIgnore")
  })

  it("test_public_ready_fragment_composes_with_marking_hidden_controls: stageOverlay over the iframe + read-only mark rows", () => {
    const savedPin = {
      n: 1,
      xPct: 50,
      yPct: 50,
      draft: "",
      body: "Move this up",
      saved: true,
      busy: false,
      error: null,
      author: "external",
      createdAt: "2026-06-06T08:00:00Z",
    }
    const html = renderToStaticMarkup(
      React.createElement(PrototypeViewer, {
        bundleUrl: "https://cdn.example/p/abc/index.html",
        isComplete: true,
        stageOverlay: React.createElement(
          React.Fragment,
          null,
          React.createElement(MarkOverlay, { markMode: true, onStageClick: () => {} }),
          React.createElement(PinLayer, { pins: [savedPin] as never }),
        ),
        chrome: React.createElement(PrototypeMarkLayer, {
          pins: [savedPin] as never,
          editorMode: false,
          canResolve: false,
        }),
      }),
    )
    // the overlay + pin render inside the proto-stage (over the iframe)
    expect(html).toContain('data-testid="proto-stage"')
    expect(html).toContain('data-testid="da-mark-overlay"')
    expect(html).toContain('data-testid="da-pin-1"')
    // the saved row renders, but Apply / Ignore / clickable resolve are hidden
    expect(html).toContain('data-testid="da-pin-comments"')
    expect(html).not.toContain('data-testid="da-pin-apply-1"')
    expect(html).not.toContain('data-testid="da-pin-ignore-1"')
    expect(html).not.toContain('data-testid="da-pin-resolve-1"')
    // read-only resolve indicator is the static (non-button) variant
    expect(html).toContain("comment-resolve-btn--static")
  })
})

// ── Phase 3: anon public writes — first-comment name capture ─────────────────
// Node-env vitest can't drive the resolver effect (ready state), the commentsOpen
// toggle, or the localStorage hydration effect, so — per the file's source-
// invariant convention — we assert the shipped PublicTokenViewer.tsx wires the
// name-capture form, the localStorage persistence, the PII notice, and threads the
// viewer name onto BOTH create paths; and we live-render the presentational name
// form fragment to prove it composes with first/last inputs + the PII notice.
describe("Phase 3 name capture + viewer_name threading", () => {
  it("test_name_capture_form_gated_on_first_comment_no_stored_name: the form renders when commentsOpen && no stored name", () => {
    // A single derived `viewerNeedsName = !viewerName` is the source of truth,
    // and needsName = commentsOpen && viewerNeedsName drives the form.
    expect(chromeSrc).toMatch(/viewerNeedsName\s*=\s*!viewerName/)
    expect(chromeSrc).toMatch(/needsName\s*=\s*commentsOpen\s*&&\s*viewerNeedsName/)
    expect(chromeSrc).toMatch(/commentsOpen\s*&&\s*needsName\s*&&/)
    expect(chromeSrc).toContain('data-testid="viewer-name-form"')
    // single full-name input (first/last collapsed).
    expect(chromeSrc).toContain('data-testid="viewer-full-name-input"')
    expect(chromeSrc).not.toContain('data-testid="viewer-first-name-input"')
    expect(chromeSrc).not.toContain('data-testid="viewer-last-name-input"')
  })

  it("test_viewer_name_persists_to_localstorage: name is read from + written to the da-viewer-name key", () => {
    expect(chromeSrc).toContain('"da-viewer-name"')
    expect(chromeSrc).toMatch(/localStorage\.getItem\(VIEWER_NAME_KEY\)/)
    expect(chromeSrc).toMatch(/localStorage\.setItem\(VIEWER_NAME_KEY/)
    // on submit, persist THEN set state (so the panel renders next).
    expect(chromeSrc).toMatch(/persistViewerName\(name\)/)
    expect(chromeSrc).toMatch(/setViewerName\(name\)/)
  })

  it("test_pii_notice_present: the capture form discloses where the name + comment go", () => {
    expect(chromeSrc).toContain('data-testid="viewer-name-notice"')
    expect(chromeSrc).toMatch(/Your name and comment are shared with the prototype/)
  })

  it("test_viewer_name_threads_into_both_create_paths: pin onCreate AND CommentsPanel mount carry the viewer name", () => {
    // Pin create path: createCommentByToken(token, { ...payload, viewer_name: viewerName }).
    expect(chromeSrc).toMatch(
      /createCommentByToken\([^)]*\{\s*\.\.\.payload,\s*viewer_name:\s*viewerName\s*\}/,
    )
    // CommentsPanel mount path: viewerName prop threaded.
    const mountMatch = chromeSrc.match(/<CommentsPanel[\s\S]*?\/>/)
    expect(mountMatch).not.toBeNull()
    expect(mountMatch![0]).toMatch(/viewerName=\{viewerName\}/)
    // still NO prototypeId on the public mount (min-disclosure preserved).
    expect(mountMatch![0]).not.toContain("prototypeId")
  })

  it("test_name_form_fragment_composes_with_input_and_notice: live-renders the single full-name input + PII notice", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        "form",
        { className: "design-agent-surface da-viewer-name-form", "data-testid": "viewer-name-form" },
        React.createElement("input", { "data-testid": "viewer-full-name-input", placeholder: "Full name" }),
        React.createElement(
          "button",
          { type: "submit", "data-testid": "viewer-name-submit" },
          "Continue",
        ),
        React.createElement(
          "p",
          { "data-testid": "viewer-name-notice" },
          "Your name and comment are shared with the prototype's owner.",
        ),
      ),
    )
    expect(html).toContain('data-testid="viewer-full-name-input"')
    expect(html).not.toContain('data-testid="viewer-first-name-input"')
    expect(html).not.toContain('data-testid="viewer-last-name-input"')
    expect(html).toContain('data-testid="viewer-name-notice"')
    expect(html).toContain("design-agent-surface")
  })

  it("test_min_disclosure_holds_on_public_mount: resolve/apply/ignore/delete stay hidden", () => {
    // The writable-anon CommentsPanel mount must not pass a prototypeId, which is
    // what gates the resolve/apply/ignore/delete affordances OFF on the public surface.
    const mountMatch = chromeSrc.match(/<CommentsPanel[\s\S]*?\/>/)
    expect(mountMatch).not.toBeNull()
    expect(mountMatch![0]).toContain("canComment")
    expect(mountMatch![0]).not.toContain("prototypeId")
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
        company_slug: "",
        target_platform: "both",
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

// REGRESSION LOCK (Option-A parity with the view-grant mint): the passcode POST
// mints the host-only `da_share_grant` cookie, so it MUST target the APP-ORIGIN
// /_da-bundle path (same-origin, relative) — NOT the API origin (API_URL). A
// mint on the API origin would set the cookie host-only to api.<domain> and it
// would never attach to the app-origin iframe asset GETs → prod blank-render.
// localhost masks this (port-agnostic cookies), so pin the target origin here.
describe("passcode mint targets the app-origin /_da-bundle path (prod cookie correctness)", () => {
  it("passcodeVerifyUrl is the relative app-origin /_da-bundle path, not an API_URL absolute", () => {
    const url = passcodeVerifyUrl("tok-123")
    expect(url).toBe("/_da-bundle/v1/design-agent/by-token/tok-123/passcode")
    // Relative (no scheme/host) ⇒ same-origin by construction; never the API origin.
    expect(url.startsWith("/_da-bundle/")).toBe(true)
    expect(url).not.toMatch(/^https?:\/\//)
    // Token is URL-encoded.
    expect(passcodeVerifyUrl("a/b?c")).toBe(
      "/_da-bundle/v1/design-agent/by-token/a%2Fb%3Fc/passcode",
    )
  })

  it("submitPasscode POSTs to the /_da-bundle path with credentials: 'include'", async () => {
    const spy = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({ bundle_url: "u", is_complete: true }),
    })
    await submitPasscode({ token: "tok-9", passcode: "p", fetchImpl: spy })
    expect(spy).toHaveBeenCalledTimes(1)
    const [calledUrl, init] = spy.mock.calls[0]
    expect(calledUrl).toBe("/_da-bundle/v1/design-agent/by-token/tok-9/passcode")
    expect(init.method).toBe("POST")
    expect(init.credentials).toBe("include")
  })
})

// ── Single-device toggle gate + device badge ─────────────────────────────────
// Behaviour (toggle hidden / badge shown per target_platform, mobile stage
// default) is proven end-to-end on the real container in PublicTokenViewer.dom.
// test.tsx. Here we lock the two things a jsdom render can't see: the exact
// prop-threading in the container source, and the token-only/scoped CSS rule
// (jsdom does not apply the external stylesheet).
describe("single-device badge — wiring + CSS invariants", () => {
  it("PublicPrototypeChrome derives the gate and threads showDesktop/showMobile/initialPlatform + gates the badge", () => {
    // The gate mirrors the signed-in single-device viewer: single device
    // ⇒ suppress the toggle.
    expect(chromeSrc).toContain('const showDesktop = targetPlatform !== "mobile"')
    expect(chromeSrc).toContain('const showMobile = targetPlatform !== "desktop"')
    // Props threaded to PrototypeViewer (a mid-tree drop fails the .dom test too).
    expect(chromeSrc).toContain("showDesktop={showDesktop}")
    expect(chromeSrc).toContain("showMobile={showMobile}")
    expect(chromeSrc).toContain(
      'initialPlatform={targetPlatform === "mobile" ? "mobile" : "desktop"}',
    )
    // The badge renders ONLY for a single-device prototype.
    expect(chromeSrc).toContain("singleDevice && <DeviceBadge platform={targetPlatform} />")
  })

  it("design-agent.css defines a scoped, token-only .device-badge rule appended after .platform-toggle", () => {
    const clean = stripCssComments(css)
    const toggleIdx = clean.indexOf(".design-agent-surface .platform-toggle")
    const badgeIdx = clean.indexOf(".design-agent-surface .device-badge")
    expect(toggleIdx).toBeGreaterThan(-1)
    expect(badgeIdx).toBeGreaterThan(toggleIdx) // appended AFTER the toggle block
    // Exactly one base rule block for the badge (no duplicate/competing rule).
    expect(baseRuleBlocksFor(css, "device-badge")).toBe(1)
    // Token-only body — the three spec tokens, no color literal.
    const body = clean.slice(badgeIdx, clean.indexOf("}", badgeIdx))
    expect(body).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(body).toContain("var(--surface-3)")
    expect(body).toContain("var(--line-strong)")
    expect(body).toContain("var(--ink-3)")
  })
})

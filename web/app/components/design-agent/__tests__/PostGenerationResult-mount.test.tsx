// P4-10 — internal manual-edit mount wiring. Node-env vitest (no DOM, no router,
// no @testing-library), so — following the CompletionBar / ManualEditOverlay
// convention — we SSR-render the real wiring via renderToStaticMarkup and assert
// the overlay mount point.
//
// What this proves: the in-app PostGenerationResult surface (Option A, internal
// by construction — it only renders inside (app)/AuthGate) mounts the EDITABLE
// overlay with a REAL numeric prototypeId so F13 manual-edit is reachable, while
// the public `/p/<token>` mount keeps passing no prototypeId → the overlay
// renders nothing (AC10 preserved). The live click→select / DOM-mutation /
// collision / Save-edits passes (AC3–AC6) require a real browser + same-origin
// iframe + built bundle and run in the sprntly-tester browser lane, NOT here.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses the
// classic runtime, so expose React globally (CommentsPanel/page test convention)
// rather than touch the shared vitest config.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  PostGenerationResultView,
  type PostGenerationResultViewProps,
} from "../PostGenerationResult"
import { ManualEditOverlay } from "../ManualEditOverlay"

const BUNDLE = "https://cdn/x/bundle/index.html"

function renderInternal(
  over: Partial<PostGenerationResultViewProps> = {},
): string {
  return renderToStaticMarkup(
    React.createElement(PostGenerationResultView, {
      prototypeId: 42,
      isComplete: false,
      shareMode: "private",
      shareToken: null,
      bundleUrl: BUNDLE,
      ...over,
    }),
  )
}

describe("PostGenerationResult internal mount — inline viewer renders (AC1)", () => {
  it("test_internal_mount_passes_numeric_prototype_id", () => {
    // The internal surface with a real numeric prototypeId + a built bundle
    // embeds the inline viewer iframe. The ManualEditOverlay trigger is
    // intentionally not rendered on the canvas — mark-and-comment is the
    // annotation path (see PostGenerationResult.tsx).
    const html = renderInternal({ prototypeId: 42, bundleUrl: BUNDLE })
    // The embedded iframe renders.
    expect(html).toContain('class="da-prototype-iframe"')
    // The overlay trigger is intentionally absent from the canvas.
    expect(html).not.toContain('data-testid="manual-edit-overlay"')
    expect(html).not.toContain('data-testid="manual-edit-toggle"')
    expect(html).not.toContain('data-testid="manual-edit-locked-note"')
  })

  it("does not embed the editable viewer or overlay when there is no built bundle", () => {
    // Without a bundle there is no same-origin iframe to drive, so the embedded
    // viewer + overlay do not mount. P6-16: the affordance is now the ALWAYS-shown
    // full-screen trigger (disabled-with-label here), NOT the obsolete
    // `view-prototype-link` anchor.
    const html = renderInternal({ bundleUrl: null, shareToken: "tok-123" })
    expect(html).not.toContain('class="da-prototype-iframe"')
    expect(html).not.toContain('data-testid="manual-edit-overlay"')
    // The always-shown View control is present and disabled (no bundle yet) — the
    // never-dead invariant (P6-16 AC1). The old anchor is gone. The trigger is now
    // the compact control-bar icon button `proto-fullscreen-trigger` (renamed from
    // `view-fullscreen-trigger`).
    expect(html).not.toContain('data-testid="view-prototype-link"')
    expect(html).toContain('data-testid="proto-fullscreen-trigger"')
    expect(html).toMatch(/data-testid="proto-fullscreen-trigger"[^>]*disabled/)
  })
})

describe("Public mount stays inert — AC10 no-regression (AC2)", () => {
  it("test_public_mount_passes_no_prototype_id_overlay_inert", () => {
    // The public `/p/<token>` viewer mounts the overlay exactly as
    // `<ManualEditOverlay isComplete={...} />` — no prototypeId. The minimum-
    // disclosure resolver never exposes a prototype_id on that surface, so the
    // overlay renders nothing (enabled = prototypeId != null = false).
    const html = renderToStaticMarkup(
      React.createElement(ManualEditOverlay, { isComplete: false }),
    )
    expect(html).toBe("")
    // And with a numeric id (the internal mount) the same component DOES render
    // its toggle — proving the difference is the presence of prototypeId.
    const internal = renderToStaticMarkup(
      React.createElement(ManualEditOverlay, { prototypeId: 42, isComplete: false }),
    )
    expect(internal).toContain('data-testid="manual-edit-toggle"')
  })
})

describe("Locked prototype on the internal mount — overlay intentionally absent (AC1/AC9)", () => {
  it("test_locked_prototype_disables_toggle_on_internal_mount", () => {
    // A complete prototype on the internal mount: the canvas inline viewer still
    // renders. ManualEditOverlay trigger is not on the canvas regardless of lock
    // state — the overlay is intentionally absent (mark-and-comment is the path).
    const html = renderInternal({ prototypeId: 42, isComplete: true, bundleUrl: BUNDLE })
    expect(html).toContain('class="da-prototype-iframe"')
    expect(html).not.toContain('data-testid="manual-edit-overlay"')
    expect(html).not.toContain('data-testid="manual-edit-toggle"')
    expect(html).not.toContain('data-testid="manual-edit-locked-note"')
  })
})

describe("Bundle-loading overlay (Focus scrim restyle) — gated on bundleNotReady", () => {
  // The overlay is a pure RESTYLE driven ENTIRELY by the existing
  // `bundleNotReady` flip (set/cleared by the useViewGrant probe). These
  // assert structure/copy/gating only (jsdom doesn't compute CSS) — the non-vacuity
  // is present-when-notReady / absent-when-ready.
  it("renders the overlay with a passive 'Loading…' label when bundleNotReady without an iterate", () => {
    const html = renderInternal({ bundleUrl: BUNDLE, bundleNotReady: true })
    // Present, with the preserved testid + a11y semantics + reused spinner.
    expect(html).toContain('data-testid="da-bundle-loading"')
    expect(html).toContain('role="status"')
    expect(html).toContain('aria-live="polite"')
    // Reused .da-spinner via a modifier — no forked spinner class.
    expect(html).toContain("da-spinner da-bundle-loading-spinner")
    // The stacked label node.
    expect(html).toContain('class="da-bundle-loading-label"')
    // Label is now iterate-aware: a passive (re)load says "Loading…", NOT the
    // misleading "Applying changes…" (which is reserved for a genuine iterate).
    expect(html).toContain("Loading…")
    expect(html).not.toContain("Applying changes…")
    // The pre-restyle copy is gone (no stale assertion left behind).
    expect(html).not.toContain("Loading preview…")
  })

  it("renders the overlay with 'Applying changes…' only during a genuine iterate", () => {
    const html = renderInternal({ bundleUrl: BUNDLE, bundleNotReady: true, iterateRunning: true })
    expect(html).toContain('data-testid="da-bundle-loading"')
    expect(html).toContain("Applying changes…")
  })

  it("does NOT render the overlay when bundleNotReady is false (default)", () => {
    // Same granted bundle, notReady false → the viewer renders but the overlay
    // is absent. This is the non-vacuity counterpart.
    const html = renderInternal({ bundleUrl: BUNDLE, bundleNotReady: false })
    expect(html).toContain('class="da-prototype-iframe"')
    expect(html).not.toContain('data-testid="da-bundle-loading"')
    expect(html).not.toContain("Applying changes…")
  })
})

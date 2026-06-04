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
import { ManualEditOverlay, LOCKED_AFFORDANCE } from "../ManualEditOverlay"

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

describe("PostGenerationResult internal mount — overlay reachable (AC1)", () => {
  it("test_internal_mount_passes_numeric_prototype_id", () => {
    // The internal surface with a real numeric prototypeId + a built bundle
    // embeds the editable viewer and mounts the overlay with that id → the
    // toggle renders (enabled = prototypeId != null). This is the mount that
    // makes F13 manual-edit reachable.
    const html = renderInternal({ prototypeId: 42, bundleUrl: BUNDLE })
    // The embedded same-origin iframe the overlay drives for click→select.
    expect(html).toContain('class="da-prototype-iframe"')
    // The overlay mounted with a numeric prototypeId → it renders (not inert).
    expect(html).toContain('data-testid="manual-edit-overlay"')
    expect(html).toContain('data-testid="manual-edit-toggle"')
    // Not locked (is_complete=false) → the toggle is the live edit affordance,
    // not the disabled locked one.
    expect(html).not.toMatch(/data-testid="manual-edit-toggle"[^>]*disabled/)
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
    // never-dead invariant (P6-16 AC1). The old anchor is gone.
    expect(html).not.toContain('data-testid="view-prototype-link"')
    expect(html).toContain('data-testid="view-fullscreen-trigger"')
    expect(html).toMatch(/data-testid="view-fullscreen-trigger"[^>]*disabled/)
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

describe("Locked prototype disables the toggle on the internal mount (AC1/AC9)", () => {
  it("test_locked_prototype_disables_toggle_on_internal_mount", () => {
    // A complete prototype on the internal mount: the overlay still mounts (real
    // prototypeId) but the toggle is disabled with the F14 locked affordance —
    // re-uses P4-01's LOCKED_AFFORDANCE, no edit until iteration resumes.
    const html = renderInternal({ prototypeId: 42, isComplete: true, bundleUrl: BUNDLE })
    expect(html).toContain('data-testid="manual-edit-overlay"')
    expect(html).toMatch(/data-testid="manual-edit-toggle"[^>]*disabled/)
    expect(html).toContain('data-testid="manual-edit-locked-note"')
    expect(html).toContain(LOCKED_AFFORDANCE)
  })
})

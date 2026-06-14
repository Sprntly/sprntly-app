// C1 Slice B — PrototypeMarkLayer leaf tests. Node-env vitest (no DOM, no
// @testing-library), so — following the PostGenerationResult / CommentsPanel
// convention — we SSR-render the pure pieces via renderToStaticMarkup.
//
// These are the LEAF render assertions for the mark overlay, the pin layer, and
// the `.da-right` pin-comment rows, moved out of PostGenerationResult.test.tsx's
// "Mark-and-comment pin flow — view layer" block. The integration coverage that
// renders the REAL PostGenerationResult container (and asserts handlePinSubmit's
// anchor_id + pin coords payload) stays in PostGenerationResult.test.tsx — a leaf
// test passing here must NOT be read as proof the container still threads the
// pin-anchor payload end to end.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses the
// classic runtime, so expose React globally (repo test convention) rather than
// touch the shared vitest config.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  MarkOverlay,
  PinLayer,
  PrototypeMarkLayer,
} from "../PrototypeMarkLayer"
import type { PinComment } from "../PostGenerationResult"

function pinComment(over: Partial<PinComment> = {}): PinComment {
  return {
    n: 1,
    xPct: 50,
    yPct: 50,
    draft: "",
    body: "",
    saved: false,
    busy: false,
    error: null,
    anchor: null,
    xPctInEl: null,
    yPctInEl: null,
    elementFriendly: null,
    elementTechnical: null,
    ...over,
  }
}

describe("MarkOverlay — stage overlay", () => {
  it("test_mark_mode_activates_overlay — markMode=true renders .da-mark-overlay.active", () => {
    const html = renderToStaticMarkup(
      React.createElement(MarkOverlay, { markMode: true }),
    )
    expect(html).toContain('class="da-mark-overlay active"')
    expect(html).toContain('data-testid="da-mark-overlay"')
    expect(html).toContain('aria-hidden="false"')
  })

  it("markMode=false renders an inert overlay (no active class, aria-hidden)", () => {
    const html = renderToStaticMarkup(
      React.createElement(MarkOverlay, { markMode: false }),
    )
    expect(html).toContain('class="da-mark-overlay"')
    expect(html).not.toContain("da-mark-overlay active")
    expect(html).toContain('aria-hidden="true"')
  })
})

describe("PinLayer — numbered teardrops", () => {
  it("renders nothing when there are no pins", () => {
    const html = renderToStaticMarkup(
      React.createElement(PinLayer, { pins: [] }),
    )
    expect(html).toBe("")
  })

  it("renders a numbered teardrop per pin at its static x/y", () => {
    const html = renderToStaticMarkup(
      React.createElement(PinLayer, {
        pins: [pinComment({ n: 1, xPct: 40, yPct: 60 })],
      }),
    )
    expect(html).toContain('data-testid="da-pin-layer"')
    expect(html).toContain('data-testid="da-pin-1"')
    // static position is reflected in the inline style
    expect(html).toContain("left:40%")
    expect(html).toContain("top:60%")
  })

  it("an anchor-tracked computedPinPositions entry overrides the static x/y", () => {
    const html = renderToStaticMarkup(
      React.createElement(PinLayer, {
        pins: [pinComment({ n: 2, xPct: 10, yPct: 10 })],
        computedPinPositions: { 2: { xPct: 80, yPct: 25 } },
      }),
    )
    expect(html).toContain('data-testid="da-pin-2"')
    expect(html).toContain("left:80%")
    expect(html).toContain("top:25%")
    // the static fallback is NOT used when a computed position exists
    expect(html).not.toContain("left:10%")
  })
})

describe("PrototypeMarkLayer — pin-comment rows", () => {
  it("renders nothing when there are no pins", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypeMarkLayer, { pins: [] }),
    )
    expect(html).toBe("")
  })

  it("test_stage_click_drops_numbered_pin_and_composer — an unsaved pin renders its composer (input + submit + cancel)", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypeMarkLayer, {
        pins: [pinComment({ n: 1, draft: "" })],
      }),
    )
    expect(html).toContain('data-testid="da-pin-comments"')
    expect(html).toContain('data-testid="da-pin-input-1"')
    expect(html).toContain('data-testid="da-pin-submit-1"')
    expect(html).toContain('data-testid="da-pin-cancel-1"')
  })

  it("test_saved_pin_row_shows_author_avatar_time — a saved row renders author + avatar + relative-time + Apply/Ignore", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypeMarkLayer, {
        pins: [
          pinComment({
            n: 2,
            saved: true,
            body: "Looks great",
            author: "Carol D",
            createdAt: "2026-06-06T08:00:00Z",
          }),
        ],
      }),
    )
    expect(html).toContain("Carol D")
    expect(html).toContain('data-testid="comment-avatar"')
    expect(html).toContain('class="proto-comment-time"')
    expect(html).toContain("2026-06-06T08:00:00Z")
    expect(html).toContain('data-testid="da-pin-apply-2"')
    expect(html).toContain('data-testid="da-pin-ignore-2"')
  })

  it("Part 2 — a saved row's resolve control reuses the shared .comment-resolve-btn (no inline comment-resolve-indicator)", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypeMarkLayer, {
        pins: [pinComment({ n: 3, saved: true, body: "fix this", author: "demo", createdAt: "2026-06-06T08:00:00Z" })],
        canResolve: true,
      }),
    )
    // The consolidated resolve control uses the shared CommentsPanel class…
    expect(html).toContain('class="comment-resolve-btn"')
    expect(html).toContain('data-testid="da-pin-resolve-3"')
    // …and the old inline indicator is gone entirely.
    expect(html).not.toContain("comment-resolve-indicator")
  })

  it("canResolve=false renders the resolve control display-only (--static), with no button", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypeMarkLayer, {
        pins: [pinComment({ n: 4, saved: true, body: "x", author: "demo", createdAt: "2026-06-06T08:00:00Z" })],
        canResolve: false,
      }),
    )
    expect(html).toContain("comment-resolve-btn--static")
    expect(html).not.toContain('data-testid="da-pin-resolve-4"')
  })

  it("a resolved saved row is green-filled (.comment-resolve-btn.resolved) and shows the Resolved note, no Apply/Ignore", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypeMarkLayer, {
        pins: [pinComment({ n: 5, saved: true, resolved: true, body: "done", author: "demo", createdAt: "2026-06-06T08:00:00Z" })],
        canResolve: true,
      }),
    )
    expect(html).toMatch(/comment-resolve-btn[^"]*\bresolved\b/)
    expect(html).toContain("Resolved")
    expect(html).not.toContain('data-testid="da-pin-apply-5"')
    expect(html).not.toContain('data-testid="da-pin-ignore-5"')
  })

  it("editorMode=false suppresses the Apply / Ignore actions", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypeMarkLayer, {
        pins: [pinComment({ n: 6, saved: true, body: "x", author: "demo", createdAt: "2026-06-06T08:00:00Z" })],
        editorMode: false,
      }),
    )
    expect(html).not.toContain('data-testid="da-pin-apply-6"')
    expect(html).not.toContain('data-testid="da-pin-ignore-6"')
  })

  it("surfaces a per-pin create error without dropping the row", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypeMarkLayer, {
        pins: [pinComment({ n: 7, error: "Could not save comment" })],
      }),
    )
    expect(html).toContain('data-testid="da-pin-comment-7"')
    expect(html).toContain("Could not save comment")
  })
})

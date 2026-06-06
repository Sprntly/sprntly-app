// Unit tests for the pinAnchorBridge resolveAnchorAtPoint function.
// Pure function + mocked DOM objects — no React, no renderToStaticMarkup,
// no live iframe needed. The function's only external dependency is the
// iframe's contentDocument and getBoundingClientRect.
import { describe, expect, it } from "vitest"

import { resolveAnchorAtPoint } from "../pinAnchorBridge"

// ─── helpers ─────────────────────────────────────────────────────────────────

function makeEl(anchorId: string | null, children: Element[] = []): Element {
  const el = {
    getAttribute: (attr: string) => (attr === "data-anchor-id" ? anchorId : null),
    closest: (selector: string) => {
      if (selector === "[data-anchor-id]") {
        if (anchorId !== null) return el
        // walk up; in these tests parents are provided explicitly
        return null
      }
      return null
    },
    children,
  } as unknown as Element
  return el
}

function makeElWithAncestorAnchor(ancestorId: string): Element {
  const ancestor = {
    getAttribute: (attr: string) => (attr === "data-anchor-id" ? ancestorId : null),
  } as unknown as Element

  const el = {
    getAttribute: () => null,
    closest: (selector: string) => {
      if (selector === "[data-anchor-id]") return ancestor
      return null
    },
  } as unknown as Element
  return el
}

function makeElWithNoAncestor(): Element {
  return {
    getAttribute: () => null,
    closest: (_selector: string) => null,
  } as unknown as Element
}

function makeIframe(
  contentDocument: Document | null,
  rect: { left: number; top: number } = { left: 0, top: 0 },
): HTMLIFrameElement {
  return {
    contentDocument,
    getBoundingClientRect: () => ({ left: rect.left, top: rect.top, width: 800, height: 600 }),
  } as unknown as HTMLIFrameElement
}

function makeDoc(elAtPoint: Element | null, expectedX?: number, expectedY?: number) {
  return {
    elementFromPoint: (x: number, y: number) => {
      if (expectedX !== undefined) {
        // The test can assert which coords were passed by checking the return.
        // We store them so the test can inspect — simplest approach: just
        // return elAtPoint and let the coord-translation test read the spy.
      }
      return elAtPoint
    },
  } as unknown as Document
}

// ─── tests ───────────────────────────────────────────────────────────────────

describe("resolveAnchorAtPoint", () => {
  it("returns data-anchor-id when the element at the point carries one", () => {
    const el = makeEl("button-primary-abc")
    const doc = makeDoc(el)
    const iframe = makeIframe(doc as Document)
    const result = resolveAnchorAtPoint(iframe, 100, 200)
    expect(result).toBe("button-primary-abc")
  })

  it("returns data-anchor-id from the closest anchored ancestor when the direct hit has none", () => {
    const el = makeElWithAncestorAnchor("section-hero-xyz")
    const doc = makeDoc(el)
    const iframe = makeIframe(doc as Document)
    expect(resolveAnchorAtPoint(iframe, 50, 50)).toBe("section-hero-xyz")
  })

  it("returns null when no element at the point carries a data-anchor-id ancestor", () => {
    const el = makeElWithNoAncestor()
    const doc = makeDoc(el)
    const iframe = makeIframe(doc as Document)
    expect(resolveAnchorAtPoint(iframe, 50, 50)).toBeNull()
  })

  it("returns null when elementFromPoint returns null (empty area)", () => {
    const doc = makeDoc(null)
    const iframe = makeIframe(doc as Document)
    expect(resolveAnchorAtPoint(iframe, 50, 50)).toBeNull()
  })

  it("returns null when iframe is null", () => {
    expect(resolveAnchorAtPoint(null, 100, 200)).toBeNull()
  })

  it("returns null when contentDocument is null (cross-origin or not loaded)", () => {
    const iframe = makeIframe(null)
    expect(resolveAnchorAtPoint(iframe, 100, 200)).toBeNull()
  })

  it("returns null and never throws when accessing contentDocument throws (SecurityError)", () => {
    const iframe = {
      get contentDocument(): never {
        throw new DOMException("Blocked a frame with origin", "SecurityError")
      },
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }),
    } as unknown as HTMLIFrameElement
    expect(() => resolveAnchorAtPoint(iframe, 100, 200)).not.toThrow()
    expect(resolveAnchorAtPoint(iframe, 100, 200)).toBeNull()
  })

  it("translates viewport coordinates into the iframe coordinate space", () => {
    // iframe sits at (left=50, top=30) in the viewport.
    // A click at viewport (120, 80) should become inner (70, 50).
    let capturedX: number | undefined
    let capturedY: number | undefined
    const doc = {
      elementFromPoint: (x: number, y: number) => {
        capturedX = x
        capturedY = y
        return makeEl("translated-anchor")
      },
    } as unknown as Document
    const iframe = makeIframe(doc, { left: 50, top: 30 })
    resolveAnchorAtPoint(iframe, 120, 80)
    expect(capturedX).toBe(70)
    expect(capturedY).toBe(50)
  })
})

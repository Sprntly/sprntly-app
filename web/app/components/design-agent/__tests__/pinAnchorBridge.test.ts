// Unit tests for pinAnchorBridge — updated for the rewritten API.
// Pure functions + mocked DOM objects — no React, no renderToStaticMarkup,
// no live iframe needed.
import { describe, expect, it } from "vitest"

import {
  getElementAtIframePoint,
  getElementAnchor,
  findByAnchor,
  getAnchorPosition,
  parseStoredAnchor,
  serializeAnchor,
  setElementHighlight,
  clearElementHighlight,
} from "../pinAnchorBridge"

// ─── helpers ─────────────────────────────────────────────────────────────────

// node-env vitest: Node global is not available; use the numeric constant directly.
const ELEMENT_NODE = 1

function makeEl(anchorId: string | null, tagName = "DIV"): Element {
  const el: Partial<Element> & { style?: Record<string, string>; tagName: string } = {
    tagName,
    getAttribute: (attr: string) => (attr === "data-anchor-id" ? anchorId : null),
    closest: (selector: string) => {
      if (selector === "[data-anchor-id]") {
        if (anchorId !== null) return el as Element
        return null
      }
      return null
    },
    previousElementSibling: null,
    parentElement: null,
    nodeType: ELEMENT_NODE,
    style: {},
  }
  return el as unknown as Element
}

function makeElWithAncestorAnchor(ancestorId: string): Element {
  const ancestor = {
    getAttribute: (attr: string) => (attr === "data-anchor-id" ? ancestorId : null),
  } as unknown as Element

  const el = {
    tagName: "SPAN",
    getAttribute: () => null,
    closest: (selector: string) => {
      if (selector === "[data-anchor-id]") return ancestor
      return null
    },
    previousElementSibling: null,
    parentElement: null,
    nodeType: ELEMENT_NODE,
  } as unknown as Element
  return el
}

function makeElWithNoAncestor(): Element {
  return {
    tagName: "DIV",
    getAttribute: () => null,
    closest: (_selector: string) => null,
    previousElementSibling: null,
    parentElement: null,
    nodeType: ELEMENT_NODE,
  } as unknown as Element
}

function makeIframe(
  contentDocument: Document | null,
  rect: { left: number; top: number; width?: number; height?: number } = { left: 0, top: 0 },
): HTMLIFrameElement {
  return {
    contentDocument,
    getBoundingClientRect: () => ({
      left: rect.left,
      top: rect.top,
      width: rect.width ?? 800,
      height: rect.height ?? 600,
    }),
  } as unknown as HTMLIFrameElement
}

function makeDoc(elAtPoint: Element | null) {
  return {
    elementFromPoint: (_x: number, _y: number) => elAtPoint,
  } as unknown as Document
}

// ─── getElementAtIframePoint ──────────────────────────────────────────────────

describe("getElementAtIframePoint", () => {
  it("returns the element at the translated point inside the iframe", () => {
    const el = makeEl("button-abc")
    const doc = makeDoc(el)
    const iframe = makeIframe(doc)
    expect(getElementAtIframePoint(iframe, 100, 200)).toBe(el)
  })

  it("returns null when iframe is null", () => {
    expect(getElementAtIframePoint(null, 100, 200)).toBeNull()
  })

  it("returns null when contentDocument is null (cross-origin or not loaded)", () => {
    const iframe = makeIframe(null)
    expect(getElementAtIframePoint(iframe, 100, 200)).toBeNull()
  })

  it("returns null and never throws when accessing contentDocument throws (SecurityError)", () => {
    const iframe = {
      get contentDocument(): never {
        throw new DOMException("Blocked a frame with origin", "SecurityError")
      },
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }),
    } as unknown as HTMLIFrameElement
    expect(() => getElementAtIframePoint(iframe, 100, 200)).not.toThrow()
    expect(getElementAtIframePoint(iframe, 100, 200)).toBeNull()
  })

  it("translates viewport coordinates into the iframe coordinate space", () => {
    // iframe at (left=50, top=30); viewport click at (120, 80) → inner (70, 50).
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
    getElementAtIframePoint(iframe, 120, 80)
    expect(capturedX).toBe(70)
    expect(capturedY).toBe(50)
  })

  it("returns null when click is outside the iframe bounds", () => {
    const el = makeEl("out-of-bounds")
    const doc = makeDoc(el)
    const iframe = makeIframe(doc, { left: 100, top: 100, width: 800, height: 600 })
    // x=50 → ix = 50-100 = -50 → out of bounds
    expect(getElementAtIframePoint(iframe, 50, 150)).toBeNull()
  })
})

// ─── getElementAnchor ─────────────────────────────────────────────────────────

describe("getElementAnchor", () => {
  it("returns anchor-id type when the element has data-anchor-id", () => {
    const el = makeEl("button-primary-abc")
    const result = getElementAnchor(el)
    expect(result).toEqual({ type: "anchor-id", value: "button-primary-abc" })
  })

  it("returns anchor-id from the closest ancestor when the element itself has none", () => {
    const el = makeElWithAncestorAnchor("section-hero-xyz")
    const result = getElementAnchor(el)
    expect(result).toEqual({ type: "anchor-id", value: "section-hero-xyz" })
  })

  it("returns xpath type when no data-anchor-id exists in the tree", () => {
    const el = makeElWithNoAncestor()
    const result = getElementAnchor(el)
    // Since parentElement is null we get a single-segment path
    expect(result).not.toBeNull()
    expect(result!.type).toBe("xpath")
    expect(result!.value.startsWith("/")).toBe(true)
  })
})

// ─── parseStoredAnchor / serializeAnchor ──────────────────────────────────────

describe("parseStoredAnchor", () => {
  it("parses a plain anchor-id string", () => {
    expect(parseStoredAnchor("button-abc")).toEqual({ type: "anchor-id", value: "button-abc" })
  })

  it("parses an xpath: prefixed string", () => {
    expect(parseStoredAnchor("xpath://div/span")).toEqual({ type: "xpath", value: "//div/span" })
  })

  it("returns null for null", () => {
    expect(parseStoredAnchor(null)).toBeNull()
  })

  it("returns null for undefined", () => {
    expect(parseStoredAnchor(undefined)).toBeNull()
  })

  it("returns null for empty string", () => {
    expect(parseStoredAnchor("")).toBeNull()
  })
})

describe("serializeAnchor", () => {
  it("serializes an anchor-id anchor to a plain string", () => {
    expect(serializeAnchor({ type: "anchor-id", value: "btn-abc" })).toBe("btn-abc")
  })

  it("serializes an xpath anchor with xpath: prefix", () => {
    expect(serializeAnchor({ type: "xpath", value: "//div/span" })).toBe("xpath://div/span")
  })

  it("returns null for null", () => {
    expect(serializeAnchor(null)).toBeNull()
  })

  it("is the inverse of parseStoredAnchor for anchor-id", () => {
    const anchor = { type: "anchor-id" as const, value: "hero-btn" }
    expect(parseStoredAnchor(serializeAnchor(anchor)!)).toEqual(anchor)
  })

  it("is the inverse of parseStoredAnchor for xpath", () => {
    const anchor = { type: "xpath" as const, value: "//div[2]/span" }
    expect(parseStoredAnchor(serializeAnchor(anchor)!)).toEqual(anchor)
  })
})

// ─── setElementHighlight / clearElementHighlight ──────────────────────────────

describe("setElementHighlight / clearElementHighlight", () => {
  it("sets outline on a highlighted element and clears on null", () => {
    const el = {
      style: { outline: "", outlineOffset: "" },
    } as unknown as HTMLElement
    setElementHighlight(el)
    expect((el as HTMLElement).style.outline).not.toBe("")
    clearElementHighlight()
    expect((el as HTMLElement).style.outline).toBe("")
  })

  it("does not throw when called with null", () => {
    expect(() => clearElementHighlight()).not.toThrow()
    expect(() => setElementHighlight(null)).not.toThrow()
  })
})

// @vitest-environment jsdom
//
// "Ability to highlight a section and it comes up in the chat text field and
// ask questions about it or ask for an edit" — the panel half.
//
// The properties that matter here are the ones a user would feel: the button
// only exists when there is a selection IN THIS DOCUMENT, pressing it hands the
// passage over exactly once, and a long highlight does not shove three screens
// of the user's own document into the composer.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("../../../(app)/artifacts/doc/DocumentEditor", () => ({
  DocumentEditor: () => React.createElement("div", { "data-testid": "editor-stub" }),
}))

const api = vi.hoisted(() => ({ get: vi.fn(), update: vi.fn() }))
const { FakeApiError } = vi.hoisted(() => ({
  FakeApiError: class FakeApiError extends Error { status = 0; body: unknown = null },
}))
vi.mock("../../../lib/api", () => ({
  ApiError: FakeApiError,
  customArtifactsApi: {
    get: (...a: unknown[]) => api.get(...a),
    update: (...a: unknown[]) => api.update(...a),
  },
}))

// The tab takes a CALLBACK rather than reaching into navigation context — see
// its prop docs. So this suite needs no provider and no context mock, which is
// exactly the property that keeps its two sibling suites working.
const nav = vi.hoisted(() => ({ onQuote: vi.fn() }))

import { DocumentTab, quoteForComposer } from "../DocumentTab"

const DOC = (over: Record<string, unknown> = {}) => ({
  id: 7, kind: "leadership update", title: "Q3", status: "ready",
  body_html: "<p>the pilot-partner scoping track is still open</p>",
  version: 2, created_at: "", updated_at: "", conversation_id: 1,
  created_by: null, updated_by: null, ...over,
})

beforeAll(() => {
  if (!Range.prototype.getClientRects) {
    Range.prototype.getClientRects = () =>
      ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} }) as unknown as DOMRectList
  }
  if (!Range.prototype.getBoundingClientRect) {
    Range.prototype.getBoundingClientRect = () =>
      ({ top: 120, left: 40, bottom: 0, right: 0, width: 0, height: 0 }) as DOMRect
  }
})

afterEach(() => { cleanup(); api.get.mockReset(); nav.onQuote.mockReset() })

/** Put a real selection inside (or outside) the rendered document. */
function selectInside(node: Node | null, text: string) {
  const sel = window.getSelection()!
  sel.removeAllRanges()
  if (node) {
    const range = document.createRange()
    range.selectNodeContents(node)
    sel.addRange(range)
  }
  // jsdom's Selection.toString() does not serialize ranges, so the text the
  // component reads is stubbed — the component's job is deciding WHETHER and
  // WHAT to hand over, not re-implementing the browser's selection.
  vi.spyOn(sel, "toString").mockReturnValue(text)
  document.dispatchEvent(new Event("selectionchange"))
}

async function mountTab() {
  api.get.mockResolvedValue(DOC())
  const utils = render(<DocumentTab documentId={7} onQuote={nav.onQuote} />)
  await waitFor(() => expect(utils.container.querySelector("[data-document-tab]")).not.toBeNull())
  return utils
}

describe("highlighting a passage offers to take it to the chat", () => {
  it("shows no button until something is selected", async () => {
    const { container } = await mountTab()
    expect(container.querySelector("[data-document-quote-cta]")).toBeNull()
  })

  it("hands the highlighted passage to the composer, once", async () => {
    const { container } = await mountTab()
    selectInside(container.querySelector("[data-document-body]"), "the pilot-partner scoping track")

    const cta = await waitFor(() => {
      const el = container.querySelector("[data-document-quote-cta]")
      expect(el).not.toBeNull()
      return el as HTMLElement
    })
    fireEvent.mouseDown(cta)

    expect(nav.onQuote).toHaveBeenCalledTimes(1)
    expect(nav.onQuote).toHaveBeenCalledWith("the pilot-partner scoping track")

    // THE OFFER SURVIVES THE PRESS, because the highlight does. Retiring it
    // while the passage stayed visibly selected put the two out of step: a
    // user who quotes, clears the composer and wants to quote again saw a
    // highlighted passage with no button and had to re-select it.
    expect(container.querySelector("[data-document-quote-cta]")).not.toBeNull()
    fireEvent.mouseDown(container.querySelector("[data-document-quote-cta]")!)
    expect(nav.onQuote).toHaveBeenCalledTimes(2)
  })

  it("ignores a selection made OUTSIDE this document", async () => {
    // The panel sits beside a chat full of selectable text. Quoting that as
    // though it came from the document would attribute someone's own message
    // to their leadership update.
    const { container } = await mountTab()
    const outside = document.createElement("p")
    outside.textContent = "a sentence in the thread"
    document.body.appendChild(outside)

    selectInside(outside, "a sentence in the thread")

    await new Promise((r) => setTimeout(r, 20))
    expect(container.querySelector("[data-document-quote-cta]")).toBeNull()
    outside.remove()
  })

  it("refuses a selection that STARTS here and ends outside", async () => {
    // The asymmetry review caught: `anchorNode` alone accepted a drag that
    // began in the document and finished in the chat beside it, while
    // `toString()` serialized both — so the thread's own text would have been
    // quoted as if it came from the document.
    const { container } = await mountTab()
    const outside = document.createElement("p")
    outside.textContent = "a sentence in the thread"
    document.body.appendChild(outside)

    const sel = window.getSelection()!
    sel.removeAllRanges()
    const range = document.createRange()
    range.setStart(container.querySelector("[data-document-body]")!, 0)
    range.setEnd(outside, 0)
    sel.addRange(range)
    vi.spyOn(sel, "toString").mockReturnValue("document text plus thread text")
    document.dispatchEvent(new Event("selectionchange"))

    await new Promise((r) => setTimeout(r, 20))
    expect(container.querySelector("[data-document-quote-cta]")).toBeNull()
    outside.remove()
  })

  it("offers nothing for a whitespace-only selection", async () => {
    const { container } = await mountTab()
    selectInside(container.querySelector("[data-document-body]"), "   \n  ")
    await new Promise((r) => setTimeout(r, 20))
    expect(container.querySelector("[data-document-quote-cta]")).toBeNull()
  })
})

describe("quoteForComposer", () => {
  it("collapses whitespace so a multi-line highlight reads as one quote", () => {
    expect(quoteForComposer("  the pilot\n\n  track  ")).toBe("the pilot track")
  })

  it("elides the middle of a very long passage, keeping both ends", () => {
    // A composer holding three screens of the user's own document is unusable,
    // and it crowds out the question they came to type. Both ends are kept so
    // the quote still reads as the start and end of what was highlighted.
    const long = `START ${"x".repeat(2000)} END`
    const out = quoteForComposer(long)
    expect(out.length).toBeLessThanOrEqual(605)
    expect(out.startsWith("START")).toBe(true)
    expect(out.endsWith("END")).toBe(true)
    expect(out).toContain("…")
  })
})

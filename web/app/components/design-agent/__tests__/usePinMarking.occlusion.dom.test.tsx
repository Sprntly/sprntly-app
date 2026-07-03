// @vitest-environment jsdom
//
// Occlusion behaviour for the shared pin engine: a comment pin is a parent-app
// overlay (z-index above the same-origin prototype iframe). When an in-iframe
// modal is drawn OVER the pin's anchored element, the pin must HIDE (not float on
// top of the modal); when the modal closes, the pin must RE-SHOW — driven by a
// MutationObserver on the iframe document, not a page reload.
//
// Exercised on real jsdom because the behaviour depends on effects, a
// MutationObserver on the iframe `contentDocument`, and elementFromPoint. jsdom
// does no layout, so the anchor-position bridge + getBoundingClientRect +
// elementFromPoint are stubbed to model "topmost element at the pin's point";
// the hook's occlusion decision + observer wiring under test are the real code.
import * as React from "react"
import { act, cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// Shared, mutable stubs the mock reads. hoisted so the vi.mock factory (hoisted
// above the module) can close over them; tests mutate them per case.
const bridge = vi.hoisted(() => ({
  pos: { xPct: 50, yPct: 50 } as { xPct: number; yPct: number } | null,
  anchorEl: null as Element | null,
}))

vi.mock("../pinAnchorBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../pinAnchorBridge")>()
  return {
    ...actual,
    getAnchorPosition: () => bridge.pos,
    getAnchorPositionWithOffset: () => bridge.pos,
    getClickOffsetInElement: () => ({ xPctInEl: 50, yPctInEl: 50 }),
    findByAnchor: () => bridge.anchorEl,
    getElementDescription: () => ({ friendly: "el", technical: "el" }),
  }
})

import { usePinMarking, type UsePinMarkingReturn } from "../usePinMarking"
import { PinLayer } from "../PrototypeMarkLayer"

let iframe: HTMLIFrameElement
let doc: Document
let anchorEl: HTMLElement
let topEl: Element | null // what elementFromPoint returns at the pin's point

// Capture the live hook API each render so the test can drive handleStageClick.
let api: UsePinMarkingReturn

function Harness() {
  api = usePinMarking({ onCreate: async () => null })
  return React.createElement(PinLayer, {
    pins: api.pins,
    computedPinPositions: api.computedPinPositions,
    occludedPins: api.occludedPins,
  })
}

beforeEach(() => {
  iframe = document.createElement("iframe")
  iframe.className = "da-prototype-iframe"
  document.body.appendChild(iframe)
  doc = iframe.contentDocument!
  anchorEl = doc.createElement("div")
  anchorEl.setAttribute("data-anchor-id", "a1")
  doc.body.appendChild(anchorEl)

  bridge.pos = { xPct: 50, yPct: 50 }
  bridge.anchorEl = anchorEl
  topEl = anchorEl // unoccluded by default

  // jsdom does no layout: model a real iframe viewport + a topmost element.
  iframe.getBoundingClientRect = () =>
    ({ width: 1000, height: 800, left: 0, top: 0, right: 1000, bottom: 800, x: 0, y: 0, toJSON() {} }) as DOMRect
  doc.elementFromPoint = () => topEl
})

afterEach(() => {
  cleanup()
  iframe?.remove()
  vi.restoreAllMocks()
})

// Drop a single anchored pin and let effects settle.
async function dropPin() {
  await act(async () => {
    api.handleStageClick(50, 50, 500, 400, { type: "anchor-id", value: "a1" })
  })
}

// Let the MutationObserver's rAF-debounced recompute run + React re-render.
async function flushObserver() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 30))
  })
}

describe("usePinMarking — pin occlusion by an in-iframe overlay", () => {
  it("renders the pin when its anchor is the topmost element at the pin point", async () => {
    render(React.createElement(Harness))
    await dropPin()
    // topEl === anchorEl (unoccluded) → the pin dot is drawn.
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()
  })

  it("hides the pin when a different element (a modal) is topmost at the pin point", async () => {
    render(React.createElement(Harness))
    // Prove non-vacuous: with the anchor topmost the pin renders…
    await dropPin()
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()
    // …now a modal covers the anchor point → topmost is a different element.
    const modal = doc.createElement("div")
    doc.body.appendChild(modal)
    topEl = modal
    // append fires the MutationObserver → rAF recompute → occluded.
    await flushObserver()
    expect(document.querySelector('[data-testid="da-pin-1"]')).toBeNull()
  })

  it("re-shows the pin when the overlay is removed — observer-driven, no reload", async () => {
    render(React.createElement(Harness))
    await dropPin()
    // open the modal → observer hides the pin.
    const modal = doc.createElement("div")
    doc.body.appendChild(modal)
    topEl = modal
    await flushObserver()
    expect(document.querySelector('[data-testid="da-pin-1"]')).toBeNull()
    // close the modal: remove it + the anchor is topmost again. The removal is an
    // in-iframe mutation the observer reacts to (NOT a page reload) → re-show.
    modal.remove()
    topEl = anchorEl
    await flushObserver()
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()
  })

  it("keeps positioning a visible pin from its anchor on scroll (no anchor-tracking regression)", async () => {
    render(React.createElement(Harness))
    await dropPin()
    expect(api.computedPinPositions[1]).toEqual({ xPct: 50, yPct: 50 })
    // the anchor moved → a scroll on the iframe window re-derives the position.
    bridge.pos = { xPct: 20, yPct: 70 }
    await act(async () => {
      iframe.contentWindow?.dispatchEvent(new Event("scroll"))
    })
    expect(api.computedPinPositions[1]).toEqual({ xPct: 20, yPct: 70 })
    // still visible (anchor topmost) → still rendered.
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()
  })
})

describe("usePinMarking — occlusion same-origin guard + cleanup", () => {
  it("falls back to SHOWING the pin when iframe document access throws (cross-origin)", async () => {
    // Simulate a cross-origin document: contentDocument access throws.
    Object.defineProperty(iframe, "contentDocument", {
      configurable: true,
      get() {
        throw new Error("cross-origin")
      },
    })
    render(React.createElement(Harness))
    // must not throw; the pin still renders (occlusion check swallowed the error).
    await dropPin()
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()
  })

  it("removes scroll/resize/load listeners + disconnects the observer on unmount", async () => {
    const winRemove = vi.spyOn(iframe.contentWindow as Window, "removeEventListener")
    const windowRemove = vi.spyOn(window, "removeEventListener")
    const iframeRemove = vi.spyOn(iframe, "removeEventListener")
    const disconnect = vi.spyOn(MutationObserver.prototype, "disconnect")
    const view = render(React.createElement(Harness))
    await dropPin()
    view.unmount()
    expect(winRemove).toHaveBeenCalledWith("scroll", expect.any(Function))
    expect(windowRemove).toHaveBeenCalledWith("resize", expect.any(Function))
    expect(iframeRemove).toHaveBeenCalledWith("load", expect.any(Function))
    expect(disconnect).toHaveBeenCalled()
  })

  it("re-attaches the observer to the fresh document on an iframe load (swap)", async () => {
    const observe = vi.spyOn(MutationObserver.prototype, "observe")
    render(React.createElement(Harness))
    await dropPin()
    const before = observe.mock.calls.length
    await act(async () => {
      fireEvent.load(iframe)
    })
    // the load handler re-attaches (disconnect old + observe the new doc).
    expect(observe.mock.calls.length).toBeGreaterThan(before)
  })
})

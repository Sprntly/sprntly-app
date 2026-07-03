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

// The live bug: on a COLD load the iframe mounts AFTER the hook's effects run — the
// grant POST + bundle fetch + `<iframe>` mount take LONGER than any fixed frame
// budget, so a bounded rAF-retry EXHAUSTS before the iframe appears and never binds
// (and the load-reattach fallback, wired only after the iframe is found, never
// installs either). The occlusion observer must bind on the iframe-appearance
// SIGNAL, however late — a DOM MutationObserver on document.body — not a bounded
// poll. The other occlusion tests render with the iframe ALREADY present, so they
// never exercise this race. This one models it: no `.da-prototype-iframe` at render,
// appended only AFTER the point a bounded poll would have exhausted.
describe("usePinMarking — occlusion binds when the iframe mounts LATE (cold-load race)", () => {
  // (Re)build the same-origin doc state on the iframe's CURRENT contentDocument
  // (re-appending an iframe can hand back a fresh document in jsdom).
  function rebuildDoc() {
    doc = iframe.contentDocument!
    anchorEl = doc.createElement("div")
    anchorEl.setAttribute("data-anchor-id", "a1")
    doc.body.appendChild(anchorEl)
    bridge.anchorEl = anchorEl
    topEl = anchorEl
    doc.elementFromPoint = () => topEl
  }

  it("binds on the iframe-mount SIGNAL, not a frame poll — the iframe appears with only microtasks flushed (no frame ticks)", async () => {
    // No iframe in the DOM at render → the mount effect must watch for it, not
    // early-return. (beforeEach appended one; detach it to model the fresh viewer.)
    iframe.remove()
    expect(document.querySelector(".da-prototype-iframe")).toBeNull()

    const observe = vi.spyOn(MutationObserver.prototype, "observe")
    // Discriminator: the OCCLUSION observer targets the iframe's contentDocument.
    // The mount watcher targets document.body — exclude it so this asserts the
    // occlusion observer specifically, not the signal detector.
    const occlusionObserverBound = () =>
      observe.mock.calls.some(([t]) => t !== document.body && t !== document)

    render(React.createElement(Harness))
    // Iframe absent → the occlusion observer cannot have bound to any contentDocument
    // (only the document.body mount-watcher is observing).
    expect(occlusionObserverBound()).toBe(false)

    // The iframe mounts LATER (cold grant + bundle finally resolved). Flush ONLY
    // microtasks — NO frame tick (rAF), NO timer. A bounded rAF-poll re-checks for
    // the iframe only on a frame tick, so with no frame allowed it would still be
    // unbound here (RED). The document.body MutationObserver fires on the DOM-insert
    // microtask and binds immediately (GREEN) — proving the bind is tied to the
    // iframe-appearance signal, not a poll.
    await act(async () => {
      document.body.appendChild(iframe)
      rebuildDoc()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(occlusionObserverBound()).toBe(true)

    // Behavioral proof the late-bound observer actually drives occlusion. Drop a
    // pin; anchor is topmost → it renders visible.
    await dropPin()
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()

    // Now a modal covers the anchor point. Append it to the LATE-mounted iframe's
    // document and set it topmost — then flush ONLY the rAF debounce. No scroll
    // dispatch, no manual recompute: the pin can hide ONLY if the observer bound to
    // the late-mounted document and drove scheduleRecompute.
    const modal = doc.createElement("div")
    doc.body.appendChild(modal)
    topEl = modal
    await flushObserver()
    expect(document.querySelector('[data-testid="da-pin-1"]')).toBeNull()
  })
})

// The live "stranded observer" bug: after the initial bind, a grant re-mint bumps a
// reloadKey that feeds the `<iframe>`'s React `key`, so React REPLACES the element
// (old removed + new appended, same URL → a third `load`). The element-level `load`
// listener lived on the OLD element (now dead → no re-bind), and a one-shot mount
// watcher would already be disconnected → the observer strands on the OLD document
// while the live doc is the NEW element's document, so an in-iframe modal never
// reaches it and the pin never auto-hides. The permanent document.body watcher must
// catch the childList remount and re-bind onto the NEW element's document.
describe("usePinMarking — occlusion re-binds when the iframe ELEMENT is replaced (remount)", () => {
  // Build same-origin doc state on the CURRENT iframe's fresh contentDocument and
  // model a real viewport + a topmost element (jsdom does no layout).
  function rebuildDocOn(el: HTMLIFrameElement) {
    iframe = el
    doc = el.contentDocument!
    anchorEl = doc.createElement("div")
    anchorEl.setAttribute("data-anchor-id", "a1")
    doc.body.appendChild(anchorEl)
    bridge.anchorEl = anchorEl
    topEl = anchorEl
    el.getBoundingClientRect = () =>
      ({ width: 1000, height: 800, left: 0, top: 0, right: 1000, bottom: 800, x: 0, y: 0, toJSON() {} }) as DOMRect
    doc.elementFromPoint = () => topEl
  }

  it("re-binds to the NEW element's document after a remount and drives occlusion on it", async () => {
    const observe = vi.spyOn(MutationObserver.prototype, "observe")
    // Count only OCCLUSION observers (target the iframe contentDocument), excluding
    // the document.body mount/remount watcher.
    const occlusionBinds = () =>
      observe.mock.calls.filter(([t]) => t !== document.body && t !== document).length

    render(React.createElement(Harness))
    await dropPin()
    // Bound to the first document; anchor topmost → pin visible.
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()
    const bindsBefore = occlusionBinds()
    expect(bindsBefore).toBeGreaterThanOrEqual(1)

    // REPLACE the iframe ELEMENT: remove the old `.da-prototype-iframe` and append a
    // NEW one with a FRESH contentDocument (React remount / third load to same URL).
    iframe.remove()
    const next = document.createElement("iframe")
    next.className = "da-prototype-iframe"
    document.body.appendChild(next)
    rebuildDocOn(next)

    // Flush ONLY microtasks → the permanent document.body watcher fires on the
    // childList remount and syncBinding re-binds onto the new element+doc. On the
    // old "disconnect-after-first-find + load-listener-on-old-element" wiring the
    // watcher is gone and the load listener is dead, so nothing re-binds (RED).
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(occlusionBinds()).toBeGreaterThan(bindsBefore)

    // Behavioural proof the RE-BOUND observer drives occlusion on the LATEST doc: a
    // modal drawn over the anchor in the NEW document hides the pin, flushing ONLY the
    // rAF debounce (no scroll dispatch, no manual recompute). Stranded on the old doc
    // this mutation never fires → the pin stays visible → the assertion fails (RED).
    const modal = doc.createElement("div")
    doc.body.appendChild(modal)
    topEl = modal
    await flushObserver()
    expect(document.querySelector('[data-testid="da-pin-1"]')).toBeNull()
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

  it("disconnects the iframe-mount watcher on unmount — no bind after teardown", async () => {
    // No iframe at render → the effect installs the document.body mount watcher.
    iframe.remove()
    const observe = vi.spyOn(MutationObserver.prototype, "observe")
    const disconnect = vi.spyOn(MutationObserver.prototype, "disconnect")
    const view = render(React.createElement(Harness))
    // the mount watcher is observing document.body (the only observer bound so far).
    expect(observe.mock.calls.some(([t]) => t === document.body)).toBe(true)

    view.unmount()
    // cleanup disconnects the watcher (and cancels the effect).
    expect(disconnect).toHaveBeenCalled()

    // proof the watcher is truly dead: a late iframe insertion must NOT bind now.
    const before = observe.mock.calls.length
    document.body.appendChild(iframe)
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(observe.mock.calls.length).toBe(before)
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

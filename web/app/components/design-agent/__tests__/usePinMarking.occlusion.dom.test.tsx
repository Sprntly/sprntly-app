// @vitest-environment jsdom
//
// Comment-pin OCCLUSION behaviour for the shared pin engine. A comment pin is a
// parent-app overlay (z-index above the same-origin prototype iframe). When an
// in-iframe modal is drawn OVER the pin's anchored element the pin must HIDE (not
// float on top of the modal); when the modal closes the pin must RE-SHOW — driven
// by a MutationObserver on the iframe document, not a page reload.
//
// The lifecycle is three cleanly-separated concerns:
//   1. iframe lifecycle binding — keep ONE content observer bound to the live doc
//      across late mount, React-key remount, and same-URL document swap.
//   2. the content observer's mutation handler — ALWAYS schedule a recompute.
//   3. the occlusion recompute — hide/show pins by elementFromPoint.
//
// Exercised on real jsdom because the behaviour depends on effects, a
// MutationObserver on the iframe `contentDocument`, and elementFromPoint. jsdom
// does no layout, so the anchor-position bridge + getBoundingClientRect +
// elementFromPoint are stubbed to model "topmost element at the pin's point"; the
// hook's binding lifecycle + occlusion decision under test are the real code.
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

// jsdom does no layout: model a real iframe viewport + a topmost element on the
// given element's CURRENT contentDocument (re-appending an iframe can hand back a
// fresh document in jsdom).
function wireDoc(el: HTMLIFrameElement) {
  iframe = el
  doc = el.contentDocument!
  anchorEl = doc.createElement("div")
  anchorEl.setAttribute("data-anchor-id", "a1")
  doc.body.appendChild(anchorEl)
  bridge.pos = { xPct: 50, yPct: 50 }
  bridge.anchorEl = anchorEl
  topEl = anchorEl // unoccluded by default
  el.getBoundingClientRect = () =>
    ({ width: 1000, height: 800, left: 0, top: 0, right: 1000, bottom: 800, x: 0, y: 0, toJSON() {} }) as DOMRect
  doc.elementFromPoint = () => topEl
}

beforeEach(() => {
  const el = document.createElement("iframe")
  el.className = "da-prototype-iframe"
  document.body.appendChild(el)
  wireDoc(el)
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

// Let the content observer's rAF-debounced recompute run + React re-render.
async function flushObserver() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 30))
  })
}

// Flush ONLY microtasks — NO frame tick (rAF), NO timer. Proves a bind is driven
// by the mount SIGNAL (the microtask-batched body MutationObserver), not a poll.
async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

// Only the OCCLUSION (content) observers target the iframe's contentDocument; the
// Concern-1 mount/remount watcher targets document.body. This discriminator counts
// content observers so tests assert the occlusion binding, not the signal detector.
function contentObserverBinds(observe: ReturnType<typeof vi.spyOn>): number {
  return observe.mock.calls.filter(([t]) => t !== document.body && t !== document).length
}

describe("usePinMarking — comment-pin occlusion lifecycle", () => {
  // ── Concern 1: binding across every way the iframe can appear/change ──

  it("warm mount — binds to an iframe already present at render (sync initial check)", async () => {
    const observe = vi.spyOn(MutationObserver.prototype, "observe")
    render(React.createElement(Harness))
    // The iframe was in the DOM before render → the synchronous initial check binds
    // the content observer to its document immediately.
    expect(contentObserverBinds(observe)).toBeGreaterThanOrEqual(1)
    // And it drives occlusion: a modal over the anchor hides the pin.
    await dropPin()
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()
    const modal = doc.createElement("div")
    doc.body.appendChild(modal)
    topEl = modal
    await flushObserver()
    expect(document.querySelector('[data-testid="da-pin-1"]')).toBeNull()
  })

  it("late cold mount — binds on the iframe-appearance SIGNAL, not a frame poll", async () => {
    // Model a fresh viewer: no `.da-prototype-iframe` at render. A COLD load (grant
    // POST + bundle fetch + `<iframe>` mount) takes longer than any fixed frame
    // budget, so a bounded poll would exhaust before the iframe appears. The bind
    // must instead ride the DOM-insert signal, however late.
    iframe.remove()
    expect(document.querySelector(".da-prototype-iframe")).toBeNull()

    const observe = vi.spyOn(MutationObserver.prototype, "observe")
    render(React.createElement(Harness))
    // Iframe absent → no content observer can have bound (only the body mount-watcher).
    expect(contentObserverBinds(observe)).toBe(0)

    // The iframe mounts LATER. Flush ONLY microtasks — NO frame tick. A poll re-checks
    // only on a frame tick, so with no frame allowed it would still be unbound (RED);
    // the document.body MutationObserver fires on the DOM-insert microtask and binds
    // immediately (GREEN) — proving the bind is signal-driven, not frame-polled.
    const late = document.createElement("iframe")
    late.className = "da-prototype-iframe"
    await act(async () => {
      document.body.appendChild(late)
      wireDoc(late)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(contentObserverBinds(observe)).toBeGreaterThanOrEqual(1)

    // The late-bound observer actually drives occlusion on the late-mounted document.
    await dropPin()
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()
    const modal = doc.createElement("div")
    doc.body.appendChild(modal)
    topEl = modal
    await flushObserver()
    expect(document.querySelector('[data-testid="da-pin-1"]')).toBeNull()
  })

  it("element remount (React key bump) — re-binds to the NEW element's document", async () => {
    const observe = vi.spyOn(MutationObserver.prototype, "observe")
    render(React.createElement(Harness))
    await dropPin()
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()
    const before = contentObserverBinds(observe)
    expect(before).toBeGreaterThanOrEqual(1)

    // A grant re-mint bumps the iframe's React `key` → React REPLACES the element
    // (old removed + new appended with a FRESH contentDocument). The permanent
    // document.body watcher catches the childList remount and re-binds onto the new
    // element+doc. Flush ONLY microtasks (no frame). Stranded on the old wiring the
    // watcher/load-listener are dead → nothing re-binds (RED).
    iframe.remove()
    const next = document.createElement("iframe")
    next.className = "da-prototype-iframe"
    document.body.appendChild(next)
    wireDoc(next)
    await flushMicrotasks()
    expect(contentObserverBinds(observe)).toBeGreaterThan(before)

    // The RE-BOUND observer drives occlusion on the LATEST doc: a modal in the NEW
    // document hides the pin, flushing ONLY the rAF debounce (no scroll, no manual
    // recompute). Stranded on the old doc this mutation never fires → pin stays (RED).
    const modal = doc.createElement("div")
    doc.body.appendChild(modal)
    topEl = modal
    await flushObserver()
    expect(document.querySelector('[data-testid="da-pin-1"]')).toBeNull()
  })

  it("same-URL doc swap — re-binds to the fresh document on an iframe load", async () => {
    const observe = vi.spyOn(MutationObserver.prototype, "observe")
    render(React.createElement(Harness))
    await dropPin()
    const before = contentObserverBinds(observe)

    // Same ELEMENT, its document replaced in place (in-page navigation to the same
    // URL) → the element-level `load` handler re-attaches (disconnect old + observe
    // the new doc).
    await act(async () => {
      fireEvent.load(iframe)
    })
    expect(contentObserverBinds(observe)).toBeGreaterThan(before)
  })

  // ── Concern 2 → 3: the mutation → observer → recompute → hide/show chain ──

  it("modal open HIDES the pin — driven THROUGH the content observer (no direct recompute)", async () => {
    render(React.createElement(Harness))
    // Non-vacuous: with the anchor topmost the pin renders…
    await dropPin()
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()

    // …now a modal covers the anchor point. Append it to the bound contentDocument
    // and set it topmost, then flush ONLY the content observer's rAF debounce — NO
    // scroll dispatch, NO direct recompute call. The pin can hide ONLY if the content
    // observer fired on the appendChild → scheduled → recomputed. This is the exact
    // chain that a binding-sync-conflated handler left dead.
    const modal = doc.createElement("div")
    doc.body.appendChild(modal)
    topEl = modal
    await flushObserver()
    expect(document.querySelector('[data-testid="da-pin-1"]')).toBeNull()
  })

  it("modal close RE-SHOWS the pin — observer-driven, no reload", async () => {
    render(React.createElement(Harness))
    await dropPin()
    const modal = doc.createElement("div")
    doc.body.appendChild(modal)
    topEl = modal
    await flushObserver()
    expect(document.querySelector('[data-testid="da-pin-1"]')).toBeNull()

    // Close the modal: its removal is another in-iframe mutation the content observer
    // reacts to (NOT a page reload) → the anchor is topmost again → the pin re-shows.
    modal.remove()
    topEl = anchorEl
    await flushObserver()
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()
  })

  it("a second content mutation while a recompute frame is pending CANCELS the prior frame and reschedules (not first-wins)", async () => {
    render(React.createElement(Harness))
    await dropPin()
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()
    // Settle any real frame so no recompute is pending before we freeze rAF.
    await flushObserver()

    // Freeze frames: rAF hands back an ever-increasing handle but NEVER invokes its
    // callback, so a scheduled recompute stays PENDING across both mutations. The
    // handle is nulled only inside that (never-firing) callback → this reproduces the
    // "pending frame that never flushes" state the wedge got permanently stuck in.
    let fakeHandle = 0
    const rafSpy = vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => ++fakeHandle)
    const cafSpy = vi.spyOn(window, "cancelAnimationFrame")

    // First in-iframe mutation → the content observer fires on the microtask →
    // scheduleRecompute schedules frame #1 (rafRecompute now a non-null, never-firing
    // handle). Flush ONLY microtasks — no timer, no frame — so the frame stays pending.
    await act(async () => {
      doc.body.appendChild(doc.createElement("div"))
      await Promise.resolve()
      await Promise.resolve()
    })
    const firstHandle = fakeHandle
    expect(rafSpy).toHaveBeenCalled()
    expect(firstHandle).toBeGreaterThan(0)
    expect(cafSpy).not.toHaveBeenCalled()

    // Second mutation while frame #1 is STILL pending → the observer fires again →
    // scheduleRecompute. Cancel-and-reschedule CANCELS handle #1 and schedules a fresh
    // frame, so the recompute always gets a live frame. A first-wins guard
    // (`if (rafRecompute != null) return`) would instead bail here and NEVER call
    // cancelAnimationFrame — the exact wedge that leaves the occlusion recompute dead.
    await act(async () => {
      doc.body.appendChild(doc.createElement("div"))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(cafSpy).toHaveBeenCalledWith(firstHandle)

    rafSpy.mockRestore()
    cafSpy.mockRestore()
  })

  it("a pending recompute frame that was cancelled without firing does not wedge the occlusion hide", async () => {
    render(React.createElement(Harness))
    await dropPin()
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()
    // Settle any real frame so no recompute is pending before we model frames ourselves.
    await flushObserver()

    // rAF mock that MODELS CANCELLATION: a cancelled handle's callback does NOT fire on
    // flush. This reproduces the exact live wedge — a recompute frame stranded (cancelled
    // WITHOUT firing) by an iframe re-bind / StrictMode teardown — as an in-suite symptom.
    const handles = new Map<number, { cb: FrameRequestCallback; cancelled: boolean }>()
    let nextId = 1
    const rafSpy = vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      const id = nextId++
      handles.set(id, { cb, cancelled: false })
      return id
    })
    const cafSpy = vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
      const h = handles.get(id)
      if (h) h.cancelled = true
    })
    const flushRaf = () => {
      for (const [id, h] of [...handles]) {
        handles.delete(id)
        if (!h.cancelled) h.cb(performance.now())
      }
    }

    // STRAND a handle: one benign in-iframe mutation fires the content observer →
    // scheduleRecompute schedules a frame (the hook's internal rafRecompute is now set).
    // Then simulate the teardown/re-bind that CANCELS that frame without firing it — the
    // hook does NOT null its internal handle, so rafRecompute is left a dead non-null
    // value. That is the wedge that stayed invisible to mechanism-only assertions.
    await act(async () => {
      doc.body.appendChild(doc.createElement("div"))
      await Promise.resolve()
      await Promise.resolve()
    })
    const strandedId = rafSpy.mock.results[rafSpy.mock.results.length - 1]!.value as number
    window.cancelAnimationFrame(strandedId)

    // The REAL modal mutation: a modal covers the anchor point (topmost). The content
    // observer fires → scheduleRecompute. Cancel-and-reschedule cancels the stranded
    // handle (a no-op) and schedules a FRESH frame; first-wins bails on the dead non-null
    // handle and schedules nothing.
    const modal = doc.createElement("div")
    doc.body.appendChild(modal)
    topEl = modal
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    // Flush frames: the stranded/cancelled handle is skipped; only a freshly scheduled
    // frame runs the recompute. Fixed → recompute runs → the pin HIDES (symptom cured).
    // First-wins → no fresh frame → the recompute never runs → the pin STAYS (the exact
    // live symptom that passed every plumbing test yet failed in the browser).
    await act(async () => {
      flushRaf()
    })
    expect(document.querySelector('[data-testid="da-pin-1"]')).toBeNull()

    rafSpy.mockRestore()
    cafSpy.mockRestore()
  })

  // ── Concern 3 preserved: anchor tracking + the show-on-null fallback ──

  it("scroll no-regression — a visible pin still repositions from its anchor", async () => {
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

  it("shows the pin when elementFromPoint returns null (off-viewport / unresolvable)", async () => {
    render(React.createElement(Harness))
    await dropPin()
    // A null topmost must NEVER be treated as an occlusion → the pin stays visible.
    topEl = null
    await flushObserver()
    expect(document.querySelector('[data-testid="da-pin-1"]')).not.toBeNull()
  })

  // ── Same-origin guard + teardown ──

  it("cross-origin guard — falls back to SHOWING the pin when document access throws", async () => {
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

  it("cleanup — unmount disconnects both observers, removes listeners, cancels the frame", async () => {
    const winRemove = vi.spyOn(iframe.contentWindow as Window, "removeEventListener")
    const windowRemove = vi.spyOn(window, "removeEventListener")
    const iframeRemove = vi.spyOn(iframe, "removeEventListener")
    const disconnect = vi.spyOn(MutationObserver.prototype, "disconnect")
    const cancelFrame = vi.spyOn(window, "cancelAnimationFrame")
    const view = render(React.createElement(Harness))
    await dropPin()

    // Leave a recompute pending so cleanup has a frame to cancel: the in-iframe
    // mutation fires the content observer (microtask) which SCHEDULES the rAF; flush
    // ONLY microtasks so the frame is still pending (not yet run) at unmount.
    const modal = doc.createElement("div")
    doc.body.appendChild(modal)
    topEl = modal
    await flushMicrotasks()

    view.unmount()
    expect(winRemove).toHaveBeenCalledWith("scroll", expect.any(Function))
    expect(windowRemove).toHaveBeenCalledWith("resize", expect.any(Function))
    expect(iframeRemove).toHaveBeenCalledWith("load", expect.any(Function))
    expect(disconnect).toHaveBeenCalled() // content observer + body watcher
    expect(cancelFrame).toHaveBeenCalled()
  })

  it("cleanup — the mount watcher is dead after unmount (no bind on a late iframe)", async () => {
    // No iframe at render → the effect installs the document.body mount watcher.
    iframe.remove()
    const observe = vi.spyOn(MutationObserver.prototype, "observe")
    const disconnect = vi.spyOn(MutationObserver.prototype, "disconnect")
    const view = render(React.createElement(Harness))
    // the mount watcher is observing document.body (the only observer bound so far).
    expect(observe.mock.calls.some(([t]) => t === document.body)).toBe(true)

    view.unmount()
    expect(disconnect).toHaveBeenCalled()

    // proof the watcher is truly dead: a late iframe insertion must NOT bind now.
    const before = observe.mock.calls.length
    const late = document.createElement("iframe")
    late.className = "da-prototype-iframe"
    document.body.appendChild(late)
    await flushMicrotasks()
    expect(observe.mock.calls.length).toBe(before)
    late.remove()
  })
})

// @vitest-environment jsdom
//
// The artifact panel's left edge is a drag handle. Two things were wrong with
// it and both are locked down here:
//
//   1. it opened at 60% of the viewport, leaving the thread the smaller half.
//      Unresized it now opens at 35% (a CSS default the component leaves
//      alone), and the first drag has to seed from that same number or the
//      panel jumps the moment you grab it.
//   2. the gesture leaked. Mouse events were used, and the panel body hosts
//      iframes — so once the widening panel's edge slid under the cursor the
//      iframe ate the stream: the panel froze mid-drag, and the swallowed
//      mouseup left the session alive, so it resumed tracking the cursor after
//      the button was released. Pointer capture ends the gesture exactly once.
//
// Smoothness is the third axis: writes are coalesced onto one animation frame,
// because each one re-lays out the panel AND the thread column's padding.
import * as React from "react"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

vi.mock("../PrdPanelContent", () => ({
  PrdPanelContent: () => React.createElement("div", { "data-testid": "prd-body" }),
}))

const navMock = vi.hoisted(() => ({
  tab: "prd" as "prd" | "evidence" | "tickets" | null,
  openContentPanel: vi.fn(),
  closeContentPanel: vi.fn(),
}))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    contentPanelTab: navMock.tab,
    openContentPanel: navMock.openContentPanel,
    closeContentPanel: navMock.closeContentPanel,
    showToast: vi.fn(),
    expandAiPanel: vi.fn(),
    setAIBarValue: vi.fn(),
  }),
}))

const contentMock = vi.hoisted(() => ({ value: {} as Record<string, unknown> }))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: vi.fn() }),
}))

import { ContentPanel } from "../ContentPanel"
import type { PrdState } from "../../../types/content"

const prd: PrdState = { prd_id: 1, title: "Scheduled Send", metaLine: "", sections: [], source: "brief" }

const STORAGE_KEY = "sprntly-cpanel-width-v2"
// jsdom's viewport. 35% = 358px, the 60% cap = 614px, the floor = 420px.
const VIEWPORT = 1024
const PAST_ANIMATION = 400

const handle = () => document.querySelector<HTMLElement>(".cpanel-resize-handle")!
const width = () => document.documentElement.style.getPropertyValue("--cpanel-width")
const resizing = () => document.documentElement.classList.contains("cpanel-resizing")

/** jsdom ships no PointerEvent; the handler only reads button / clientX /
 *  pointerId, so a MouseEvent dispatched under the pointer type name drives it
 *  exactly like the real thing. */
function pointer(el: EventTarget, type: string, clientX = 0) {
  const e = new MouseEvent(type, { bubbles: true, cancelable: true, clientX, button: 0 })
  act(() => { el.dispatchEvent(e) })
  return e
}

// Hand-rolled rAF so a frame only runs when the test says so — that's what
// makes the coalescing assertions exact rather than timing-dependent.
let frames: Array<FrameRequestCallback | null> = []
const runFrame = () => act(() => { frames.splice(0).forEach((cb) => cb?.(0)) })

const capture = { set: vi.fn(), release: vi.fn() }

function renderOpen() {
  navMock.tab = "prd"
  contentMock.value = { prd, evidence: null, evidenceGenerating: false }
  const utils = render(React.createElement(ContentPanel))
  act(() => { vi.advanceTimersByTime(PAST_ANIMATION) })
  return utils
}

/** Grab the handle at `from` and drag to `to`, flushing one frame per move. */
function drag(from: number, ...to: number[]) {
  pointer(handle(), "pointerdown", from)
  for (const x of to) { pointer(window, "pointermove", x); runFrame() }
}

beforeEach(() => {
  vi.useFakeTimers()
  frames = []
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb))
  vi.stubGlobal("cancelAnimationFrame", (id: number) => { frames[id - 1] = null })
  vi.stubGlobal("innerWidth", VIEWPORT)
  // jsdom implements neither, and the capture call is the whole iframe fix.
  Element.prototype.setPointerCapture = capture.set
  Element.prototype.releasePointerCapture = capture.release
  localStorage.clear()
})

afterEach(() => {
  vi.runOnlyPendingTimers()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  cleanup()
  document.documentElement.style.removeProperty("--cpanel-width")
  document.documentElement.classList.remove("cpanel-resizing")
  capture.set.mockClear()
  capture.release.mockClear()
  navMock.tab = "prd"
})

describe("ContentPanel — default width", () => {
  it("leaves the width to CSS until the user resizes, so it opens at 35%", () => {
    renderOpen()
    // No inline var → the stylesheet's `clamp(420px, 35vw, 60vw)` governs, which
    // is the 35/65 split. Any px written here would override it.
    expect(width()).toBe("")
  })

  it("ignores widths saved before the 35% default", () => {
    // Old key, old bounds: dragged against a 60vw default and a 650px floor.
    localStorage.setItem("sprntly-cpanel-width", "900")
    renderOpen()
    expect(width()).toBe("")
  })

  it("restores a width the user chose under the current bounds", () => {
    localStorage.setItem(STORAGE_KEY, "500")
    renderOpen()
    expect(width()).toBe("500px")
  })

  it("seeds the first drag from the 35% default, not from the old 60%", () => {
    renderOpen()
    // 35% of 1024 = 358; dragging 100px left widens it to 458. Seeding from the
    // old 60vw would have jumped the edge to 714 on the first pixel of travel.
    drag(600, 500)
    expect(width()).toBe("458px")
  })
})

describe("ContentPanel — resize drag", () => {
  it("widens as the pointer travels left and narrows as it travels right", () => {
    localStorage.setItem(STORAGE_KEY, "500")
    renderOpen()

    drag(600, 550)
    expect(width()).toBe("550px")

    pointer(window, "pointermove", 620)
    runFrame()
    expect(width()).toBe("480px")
  })

  it("captures the pointer so an iframe under the cursor can't steal the drag", () => {
    renderOpen()
    pointer(handle(), "pointerdown", 600)
    // Without this the PRD/report frames swallow the rest of the gesture.
    expect(capture.set).toHaveBeenCalledTimes(1)

    pointer(window, "pointerup", 500)
    expect(capture.release).toHaveBeenCalledTimes(1)
  })

  it("stops resizing the moment the pointer is released", () => {
    localStorage.setItem(STORAGE_KEY, "500")
    renderOpen()

    drag(600, 550)
    pointer(window, "pointerup", 550)

    // The reported bug: a swallowed release left the session live, so the panel
    // carried on following the cursor with no button held.
    pointer(window, "pointermove", 200)
    runFrame()
    expect(width()).toBe("550px")
    expect(resizing()).toBe(false)
  })

  it("ends the drag when the pointer is cancelled instead of released", () => {
    localStorage.setItem(STORAGE_KEY, "500")
    renderOpen()

    drag(600, 550)
    pointer(window, "pointercancel", 550)

    pointer(window, "pointermove", 200)
    runFrame()
    expect(width()).toBe("550px")
    expect(resizing()).toBe(false)
  })

  it("coalesces a burst of moves into a single frame, landing on the last one", () => {
    localStorage.setItem(STORAGE_KEY, "500")
    renderOpen()

    pointer(handle(), "pointerdown", 600)
    pointer(window, "pointermove", 590)
    pointer(window, "pointermove", 580)
    pointer(window, "pointermove", 570)

    // Three moves, one queued frame — each write re-lays out the panel and the
    // thread column, so doing it per event is what made the drag feel heavy.
    expect(frames.length).toBe(1)
    runFrame()
    expect(width()).toBe("530px")
  })

  it("lands the final position even when the release beats the frame", () => {
    localStorage.setItem(STORAGE_KEY, "500")
    renderOpen()

    pointer(handle(), "pointerdown", 600)
    pointer(window, "pointermove", 560)   // queued, not yet flushed
    pointer(window, "pointerup", 560)

    // Released mid-frame: the pending move is applied rather than dropped, so
    // the edge doesn't settle a frame short of where the cursor let go.
    expect(width()).toBe("540px")
    expect(localStorage.getItem(STORAGE_KEY)).toBe("540")
  })

  it("clamps between the floor and 60% of the viewport", () => {
    renderOpen()

    drag(600, -400)                       // hauled far past the left edge
    expect(width()).toBe("614px")         // 60% of 1024

    pointer(window, "pointermove", 2000)  // hauled far past the right edge
    runFrame()
    expect(width()).toBe("420px")
  })

  it("marks the document while dragging so transitions and selection are off", () => {
    renderOpen()
    pointer(handle(), "pointerdown", 600)
    expect(resizing()).toBe(true)

    pointer(window, "pointerup", 600)
    expect(resizing()).toBe(false)
  })

  it("ends a drag the panel unmounts out from under", () => {
    localStorage.setItem(STORAGE_KEY, "500")
    const { unmount } = renderOpen()

    drag(600, 550)
    // Closing the panel (or navigating) mid-gesture: the session has to go with
    // it, or its window listeners keep resizing a panel that isn't there.
    act(() => { unmount() })

    expect(resizing()).toBe(false)
    expect(localStorage.getItem(STORAGE_KEY)).toBe("550")
    pointer(window, "pointermove", 200)
    expect(frames.length).toBe(0)
  })

  it("ignores a non-primary button", () => {
    renderOpen()
    const e = new MouseEvent("pointerdown", { bubbles: true, cancelable: true, clientX: 600, button: 2 })
    act(() => { handle().dispatchEvent(e) })

    expect(resizing()).toBe(false)
    expect(capture.set).not.toHaveBeenCalled()
  })
})

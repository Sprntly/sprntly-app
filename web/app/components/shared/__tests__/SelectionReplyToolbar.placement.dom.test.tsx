// @vitest-environment jsdom
//
// Where the "Reply" pill lands.
//
// Reported as a Reply button at the bottom-left of the screen, nowhere near the
// highlighted words and wearing the browser's default button chrome. Two
// separate ways placement can go wrong, both pinned here:
//
//   * the pill depends on `position: fixed` to be over the selection at all,
//     and a CSS-module class is not a load-bearing thing to depend on for that
//     — without it the coordinates are inert and the box falls into the flex
//     column just above the composer, at its left edge;
//   * the coordinates themselves were never clamped, so a passage on the first
//     visible line anchored the pill off the top of the screen.
import * as React from "react"
import { act, cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { SelectionReplyToolbar } from "../SelectionReplyToolbar"

/** A transcript with one agent body, and a selection over part of it. */
function mount() {
  const host = document.createElement("div")
  host.innerHTML = `<div class="bc-agent-body"><p id="p">MIME-type sniffing attacks</p></div>`
  document.body.appendChild(host)
  const ref = { current: host }

  const view = render(<SelectionReplyToolbar containerRef={ref} onReply={vi.fn()} />)
  return { host, view }
}

/** Select inside the paragraph, with `rect` as what the range reports. */
function selectWithRect(rect: Partial<DOMRect>) {
  const p = document.getElementById("p")!
  const range = document.createRange()
  range.selectNodeContents(p)
  range.getBoundingClientRect = () =>
    ({ top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0, ...rect }) as DOMRect
  const sel = window.getSelection()!
  sel.removeAllRanges()
  sel.addRange(range)
  act(() => {
    fireEvent.mouseUp(document)
  })
}

const toolbar = () => document.querySelector('[data-testid="selection-reply-toolbar"]') as HTMLElement

beforeEach(() => {
  Object.defineProperty(window, "innerWidth", { value: 1200, configurable: true })
  Object.defineProperty(window, "innerHeight", { value: 800, configurable: true })
})

afterEach(() => {
  cleanup()
  document.body.innerHTML = ""
})

describe("the pill floats", () => {
  it("is fixed inline, not only by its class", () => {
    // THE REPORTED BUG. A static box ignores `top`/`left` and lands wherever
    // the flex column puts it — bottom-left, above the composer. The class can
    // fail to arrive; the placement must not depend on it.
    mount()
    selectWithRect({ top: 400, left: 600, width: 80, height: 18, bottom: 418 })

    expect(toolbar().style.position).toBe("fixed")
  })

  it("centres on the selection", () => {
    mount()
    selectWithRect({ top: 400, left: 600, width: 80, height: 18, bottom: 418 })

    // The element's own transform pulls it back by half its width.
    expect(toolbar().style.left).toBe("640px")
    expect(toolbar().style.top).toBe("400px")
  })
})

describe("and stays on screen", () => {
  it("drops below a passage too near the top to sit above", () => {
    // Unclamped this anchors at top: 4 and the pill, drawn a row higher, is
    // off the screen entirely — the reader highlights and sees nothing.
    mount()
    selectWithRect({ top: 4, left: 600, width: 80, height: 18, bottom: 22 })

    expect(Number.parseFloat(toolbar().style.top)).toBeGreaterThan(22)
  })

  it("keeps a selection at the left edge fully visible", () => {
    mount()
    selectWithRect({ top: 400, left: 0, width: 20, height: 18, bottom: 418 })

    // Centred on x=10 the pill would hang off the left of the window.
    expect(Number.parseFloat(toolbar().style.left)).toBeGreaterThan(10)
  })

  it("keeps a selection at the right edge fully visible", () => {
    mount()
    selectWithRect({ top: 400, left: 1180, width: 20, height: 18, bottom: 418 })

    expect(Number.parseFloat(toolbar().style.left)).toBeLessThan(1190)
  })

  it("still places something when the rect is unusable", () => {
    // jsdom, and any engine mid-layout, can hand back nothing. The TEXT is
    // what the feature is about, so the offer survives — on screen.
    mount()
    selectWithRect({})

    const el = toolbar()
    expect(el).toBeTruthy()
    expect(Number.parseFloat(el.style.top)).toBeGreaterThan(0)
    expect(Number.parseFloat(el.style.left)).toBeGreaterThan(0)
  })
})

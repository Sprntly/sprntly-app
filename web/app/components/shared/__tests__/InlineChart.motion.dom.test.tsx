// @vitest-environment jsdom
//
// Chart motion, and the rule it must never break: THE DATA IS NOT THE
// ANIMATION. Every reveal here is an override that applies only while the
// figure is unrevealed; the resting state is the finished chart. A browser
// without IntersectionObserver, a printed page, a screenshot, or a reader who
// has asked for reduced motion all get real bars — not an empty frame waiting
// for an event that never comes.
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import * as React from "react"
import { act, cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { CHART_COLORS, InlineChart } from "../InlineChart"

const DATA = [
  { label: "Storage layer", value: 12 },
  { label: "Chat generation", value: 8 },
  { label: "Editor", value: 3 },
]

/** Swap IntersectionObserver for one this test drives by hand. */
function stubObserver() {
  const instances: { cb: IntersectionObserverCallback; disconnect: () => void }[] = []
  class Stub {
    cb: IntersectionObserverCallback
    disconnect = vi.fn()
    constructor(cb: IntersectionObserverCallback) {
      this.cb = cb
      instances.push(this)
    }
    observe = vi.fn()
    unobserve = vi.fn()
    takeRecords = vi.fn(() => [])
    root = null
    rootMargin = ""
    thresholds = []
  }
  ;(globalThis as Record<string, unknown>).IntersectionObserver = Stub
  return instances
}

function setReducedMotion(reduce: boolean) {
  ;(window as Window & typeof globalThis).matchMedia = ((q: string) => ({
    matches: reduce && q.includes("prefers-reduced-motion"),
    media: q,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    onchange: null,
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia
}

beforeEach(() => setReducedMotion(false))
afterEach(() => {
  cleanup()
  delete (globalThis as Record<string, unknown>).IntersectionObserver
})

describe("a chart reveals when it is scrolled to", () => {
  it("starts unrevealed, and flips when the card intersects", () => {
    const observers = stubObserver()
    const { container } = render(<InlineChart kind="bar" data={DATA} />)
    const figure = container.querySelector(".prd-chart")!
    expect(figure.getAttribute("data-revealed")).toBe("false")

    act(() => {
      observers[0].cb(
        [{ isIntersecting: true } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      )
    })

    expect(figure.getAttribute("data-revealed")).toBe("true")
  })

  it("fires once and lets go of the observer", () => {
    // A bar that re-grows every time it scrolls past is a distraction the
    // second time and a fault the tenth.
    const observers = stubObserver()
    render(<InlineChart kind="bar" data={DATA} />)
    act(() => {
      observers[0].cb(
        [{ isIntersecting: true } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      )
    })
    expect(observers[0].disconnect).toHaveBeenCalled()
  })

  it("shows the finished chart when there is no observer at all", () => {
    // Old browsers, jsdom, a print path. The reveal is decoration; the data is
    // not, and an empty frame is worse than no animation.
    delete (globalThis as Record<string, unknown>).IntersectionObserver
    const { container } = render(<InlineChart kind="bar" data={DATA} />)
    expect(container.querySelector(".prd-chart")!.getAttribute("data-revealed")).toBe("true")
  })

  it("skips the animation entirely for reduced motion — no observer is made", () => {
    // Someone who asked their OS for less motion is not asking for a slower
    // reveal. They are asking for none.
    setReducedMotion(true)
    const observers = stubObserver()
    const { container } = render(<InlineChart kind="bar" data={DATA} />)
    expect(container.querySelector(".prd-chart")!.getAttribute("data-revealed")).toBe("true")
    expect(observers).toHaveLength(0)
  })
})

describe("the marks carry their data whatever the reveal is doing", () => {
  it("bars are their real width in the DOM before any animation runs", () => {
    // The width is set from the data and animated by CSS from 0. If the
    // component ever set width: 0 in JS instead, a screenshot or a
    // no-JS-animation environment would render an empty chart.
    const { container } = render(<InlineChart kind="bar" data={DATA} />)
    const fills = container.querySelectorAll<HTMLElement>(".prd-bar-fill")
    expect(fills).toHaveLength(3)
    expect(fills[0].style.width).toBe("100%")   // the max
    expect(fills[2].style.width).toBe("25%")    // 3 of 12
  })

  it("staggers the rows so the ranking reads in order", () => {
    const { container } = render(<InlineChart kind="bar" data={DATA} />)
    const delays = Array.from(
      container.querySelectorAll<HTMLElement>(".prd-bar-fill"),
    ).map((el) => el.style.transitionDelay)
    expect(delays).toEqual(["0ms", "60ms", "120ms"])
  })

  it("normalises the line's draw with pathLength, so shape does not change the timing", () => {
    const { container } = render(<InlineChart kind="line" data={DATA} />)
    const path = container.querySelector(".prd-line-path")!
    expect(path.getAttribute("pathLength")).toBe("1")
  })

  it("draws markers at the spec size with a surface ring", () => {
    // >= 8px across (r >= 4), so a dot on a gridline is still its own mark.
    const { container } = render(<InlineChart kind="line" data={DATA} />)
    for (const dot of container.querySelectorAll(".prd-line-dot")) {
      expect(Number(dot.getAttribute("r"))).toBeGreaterThanOrEqual(4)
    }
  })
})

describe("the series palette", () => {
  it("is the validated set, in fixed order", () => {
    // Chosen by validator, not by eye: the previous eight failed the lightness
    // band (three washed out on white) and five fell under 3:1 on the surface.
    // Order is fixed because colour follows the ENTITY — a filter that drops a
    // series must not repaint the survivors.
    expect(CHART_COLORS.slice(0, 3)).toEqual(["#2a78d6", "#eb6834", "#1baf7a"])
    expect(CHART_COLORS).toHaveLength(8)
  })

  it("carries no colour twice", () => {
    expect(new Set(CHART_COLORS).size).toBe(CHART_COLORS.length)
  })

  it("keeps a visible value beside every bar — the relief the palette needs", () => {
    // Three slots sit under 3:1 against a light surface, which is only allowed
    // with relief: a visible label, so identity never rests on colour alone.
    const { container } = render(<InlineChart kind="bar" data={DATA} />)
    const values = Array.from(container.querySelectorAll(".prd-bar-val")).map(
      (el) => el.textContent,
    )
    expect(values).toEqual(["12", "8", "3"])
  })
})


describe("a donut can be read, not just looked at", () => {
  const SHARE = [
    { label: "To Do", value: 20 },
    { label: "In Progress", value: 8 },
    { label: "In Review", value: 6 },
  ]

  it("holds the total in the hole until something is pointed at", () => {
    // A share chart otherwise makes the reader add the slices up.
    const { container } = render(<InlineChart kind="donut" data={SHARE} />)
    expect(container.querySelector(".prd-pie-center-val")?.textContent).toBe("34")
    expect(container.querySelector(".prd-pie-center-lbl")?.textContent).toBe("total")
  })

  it("reads out the slice under the pointer, in the same place", () => {
    const { container } = render(<InlineChart kind="donut" data={SHARE} />)
    const slices = container.querySelectorAll(".prd-pie-slice")
    fireEvent.mouseEnter(slices[1])

    expect(container.querySelector(".prd-pie-center-val")?.textContent).toBe("8")
    expect(container.querySelector(".prd-pie-center-lbl")?.textContent).toBe(
      "In Progress · 24%",
    )
  })

  it("dims the others with fill-opacity, never opacity", () => {
    // `opacity` belongs to the reveal. Two rules on one property fight the
    // moment a reader hovers mid-animation.
    const { container } = render(<InlineChart kind="donut" data={SHARE} />)
    const slices = container.querySelectorAll(".prd-pie-slice")
    fireEvent.mouseEnter(slices[0])

    expect(slices[0].getAttribute("fill-opacity")).toBe("1")
    expect(slices[1].getAttribute("fill-opacity")).toBe("0.28")
    expect(slices[1].getAttribute("opacity")).toBeNull()
  })

  it("is reachable from the keyboard through the legend", () => {
    // The ring itself cannot be tabbed to. The legend is the way in, and
    // focusing a row lights its slice exactly as hovering does.
    const { container, getAllByRole } = render(<InlineChart kind="donut" data={SHARE} />)
    const rows = getAllByRole("button")
    fireEvent.focus(rows[2])

    expect(container.querySelector(".prd-pie-center-val")?.textContent).toBe("6")
    expect(container.querySelector(".prd-pie-slice[data-active='true']")).not.toBeNull()
  })

  it("announces each row's figures to a screen reader", () => {
    const { getAllByRole } = render(<InlineChart kind="donut" data={SHARE} />)
    expect(getAllByRole("button")[0].getAttribute("aria-label")).toBe(
      "To Do: 20, 59%",
    )
  })

  it("separates touching slices with a surface-coloured gap", () => {
    // Two adjacent hues with no gap read as one shape.
    const { container } = render(<InlineChart kind="donut" data={SHARE} />)
    const slice = container.querySelector(".prd-pie-slice")!
    expect(slice.getAttribute("stroke")).toBe("var(--surface-2)")
    expect(slice.getAttribute("stroke-width")).toBe("2")
  })

  it("gives a plain pie the legend but no centre readout", () => {
    // There is no hole to put it in.
    const { container } = render(<InlineChart kind="pie" data={SHARE} />)
    expect(container.querySelector(".prd-pie-center")).toBeNull()
    expect(container.querySelectorAll(".prd-pie-legend-row")).toHaveLength(3)
  })
})

describe("the figure wears ONE frame, not two", () => {
  // Reported as "this white background with some outermost line gray line
  // around it". A chart arrives as a fenced ```chart block, so the markdown
  // renderer wraps it in <pre> — and <pre> inside a reply is styled as a CODE
  // BLOCK: tinted, bordered, padded. The chart then drew its own card inside
  // that. jsdom computes no layout, so this asserts the RULES.
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "globals.css"),
    "utf8",
  )

  it("strips the code-block chrome from the <pre> that carries a chart", () => {
    const rule = /\.ai-bar-reply-answer pre:has\(\.prd-chart\) \{([^}]*)\}/.exec(css)?.[1] ?? ""
    expect(rule, "no :has(.prd-chart) override — the double frame is back").not.toBe("")
    expect(rule).toMatch(/background:\s*none/)
    expect(rule).toMatch(/border:\s*none/)
    expect(rule).toMatch(/padding:\s*0/)
  })

  it("leaves the figure itself unboxed — air and a hairline, no card", () => {
    const rule = /^\s{2}\.prd-chart \{([^}]*)\}/m.exec(css)?.[1] ?? ""
    expect(rule, ".prd-chart rule not found").not.toBe("")
    expect(rule).toMatch(/border:\s*none/)
    expect(rule).toMatch(/background:\s*none/)
    expect(rule).toMatch(/border-top:\s*1px solid/)
  })
})

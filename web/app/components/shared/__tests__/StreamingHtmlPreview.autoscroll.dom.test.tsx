// @vitest-environment jsdom
//
// Auto-follow: as the streaming document grows past the fold, the panel that
// scrolls it (the artifact panel body) is kept at the bottom so the section
// being generated stays visible. Scrolling up to re-read pauses the follow.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { StreamingHtmlPreview } from "../StreamingHtmlPreview"

// jsdom has no layout engine: scrollHeight/clientHeight are hard 0 and scrollTop
// is inert. Stand in real numbers so the follow logic has something to measure.
function mountScroller(state: { scrollHeight: number; clientHeight: number }) {
  const el = document.createElement("div")
  el.style.overflowY = "auto"
  let top = 0
  Object.defineProperty(el, "scrollHeight", { configurable: true, get: () => state.scrollHeight })
  Object.defineProperty(el, "clientHeight", { configurable: true, get: () => state.clientHeight })
  Object.defineProperty(el, "scrollTop", {
    configurable: true,
    get: () => top,
    set: (v: number) => {
      top = v
    },
  })
  document.body.appendChild(el)
  return el
}

// The iframe grows to its content height, which is what drives the follow.
function setContentHeight(iframe: HTMLIFrameElement, px: number) {
  const body = iframe.contentDocument?.body
  if (!body) throw new Error("preview iframe has no document body")
  Object.defineProperty(body, "scrollHeight", { configurable: true, get: () => px })
}

const HEAD = "<!doctype html><html><body><h1>Draft PRD</h1>"

function preview(html: string) {
  return React.createElement(StreamingHtmlPreview, {
    html,
    title: "PRD draft (generating)",
    testId: "prd-streaming-preview",
  })
}

afterEach(() => {
  cleanup()
  document.body.innerHTML = ""
})

describe("StreamingHtmlPreview — follows the stream", () => {
  it("scrolls the panel to the bottom as the document grows", () => {
    const state = { scrollHeight: 400, clientHeight: 400 }
    const scroller = mountScroller(state)
    const { rerender } = render(preview(HEAD), { container: scroller })

    const iframe = screen.getByTestId("prd-streaming-preview") as HTMLIFrameElement
    // Next chunk lands: the doc is now taller than the panel's viewport.
    setContentHeight(iframe, 1600)
    state.scrollHeight = 1800
    rerender(preview(`${HEAD}<h2>Goals</h2><p>Ship it.</p>`))

    expect(scroller.scrollTop).toBe(1800)
  })

  it("stops following once the user scrolls up, and resumes at the bottom", () => {
    const state = { scrollHeight: 1800, clientHeight: 400 }
    const scroller = mountScroller(state)
    const { rerender } = render(preview(HEAD), { container: scroller })

    const iframe = screen.getByTestId("prd-streaming-preview") as HTMLIFrameElement

    // Reader scrolls back up to an earlier section.
    scroller.scrollTop = 200
    fireEvent.scroll(scroller)

    setContentHeight(iframe, 2400)
    state.scrollHeight = 2600
    rerender(preview(`${HEAD}<h2>Goals</h2>`))
    expect(scroller.scrollTop).toBe(200)

    // Back to the bottom → following resumes with the next chunk.
    scroller.scrollTop = 2600 - 400
    fireEvent.scroll(scroller)

    setContentHeight(iframe, 3200)
    state.scrollHeight = 3400
    rerender(preview(`${HEAD}<h2>Goals</h2><h2>Scope</h2>`))
    expect(scroller.scrollTop).toBe(3400)
  })
})

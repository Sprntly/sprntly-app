// @vitest-environment jsdom
//
// SplashRemover clears the pre-hydration loading splash (#app-splash, painted
// by the root layout's inline critical CSS) once the app is actually ready to
// be looked at.
//
// It used to clear on the first effect after hydration, which is EARLIER than
// the app is worth showing: React had mounted, but the webfonts were still
// loading and the shell had not painted, so the splash cleared onto a bare or
// re-flowing page. The animated mark made the gap obvious — it stopped while
// the page was still visibly arriving.
//
// So these tests are about WHEN it goes, and about the fact that nothing can
// leave it stranded there.
import * as React from "react"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import SplashRemover from "../SplashRemover"

/** Resolvable stand-in for `document.fonts.ready`. */
function stubFonts(): { resolve: () => void } {
  let resolve!: () => void
  const ready = new Promise<void>((r) => { resolve = () => r() })
  Object.defineProperty(document, "fonts", {
    configurable: true,
    value: { ready },
  })
  return { resolve }
}

function removeFonts() {
  Object.defineProperty(document, "fonts", { configurable: true, value: undefined })
}

function mountSplash(): HTMLElement {
  const splash = document.createElement("div")
  splash.id = "app-splash"
  document.body.appendChild(splash)
  return splash
}

/** Let the queued promise callbacks and both animation frames run. */
async function settleFrames() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
    vi.advanceTimersByTime(50) // fake timers drive rAF
  })
}

describe("SplashRemover", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
    removeFonts()
    cleanup()
    document.body.innerHTML = ""
  })

  it("holds the splash until the fonts are ready", async () => {
    const fonts = stubFonts()
    const splash = mountSplash()

    act(() => {
      render(<SplashRemover />)
    })
    // The regression this replaces: hidden the instant React mounted.
    expect(splash.classList.contains("is-hidden")).toBe(false)

    fonts.resolve()
    await settleFrames()
    expect(splash.classList.contains("is-hidden")).toBe(true)
  })

  it("removes the node once the fade has run", async () => {
    const fonts = stubFonts()
    mountSplash()
    act(() => {
      render(<SplashRemover />)
    })
    fonts.resolve()
    await settleFrames()

    // Still present while fading, so nothing pops.
    expect(document.getElementById("app-splash")).not.toBeNull()
    act(() => {
      vi.advanceTimersByTime(250)
    })
    expect(document.getElementById("app-splash")).toBeNull()
  })

  it("clears anyway when the fonts never resolve", async () => {
    // A font CDN that never answers must not strand a loading screen over the
    // working app — the backstop is the whole reason it exists.
    stubFonts() // never resolved
    const splash = mountSplash()
    act(() => {
      render(<SplashRemover />)
    })
    expect(splash.classList.contains("is-hidden")).toBe(false)

    act(() => {
      vi.advanceTimersByTime(2500)
    })
    expect(splash.classList.contains("is-hidden")).toBe(true)
  })

  it("works on a browser with no Font Loading API", async () => {
    removeFonts()
    const splash = mountSplash()
    act(() => {
      render(<SplashRemover />)
    })
    await settleFrames()
    expect(splash.classList.contains("is-hidden")).toBe(true)
  })

  it("no-ops when there is no splash element", () => {
    expect(() =>
      act(() => {
        render(<SplashRemover />)
      }),
    ).not.toThrow()
  })
})

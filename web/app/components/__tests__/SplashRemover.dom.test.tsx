// @vitest-environment jsdom
//
// SplashRemover fades out the pre-hydration loading splash (#app-splash, painted
// white with green "Loading…" by the root layout's inline critical CSS) once the
// client app mounts. These tests prove it (a) marks the splash hidden on mount,
// (b) removes it from the DOM after the fade, and (c) no-ops when no splash exists.
import * as React from "react"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import SplashRemover from "../SplashRemover"

describe("SplashRemover", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
    cleanup()
    document.body.innerHTML = ""
  })

  it("marks the splash hidden on mount and removes it after the fade", () => {
    const splash = document.createElement("div")
    splash.id = "app-splash"
    document.body.appendChild(splash)

    act(() => {
      render(<SplashRemover />)
    })

    // Fade class applied immediately on mount.
    expect(splash.classList.contains("is-hidden")).toBe(true)
    expect(document.getElementById("app-splash")).not.toBeNull()

    // After the fade window, the node is gone entirely.
    act(() => {
      vi.advanceTimersByTime(250)
    })
    expect(document.getElementById("app-splash")).toBeNull()
  })

  it("no-ops when there is no splash element", () => {
    expect(() =>
      act(() => {
        render(<SplashRemover />)
      }),
    ).not.toThrow()
  })
})

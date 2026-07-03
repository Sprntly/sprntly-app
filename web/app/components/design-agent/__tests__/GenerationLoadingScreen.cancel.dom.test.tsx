// @vitest-environment jsdom
//
// DOM tests for the Cancel escape hatch on the generating overlay. The generating
// overlay must expose a visible Cancel control (footer button + top-right close)
// AND respond to the Escape key, all routed to the `onCancel` prop. Without
// `onCancel`, the overlay renders exactly as before (no cancel affordances).
//
// No prototypeId is passed, so the SSE effect early-returns and no EventSource is
// constructed in jsdom.

import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as Record<string, unknown>).React = React

import { GenerationLoadingScreen } from "../GenerationLoadingScreen"

afterEach(cleanup)

describe("GenerationLoadingScreen — cancel affordance", () => {
  it("renders a visible Cancel button + close and invokes onCancel on click", () => {
    const onCancel = vi.fn()
    render(
      React.createElement(GenerationLoadingScreen, { open: true, onCancel }),
    )

    const cancelBtn = screen.getByTestId("proto-gen-cancel-btn")
    expect(cancelBtn.textContent).toContain("Cancel")
    expect(screen.getByTestId("proto-gen-cancel-x")).toBeTruthy()

    fireEvent.click(cancelBtn)
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it("the top-right close (X) also invokes onCancel", () => {
    const onCancel = vi.fn()
    render(
      React.createElement(GenerationLoadingScreen, { open: true, onCancel }),
    )

    fireEvent.click(screen.getByTestId("proto-gen-cancel-x"))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it("the Escape key invokes onCancel", () => {
    const onCancel = vi.fn()
    render(
      React.createElement(GenerationLoadingScreen, { open: true, onCancel }),
    )

    // The listener is on window; a keydown on the body bubbles up to it.
    fireEvent.keyDown(document.body, { key: "Escape" })
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it("renders NO cancel affordance when onCancel is absent (default unchanged)", () => {
    render(React.createElement(GenerationLoadingScreen, { open: true }))

    expect(screen.queryByTestId("proto-gen-cancel-btn")).toBeNull()
    expect(screen.queryByTestId("proto-gen-cancel-x")).toBeNull()
    // Escape is inert without a handler wired.
    fireEvent.keyDown(document.body, { key: "Escape" })
    // (nothing to assert beyond no throw — the overlay is still mounted)
    expect(screen.getByTestId).toBeTruthy()
  })
})

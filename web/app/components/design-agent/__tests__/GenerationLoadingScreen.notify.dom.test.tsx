// @vitest-environment jsdom
//
// Real-DOM interaction tests for "Notify me when ready": a single click
// must call onNotifyWhenReady synchronously and never render an
// intermediate confirmation panel. SSR render can't exercise click, so this
// mirrors the repo's existing .dom.test.tsx convention (e.g.
// GenerationLoadingScreen.cancel.dom.test.tsx, GenerateModalImageSteer.dom.test.tsx).

import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as Record<string, unknown>).React = React

import { GenerationLoadingScreen } from "../GenerationLoadingScreen"

afterEach(cleanup)

describe("GenerationLoadingScreen — notify-when-ready goes straight through", () => {
  it("test_notify_click_calls_on_notify_when_ready_immediately — click calls onNotifyWhenReady exactly once, synchronously", () => {
    const onNotifyWhenReady = vi.fn()
    render(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        onNotifyWhenReady,
      }),
    )

    fireEvent.click(screen.getByText("Notify me when ready"))

    expect(onNotifyWhenReady).toHaveBeenCalledTimes(1)
  })

  it("test_notify_click_never_renders_armed_panel — no armed confirmation panel ever appears, checked immediately post-click", () => {
    const onNotifyWhenReady = vi.fn()
    render(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        onNotifyWhenReady,
      }),
    )

    expect(screen.queryByTestId("proto-gen-notify-armed")).toBeNull()
    fireEvent.click(screen.getByText("Notify me when ready"))

    expect(screen.queryByTestId("proto-gen-notify-armed")).toBeNull()
    expect(screen.queryByText("You're set")).toBeNull()
  })
})

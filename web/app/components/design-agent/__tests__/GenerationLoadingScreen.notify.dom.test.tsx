// @vitest-environment jsdom
//
// Part D (Treatment B) — real-DOM interaction tests for the promoted
// "Notify me when ready" control: click -> armed confirmation -> focus move
// -> "Back to Briefs" hand-off. SSR render can't exercise click/focus, so this
// mirrors the repo's existing .dom.test.tsx convention (e.g.
// GenerationLoadingScreen.cancel.dom.test.tsx, GenerateModalImageSteer.dom.test.tsx).

import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as Record<string, unknown>).React = React

import { GenerationLoadingScreen } from "../GenerationLoadingScreen"

afterEach(cleanup)

describe("GenerationLoadingScreen — notify-when-ready armed confirmation", () => {
  it("test_notify_click_does_not_call_on_notify_when_ready_immediately — click arms the confirmation WITHOUT calling onNotifyWhenReady (AC19)", () => {
    const onNotifyWhenReady = vi.fn()
    render(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        onNotifyWhenReady,
      }),
    )

    expect(screen.queryByTestId("proto-gen-notify-armed")).toBeNull()
    fireEvent.click(screen.getByText("Notify me when ready"))

    expect(screen.getByTestId("proto-gen-notify-armed")).toBeTruthy()
    expect(onNotifyWhenReady).not.toHaveBeenCalled()
  })

  it("test_notify_armed_focus_moves_to_back_to_briefs_link — focus moves to the Back to Briefs link once armed (AC20)", () => {
    const onNotifyWhenReady = vi.fn()
    render(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        onNotifyWhenReady,
      }),
    )

    fireEvent.click(screen.getByText("Notify me when ready"))

    const link = screen.getByText("Back to Briefs").closest("a")
    expect(link).toBeTruthy()
    expect(document.activeElement).toBe(link)
  })

  it("test_back_to_briefs_click_fires_on_notify_when_ready_once — clicking Back to Briefs calls onNotifyWhenReady exactly once (AC21)", () => {
    const onNotifyWhenReady = vi.fn()
    render(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        onNotifyWhenReady,
      }),
    )

    fireEvent.click(screen.getByText("Notify me when ready"))
    const link = screen.getByText("Back to Briefs").closest("a")!
    fireEvent.click(link)

    expect(onNotifyWhenReady).toHaveBeenCalledTimes(1)
  })

  it("the armed confirmation's copy is present and the notify button/cancel are gone", () => {
    const onNotifyWhenReady = vi.fn()
    const onCancel = vi.fn()
    render(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        onNotifyWhenReady,
        onCancel,
      }),
    )

    fireEvent.click(screen.getByText("Notify me when ready"))

    expect(screen.getByText("You're set")).toBeTruthy()
    expect(
      screen.getByText(
        "We'll notify you when it's ready — you're free to close this tab or carry on elsewhere.",
      ),
    ).toBeTruthy()
    expect(screen.queryByText("Notify me when ready")).toBeNull()
  })
})

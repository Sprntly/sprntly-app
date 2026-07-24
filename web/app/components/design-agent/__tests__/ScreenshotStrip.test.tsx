/**
 * @vitest-environment jsdom
 *
 * Presentational-component tests for ScreenshotStrip: render count, remove
 * callback, add-hidden-at-limit, aria-labels, empty state. Pure props in — no
 * fetch/upload logic lives here (the upload flow is exercised at the
 * GenerateModal level in GenerateModalScreenshotSource.dom.test.tsx).
 */
import * as React from "react"
import { cleanup, render, fireEvent } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ScreenshotStrip, type StripScreenshot } from "../ScreenshotStrip"

afterEach(cleanup)

function makeScreenshots(count: number): StripScreenshot[] {
  return Array.from({ length: count }, (_, i) => ({
    key: `key-${i + 1}`,
    preview: `data:image/png;base64,stub-${i + 1}`,
    name: `shot-${i + 1}.png`,
  }))
}

function baseProps(overrides: Partial<React.ComponentProps<typeof ScreenshotStrip>> = {}) {
  return {
    screenshots: [],
    onAdd: vi.fn(),
    onRemove: vi.fn(),
    uploading: false,
    error: null,
    ...overrides,
  }
}

it("test_screenshot_strip_renders_zero_state — empty array renders the strip with no slots and no limit message; count reads '0 of 10'", () => {
  const { container, getByText, queryByRole } = render(
    <ScreenshotStrip {...baseProps()} />,
  )
  expect(container.querySelectorAll("img").length).toBe(0)
  expect(getByText("0 of 10")).toBeTruthy()
  expect(queryByRole("status")).toBeNull()
})

it("test_screenshot_strip_renders_each_slot_with_indexed_aria_label — AC11; 3 screenshots render 3 tiles, each aria-label matching position", () => {
  const { container } = render(
    <ScreenshotStrip {...baseProps({ screenshots: makeScreenshots(3) })} />,
  )
  expect(container.querySelectorAll("img").length).toBe(3)
  for (const n of [1, 2, 3]) {
    expect(
      container.querySelector(`[aria-label="Remove screenshot ${n}"]`),
    ).toBeTruthy()
  }
})

it("test_screenshot_strip_remove_calls_onremove_with_correct_index — AC3; clicking tile 2's remove button calls onRemove(1)", () => {
  const onRemove = vi.fn()
  const { container } = render(
    <ScreenshotStrip
      {...baseProps({ screenshots: makeScreenshots(3), onRemove })}
    />,
  )
  const removeSecond = container.querySelector(
    '[aria-label="Remove screenshot 2"]',
  ) as HTMLButtonElement
  fireEvent.click(removeSecond)
  expect(onRemove).toHaveBeenCalledWith(1)
})

it("test_screenshot_strip_add_button_present_below_ten — AC4; 9 screenshots -> the add button is present", () => {
  const { queryByTestId } = render(
    <ScreenshotStrip {...baseProps({ screenshots: makeScreenshots(9) })} />,
  )
  expect(queryByTestId("screenshot-strip-add")).toBeTruthy()
})

it("test_screenshot_strip_add_button_absent_at_ten — AC4; 10 screenshots -> the add button is absent (not disabled)", () => {
  const { queryByTestId } = render(
    <ScreenshotStrip {...baseProps({ screenshots: makeScreenshots(10) })} />,
  )
  expect(queryByTestId("screenshot-strip-add")).toBeNull()
})

it("test_screenshot_strip_limit_message_shows_at_ten_only — AC5, AC6; count text + limit message assertions at 9 vs 10", () => {
  const { getByText, queryByRole, rerender } = render(
    <ScreenshotStrip {...baseProps({ screenshots: makeScreenshots(9) })} />,
  )
  expect(getByText("9 of 10")).toBeTruthy()
  expect(queryByRole("status")).toBeNull()

  rerender(<ScreenshotStrip {...baseProps({ screenshots: makeScreenshots(10) })} />)
  expect(getByText("10 of 10")).toBeTruthy()
  const status = queryByRole("status")
  expect(status).toBeTruthy()
  expect(status!.textContent).toBe(
    "10 of 10 attached — remove one to add another.",
  )
})

it("test_screenshot_strip_add_button_disabled_while_uploading — the add button (when present) carries disabled while uploading=true", () => {
  const { getByTestId } = render(
    <ScreenshotStrip
      {...baseProps({ screenshots: makeScreenshots(2), uploading: true })}
    />,
  )
  expect((getByTestId("screenshot-strip-add") as HTMLButtonElement).disabled).toBe(
    true,
  )
})

it("test_screenshot_strip_scroll_container_is_keyboard_focusable — AC12; the strip's scroll container has tabIndex={0}; every remove/add control is a real <button>", () => {
  const { container, getByTestId } = render(
    <ScreenshotStrip {...baseProps({ screenshots: makeScreenshots(2) })} />,
  )
  const scrollContainer = container.querySelector(
    '[aria-label="Attached screenshots, scroll to see all"]',
  ) as HTMLElement
  expect(scrollContainer).toBeTruthy()
  expect(scrollContainer.getAttribute("tabIndex")).toBe("0")

  const removeButtons = container.querySelectorAll(
    '[aria-label^="Remove screenshot"]',
  )
  expect(removeButtons.length).toBe(2)
  removeButtons.forEach((btn) => expect(btn.tagName).toBe("BUTTON"))
  expect(getByTestId("screenshot-strip-add").tagName).toBe("BUTTON")
})

it("test_screenshot_strip_error_line_renders_when_present — the error prop, when non-null, renders a role=alert element with that exact text", () => {
  const { getByRole, queryByRole, rerender } = render(
    <ScreenshotStrip {...baseProps({ error: "Something went wrong." })} />,
  )
  expect(getByRole("alert").textContent).toBe("Something went wrong.")

  rerender(<ScreenshotStrip {...baseProps({ error: null })} />)
  expect(queryByRole("alert")).toBeNull()
})

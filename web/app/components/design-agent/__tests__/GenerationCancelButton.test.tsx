// @vitest-environment jsdom
//
// Unit tests for the shared Cancel button extracted from
// GenerationLoadingScreen's footer. Both GenerationLoadingScreen (the
// "generating" full-page overlay) and GenerateModal (the "locating" in-modal
// phase) render this same component so the control's markup/testid/behavior
// never drifts between the two surfaces.

import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as Record<string, unknown>).React = React

import { GenerationCancelButton } from "../GenerationCancelButton"

afterEach(cleanup)

describe("GenerationCancelButton", () => {
  it("test_generationCancelButton_renders_labeled_button", () => {
    render(<GenerationCancelButton onCancel={vi.fn()} />)

    const btn = screen.getByTestId("proto-gen-cancel-btn")
    expect(btn.tagName).toBe("BUTTON")
    expect(btn.getAttribute("type")).toBe("button")
    expect(btn.className).toBe("btn btn-ghost btn-sm proto-gen-cancel-btn")
    expect(btn.textContent).toBe("Cancel")
  })

  it("test_generationCancelButton_click_invokes_onCancel", () => {
    const onCancel = vi.fn()
    render(<GenerationCancelButton onCancel={onCancel} />)

    fireEvent.click(screen.getByTestId("proto-gen-cancel-btn"))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })
})

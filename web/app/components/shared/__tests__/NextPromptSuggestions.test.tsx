// @vitest-environment jsdom
//
// NextPromptSuggestions — the strip of next-prompt chips above the composer.
//
// The first test is the acceptance criterion for the whole feature: with no
// suggestions the component must render NOTHING — not an empty container, not
// a placeholder, not a zero-height row that still occupies layout. It is
// asserted on the container's own markup (`innerHTML === ""`) rather than on a
// query for the chips, because a query would happily pass against an empty
// wrapper div that shifts the composer.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// The suite compiles JSX with the CLASSIC runtime (no `jsx` override in
// vitest.config), so `React` has to be reachable as a global — the same line
// every other DOM suite here carries.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { NextPromptSuggestions } from "../NextPromptSuggestions"

afterEach(cleanup)

describe("NextPromptSuggestions", () => {
  it("renders NOTHING for an empty list — no container, no layout", () => {
    const { container } = render(
      <NextPromptSuggestions suggestions={[]} onPick={vi.fn()} />,
    )
    expect(container.innerHTML).toBe("")
    expect(container.childNodes.length).toBe(0)
    expect(screen.queryByTestId("next-prompt-suggestions")).toBeNull()
  })

  it("renders one chip per suggestion", () => {
    render(
      <NextPromptSuggestions
        suggestions={[
          "Break the promo code bug into tickets",
          "Draft a PRD for Apple Pay on mobile",
        ]}
        onPick={vi.fn()}
      />,
    )
    const strip = screen.getByTestId("next-prompt-suggestions")
    expect(strip.querySelectorAll("button")).toHaveLength(2)
    expect(strip.textContent).toContain("Break the promo code bug into tickets")
    expect(strip.textContent).toContain("Draft a PRD for Apple Pay on mobile")
  })

  it("clicking a chip hands the exact prompt text back", () => {
    const onPick = vi.fn()
    render(
      <NextPromptSuggestions
        suggestions={["Break the promo code bug into tickets"]}
        onPick={onPick}
      />,
    )
    fireEvent.click(screen.getByText("Break the promo code bug into tickets"))
    expect(onPick).toHaveBeenCalledWith("Break the promo code bug into tickets")
  })

  it("disabled chips stay visible but inert — no vanish-and-reflow mid-send", () => {
    const onPick = vi.fn()
    render(
      <NextPromptSuggestions
        suggestions={["Break the promo code bug into tickets"]}
        onPick={onPick}
        disabled
      />,
    )
    const chip = screen.getByText("Break the promo code bug into tickets") as HTMLButtonElement
    expect(chip.disabled).toBe(true)
    fireEvent.click(chip)
    expect(onPick).not.toHaveBeenCalled()
  })
})

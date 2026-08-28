// @vitest-environment jsdom
//
// The colour picker both toolbars open.
//
// It replaced a five-entry dropdown, and the reason is the thing to keep true:
// a list is fine for fonts, where the options ARE the vocabulary, and wrong for
// colour, where what someone wants is a point in a space. So the assertions
// here are about REACH — a grid wide enough to be a palette, a way back to the
// document's own colour, and the platform's own picker for anything else.
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { COLOR_SWATCHES } from "../../../(app)/artifacts/doc/editorSchema"
import { ColorGrid } from "../ColorGrid"

afterEach(cleanup)

const renderGrid = () => {
  const onPick = vi.fn()
  const view = render(
    <ColorGrid onPick={onPick} clearLabel="Default" testId="grid" />,
  )
  return { view, onPick }
}

describe("ColorGrid", () => {
  it("shows the whole palette, not a shortlist", () => {
    const { view } = renderGrid()
    const swatches = view.container.querySelectorAll(".prd-colorgrid-swatch")
    expect(swatches.length).toBe(COLOR_SWATCHES.flat().length)
    expect(swatches.length).toBeGreaterThanOrEqual(40)
  })

  it("paints each swatch in the colour it applies", () => {
    // A grid of identical grey squares is a grid of nothing.
    const { view } = renderGrid()
    const first = view.container.querySelector(".prd-colorgrid-swatch") as HTMLElement
    expect(first.style.background).not.toBe("")
  })

  it("picks the colour that was clicked", () => {
    const { view, onPick } = renderGrid()
    fireEvent.click(view.getByTestId("grid-ff0000"))
    expect(onPick).toHaveBeenCalledWith("#FF0000")
  })

  it("offers a way back to the document's own colour", () => {
    // Without it, a colour applied by accident can only be removed by clearing
    // ALL formatting off the selection.
    const { view, onPick } = renderGrid()
    fireEvent.click(view.getByTestId("grid-clear"))
    expect(onPick).toHaveBeenCalledWith("")
  })

  it("reaches colours that are not on the grid, through the platform's picker", () => {
    // `<input type="color">` rather than a second colour dialog written in
    // JavaScript: every platform already ships one, and ours would be worse.
    const { view, onPick } = renderGrid()
    const input = view.getByTestId("grid-custom") as HTMLInputElement
    expect(input.type).toBe("color")
    fireEvent.input(input, { target: { value: "#123456" } })
    expect(onPick).toHaveBeenCalledWith("#123456")
  })

  it("names every swatch for the people who cannot see it", () => {
    const { view } = renderGrid()
    for (const el of view.container.querySelectorAll(".prd-colorgrid-swatch")) {
      const name = el.getAttribute("aria-label")
      expect(name, "a swatch with no accessible name").toBeTruthy()
      expect(name).not.toMatch(/^#/)
    }
  })

  it("suppresses mousedown, or it applies to nothing", () => {
    // The same rule every control in these bars follows: the selection IS the
    // argument, and it does not survive the focus move a plain press causes.
    const { view } = renderGrid()
    for (const testId of ["grid-clear", "grid-ff0000"]) {
      const e = new MouseEvent("mousedown", { bubbles: true, cancelable: true })
      view.getByTestId(testId).dispatchEvent(e)
      expect(e.defaultPrevented, `${testId} did not suppress mousedown`).toBe(true)
    }
  })
})

// @vitest-environment jsdom
//
// The PRD formatting toolbar, shared by the markdown editor and the HTML PRD's
// iframe view.
//
// Two things it has to get right, both learned the hard way:
//
//  1. Every control must SUPPRESS mousedown. Pressing a toolbar button
//     otherwise moves focus out of the document and collapses the selection the
//     command is supposed to act on — the button appears to do nothing.
//  2. The row is too wide for a narrow panel (the artifact drawer open on a
//     laptop), and when it overflowed it pushed the SAVE STATUS off the end.
//     That is the one control in the bar that must never be hidden: it is the
//     only thing telling anyone whether their edit reached the server. So the
//     rest collapses behind a "More" menu instead.
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("../../../lib/api", () => ({ prdApi: { update: vi.fn() } }))

import { PrdToolbar } from "../PrdMarkdownEditor"

afterEach(cleanup)

const renderBar = (over: Partial<React.ComponentProps<typeof PrdToolbar>> = {}) => {
  const exec = vi.fn()
  const view = render(<PrdToolbar hasDoc saveStatus="saved" exec={exec} {...over} />)
  return { view, exec }
}

describe("PrdToolbar — the always-visible row", () => {
  it("runs the common commands", () => {
    const { view, exec } = renderBar()
    for (const [testId, cmd] of [
      ["prd-tool-undo", "undo"],
      ["prd-tool-redo", "redo"],
      ["prd-tool-bold", "bold"],
      ["prd-tool-italic", "italic"],
      ["prd-tool-underline", "underline"],
      ["prd-tool-ul", "insertUnorderedList"],
      ["prd-tool-ol", "insertOrderedList"],
    ] as const) {
      fireEvent.click(view.getByTestId(testId))
      expect(exec, testId).toHaveBeenCalledWith(cmd, undefined)
    }
  })

  it("suppresses mousedown so the selection survives the click", () => {
    // The failure this rules out: the button works in isolation and does
    // nothing in the product, because focus left the document first.
    const { view } = renderBar()
    const notCancelled = fireEvent.mouseDown(view.getByTestId("prd-tool-bold"))
    expect(notCancelled).toBe(false)
  })

  it("inserts a real table — the button used to have no onClick at all", () => {
    const { view, exec } = renderBar()
    fireEvent.click(view.getByTestId("prd-tool-table"))
    expect(exec).toHaveBeenCalledTimes(1)
    const [cmd, html] = exec.mock.calls[0]
    expect(cmd).toBe("insertHTML")
    expect(html).toContain("<table>")
  })

  it("applies a block format from the style menu", () => {
    // A custom menu, not a native <select> — an <option> cannot carry an icon.
    const { view, exec } = renderBar()
    fireEvent.click(view.getByTestId("prd-tool-block"))
    fireEvent.click(view.getByTestId("prd-more-h2"))
    expect(exec).toHaveBeenCalledWith("formatBlock", "h2")
  })

  it("shows an icon beside every entry in both menus", () => {
    const { view } = renderBar()
    for (const trigger of ["prd-tool-block", "prd-tool-more"]) {
      fireEvent.click(view.getByTestId(trigger))
      const items = view.getByTestId(`${trigger}-menu`).querySelectorAll(".prd-more-item")
      expect(items.length).toBeGreaterThan(0)
      for (const item of items) {
        expect(item.querySelector(".prd-more-icon"), `${trigger} row has no icon`).not.toBeNull()
      }
    }
  })

  it("opening one dropdown closes the other", () => {
    // Two independent booleans would let both hang open over the document.
    const { view } = renderBar()
    fireEvent.click(view.getByTestId("prd-tool-block"))
    expect(view.queryByTestId("prd-tool-block-menu")).not.toBeNull()
    fireEvent.click(view.getByTestId("prd-tool-more"))
    expect(view.queryByTestId("prd-tool-block-menu")).toBeNull()
    expect(view.queryByTestId("prd-tool-more-menu")).not.toBeNull()
  })

  it("disables everything when there is no document", () => {
    // `toBeDisabled` is jest-dom; this repo's vitest setup doesn't load it.
    const { view } = renderBar({ hasDoc: false })
    expect((view.getByTestId("prd-tool-bold") as HTMLButtonElement).disabled).toBe(true)
    expect((view.getByTestId("prd-tool-more") as HTMLButtonElement).disabled).toBe(true)
  })
})

describe("PrdToolbar — the overflow menu", () => {
  it("keeps the extra tools out of the row until asked for", () => {
    const { view } = renderBar()
    expect(view.queryByTestId("prd-tool-more-menu")).toBeNull()
    expect(view.queryByTestId("prd-more-strikeThrough")).toBeNull()
  })

  it("opens, runs a command, and closes again", () => {
    const { view, exec } = renderBar()
    fireEvent.click(view.getByTestId("prd-tool-more"))
    expect(view.getByTestId("prd-tool-more-menu")).not.toBeNull()

    fireEvent.click(view.getByTestId("prd-more-justifyCenter"))
    expect(exec).toHaveBeenCalledWith("justifyCenter", undefined)
    // A menu left open over the document is in the way of the next edit.
    expect(view.queryByTestId("prd-tool-more-menu")).toBeNull()
  })

  it("closes on click-away and on Escape", () => {
    const { view } = renderBar()

    fireEvent.click(view.getByTestId("prd-tool-more"))
    fireEvent.mouseDown(document.body)
    expect(view.queryByTestId("prd-tool-more-menu")).toBeNull()

    fireEvent.click(view.getByTestId("prd-tool-more"))
    fireEvent.keyDown(document, { key: "Escape" })
    expect(view.queryByTestId("prd-tool-more-menu")).toBeNull()
  })

  it("lives OUTSIDE the scrolling tool row, or it is invisible", () => {
    // The bug this exists for: `.prd-tools-l` scrolls (`overflow-x: auto`) so
    // no tool is unreachable in a narrow panel — and an absolutely-positioned
    // menu inside a scroll container is CLIPPED by it. The button opened, state
    // flipped, every unit test passed, and nothing appeared on screen.
    //
    // jsdom applies no layout, so the clipping itself can't be asserted. The
    // structural fact that causes it can: the menu must not be a descendant of
    // the scrolling row.
    const { view } = renderBar()
    fireEvent.click(view.getByTestId("prd-tool-more"))
    const row = view.container.querySelector(".prd-tools-l")!
    const menu = view.getByTestId("prd-tool-more-menu")
    expect(row.contains(menu)).toBe(false)
    // …and it is still inside the toolbar, not orphaned somewhere else.
    expect(view.container.querySelector(".prd-toolbar")!.contains(menu)).toBe(true)
  })

  it("suppresses mousedown on menu items too", () => {
    const { view } = renderBar()
    fireEvent.click(view.getByTestId("prd-tool-more"))
    const notCancelled = fireEvent.mouseDown(view.getByTestId("prd-more-removeFormat"))
    expect(notCancelled).toBe(false)
  })
})

describe("PrdToolbar — pinned to the top", () => {
  // The bar stays put while the document scrolls under it, like the
  // Evidence / PRD / Tickets tabs above it. Formatting controls that scroll
  // away are unreachable exactly when they are wanted: partway down a long
  // PRD, mid-edit.
  //
  // Read off globals.css — jsdom applies no layout, so `position: sticky`
  // cannot be observed by rendering. What CAN be pinned is the rule, and the
  // two properties that silently defeat it.
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "globals.css"),
    "utf8",
  )
  // ANCHORED to the start of a line: an unanchored `.prd-toolbar {` also
  // matches `.main--reading .prd-toolbar {`, a padding-only override that
  // appears earlier in the file — the first draft of this asserted against
  // that and reported the base rule as missing everything.
  const rule = /^\s*\.prd-toolbar \{([^}]*)\}/m.exec(css)?.[1] ?? ""

  it("is sticky to the top of the scrolling panel", () => {
    expect(rule).toMatch(/position:\s*sticky/)
    expect(rule).toMatch(/top:\s*0/)
  })

  it("is opaque, or the document scrolls through it", () => {
    expect(rule).toMatch(/background:/)
  })

  it("sits under the overflow menu, so an open dropdown still draws over it", () => {
    const z = /z-index:\s*(\d+)/.exec(rule)
    expect(z, "the toolbar needs a stacking order against the document").not.toBeNull()
    const menu = /^\s*\.prd-more-menu \{([^}]*)\}/m.exec(css)?.[1] ?? ""
    const menuZ = /z-index:\s*(\d+)/.exec(menu)
    expect(menuZ).not.toBeNull()
    expect(Number(z![1])).toBeLessThan(Number(menuZ![1]))
  })

  it("has no clipping ancestor — `.prd-frame` must not set overflow", () => {
    // An `overflow` on the frame between the bar and `.prd-scroll` would kill
    // the stickiness outright, and silently.
    const frame = /^\s*\.prd-frame \{([^}]*)\}/m.exec(css)?.[1] ?? ""
    expect(frame).not.toMatch(/^\s*overflow[^:]*:/m)
  })
})

describe("PrdToolbar — the save status", () => {
  it("is always rendered, whatever else is in the row", () => {
    // The regression: the tool row grew, and the status was pushed out of the
    // bar entirely. It is pinned `flex: 0 0 auto` now; this is the DOM half.
    for (const status of ["saved", "saving", "unsaved"] as const) {
      const { view } = renderBar({ saveStatus: status })
      expect(view.container.querySelector(".prd-status")).not.toBeNull()
      cleanup()
    }
  })

  it("says what is actually happening, including unsaved", () => {
    const { view } = renderBar({ saveStatus: "unsaved" })
    expect(view.container.querySelector(".prd-status")!.textContent).toContain("Unsaved")
  })

  it("reads 'No draft' when there is no document", () => {
    const { view } = renderBar({ hasDoc: false })
    expect(view.container.querySelector(".prd-status")!.textContent).toContain("No draft")
  })
})

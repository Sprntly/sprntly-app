// @vitest-environment jsdom
//
// ConfirmDialog — the `elevated` stacking prop. A confirm opened from inside
// another `modal-overlay` (e.g. the project settings modal's Members tab)
// shares the base modal z-index (200) and, rendered earlier in the DOM, would
// paint BEHIND its parent modal. `elevated` lifts this overlay above that layer
// (inline z-index 300) so a nested confirm is never buried; the default keeps
// the class's own z-index untouched for standalone confirms.
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ConfirmDialog } from "../ConfirmDialog"

afterEach(() => cleanup())

const baseProps = {
  open: true,
  title: "Remove Fortune Adeyemi?",
  body: "They'll lose access to this project's chats, artifacts, and memory.",
  confirmLabel: "Remove",
  onConfirm: vi.fn(),
  onCancel: vi.fn(),
}

const overlayOf = (el: HTMLElement) => el.closest(".modal-overlay") as HTMLElement | null

describe("ConfirmDialog — elevated stacking prop", () => {
  it("elevated lifts the overlay above the base modal layer (inline z-index 300)", () => {
    render(<ConfirmDialog {...baseProps} elevated />)
    const overlay = overlayOf(screen.getByRole("dialog"))
    expect(overlay).not.toBeNull()
    expect(overlay?.style.zIndex).toBe("300")
  })

  it("defaults to no inline z-index so a standalone confirm keeps the class's base layer", () => {
    render(<ConfirmDialog {...baseProps} />)
    const overlay = overlayOf(screen.getByRole("dialog"))
    expect(overlay?.style.zIndex).toBe("")
  })
})

// @vitest-environment jsdom
//
// useEscapeToClose — the shared hook behind Escape-to-close on the three
// rail modals (ArtifactsModal/CreateProjectModal/MemoryModal). A React
// onKeyDown on the dialog panel isn't a reliable source for Escape (the
// panel-scoped handler can miss it entirely); this hook attaches a real
// document-level `keydown` listener for the open lifetime instead. Tested
// standalone here (in addition to the per-modal regression coverage) so the
// hook's own contract — fires only while open, on Escape only, cleaned up
// on close/unmount — is pinned independent of any one modal's markup.
import * as React from "react"
import { act, cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { useEscapeToClose } from "../useEscapeToClose"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

function Harness({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEscapeToClose(open, onClose)
  return React.createElement("div", { "data-testid": "harness" }, "harness")
}

afterEach(() => cleanup())

describe("useEscapeToClose", () => {
  it("calls onClose when Escape is dispatched at the document level while open", () => {
    const onClose = vi.fn()
    render(React.createElement(Harness, { open: true, onClose }))
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("ignores non-Escape keys", () => {
    const onClose = vi.fn()
    render(React.createElement(Harness, { open: true, onClose }))
    fireEvent.keyDown(document, { key: "Enter" })
    fireEvent.keyDown(document, { key: "Tab" })
    expect(onClose).not.toHaveBeenCalled()
  })

  it("does not attach a listener at all while closed — Escape does nothing", () => {
    const onClose = vi.fn()
    render(React.createElement(Harness, { open: false, onClose }))
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).not.toHaveBeenCalled()
  })

  it("removes the listener when `open` flips to false — no leaked listener (cleanup)", () => {
    const onClose = vi.fn()
    const { rerender } = render(React.createElement(Harness, { open: true, onClose }))
    act(() => {
      rerender(React.createElement(Harness, { open: false, onClose }))
    })
    onClose.mockClear()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).not.toHaveBeenCalled()
  })

  it("removes the listener on unmount — no leaked listener (cleanup)", () => {
    const onClose = vi.fn()
    render(React.createElement(Harness, { open: true, onClose }))
    cleanup()
    onClose.mockClear()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).not.toHaveBeenCalled()
  })
})

// @vitest-environment jsdom
//
// Highlight-to-reply: the toolbar must appear ONLY for a selection inside an
// answer body within the transcript, and pressing it must report the text that
// was actually selected — the failure mode this guards is the classic
// selection-toolbar bug where mousedown on the button collapses the selection
// and the handler quotes "".
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { SelectionReplyToolbar } from "../SelectionReplyToolbar"

afterEach(() => {
  cleanup()
  window.getSelection()?.removeAllRanges()
})

/** Mount a transcript-shaped container plus the toolbar bound to it. */
function mount(onReply = vi.fn()) {
  const containerRef = { current: null as HTMLElement | null }
  const view = render(
    <div>
      <div
        ref={(el) => {
          containerRef.current = el
        }}
        data-testid="column"
      >
        <div className="bc-user-bubble" data-testid="mine">my own question</div>
        <div className="bc-agent-body" data-testid="answer">
          findings unsupported by adequate documentation are not to be included
        </div>
      </div>
      <div data-testid="outside">dock text</div>
      <SelectionReplyToolbar containerRef={containerRef} onReply={onReply} />
    </div>,
  )
  return { view, onReply }
}

/** Select the whole text of `el` and fire the mouseup the toolbar listens for. */
function selectAll(el: Element) {
  const range = document.createRange()
  range.selectNodeContents(el)
  const sel = window.getSelection()!
  sel.removeAllRanges()
  sel.addRange(range)
  fireEvent.mouseUp(document)
}

describe("SelectionReplyToolbar", () => {
  it("offers Reply for a selection inside an answer body", () => {
    const { view } = mount()
    expect(view.queryByTestId("selection-reply-toolbar")).toBeNull()
    selectAll(view.getByTestId("answer"))
    expect(view.queryByTestId("selection-reply-toolbar")).not.toBeNull()
  })

  it("reports the selected text, normalized, when Reply is pressed", () => {
    const { view, onReply } = mount()
    selectAll(view.getByTestId("answer"))
    fireEvent.click(view.getByTestId("selection-reply-button"))
    expect(onReply).toHaveBeenCalledTimes(1)
    expect(onReply.mock.calls[0][0]).toBe(
      "findings unsupported by adequate documentation are not to be included",
    )
  })

  it("dismisses itself once the quote has been taken", () => {
    const { view } = mount()
    selectAll(view.getByTestId("answer"))
    fireEvent.click(view.getByTestId("selection-reply-button"))
    expect(view.queryByTestId("selection-reply-toolbar")).toBeNull()
  })

  it("does NOT offer Reply on the reader's own message", () => {
    // Quoting exists to point at something the AGENT said; there is nothing to
    // ground on in your own turn.
    const { view } = mount()
    selectAll(view.getByTestId("mine"))
    expect(view.queryByTestId("selection-reply-toolbar")).toBeNull()
  })

  it("does NOT offer Reply for a selection outside the transcript column", () => {
    const { view } = mount()
    selectAll(view.getByTestId("outside"))
    expect(view.queryByTestId("selection-reply-toolbar")).toBeNull()
  })

  it("hides on a collapsed selection, a new mousedown and a scroll", () => {
    const { view } = mount()

    selectAll(view.getByTestId("answer"))
    window.getSelection()?.removeAllRanges()
    fireEvent.mouseUp(document)
    expect(view.queryByTestId("selection-reply-toolbar")).toBeNull()

    selectAll(view.getByTestId("answer"))
    fireEvent.mouseDown(view.getByTestId("outside"))
    expect(view.queryByTestId("selection-reply-toolbar")).toBeNull()

    selectAll(view.getByTestId("answer"))
    fireEvent.scroll(view.getByTestId("column"))
    expect(view.queryByTestId("selection-reply-toolbar")).toBeNull()
  })

  it("keeps the selection alive across the button's own mousedown", () => {
    // `onMouseDown` must preventDefault, or the browser collapses the range
    // before click fires and the quote comes back empty.
    const { view, onReply } = mount()
    selectAll(view.getByTestId("answer"))
    const button = view.getByTestId("selection-reply-button")
    const notCancelled = fireEvent.mouseDown(button)
    expect(notCancelled).toBe(false) // preventDefault() was called
    fireEvent.click(button)
    expect(onReply.mock.calls[0][0]).not.toBe("")
  })
})

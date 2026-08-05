// @vitest-environment jsdom
//
// The v3 HTML PRD's contenteditable + debounced autosave-on-input loop is a
// real write path, independent of any outer Save button — a guest-mode
// caller must stop it at the source. These tests pin all three of
// PrdHtmlView's `readOnly` guards: (1) every [contenteditable] element in the
// loaded document is force-disabled, (2) the input→persist listener is never
// wired, and (3) persist() itself (and therefore the imperative save()
// handle) refuses to call prdApi.update — the real backstop even if
// something upstream still tries to trigger a save.
import { createRef } from "react"
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const updateMock = vi.fn()
vi.mock("../../../lib/api", () => ({
  prdApi: { update: (...a: unknown[]) => updateMock(...a) },
}))

import { PrdHtmlView, type PrdHtmlHandle } from "../PrdHtmlView"

const HTML = '<html><body><div id="doc" contenteditable="true">Edit me</div></body></html>'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function getIframeDoc(container: HTMLElement): { iframe: HTMLIFrameElement; idoc: Document } {
  const iframe = container.querySelector("iframe") as HTMLIFrameElement
  const idoc = iframe.contentDocument as Document
  return { iframe, idoc }
}

describe("PrdHtmlView — readOnly guest guard", () => {
  it("force-disables every [contenteditable] element once the document loads", async () => {
    const { container } = render(
      <PrdHtmlView html={HTML} prdId={1} title="T" readOnly />,
    )
    const { iframe, idoc } = getIframeDoc(container)
    // jsdom's srcdoc iframe fires a real 'load' event; wiring the body content
    // in first (srcdoc parsing is best-effort in jsdom) keeps this hermetic.
    idoc.body.innerHTML = '<div id="doc" contenteditable="true">Edit me</div>'
    fireEvent.load(iframe)

    await waitFor(() => {
      expect(idoc.getElementById("doc")?.getAttribute("contenteditable")).toBe("false")
    })
  })

  it("never wires the autosave listener — an input event never calls prdApi.update", async () => {
    const { container } = render(
      <PrdHtmlView html={HTML} prdId={1} title="T" readOnly />,
    )
    const { iframe, idoc } = getIframeDoc(container)
    idoc.body.innerHTML = '<div id="doc" contenteditable="true">Edit me</div>'
    fireEvent.load(iframe)
    await waitFor(() => {
      expect(idoc.getElementById("doc")?.getAttribute("contenteditable")).toBe("false")
    })

    idoc.dispatchEvent(new Event("input", { bubbles: true }))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 2100)) // past the 2s debounce
    })
    expect(updateMock).not.toHaveBeenCalled()
  })

  it("persist() itself refuses prdApi.update when readOnly, even via the imperative save() handle", async () => {
    const ref = createRef<PrdHtmlHandle>()
    const { container } = render(
      <PrdHtmlView ref={ref} html={HTML} prdId={1} title="T" readOnly />,
    )
    const { iframe, idoc } = getIframeDoc(container)
    idoc.body.innerHTML = '<div id="doc" contenteditable="true">Edit me</div>'
    fireEvent.load(iframe)
    await waitFor(() => expect(ref.current).not.toBeNull())

    await act(async () => {
      await ref.current!.save()
    })
    expect(updateMock).not.toHaveBeenCalled()
  })

  it("regression: the non-guest (readOnly absent) path is unchanged — input still autosaves", async () => {
    updateMock.mockResolvedValue({})
    const { container } = render(<PrdHtmlView html={HTML} prdId={1} title="T" />)
    const { iframe, idoc } = getIframeDoc(container)
    idoc.body.innerHTML = '<div id="doc" contenteditable="true">Edit me</div>'
    fireEvent.load(iframe)
    await waitFor(() => {
      // Non-guest: contenteditable must NOT be forced off.
      expect(idoc.getElementById("doc")?.getAttribute("contenteditable")).toBe("true")
    })

    idoc.dispatchEvent(new Event("input", { bubbles: true }))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 2100))
    })
    expect(updateMock).toHaveBeenCalledTimes(1)
    expect(updateMock).toHaveBeenCalledWith(1, expect.objectContaining({ title: "T" }))
  })
})

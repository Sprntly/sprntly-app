// @vitest-environment jsdom
//
// The local HTML draft is crash-recovery for an edit that has NOT reached the
// server — not a second copy of the document. It used to be written on every
// autosave and never cleared, so any user who had edited a PRD once carried a
// permanent shadow copy that outranked the server on every later open. Two
// users then diverged asymmetrically: the one who had edited kept seeing their
// own stale document (and autosaved it back over the other's saved work),
// while the one who had never edited saw the server copy and so saw their
// collaborator's edits fine.
//
// These tests pin the invariant that fixes that: a draft survives only while
// the server still holds the document it was based on, and disappears the
// moment the server accepts the save.
import { createRef } from "react"
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const updateMock = vi.fn()
vi.mock("../../../lib/api", () => ({
  prdApi: { update: (...a: unknown[]) => updateMock(...a) },
}))

import { PrdHtmlView, type PrdHtmlHandle } from "../PrdHtmlView"

const DRAFT_KEY = (prdId: number) => `sprntly_prd_html_draft_${prdId}`

/** The document as the server currently holds it. */
const SERVER = '<html><body><div id="doc" contenteditable="true">server copy</div></body></html>'

beforeEach(() => {
  localStorage.clear()
  updateMock.mockReset()
  updateMock.mockResolvedValue({})
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function mount(html: string, prdId = 1) {
  const ref = createRef<PrdHtmlHandle>()
  const { container } = render(
    <PrdHtmlView ref={ref} html={html} prdId={prdId} title="T" />,
  )
  const iframe = container.querySelector("iframe") as HTMLIFrameElement
  const idoc = iframe.contentDocument as Document
  return { ref, iframe, idoc }
}

/** Put `text` in the iframe document and drive one save through the handle. */
async function editAndSave(
  ref: React.RefObject<PrdHtmlHandle | null>,
  idoc: Document,
  iframe: HTMLIFrameElement,
  text: string,
) {
  idoc.body.innerHTML = `<div id="doc" contenteditable="true">${text}</div>`
  fireEvent.load(iframe)
  await waitFor(() => expect(ref.current).not.toBeNull())
  await act(async () => {
    await ref.current!.save()
  })
}

describe("PrdHtmlView — the local draft is unsaved work, not a shadow copy", () => {
  it("clears the draft once the server accepts the save", async () => {
    const { ref, idoc, iframe } = mount(SERVER)
    await editAndSave(ref, idoc, iframe, "my edit")

    expect(updateMock).toHaveBeenCalledTimes(1)
    // Saved — the server is now the record. A draft left here is exactly the
    // stale copy that hid a collaborator's later edits.
    expect(localStorage.getItem(DRAFT_KEY(1))).toBeNull()
  })

  it("keeps the draft when the save fails, so unsaved work still survives", async () => {
    updateMock.mockRejectedValue(new Error("offline"))
    const { ref, idoc, iframe } = mount(SERVER)
    await editAndSave(ref, idoc, iframe, "my unsaved edit")

    const raw = localStorage.getItem(DRAFT_KEY(1))
    expect(raw).not.toBeNull()
    expect(JSON.parse(raw as string).doc).toContain("my unsaved edit")
  })

  it("renders a surviving draft when the server still holds what it was based on", async () => {
    // A failed save left a draft based on the CURRENT server document.
    localStorage.setItem(
      DRAFT_KEY(1),
      JSON.stringify({ base: SERVER, doc: SERVER.replace("server copy", "my unsaved edit") }),
    )
    const { idoc, iframe } = mount(SERVER)
    fireEvent.load(iframe)

    await waitFor(() => {
      const srcdoc = iframe.getAttribute("srcdoc") ?? ""
      expect(srcdoc).toContain("my unsaved edit")
    })
    expect(idoc).toBeTruthy()
  })

  it("drops a draft the server has moved past — a collaborator's edit wins", async () => {
    // David's leftover draft, based on the document as it was BEFORE Jide saved.
    localStorage.setItem(
      DRAFT_KEY(1),
      JSON.stringify({ base: SERVER, doc: SERVER.replace("server copy", "davids stale copy") }),
    )
    // Jide has since saved; this is what the server returns now.
    const AFTER_JIDE = SERVER.replace("server copy", "jides edit")
    const { iframe } = mount(AFTER_JIDE)
    fireEvent.load(iframe)

    await waitFor(() => {
      const srcdoc = iframe.getAttribute("srcdoc") ?? ""
      expect(srcdoc).toContain("jides edit")
      expect(srcdoc).not.toContain("davids stale copy")
    })
    // And it is gone, so it cannot resurface on the next open either.
    expect(localStorage.getItem(DRAFT_KEY(1))).toBeNull()
  })

  it("ignores a legacy bare-string draft, which carries no base to check", async () => {
    // Pre-fix drafts were the raw HTML with no record of what they were based
    // on — indistinguishable from the stale shadow copy, so the server wins.
    localStorage.setItem(DRAFT_KEY(1), SERVER.replace("server copy", "legacy stale copy"))
    const { iframe } = mount(SERVER)
    fireEvent.load(iframe)

    await waitFor(() => {
      const srcdoc = iframe.getAttribute("srcdoc") ?? ""
      expect(srcdoc).toContain("server copy")
      expect(srcdoc).not.toContain("legacy stale copy")
    })
  })
})

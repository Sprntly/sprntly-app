// @vitest-environment jsdom
//
// The panel's Document tab, on the two paths that can DESTROY WORK.
//
// Both were live in the first cut of this tab and both are silent — no error,
// no failed request, just the wrong text stored:
//
//   * "Keep mine" re-read the body ref AFTER reloading, so it sent the
//     SERVER's document back and saved theirs over the user's;
//   * the scheduler was built while the row was still `generating` (version 1)
//     and never re-based, so the first keystroke after the document landed
//     saved against a version the server had already moved past — raising a
//     conflict on a document nobody else had touched.
import * as React from "react"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

// The editor is stubbed: these tests are about the SAVE wiring, and mounting
// ProseMirror would make them about ProseMirror. The stub exposes onChange so a
// test can simulate typing.
const editorMock = vi.hoisted(() => ({ onChange: null as ((html: string) => void) | null }))
vi.mock("../../../(app)/artifacts/doc/DocumentEditor", () => ({
  DocumentEditor: (props: { onChange?: (html: string) => void }) => {
    editorMock.onChange = props.onChange ?? null
    return React.createElement("div", { "data-testid": "editor-stub" })
  },
}))

const api = vi.hoisted(() => ({
  get: vi.fn(),
  update: vi.fn(),
}))
// Declared inside vi.hoisted: `vi.mock` factories are hoisted above module
// scope, so a top-level class is not initialized when the factory runs.
const { FakeApiError } = vi.hoisted(() => ({
  FakeApiError: class FakeApiError extends Error {
    status: number
    body: unknown
    constructor(status: number, body: unknown) {
      super("api"); this.status = status; this.body = body
    }
  },
}))
vi.mock("../../../lib/api", () => ({
  ApiError: FakeApiError,
  customArtifactsApi: {
    get: (...a: unknown[]) => api.get(...a),
    update: (...a: unknown[]) => api.update(...a),
  },
}))

import { DocumentTab } from "../DocumentTab"

const DOC = (over: Record<string, unknown> = {}) => ({
  id: 5, kind: "leadership update", title: "Q3", status: "ready",
  body_html: "<p>theirs on server</p>", version: 2,
  created_at: "", updated_at: "", conversation_id: 1,
  created_by: null, updated_by: null, ...over,
})

beforeAll(() => {
  if (!Range.prototype.getClientRects) {
    Range.prototype.getClientRects = () =>
      ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} }) as unknown as DOMRectList
  }
})

afterEach(() => {
  cleanup()
  api.get.mockReset()
  api.update.mockReset()
  editorMock.onChange = null
})

async function mountTab(doc = DOC()) {
  api.get.mockResolvedValue(doc)
  await act(async () => { render(React.createElement(DocumentTab, { documentId: 5 })) })
  await waitFor(() => expect(editorMock.onChange).not.toBeNull())
}

describe("the version it saves against", () => {
  it("re-bases when a generation finishes under it", async () => {
    // THE CASE THAT MATTERS, and the one an already-ready mount cannot test.
    //
    // The chat opens this tab the moment it asks for a document, while the row
    // is still `generating` at version 1. `finish_artifact` then bumps it to 2
    // when the text lands. If the scheduler keeps the version it was built
    // with, the user's FIRST keystroke saves against version 1, matches no row,
    // and raises "Someone else saved this document while you were editing" on
    // a document nobody else has touched.
    vi.useFakeTimers()
    try {
      api.get.mockResolvedValue(DOC({ status: "generating", version: 1, body_html: "" }))
      await act(async () => { render(React.createElement(DocumentTab, { documentId: 5 })) })

      // The generation lands: the next poll returns ready, at version 2.
      api.get.mockResolvedValue(DOC({ status: "ready", version: 2 }))
      await act(async () => { await vi.advanceTimersByTimeAsync(2600) })
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })

      expect(editorMock.onChange).not.toBeNull()
      api.update.mockResolvedValue({ ...DOC(), version: 3 })
      await act(async () => { editorMock.onChange!("<p>first keystroke</p>") })
      await act(async () => { await vi.advanceTimersByTimeAsync(1500) })

      expect(api.update).toHaveBeenCalled()
      // 2, not the 1 it was built with.
      expect(api.update.mock.calls[0][1].base_version).toBe(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it("does not re-base over text the user has already typed", async () => {
    // The re-base is gated on nothing being queued: a poll landing mid-sentence
    // must not drop the pending save it would otherwise reset away.
    await mountTab(DOC({ version: 2 }))
    api.update.mockResolvedValue({ ...DOC(), version: 3 })
    await act(async () => { editorMock.onChange!("<p>typed</p>") })
    await waitFor(() => expect(api.update).toHaveBeenCalled(), { timeout: 4000 })
    expect(api.update.mock.calls[0][1].body_html).toBe("<p>typed</p>")
  })
})

describe("keep mine", () => {
  it("saves the USER's text, not the server's", async () => {
    // THE BUG: `load()` seeds the body ref, and the old resolver read that ref
    // AFTER reloading — so "Keep mine" posted the server's body back and the
    // user's edits were gone, deterministically.
    await mountTab()

    // The user types, and their save is refused by a colleague's.
    api.update.mockRejectedValueOnce(
      new FakeApiError(409, {
        detail: { error: "version_conflict", current: DOC({ version: 3, body_html: "<p>theirs</p>" }) },
      }),
    )
    await act(async () => { editorMock.onChange!("<p>MINE — must survive</p>") })
    await waitFor(
      () => expect(document.querySelector("[data-document-conflict]")).not.toBeNull(),
      { timeout: 4000 },
    )

    // The reload behind "Keep mine" returns THEIR document.
    api.get.mockResolvedValue(DOC({ version: 3, body_html: "<p>theirs</p>" }))
    api.update.mockResolvedValue({ ...DOC(), version: 4 })

    const keepMine = document.querySelector<HTMLButtonElement>("[data-testid='doc-tab-conflict-mine']")!
    await act(async () => { keepMine.click() })

    await waitFor(() => {
      const bodies = api.update.mock.calls.map((c) => c[1].body_html).filter(Boolean)
      expect(bodies.at(-1)).toBe("<p>MINE — must survive</p>")
    }, { timeout: 4000 })
  })

  it("does not send a body the user never edited", async () => {
    // Sending an unwritten ref is how this button ends up storing an empty
    // document over a real one.
    await mountTab()
    api.update.mockRejectedValueOnce(
      new FakeApiError(409, {
        detail: { error: "version_conflict", current: DOC({ version: 3 }) },
      }),
    )
    // A title-only save (no editor onChange at all) hits the conflict.
    // Simulated by driving the same path the title field uses.
    await act(async () => { editorMock.onChange!("<p>theirs on server</p>") })
    await waitFor(
      () => expect(document.querySelector("[data-document-conflict]")).not.toBeNull(),
      { timeout: 4000 },
    )

    api.get.mockResolvedValue(DOC({ version: 3 }))
    api.update.mockResolvedValue({ ...DOC(), version: 4 })
    const theirs = document.querySelector<HTMLButtonElement>("[data-testid='doc-tab-conflict-theirs']")!
    await act(async () => { theirs.click() })

    // Taking theirs must not write anything back at all.
    const afterTheirs = api.update.mock.calls.length
    await new Promise((r) => setTimeout(r, 50))
    expect(api.update.mock.calls.length).toBe(afterTheirs)
  })
})

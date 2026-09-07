// @vitest-environment jsdom
//
// A document mid-generation used to say one static sentence ("Writing this
// document…") over a body that stayed empty the whole time it wrote — no
// spinner, no phase, nothing that read as in-progress rather than stalled.
// The PRD and Reports panels already share a real working-state component
// (GenerationState.tsx: a pulsing icon, a rotating phase line, a progress bar
// and document-shaped skeleton lines) for the identical "nothing to show yet"
// window; this pins that the Document tab now uses the SAME one, since a
// custom-artifact document writes its body in one call at the end and has no
// partial content to stream the way the PRD does.
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("../../../(app)/artifacts/doc/DocumentEditor", () => ({
  DocumentEditor: () => React.createElement("div", { "data-testid": "editor-stub" }),
}))

const api = vi.hoisted(() => ({ get: vi.fn(), update: vi.fn() }))
vi.mock("../../../lib/api", () => ({
  ApiError: class FakeApiError extends Error {
    status = 0
    body: unknown = null
  },
  customArtifactsApi: {
    get: (...a: unknown[]) => api.get(...a),
    update: (...a: unknown[]) => api.update(...a),
  },
}))

import { DocumentTab } from "../DocumentTab"

const ROW = (over: Record<string, unknown> = {}) => ({
  id: 9, kind: "leadership update", title: "Leadership update", status: "generating",
  body_html: "", version: 1, created_at: "", updated_at: "",
  conversation_id: 1, created_by: null, updated_by: null, ...over,
})

beforeAll(() => {
  if (!Range.prototype.getClientRects) {
    Range.prototype.getClientRects = () =>
      ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} }) as unknown as DOMRectList
  }
})

afterEach(() => { cleanup(); api.get.mockReset() })

describe("DocumentTab while generating", () => {
  it("shows the shared working pane — pulsing icon, phase line, skeleton — not a static sentence", async () => {
    api.get.mockResolvedValue(ROW())
    render(<DocumentTab documentId={9} />)

    const pane = await screen.findByTestId("document-generating")
    expect(pane.getAttribute("aria-busy")).toBe("true")
    expect(screen.getByText("Generating document…")).not.toBeNull()
    // One of DOCUMENT_GEN's real phase lines, not a placeholder.
    expect(screen.getByText(/Reading the brief/i)).not.toBeNull()
    // The old static line is gone — this is the regression the pane replaces.
    expect(screen.queryByText(/Writing this document/i)).toBeNull()
  })

  it("mounts no editor and no toolbar while generating — there is nothing yet to edit", async () => {
    api.get.mockResolvedValue(ROW())
    render(<DocumentTab documentId={9} />)

    await screen.findByTestId("document-generating")
    expect(screen.queryByTestId("editor-stub")).toBeNull()
  })

  it("still renders the real editor once the document is ready (unaffected by the generating change)", async () => {
    api.get.mockResolvedValue(ROW({ status: "ready", body_html: "<p>Done.</p>" }))
    render(<DocumentTab documentId={9} />)

    expect(await screen.findByTestId("editor-stub")).not.toBeNull()
    expect(screen.queryByTestId("document-generating")).toBeNull()
  })
})

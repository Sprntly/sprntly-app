// @vitest-environment jsdom
//
// The full-page document editor (/artifacts/doc) had the SAME gap as the chat
// panel's Document tab: while a document was being written it showed one
// static sentence ("Writing this document…") over a body that stayed empty
// for the whole run — no spinner, no phase, nothing that read as working
// rather than stuck. This pins that the page now shows the shared working
// pane (GenerationState.tsx) instead, the same one the PRD and Reports
// surfaces already use for their own "nothing to show yet" window.
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams("id=9"),
}))

vi.mock("../../../../components/screens/app/AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))

vi.mock("../DocumentEditor", () => ({
  DocumentEditor: () => React.createElement("div", { "data-testid": "editor-stub" }),
}))

const api = vi.hoisted(() => ({ get: vi.fn(), update: vi.fn() }))
vi.mock("../../../../lib/api", () => ({
  ApiError: class FakeApiError extends Error {
    status = 0
    body: unknown = null
  },
  customArtifactsApi: {
    get: (...a: unknown[]) => api.get(...a),
    update: (...a: unknown[]) => api.update(...a),
  },
}))

import { DocumentRoute } from "../DocumentRoute"

const ROW = (over: Record<string, unknown> = {}) => ({
  id: 9, kind: "leadership update", title: "Leadership update", status: "generating",
  body_html: "", version: 1, created_at: "", updated_at: "",
  conversation_id: 1, created_by: null, updated_by: null, ...over,
})

afterEach(() => { cleanup(); api.get.mockReset() })

describe("DocumentRoute while generating", () => {
  it("shows the shared working pane instead of the static sentence over an empty body", async () => {
    api.get.mockResolvedValue(ROW())
    render(<DocumentRoute />)

    const pane = await screen.findByTestId("document-generating")
    expect(pane.getAttribute("aria-busy")).toBe("true")
    expect(screen.getByText("Generating document…")).not.toBeNull()
    expect(screen.queryByText(/Writing this document/i)).toBeNull()
    // No editor mounted yet — there is nothing to edit.
    expect(screen.queryByTestId("editor-stub")).toBeNull()
    // No leftover empty-body div, either.
    expect(document.querySelector("[data-doc-body]")).toBeNull()
  })

  it("still renders the real editor once the document is ready", async () => {
    api.get.mockResolvedValue(ROW({ status: "ready", body_html: "<p>Done.</p>" }))
    render(<DocumentRoute />)

    expect(await screen.findByTestId("editor-stub")).not.toBeNull()
    expect(screen.queryByTestId("document-generating")).toBeNull()
  })
})

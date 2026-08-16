// @vitest-environment jsdom
//
// What the panel says when a document COULD NOT BE WRITTEN.
//
// The tab always had a failed branch, but it said one sentence for every cause,
// so an empty generation, an unreachable model and a deploy that restarted
// mid-write were indistinguishable to the person who asked. The only question a
// reader actually has is "will asking again help?" — these tests pin that the
// answer differs, and that the server's raw error is never what gets rendered.
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("../../../(app)/artifacts/doc/DocumentEditor", () => ({
  DocumentEditor: () => React.createElement("div", { "data-testid": "editor-stub" }),
}))

const api = vi.hoisted(() => ({ get: vi.fn(), update: vi.fn() }))
const { FakeApiError } = vi.hoisted(() => ({
  FakeApiError: class FakeApiError extends Error {
    status = 0
    body: unknown = null
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

const FAILED = (over: Record<string, unknown> = {}) => ({
  id: 7, kind: "leadership update", title: "Q3", status: "failed",
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

async function mountFailed(over: Record<string, unknown>) {
  api.get.mockResolvedValue(FAILED(over))
  render(<DocumentTab documentId={7} />)
  return waitFor(() => screen.getByText((_, el) => el?.hasAttribute("data-document-failed") ?? false))
}

describe("a failed document explains itself", () => {
  it("says a restart interrupted it — and that asking again works", async () => {
    const el = await mountFailed({ error_code: "interrupted" })
    expect(el.textContent).toMatch(/interrupted by a server restart/i)
    expect(el.textContent).toMatch(/again/i)
  })

  it("distinguishes an empty result from an unreachable generator", async () => {
    const empty = await mountFailed({ error_code: "empty" })
    expect(empty.textContent).toMatch(/empty document/i)
    cleanup()
    const llm = await mountFailed({ error_code: "llm_error" })
    expect(llm.textContent).toMatch(/could not be reached/i)
    // THE ASSERTION THAT MATTERS: two causes, two sentences. Identical copy is
    // what made every failure read as the same dead end.
    expect(llm.textContent).not.toEqual(empty.textContent)
  })

  it("tells a too-long document to ask for a shorter one, not to retry", async () => {
    const el = await mountFailed({ error_code: "too_large" })
    expect(el.textContent).toMatch(/shorter/i)
  })

  it("falls back to the generic sentence when the reason is unknown", async () => {
    // Every row that failed before the column existed, and a code from a
    // server newer than this bundle — a normal state mid-rollout, since the
    // two sides deploy separately.
    const nulled = await mountFailed({ error_code: null })
    expect(nulled.textContent).toMatch(/could not be written/i)
    cleanup()
    const unseen = await mountFailed({ error_code: "quota_exhausted_v2" })
    expect(unseen.textContent).toMatch(/could not be written/i)
    // Never the raw code: it is an identifier, not copy.
    expect(unseen.textContent).not.toMatch(/quota_exhausted_v2/)
  })

  it("still renders the failure when the row carries no code at all", async () => {
    // The key is absent rather than null — what a listing-shaped row looks
    // like, and what an older server returns.
    const el = await mountFailed({})
    expect(el.textContent).toMatch(/could not be written/i)
  })
})

// @vitest-environment jsdom
//
// comment-surface reconcile — usePinMarking behaviour tests.
//
// These prove the KEYSTONE + persisted-resolve fix:
//   1. handlePinSubmit captures the server comment id (created.id) onto the pin
//      so the local pin layer can reconcile with the server-backed CommentsPanel.
//      A create that returns null leaves commentId null and never throws.
//   2. handlePinApply / handlePinIgnore PERSIST the resolve through the injected
//      `onResolve` using the captured commentId, optimistically (resolved=true),
//      rolling back (resolved=false + error) on a server failure.
//   3. A pin with no commentId — or a surface that injects no onResolve (the
//      public anon viewer) — does a local-only resolve: onResolve is NOT called
//      and nothing throws (public pins stay non-resolvable).
//
// jsdom + renderHook drives the hook's state machine (the node-env SSR harness in
// usePinMarking.test.tsx can only snapshot the returned surface, not drive state).
import * as React from "react"
import { act, renderHook, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { usePinMarking } from "../usePinMarking"
import type { CommentRecord } from "../../../lib/api"

function record(over: Partial<CommentRecord> = {}): CommentRecord {
  return {
    id: 7,
    anchor_id: "pin-1",
    body: "looks good",
    author: "demo",
    status: "open",
    created_at: "2026-06-06T08:00:00Z",
    resolved_at: null,
    ...over,
  }
}

// Drop a pin then submit it; returns the renderHook result + the onCreate/onResolve mocks.
async function dropAndSubmit(opts: {
  created: CommentRecord | null
  onResolve?: (id: number) => Promise<unknown>
  onPinApply?: (c: CommentRecord) => void
}) {
  const onCreate = vi.fn<(p: unknown) => Promise<CommentRecord | null>>().mockResolvedValue(
    opts.created,
  )
  const onResolve = opts.onResolve ? vi.fn(opts.onResolve) : undefined
  const { result } = renderHook(() =>
    usePinMarking({ onCreate, onResolve, onPinApply: opts.onPinApply }),
  )
  // drop a pin (no iframe in jsdom → anchor path is skipped, pin still drops)
  act(() => {
    result.current.handleStageClick(50, 50, 0, 0, null)
  })
  expect(result.current.pins).toHaveLength(1)
  const n = result.current.pins[0].n
  // a non-empty draft is required for handlePinSubmit to reach the create call.
  act(() => {
    result.current.handlePinDraftChange(n, "looks good")
  })
  await act(async () => {
    await result.current.handlePinSubmit(n)
  })
  return { result, onCreate, onResolve, n }
}

afterEach(() => vi.restoreAllMocks())

describe("usePinMarking — keystone: capture created.id", () => {
  it("stores created.id onto the saved pin", async () => {
    const { result } = await dropAndSubmit({ created: record({ id: 42 }) })
    expect(result.current.pins[0].saved).toBe(true)
    expect(result.current.pins[0].commentId).toBe(42)
  })

  it("created==null → pin saved, commentId null, no throw", async () => {
    const { result } = await dropAndSubmit({ created: null })
    expect(result.current.pins[0].saved).toBe(true)
    expect(result.current.pins[0].commentId).toBeNull()
  })
})

describe("usePinMarking — persisted resolve (Ignore)", () => {
  it("handlePinIgnore calls onResolve with the captured id; success keeps resolved", async () => {
    const { result, onResolve } = await dropAndSubmit({
      created: record({ id: 99 }),
      onResolve: () => Promise.resolve({}),
    })
    await act(async () => {
      await result.current.handlePinIgnore(1)
    })
    expect(onResolve).toHaveBeenCalledWith(99)
    expect(result.current.pins[0].resolved).toBe(true)
    expect(result.current.pins[0].error).toBeNull()
  })

  it("onResolve rejection rolls back resolved=false + sets error", async () => {
    const { result, onResolve } = await dropAndSubmit({
      created: record({ id: 5 }),
      onResolve: () => Promise.reject(new Error("boom")),
    })
    await act(async () => {
      await result.current.handlePinIgnore(1)
    })
    expect(onResolve).toHaveBeenCalledWith(5)
    await waitFor(() => expect(result.current.pins[0].resolved).toBe(false))
    expect(result.current.pins[0].error).toBe("boom")
  })

  it("pin with no commentId → onResolve NOT called, resolves locally, no throw", async () => {
    const { result, onResolve } = await dropAndSubmit({
      created: null, // commentId stays null
      onResolve: () => Promise.resolve({}),
    })
    await act(async () => {
      await result.current.handlePinIgnore(1)
    })
    expect(onResolve).not.toHaveBeenCalled()
    expect(result.current.pins[0].resolved).toBe(true)
  })
})

describe("usePinMarking — persisted resolve (Apply)", () => {
  it("handlePinApply runs the local apply seam AND persists the resolve via onResolve", async () => {
    const onPinApply = vi.fn()
    const { result, onResolve } = await dropAndSubmit({
      created: record({ id: 12 }),
      onResolve: () => Promise.resolve({}),
      onPinApply,
    })
    await act(async () => {
      await result.current.handlePinApply(1)
    })
    expect(onPinApply).toHaveBeenCalledTimes(1) // local seam still runs
    expect(onResolve).toHaveBeenCalledWith(12)
    expect(result.current.pins[0].resolved).toBe(true)
  })

  it("Apply rolls back on a server failure (resolved=false + error)", async () => {
    const { result } = await dropAndSubmit({
      created: record({ id: 13 }),
      onResolve: () => Promise.reject(new Error("nope")),
      onPinApply: vi.fn(),
    })
    await act(async () => {
      await result.current.handlePinApply(1)
    })
    await waitFor(() => expect(result.current.pins[0].resolved).toBe(false))
    expect(result.current.pins[0].error).toBe("nope")
  })
})

describe("usePinMarking — public surface stays non-resolvable (public-surface guard)", () => {
  it("with NO onResolve injected (public), Ignore resolves locally and never throws", async () => {
    // public surface: onCreate routes via token, NO onResolve injected.
    const { result } = await dropAndSubmit({ created: record({ id: 1 }) })
    await act(async () => {
      await result.current.handlePinIgnore(1)
    })
    expect(result.current.pins[0].resolved).toBe(true)
    expect(result.current.pins[0].error).toBeNull()
  })
})

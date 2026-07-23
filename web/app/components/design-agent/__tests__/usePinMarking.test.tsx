// @vitest-environment jsdom
//
// C2b — usePinMarking hook tests. jsdom + testing-library's renderHook/act (the
// same convention useIterateRun.test.tsx already uses in this directory), so
// the returned handlers are exercised across REAL re-renders — not just the
// SSR-frozen snapshot the old node-env harness could observe. The deeper
// occlusion/reconcile/markmode behaviour stays covered by the dedicated
// usePinMarking.*.dom.test.tsx files; this file owns the returned API surface,
// the initial state, and (added here) handlePinApply's await-then-conditionally-
// resolve contract against the shared iterate runner.
import * as React from "react"
import { act, renderHook } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { usePinMarking, type UsePinMarkingReturn } from "../usePinMarking"
import type { CommentRecord } from "../../../lib/api"

function comment(overrides: Partial<CommentRecord> = {}): CommentRecord {
  return {
    id: 42,
    anchor_id: "pin-1",
    body: "make it bigger",
    author: "demo",
    status: "open",
    created_at: "2026-07-01T00:00:00Z",
    resolved_at: null,
    ...overrides,
  }
}

describe("usePinMarking — returned API surface + initial state", () => {
  it("exposes the full pin API and starts empty / mark-off", () => {
    const { result } = renderHook(() => usePinMarking({ onCreate: async () => null }))
    expect(result.current.markMode).toBe(false)
    expect(result.current.pins).toEqual([])
    expect(result.current.computedPinPositions).toEqual({})
    // the full handler surface both surfaces consume
    for (const key of [
      "toggleMark",
      "handleStageClick",
      "handlePinDraftChange",
      "handlePinRemove",
      "handlePinSubmit",
      "handlePinApply",
      "handlePinIgnore",
      "setMarkMode",
    ] as const) {
      expect(typeof result.current[key]).toBe("function")
    }
  })

  it("handlePinSubmit no-ops when the pin does not exist (does not call onCreate)", async () => {
    let calls = 0
    const { result } = renderHook(() =>
      usePinMarking({
        onCreate: async () => {
          calls += 1
          return null
        },
      }),
    )
    // no pins dropped → submitting a non-existent pin must not hit the create-fn
    await act(async () => {
      await result.current.handlePinSubmit(99)
    })
    expect(calls).toBe(0)
  })
})

/** Drop a pin, fill its draft, and submit it to a saved state (`saved: true`,
 *  `commentId` set from the mocked create's returned record). Returns the
 *  dropped pin's number. */
async function dropAndSavePin(
  result: { current: UsePinMarkingReturn },
): Promise<number> {
  act(() => {
    result.current.handleStageClick(10, 10, 0, 0, null)
  })
  const n = result.current.pins[result.current.pins.length - 1].n
  act(() => {
    result.current.handlePinDraftChange(n, "make it bigger")
  })
  await act(async () => {
    await result.current.handlePinSubmit(n)
  })
  return n
}

describe("usePinMarking — handlePinApply awaits onPinIterate before resolving the pin", () => {
  it("test_pin_apply_rejected_does_not_resolve_the_pin", async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined)
    const onPinIterate = vi.fn().mockResolvedValue(false)
    const created = comment({ id: 77 })

    const { result } = renderHook(() =>
      usePinMarking({
        onCreate: async () => created,
        onPinIterate,
        onResolve,
      }),
    )

    const n = await dropAndSavePin(result)

    await act(async () => {
      await result.current.handlePinApply(n)
    })

    expect(onPinIterate).toHaveBeenCalledTimes(1)
    const pin = result.current.pins.find((p) => p.n === n)
    expect(pin?.resolved).not.toBe(true)
    expect(onResolve).not.toHaveBeenCalled()
  })

  it("test_pin_apply_accepted_resolves_the_pin", async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined)
    const onPinIterate = vi.fn().mockResolvedValue(true)
    const created = comment({ id: 77 })

    const { result } = renderHook(() =>
      usePinMarking({
        onCreate: async () => created,
        onPinIterate,
        onResolve,
      }),
    )

    const n = await dropAndSavePin(result)

    await act(async () => {
      await result.current.handlePinApply(n)
    })

    expect(onPinIterate).toHaveBeenCalledTimes(1)
    const pin = result.current.pins.find((p) => p.n === n)
    expect(pin?.resolved).toBe(true)
    expect(onResolve).toHaveBeenCalledTimes(1)
    expect(onResolve).toHaveBeenCalledWith(77)
  })
})

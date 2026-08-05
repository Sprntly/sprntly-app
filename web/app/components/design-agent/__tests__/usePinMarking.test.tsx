// @vitest-environment jsdom
//
// C2b — usePinMarking hook smoke test, PLUS the requireName-gate regression
// tests and the handlePinApply await-then-conditionally-resolve tests below.
// The original smoke test mounted the hook via renderToStaticMarkup
// (SSR-only, no interactivity) — that's kept as-is (renderToStaticMarkup
// works fine under jsdom too). The requireName-gate and handlePinApply tests
// need a real interactive render (drop a pin, mutate its draft, submit,
// observe the resulting state), so they use `renderHook` + `act` from
// @testing-library/react, the same pattern as the sibling
// usePinMarking.*.dom.test.tsx files — hence the file-level jsdom environment
// switch. The deeper behaviour (the optimistic submit machine, the anchor
// capture) is guarded by the source-invariants in PostGenerationResult.test.tsx
// (the logic was moved verbatim) + the two container integration tests on both
// surfaces.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { act, renderHook } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { usePinMarking, type UsePinMarkingReturn } from "../usePinMarking"
import type { CommentRecord } from "../../../lib/api"

function captureHook(
  params: Parameters<typeof usePinMarking>[0],
): UsePinMarkingReturn {
  let captured: UsePinMarkingReturn | null = null
  function Harness() {
    captured = usePinMarking(params)
    return null
  }
  renderToStaticMarkup(React.createElement(Harness))
  if (!captured) throw new Error("hook did not run")
  return captured
}

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
    const api = captureHook({ onCreate: async () => null })
    expect(api.markMode).toBe(false)
    expect(api.pins).toEqual([])
    expect(api.computedPinPositions).toEqual({})
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
      expect(typeof api[key]).toBe("function")
    }
  })

  it("handlePinSubmit no-ops when the pin does not exist (does not call onCreate)", async () => {
    let calls = 0
    const api = captureHook({
      onCreate: async () => {
        calls += 1
        return null
      },
    })
    // no pins dropped → submitting a non-existent pin must not hit the create-fn
    await api.handlePinSubmit(99)
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

describe("usePinMarking — public requireName gate on handlePinSubmit", () => {
  it("test_pin_submit_blocked_by_require_name_sets_visible_error", async () => {
    let createCalls = 0
    const { result } = renderHook(() =>
      usePinMarking({
        onCreate: async () => {
          createCalls += 1
          return null
        },
        requireName: true,
      }),
    )
    act(() => result.current.handleStageClick(50, 50, 0, 0, null))
    const n = result.current.pins[0].n
    act(() => result.current.handlePinDraftChange(n, "hello there"))
    await act(async () => {
      await result.current.handlePinSubmit(n)
    })
    const pin = result.current.pins.find((p) => p.n === n)
    expect(pin?.error).toBeTruthy()
    expect(typeof pin?.error).toBe("string")
    expect(createCalls).toBe(0)
  })

  it("test_pin_submit_blocked_by_require_name_preserves_draft", async () => {
    const { result } = renderHook(() =>
      usePinMarking({
        onCreate: async () => null,
        requireName: true,
      }),
    )
    act(() => result.current.handleStageClick(50, 50, 0, 0, null))
    const n = result.current.pins[0].n
    act(() => result.current.handlePinDraftChange(n, "hello there"))
    await act(async () => {
      await result.current.handlePinSubmit(n)
    })
    const pin = result.current.pins.find((p) => p.n === n)
    expect(pin?.draft).toBe("hello there")
  })

  it("test_pin_submit_blocked_by_require_name_still_calls_on_require_name", async () => {
    let onRequireNameCalls = 0
    const { result } = renderHook(() =>
      usePinMarking({
        onCreate: async () => null,
        requireName: true,
        onRequireName: () => {
          onRequireNameCalls += 1
        },
      }),
    )
    act(() => result.current.handleStageClick(50, 50, 0, 0, null))
    const n = result.current.pins[0].n
    act(() => result.current.handlePinDraftChange(n, "hello there"))
    await act(async () => {
      await result.current.handlePinSubmit(n)
    })
    expect(onRequireNameCalls).toBe(1)
  })

  it("test_pin_submit_succeeds_normally_when_name_not_required", async () => {
    let createCalls = 0
    const { result } = renderHook(() =>
      usePinMarking({
        onCreate: async (payload) => {
          createCalls += 1
          expect(payload.body).toBe("hello there")
          return {
            id: 1,
            anchor_id: payload.anchor_id,
            body: payload.body,
            author: "demo",
            status: "open",
            created_at: new Date().toISOString(),
            resolved_at: null,
          }
        },
        requireName: false,
      }),
    )
    act(() => result.current.handleStageClick(50, 50, 0, 0, null))
    const n = result.current.pins[0].n
    act(() => result.current.handlePinDraftChange(n, "hello there"))
    const pinBefore = result.current.pins.find((p) => p.n === n)
    expect(pinBefore?.error).toBe(null)
    await act(async () => {
      await result.current.handlePinSubmit(n)
    })
    const pinAfter = result.current.pins.find((p) => p.n === n)
    expect(pinAfter?.error).toBe(null)
    expect(pinAfter?.busy).toBe(false)
    expect(pinAfter?.saved).toBe(true)
    expect(createCalls).toBe(1)
  })
})

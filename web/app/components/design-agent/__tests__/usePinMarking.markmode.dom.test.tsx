// @vitest-environment jsdom
//
// usePinMarking — mark-mode persistence across a pin drop.
//
// The public share viewer stays in mark mode through repeated
// mark→comment→mark cycles: dropping a pin must NOT auto-exit mark mode, so the
// next stage click starts a fresh pin without re-enabling the element selector.
// The signed-in editor keeps its original drop-then-exit behaviour. This proves
// the `stayInMarkMode` flag gates exactly that difference — nothing else.
//
// jsdom + renderHook drives the hook's state machine (the node-env SSR harness in
// usePinMarking.test.tsx can only snapshot the returned surface). handleStageClick
// is called with anchor=null so the iframe/anchor branch is skipped (no real
// prototype iframe needed) — it still drops the pin and runs the mark-mode exit.
import * as React from "react"
import { act, renderHook } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { usePinMarking } from "../usePinMarking"

afterEach(() => {
  vi.clearAllMocks()
})

describe("usePinMarking — stayInMarkMode gates the drop-time exit", () => {
  it("public (stayInMarkMode: true): mark mode STAYS on after a pin is dropped", () => {
    const { result } = renderHook(() =>
      usePinMarking({ onCreate: async () => null, stayInMarkMode: true }),
    )
    act(() => result.current.setMarkMode(true))
    expect(result.current.markMode).toBe(true)
    // Drop a pin (anchor=null → skips the iframe branch, still drops + runs exit).
    act(() => result.current.handleStageClick(50, 50, 0, 0, null))
    // Pin dropped …
    expect(result.current.pins).toHaveLength(1)
    // … but mark mode is still ON — the next click can start a new pin.
    expect(result.current.markMode).toBe(true)
  })

  it("signed-in default (flag omitted): mark mode EXITS after a pin is dropped", () => {
    const { result } = renderHook(() =>
      usePinMarking({ onCreate: async () => null }),
    )
    act(() => result.current.setMarkMode(true))
    expect(result.current.markMode).toBe(true)
    act(() => result.current.handleStageClick(50, 50, 0, 0, null))
    expect(result.current.pins).toHaveLength(1)
    // Editor behaviour preserved: dropping a pin exits mark mode.
    expect(result.current.markMode).toBe(false)
  })

  it("explicit toggle still turns mark mode off even when stayInMarkMode is set", () => {
    const { result } = renderHook(() =>
      usePinMarking({ onCreate: async () => null, stayInMarkMode: true }),
    )
    act(() => result.current.setMarkMode(true))
    act(() => result.current.handleStageClick(50, 50, 0, 0, null))
    expect(result.current.markMode).toBe(true)
    // The explicit off-affordance (Mark toggle) is unaffected by the flag.
    act(() => result.current.toggleMark())
    expect(result.current.markMode).toBe(false)
  })
})

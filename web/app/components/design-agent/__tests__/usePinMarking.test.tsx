// C2b — usePinMarking hook smoke test. Node-env vitest (no DOM, no router, no
// testing-library), so we mount the hook inside a tiny harness component and
// render it via renderToStaticMarkup, capturing the returned API on first render
// (SSR runs the render body / hook call but not effects — which is enough to
// assert the returned surface + initial state). The deeper behaviour (the
// optimistic submit machine, the anchor capture) is guarded by the source-
// invariants in PostGenerationResult.test.tsx (the logic was moved verbatim) +
// the two container integration tests on both surfaces.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { usePinMarking, type UsePinMarkingReturn } from "../usePinMarking"

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

// @vitest-environment jsdom
//
// useIterateRun.dismissQuestion — the "Skip this change" path. Skipping must
// clear the FE pending state, record a skipped activity turn, hit the dismiss
// endpoint, and crucially NOT iterate and NOT reload the preview (onComplete
// never fires). Driven via renderHook + act under jsdom.
import * as React from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { useIterateRun } from "../useIterateRun"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("useIterateRun.dismissQuestion", () => {
  it("skips: dismisses server-side, clears pending, records a skipped turn, and never iterates or completes", async () => {
    const iterate = vi.fn()
    const get = vi.fn()
    const dismissQuestion = vi.fn().mockResolvedValue({ ok: true })
    const onComplete = vi.fn()

    const { result } = renderHook(() =>
      useIterateRun({
        prototypeId: 4242,
        onComplete,
        api: { iterate, get, dismissQuestion },
      }),
    )

    await act(async () => {
      await result.current.dismissQuestion()
    })

    // dismiss endpoint hit once with the prototype id
    expect(dismissQuestion).toHaveBeenCalledTimes(1)
    expect(dismissQuestion).toHaveBeenCalledWith(4242)
    // never iterated, never completed → no preview reload
    expect(iterate).not.toHaveBeenCalled()
    expect(onComplete).not.toHaveBeenCalled()
    // FE pending cleared
    expect(result.current.pendingQuestion).toBeNull()
    // a skipped turn was recorded in the activity stream
    expect(result.current.activity.some((e) => e.kind === "skipped")).toBe(true)
  })
})

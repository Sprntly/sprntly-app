// @vitest-environment jsdom
//
// Live-preview wiring in runAskGeneration: the optional onPartial callback
// opens the SSE token stream alongside the authoritative status poll and
// forwards the accumulating answer markdown (throttled). The poll stays the
// only source of the finished reply; the stream is always torn down before
// the promise settles, and a cache-hit (immediately-ready) ask never opens one.
import { afterEach, describe, expect, it, vi } from "vitest"

const { subscribeMock, stopMock } = vi.hoisted(() => ({
  subscribeMock: vi.fn(),
  stopMock: vi.fn(),
}))
vi.mock("../streamGeneration", () => ({
  subscribeToGenerationStream: (...args: unknown[]) => {
    subscribeMock(...args)
    return stopMock
  },
}))

import { askApi } from "../api"
import { runAskGeneration, resumeAskGeneration } from "../runAskGeneration"

type StreamHandlers = {
  onDelta: (full: string, delta: string) => void
  onDone?: () => void
  onError?: () => void
}

const READY_BODY = {
  status: "ready",
  answer: "Final answer.",
  key_points: [],
  citations: [],
  confidence: 1,
  unanswered: "",
} as never

afterEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
  localStorage.clear()
})

describe("runAskGeneration — live answer stream", () => {
  it("streams partial markdown to onPartial while polling; final reply comes from the poll", async () => {
    vi.spyOn(askApi, "start").mockResolvedValue({ ask_id: 41, status: "generating" } as never)
    vi.spyOn(askApi, "get").mockResolvedValue(READY_BODY)

    const partials: string[] = []
    const resultP = runAskGeneration("q?", "acme", "tab-1", {
      onPartial: (md) => partials.push(md),
    })

    await vi.waitFor(() => expect(subscribeMock).toHaveBeenCalledTimes(1))
    const handlers = subscribeMock.mock.calls[0][1] as StreamHandlers

    // First delta renders immediately (leading edge of the throttle).
    handlers.onDelta("The top", "The top")
    expect(partials).toEqual(["The top"])

    const result = await resultP
    expect(result.answer).toBe("Final answer.")
    // The stream is always torn down before the promise settles.
    expect(stopMock).toHaveBeenCalled()
  })

  it("a cache-hit (immediately-ready) ask never opens a stream", async () => {
    vi.spyOn(askApi, "start").mockResolvedValue({ ask_id: 42, status: "ready" } as never)
    vi.spyOn(askApi, "get").mockResolvedValue(READY_BODY)

    const result = await runAskGeneration("q?", "acme", "tab-1", {
      onPartial: () => {},
    })
    expect(result.answer).toBe("Final answer.")
    expect(subscribeMock).not.toHaveBeenCalled()
  })

  it("does not open a stream when no onPartial is given (existing callers unchanged)", async () => {
    vi.spyOn(askApi, "start").mockResolvedValue({ ask_id: 43, status: "generating" } as never)
    vi.spyOn(askApi, "get").mockResolvedValue(READY_BODY)

    const result = await runAskGeneration("q?", "acme", "tab-1")
    expect(result.answer).toBe("Final answer.")
    expect(subscribeMock).not.toHaveBeenCalled()
  })

  it("resumeAskGeneration re-attaches the stream so a remount catches up via replay", async () => {
    vi.spyOn(askApi, "get").mockResolvedValue(READY_BODY)

    const partials: string[] = []
    const resultP = resumeAskGeneration(
      44, "acme", "tab-1", undefined, undefined,
      (md) => partials.push(md),
    )

    await vi.waitFor(() => expect(subscribeMock).toHaveBeenCalledTimes(1))
    const handlers = subscribeMock.mock.calls[0][1] as StreamHandlers
    // The catch-up replay frame arrives as one big accumulated onDelta.
    handlers.onDelta("Everything written so far…", "Everything written so far…")
    expect(partials).toEqual(["Everything written so far…"])

    const result = await resultP
    expect(result.answer).toBe("Final answer.")
    expect(stopMock).toHaveBeenCalled()
  })
})

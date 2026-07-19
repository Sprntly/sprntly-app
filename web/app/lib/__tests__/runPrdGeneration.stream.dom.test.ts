// @vitest-environment jsdom
//
// Live-preview wiring in runPrdGeneration: the optional onPartial callback
// opens the SSE token stream alongside the authoritative poll, forwards the
// accumulating Part A HTML (throttled), and the stream's terminal `done` frame
// wakes the poll immediately instead of waiting out the 4s tick.
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

import { prdApi } from "../api"
import { runPrdGeneration, throttlePartial } from "../runPrdGeneration"

type StreamHandlers = {
  onDelta: (full: string, delta: string) => void
  onDone?: () => void
  onError?: () => void
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
  localStorage.clear()
})

describe("runPrdGeneration — live preview stream", () => {
  it("streams partial HTML to onPartial and wakes the poll on the done frame", async () => {
    vi.spyOn(prdApi, "generate").mockResolvedValue({ prd_id: 11 } as never)
    const get = vi
      .spyOn(prdApi, "get")
      .mockResolvedValueOnce({ id: 11, status: "generating", payload_md: "" } as never)
      .mockResolvedValue({ id: 11, status: "ready", payload_md: "# T\n\nBody." } as never)

    const partials: string[] = []
    const resultP = runPrdGeneration(
      { briefId: 1, insightIndex: 0 },
      (html) => partials.push(html),
    )

    await vi.waitFor(() => expect(subscribeMock).toHaveBeenCalledTimes(1))
    const handlers = subscribeMock.mock.calls[0][1] as StreamHandlers

    // First delta renders immediately (leading edge of the throttle).
    handlers.onDelta("<!doctype html><h1>Hi", "<!doctype html><h1>Hi")
    expect(partials).toEqual(["<!doctype html><h1>Hi"])

    // The terminal frame wakes the sleeping poll — the ready status is read
    // right away; without the wake this test would sit through a 4s tick and
    // blow the default timeout.
    handlers.onDone!()
    const result = await resultP
    expect(result.ok).toBe(true)
    expect(get).toHaveBeenCalledTimes(2)
    // The stream is always torn down before returning.
    expect(stopMock).toHaveBeenCalled()
  })

  it("does not open a stream when no onPartial is given", async () => {
    vi.spyOn(prdApi, "generate").mockResolvedValue({ prd_id: 12 } as never)
    vi.spyOn(prdApi, "get").mockResolvedValue({ id: 12, status: "ready", payload_md: "# T\n\nB." } as never)

    const result = await runPrdGeneration({ briefId: 1, insightIndex: 0 })
    expect(result.ok).toBe(true)
    expect(subscribeMock).not.toHaveBeenCalled()
  })
})

describe("throttlePartial", () => {
  it("fires the leading edge immediately, coalesces a burst, ends on the latest html", () => {
    vi.useFakeTimers()
    try {
      const out: string[] = []
      const t = throttlePartial((h) => out.push(h), 400)
      t.push("a")
      t.push("ab")
      t.push("abc")
      // Leading edge only — the burst is coalesced into one trailing update.
      expect(out).toEqual(["a"])
      vi.advanceTimersByTime(400)
      expect(out).toEqual(["a", "abc"])
    } finally {
      vi.useRealTimers()
    }
  })

  it("cancel drops the pending trailing update (no stale preview after teardown)", () => {
    vi.useFakeTimers()
    try {
      const out: string[] = []
      const t = throttlePartial((h) => out.push(h), 400)
      t.push("a")
      t.push("ab") // pending trailing update
      t.cancel()
      vi.advanceTimersByTime(1000)
      expect(out).toEqual(["a"])
    } finally {
      vi.useRealTimers()
    }
  })
})

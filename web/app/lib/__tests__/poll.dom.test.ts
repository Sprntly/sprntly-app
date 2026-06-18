// @vitest-environment jsdom
//
// Unit tests for the shared visibility-aware poll helpers (poll.ts), the
// background-throttling fix extracted from the brief poller. Background tabs
// throttle setTimeout to ~1/min, so a plain setTimeout sleep stalls polling
// though the server-side job finishes; sleepUntilNextPoll wakes the instant the
// tab is refocused, and pollUntil uses a Date.now() wall-clock budget (not a
// tick count) so it still times out correctly when throttled.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { sleepUntilNextPoll, pollUntil } from "../poll"

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  })
}

describe("sleepUntilNextPoll", () => {
  it("resolves after the timeout when the tab stays visible", async () => {
    setVisibility("visible")
    let resolved = false
    const p = sleepUntilNextPoll(5000).then(() => {
      resolved = true
    })

    // Before the timer fires, still pending.
    await vi.advanceTimersByTimeAsync(4000)
    expect(resolved).toBe(false)

    await vi.advanceTimersByTimeAsync(1000)
    await p
    expect(resolved).toBe(true)
  })

  it("resolves EARLY when a hidden tab becomes visible (visibilitychange)", async () => {
    setVisibility("hidden")
    let resolved = false
    const p = sleepUntilNextPoll(60_000).then(() => {
      resolved = true
    })

    // Well short of the 60s timer — but a refocus should wake it immediately.
    await vi.advanceTimersByTimeAsync(2000)
    expect(resolved).toBe(false)

    setVisibility("visible")
    document.dispatchEvent(new Event("visibilitychange"))
    await p
    expect(resolved).toBe(true)
  })

  it("does NOT resolve early when the event fires while still hidden", async () => {
    setVisibility("hidden")
    let resolved = false
    const p = sleepUntilNextPoll(10_000).then(() => {
      resolved = true
    })

    // A visibilitychange while still hidden (e.g. partial occlusion) must not wake.
    document.dispatchEvent(new Event("visibilitychange"))
    await vi.advanceTimersByTimeAsync(5000)
    expect(resolved).toBe(false)

    await vi.advanceTimersByTimeAsync(5000)
    await p
    expect(resolved).toBe(true)
  })
})

describe("pollUntil", () => {
  it("polls immediately then stops as soon as isDone is true", async () => {
    setVisibility("visible")
    const statuses = ["generating", "generating", "ready"]
    let i = 0
    const fetchStatus = vi.fn(async () => ({ status: statuses[i++] ?? "ready" }))

    const promise = pollUntil({
      fetchStatus,
      isDone: (v) => v.status === "ready",
      maxMs: 60_000,
      intervalMs: 1000,
    })

    // First fetch is immediate; then one sleep+fetch per remaining tick.
    await vi.advanceTimersByTimeAsync(1000)
    await vi.advanceTimersByTimeAsync(1000)
    const result = await promise

    expect(result.status).toBe("ready")
    expect(fetchStatus).toHaveBeenCalledTimes(3)
  })

  it("respects the Date.now() wall-clock budget and returns the last value on timeout", async () => {
    setVisibility("visible")
    const fetchStatus = vi.fn(async () => ({ status: "generating" }))

    const promise = pollUntil({
      fetchStatus,
      isDone: (v) => v.status === "ready",
      maxMs: 3000,
      intervalMs: 1000,
    })

    // Drive past the 3s budget; the loop must stop and return the last value.
    await vi.advanceTimersByTimeAsync(5000)
    const result = await promise

    expect(result.status).toBe("generating")
    // Never-ending: it gives up by the budget rather than spinning forever.
    expect(fetchStatus.mock.calls.length).toBeLessThanOrEqual(5)
  })

  it("bails out when isCancelled becomes true", async () => {
    setVisibility("visible")
    let cancelled = false
    const fetchStatus = vi.fn(async () => ({ status: "generating" }))

    const promise = pollUntil({
      fetchStatus,
      isDone: (v) => v.status === "ready",
      maxMs: 60_000,
      intervalMs: 1000,
      isCancelled: () => cancelled,
    })

    await vi.advanceTimersByTimeAsync(1000)
    cancelled = true
    await vi.advanceTimersByTimeAsync(1000)
    await promise

    // One immediate fetch + at most the in-flight tick before cancel was seen.
    expect(fetchStatus.mock.calls.length).toBeLessThanOrEqual(2)
  })
})

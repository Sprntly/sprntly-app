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

// The ask's SSE `done` frame lands before the next poll tick would, so without
// an external wake the finished answer sits rendered-but-unshown for up to a
// full interval. The wake must SHORTEN the sleep without ever supplying a
// value — the polled row stays authoritative.
describe("wakeOn (external early wake)", () => {
  it("cuts the sleep short when the wake fires", async () => {
    setVisibility("visible")
    let wake: (() => void) | null = null
    let resolved = false

    const p = sleepUntilNextPoll(30_000, (w) => {
      wake = w
      return () => {
        wake = null
      }
    }).then(() => {
      resolved = true
    })

    await vi.advanceTimersByTimeAsync(100)
    expect(resolved).toBe(false)

    wake!()
    await p
    expect(resolved).toBe(true)
  })

  it("unsubscribes on every exit path, including the plain timeout", async () => {
    setVisibility("visible")
    const off = vi.fn()

    const p = sleepUntilNextPoll(1000, () => off)
    await vi.advanceTimersByTimeAsync(1000)
    await p

    expect(off).toHaveBeenCalledTimes(1)
  })

  it("survives a wake source that fires synchronously", async () => {
    setVisibility("visible")
    const off = vi.fn()

    // Fires immediately, before the subscribe call has even returned.
    const p = sleepUntilNextPoll(30_000, (w) => {
      w()
      return off
    })
    await p

    // Still unsubscribed, rather than parking a listener nothing will clear.
    expect(off).toHaveBeenCalledTimes(1)
  })

  it("never stalls the poll when the wake source throws", async () => {
    setVisibility("visible")
    const p = sleepUntilNextPoll(1000, () => {
      throw new Error("broken wake source")
    })
    await vi.advanceTimersByTimeAsync(1000)
    await expect(p).resolves.toBeUndefined()
  })

  it("re-reads status on wake instead of resolving from it", async () => {
    setVisibility("visible")
    let wake: (() => void) | null = null
    let status = "generating"
    const fetchStatus = vi.fn(async () => ({ status }))

    const promise = pollUntil({
      fetchStatus,
      isDone: (v) => v.status === "ready",
      maxMs: 60_000,
      intervalMs: 30_000,
      wakeOn: (w) => {
        wake = w
        return () => {
          wake = null
        }
      },
    })

    await vi.advanceTimersByTimeAsync(10)
    expect(fetchStatus).toHaveBeenCalledTimes(1)

    // The job finished; the wake only prompts another read.
    status = "ready"
    wake!()
    const result = await promise

    expect(result.status).toBe("ready")
    // Second read came from the wake, not from the 30s interval elapsing.
    expect(fetchStatus).toHaveBeenCalledTimes(2)
  })
})

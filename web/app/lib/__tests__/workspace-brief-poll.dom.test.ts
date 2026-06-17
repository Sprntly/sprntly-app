// @vitest-environment jsdom
//
// Tests for pollBriefStatus' background-resilience fix (the onboarding "stall"
// half of the refocus bug). Background tabs throttle setTimeout to ~1/min, so
// the poller would sleep up to a minute between status checks. The fix wakes
// the sleep immediately when the tab becomes visible again, so a refocused tab
// re-reads the (server-side, idempotent) job status at once.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { pollBriefStatus } from "../workspace-brief"
import { briefApi, type BriefStatus } from "../api"

const statusMock = vi.fn((_company?: string): Promise<BriefStatus> => {
  throw new Error("not configured")
})

beforeEach(() => {
  vi.spyOn(briefApi, "status").mockImplementation((c?: string) => statusMock(c))
})

afterEach(() => {
  vi.restoreAllMocks()
  statusMock.mockReset()
})

function setVisibility(state: "hidden" | "visible") {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  })
}

function fireVisibilityChange(state: "hidden" | "visible") {
  setVisibility(state)
  document.dispatchEvent(new Event("visibilitychange"))
}

describe("pollBriefStatus — background resilience", () => {
  it("returns immediately when the first status is terminal (no sleep)", async () => {
    statusMock.mockResolvedValue({ company: "acme", status: "ready" })
    const out = await pollBriefStatus("acme")
    expect(out.status).toBe("ready")
    expect(statusMock).toHaveBeenCalledTimes(1)
  })

  it("re-polls immediately on visibilitychange→visible instead of waiting the full poll interval", async () => {
    // First check: still generating → poller sleeps. We then "refocus" the tab
    // to wake the sleep early; the next check is terminal.
    statusMock
      .mockResolvedValueOnce({ company: "acme", status: "generating" })
      .mockResolvedValueOnce({ company: "acme", status: "ready" })

    setVisibility("hidden")
    const p = pollBriefStatus("acme", { maxMs: 60_000 })

    // Let the first status() resolve and the sleep be set up.
    await Promise.resolve()
    await Promise.resolve()
    expect(statusMock).toHaveBeenCalledTimes(1)

    // Refocus the backgrounded tab → wakes the sleep without waiting POLL_MS.
    fireVisibilityChange("visible")

    const out = await p
    expect(out.status).toBe("ready")
    expect(statusMock).toHaveBeenCalledTimes(2)
  })

  it("stops once the wall-clock budget is exhausted, reporting 'generating'", async () => {
    statusMock.mockResolvedValue({ company: "acme", status: "generating" })
    // maxMs:0 → loop body never runs; returns the non-terminal generating
    // sentinel rather than hanging.
    const out = await pollBriefStatus("acme", { maxMs: 0 })
    expect(out).toEqual({ company: "acme", status: "generating" })
    expect(statusMock).not.toHaveBeenCalled()
  })
})

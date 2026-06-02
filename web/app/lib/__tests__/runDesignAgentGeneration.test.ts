import { afterEach, describe, expect, it, vi } from "vitest"
import { designAgentApi, type PrototypeRecord } from "../api"
import { runDesignAgentGeneration } from "../runDesignAgentGeneration"

function proto(over: Partial<PrototypeRecord>): PrototypeRecord {
  return {
    id: 1,
    status: "generating",
    bundle_url: null,
    error: null,
    ...over,
  }
}

describe("runDesignAgentGeneration", () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it("returns ok immediately when status is already ready", async () => {
    vi.spyOn(designAgentApi, "get").mockResolvedValueOnce(
      proto({ status: "ready", bundle_url: "https://x/bundle" }),
    )
    const r = await runDesignAgentGeneration({ prototypeId: 1 })
    expect(r).toEqual({
      ok: true,
      prototype: proto({ status: "ready", bundle_url: "https://x/bundle" }),
    })
  })

  it("polls every TICK_MS (4s) until ready — 3 fetches, ~8s elapsed", async () => {
    vi.useFakeTimers()
    const get = vi
      .spyOn(designAgentApi, "get")
      .mockResolvedValueOnce(proto({ status: "generating" }))
      .mockResolvedValueOnce(proto({ status: "generating" }))
      .mockResolvedValueOnce(proto({ status: "ready", bundle_url: "b" }))

    const p = runDesignAgentGeneration({ prototypeId: 1 })
    // Drive the two 4s ticks between the three GETs.
    await vi.advanceTimersByTimeAsync(8000)
    const r = await p

    expect(get).toHaveBeenCalledTimes(3)
    expect(get).toHaveBeenCalledWith(1)
    expect(r.ok).toBe(true)
  })

  it("returns fail with the backend error on status failed", async () => {
    vi.spyOn(designAgentApi, "get").mockResolvedValueOnce(
      proto({ status: "failed", error: "oops" }),
    )
    const r = await runDesignAgentGeneration({ prototypeId: 1 })
    expect(r).toEqual({ ok: false, message: "oops" })
  })

  it("returns a generic message on status failed with no error text", async () => {
    vi.spyOn(designAgentApi, "get").mockResolvedValueOnce(
      proto({ status: "failed", error: null }),
    )
    const r = await runDesignAgentGeneration({ prototypeId: 1 })
    expect(r).toEqual({ ok: false, message: "Generation failed" })
  })

  it("returns the retry message on status invalidated", async () => {
    vi.spyOn(designAgentApi, "get").mockResolvedValueOnce(
      proto({ status: "invalidated" }),
    )
    const r = await runDesignAgentGeneration({ prototypeId: 1 })
    expect(r).toEqual({ ok: false, message: "Template invalidated; retry" })
  })

  it("returns the timeout message after MAX_MS (6 min) of generating", async () => {
    vi.useFakeTimers()
    vi.spyOn(designAgentApi, "get").mockResolvedValue(
      proto({ status: "generating" }),
    )
    const p = runDesignAgentGeneration({ prototypeId: 1 })
    // Past the 6-minute cap (with a tick of slack).
    await vi.advanceTimersByTimeAsync(6 * 60 * 1000 + 4000)
    const r = await p
    expect(r).toEqual({ ok: false, message: "Generation timed out (6 minutes)" })
  })

  it("never throws — a rejecting GET is surfaced as ok:false", async () => {
    vi.spyOn(designAgentApi, "get").mockRejectedValueOnce(new Error("network"))
    const r = await runDesignAgentGeneration({ prototypeId: 1 })
    expect(r).toEqual({ ok: false, message: "network" })
  })
})

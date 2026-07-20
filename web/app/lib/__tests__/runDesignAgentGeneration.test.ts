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
    // Closed-world repair (AC12): the MAX_MS fallback now also sets the
    // `timedOut` discriminant so downstream callers can tell a client-side
    // give-up apart from a genuine backend failure.
    expect(r).toEqual({
      ok: false,
      timedOut: true,
      message: "Generation timed out (6 minutes)",
    })
  })

  it("test_run_design_agent_generation_marks_timeout_result — AC1: MAX_MS fallback sets timedOut:true", async () => {
    vi.useFakeTimers()
    vi.spyOn(designAgentApi, "get").mockResolvedValue(
      proto({ status: "generating" }),
    )
    const p = runDesignAgentGeneration({ prototypeId: 1 })
    await vi.advanceTimersByTimeAsync(6 * 60 * 1000 + 4000)
    const r = await p
    expect(r.ok).toBe(false)
    expect((r as { timedOut?: true }).timedOut).toBe(true)
  })

  it("test_run_design_agent_generation_timeout_message_byte_identical — AC1 edge: message text is exactly unchanged (other consumers already render it)", async () => {
    vi.useFakeTimers()
    vi.spyOn(designAgentApi, "get").mockResolvedValue(
      proto({ status: "generating" }),
    )
    const p = runDesignAgentGeneration({ prototypeId: 1 })
    await vi.advanceTimersByTimeAsync(6 * 60 * 1000 + 4000)
    const r = await p
    expect((r as { message: string }).message).toBe(
      "Generation timed out (6 minutes)",
    )
  })

  it("test_run_design_agent_generation_other_branches_omit_timed_out — AC2: every non-timeout branch leaves timedOut undefined", async () => {
    const cases: { status: string; error?: string | null }[] = [
      { status: "failed", error: "oops" },
      { status: "failed", error: null },
      { status: "invalidated" },
    ]
    for (const c of cases) {
      vi.spyOn(designAgentApi, "get").mockResolvedValueOnce(
        proto({ status: c.status as never, error: c.error ?? null }),
      )
      const r = await runDesignAgentGeneration({ prototypeId: 1 })
      expect((r as { timedOut?: true }).timedOut).toBeUndefined()
    }

    // ready branch
    vi.spyOn(designAgentApi, "get").mockResolvedValueOnce(
      proto({ status: "ready", bundle_url: "b" }),
    )
    const readyResult = await runDesignAgentGeneration({ prototypeId: 1 })
    expect((readyResult as { timedOut?: true }).timedOut).toBeUndefined()

    // rejected GET
    vi.spyOn(designAgentApi, "get").mockRejectedValueOnce(new Error("network"))
    const rejectedResult = await runDesignAgentGeneration({ prototypeId: 1 })
    expect((rejectedResult as { timedOut?: true }).timedOut).toBeUndefined()
  })

  it("never throws — a rejecting GET is surfaced as ok:false", async () => {
    vi.spyOn(designAgentApi, "get").mockRejectedValueOnce(new Error("network"))
    const r = await runDesignAgentGeneration({ prototypeId: 1 })
    expect(r).toEqual({ ok: false, message: "network" })
  })
})

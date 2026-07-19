// @vitest-environment jsdom
//
// Live-preview wiring in runEvidenceGeneration — mirrors
// runPrdGeneration.stream.dom.test.ts: the optional onPartial callback opens
// the SSE token stream alongside the authoritative poll, forwards the
// accumulating evidence HTML (throttled), and the stream's terminal `done`
// frame wakes the poll immediately instead of waiting out the 4s tick.
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

import { evidenceApi } from "../api"
import { runEvidenceGeneration } from "../runEvidenceGeneration"

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

describe("runEvidenceGeneration — live preview stream", () => {
  it("streams partial HTML to onPartial and wakes the poll on the done frame", async () => {
    // No existing ready evidence → the read-first path falls through to generate.
    vi.spyOn(evidenceApi, "byInsight").mockResolvedValue(null)
    vi.spyOn(evidenceApi, "generate").mockResolvedValue({ evidence_id: 21, status: "generating" } as never)
    const get = vi
      .spyOn(evidenceApi, "get")
      .mockResolvedValueOnce({ id: 21, status: "generating", payload_md: "" } as never)
      .mockResolvedValue({ id: 21, status: "ready", payload_md: "# E\n\nBody." } as never)

    const partials: string[] = []
    const resultP = runEvidenceGeneration(
      { briefId: 1, insightIndex: 0 },
      undefined,
      (html) => partials.push(html),
    )

    await vi.waitFor(() => expect(subscribeMock).toHaveBeenCalledTimes(1))
    const handlers = subscribeMock.mock.calls[0][1] as StreamHandlers

    // First delta renders immediately (leading edge of the throttle).
    handlers.onDelta("<!doctype html><h1>Evidence", "<!doctype html><h1>Evidence")
    expect(partials).toEqual(["<!doctype html><h1>Evidence"])

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

  it("builds the stream URL from the evidence id (evidenceApi.streamUrl)", async () => {
    vi.spyOn(evidenceApi, "byInsight").mockResolvedValue(null)
    vi.spyOn(evidenceApi, "generate").mockResolvedValue({ evidence_id: 33, status: "generating" } as never)
    vi.spyOn(evidenceApi, "get").mockResolvedValue({ id: 33, status: "ready", payload_md: "# E\n\nB." } as never)

    await runEvidenceGeneration({ briefId: 1, insightIndex: 0 }, undefined, () => {})

    await vi.waitFor(() => expect(subscribeMock).toHaveBeenCalledTimes(1))
    const buildUrl = subscribeMock.mock.calls[0][0] as (t: string) => string
    expect(buildUrl("tok")).toContain("/v1/evidence/33/stream")
    expect(buildUrl("tok")).toContain("token=tok")
  })

  it("does not open a stream when no onPartial is given", async () => {
    vi.spyOn(evidenceApi, "byInsight").mockResolvedValue(null)
    vi.spyOn(evidenceApi, "generate").mockResolvedValue({ evidence_id: 22, status: "generating" } as never)
    vi.spyOn(evidenceApi, "get").mockResolvedValue({ id: 22, status: "ready", payload_md: "# E\n\nB." } as never)

    const result = await runEvidenceGeneration({ briefId: 1, insightIndex: 0 })
    expect(result.ok).toBe(true)
    expect(subscribeMock).not.toHaveBeenCalled()
  })

  it("read-first short-circuit returns existing evidence without opening a stream", async () => {
    vi.spyOn(evidenceApi, "byInsight").mockResolvedValue({
      id: 9, status: "ready", payload_md: "# Existing\n\nDoc.",
    } as never)
    const generate = vi.spyOn(evidenceApi, "generate")

    const result = await runEvidenceGeneration({ briefId: 1, insightIndex: 0 }, undefined, () => {})
    expect(result.ok).toBe(true)
    expect(generate).not.toHaveBeenCalled()
    expect(subscribeMock).not.toHaveBeenCalled()
  })
})

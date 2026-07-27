// runEvidenceGeneration — the `existing` marker + evidenceId on the ok result.
//
// The artifact chat summary must fire only when evidence was BUILT, but the
// runner is read-first: already-ready evidence returns from one GET with no
// generation. The ok result now carries the row id (what the summary endpoint
// keys on) and `existing: true` on that read-first path, so completion sites
// can tell a build from a reopen. This pins that contract.
import { beforeEach, describe, expect, it, vi } from "vitest"

const { byInsight, generate, get } = vi.hoisted(() => ({
  byInsight: vi.fn(),
  generate: vi.fn(),
  get: vi.fn(),
}))
vi.mock("../api", () => ({
  evidenceApi: {
    byInsight: (...a: unknown[]) => byInsight(...a),
    generate: (...a: unknown[]) => generate(...a),
    get: (...a: unknown[]) => get(...a),
    streamUrl: vi.fn(() => "http://x/stream"),
  },
}))
vi.mock("../evidence-adapter", () => ({
  markdownToEvidenceState: (md: string) => ({ parsed: md }),
}))
vi.mock("../jobResume", () => ({
  insightScope: (b: number, i: number) => `${b}:${i}`,
  setPendingJob: vi.fn(),
  clearPendingJob: vi.fn(),
}))
vi.mock("../streamGeneration", () => ({ subscribeToGenerationStream: () => () => {} }))
vi.mock("../poll", () => ({ sleepUntilNextPoll: () => Promise.resolve() }))

import { runEvidenceGeneration, resumeEvidenceGeneration } from "../runEvidenceGeneration"

const META = { briefId: 7, insightIndex: 0 } as Parameters<typeof runEvidenceGeneration>[0]

beforeEach(() => {
  byInsight.mockReset()
  generate.mockReset()
  get.mockReset()
})

describe("runEvidenceGeneration result contract", () => {
  it("marks a read-first hit as `existing` and carries the row id", async () => {
    byInsight.mockResolvedValue({ id: 91, status: "ready", payload_md: "## Evidence" })

    const result = await runEvidenceGeneration(META)
    expect(result).toEqual({
      ok: true,
      evidence: { parsed: "## Evidence" },
      evidenceId: 91,
      existing: true,
    })
    expect(generate).not.toHaveBeenCalled() // a reopen builds nothing
  })

  it("a genuine generation completes WITHOUT the existing marker", async () => {
    byInsight.mockResolvedValue(null)
    generate.mockResolvedValue({ evidence_id: 92, status: "generating" })
    get.mockResolvedValue({ status: "ready", payload_md: "## Fresh" })

    const result = await runEvidenceGeneration(META)
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.evidenceId).toBe(92)
      expect(result.existing).toBeUndefined()
    }
  })

  it("resume carries the id and never marks existing (it resumes a real run)", async () => {
    get.mockResolvedValue({ status: "ready", payload_md: "## Resumed" })

    const result = await resumeEvidenceGeneration(93, META)
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.evidenceId).toBe(93)
      expect(result.existing).toBeUndefined()
    }
  })
})

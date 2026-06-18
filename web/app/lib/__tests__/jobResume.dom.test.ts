// @vitest-environment jsdom
//
// Tests for the in-flight job RESUME path (jobResume.ts + the resume* helpers
// in runPrdGeneration / runEvidenceGeneration). A server-side PRD/evidence job
// is fire-and-forget; a remount used to orphan it (UI never resumed though the
// server finished). We persist the active job id and, on remount, re-enter the
// poll against the EXISTING status endpoint (a GET) rather than calling
// generate again — and clear the persisted id when the job completes.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { prdApi, evidenceApi } from "../api"
import {
  getPendingJob,
  setPendingJob,
  clearPendingJob,
  insightScope,
} from "../jobResume"
import { resumePrdGeneration, runPrdGeneration } from "../runPrdGeneration"
import { resumeEvidenceGeneration } from "../runEvidenceGeneration"

const meta = { briefId: 7, insightIndex: 2 }
const scope = insightScope(meta.briefId, meta.insightIndex)

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe("jobResume store", () => {
  it("round-trips and clears a pending job id", () => {
    expect(getPendingJob("prd", "_", scope)).toBeNull()
    setPendingJob("prd", "_", scope, 42)
    expect(getPendingJob("prd", "_", scope)).toEqual({ id: "42" })
    clearPendingJob("prd", "_", scope)
    expect(getPendingJob("prd", "_", scope)).toBeNull()
  })

  it("scopes ids per kind + insight so they never collide", () => {
    setPendingJob("prd", "_", scope, 1)
    setPendingJob("evidence", "_", scope, 2)
    setPendingJob("prd", "_", insightScope(99, 0), 3)
    expect(getPendingJob("prd", "_", scope)).toEqual({ id: "1" })
    expect(getPendingJob("evidence", "_", scope)).toEqual({ id: "2" })
    expect(getPendingJob("prd", "_", insightScope(99, 0))).toEqual({ id: "3" })
  })
})

describe("resumePrdGeneration", () => {
  it("polls the existing PRD by id (GET only — never calls generate) and clears on ready", async () => {
    setPendingJob("prd", "_", scope, 42)
    const getSpy = vi
      .spyOn(prdApi, "get")
      .mockResolvedValue({ id: 42, status: "ready", payload_md: "# T\n\nBody." } as never)
    const generateSpy = vi.spyOn(prdApi, "generate")

    const result = await resumePrdGeneration(42, meta)

    expect(getSpy).toHaveBeenCalledWith(42)
    // Resume must NOT re-kick generation — it polls the existing row.
    expect(generateSpy).not.toHaveBeenCalled()
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.prd.prd_id).toBe(42)
    // Completing clears the persisted pending id so we don't resume again.
    expect(getPendingJob("prd", "_", scope)).toBeNull()
  })

  it("clears the persisted id when the job has failed", async () => {
    setPendingJob("prd", "_", scope, 9)
    vi.spyOn(prdApi, "get").mockResolvedValue({
      id: 9,
      status: "failed",
      payload_md: "",
      error: "boom",
    } as never)

    const result = await resumePrdGeneration(9, meta)
    expect(result.ok).toBe(false)
    expect(getPendingJob("prd", "_", scope)).toBeNull()
  })
})

describe("runPrdGeneration persistence", () => {
  it("persists the prd_id when generation starts and clears it on ready", async () => {
    vi.spyOn(prdApi, "generate").mockImplementation(async () => {
      // At generate time the id is persisted before the first poll runs.
      // (Asserting here proves the start-of-job persistence, before the
      // first `get` returns ready and clears it.)
      return { prd_id: 77 } as never
    })
    // Returns ready on the first poll so the (visibility-aware) sleep loop is
    // never entered — keeps the test fast under real timers.
    vi.spyOn(prdApi, "get").mockImplementation(async () => {
      // Observable mid-run: the id was persisted by setPendingJob in
      // runPrdGeneration before this first GET.
      expect(getPendingJob("prd", "_", scope)).toEqual({ id: "77" })
      return { id: 77, status: "ready", payload_md: "# T\n\nB." } as never
    })

    const result = await runPrdGeneration(meta)
    expect(result.ok).toBe(true)
    // Cleared once terminal.
    expect(getPendingJob("prd", "_", scope)).toBeNull()
  })
})

describe("resumeEvidenceGeneration", () => {
  it("polls the existing evidence by id (GET only) and clears on ready", async () => {
    setPendingJob("evidence", "_", scope, 55)
    const getSpy = vi
      .spyOn(evidenceApi, "get")
      .mockResolvedValue({ id: 55, status: "ready", payload_md: "# E\n\nBody." } as never)
    const generateSpy = vi.spyOn(evidenceApi, "generate")

    const result = await resumeEvidenceGeneration(55, meta)

    expect(getSpy).toHaveBeenCalledWith(55)
    expect(generateSpy).not.toHaveBeenCalled()
    expect(result.ok).toBe(true)
    expect(getPendingJob("evidence", "_", scope)).toBeNull()
  })
})

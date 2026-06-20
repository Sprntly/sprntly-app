// @vitest-environment jsdom
//
// Unit tests for the blur/remount-safe onboarding website-analysis flow
// (runWebsiteAnalysis.ts). POST /v1/onboarding/analyze-website is fire-and-
// forget: it returns a job_id and the analysis runs server-side; the client
// polls the status endpoint via the shared visibility-aware pollUntil and
// persists the active job_id per workspace (jobResume) so a remount re-attaches
// instead of re-POSTing. These tests mock the api layer and use fake timers to
// drive the poll without real wall-clock waits.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { onboardingApi } from "../../api"
import type { AnalyzeWebsiteResponse } from "../../api"
import {
  runWebsiteAnalysis,
  resumeWebsiteAnalysis,
  getPendingAnalysis,
  analysisScope,
} from "../runWebsiteAnalysis"
import { getPendingJob } from "../../jobResume"

beforeEach(() => {
  vi.useFakeTimers()
  setVisibility("visible")
  localStorage.clear()
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

const ANALYSIS: AnalyzeWebsiteResponse = {
  ok: true,
  reason: null,
  url: "https://acme.com",
  industry: "Fintech",
  sub_vertical: "Payments",
  business_type: "Marketplace",
  stage: "Growth",
  business_context: "Acme reconciles payments.",
  suggested_metrics: [{ metric: "Reconciled volume", description: "Weekly $." }],
  provenance: "website",
  business_context_version: 1,
}

const READY = { status: "ready" as const, result: ANALYSIS, error: null }

describe("runWebsiteAnalysis", () => {
  it("POSTs for a job_id, polls the status endpoint, and returns the analysis", async () => {
    const startSpy = vi
      .spyOn(onboardingApi, "analyzeWebsite")
      .mockResolvedValue({ job_id: 77, status: "generating" } as never)
    const getSpy = vi
      .spyOn(onboardingApi, "analyzeWebsiteStatus")
      // first poll still generating, second poll ready
      .mockResolvedValueOnce({ status: "generating", result: null, error: null } as never)
      .mockResolvedValueOnce(READY as never)

    const p = runWebsiteAnalysis("https://acme.com", "ws-1", "ws-1")
    await vi.advanceTimersByTimeAsync(2000)
    const res = await p

    expect(startSpy).toHaveBeenCalledWith("https://acme.com")
    expect(getSpy).toHaveBeenCalledWith(77)
    expect(res.result).toEqual(ANALYSIS)
    // Pending marker cleared on terminal exit.
    expect(getPendingAnalysis("ws-1", "ws-1")).toBeNull()
  })

  it("persists the active job_id so a remount can re-attach", async () => {
    vi.spyOn(onboardingApi, "analyzeWebsite").mockResolvedValue({
      job_id: 99,
      status: "generating",
    } as never)
    // Never resolves to ready within the test — we only assert the marker is set.
    vi.spyOn(onboardingApi, "analyzeWebsiteStatus").mockResolvedValue({
      status: "generating",
      result: null,
      error: null,
    } as never)

    void runWebsiteAnalysis("https://acme.com", "ws-7", "ws-7")
    await vi.advanceTimersByTimeAsync(0)

    expect(getPendingAnalysis("ws-7", "ws-7")).toEqual({ id: "99" })
    expect(getPendingJob("website-analysis", "ws-7", analysisScope("ws-7"))).toEqual({
      id: "99",
    })
  })

  it("on a backend error status resolves with result:null (forwards, no throw)", async () => {
    vi.spyOn(onboardingApi, "analyzeWebsite").mockResolvedValue({
      job_id: 5,
      status: "generating",
    } as never)
    vi.spyOn(onboardingApi, "analyzeWebsiteStatus").mockResolvedValue({
      status: "error",
      result: null,
      error: "kaboom",
    } as never)

    const p = runWebsiteAnalysis("https://acme.com", "ws-err", "ws-err")
    await vi.advanceTimersByTimeAsync(0)
    const res = await p
    expect(res.result).toBeNull()
    // Cleared even on error.
    expect(getPendingAnalysis("ws-err", "ws-err")).toBeNull()
  })

  it("on a POST transport failure resolves with result:null (never rejects)", async () => {
    vi.spyOn(onboardingApi, "analyzeWebsite").mockRejectedValue(
      new Error("network down"),
    )
    const res = await runWebsiteAnalysis("https://acme.com", "ws-net", "ws-net")
    expect(res.result).toBeNull()
  })
})

describe("resumeWebsiteAnalysis", () => {
  it("re-attaches to a persisted job_id WITHOUT re-POSTing", async () => {
    const startSpy = vi.spyOn(onboardingApi, "analyzeWebsite")
    const getSpy = vi
      .spyOn(onboardingApi, "analyzeWebsiteStatus")
      .mockResolvedValue(READY as never)

    const p = resumeWebsiteAnalysis(123, "ws-r", "ws-r")
    await vi.advanceTimersByTimeAsync(0)
    const res = await p

    expect(startSpy).not.toHaveBeenCalled()
    expect(getSpy).toHaveBeenCalledWith(123)
    expect(res.result).toEqual(ANALYSIS)
  })

  it("a remount reads the persisted id and resumes — analyzeWebsite is called exactly once", async () => {
    const startSpy = vi
      .spyOn(onboardingApi, "analyzeWebsite")
      .mockResolvedValue({ job_id: 555, status: "generating" } as never)
    vi.spyOn(onboardingApi, "analyzeWebsiteStatus").mockResolvedValue({
      status: "generating",
      result: null,
      error: null,
    } as never)
    void runWebsiteAnalysis("https://acme.com", "ws-remount", "ws-remount")
    await vi.advanceTimersByTimeAsync(0)

    const pending = getPendingAnalysis("ws-remount", "ws-remount")
    expect(pending).toEqual({ id: "555" })

    vi.spyOn(onboardingApi, "analyzeWebsiteStatus").mockResolvedValue(READY as never)
    const p = resumeWebsiteAnalysis(Number(pending!.id), "ws-remount", "ws-remount")
    await vi.advanceTimersByTimeAsync(0)
    const res = await p

    expect(res.result).toEqual(ANALYSIS)
    expect(startSpy).toHaveBeenCalledTimes(1)
    expect(getPendingAnalysis("ws-remount", "ws-remount")).toBeNull()
  })
})

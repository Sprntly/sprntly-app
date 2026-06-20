// Tests for the onboarding website-analysis API wrappers. The flow is now
// fire-and-forget (blur/remount-safe): POST /v1/onboarding/analyze-website
// returns a job_id and the analysis runs server-side; the client polls
// GET /v1/onboarding/analyze-website/{job_id} for the result. The GET's
// `result` carries the SAME AnalyzeWebsiteResponse the old synchronous POST
// body did (so the metrics page's setWebsiteAnalysis(result) is unchanged) —
// including the graceful-degrade `ok: false` shape.
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  onboardingApi,
  type AnalyzeWebsiteResponse,
  type AnalyzeWebsiteStartResponse,
  type AnalyzeWebsiteStatusResponse,
} from "../../api"
import { api } from "../../api"

afterEach(() => {
  vi.restoreAllMocks()
})

const OK: AnalyzeWebsiteResponse = {
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

describe("onboardingApi.analyzeWebsite (fire-and-forget POST)", () => {
  it("POSTs { url } to /v1/onboarding/analyze-website and returns a job_id", async () => {
    const start: AnalyzeWebsiteStartResponse = { job_id: 42, status: "generating" }
    const post = vi.spyOn(api, "post").mockResolvedValue(start)

    const res = await onboardingApi.analyzeWebsite("https://acme.com")

    expect(post).toHaveBeenCalledWith("/v1/onboarding/analyze-website", {
      url: "https://acme.com",
    })
    expect(res.job_id).toBe(42)
    expect(res.status).toBe("generating")
  })
})

describe("onboardingApi.analyzeWebsiteStatus (GET)", () => {
  it("GETs the job status and passes the ready analysis through", async () => {
    const ready: AnalyzeWebsiteStatusResponse = { status: "ready", result: OK, error: null }
    const get = vi.spyOn(api, "get").mockResolvedValue(ready)

    const res = await onboardingApi.analyzeWebsiteStatus(42)

    expect(get).toHaveBeenCalledWith("/v1/onboarding/analyze-website/42")
    expect(res.status).toBe("ready")
    expect(res.result?.industry).toBe("Fintech")
    expect(res.result?.suggested_metrics).toHaveLength(1)
  })

  it("passes through the graceful-degrade shape (result.ok:false + reason)", async () => {
    const degraded: AnalyzeWebsiteStatusResponse = {
      status: "ready",
      result: {
        ok: false,
        reason: "unreachable_or_empty",
        url: "https://nope.example",
        industry: null,
        sub_vertical: null,
        business_type: null,
        stage: null,
        business_context: "",
        suggested_metrics: [],
        provenance: "none",
        business_context_version: null,
      },
      error: null,
    }
    vi.spyOn(api, "get").mockResolvedValue(degraded)

    const res = await onboardingApi.analyzeWebsiteStatus(7)
    expect(res.result?.ok).toBe(false)
    expect(res.result?.reason).toBe("unreachable_or_empty")
    expect(res.result?.business_context).toBe("")
    expect(res.result?.suggested_metrics).toEqual([])
  })

  it("exposes the error status for a failed job", async () => {
    const errored: AnalyzeWebsiteStatusResponse = {
      status: "error",
      result: null,
      error: "kaboom",
    }
    vi.spyOn(api, "get").mockResolvedValue(errored)

    const res = await onboardingApi.analyzeWebsiteStatus(9)
    expect(res.status).toBe("error")
    expect(res.result).toBeNull()
    expect(res.error).toBe("kaboom")
  })
})

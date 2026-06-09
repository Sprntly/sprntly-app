// Tests for the onboarding website-analysis API wrapper. The endpoint always
// answers HTTP 200; `ok: false` (with a reason) is the graceful-degrade signal
// the UI uses to fall back to manual entry. We assert the wrapper POSTs to the
// right path with the `{ url }` body and passes both the happy and degraded
// shapes straight through to the caller.
import { afterEach, describe, expect, it, vi } from "vitest"

import { onboardingApi, type AnalyzeWebsiteResponse } from "../../api"
import { api } from "../../api"

afterEach(() => {
  vi.restoreAllMocks()
})

describe("onboardingApi.analyzeWebsite", () => {
  it("POSTs { url } to /v1/onboarding/analyze-website", async () => {
    const ok: AnalyzeWebsiteResponse = {
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
    const post = vi.spyOn(api, "post").mockResolvedValue(ok)

    const res = await onboardingApi.analyzeWebsite("https://acme.com")

    expect(post).toHaveBeenCalledWith("/v1/onboarding/analyze-website", {
      url: "https://acme.com",
    })
    expect(res.ok).toBe(true)
    expect(res.industry).toBe("Fintech")
    expect(res.suggested_metrics).toHaveLength(1)
  })

  it("passes through the graceful-degrade shape (ok:false + reason)", async () => {
    const degraded: AnalyzeWebsiteResponse = {
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
    }
    vi.spyOn(api, "post").mockResolvedValue(degraded)

    const res = await onboardingApi.analyzeWebsite("https://nope.example")
    expect(res.ok).toBe(false)
    expect(res.reason).toBe("unreachable_or_empty")
    expect(res.business_context).toBe("")
    expect(res.suggested_metrics).toEqual([])
  })
})

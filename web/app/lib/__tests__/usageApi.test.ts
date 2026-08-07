// usageApi wire shapes — specifically how the provider filter reaches the URL.
//
// The Admin dashboard reports on ONE provider at a time (see UsageSettings), so
// every request it makes carries `provider`. The rule that needs a test is the
// asymmetry: a provider must appear in the query string, and its ABSENCE must
// mean the param is omitted entirely rather than sent empty — `provider=` is a
// different request from no `provider` at all, and the backend reads the second
// as "every provider".
//
// Model: app/lib/__tests__/artifactTemplatesApi.test.ts.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { setActiveWorkspaceId, usageApi } from "../api"

type MockResponse = {
  ok: boolean
  status: number
  text: () => Promise<string>
}

function jsonResponse(status: number, body: unknown): MockResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  }
}

const SUMMARY = {
  range: { start: "", end: "", days: 30, tz: "UTC" },
  cost_basis: "estimated_from_tokens",
  scope: "customer_key",
  provider: "openai",
  totals: {},
  daily: [],
  by_feature: [],
  by_model: [],
  by_provider: [],
  by_operation: [],
}

describe("usageApi", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
    setActiveWorkspaceId(null)
  })

  afterEach(() => {
    setActiveWorkspaceId(null)
    vi.unstubAllGlobals()
  })

  function lastUrl(): string {
    return fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0] as string
  }

  it("scopes the summary to a provider when given one", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, SUMMARY))
    await usageApi.summary(30, "openai", "UTC")

    const url = lastUrl()
    expect(url).toContain("/v1/admin/usage/summary")
    expect(url).toContain("days=30")
    expect(url).toContain("provider=openai")
  })

  it("omits the param entirely when no provider is given", async () => {
    // Not `provider=` — an empty value is a filter for a provider named "",
    // where an absent param is what asks for every provider.
    fetchMock.mockResolvedValueOnce(jsonResponse(200, SUMMARY))
    await usageApi.summary(7)

    expect(lastUrl()).not.toContain("provider")
  })

  it("keeps the timezone independent of the provider", async () => {
    // tz moved to the third position when `provider` was inserted; this pins
    // that both still arrive, so a positional slip can't silently drop the zone
    // and shift every day on the chart.
    fetchMock.mockResolvedValueOnce(jsonResponse(200, SUMMARY))
    await usageApi.summary(90, "anthropic", "America/New_York")

    const url = lastUrl()
    expect(url).toContain("provider=anthropic")
    expect(url).toContain("tz=America%2FNew_York")
    expect(url).toContain("days=90")
  })

  it("carries the same filter into the CSV export", async () => {
    // An export taken from a scoped dashboard must contain what that dashboard
    // showed, not everything.
    fetchMock.mockResolvedValueOnce(jsonResponse(200, "day,feature\n"))
    await usageApi.exportCsv(30, "openai", "UTC")

    const url = lastUrl()
    expect(url).toContain("/v1/admin/usage/export.csv")
    expect(url).toContain("provider=openai")
  })
})

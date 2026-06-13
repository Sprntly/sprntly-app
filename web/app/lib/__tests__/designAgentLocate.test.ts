/**
 * Tests for designAgentApi.locate + the LocateCandidate / LocateResponse types.
 *
 * Stubs global fetch to verify the method issues the correct POST and returns
 * the typed response without any actual network call.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { vi } from "vitest"
import { API_URL, designAgentApi } from "../api"
import type { LocateCandidate, LocateResponse } from "../api"

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

function makeLocateCandidate(overrides: Partial<LocateCandidate> = {}): LocateCandidate {
  return {
    route: "/home",
    entry_component: "HomeScreen",
    confidence: 90,
    rationale: "Main screen",
    ambiguous: false,
    component_count: 3,
    ...overrides,
  }
}

function makeLocateResponse(overrides: Partial<LocateResponse> = {}): LocateResponse {
  const candidate = makeLocateCandidate()
  return {
    decision: "auto_proceed",
    chosen: [candidate],
    ranked: [candidate],
    top_confidence: 90,
    threshold: 80,
    repo: "org/repo",
    posture: "CLEAN",
    unmapped: false,
    commit_sha: "",
    ...overrides,
  }
}

describe("designAgentApi.locate", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("POSTs to /v1/design-agent/locate with the request body", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, makeLocateResponse()))
    await designAgentApi.locate({ prd_id: 42, github_repo: "org/repo" })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(`${API_URL}/v1/design-agent/locate`)
    expect(init.method).toBe("POST")
    expect(init.credentials).toBe("include")
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json")
    expect(JSON.parse(init.body as string)).toEqual({ prd_id: 42, github_repo: "org/repo" })
  })

  it("forwards the optional ref field when supplied", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, makeLocateResponse()))
    await designAgentApi.locate({ prd_id: 1, github_repo: "org/repo", ref: "main" })

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({
      prd_id: 1,
      github_repo: "org/repo",
      ref: "main",
    })
  })

  it("returns a typed LocateResponse with all required fields", async () => {
    const expected = makeLocateResponse()
    fetchMock.mockResolvedValueOnce(jsonResponse(200, expected))

    const result: LocateResponse = await designAgentApi.locate({
      prd_id: 42,
      github_repo: "org/repo",
    })

    expect(result.decision).toBe("auto_proceed")
    expect(result.chosen).toHaveLength(1)
    expect(result.ranked).toHaveLength(1)
    expect(result.top_confidence).toBe(90)
    expect(result.threshold).toBe(80)
    expect(result.repo).toBe("org/repo")
    expect(result.posture).toBe("CLEAN")
    expect(result.unmapped).toBe(false)
  })

  it("returns component_count on each candidate", async () => {
    const candidate = makeLocateCandidate({ component_count: 5 })
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, makeLocateResponse({ chosen: [candidate], ranked: [candidate] })),
    )

    const result = await designAgentApi.locate({ prd_id: 1, github_repo: "org/repo" })

    expect(result.chosen[0].component_count).toBe(5)
    expect(result.ranked[0].component_count).toBe(5)
  })

  it("returns unmapped=true and empty chosen/ranked when map is unavailable", async () => {
    const unmapped = makeLocateResponse({
      decision: "ranked_confirm",
      chosen: [],
      ranked: [],
      top_confidence: 0,
      posture: "PARTIAL",
      unmapped: true,
    })
    fetchMock.mockResolvedValueOnce(jsonResponse(200, unmapped))

    const result = await designAgentApi.locate({ prd_id: 1, github_repo: "org/repo" })

    expect(result.unmapped).toBe(true)
    expect(result.chosen).toEqual([])
    expect(result.ranked).toEqual([])
    expect(result.decision).toBe("ranked_confirm")
  })

  it("propagates a non-2xx response as an error", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(502, { detail: "locate failed" }))

    await expect(
      designAgentApi.locate({ prd_id: 1, github_repo: "org/repo" }),
    ).rejects.toMatchObject({ status: 502 })
  })
})

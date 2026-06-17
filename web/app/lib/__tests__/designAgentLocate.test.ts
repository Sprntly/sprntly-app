/**
 * Tests for the ASYNC locate contract: designAgentApi.locate (POST →
 * 202 { job_id }) + designAgentApi.locateJob (GET the poll endpoint), plus the
 * LocateCandidate / LocateResponse types reused as the job result shape.
 *
 * Stubs global fetch to verify each method issues the correct request and
 * returns the typed response without any actual network call.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { vi } from "vitest"
import { API_URL, designAgentApi } from "../api"
import type {
  LocateCandidate,
  LocateResponse,
  LocateJobHandle,
  LocateJobStatus,
} from "../api"

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
    id: "/home",
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

describe("designAgentApi.locate (async POST → job handle)", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("POSTs to /v1/design-agent/locate with the request body", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(202, { job_id: "job-1", status: "running" }),
    )
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
    fetchMock.mockResolvedValueOnce(
      jsonResponse(202, { job_id: "job-1", status: "running" }),
    )
    await designAgentApi.locate({ prd_id: 1, github_repo: "org/repo", ref: "main" })

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({
      prd_id: 1,
      github_repo: "org/repo",
      ref: "main",
    })
  })

  it("returns the typed job handle ({ job_id, status: 'running' }) on 202", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(202, { job_id: "abc-123", status: "running" }),
    )

    const handle: LocateJobHandle = await designAgentApi.locate({
      prd_id: 42,
      github_repo: "org/repo",
    })

    expect(handle.job_id).toBe("abc-123")
    expect(handle.status).toBe("running")
  })

  it("propagates an inline 404 on the POST (feature off / PRD not owned / cross-workspace)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(404, { detail: "not found" }))

    await expect(
      designAgentApi.locate({ prd_id: 1, github_repo: "org/repo" }),
    ).rejects.toMatchObject({ status: 404 })
  })

  it("propagates a non-2xx 5xx as an error", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(502, { detail: "locate failed" }))

    await expect(
      designAgentApi.locate({ prd_id: 1, github_repo: "org/repo" }),
    ).rejects.toMatchObject({ status: 502 })
  })
})

describe("designAgentApi.locateJob (poll the job)", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("GETs /v1/design-agent/locate/jobs/{job_id}", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { status: "running" }))
    await designAgentApi.locateJob("abc-123")

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(`${API_URL}/v1/design-agent/locate/jobs/abc-123`)
    expect(init.method).toBe("GET")
    expect(init.credentials).toBe("include")
  })

  it("URL-encodes the job id", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { status: "running" }))
    await designAgentApi.locateJob("a/b c")

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(`${API_URL}/v1/design-agent/locate/jobs/a%2Fb%20c`)
  })

  it("returns status 'running' while the job is in flight", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { status: "running" }))

    const status: LocateJobStatus = await designAgentApi.locateJob("j1")
    expect(status.status).toBe("running")
    expect(status.result).toBeUndefined()
  })

  it("returns status 'done' carrying the full LocateResponse result", async () => {
    const result = makeLocateResponse()
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { status: "done", result }))

    const status = await designAgentApi.locateJob("j1")
    expect(status.status).toBe("done")
    expect(status.result?.decision).toBe("auto_proceed")
    expect(status.result?.chosen).toHaveLength(1)
    expect(status.result?.ranked[0].component_count).toBe(3)
    expect(status.result?.repo).toBe("org/repo")
  })

  it("carries unmapped=true through the done result when the map is unavailable", async () => {
    const result = makeLocateResponse({
      decision: "ranked_confirm",
      chosen: [],
      ranked: [],
      top_confidence: 0,
      posture: "PARTIAL",
      unmapped: true,
    })
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { status: "done", result }))

    const status = await designAgentApi.locateJob("j1")
    expect(status.result?.unmapped).toBe(true)
    expect(status.result?.chosen).toEqual([])
  })

  it("returns status 'error' with the failure reason", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { status: "error", error: "mapper crashed" }),
    )

    const status = await designAgentApi.locateJob("j1")
    expect(status.status).toBe("error")
    expect(status.error).toBe("mapper crashed")
  })

  it("propagates a 404 (unknown / TTL-swept / cross-workspace job) as a terminal error", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(404, { detail: "unknown job" }))

    await expect(designAgentApi.locateJob("gone")).rejects.toMatchObject({
      status: 404,
    })
  })

  it("propagates a transient 5xx as an error the caller can retry", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(503, { detail: "upstream down" }))

    await expect(designAgentApi.locateJob("j1")).rejects.toMatchObject({
      status: 503,
    })
  })
})

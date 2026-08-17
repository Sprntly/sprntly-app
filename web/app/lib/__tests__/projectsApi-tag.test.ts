// The two tag-action `projectsApi` methods — pins the exact method + URL +
// body each one sends. Model: app/lib/__tests__/projectsApi-ledger.test.ts.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { projectsApi, setActiveWorkspaceId } from "../api"

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

describe("projectsApi tag-action methods", () => {
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

  function lastCall(): [string, RequestInit] {
    const call = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]
    return [call[0] as string, call[1] as RequestInit]
  }

  it("tagCandidate POSTs the needle body to the tag route", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { tier: "t_workspace", added: {} }))
    await projectsApi.tagCandidate(3, "Fortune")

    const [url, init] = lastCall()
    expect(url).toContain("/v1/projects/3/tag")
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body as string)).toEqual({ needle: "Fortune" })
  })

  it("candidateSearch GETs the q-scoped candidates route and unwraps candidates", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        candidates: [{ kind: "member", user_id: "u1", name: "A", email: "a@x.co" }],
      }),
    )
    const out = await projectsApi.candidateSearch(3, "for")

    const [url, init] = lastCall()
    expect(url).toContain("/v1/projects/3/candidates?q=for")
    expect(init.method).toBe("GET")
    expect(out).toEqual([{ kind: "member", user_id: "u1", name: "A", email: "a@x.co" }])
  })

  it("candidateSearch url-encodes the query", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { candidates: [] }))
    await projectsApi.candidateSearch(3, "a b@x.co")

    const [url] = lastCall()
    expect(url).toContain("q=a%20b%40x.co")
  })
})

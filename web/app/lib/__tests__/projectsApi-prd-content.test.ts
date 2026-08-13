// `projectsApi.savePrdContent` — pins the exact method + URL + body it sends.
// Model: app/lib/__tests__/projectsApi-tag.test.ts.
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

describe("projectsApi.savePrdContent", () => {
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

  it("POSTs the documented endpoint + body via the existing api.post helper", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { id: 42, title: "Doc", payload_md: "<html></html>" }),
    )
    await projectsApi.savePrdContent(3, 42, "Doc", "<!DOCTYPE html><html></html>")

    const [url, init] = lastCall()
    expect(url).toContain("/v1/projects/3/prd/content")
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body as string)).toEqual({
      prd_id: 42,
      title: "Doc",
      html: "<!DOCTYPE html><html></html>",
    })
  })

  it("URL-encodes the id path segment", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { id: 1, title: "T", payload_md: "" }),
    )
    await projectsApi.savePrdContent("proj a/b", 1, "T", "<html></html>")

    const [url] = lastCall()
    expect(url).toContain("/v1/projects/proj%20a%2Fb/prd/content")
  })
})

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { API_URL, designAgentApi } from "../api"

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

describe("designAgentApi", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  describe("generate", () => {
    it("POSTs /v1/design-agent/generate with the form body + credentials + JSON", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(202, { prototype_id: 7, status: "generating" }),
      )
      const r = await designAgentApi.generate({
        prd_id: 3,
        target_platform: "mobile",
        instructions: "dark theme",
        figma_file_key: "abc",
      })
      expect(r.prototype_id).toBe(7)
      expect(fetchMock).toHaveBeenCalledTimes(1)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/generate`)
      expect(init.method).toBe("POST")
      expect(init.credentials).toBe("include")
      expect(
        (init.headers as Record<string, string>)["Content-Type"],
      ).toBe("application/json")
      const body = JSON.parse(init.body as string)
      expect(body).toEqual({
        prd_id: 3,
        target_platform: "mobile",
        instructions: "dark theme",
        figma_file_key: "abc",
      })
    })

    it("propagates a non-2xx response as ApiError", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(500, { detail: "boom" }),
      )
      await expect(
        designAgentApi.generate({
          prd_id: 1,
          target_platform: "both",
          instructions: "",
        }),
      ).rejects.toMatchObject({ status: 500 })
    })
  })

  describe("get", () => {
    it("GETs /v1/design-agent/{id} with credentials included", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          id: 5,
          status: "ready",
          bundle_url: "https://x/b",
          error: null,
        }),
      )
      const r = await designAgentApi.get(5)
      expect(r.id).toBe(5)
      expect(r.status).toBe("ready")
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/5`)
      expect(init.method).toBe("GET")
      expect(init.credentials).toBe("include")
    })
  })
})

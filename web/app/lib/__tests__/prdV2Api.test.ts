import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { API_URL, prdV2Api } from "../api"

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

describe("prdV2Api", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  describe("generate", () => {
    it("POSTs /v1/prd/v2/generate with brief_id + insight_index (force defaults false)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(202, {
          prd_id: 42,
          status: "generating",
          title: "Sample",
          variant: "v2",
        }),
      )
      const r = await prdV2Api.generate(7, 2)
      expect(r.prd_id).toBe(42)
      expect(r.variant).toBe("v2")
      expect(fetchMock).toHaveBeenCalledTimes(1)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/prd/v2/generate`)
      expect(init.method).toBe("POST")
      expect(init.credentials).toBe("include")
      const body = JSON.parse(init.body as string)
      expect(body).toEqual({ brief_id: 7, insight_index: 2, force: false })
    })

    it("passes force=true through to the request body", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(202, {
          prd_id: 43,
          status: "generating",
          title: "x",
          variant: "v2",
        }),
      )
      await prdV2Api.generate(1, 0, true)
      const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      const body = JSON.parse(init.body as string)
      expect(body.force).toBe(true)
    })

    it("propagates API errors as ApiError", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(404, { detail: "brief not found" }),
      )
      await expect(prdV2Api.generate(999, 0)).rejects.toMatchObject({
        status: 404,
      })
    })
  })

  describe("get", () => {
    it("GETs /v1/prd/v2/{id} with credentials included", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          id: 5,
          brief_id: 1,
          insight_index: 0,
          generated_at: "2026-05-19T00:00:00Z",
          title: "T",
          payload_md: "# T",
          status: "ready",
          variant: "v2",
        }),
      )
      const r = await prdV2Api.get(5)
      expect(r.id).toBe(5)
      expect(r.variant).toBe("v2")
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/prd/v2/5`)
      expect(init.method).toBe("GET")
      expect(init.credentials).toBe("include")
    })

    it("propagates 409 when the id resolves to a v1 row (per contract)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(409, { detail: "id resolves to v1 row" }),
      )
      await expect(prdV2Api.get(99)).rejects.toMatchObject({ status: 409 })
    })
  })
})

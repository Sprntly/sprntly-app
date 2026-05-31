import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { api, API_URL, designAgentApi, setAccessTokenProvider } from "../api"

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

/** Raw text/markdown response for the export endpoint (NOT JSON-encoded). */
function textResponse(status: number, body: string): MockResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => body,
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

  describe("complete", () => {
    it("POSTs /v1/design-agent/{id}/complete with an empty body (AC13)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          prototype_id: 5,
          is_complete: true,
          complete_checkpoint_id: 9,
        }),
      )
      await designAgentApi.complete(5)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/5/complete`)
      expect(init.method).toBe("POST")
      expect(init.credentials).toBe("include")
      expect(JSON.parse(init.body as string)).toEqual({})
    })

    it("parses the response (AC13)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          prototype_id: 5,
          is_complete: true,
          complete_checkpoint_id: 9,
        }),
      )
      const r = await designAgentApi.complete(5)
      expect(r.is_complete).toBe(true)
      expect(r.complete_checkpoint_id).toBe(9)
    })
  })

  describe("resume", () => {
    it("POSTs /v1/design-agent/{id}/resume (AC14)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          prototype_id: 5,
          is_complete: false,
          handoffs_flagged_stale: 2,
        }),
      )
      await designAgentApi.resume(5)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/5/resume`)
      expect(init.method).toBe("POST")
    })

    it("parses the response (AC14)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          prototype_id: 5,
          is_complete: false,
          handoffs_flagged_stale: 2,
        }),
      )
      const r = await designAgentApi.resume(5)
      expect(r.is_complete).toBe(false)
      expect(r.handoffs_flagged_stale).toBe(2)
    })
  })

  describe("share", () => {
    it("POSTs /v1/design-agent/{id}/share, passing the body through (AC15)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          prototype_id: 5,
          share_mode: "passcode",
          share_token: "tok-xyz",
        }),
      )
      await designAgentApi.share(5, { mode: "passcode", passcode: "hunter2" })
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/5/share`)
      expect(init.method).toBe("POST")
      expect(JSON.parse(init.body as string)).toEqual({
        mode: "passcode",
        passcode: "hunter2",
      })
    })

    it("parses the response (AC15)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          prototype_id: 5,
          share_mode: "public",
          share_token: "tok-abc",
        }),
      )
      const r = await designAgentApi.share(5, { mode: "public" })
      expect(r.share_mode).toBe("public")
      expect(r.share_token).toBe("tok-abc")
    })
  })

  describe("exportMarkdown", () => {
    it("GETs /v1/design-agent/{id}/export (AC16)", async () => {
      fetchMock.mockResolvedValueOnce(textResponse(200, "# Design brief\n"))
      await designAgentApi.exportMarkdown(5)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/5/export`)
      expect(init.method).toBe("GET")
      expect(init.credentials).toBe("include")
    })

    it("returns the response text as a string (AC16)", async () => {
      fetchMock.mockResolvedValueOnce(textResponse(200, "# Design brief\n## Section"))
      const md = await designAgentApi.exportMarkdown(5)
      expect(md).toBe("# Design brief\n## Section")
    })

    it("sets the Accept header to text/markdown (AC16)", async () => {
      fetchMock.mockResolvedValueOnce(textResponse(200, "# md"))
      await designAgentApi.exportMarkdown(5)
      const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect((init.headers as Record<string, string>)["Accept"]).toBe("text/markdown")
    })

    it("throws an ApiError on a non-ok response (AC16)", async () => {
      fetchMock.mockResolvedValueOnce(textResponse(409, "prototype is still WIP"))
      await expect(designAgentApi.exportMarkdown(5)).rejects.toMatchObject({
        status: 409,
      })
    })

    it("includes the Bearer token when an access-token provider is set (AC17)", async () => {
      setAccessTokenProvider(async () => "jwt-123")
      try {
        fetchMock.mockResolvedValueOnce(textResponse(200, "# md"))
        await designAgentApi.exportMarkdown(5)
        const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
        expect((init.headers as Record<string, string>)["Authorization"]).toBe(
          "Bearer jwt-123",
        )
        expect(init.credentials).toBe("include")
      } finally {
        // Reset so the provider does not leak into other tests in this file.
        setAccessTokenProvider(async () => null)
      }
    })
  })

  // ── F8 anchored comments (P3-03) ──────────────────────────────────────────
  describe("comments", () => {
    it("createCommentByToken POSTs to the public by-token route (AC8)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          id: 11,
          anchor_id: "fb3007b5",
          body: "make it bigger",
          author: "external",
          status: "open",
          created_at: "2026-05-30T12:00:00Z",
          resolved_at: null,
        }),
      )
      const r = await designAgentApi.createCommentByToken("tok-xyz", {
        anchor_id: "fb3007b5",
        body: "make it bigger",
      })
      expect(r.id).toBe(11)
      expect(r.status).toBe("open")
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/by-token/tok-xyz/comments`)
      expect(init.method).toBe("POST")
      expect(init.credentials).toBe("include")
      expect(JSON.parse(init.body as string)).toEqual({
        anchor_id: "fb3007b5",
        body: "make it bigger",
      })
    })

    it("listCommentsByToken GETs the public by-token route (AC8)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, [
          {
            id: 11,
            anchor_id: "fb3007b5",
            body: "hi",
            author: "external",
            status: "open",
            created_at: "2026-05-30T12:00:00Z",
            resolved_at: null,
          },
        ]),
      )
      const r = await designAgentApi.listCommentsByToken("tok-xyz")
      expect(r).toHaveLength(1)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/by-token/tok-xyz/comments`)
      expect(init.method).toBe("GET")
      expect(init.credentials).toBe("include")
    })

    it("resolveComment PATCHes the internal resolve route (AC8)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          id: 7,
          anchor_id: "fb3007b5",
          body: "hi",
          author: "demo",
          status: "resolved",
          created_at: "2026-05-30T12:00:00Z",
          resolved_at: "2026-05-30T13:00:00Z",
        }),
      )
      const r = await designAgentApi.resolveComment(5, 7)
      expect(r.status).toBe("resolved")
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/5/comments/7/resolve`)
      expect(init.method).toBe("PATCH")
      expect(init.credentials).toBe("include")
    })

    it("api.patch issues a PATCH request via the shared helper (AC9)", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }))
      await api.patch("/v1/some/patch/route", { a: 1 })
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/some/patch/route`)
      expect(init.method).toBe("PATCH")
      expect(init.credentials).toBe("include")
      expect(JSON.parse(init.body as string)).toEqual({ a: 1 })
    })
  })

  // ── F11 PRD patches (P3-10) ────────────────────────────────────────────────
  describe("prd patches", () => {
    function patchRow(over: Record<string, unknown> = {}) {
      return {
        id: 1,
        prd_id: 7,
        prototype_id: 3,
        rationale: "tighten the metric",
        patch_md: "## Metric\n7-day activation",
        status: "pending",
        created_at: "2026-05-30T12:00:00Z",
        ...over,
      }
    }

    it("listPendingPatches GETs the prd-patches route with the prd_id query (AC5)", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(200, [patchRow()]))
      const r = await designAgentApi.listPendingPatches(7)
      expect(r).toHaveLength(1)
      expect(r[0].status).toBe("pending")
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/prd-patches?prd_id=7`)
      expect(init.method).toBe("GET")
      expect(init.credentials).toBe("include")
    })

    it("acceptPatch POSTs the accept route with an empty body (AC5)", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(200, patchRow({ status: "applied" })))
      const r = await designAgentApi.acceptPatch(1)
      expect(r.status).toBe("applied")
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/prd-patches/1/accept`)
      expect(init.method).toBe("POST")
      expect(init.credentials).toBe("include")
      expect(JSON.parse(init.body as string)).toEqual({})
    })

    it("rejectPatch POSTs the reject route with an empty body (AC5)", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(200, patchRow({ status: "rejected" })))
      const r = await designAgentApi.rejectPatch(1)
      expect(r.status).toBe("rejected")
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/prd-patches/1/reject`)
      expect(init.method).toBe("POST")
      expect(init.credentials).toBe("include")
      expect(JSON.parse(init.body as string)).toEqual({})
    })
  })
})

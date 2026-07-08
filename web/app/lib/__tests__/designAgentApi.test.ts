import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  api,
  API_URL,
  designAgentApi,
  setAccessTokenProvider,
  VIEW_GRANT_FETCH_TIMEOUT_MS,
  type ManualEditTriple,
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

  // ── AD14 pre-flight cost estimate (P3-11) ──────────────────────────────────
  describe("estimateIterate", () => {
    it("POSTs to the iterate/estimate route with the prompt body (AC7)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          cached_input_tokens: 100,
          new_input_tokens: 5,
          expected_output_tokens: 2000,
          est_cost_usd: 0.03,
          soft_cap_usd: 0.5,
          exceeds_soft_cap: false,
          model: "claude-sonnet-4-6",
        }),
      )
      const out = await designAgentApi.estimateIterate(7, { prompt: "make it blue" })
      expect(out.est_cost_usd).toBe(0.03)
      expect(out.model).toBe("claude-sonnet-4-6")
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/7/iterate/estimate`)
      expect(init.method).toBe("POST")
      expect(init.credentials).toBe("include")
      expect(JSON.parse(init.body as string)).toEqual({ prompt: "make it blue" })
    })
  })

  // ── F9/F10 iterate (P3-14) ─────────────────────────────────────────────────
  describe("iterate", () => {
    it("POSTs to the iterate route with the body + default mode:'execute' (P3-14 AC4)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(202, { prototype_id: 7, status: "generating", queue_position: 0 }),
      )
      const out = await designAgentApi.iterate(7, {
        prompt: "make it blue",
        applied_comment_id: 5,
      })
      expect(out.prototype_id).toBe(7)
      expect(out.status).toBe("generating")
      expect(out.queue_position).toBe(0)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/7/iterate`)
      expect(init.method).toBe("POST")
      expect(init.credentials).toBe("include")
      expect(JSON.parse(init.body as string)).toEqual({
        prompt: "make it blue",
        applied_comment_id: 5,
        mode: "execute",
      })
    })

    it("defaults mode to 'execute' and works without applied_comment_id", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(202, { prototype_id: 9, status: "generating", queue_position: 2 }),
      )
      const out = await designAgentApi.iterate(9, { prompt: "tweak" })
      expect(out.queue_position).toBe(2)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/9/iterate`)
      expect(JSON.parse(init.body as string)).toEqual({ prompt: "tweak", mode: "execute" })
    })

    it("honours an explicit mode:'plan' (P3-07 path)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(202, { prototype_id: 3, status: "generating", queue_position: 0 }),
      )
      await designAgentApi.iterate(3, { prompt: "plan it", mode: "plan" })
      const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(JSON.parse(init.body as string)).toEqual({ prompt: "plan it", mode: "plan" })
    })
  })

  // ── F13 manual edit (P4-01) ────────────────────────────────────────────────
  describe("manualEdit", () => {
    it("test_manual_edit_posts_to_manual_edit_route — POSTs {edits} (AC6/AC11)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(202, { prototype_id: 7, status: "generating", queue_position: 0 }),
      )
      const edits: ManualEditTriple[] = [
        {
          anchor_id: "fb3007b5",
          property: "color",
          old_value: "rgb(0, 0, 0)",
          new_value: "rgb(255, 0, 0)",
        },
      ]
      const out = await designAgentApi.manualEdit(7, { edits })
      expect(out.prototype_id).toBe(7)
      expect(out.status).toBe("generating")
      expect(out.queue_position).toBe(0)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/design-agent/7/manual-edit`)
      expect(init.method).toBe("POST")
      expect(init.credentials).toBe("include")
      expect(JSON.parse(init.body as string)).toEqual({ edits })
    })

    it("propagates a stale-anchor error as ApiError (overlay maps it, AC8)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(400, { detail: "anchor fb3007b5 no longer exists in the current bundle" }),
      )
      await expect(
        designAgentApi.manualEdit(7, {
          edits: [{ anchor_id: "fb3007b5", property: "text", old_value: "a", new_value: "b" }],
        }),
      ).rejects.toMatchObject({ status: 400 })
    })
  })

  // ── view-grant fetch timeout (stalled-request bug fix) ──────────────────
  describe("viewGrant", () => {
    const viewGrantUrl = "https://app.sprntly.ai/_da-bundle/x/view-grant"

    beforeEach(() => {
      vi.useFakeTimers()
    })

    afterEach(() => {
      vi.useRealTimers()
    })

    it("test_view_grant_stalled_fetch_rejects_within_timeout_bound — a fetch that never resolves rejects within VIEW_GRANT_FETCH_TIMEOUT_MS instead of hanging forever", async () => {
      // Simulate a real fetch()'s AbortController contract: the returned
      // promise never settles on its own, but rejects once the passed
      // signal aborts (exactly what happens when viewGrant's own timeout
      // fires). A fetch mock that ignores the signal (as the unfixed
      // pre-ticket code effectively does, since it never passes one) would
      // leave this promise pending forever and this test would time out —
      // that's the regression this test proves is fixed.
      fetchMock.mockImplementationOnce((_url: string, init?: RequestInit) => {
        return new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("The operation was aborted.", "AbortError"))
          })
        })
      })

      let settled = false
      const promise = designAgentApi.viewGrant(viewGrantUrl)
      // Single .then(onFulfilled, onRejected) attached directly to `promise` —
      // marks it handled (avoids an unhandled-rejection warning) and tracks
      // settlement on either outcome, without spinning off a second derived
      // promise (e.g. via .finally()) that would itself go unhandled.
      promise.then(
        () => {
          settled = true
        },
        () => {
          settled = true
        },
      )

      // Not yet at the bound: still pending.
      await vi.advanceTimersByTimeAsync(VIEW_GRANT_FETCH_TIMEOUT_MS - 1)
      expect(settled).toBe(false)

      // Crossing the bound fires the AbortController and the fetch rejects.
      await vi.advanceTimersByTimeAsync(1)
      await expect(promise).rejects.toBeInstanceOf(DOMException)
      expect(settled).toBe(true)
    })

    it("test_view_grant_still_throws_apierror_on_non_ok_response — a resolved non-ok response still throws ApiError with the original status", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "unauthorized" }))
      await expect(designAgentApi.viewGrant(viewGrantUrl)).rejects.toMatchObject({
        status: 401,
      })
    })

    it("test_view_grant_clears_timeout_on_success — a promptly-resolving 204 leaves no dangling timer", async () => {
      const baseline = vi.getTimerCount()
      fetchMock.mockResolvedValueOnce(jsonResponse(204, null))
      await designAgentApi.viewGrant(viewGrantUrl)
      expect(vi.getTimerCount()).toBe(baseline)
    })

    it("test_view_grant_clears_timeout_on_thrown_apierror — the finally block clears the timeout even when a non-ok response throws", async () => {
      const baseline = vi.getTimerCount()
      fetchMock.mockResolvedValueOnce(jsonResponse(404, { detail: "not found" }))
      await expect(designAgentApi.viewGrant(viewGrantUrl)).rejects.toMatchObject({
        status: 404,
      })
      expect(vi.getTimerCount()).toBe(baseline)
    })
  })
})

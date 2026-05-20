import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { API_URL, sourcesApi } from "../api"

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

describe("sourcesApi", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    // jsdom / node both leave `fetch` writable; stub the global directly.
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  describe("list", () => {
    it("GETs /v1/datasets/{slug}/files with credentials included", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, { slug: "asurion", files: [] }),
      )
      const r = await sourcesApi.list("asurion")
      expect(r).toEqual({ slug: "asurion", files: [] })
      expect(fetchMock).toHaveBeenCalledTimes(1)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/datasets/asurion/files`)
      expect(init.method).toBe("GET")
      expect(init.credentials).toBe("include")
    })

    it("URL-encodes the slug", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(200, { slug: "a b/c", files: [] }))
      await sourcesApi.list("a b/c")
      const [url] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/datasets/a%20b%2Fc/files`)
    })

    it("propagates API errors as ApiError", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(404, { detail: "nope" }))
      await expect(sourcesApi.list("missing")).rejects.toMatchObject({
        status: 404,
      })
    })
  })

  describe("remove", () => {
    it("DELETEs /v1/datasets/{slug}/files/{filename}", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          slug: "asurion",
          filename: "notes.txt",
          removed: { raw: true, md: true },
        }),
      )
      const r = await sourcesApi.remove("asurion", "notes.txt")
      expect(r.removed.raw).toBe(true)
      expect(r.removed.md).toBe(true)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/datasets/asurion/files/notes.txt`)
      expect(init.method).toBe("DELETE")
      expect(init.credentials).toBe("include")
    })

    it("URL-encodes the filename (spaces, plus, parens)", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          slug: "asurion",
          filename: "Q1 notes (v2).docx",
          removed: { raw: true, md: false },
        }),
      )
      await sourcesApi.remove("asurion", "Q1 notes (v2).docx")
      const [url] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(
        `${API_URL}/v1/datasets/asurion/files/Q1%20notes%20(v2).docx`,
      )
    })

    it("URL-encodes both slug and filename independently", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          slug: "acme/co",
          filename: "a&b.pdf",
          removed: { raw: true, md: true },
        }),
      )
      await sourcesApi.remove("acme/co", "a&b.pdf")
      const [url] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/datasets/acme%2Fco/files/a%26b.pdf`)
    })

    it("propagates 422 invalid-filename errors as ApiError", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(422, { detail: "invalid filename" }),
      )
      await expect(sourcesApi.remove("asurion", "../bad")).rejects.toMatchObject({
        status: 422,
      })
    })
  })
})

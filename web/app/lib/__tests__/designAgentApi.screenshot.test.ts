/**
 * Unit tests for `designAgentApi.uploadScreenshot` — the multipart client for
 * POST /v1/design-agent/uploads/screenshot. Mirrors designAgentApi.test.ts's
 * fetch-mock house pattern (vi.stubGlobal("fetch", …), node env — Node's own
 * FormData/Blob/File globals).
 */
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

describe("designAgentApi.uploadScreenshot", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("test_upload_screenshot_posts_formdata_with_credentials — POSTs multipart FormData to the uploads route with credentials and NO manual JSON content-type", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        screenshot_key: "da-upload/ws1/k1.png",
        media_type: "image/png",
      }),
    )
    const blob = new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], {
      type: "image/png",
    })
    const r = await designAgentApi.uploadScreenshot(blob)
    expect(r.screenshot_key).toBe("da-upload/ws1/k1.png")
    expect(r.media_type).toBe("image/png")

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(`${API_URL}/v1/design-agent/uploads/screenshot`)
    expect(init.method).toBe("POST")
    expect(init.credentials).toBe("include")
    expect(init.body).toBeInstanceOf(FormData)
    // The file part rides the backend's expected "file" field.
    const part = (init.body as FormData).get("file")
    expect(part).toBeTruthy()
    // The multipart boundary must be set by the runtime, NOT a manual
    // Content-Type header (the shared helper's isForm branch).
    expect(
      (init.headers as Record<string, string>)["Content-Type"],
    ).toBeUndefined()
    expect((init.headers as Record<string, string>)["Accept"]).toBe(
      "application/json",
    )
  })

  it("keeps a File's own name on the multipart file part", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        screenshot_key: "da-upload/ws1/k2.png",
        media_type: "image/png",
      }),
    )
    const file = new File([new Uint8Array([1, 2, 3])], "my-shot.png", {
      type: "image/png",
    })
    await designAgentApi.uploadScreenshot(file)
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const part = (init.body as FormData).get("file")
    expect(part).toBeInstanceOf(File)
    expect((part as File).name).toBe("my-shot.png")
  })

  it("propagates a 413 as ApiError with the server's user-readable message verbatim", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(413, { detail: "File too large (max 8 MB)." }),
    )
    await expect(
      designAgentApi.uploadScreenshot(new Blob(["x"], { type: "image/png" })),
    ).rejects.toMatchObject({
      status: 413,
      message: "File too large (max 8 MB).",
    })
  })

  it("propagates a 422 as ApiError with the server's user-readable message verbatim", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(422, {
        detail: "Unsupported file type (PNG, JPEG, or WebP required).",
      }),
    )
    await expect(
      designAgentApi.uploadScreenshot(new Blob(["x"], { type: "text/plain" })),
    ).rejects.toMatchObject({
      status: 422,
      message: "Unsupported file type (PNG, JPEG, or WebP required).",
    })
  })
})

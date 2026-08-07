// artifactTemplatesApi wire shapes — paths, methods, JSON vs multipart bodies,
// and the one header rule this client must NOT break.
//
// `X-Workspace-Id` is injected centrally in `request()`. A per-call header here
// would be a second place to keep in sync and would silently diverge the day
// someone adds a route — so the last describe asserts it comes from the
// injector and from nowhere else.
//
// Model: app/lib/__tests__/prdApi.test.ts.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  API_URL,
  ApiError,
  artifactTemplatesApi,
  setActiveWorkspaceId,
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

const ROW = {
  id: "t1",
  name: "Acme PRD v3",
  artifact_type: "prd",
  uploader_name: "Dana Okoye",
  created_at: "2026-08-03T10:00:00Z",
  updated_at: "2026-08-03T10:00:00Z",
  compile_status: "ready",
  is_active: false,
  source_chars: 4210,
  compile_summary: null,
  compile_note_count: 0,
}

describe("artifactTemplatesApi", () => {
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
    return fetchMock.mock.calls[fetchMock.mock.calls.length - 1] as [
      string,
      RequestInit,
    ]
  }

  it("list GETs /v1/artifact-templates and passes the whole payload through", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        templates: [ROW],
        generation_enabled: { prd: false, tickets: false, impl_spec: false },
      }),
    )
    const r = await artifactTemplatesApi.list()
    const [url, init] = lastCall()
    expect(url).toBe(`${API_URL}/v1/artifact-templates`)
    expect(init.method).toBe("GET")
    expect(init.credentials).toBe("include")
    // `generation_enabled` is TOP-LEVEL, not per row — the screen renders three
    // group headers with zero rows and needs it there.
    expect(r.generation_enabled).toEqual({
      prd: false,
      tickets: false,
      impl_spec: false,
    })
    expect(r.templates).toHaveLength(1)
  })

  it("list narrows by type with an encoded query string", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { templates: [], generation_enabled: {} }),
    )
    await artifactTemplatesApi.list("impl_spec")
    expect(lastCall()[0]).toBe(`${API_URL}/v1/artifact-templates?type=impl_spec`)
  })

  it("get / preview / compile hit the right paths and methods", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, ROW))
    await artifactTemplatesApi.get("t1")
    expect(lastCall()[0]).toBe(`${API_URL}/v1/artifact-templates/t1`)
    expect(lastCall()[1].method).toBe("GET")

    await artifactTemplatesApi.preview("t1")
    expect(lastCall()[0]).toBe(`${API_URL}/v1/artifact-templates/t1/preview`)
    expect(lastCall()[1].method).toBe("GET")

    await artifactTemplatesApi.compile("t1")
    expect(lastCall()[0]).toBe(`${API_URL}/v1/artifact-templates/t1/compile`)
    expect(lastCall()[1].method).toBe("POST")
  })

  it("encodes an id that would otherwise change the path", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, ROW))
    await artifactTemplatesApi.get("a/b?c")
    expect(lastCall()[0]).toBe(`${API_URL}/v1/artifact-templates/a%2Fb%3Fc`)
  })

  it("create POSTs a JSON body", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, ROW))
    await artifactTemplatesApi.create({
      name: "Acme PRD v3",
      artifact_type: "prd",
      source_md: "# Product requirements",
    })
    const [url, init] = lastCall()
    expect(url).toBe(`${API_URL}/v1/artifact-templates`)
    expect(init.method).toBe("POST")
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    )
    expect(JSON.parse(init.body as string)).toEqual({
      name: "Acme PRD v3",
      artifact_type: "prd",
      source_md: "# Product requirements",
    })
  })

  it("upload POSTs multipart to the SAME path — file, artifact_type, name", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, ROW))
    const file = new File(["# Product requirements"], "acme.md", {
      type: "text/markdown",
    })
    await artifactTemplatesApi.upload(file, "prd", "Acme PRD v3")
    const [url, init] = lastCall()
    expect(url).toBe(`${API_URL}/v1/artifact-templates`)
    expect(init.method).toBe("POST")
    const form = init.body as FormData
    expect(form instanceof FormData).toBe(true)
    expect((form.get("file") as File).name).toBe("acme.md")
    expect(form.get("artifact_type")).toBe("prd")
    expect(form.get("name")).toBe("Acme PRD v3")
    // Never a JSON content-type on a multipart body — the boundary is the
    // browser's to set.
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined()
  })

  it("upload omits `name` entirely when none is given (server uses the filename)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, ROW))
    await artifactTemplatesApi.upload(
      new File(["#"], "acme.md", { type: "text/markdown" }),
      "tickets",
    )
    const form = lastCall()[1].body as FormData
    expect(form.get("name")).toBeNull()
  })

  it("update PATCHes only what changed", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, ROW))
    await artifactTemplatesApi.update("t1", { name: "Acme PRD v4" })
    const [url, init] = lastCall()
    expect(url).toBe(`${API_URL}/v1/artifact-templates/t1`)
    expect(init.method).toBe("PATCH")
    // An omitted field means "not sent" — the rename must never blank a source
    // it didn't render.
    expect(JSON.parse(init.body as string)).toEqual({ name: "Acme PRD v4" })
  })

  it("activate / deactivate POST, remove DELETEs", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, ROW))
    await artifactTemplatesApi.activate("t1")
    expect(lastCall()[0]).toBe(`${API_URL}/v1/artifact-templates/t1/activate`)
    expect(lastCall()[1].method).toBe("POST")

    await artifactTemplatesApi.deactivate("t1")
    expect(lastCall()[0]).toBe(`${API_URL}/v1/artifact-templates/t1/deactivate`)
    expect(lastCall()[1].method).toBe("POST")

    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        deleted: true,
        id: "t1",
        artifact_type: "prd",
        fell_back_to_builtin: true,
      }),
    )
    const del = await artifactTemplatesApi.remove("t1")
    expect(lastCall()[0]).toBe(`${API_URL}/v1/artifact-templates/t1`)
    expect(lastCall()[1].method).toBe("DELETE")
    expect(del.fell_back_to_builtin).toBe(true)
  })

  describe("error passthrough", () => {
    it("throws ApiError carrying the status and the parsed body", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(404, { detail: "Format not found." }),
      )
      await expect(artifactTemplatesApi.get("nope")).rejects.toMatchObject({
        status: 404,
      })
    })

    it("activate's 409 body survives intact for the caller to translate", async () => {
      // apiErrorMessage can't read this shape (object detail), so the BODY is
      // the contract — compileNotes.activationRefusal reads it.
      const detail = {
        message: "This format isn't ready.",
        code: "not_ready",
        notes: [{ code: "missing_evidence_list", message: "no ul.ev" }],
      }
      fetchMock.mockResolvedValueOnce(jsonResponse(409, { detail }))
      try {
        await artifactTemplatesApi.activate("t1")
        throw new Error("expected a rejection")
      } catch (e) {
        expect(e).toBeInstanceOf(ApiError)
        const err = e as ApiError
        expect(err.status).toBe(409)
        expect((err.body as { detail: unknown }).detail).toEqual(detail)
        // And the reason this test exists: the generic message is useless here.
        expect(err.message).toBe("Request failed (409)")
      }
    })

    it("a 403 detail is a plain string and reads fine through err.message", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(403, {
          detail: "Only an admin can change your team's format.",
        }),
      )
      await expect(artifactTemplatesApi.activate("t1")).rejects.toThrow(
        /Only an admin can change your team's format\./,
      )
    })
  })

  describe("X-Workspace-Id is injected centrally, never per call", () => {
    it("is absent when no workspace is active", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, { templates: [], generation_enabled: {} }),
      )
      await artifactTemplatesApi.list()
      const headers = lastCall()[1].headers as Record<string, string>
      expect(headers["X-Workspace-Id"]).toBeUndefined()
    })

    it("appears on EVERY call once the injector is set — not added by the wrapper", async () => {
      setActiveWorkspaceId("ws-1")
      fetchMock.mockResolvedValue(jsonResponse(200, ROW))
      await artifactTemplatesApi.get("t1")
      expect(
        (lastCall()[1].headers as Record<string, string>)["X-Workspace-Id"],
      ).toBe("ws-1")
      await artifactTemplatesApi.activate("t1")
      expect(
        (lastCall()[1].headers as Record<string, string>)["X-Workspace-Id"],
      ).toBe("ws-1")
      // Multipart too — the injector runs before the body type is considered.
      await artifactTemplatesApi.upload(
        new File(["#"], "a.md", { type: "text/markdown" }),
        "prd",
      )
      expect(
        (lastCall()[1].headers as Record<string, string>)["X-Workspace-Id"],
      ).toBe("ws-1")
    })
  })
})

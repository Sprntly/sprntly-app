import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  API_URL,
  artifactTemplatesApi,
  setActiveWorkspaceId,
  type ArtifactTemplateDetail,
  type ArtifactTemplateList,
  type ArtifactTemplatePreview,
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

const DETAIL: ArtifactTemplateDetail = {
  id: "t-1",
  name: "Acme PRD v3",
  artifact_type: "prd",
  uploader_name: "Ada",
  created_at: "2026-08-05T00:00:00Z",
  updated_at: "2026-08-05T00:00:00Z",
  compile_status: "pending",
  is_active: false,
  source_chars: 42,
  compile_summary: null,
  compile_note_count: 0,
  source_md: "# Acme PRD\n",
  content_hash: "abc123def456",
  compile_notes: [],
  section_map: { sections: [], unmapped_house: [], extra_sections: [] },
}

describe("artifactTemplatesApi", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    setActiveWorkspaceId(null)
  })

  describe("list", () => {
    it("GETs /v1/artifact-templates and returns templates + generation_enabled", async () => {
      const body: ArtifactTemplateList = {
        templates: [{ ...DETAIL }],
        generation_enabled: { prd: false, tickets: false, impl_spec: false },
      }
      fetchMock.mockResolvedValueOnce(jsonResponse(200, body))

      const r = await artifactTemplatesApi.list()
      // generation_enabled is TOP-LEVEL, not per row: the screen renders all
      // three group headers even with zero templates, and two of them need the
      // "not wired yet" note with no row to hang it off.
      expect(r.generation_enabled).toEqual({
        prd: false,
        tickets: false,
        impl_spec: false,
      })
      expect(r.templates).toHaveLength(1)

      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/artifact-templates`)
      expect(init.method).toBe("GET")
      expect(init.credentials).toBe("include")
    })

    it("appends ?type= when narrowing to one artifact type", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          templates: [],
          generation_enabled: { prd: false, tickets: false, impl_spec: false },
        }),
      )
      await artifactTemplatesApi.list("impl_spec")
      const [url] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/artifact-templates?type=impl_spec`)
    })
  })

  describe("create vs upload — one route, two body shapes", () => {
    it("create POSTs a JSON body", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(201, DETAIL))
      await artifactTemplatesApi.create({
        name: "Acme PRD v3",
        artifact_type: "prd",
        source_md: "# Acme PRD\n",
      })
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/artifact-templates`)
      expect(init.method).toBe("POST")
      expect(JSON.parse(init.body as string)).toEqual({
        name: "Acme PRD v3",
        artifact_type: "prd",
        source_md: "# Acme PRD\n",
      })
      expect(
        (init.headers as Record<string, string>)["Content-Type"],
      ).toBe("application/json")
    })

    it("upload POSTs multipart and never sets Content-Type by hand", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(201, DETAIL))
      const file = new File(["# Acme PRD\n"], "acme-prd.md", {
        type: "text/markdown",
      })
      await artifactTemplatesApi.upload(file, "prd", "Acme PRD v3")

      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/artifact-templates`)
      expect(init.body).toBeInstanceOf(FormData)
      const form = init.body as FormData
      expect(form.get("artifact_type")).toBe("prd")
      expect(form.get("name")).toBe("Acme PRD v3")
      expect((form.get("file") as File).name).toBe("acme-prd.md")
      // Setting Content-Type on a multipart request drops the boundary and the
      // server can't parse the form.
      expect(
        (init.headers as Record<string, string>)["Content-Type"],
      ).toBeUndefined()
    })

    it("upload omits `name` entirely when none is given, so the server falls back to the filename", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(201, DETAIL))
      const file = new File(["# x\n"], "Acme PRD v3.md", { type: "text/markdown" })
      await artifactTemplatesApi.upload(file, "prd")
      const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect((init.body as FormData).get("name")).toBeNull()
    })
  })

  describe("per-id routes", () => {
    it("get / preview / compile / activate / deactivate / remove hit the right paths and methods", async () => {
      const preview: ArtifactTemplatePreview = {
        id: "t-1",
        name: "Acme PRD v3",
        artifact_type: "prd",
        compile_status: "ready",
        compile_notes: [],
        format: "html",
        body: "<html></html>",
        section_map: { sections: [], unmapped_house: [], extra_sections: [] },
      }
      fetchMock
        .mockResolvedValueOnce(jsonResponse(200, DETAIL))
        .mockResolvedValueOnce(jsonResponse(200, preview))
        .mockResolvedValueOnce(jsonResponse(200, preview))
        .mockResolvedValueOnce(jsonResponse(200, DETAIL))
        .mockResolvedValueOnce(jsonResponse(200, DETAIL))
        .mockResolvedValueOnce(
          jsonResponse(200, {
            deleted: true,
            id: "t-1",
            artifact_type: "prd",
            fell_back_to_builtin: false,
          }),
        )

      await artifactTemplatesApi.get("t-1")
      await artifactTemplatesApi.preview("t-1")
      await artifactTemplatesApi.compile("t-1")
      await artifactTemplatesApi.activate("t-1")
      await artifactTemplatesApi.deactivate("t-1")
      await artifactTemplatesApi.remove("t-1")

      const seen = fetchMock.mock.calls.map(([url, init]) => [
        url,
        (init as RequestInit).method,
      ])
      expect(seen).toEqual([
        [`${API_URL}/v1/artifact-templates/t-1`, "GET"],
        [`${API_URL}/v1/artifact-templates/t-1/preview`, "GET"],
        [`${API_URL}/v1/artifact-templates/t-1/compile`, "POST"],
        [`${API_URL}/v1/artifact-templates/t-1/activate`, "POST"],
        [`${API_URL}/v1/artifact-templates/t-1/deactivate`, "POST"],
        [`${API_URL}/v1/artifact-templates/t-1`, "DELETE"],
      ])
    })

    it("encodes the id, so an id with a slash can't escape the route", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(200, DETAIL))
      await artifactTemplatesApi.get("a/b")
      const [url] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/artifact-templates/a%2Fb`)
    })

    it("update PATCHes only the fields it was given", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(200, DETAIL))
      await artifactTemplatesApi.update("t-1", { name: "Acme PRD v4" })
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe(`${API_URL}/v1/artifact-templates/t-1`)
      expect(init.method).toBe("PATCH")
      // An omitted field means "leave it alone" — the rename modal must not
      // blank a source it never rendered.
      expect(JSON.parse(init.body as string)).toEqual({ name: "Acme PRD v4" })
    })
  })

  describe("errors and headers", () => {
    it("propagates a 409 activation refusal as an ApiError carrying its detail", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(409, {
          detail: {
            message: "This PRD format isn't ready to use yet",
            code: "not_ready",
            notes: [
              { code: "missing_evidence_list", message: "No evidence list." },
            ],
          },
        }),
      )
      await expect(artifactTemplatesApi.activate("t-1")).rejects.toMatchObject({
        status: 409,
      })
    })

    it("propagates a 404 so a foreign id renders as not-found, never as denied", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(404, { detail: "Format not found." }),
      )
      await expect(artifactTemplatesApi.get("nope")).rejects.toMatchObject({
        status: 404,
      })
    })

    it("gets X-Workspace-Id from the central injector, never from a per-call header", async () => {
      setActiveWorkspaceId("ws-9")
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          templates: [],
          generation_enabled: { prd: false, tickets: false, impl_spec: false },
        }),
      )
      await artifactTemplatesApi.list()
      const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect((init.headers as Record<string, string>)["X-Workspace-Id"]).toBe(
        "ws-9",
      )
    })

    it("sends no X-Workspace-Id when no workspace is active", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, {
          templates: [],
          generation_enabled: { prd: false, tickets: false, impl_spec: false },
        }),
      )
      await artifactTemplatesApi.list()
      const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(
        (init.headers as Record<string, string>)["X-Workspace-Id"],
      ).toBeUndefined()
    })
  })
})

// @vitest-environment jsdom
//
// Round-trip tests for the EXEMPLAR half of the Templates screen ("what good
// looks like"): it lists the company's gold-standard examples, uploads a new
// one (calling templatesApi.upload), and removes one (calling
// templatesApi.remove) — then re-fetches via templatesApi.list each time.
//
// The screen now mounts a SECOND section above this one — ArtifactFormatsSection,
// the governing format library — so this file's api mock has to carry
// `artifactTemplatesApi` and the two contexts that section reads. Without them
// the mount throws and every test here fails for a reason that has nothing to
// do with exemplars. The last describe guards the one thing about the pairing
// that belongs in this file: both sections render, formats on top.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const listMock = vi.fn()
const uploadMock = vi.fn()
const removeMock = vi.fn()
const formatsListMock = vi.fn()

vi.mock("../../../../lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 0
    body: unknown = null
  },
  templatesApi: {
    list: (...a: unknown[]) => listMock(...a),
    upload: (...a: unknown[]) => uploadMock(...a),
    remove: (...a: unknown[]) => removeMock(...a),
  },
  artifactTemplatesApi: {
    list: (...a: unknown[]) => formatsListMock(...a),
    create: vi.fn(),
    upload: vi.fn(),
    update: vi.fn(),
    compile: vi.fn(),
    preview: vi.fn(),
    activate: vi.fn(),
    deactivate: vi.fn(),
    remove: vi.fn(),
    get: vi.fn(),
  },
}))

vi.mock("../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    orgRole: "admin",
    activeWorkspace: { id: "ws-1" },
    workspace: { display_name: "Acme" },
  }),
}))

vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn() }),
}))

// AppLayout drags in app contexts; the screen logic under test doesn't need it.
vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", null, children),
}))

import { TemplatesScreen } from "../TemplatesScreen"
import type { CompanyTemplate } from "../../../../lib/api"

const T1: CompanyTemplate = {
  id: "t1",
  label: "Guest Deal Alerts — PRD",
  type: "prd",
  filename: "guest.md",
  content_type: "text/markdown",
  extracted_chars: 4200,
  uploaded_at: "2026-06-01T00:00:00Z",
}

function fileInput(): HTMLInputElement {
  const el = document.querySelector(
    '[data-testid="template-file-input"]',
  ) as HTMLInputElement | null
  if (!el) throw new Error("file input not found")
  return el
}

beforeEach(() => {
  listMock.mockResolvedValue([T1])
  uploadMock.mockResolvedValue({ ok: true, ...T1, id: "t2", filename: "new.md" })
  removeMock.mockResolvedValue({ ok: true, id: "t1" })
  formatsListMock.mockResolvedValue({
    templates: [],
    generation_enabled: { prd: false, tickets: false, impl_spec: false },
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("TemplatesScreen", () => {
  it("lists templates fetched from templatesApi.list on mount", async () => {
    await act(async () => {
      render(React.createElement(TemplatesScreen))
    })
    await waitFor(() => expect(listMock).toHaveBeenCalled())
    expect(screen.getByText("Guest Deal Alerts — PRD")).toBeTruthy()
  })

  it("uploads a picked file via templatesApi.upload, then refetches", async () => {
    await act(async () => {
      render(React.createElement(TemplatesScreen))
    })
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1))

    const file = new File(["# Gold"], "new.md", { type: "text/markdown" })
    await act(async () => {
      fireEvent.change(fileInput(), { target: { files: [file] } })
    })

    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(1))
    // The picked file is passed through to the API.
    const [passedFile] = uploadMock.mock.calls[0]
    expect((passedFile as File).name).toBe("new.md")
    // A refetch follows a successful upload.
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
  })

  it("removes a template via templatesApi.remove, then refetches", async () => {
    await act(async () => {
      render(React.createElement(TemplatesScreen))
    })
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1))

    const removeBtn = screen.getByRole("button", {
      name: /remove guest deal alerts/i,
    })
    await act(async () => {
      fireEvent.click(removeBtn)
    })

    await waitFor(() => expect(removeMock).toHaveBeenCalledWith("t1"))
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
  })

  it("filters the visible cards by type client-side", async () => {
    listMock.mockResolvedValue([
      T1,
      { ...T1, id: "t2", label: "Strategy doc", type: "strategy", filename: "s.md" },
    ])
    await act(async () => {
      render(React.createElement(TemplatesScreen))
    })
    await waitFor(() => expect(screen.getByText("Strategy doc")).toBeTruthy())

    // Click the PRD filter pill → only the PRD card remains.
    await act(async () => {
      fireEvent.click(screen.getByRole("tab", { name: "PRD" }))
    })
    expect(screen.queryByText("Strategy doc")).toBeNull()
    expect(screen.getByText("Guest Deal Alerts — PRD")).toBeTruthy()
    // No refetch — filtering is client-side.
    expect(listMock).toHaveBeenCalledTimes(1)
  })

  it("surfaces an error when loading fails", async () => {
    listMock.mockRejectedValueOnce(new Error("network down"))
    await act(async () => {
      render(React.createElement(TemplatesScreen))
    })
    await waitFor(() => expect(screen.getByText(/network down/i)).toBeTruthy())
  })
})

// ── the two libraries on one screen ──────────────────────────────────────────
describe("TemplatesScreen — formats above examples", () => {
  it("mounts BOTH sections, governing library first, in one scroller", async () => {
    const { container } = render(React.createElement(TemplatesScreen))
    await act(async () => {})
    await waitFor(() => expect(formatsListMock).toHaveBeenCalled())

    const page = container.querySelector(".tplpage")
    expect(page).toBeTruthy()
    // Order is the design: the silhouette does the work before a word is read,
    // and the governing library has to be the one on top.
    expect(container.querySelector(".tplpage > .afmt")).toBeTruthy()
    const html = container.innerHTML
    expect(html.indexOf("Formats we write in")).toBeLessThan(
      html.indexOf("Examples we learn from"),
    )
  })

  it("each section fetches independently — a formats failure leaves examples intact", async () => {
    // §2 is a separate fetch on purpose: one library being unreachable must not
    // take the other off screen.
    formatsListMock.mockRejectedValue(new Error("formats down"))
    await act(async () => {
      render(React.createElement(TemplatesScreen))
    })
    await waitFor(() =>
      expect(screen.getByText(/We couldn't load your document formats/)).toBeTruthy(),
    )
    expect(screen.getByText("Guest Deal Alerts — PRD")).toBeTruthy()
  })
})

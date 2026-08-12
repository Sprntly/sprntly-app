// @vitest-environment jsdom
//
// The footer's Format control: shows which format wrote this PRD, opens a
// picker of the company's PRD formats (built-in first), confirms before
// re-writing, and reads the regeneration's outcome from the row's stamp —
// an unchanged artifact_template_id means the switch failed and the previous
// document still stands.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const toastMock = vi.hoisted(() => ({ fn: vi.fn() }))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: toastMock.fn, openContentPanel: vi.fn() }),
}))
const contentMock = vi.hoisted(() => ({
  value: {} as Record<string, unknown>,
  set: vi.fn(),
}))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: contentMock.set }),
}))
vi.mock("../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme" }),
}))
const apiMock = vi.hoisted(() => ({
  changeTemplate: vi.fn(),
  get: vi.fn(),
  list: vi.fn(),
}))
vi.mock("../../../lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 404
  },
  prdApi: {
    latest: vi.fn().mockResolvedValue({ payload_md: "" }),
    get: apiMock.get,
    update: vi.fn(),
    listVersions: vi.fn(),
    listGenerations: vi.fn(),
    restoreVersion: vi.fn(),
    changeTemplate: apiMock.changeTemplate,
  },
  artifactTemplatesApi: { list: apiMock.list },
  designAgentApi: { getByPrd: vi.fn() },
  multiAgentApi: { getQaScenarios: vi.fn().mockResolvedValue({ doc: null }) },
  storiesApi: { getForPrd: vi.fn().mockResolvedValue({ status: "none", fresh: false, stories: [] }) },
}))
const regenMock = vi.hoisted(() => ({ resume: vi.fn() }))
vi.mock("../../../lib/runPrdGeneration", () => ({
  resumePrdGeneration: regenMock.resume,
}))

import { PrdPanelContent } from "../PrdPanelContent"

const ACME_FORMAT = {
  id: "tpl-acme",
  name: "Acme PRD v2",
  artifact_type: "prd",
  uploader_name: "Ada",
  created_at: null,
  updated_at: null,
  compile_status: "ready",
  is_active: true,
  source_chars: 100,
  compile_summary: null,
  compile_note_count: 0,
  summary: "Two sections, evidence-first.",
}

function basePrd(overrides: Record<string, unknown> = {}) {
  return {
    prd_id: 7,
    title: "Dark mode",
    metaLine: "",
    sections: [],
    // Known format state: built-in. `undefined` here exercises hydration.
    artifactTemplateId: null,
    artifactTemplateName: null,
    ...overrides,
  }
}

function renderWith(content: Record<string, unknown>) {
  contentMock.value = {
    prd: null,
    prdGenerating: false,
    prdPartialHtml: null,
    ...content,
  }
  return render(React.createElement(PrdPanelContent))
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("PrdPanelContent — the Format control", () => {
  it("labels the footer with the built-in for an unstamped PRD", async () => {
    renderWith({ prd: basePrd() })
    await waitFor(() => {})

    expect(screen.getByTestId("prd-format-toggle").textContent).toContain(
      "Format: Sprntly built-in",
    )
  })

  it("labels the footer with the stamped format's name, clamped", async () => {
    renderWith({
      prd: basePrd({
        artifactTemplateId: "tpl-acme",
        artifactTemplateName: "A very long format name that will not fit",
      }),
    })
    await waitFor(() => {})

    const label = screen.getByTestId("prd-format-toggle").textContent ?? ""
    expect(label).toContain("Format: A very long format name")
    expect(label).toContain("…")
  })

  it("hydrates the label with one GET when the load path predates the field", async () => {
    apiMock.get.mockResolvedValue({
      artifact_template_id: "tpl-acme",
      artifact_template_name: "Acme PRD v2",
    })
    renderWith({ prd: basePrd({ artifactTemplateId: undefined, artifactTemplateName: undefined }) })

    await waitFor(() => {
      expect(screen.getByTestId("prd-format-toggle").textContent).toContain(
        "Format: Acme PRD v2",
      )
    })
    expect(apiMock.get).toHaveBeenCalledWith(7)
  })

  it("opens the picker: built-in row first and Current, uploads offered, hint line present", async () => {
    apiMock.list.mockResolvedValue({ templates: [ACME_FORMAT], generation_enabled: { prd: true } })
    renderWith({ prd: basePrd() })
    await waitFor(() => {})

    fireEvent.click(screen.getByTestId("prd-format-toggle"))

    await waitFor(() => {
      expect(screen.getByText("Sprntly's built-in PRD format")).toBeTruthy()
    })
    expect(apiMock.list).toHaveBeenCalledWith("prd")
    // The current format has NO action — picking it is impossible.
    expect(screen.getByText("Current")).toBeTruthy()
    expect(screen.getAllByText("Use this format")).toHaveLength(1)
    expect(
      screen.getByText(/Switching re-writes this document into the new structure/),
    ).toBeTruthy()
  })

  it("confirms, dispatches the switch, and reports success when the stamp landed", async () => {
    apiMock.list.mockResolvedValue({ templates: [ACME_FORMAT], generation_enabled: { prd: true } })
    apiMock.changeTemplate.mockResolvedValue({ prd_id: 7, status: "generating", artifact_template_id: "tpl-acme" })
    regenMock.resume.mockResolvedValue({
      ok: true,
      prd: { ...basePrd(), artifactTemplateId: "tpl-acme", artifactTemplateName: "Acme PRD v2" },
    })
    renderWith({ prd: basePrd() })
    await waitFor(() => {})

    fireEvent.click(screen.getByTestId("prd-format-toggle"))
    await waitFor(() => screen.getByText("Use this format"))
    fireEvent.click(screen.getByText("Use this format"))

    // The confirm restates the consequence before anything is sent.
    expect(screen.getByText("Re-write this PRD in “Acme PRD v2”?")).toBeTruthy()
    expect(apiMock.changeTemplate).not.toHaveBeenCalled()

    fireEvent.click(screen.getByText("Re-write in this format"))

    await waitFor(() => {
      expect(apiMock.changeTemplate).toHaveBeenCalledWith(7, "tpl-acme")
    })
    await waitFor(() => {
      expect(toastMock.fn).toHaveBeenCalledWith(
        "Format switched",
        expect.stringContaining("Acme PRD v2"),
      )
    })
    // The panel was put into the generating state for the poll to fill.
    expect(contentMock.set).toHaveBeenCalledWith(
      expect.objectContaining({ prd: null, prdGenerating: true }),
    )
  })

  it("reports failure when the regeneration came back with the old stamp", async () => {
    apiMock.list.mockResolvedValue({ templates: [ACME_FORMAT], generation_enabled: { prd: true } })
    apiMock.changeTemplate.mockResolvedValue({ prd_id: 7, status: "generating", artifact_template_id: "tpl-acme" })
    // Ready again, but still the built-in: the backend restored the previous
    // document after a failed regeneration. Content shown is still correct.
    regenMock.resume.mockResolvedValue({
      ok: true,
      prd: { ...basePrd(), artifactTemplateId: null },
    })
    renderWith({ prd: basePrd() })
    await waitFor(() => {})

    fireEvent.click(screen.getByTestId("prd-format-toggle"))
    await waitFor(() => screen.getByText("Use this format"))
    fireEvent.click(screen.getByText("Use this format"))
    fireEvent.click(screen.getByText("Re-write in this format"))

    await waitFor(() => {
      expect(toastMock.fn).toHaveBeenCalledWith(
        "Couldn't switch the format",
        expect.stringContaining("unchanged"),
      )
    })
  })

  it("skips the regenerating state entirely on an unchanged no-op", async () => {
    apiMock.list.mockResolvedValue({ templates: [ACME_FORMAT], generation_enabled: { prd: true } })
    apiMock.changeTemplate.mockResolvedValue({ prd_id: 7, status: "ready", unchanged: true, artifact_template_id: null })
    renderWith({ prd: basePrd() })
    await waitFor(() => {})

    fireEvent.click(screen.getByTestId("prd-format-toggle"))
    await waitFor(() => screen.getByText("Use this format"))
    fireEvent.click(screen.getByText("Use this format"))
    fireEvent.click(screen.getByText("Re-write in this format"))

    await waitFor(() => expect(apiMock.changeTemplate).toHaveBeenCalled())
    expect(regenMock.resume).not.toHaveBeenCalled()
    expect(contentMock.set).not.toHaveBeenCalledWith(
      expect.objectContaining({ prdGenerating: true }),
    )
  })

  it("says so when the company has no PRD formats yet", async () => {
    apiMock.list.mockResolvedValue({ templates: [], generation_enabled: { prd: true } })
    renderWith({ prd: basePrd() })
    await waitFor(() => {})

    fireEvent.click(screen.getByTestId("prd-format-toggle"))

    await waitFor(() => {
      expect(
        screen.getByText(/No PRD formats uploaded yet — add one on the Templates screen\./),
      ).toBeTruthy()
    })
  })

  it("offers a retry when the format list cannot load", async () => {
    apiMock.list.mockRejectedValueOnce(new Error("offline"))
    apiMock.list.mockResolvedValueOnce({ templates: [ACME_FORMAT], generation_enabled: { prd: true } })
    renderWith({ prd: basePrd() })
    await waitFor(() => {})

    fireEvent.click(screen.getByTestId("prd-format-toggle"))
    await waitFor(() => screen.getByText(/couldn't load your formats/i))

    fireEvent.click(screen.getByText("Try again"))
    await waitFor(() => screen.getByText("Acme PRD v2"))
  })
})

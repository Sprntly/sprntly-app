// @vitest-environment jsdom
//
// PrdPanelContent is bound by default to the workspace-root ContentContext/
// NavigationContext (main chat, unchanged). This suite proves the two halves
// of the context-optional contract added on top of that:
//   * no override props (every EXISTING caller) → behaves exactly as it did
//     before: renders the context's PRD, routes actions through context.
//   * override props provided → used INSTEAD of context, so the same panel
//     can be driven by a caller-owned PRD (e.g. a project's own scoped
//     fetch) without touching the global, workspace-wide content slice.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const navMock = vi.hoisted(() => ({
  showToast: vi.fn(),
  openContentPanel: vi.fn(),
}))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: navMock.showToast, openContentPanel: navMock.openContentPanel }),
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
import type { PrdState } from "../../../types/content"

function basePrd(overrides: Record<string, unknown> = {}): PrdState {
  return {
    prd_id: 7,
    title: "Context PRD",
    metaLine: "",
    sections: [],
    artifactTemplateId: null,
    artifactTemplateName: null,
    ...overrides,
  } as unknown as PrdState
}

function setGlobalContent(content: Record<string, unknown>) {
  contentMock.value = {
    prd: null,
    prdGenerating: false,
    prdPartialHtml: null,
    ...content,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("PrdPanelContent — no override props (main chat, unchanged)", () => {
  it("renders the context's PRD when no props override it", async () => {
    setGlobalContent({ prd: basePrd({ title: "Context PRD" }) })
    render(React.createElement(PrdPanelContent))
    await waitFor(() => {})

    expect(screen.getByText("Context PRD")).toBeTruthy()
  })

  it("routes the tickets CTA through context's openContentPanel when onOpenTab is absent", async () => {
    setGlobalContent({ prd: basePrd() })
    render(React.createElement(PrdPanelContent))
    await waitFor(() => {})

    fireEvent.click(screen.getByTestId("prd-footer-tickets-cta"))
    expect(navMock.openContentPanel).toHaveBeenCalledWith("tickets")
  })
})

describe("PrdPanelContent — override props (props-drivable, context as fallback)", () => {
  it("renders the injected `prd` prop instead of the context's", async () => {
    setGlobalContent({ prd: basePrd({ title: "Context PRD" }) })
    render(<PrdPanelContent prd={basePrd({ prd_id: 900, title: "Injected PRD" })} />)
    await waitFor(() => {})

    expect(screen.getByText("Injected PRD")).toBeTruthy()
    expect(screen.queryByText("Context PRD")).toBeNull()
  })

  it("routes the tickets CTA through `onOpenTab` instead of context, when provided", async () => {
    setGlobalContent({ prd: basePrd({ title: "Context PRD" }) })
    const onOpenTab = vi.fn()
    render(
      <PrdPanelContent
        prd={basePrd({ prd_id: 900, title: "Injected PRD" })}
        onOpenTab={onOpenTab}
      />,
    )
    await waitFor(() => {})

    fireEvent.click(screen.getByTestId("prd-footer-tickets-cta"))
    expect(onOpenTab).toHaveBeenCalledWith("tickets")
    expect(navMock.openContentPanel).not.toHaveBeenCalled()
  })

  it("drives a format switch through `onPrdContentChange`/`onToast` instead of the global context", async () => {
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
    apiMock.list.mockResolvedValue({ templates: [ACME_FORMAT], generation_enabled: { prd: true } })
    apiMock.changeTemplate.mockResolvedValue({ prd_id: 900, status: "generating", artifact_template_id: "tpl-acme" })
    regenMock.resume.mockResolvedValue({
      ok: true,
      prd: { ...basePrd({ prd_id: 900 }), artifactTemplateId: "tpl-acme", artifactTemplateName: "Acme PRD v2" },
    })

    // The global context holds a DIFFERENT PRD entirely — proves the switch
    // never touches it when overrides are supplied.
    setGlobalContent({ prd: basePrd({ title: "Context PRD" }) })
    const onPrdContentChange = vi.fn()
    const onToast = vi.fn()
    render(
      <PrdPanelContent
        prd={basePrd({ prd_id: 900, title: "Injected PRD" })}
        onPrdContentChange={onPrdContentChange}
        onToast={onToast}
      />,
    )
    await waitFor(() => {})

    fireEvent.click(screen.getByTestId("prd-format-toggle"))
    await waitFor(() => screen.getByText("Use this format"))
    fireEvent.click(screen.getByText("Use this format"))
    fireEvent.click(screen.getByText("Re-write in this format"))

    await waitFor(() => {
      expect(apiMock.changeTemplate).toHaveBeenCalledWith(900, "tpl-acme")
    })
    await waitFor(() => {
      expect(onToast).toHaveBeenCalledWith("Format switched", expect.stringContaining("Acme PRD v2"))
    })
    expect(onPrdContentChange).toHaveBeenCalledWith(
      expect.objectContaining({ prd: null, prdGenerating: true }),
    )
    // Neither the global toast nor the global content patch fired — the
    // workspace-wide PRD in `contentMock` was never touched by this switch.
    expect(navMock.showToast).not.toHaveBeenCalled()
    expect(contentMock.set).not.toHaveBeenCalled()
  })
})

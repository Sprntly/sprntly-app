// @vitest-environment jsdom
//
// Live streaming preview: while a PRD generation is in flight AND partial Part A
// HTML has arrived (content.prdPartialHtml), the panel renders the growing
// document in a sandboxed read-only iframe with a slim "Generating…" indicator —
// not the old full-pane spinner. With no partial yet, the spinner still shows.
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), openContentPanel: vi.fn() }),
}))
const contentMock = vi.hoisted(() => ({ value: {} as Record<string, unknown> }))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: vi.fn() }),
}))
vi.mock("../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme" }),
}))
vi.mock("../../../lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 404
  },
  prdApi: {
    latest: vi.fn().mockResolvedValue({ payload_md: "" }),
    get: vi.fn(),
    update: vi.fn(),
    listVersions: vi.fn(),
    listGenerations: vi.fn(),
    restoreVersion: vi.fn(),
  },
  designAgentApi: { getByPrd: vi.fn() },
  multiAgentApi: { getQaScenarios: vi.fn() },
  storiesApi: { getForPrd: vi.fn().mockResolvedValue({ status: "none", fresh: false, stories: [] }) },
}))

import { PrdPanelContent } from "../PrdPanelContent"

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

describe("PrdPanelContent — live streaming preview", () => {
  it("renders the partial HTML in a sandboxed iframe with the slim indicator (no full-pane spinner)", async () => {
    renderWith({
      prdGenerating: true,
      prdPartialHtml: "<!doctype html><html><body><h1>Draft PRD</h1>",
    })
    await waitFor(() => {})

    // Slim pulsing indicator, not the big "Generating PRD…" pane.
    expect(screen.getByTestId("prd-streaming")).toBeTruthy()
    expect(screen.queryByTestId("prd-generating")).toBeNull()

    // The preview iframe is sandboxed (no script execution) and read-only.
    const iframe = screen.getByTestId("prd-streaming-preview") as HTMLIFrameElement
    expect(iframe.getAttribute("sandbox")).toBe("allow-same-origin")
  })

  it("keeps the full-pane spinner while generating with no partial yet", async () => {
    renderWith({ prdGenerating: true, prdPartialHtml: null })
    await waitFor(() => {})

    expect(screen.getByTestId("prd-generating")).toBeTruthy()
    expect(screen.queryByTestId("prd-streaming")).toBeNull()
  })

  it("ignores a stale partial once generation is over (empty state, no preview)", async () => {
    // Terminal paths clear prdPartialHtml, but even a missed clear must not
    // resurrect the preview once prdGenerating is off.
    renderWith({ prdGenerating: false, prdPartialHtml: "<!doctype html><p>old</p>" })
    await waitFor(() => {})

    expect(screen.queryByTestId("prd-streaming")).toBeNull()
    expect(screen.queryByTestId("prd-streaming-preview")).toBeNull()
  })
})

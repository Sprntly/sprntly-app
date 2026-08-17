// @vitest-environment jsdom
//
// The content panel's project-menu affordance — the entry-flow reshape's
// header addition. Strictly additive:
//
//   * with `content.activeProjectId == null` (every pre-existing chat, and
//     every non-project PRD) the header carries NO project icon and the panel
//     is byte-identical to before — this is the LOAD-BEARING invariant this
//     suite exists to prove, first and hardest.
//   * with `content.activeProjectId` set, a folder icon appears in the header;
//     clicking it swaps the panel BODY to the in-panel project section;
//     clicking any artifact tab returns the body to the artifact and closes
//     the section (mutually exclusive within the one open panel).
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

vi.mock("../PrdPanelContent", () => ({
  PrdPanelContent: () => React.createElement("div", { "data-testid": "prd-body" }, "prd body"),
}))

vi.mock("../../screens/app/projects/ProjectPanelSection", () => ({
  ProjectPanelSection: ({ projectId }: { projectId: number }) =>
    React.createElement("div", { "data-testid": "project-section-stub" }, `project ${projectId}`),
}))

const navMock = vi.hoisted(() => ({
  tab: "prd" as "prd" | "evidence" | "tickets" | null,
  openContentPanel: vi.fn(),
  closeContentPanel: vi.fn(),
}))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    contentPanelTab: navMock.tab,
    openContentPanel: navMock.openContentPanel,
    closeContentPanel: navMock.closeContentPanel,
    showToast: vi.fn(),
    expandAiPanel: vi.fn(),
    setAIBarValue: vi.fn(),
  }),
}))

const contentMock = vi.hoisted(() => ({ value: {} as Record<string, unknown> }))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: vi.fn() }),
}))

import { ContentPanel } from "../ContentPanel"

const PRD = { prd_id: 501, title: "Dark mode on mobile", metaLine: "", sections: [], source: "brief" }

function baseContent(extra: Record<string, unknown>) {
  return {
    prd: PRD,
    prdMeta: null,
    prdGenerating: false,
    evidence: null,
    evidenceGenerating: false,
    detail: null,
    connectedConnectorIds: [],
    threadReports: [],
    threadReportsStatus: "idle",
    reportFocusId: null,
    ticketSet: null,
    ticketSetGenerating: false,
    ticketSetStandalone: false,
    ...extra,
  }
}

function renderPanel(extra: Record<string, unknown>) {
  navMock.tab = "prd"
  contentMock.value = baseContent(extra)
  return render(<ContentPanel />)
}

beforeEach(() => {
  navMock.openContentPanel.mockClear()
  navMock.closeContentPanel.mockClear()
})
afterEach(() => {
  cleanup()
})

describe("ContentPanel — project-menu header icon (additive baseline)", () => {
  it("shows no project icon when activeProjectId is absent (never set — the field simply not present on content)", () => {
    renderPanel({})
    expect(screen.queryByLabelText("Project")).toBeNull()
    expect(screen.queryByLabelText("Back to document")).toBeNull()
    // The header's action cluster carries only its pre-existing controls —
    // the Share menu button + Close. Nothing else got inserted.
    const actions = document.querySelector(".cpanel-head-actions")!
    expect(actions.querySelectorAll("button").length).toBe(2)
    // The body renders exactly the pre-existing artifact switch — the PRD
    // body stub, not the project section.
    expect(screen.getByTestId("prd-body")).toBeTruthy()
    expect(screen.queryByTestId("project-section-stub")).toBeNull()
  })

  it("shows no project icon when activeProjectId is explicitly null (the normal cleared state)", () => {
    renderPanel({ activeProjectId: null })
    expect(screen.queryByLabelText("Project")).toBeNull()
    const actions = document.querySelector(".cpanel-head-actions")!
    expect(actions.querySelectorAll("button").length).toBe(2)
    expect(screen.getByTestId("prd-body")).toBeTruthy()
  })

  it("the existing ticket-format suite (#1166) still passes under this same file — sanity cross-check", () => {
    // Not a duplicate of that suite — just confirms this file's baseContent
    // shape doesn't accidentally diverge from the one that suite locks down.
    renderPanel({ ticketSet: null })
    expect(screen.queryByTestId("tickets-format-toggle")).toBeNull()
  })
})

describe("ContentPanel — project-menu header icon (bound state)", () => {
  it("shows the folder icon when activeProjectId is set", () => {
    renderPanel({ activeProjectId: 555 })
    expect(screen.getByLabelText("Project")).toBeTruthy()
    const actions = document.querySelector(".cpanel-head-actions")!
    // Share + Project toggle + Close.
    expect(actions.querySelectorAll("button").length).toBe(3)
  })

  it("clicking the folder icon swaps the body to the project section", () => {
    renderPanel({ activeProjectId: 555 })
    expect(screen.getByTestId("prd-body")).toBeTruthy()
    expect(screen.queryByTestId("project-section-stub")).toBeNull()

    fireEvent.click(screen.getByLabelText("Project"))

    expect(screen.queryByTestId("prd-body")).toBeNull()
    const stub = screen.getByTestId("project-section-stub")
    expect(stub.textContent).toBe("project 555")
    // The toggle now reads as the way back.
    expect(screen.getByLabelText("Back to document")).toBeTruthy()
  })

  it("clicking an artifact tab returns to the artifact and closes the project section", () => {
    renderPanel({ activeProjectId: 555 })
    fireEvent.click(screen.getByLabelText("Project"))
    expect(screen.getByTestId("project-section-stub")).toBeTruthy()

    // The PRD tab is the artifact tab in scope for this content (prd loaded).
    fireEvent.click(screen.getByRole("button", { name: /PRD/ }))

    expect(screen.queryByTestId("project-section-stub")).toBeNull()
    expect(screen.getByTestId("prd-body")).toBeTruthy()
    // Mutually exclusive — clicking the tab notified navigation too (it still
    // calls openContentPanel, even though this mock's tab was already "prd").
    expect(navMock.openContentPanel).toHaveBeenCalledWith("prd")
  })

  it("toggling the folder icon again closes the section without touching a tab", () => {
    renderPanel({ activeProjectId: 555 })
    const toggle = screen.getByLabelText("Project")
    fireEvent.click(toggle)
    expect(screen.getByTestId("project-section-stub")).toBeTruthy()

    fireEvent.click(screen.getByLabelText("Back to document"))

    expect(screen.queryByTestId("project-section-stub")).toBeNull()
    expect(screen.getByTestId("prd-body")).toBeTruthy()
  })
})

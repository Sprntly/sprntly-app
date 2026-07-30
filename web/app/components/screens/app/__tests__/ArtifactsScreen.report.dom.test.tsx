// @vitest-environment jsdom
//
// Integration test for opening a report from ArtifactsScreen: clicking a report
// row fetches the document by id and slides it into the report drawer — NOT the
// PRD-pipeline ContentPanel. The api + contexts are stubbed so this exercises the
// screen's wiring, not the network.

import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const REPORT_ROW = {
  type: "report" as const,
  id: 4,
  title: "Voice of Customer Report · Q2",
  status: "",
  created_at: new Date().toISOString(),
  skill: "voice-of-customer-report",
  share_mode: "private" as const,
  source: {
    skill: "voice-of-customer-report",
    question: "what are customers saying?",
    conversation_id: 77,
    conversation_title: "Q2 customer themes",
    prd_id: null,
    prd_title: null,
  },
  open: { report_id: 4 },
}

const artifactsList = vi.fn((..._a: unknown[]) => Promise.resolve([REPORT_ROW]))
const reportGet = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>(null))
const reportShare = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>(null))

vi.mock("../../../../lib/api", () => ({
  artifactsApi: { list: (...a: unknown[]) => artifactsList(...a) },
  prdApi: { importDoc: vi.fn(), get: vi.fn() },
  evidenceApi: { get: vi.fn() },
  reportsApi: {
    get: (...a: unknown[]) => reportGet(...a),
    share: (...a: unknown[]) => reportShare(...a),
    downloadPdf: vi.fn(),
  },
}))

const setContent = vi.fn()
const openContentPanel = vi.fn()
const openPrdTab = vi.fn()
const showToast = vi.fn()
vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ openContentPanel, openPrdTab, showToast, contentPanelTab: null }),
}))
vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ setContent }),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme" }),
}))
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock("../../../../lib/evidence-adapter", () => ({ markdownToEvidenceState: () => ({}) }))
vi.mock("../../../../lib/routes", () => ({ prototypePath: () => "/prototype" }))
vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))

import { ArtifactsScreen } from "../ArtifactsScreen"

const DOC = {
  id: 4,
  skill: "voice-of-customer-report",
  title: "Voice of Customer Report · Q2",
  question: "what are customers saying?",
  html: "<!DOCTYPE html><html><body><h1>VoC</h1></body></html>",
  created_at: new Date().toISOString(),
  conversation_id: 77,
  prd_id: null,
  share_mode: "private",
  share_token: null,
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderAndClickReport() {
  await act(async () => { render(<ArtifactsScreen />) })
  await waitFor(() => expect(artifactsList).toHaveBeenCalled())
  const row = await waitFor(() =>
    document.querySelector('[data-artifact-type="report"]') as HTMLElement,
  )
  await act(async () => { fireEvent.click(row) })
  return row
}

describe("ArtifactsScreen — opening a report", () => {
  it("fetches the document by id and shows it in the report drawer", async () => {
    reportGet.mockResolvedValue(DOC)

    await renderAndClickReport()

    expect(reportGet).toHaveBeenCalledWith(4)
    await waitFor(() =>
      expect(screen.getByTestId("report-panel-title").textContent).toBe(
        "Voice of Customer Report · Q2",
      ),
    )
    const frame = document.querySelector("iframe") as HTMLIFrameElement
    expect(frame.getAttribute("srcdoc")).toContain("<h1>VoC</h1>")
    // A report is NOT part of the PRD pipeline — it must not touch that drawer.
    expect(openContentPanel).not.toHaveBeenCalled()
  })

  it("carries the row's attachment into the viewer header without a second fetch", async () => {
    reportGet.mockResolvedValue(DOC)

    await renderAndClickReport()

    await waitFor(() =>
      expect(screen.getByTestId("report-panel-attachment").textContent).toBe(
        "from Q2 customer themes",
      ),
    )
    expect(reportGet).toHaveBeenCalledTimes(1)
  })

  it("marks the clicked row as the selected one", async () => {
    reportGet.mockResolvedValue(DOC)

    const row = await renderAndClickReport()

    await waitFor(() => expect(row.getAttribute("data-active")).toBe("true"))
  })

  it("closes the drawer and deselects the row on close", async () => {
    reportGet.mockResolvedValue(DOC)

    const row = await renderAndClickReport()
    await waitFor(() => expect(screen.getByTestId("report-panel")).toBeTruthy())

    await act(async () => {
      fireEvent.click(document.querySelector(".cpanel-close") as HTMLElement)
    })

    // The drawer animates out before unmounting, so assert the selection
    // released — that is immediate and is what ties the row to the panel.
    await waitFor(() => expect(row.getAttribute("data-active")).toBeNull())
  })

  it("toasts and leaves no drawer behind when the fetch fails", async () => {
    reportGet.mockRejectedValue(new Error("boom"))

    await renderAndClickReport()

    await waitFor(() => expect(showToast).toHaveBeenCalled())
    expect(showToast.mock.calls[0][0]).toContain("Couldn't open artifact")
    // No half-open drawer stuck in its loading state.
    expect(screen.queryByTestId("report-panel-loading")).toBeNull()
  })
})

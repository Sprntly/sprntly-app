// @vitest-environment jsdom
//
// Integration test for opening a report from ArtifactsScreen.
//
// A report's home is the chat it was generated in, so clicking one hands off to
// that thread (the ordinary `sprntly_resume_conv` payload) and asks ChatScreen to
// land the panel's Reports tab on the document. Only a report with no surviving
// chat opens in the standalone drawer here. The api + contexts are stubbed so
// this exercises the screen's wiring, not the network.

import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const ATTACHED_ROW = {
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

/** No chat to open — the report stands alone. */
const UNATTACHED_ROW = {
  ...ATTACHED_ROW,
  id: 5,
  source: { ...ATTACHED_ROW.source, conversation_id: null, conversation_title: null },
  open: { report_id: 5 },
}

/** The chat was deleted: the id survives on the row but the title doesn't
 *  resolve, so there is no thread left to open. */
const ORPHANED_ROW = {
  ...ATTACHED_ROW,
  id: 6,
  source: { ...ATTACHED_ROW.source, conversation_id: 88, conversation_title: null },
  open: { report_id: 6 },
}

const artifactsList = vi.fn((..._a: unknown[]) => Promise.resolve<unknown[]>([ATTACHED_ROW]))
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
const openReportTab = vi.fn()
const showToast = vi.fn()
vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    openContentPanel, openPrdTab, openReportTab, showToast, contentPanelTab: null,
  }),
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

afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.clearAllMocks()
})

async function renderAndClickReport(row: unknown = ATTACHED_ROW) {
  artifactsList.mockResolvedValue([row])
  await act(async () => { render(<ArtifactsScreen />) })
  await waitFor(() => expect(artifactsList).toHaveBeenCalled())
  const el = await waitFor(() =>
    document.querySelector('[data-artifact-type="report"]') as HTMLElement,
  )
  await act(async () => { fireEvent.click(el) })
  return el
}

describe("ArtifactsScreen — opening a report attached to a chat", () => {
  it("hands off to the report's own thread and asks for it in the panel", async () => {
    await renderAndClickReport()

    expect(openReportTab).toHaveBeenCalledWith({ conversationId: 77, reportId: 4 })
    // The document is fetched by the panel's Reports tab, not here.
    expect(reportGet).not.toHaveBeenCalled()
    expect(screen.queryByTestId("report-panel")).toBeNull()
  })

  it("writes the ordinary resume hand-off so ChatScreen reopens that chat", async () => {
    await renderAndClickReport()

    const payload = JSON.parse(localStorage.getItem("sprntly_resume_conv") ?? "{}")
    expect(payload.dbId).toBe(77)
    expect(payload.title).toBe("Q2 customer themes")
  })
})

describe("ArtifactsScreen — a report with no chat to open", () => {
  it("opens an unattached report in the SAME panel, on its Reports tab", async () => {
    // There is one slide-over. A report with no thread behind it still reads
    // there — as a focused document with no list — rather than in a second
    // drawer of its own.
    await renderAndClickReport(UNATTACHED_ROW)

    expect(openReportTab).not.toHaveBeenCalled()
    // `reportFocusStandalone` is what tells the Reports tab to trust a pointer
    // with no conversation behind it. It is stated here rather than inferred
    // from the null conversation id, because a brand-new chat tab has a null
    // conversation id too — inferring it there opened THIS document inside an
    // unrelated empty chat.
    expect(setContent).toHaveBeenCalledWith({
      conversationId: null, reportFocusId: 5, reportFocusStandalone: true,
    })
    expect(openContentPanel).toHaveBeenCalledWith("reports")
  })

  it("does the same when the report's chat was deleted", async () => {
    // The conversation id survives on the row, but there is no chat left to
    // resume — opening a tab for it would land the user in an empty thread.
    await renderAndClickReport(ORPHANED_ROW)

    expect(openReportTab).not.toHaveBeenCalled()
    expect(localStorage.getItem("sprntly_resume_conv")).toBeNull()
    expect(setContent).toHaveBeenCalledWith({
      conversationId: null, reportFocusId: 6, reportFocusStandalone: true,
    })
    expect(openContentPanel).toHaveBeenCalledWith("reports")
  })

  it("marks the clicked row as the selected one", async () => {
    const row = await renderAndClickReport(UNATTACHED_ROW)

    await waitFor(() => expect(row.getAttribute("data-active")).toBe("true"))
  })
})

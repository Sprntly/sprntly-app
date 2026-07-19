// @vitest-environment jsdom
//
// The right-panel Report tab hosts a chat surface's self-contained HTML report
// answer (e.g. voice-of-customer-report). The tab only exists while
// content.report is set, the document renders in the sandboxed HtmlReportView
// iframe (nothing inside it is clickable), and the REAL pipeline action — the
// bottom bar's Generate PRD — lives outside the iframe and enters the standard
// PRD generation flow (from which tickets follow).
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// ContentPanel has module-level JSX (the TABS array), so global React must exist
// before the import below evaluates. vi.hoisted runs before hoisted imports.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

// The real PrdPanelContent fetches the latest PRD on mount — stub it so these
// tests stay hermetic.
vi.mock("../PrdPanelContent", () => ({
  PrdPanelContent: () => React.createElement("div", { "data-testid": "prd-body" }),
}))

const prdGenMock = vi.hoisted(() => ({
  runPrdGeneration: vi.fn(async () => ({ ok: true, prd: { prd_id: 9, title: "P", metaLine: "", sections: [] } })),
}))
vi.mock("../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: prdGenMock.runPrdGeneration,
  loadPrdById: vi.fn(),
}))

const navMock = vi.hoisted(() => ({ openContentPanel: vi.fn() }))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    contentPanelTab: navMock.tab,
    openContentPanel: navMock.openContentPanel,
    closeContentPanel: vi.fn(),
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

const REPORT_HTML =
  '<!DOCTYPE html><html><head><title>Voice of Customer — Q2</title></head>' +
  '<body><div class="page"><h1>Voice of Customer</h1></div></body></html>'

function renderPanel(opts: {
  tab: "report" | "prd" | "evidence" | "tickets"
  report?: { html: string; title: string } | null
  prd?: unknown
  prdMeta?: { briefId: number; insightIndex: number } | null
}) {
  ;(navMock as Record<string, unknown>).tab = opts.tab
  contentMock.value = {
    report: opts.report ?? null,
    prd: opts.prd ?? null,
    prdMeta: opts.prdMeta ?? null,
    evidence: null,
    evidenceGenerating: false,
    briefDetails: {},
  }
  return render(React.createElement(ContentPanel))
}

afterEach(() => {
  cleanup()
  navMock.openContentPanel.mockClear()
  prdGenMock.runPrdGeneration.mockClear()
})

describe("ContentPanel — Report tab", () => {
  it("hides the Report tab when no report is open", () => {
    renderPanel({ tab: "prd" })
    expect(screen.queryByRole("button", { name: /Report/i })).toBeNull()
  })

  it("shows the Report tab first when a report is open", () => {
    renderPanel({ tab: "report", report: { html: REPORT_HTML, title: "Voice of Customer — Q2" } })
    const labels = Array.from(document.querySelectorAll(".cpanel-tab")).map((b) => b.textContent?.trim())
    expect(labels).toEqual(["Report", "Evidence", "PRD", "Tickets"])
  })

  it("renders the report in a sandboxed iframe with the report title in the header", () => {
    renderPanel({ tab: "report", report: { html: REPORT_HTML, title: "Voice of Customer — Q2" } })
    const iframe = document.querySelector("iframe")
    expect(iframe).not.toBeNull()
    expect(iframe!.getAttribute("srcdoc")).toBe(REPORT_HTML)
    expect(iframe!.getAttribute("sandbox")).toBe("allow-same-origin")
    expect(document.querySelector(".cpanel-main-name")?.textContent).toContain("Voice of Customer — Q2")
  })

  it("bottom bar Generate PRD is disabled without an insight anchor", () => {
    renderPanel({ tab: "report", report: { html: REPORT_HTML, title: "R" } })
    const btn = screen.getByTestId("report-footer-prd-cta") as HTMLButtonElement
    expect(btn.disabled).toBe(true)
  })

  it("bottom bar Generate PRD enters the standard PRD flow (generate + flip to PRD tab)", () => {
    renderPanel({
      tab: "report",
      report: { html: REPORT_HTML, title: "R" },
      prdMeta: { briefId: 1, insightIndex: 0 },
    })
    const btn = screen.getByTestId("report-footer-prd-cta") as HTMLButtonElement
    expect(btn.disabled).toBe(false)
    fireEvent.click(btn)
    expect(prdGenMock.runPrdGeneration).toHaveBeenCalledWith({ briefId: 1, insightIndex: 0 })
    expect(navMock.openContentPanel).toHaveBeenCalledWith("prd")
  })

  it("shows View PRD instead once a PRD is loaded", () => {
    renderPanel({
      tab: "report",
      report: { html: REPORT_HTML, title: "R" },
      prd: { prd_id: 5, title: "P", metaLine: "", sections: [] },
    })
    const btn = screen.getByRole("button", { name: "View PRD" })
    fireEvent.click(btn)
    expect(navMock.openContentPanel).toHaveBeenCalledWith("prd")
    expect(prdGenMock.runPrdGeneration).not.toHaveBeenCalled()
  })

  it("redirects to the PRD tab when parked on Report after the report is cleared", () => {
    renderPanel({ tab: "report", report: null })
    expect(navMock.openContentPanel).toHaveBeenCalledWith("prd")
    expect(screen.getByTestId("prd-body")).toBeTruthy()
  })
})

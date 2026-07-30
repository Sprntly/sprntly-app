// @vitest-environment jsdom
//
// The content panel's Reports tab: a thread's captured reports as a list → detail
// view inside the slide. Several reports list; opening one shows the document in
// place; "All reports" comes back so the next one is one click away. A thread
// with a single report opens straight into it (a one-item list says nothing), and
// Back still works there.

import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const reportGet = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>(null))

vi.mock("../../../lib/api", () => ({
  reportsApi: { get: (...a: unknown[]) => reportGet(...a), share: vi.fn(), downloadPdf: vi.fn() },
}))

// The content slice is held in REAL React state by the harness below, so a
// setContent from inside the tab (Back clearing the focus pointer) actually
// re-renders it — the way ContentProvider does in the app. A plain vi.fn mock
// would swallow that and the test would assert against a stale tree.
const contentMock = vi.hoisted(() => ({
  initial: {
    conversationId: 77, reportFocusId: null, threadReportsStatus: "ready",
  } as Record<string, unknown>,
  current: { content: {} as Record<string, unknown>, setContent: (_p: unknown) => {} },
}))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => contentMock.current,
}))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn() }),
}))

import { ReportsTab } from "../ReportsTab"
import type { ReportSummary } from "../../../lib/api"

const ROWS = [
  {
    id: 9, skill: "voice-of-customer-report", title: "VoC · Q2", question: "",
    created_at: new Date().toISOString(), conversation_id: 77, prd_id: null,
    share_mode: "private" as const,
  },
  {
    id: 8, skill: "competitive-intelligence-review", title: "Competitors · Q2", question: "",
    created_at: new Date().toISOString(), conversation_id: 77, prd_id: null,
    share_mode: "private" as const,
  },
]

const doc = (id: number, title: string) => ({
  id, skill: "voice-of-customer-report", title, question: "",
  html: `<!DOCTYPE html><html><body><h1>${title}</h1></body></html>`,
  created_at: new Date().toISOString(), conversation_id: 77, prd_id: null,
  share_mode: "private", share_token: null,
})

afterEach(() => {
  cleanup()
  contentMock.initial = {
    conversationId: 77, reportFocusId: null, threadReportsStatus: "ready",
  }
  vi.clearAllMocks()
})

/** Owns the content slice in state and republishes it on every change, so the
 *  tab sees writes it makes to context — exactly as ContentProvider does. */
function Harness({ reports }: { reports: ReportSummary[] }) {
  const [value, setValue] = React.useState(contentMock.initial)
  contentMock.current = {
    content: value,
    setContent: (patch) => setValue((v) => ({ ...v, ...(patch as object) })),
  }
  return <ReportsTab reports={reports} loading={false} />
}

async function renderTab(reports = ROWS) {
  await act(async () => { render(<Harness reports={reports} />) })
}

describe("ReportsTab — several reports in one thread", () => {
  it("lists them instead of picking one", async () => {
    await renderTab()

    expect(screen.getByTestId("reports-list")).toBeTruthy()
    expect(document.querySelectorAll("[data-report-id]").length).toBe(2)
    expect(screen.getByTestId("reports-list").textContent).toContain("VoC · Q2")
    expect(screen.getByTestId("reports-list").textContent).toContain("Competitors · Q2")
    expect(reportGet).not.toHaveBeenCalled()
  })

  it("opens the one you click, in the same slide", async () => {
    reportGet.mockResolvedValue(doc(8, "Competitors · Q2"))

    await renderTab()
    await act(async () => {
      fireEvent.click(document.querySelector('[data-report-id="8"]') as HTMLElement)
    })

    expect(reportGet).toHaveBeenCalledWith(8)
    await waitFor(() =>
      expect(screen.getByTestId("reports-detail-title").textContent).toBe("Competitors · Q2"),
    )
    const frame = document.querySelector("iframe") as HTMLIFrameElement
    expect(frame.getAttribute("srcdoc")).toContain("<h1>Competitors · Q2</h1>")
  })

  it("goes back to the list, so the next report is one click away", async () => {
    reportGet.mockResolvedValue(doc(8, "Competitors · Q2"))

    await renderTab()
    await act(async () => {
      fireEvent.click(document.querySelector('[data-report-id="8"]') as HTMLElement)
    })
    await waitFor(() => expect(screen.getByTestId("reports-detail")).toBeTruthy())

    await act(async () => { fireEvent.click(screen.getByTestId("reports-back")) })

    expect(screen.getByTestId("reports-list")).toBeTruthy()
    expect(document.querySelectorAll("[data-report-id]").length).toBe(2)

    // …and the other one opens from there.
    reportGet.mockResolvedValue(doc(9, "VoC · Q2"))
    await act(async () => {
      fireEvent.click(document.querySelector('[data-report-id="9"]') as HTMLElement)
    })
    await waitFor(() =>
      expect(screen.getByTestId("reports-detail-title").textContent).toBe("VoC · Q2"),
    )
  })
})

describe("ReportsTab — arriving on one specific report", () => {
  it("opens the report the user clicked rather than the list", async () => {
    contentMock.initial = { conversationId: 77, reportFocusId: 9, threadReportsStatus: "ready" }
    reportGet.mockResolvedValue(doc(9, "VoC · Q2"))

    await renderTab()

    expect(reportGet).toHaveBeenCalledWith(9)
    await waitFor(() =>
      expect(screen.getByTestId("reports-detail-title").textContent).toBe("VoC · Q2"),
    )
  })

  it("acts on the focus ONCE, so Back still reaches the list", async () => {
    // The focus value has to survive (it's also what keeps the tab on a
    // standalone report), so re-selecting has to be guarded by more than
    // clearing it — otherwise Back is yanked straight back into the report.
    contentMock.initial = { conversationId: 77, reportFocusId: 9, threadReportsStatus: "ready" }
    reportGet.mockResolvedValue(doc(9, "VoC · Q2"))

    await renderTab()
    await waitFor(() => expect(screen.getByTestId("reports-detail")).toBeTruthy())

    await act(async () => { fireEvent.click(screen.getByTestId("reports-back")) })
    expect(screen.getByTestId("reports-list")).toBeTruthy()
  })

  it("ignores a focus belonging to a DIFFERENT thread", async () => {
    // The panel is global, so a leftover id must never open one thread's report
    // inside another's.
    contentMock.initial = { conversationId: 77, reportFocusId: 4242, threadReportsStatus: "ready" }

    await renderTab()

    expect(reportGet).not.toHaveBeenCalled()
    expect(screen.getByTestId("reports-list")).toBeTruthy()
  })

  it("honours a focus with no thread behind it — a standalone report", async () => {
    // Opened from Artifacts: no conversation, so no list to check against. The
    // document itself is the whole tab.
    contentMock.initial = { conversationId: null, reportFocusId: 9, threadReportsStatus: "idle" }
    reportGet.mockResolvedValue(doc(9, "VoC · Q2"))

    await renderTab([])

    expect(reportGet).toHaveBeenCalledWith(9)
    await waitFor(() =>
      expect(screen.getByTestId("reports-detail-title").textContent).toBe("VoC · Q2"),
    )
    expect(screen.queryByTestId("reports-back")).toBeNull()
  })
})

describe("ReportsTab — a thread with one report", () => {
  it("IS the report: opens it directly, with no list framing and no back button", async () => {
    reportGet.mockResolvedValue(doc(9, "VoC · Q2"))

    await renderTab([ROWS[0]])

    await waitFor(() =>
      expect(screen.getByTestId("reports-detail-title").textContent).toBe("VoC · Q2"),
    )
    // A "All reports" button leading to a one-item list shows the reader nothing.
    expect(screen.queryByTestId("reports-back")).toBeNull()
  })
})

describe("ReportsTab — nothing to show", () => {
  it("says the chat has no reports", async () => {
    await renderTab([])
    expect(screen.getByTestId("reports-list").textContent).toContain("No reports in this chat")
  })

  it("distinguishes a failed load from an empty thread", async () => {
    await act(async () => {
      render(<ReportsTab reports={[]} loading={false} error />)
    })
    expect(screen.getByTestId("reports-list-error")).toBeTruthy()
  })

  it("says so when the document itself fails to load", async () => {
    reportGet.mockRejectedValue(new Error("boom"))

    await renderTab()
    await act(async () => {
      fireEvent.click(document.querySelector('[data-report-id="8"]') as HTMLElement)
    })

    await waitFor(() => expect(screen.getByTestId("reports-detail-error")).toBeTruthy())
  })
})

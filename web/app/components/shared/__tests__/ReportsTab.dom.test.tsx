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
    //
    // Said with a FLAG now, not inferred from the null conversation id —
    // ArtifactsScreen sets `reportFocusStandalone` on this one path. The
    // behaviour under test is unchanged; what changed is that a null
    // conversation id no longer means "standalone" by itself (see the test
    // below for why that mattered).
    contentMock.initial = {
      conversationId: null, reportFocusId: 9, reportFocusStandalone: true,
      threadReportsStatus: "idle",
    }
    reportGet.mockResolvedValue(doc(9, "VoC · Q2"))

    await renderTab([])

    expect(reportGet).toHaveBeenCalledWith(9)
    await waitFor(() =>
      expect(screen.getByTestId("reports-detail-title").textContent).toBe("VoC · Q2"),
    )
    expect(screen.queryByTestId("reports-back")).toBeNull()
  })

  it("ignores a focus on a NEW chat that merely has no conversation yet", async () => {
    // The worse half of the reported bug. A brand-new chat tab has a null
    // conversation id too — a tab gains one only when its first ask persists —
    // so a pointer left over from the thread before it used to read as
    // "standalone, trust it", and the PREVIOUS thread's whole document rendered
    // inside the empty new chat. Without the standalone flag, a null
    // conversation is a thread that cannot vouch for anything.
    contentMock.initial = { conversationId: null, reportFocusId: 9, threadReportsStatus: "idle" }
    reportGet.mockResolvedValue(doc(9, "VoC · Q2"))

    await renderTab([])

    expect(reportGet).not.toHaveBeenCalled()
    expect(screen.queryByTestId("reports-detail")).toBeNull()
    expect(document.querySelector("iframe")).toBeNull()
    expect(screen.getByTestId("reports-list")).toBeTruthy()
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

describe("ReportsTab — a report renders as the rich document it is", () => {
  // Reports are HTML now: captured as HTML, and the rows written before that
  // converted on the way out of the API (`app/report_markdown.py`). The panel
  // renders one through the SAME editor a team document uses — that is what
  // makes "edit the report in the panel" the same gesture on both.
  const htmlDoc = (id: number, title: string, html: string) => ({
    id, skill: "saved-chat", title, question: "",
    html,
    created_at: new Date().toISOString(), conversation_id: 77, prd_id: null,
    share_mode: "private", share_token: null,
  })

  it("renders the document, not the sandboxed iframe", async () => {
    reportGet.mockResolvedValue(
      htmlDoc(8, "Competitors · Q2", "<h1>Prioritization</h1><p><strong>Ship A</strong> first</p>"),
    )

    await renderTab()
    await act(async () => {
      fireEvent.click(document.querySelector('[data-report-id="8"]') as HTMLElement)
    })

    await waitFor(() => expect(screen.getByTestId("report-document")).toBeTruthy())
    // The iframe path is reserved for a legacy self-contained document, which
    // owns its own <head> and <style>.
    expect(document.querySelector("iframe")).toBeNull()
  })

  it("still routes a skill-template HTML report to the sandboxed iframe, unchanged", async () => {
    // A non-"saved-chat" report (e.g. voice-of-customer-report) is untouched:
    // `doc()` above already builds one with a real `<!DOCTYPE html>` body.
    reportGet.mockResolvedValue(doc(9, "VoC · Q2"))

    await renderTab([ROWS[0]])

    await waitFor(() => {
      const frame = document.querySelector("iframe") as HTMLIFrameElement | null
      expect(frame).toBeTruthy()
      expect(frame?.getAttribute("srcdoc")).toContain("<h1>VoC · Q2</h1>")
    })
    expect(screen.queryByTestId("saved-chat-markdown")).toBeNull()
  })
})


describe("ReportsTab — a scheduled monthly report", () => {
  it("renders as a document: the API serves HTML whatever is stored", async () => {
    // A scheduled monthly run saves the report skill's answer, and the read
    // route converts it — so what reaches this panel is the same HTML shape
    // every other report arrives in, editable in the same editor.
    reportGet.mockResolvedValue({
      id: 8, skill: "competitive-intelligence-review",
      title: "Competitive Intelligence report · June 2026", question: "",
      html: "<h2>Competitive review</h2><p><strong>Acme</strong> shipped X</p>",
      created_at: new Date().toISOString(), conversation_id: 77, prd_id: null,
      share_mode: "private", share_token: null,
    })

    await renderTab()
    await act(async () => {
      fireEvent.click(document.querySelector('[data-report-id="8"]') as HTMLElement)
    })

    await waitFor(() => expect(screen.getByTestId("report-document")).toBeTruthy())
    expect(document.querySelector("iframe")).toBeNull()
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

  it("says a selected report is unavailable instead of 'REPORT REPORT'", async () => {
    // A selection that resolves to neither a document nor a list row rendered a
    // titled, empty frame under an eyebrow reading "REPORT REPORT" — the
    // uppercased `${reportKindLabel(null)} report`, where reportKindLabel(null)
    // is itself "Report". Every render branch below it was false, so the body
    // was blank.
    contentMock.initial = {
      conversationId: null, reportFocusId: 9, reportFocusStandalone: true,
      threadReportsStatus: "idle",
    }
    reportGet.mockResolvedValue(null)

    await renderTab([])

    await waitFor(() => expect(screen.getByTestId("reports-detail-empty")).toBeTruthy())
    const detail = screen.getByTestId("reports-detail")
    expect(detail.textContent).toContain("This report isn't available")
    expect(detail.textContent).not.toContain("Report report")
    expect(document.querySelector("iframe")).toBeNull()
  })

  it("does not flash the empty state on the way into a report", async () => {
    // The detail's loading flag is set from an EFFECT, so it is still false on
    // the first render after a selection. An empty state derived from "no doc and
    // not loading" alone would therefore blink "This report isn't available"
    // before every single open — which is why it waits on the fetch settling for
    // this exact selection instead.
    let release: (v: unknown) => void = () => {}
    reportGet.mockReturnValue(new Promise<unknown>((res) => { release = res }))
    contentMock.initial = { conversationId: 77, reportFocusId: 9, threadReportsStatus: "ready" }

    await renderTab([ROWS[0]])

    // Mid-flight: the skeleton, never the terminal empty state.
    expect(screen.queryByTestId("reports-detail-empty")).toBeNull()
    expect(screen.getByTestId("reports-loading")).toBeTruthy()

    await act(async () => { release(doc(9, "VoC · Q2")) })
    await waitFor(() =>
      expect(screen.getByTestId("reports-detail-title").textContent).toBe("VoC · Q2"),
    )
    expect(screen.queryByTestId("reports-detail-empty")).toBeNull()
  })
})

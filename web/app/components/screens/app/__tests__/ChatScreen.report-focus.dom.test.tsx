// @vitest-environment jsdom
//
// ChatScreen — a report opened from Artifacts lands in its OWN chat thread.
//
// Clicking a report in the library writes the ordinary `sprntly_resume_conv`
// hand-off and fills `pendingReportFocus` (openReportTab). ChatScreen must then:
//   • resume that conversation as a tab, and
//   • open the content panel on its Reports tab, focused on that document.
//
// The trap this guards: resuming a chat IS a tab switch, and the panel↔tab
// reconcile closes the panel for any tab that carries no PRD — which is most
// report threads. Without the suppression, the panel opened and was shut in the
// same commit, so the click looked like it did nothing.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined") window.scrollTo = (() => {}) as typeof window.scrollTo
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const listTurns = vi.fn().mockResolvedValue({ turns: [] })
const listForConversation = vi.fn((..._a: unknown[]) => Promise.resolve<unknown[]>([]))

vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
      update: vi.fn().mockResolvedValue({}),
      byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
      listTurns: (...a: unknown[]) => listTurns(...a),
    },
    reportsApi: {
      listForConversation: (...a: unknown[]) => listForConversation(...a),
      get: vi.fn(),
    },
    prdApi: { importDoc: vi.fn() },
  }
})

const REPORT_ROW = {
  id: 9, skill: "voice-of-customer-report", title: "VoC · Q2", question: "",
  created_at: new Date().toISOString(), conversation_id: 77, prd_id: null,
  share_mode: "private" as const,
}

/** A stored report and the chat answer that produced it. The stored title comes
 *  from the document's <title>, which is what report_capture.report_title reads
 *  first — the client must derive the same string or a card can never find its
 *  own report. */
const STORED_TITLE = "Voice of Customer Report — 30 July 2026 · 1 day"
const REPORT_HTML =
  `<!DOCTYPE html><html><head><title>${STORED_TITLE}</title></head>` +
  "<body><h1>Voice of Customer Report</h1></body></html>"

const loadPrdById = vi.fn((id: number) =>
  Promise.resolve({ ok: true, prd: { prd_id: id, title: `PRD ${id}`, metaLine: "", sections: [] } }),
)
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn(),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(),
  loadPrdById: (id: number) => loadPrdById(id),
}))

vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn(() => null),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({ runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn() }),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(""),
}))

vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({ loading: false, profile: null, workspace: null, refresh: async () => {} }),
}))

vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))

vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: new Map(), loading: false, refetch: vi.fn() }),
}))

import { NavigationProvider, useNavigation } from "../../../../context/NavigationContext"
import { ContentProvider, useContent } from "../../../../context/ContentContext"
import { useThreadReportsSync } from "../../../shared/useThreadReports"
import { ChatScreen } from "../ChatScreen"

/** Drives the hand-off the way ArtifactsScreen does, and probes what landed.
 *  Calls the reports sync hook where AppShell would, so ChatScreen sees the
 *  thread's reports exactly as it does in the app. */
function Harness({ focus }: { focus?: { conversationId: number; reportId: number } }) {
  const { contentPanelTab, openReportTab, closeContentPanel } = useNavigation()
  const { content } = useContent()
  useThreadReportsSync()
  const fired = React.useRef(false)
  React.useEffect(() => {
    if (!focus || fired.current) return
    fired.current = true
    openReportTab(focus)
  }, [focus, openReportTab])
  return React.createElement(
    React.Fragment,
    null,
    React.createElement("div", { "data-testid": "panel-probe" }, contentPanelTab ?? "none"),
    React.createElement("div", { "data-testid": "focus-probe" },
      content.reportFocusId != null ? String(content.reportFocusId) : "none"),
    React.createElement("div", { "data-testid": "conv-probe" },
      content.conversationId != null ? String(content.conversationId) : "none"),
    // The real ContentPanel isn't mounted here (it's a route-level overlay), so
    // expose its close — the same closeContentPanel its × and overlay call.
    React.createElement("button", { "data-testid": "close-panel", onClick: closeContentPanel }, "close"),
    React.createElement(ChatScreen),
  )
}

function mountApp(focus?: { conversationId: number; reportId: number }) {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(Harness, { focus })),
    ),
  )
}

const panelProbe = () => screen.getByTestId("panel-probe").textContent
const focusProbe = () => screen.getByTestId("focus-probe").textContent
const convProbe = () => screen.getByTestId("conv-probe").textContent

/** What ArtifactsScreen writes before handing off. */
function seedResume(dbId: number, title: string) {
  localStorage.setItem("sprntly_resume_conv", JSON.stringify({
    dbId, title, fallbackTurns: [], prdId: null,
  }))
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  vi.clearAllMocks()
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("ChatScreen — opening a report in its own thread", () => {
  it("resumes the report's chat and opens the panel on its Reports tab", async () => {
    seedResume(77, "Q2 customer themes")

    await act(async () => { mountApp({ conversationId: 77, reportId: 4 }) })

    await waitFor(() => expect(panelProbe()).toBe("reports"))
    // …focused on the document the user actually clicked.
    await waitFor(() => expect(focusProbe()).toBe("4"))
    // …and the panel knows which thread it is showing, so the tab lists that
    // conversation's reports.
    await waitFor(() => expect(convProbe()).toBe("77"))
    // A report thread is not a PRD flow — nothing may be generated or loaded.
    expect(loadPrdById).not.toHaveBeenCalled()
  })

  it("opens the resumed chat as a tab and hydrates its turns", async () => {
    seedResume(77, "Q2 customer themes")

    await act(async () => { mountApp({ conversationId: 77, reportId: 4 }) })

    await waitFor(() => expect(listTurns).toHaveBeenCalledWith(77))
    await waitFor(() =>
      expect(document.body.textContent).toContain("Q2 customer themes"),
    )
  })

  it("never opens the panel over a DIFFERENT thread than the report's", async () => {
    // The resume lands on conversation 5, but the focus is for 77 — the ids
    // disagree, so nothing opens rather than showing a thread's reports under
    // another thread's chat.
    seedResume(5, "Some other chat")

    await act(async () => { mountApp({ conversationId: 77, reportId: 4 }) })
    await act(async () => { await Promise.resolve() })

    expect(panelProbe()).toBe("none")
    expect(focusProbe()).toBe("none")
  })

  it("leaves an ordinary resumed chat alone — no panel, no focus", async () => {
    // No hand-off and no reports: the plain history-resume path must be
    // untouched by any of this.
    seedResume(7, "Just a chat")

    await act(async () => { mountApp() })
    await act(async () => { await Promise.resolve() })

    expect(panelProbe()).toBe("none")
    expect(focusProbe()).toBe("none")
  })
})

// Opening a chat should SHOW what that chat produced — the panel used to come
// back shut on a report thread, with no sign the document existed.
describe("ChatScreen — opening a thread that has reports", () => {
  it("pulls its reports and slides in showing the LATEST one", async () => {
    // Newest first, so [0] is the report that was just written. Landing on a
    // list would make the reader hunt for what they came back for; the list is
    // what "All reports" is for.
    listForConversation.mockResolvedValue([REPORT_ROW, { ...REPORT_ROW, id: 3, title: "Older" }])
    seedResume(77, "Q2 customer themes")

    await act(async () => { mountApp() })

    await waitFor(() => expect(listForConversation).toHaveBeenCalledWith(77))
    await waitFor(() => expect(panelProbe()).toBe("reports"))
    await waitFor(() => expect(focusProbe()).toBe("9"))
  })

  it("leaves the PRD as the active tab when the thread has one", async () => {
    // The explicit ordering: a PRD is what the chat is about, so it owns the
    // panel and Reports sits one click away in the tab bar.
    listForConversation.mockResolvedValue([REPORT_ROW])
    localStorage.setItem("sprntly_resume_conv", JSON.stringify({
      dbId: 77, title: "Cart prefill PRD", fallbackTurns: [], prdId: 88,
    }))

    await act(async () => { mountApp() })

    await waitFor(() => expect(loadPrdById).toHaveBeenCalledWith(88))
    await waitFor(() => expect(panelProbe()).toBe("prd"))
    // …and it stays there — the reports landing must not steal the panel.
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    expect(panelProbe()).toBe("prd")
  })

  it("does not open the panel for a thread with no reports", async () => {
    listForConversation.mockResolvedValue([])
    seedResume(7, "Just a chat")

    await act(async () => { mountApp() })
    await waitFor(() => expect(listForConversation).toHaveBeenCalledWith(7))
    await act(async () => { await Promise.resolve() })

    expect(panelProbe()).toBe("none")
  })
})

// Closing the panel must not be one-way for a report thread either: the tab
// strip's reopen button — the same one that brings a PRD back — carries reports.
describe("ChatScreen — the tab strip's reopen button on a report thread", () => {
  const reopenBtn = () => screen.queryByTestId("chat-reopen-artifact")

  it("brings the panel back on the latest report after the user closes it", async () => {
    listForConversation.mockResolvedValue([REPORT_ROW, { ...REPORT_ROW, id: 3, title: "Older" }])
    seedResume(77, "Q2 customer themes")

    await act(async () => { mountApp({ conversationId: 77, reportId: 9 }) })
    await waitFor(() => expect(panelProbe()).toBe("reports"))
    // While the panel is open the button stays out of the way.
    expect(reopenBtn()).toBeNull()

    await act(async () => { fireEvent.click(screen.getByTestId("close-panel")) })
    await act(async () => { await Promise.resolve() })
    expect(panelProbe()).toBe("none")

    const btn = reopenBtn()
    expect(btn).not.toBeNull()
    expect(btn!.getAttribute("aria-label")).toBe("View reports")

    await act(async () => { fireEvent.click(btn!) })
    await waitFor(() => expect(panelProbe()).toBe("reports"))
    await waitFor(() => expect(focusProbe()).toBe("9"))
  })

  it("says 'View report' when the thread has exactly one", async () => {
    listForConversation.mockResolvedValue([REPORT_ROW])
    seedResume(77, "Q2 customer themes")

    await act(async () => { mountApp() })
    await waitFor(() => expect(panelProbe()).toBe("reports"))
    await act(async () => { fireEvent.click(screen.getByTestId("close-panel")) })

    await waitFor(() => expect(reopenBtn()).not.toBeNull())
    expect(reopenBtn()!.getAttribute("aria-label")).toBe("View report")
  })

  it("opens THAT report when its card in the thread is clicked, not the list", async () => {
    // The reported bug: with two reports in a thread, clicking a specific card
    // opened the Reports tab on the LIST — the user had already chosen a
    // document and was made to choose again.
    const rows = [
      { ...REPORT_ROW, id: 9, title: STORED_TITLE },
      { ...REPORT_ROW, id: 3, title: "Voice of Customer Report — 29 July 2026 · 2 days" },
    ]
    listForConversation.mockResolvedValue(rows)
    // A resumed thread whose turn IS a report answer.
    localStorage.setItem("sprntly_resume_conv", JSON.stringify({
      dbId: 77, title: "Q2 customer themes", fallbackTurns: [], prdId: null,
    }))
    listTurns.mockResolvedValue({
      turns: [
        { role: "user", content: "/voice-of-customer-report what are customers saying" },
        { role: "assistant", content: REPORT_HTML },
      ],
    })

    await act(async () => { mountApp() })
    await waitFor(() => expect(listForConversation).toHaveBeenCalledWith(77))

    // Close whatever auto-opened so the click is what does the work.
    await act(async () => { fireEvent.click(screen.getByTestId("close-panel")) })

    const card = await waitFor(() => screen.getByTestId("open-report-btn"))
    await act(async () => { fireEvent.click(card) })

    await waitFor(() => expect(panelProbe()).toBe("reports"))
    // Focused on the card's OWN report (id 9), resolved from its title.
    await waitFor(() => expect(focusProbe()).toBe("9"))
  })

  it("shows no button on a thread with no artifact at all", async () => {
    listForConversation.mockResolvedValue([])
    seedResume(7, "Just a chat")

    await act(async () => { mountApp() })
    await waitFor(() => expect(listForConversation).toHaveBeenCalledWith(7))
    await act(async () => { await Promise.resolve() })

    expect(reopenBtn()).toBeNull()
  })
})

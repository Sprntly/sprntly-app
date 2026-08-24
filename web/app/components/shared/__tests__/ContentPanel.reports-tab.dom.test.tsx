// @vitest-environment jsdom
//
// The right-panel Reports tab appears only for threads that actually have a
// report. It is not part of the PRD pipeline (Evidence → PRD → Tickets) — a
// report hangs off the CHAT, not the PRD — so an always-present, usually-empty
// tab would be clutter on every chat that never asked for one.
//
// These lock the tab bar: when it shows, when it doesn't, and that it lists the
// thread's reports rather than anything PRD-scoped.
import * as React from "react"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
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

// The real PrdPanelContent fetches on mount — stub it so the tab-bar test stays
// hermetic. Only the header tabs and the Reports body are under test.
vi.mock("../PrdPanelContent", () => ({
  PrdPanelContent: () => React.createElement("div", { "data-testid": "prd-body" }),
}))

const reportGet = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>(null))
vi.mock("../../../lib/api", () => ({
  ApiError: class ApiError extends Error { status = 0 },
  storiesApi: { getForPrd: vi.fn().mockResolvedValue({ stories: [] }) },
  reportsApi: {
    listForConversation: vi.fn(),
    get: (...a: unknown[]) => reportGet(...a),
    share: vi.fn(),
    downloadPdf: vi.fn(),
  },
}))

const navMock = vi.hoisted(() => ({ openContentPanel: vi.fn(), tab: "prd" as string | null }))
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

const ROW = {
  id: 9, skill: "voice-of-customer-report", title: "VoC · Q2", question: "",
  created_at: new Date().toISOString(), conversation_id: 77, prd_id: null,
  share_mode: "private" as const,
}

/** The panel is a pure consumer of the list — useThreadReportsSync (AppShell)
 *  owns the fetch — so these render against content directly. */
async function renderPanel(opts: {
  tab?: string
  reports?: unknown[]
  status?: "idle" | "loading" | "ready" | "error"
  /** A PRD in scope puts the Evidence → PRD → Tickets pipeline in scope too. */
  prd?: unknown
  /** A report is being written for this thread right now. */
  generating?: boolean
  /** The streamed draft so far. */
  partial?: string | null
  /** Which conversation the rows were FETCHED for. Defaults to the thread the
   *  panel is showing — the normal case, once useThreadReportsSync has answered
   *  for it. Pass a different id to stage the commit where the chat has already
   *  switched threads and the shared list has not caught up. */
  listFor?: number | null
}) {
  navMock.tab = opts.tab ?? "prd"
  contentMock.value = {
    // Default: a report-only thread — no PRD, no insight, no evidence.
    prd: opts.prd ?? null,
    evidence: null,
    evidenceGenerating: false,
    prdMeta: null,
    detail: null,
    conversationId: 77,
    reportFocusId: null,
    reportFocusStandalone: false,
    reportGenerating: opts.generating ?? false,
    reportPartialMd: opts.partial ?? null,
    threadReports: opts.reports ?? [],
    threadReportsStatus: opts.status ?? "ready",
    threadReportsConversationId: opts.listFor === undefined ? 77 : opts.listFor,
    // Read by TicketsTab, which renders when the panel is parked on Tickets.
    connectedConnectorIds: [],
  }
  await act(async () => { render(React.createElement(ContentPanel)) })
}

const PRD = { prd_id: 1, title: "T", metaLine: "", sections: [], source: "brief" }

/** Scoped to the tab BAR — the detail view's "All reports" back button would
 *  otherwise match too. */
const tabLabels = () =>
  Array.from(document.querySelectorAll(".cpanel-tab")).map((b) => b.textContent?.trim())
const reportsTab = () => tabLabels().find((t) => t === "Reports") ?? null

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("ContentPanel — the Reports tab", () => {
  it("shows the tab once the thread has a report", async () => {
    await renderPanel({ reports: [ROW] })
    expect(reportsTab()).toBeTruthy()
  })

  it("hides it for a chat that never asked for one", async () => {
    await renderPanel({ reports: [], status: "ready", prd: PRD })

    expect(reportsTab()).toBeNull()
    // The pipeline tabs are untouched for a chat that HAS a PRD.
    expect(tabLabels()).toContain("PRD")
    expect(tabLabels()).toContain("Tickets")
  })

  it("does not flash the tab while the list is still loading", async () => {
    await renderPanel({ reports: [], status: "loading" })
    expect(reportsTab()).toBeNull()
  })

  it("keeps the tab when the list FAILED — an error is not an empty thread", async () => {
    // The reported bug: a failed fetch left an empty list, which hid the tab, so
    // switching to another tab made the report the user was reading vanish from
    // the panel with no way back to it. Asserted from a NON-reports tab, which
    // is the case that used to drop it.
    await renderPanel({ tab: "prd", reports: [], status: "error" })
    expect(reportsTab()).toBeTruthy()
  })

  it("stays visible while it is the active tab, before the list lands", async () => {
    // Arriving from an Artifacts row opens straight onto this tab — it must not
    // be hidden in the window before the list resolves.
    await renderPanel({ tab: "reports", reports: [], status: "loading" })
    expect(reportsTab()).toBeTruthy()
  })

  // THE RULE: a tab exists only when the thread actually has that artifact.
  it("shows ONLY Reports on a thread with no PRD, evidence or tickets", async () => {
    // The reported bug: a report-only chat still offered Evidence / PRD /
    // Tickets, advertising three documents that don't exist and can't be made
    // from there.
    await renderPanel({ tab: "reports", reports: [ROW] })

    expect(tabLabels()).toEqual(["Reports"])
  })

  it("brings the pipeline back as soon as a PRD is in scope", async () => {
    await renderPanel({ tab: "reports", reports: [ROW], prd: PRD })

    expect(tabLabels()).toEqual(["Evidence", "PRD", "Tickets", "Reports"])
  })

  it("shows no tab for a list fetched for a DIFFERENT thread", async () => {
    // The list is fetched by AppShell (the parent of the chat screen), so on the
    // commit where the chat switches threads it still holds the previous one's
    // rows. Advertising a Reports tab off those rows put another thread's
    // documents one click away from this one.
    await renderPanel({ reports: [ROW], listFor: 4242 })
    expect(reportsTab()).toBeNull()
  })

  it("does not list another thread's reports in the body", async () => {
    // …and if the panel is parked ON the Reports tab through that window, it
    // reads as still loading rather than listing rows that belong elsewhere.
    // "No reports in this chat" would be just as wrong: nobody has answered for
    // this chat yet.
    await renderPanel({ tab: "reports", reports: [ROW], listFor: 4242 })

    expect(document.querySelectorAll("[data-report-id]").length).toBe(0)
    expect(document.body.textContent).not.toContain("VoC · Q2")
    expect(document.body.textContent).not.toContain("No reports in this chat")
    expect(screen.getByTestId("reports-loading")).toBeTruthy()
  })

  it("renders the thread's reports in the body, headed 'Reports'", async () => {
    await renderPanel({
      tab: "reports",
      reports: [ROW, { ...ROW, id: 8, title: "Competitors · Q2" }],
    })

    await waitFor(() => expect(screen.getByTestId("reports-list")).toBeTruthy())
    expect(document.querySelectorAll("[data-report-id]").length).toBe(2)
    // The header names what the panel is showing — not the PRD, which a
    // report-only thread may not even have.
    expect(document.querySelector(".cpanel-main-name")?.textContent).toBe("Reports")
  })
})

// ── A report generates in the panel, not in the chat ────────────────────────
// A report is an artifact, so it is written where artifacts are written — the
// same posture the PRD build takes. The tab has to EXIST for that whole window,
// which is the whole generation: capture is a post-terminal server step, so
// there is no row to key the tab on until the report has already landed.
describe("ContentPanel — a report being written", () => {
  it("shows the Reports tab while one is generating, with no row yet", async () => {
    await renderPanel({ generating: true })
    expect(reportsTab()).toBeTruthy()
  })

  it("shows the working state on that tab before the first delta", async () => {
    await renderPanel({ tab: "reports", generating: true })
    expect(screen.getByTestId("reports-generating-pane")).toBeTruthy()
  })

  it("renders the draft as it streams, in the panel", async () => {
    await renderPanel({
      tab: "reports",
      generating: true,
      partial: "# Voice of customer\n\nOnboarding friction leads the window.",
    })
    expect(screen.getByTestId("reports-streaming")).toBeTruthy()
    expect(screen.getByTestId("reports-streaming-preview").textContent)
      .toContain("Onboarding friction")
  })

  it("goes back to the thread's reports once it lands", async () => {
    await renderPanel({ tab: "reports", reports: [ROW], generating: false })
    expect(document.querySelector("[data-testid='reports-generating']")).toBeNull()
  })
})

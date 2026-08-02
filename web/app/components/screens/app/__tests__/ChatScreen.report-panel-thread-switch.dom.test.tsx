// @vitest-environment jsdom
//
// ChatScreen — the content panel follows the thread you are ACTUALLY on.
//
// The reported bug: open a chat that produced a report, hit "+" for a new chat,
// and the panel slid open on the brand-new tab showing the PREVIOUS thread's
// report. Worse, with the panel closed on the old tab first, the old thread's
// whole document rendered inside the empty new chat.
//
// Two things caused it and both are exercised here:
//   • the reports list lives in shared content and is fetched by AppShell — our
//     PARENT — so on the commit where the tab changes, React has already run
//     ChatScreen's effects and NOT yet run the fetcher's. The auto-open read the
//     previous thread's rows as if they were this tab's.
//   • `content.reportFocusId` was never cleared on a thread change, so the
//     pointer at one thread's report outlived it.
//
// The auto-open itself is WANTED — a thread that produced a report should show
// it. So the positive case (switching BACK to the report thread opens it again)
// is asserted just as hard as the negative one; a fix that stopped the panel
// opening at all would trade a visible bug for an invisible one.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
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

const listTurns = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>({ turns: [] }))
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

const REPORT_ROW = {
  id: 9, skill: "voice-of-customer-report", title: "VoC · Q2", question: "",
  created_at: new Date().toISOString(), conversation_id: 77, prd_id: null,
  share_mode: "private" as const,
}

const TAB_A_TITLE = "Q2 customer themes"

/** The reports sync hook is called HERE, where AppShell calls it: from the
 *  PARENT of ChatScreen. That ordering is the bug — React flushes a child's
 *  effects before its parent's — so a harness that called it inside ChatScreen,
 *  or not at all, could not reproduce this. */
function Harness() {
  const { contentPanelTab, closeContentPanel } = useNavigation()
  const { content } = useContent()
  useThreadReportsSync()
  return React.createElement(
    React.Fragment,
    null,
    React.createElement("div", { "data-testid": "panel-probe" }, contentPanelTab ?? "none"),
    React.createElement("div", { "data-testid": "focus-probe" },
      content.reportFocusId != null ? String(content.reportFocusId) : "none"),
    React.createElement("div", { "data-testid": "conv-probe" },
      content.conversationId != null ? String(content.conversationId) : "none"),
    // What the panel would list, and which thread those rows were fetched FOR.
    React.createElement("div", { "data-testid": "list-probe" },
      `${content.threadReportsConversationId ?? "none"}:${(content.threadReports ?? []).length}`),
    // The real ContentPanel is a route-level overlay and isn't mounted here, so
    // expose the same close its × and overlay call.
    React.createElement("button", { "data-testid": "close-panel", onClick: closeContentPanel }, "close"),
    React.createElement(ChatScreen),
  )
}

function mountApp() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(Harness)),
    ),
  )
}

const panelProbe = () => screen.getByTestId("panel-probe").textContent
const focusProbe = () => screen.getByTestId("focus-probe").textContent
const convProbe = () => screen.getByTestId("conv-probe").textContent
const listProbe = () => screen.getByTestId("list-probe").textContent
const reopenBtn = () => screen.queryByTestId("chat-reopen-artifact")
// Scoped to the tab strip — the sidebar rail carries a "New chat" of its own.
const newChatBtn = () =>
  within(screen.getByTestId("chat-tab-bar")).getByLabelText("New chat")
const tabChip = (title: string) =>
  within(screen.getByTestId("chat-tab-bar")).getByText(title)

/** What ChatsScreen / Artifacts write before handing a chat over. */
function seedResume(dbId: number, title: string) {
  localStorage.setItem("sprntly_resume_conv", JSON.stringify({
    dbId, title, fallbackTurns: [], prdId: null,
  }))
}

/** Tab A must carry real turns: `startNewThread` prunes tabs with an empty
 *  thread as disposable, and a resumed chat that got pruned would make the
 *  switch-back assertions vacuous. */
function seedThreadTurns() {
  listTurns.mockResolvedValue({
    turns: [
      { role: "user", content: "what are customers saying about checkout" },
      { role: "assistant", content: "Here is what the last quarter looks like." },
    ],
  })
}

/** Open the report thread and wait for its panel to land. */
async function openReportThread() {
  seedThreadTurns()
  listForConversation.mockResolvedValue([REPORT_ROW])
  seedResume(77, TAB_A_TITLE)

  await act(async () => { mountApp() })
  await waitFor(() => expect(listForConversation).toHaveBeenCalledWith(77))
  await waitFor(() => expect(panelProbe()).toBe("reports"))
  await waitFor(() => expect(focusProbe()).toBe("9"))
}

/** Let every pending effect + microtask settle, so "nothing opened" is a
 *  conclusion rather than a race we won. */
async function settle() {
  await act(async () => { await new Promise((r) => setTimeout(r, 30)) })
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

describe("ChatScreen — a brand-new tab beside a report thread", () => {
  it("opens no panel and carries no focus from the thread before it", async () => {
    await openReportThread()

    await act(async () => { fireEvent.click(newChatBtn()) })
    await settle()

    // The reported screenshot: a fresh chat with another thread's report slid in
    // beside it.
    expect(panelProbe()).toBe("none")
    // …and no pointer left aimed at that report, which is what would re-open it
    // the moment anything else opened the panel.
    expect(focusProbe()).toBe("none")
    // A tab has no conversation until its first ask persists.
    expect(convProbe()).toBe("none")
    // The list is cleared with the thread, not carried into the new one.
    expect(listProbe()).toBe("none:0")
  })

  it("opens no panel when the previous thread's panel was CLOSED first", async () => {
    // The worse variant, and the one that actually reproduces: with the panel
    // shut, the auto-open's "something is already open — don't hijack it" bail
    // does not fire, so it ran on the stale list and ReportsTab then mounted
    // fresh and fetched the PREVIOUS thread's document into the empty chat.
    await openReportThread()

    await act(async () => { fireEvent.click(screen.getByTestId("close-panel")) })
    await settle()
    expect(panelProbe()).toBe("none")

    await act(async () => { fireEvent.click(newChatBtn()) })
    await settle()

    expect(panelProbe()).toBe("none")
    expect(focusProbe()).toBe("none")
    expect(listProbe()).toBe("none:0")
  })

  it("offers no 'View report' on the new tab", async () => {
    // The tab strip's reopen button read the same shared list, so a brand-new
    // chat advertised a document it does not have.
    await openReportThread()

    await act(async () => { fireEvent.click(screen.getByTestId("close-panel")) })
    await waitFor(() => expect(reopenBtn()).not.toBeNull())
    expect(reopenBtn()!.getAttribute("aria-label")).toBe("View report")

    await act(async () => { fireEvent.click(newChatBtn()) })
    await settle()

    expect(reopenBtn()).toBeNull()
  })
})

describe("ChatScreen — coming back to a thread that has a report", () => {
  it("opens its report again on the way back, not only on cold mount", async () => {
    // THE POSITIVE CASE. The auto-open is the feature; the bug was only ever
    // WHICH thread it fired for. A fix that made a report-less tab safe by
    // making the auto-open fire once per session would leave this thread's
    // document hidden behind a button — worse, because nothing would look wrong.
    await openReportThread()

    await act(async () => { fireEvent.click(newChatBtn()) })
    await settle()
    expect(panelProbe()).toBe("none")

    await act(async () => { fireEvent.click(tabChip(TAB_A_TITLE)) })

    await waitFor(() => expect(panelProbe()).toBe("reports"))
    await waitFor(() => expect(focusProbe()).toBe("9"))
    await waitFor(() => expect(convProbe()).toBe("77"))
    // …and on rows that really are this thread's.
    expect(listProbe()).toBe("77:1")
  })

  it("still opens it after the user closed the panel and left the tab", async () => {
    // Refocusing a tab restores its artifact, the same way the reconcile
    // restores a PRD tab's document on every refocus. A manual close sticks
    // while you are ON the tab; leaving retires it.
    await openReportThread()

    await act(async () => { fireEvent.click(screen.getByTestId("close-panel")) })
    await settle()

    await act(async () => { fireEvent.click(newChatBtn()) })
    await settle()
    expect(panelProbe()).toBe("none")

    await act(async () => { fireEvent.click(tabChip(TAB_A_TITLE)) })

    await waitFor(() => expect(panelProbe()).toBe("reports"))
    await waitFor(() => expect(focusProbe()).toBe("9"))
  })

  it("does not reopen a panel the user closed while they stay on the tab", async () => {
    // The other half of the same rule: within one visit, closing means closed.
    await openReportThread()

    await act(async () => { fireEvent.click(screen.getByTestId("close-panel")) })
    await settle()

    expect(panelProbe()).toBe("none")
  })
})

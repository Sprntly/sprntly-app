// @vitest-environment jsdom
//
// ChatScreen — the Goal Analysis run's lifetime against a chat thread.
//
// Both behaviours here shipped with ZERO coverage, and both are the kind that
// only bite in a browser:
//
//   - `goalRunId` lives in the shared content slot, which is memory only. A
//     reload made a running analysis UNREACHABLE while it carried on finishing
//     on the server.
//   - The same slot is shared across chat tabs, so a run left set showed thread
//     A's analysis — with a LIVE Confirm button — on thread B, where confirming
//     would lock a goal definition against a conversation the reader was not
//     even looking at.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined") window.scrollTo = () => {}
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const listRuns = vi.fn()
const startRun = vi.fn()

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
    },
    goalAnalysisApi: {
      list: (...a: unknown[]) => listRuns(...a),
      start: (...a: unknown[]) => startRun(...a),
      get: vi.fn(),
      confirm: vi.fn(),
    },
  }
})

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn(), resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(), loadPrdById: vi.fn(),
}))
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(), resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn().mockReturnValue(null),
}))
vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn(),
  }),
}))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(""),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))
vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({
    entriesByInsight: new Map(), loading: false, refetch: vi.fn(),
  }),
}))

// The flag is the gate. Flipped per test, because "does nothing for an
// unenrolled company" is as much a requirement as the restore itself.
let crucible = true
vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({
    loading: false,
    profile: null,
    workspace: { feature_flags: { crucible } },
    refresh: async () => {},
  }),
}))

import { NavigationProvider, useNavigation } from "../../../../context/NavigationContext"
import { ContentProvider, useContent } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

function Harness() {
  const { content } = useContent()
  // The panel's OPEN state, which the slot probe below cannot see.
  //
  // Every test in this file asserted `goal-probe` — the content slot — and the
  // slot was always set correctly. What shipped broken was the other half:
  // `ContentPanel` only un-hides the `goal` tab once the panel is open, so a
  // restored run sat in the slot with nothing on screen and no control to
  // reveal it. A probe that cannot distinguish "restored" from "restored and
  // visible" is why that had coverage and still shipped.
  const nav = useNavigation()
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      "div",
      { "data-testid": "goal-probe" },
      content.goalRunId != null ? String(content.goalRunId) : "none",
    ),
    React.createElement(
      "div",
      { "data-testid": "panel-probe" },
      nav.contentPanelTab ?? "closed",
    ),
    React.createElement(
      "button",
      { "data-testid": "close-panel", onClick: () => nav.closeContentPanel() },
      "close",
    ),
    React.createElement(
      "button",
      { "data-testid": "open-prd-panel", onClick: () => nav.openContentPanel("prd") },
      "open prd",
    ),
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

const goalProbe = () => screen.getByTestId("goal-probe").textContent
const panelProbe = () => screen.getByTestId("panel-probe").textContent

function seedPersistedTab(
  tab: Record<string, unknown>,
  activeId: string,
  more: Record<string, unknown>[] = [],
) {
  sessionStorage.setItem(
    "sprntly_chat_tabs_anon_acme", JSON.stringify([tab, ...more]))
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", activeId)
}

async function switchToTab(title: string) {
  const bar = screen.getByTestId("chat-tab-bar")
  const tab = within(bar).getByText(title)
  await act(async () => { fireEvent.click(tab) })
}

/** Drive the composer the way a user does: + menu -> Analyse a goal -> send. */
async function startAGoal(text: string) {
  await act(async () => {
    fireEvent.click(screen.getAllByLabelText("Add attachment or skill")[0])
  })
  await act(async () => {
    fireEvent.click(screen.getAllByTestId("menu-goal-analysis")[0])
  })
  const box = screen.getAllByPlaceholderText(/Ask Sprntly anything/)[0]
  await act(async () => {
    fireEvent.change(box, { target: { value: text } })
  })
  await act(async () => {
    fireEvent.click(screen.getAllByLabelText("Send")[0])
  })
}

beforeEach(() => {
  crucible = true
  listRuns.mockReset()
  listRuns.mockResolvedValue({ runs: [] })
  startRun.mockReset()
  startRun.mockResolvedValue({ id: 1, conversation_id: null, status: "resolving_goal" })
  sessionStorage.clear()
  localStorage.clear()
})
afterEach(() => {
  cleanup()
  sessionStorage.clear()
  localStorage.clear()
})

describe("restoring a run after a reload", () => {
  it("reopens this thread's most recent run", async () => {
    listRuns.mockResolvedValue({
      runs: [{ id: 42, conversation_id: 7, status: "running" }],
    })
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await waitFor(() => expect(goalProbe()).toBe("42"))
  })

  it("puts it ON SCREEN, not merely in the slot", async () => {
    // The bug this file's other tests could not see. `ContentPanel` un-hides
    // the `goal` tab only once the panel is open, and on a fresh load it is
    // closed — so a run restored into the slot was invisible, with no control
    // anywhere to reveal it. A multi-minute analysis behind two human gates
    // became single-use.
    listRuns.mockResolvedValue({
      runs: [{ id: 42, conversation_id: 7, status: "ready" }],
    })
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await waitFor(() => expect(goalProbe()).toBe("42"))
    await waitFor(() => expect(panelProbe()).toBe("goal"))
  })

  it("does not hijack a panel the reader already has open", async () => {
    listRuns.mockResolvedValue({
      runs: [{ id: 42, conversation_id: 7, status: "ready" }],
    })
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    fireEvent.click(screen.getByTestId("open-prd-panel"))
    await waitFor(() => expect(goalProbe()).toBe("42"))
    // Restored and reachable, but the reader's own choice still wins.
    expect(panelProbe()).toBe("prd")
  })

  it("stays closed once the reader closes it", async () => {
    // Opening once per tab is the difference between "you can get back to it"
    // and "a panel you dismissed keeps reappearing".
    listRuns.mockResolvedValue({
      runs: [{ id: 42, conversation_id: 7, status: "ready" }],
    })
    seedPersistedTab(
      { id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1",
      [{ id: "t2", title: "other", dbConvId: 8, messages: [] }],
    )
    mountApp()
    await waitFor(() => expect(panelProbe()).toBe("goal"))

    fireEvent.click(screen.getByTestId("close-panel"))
    await waitFor(() => expect(panelProbe()).toBe("closed"))

    await switchToTab("other")
    await switchToTab("chat")
    await waitFor(() => expect(goalProbe()).toBe("42"))
    expect(panelProbe()).toBe("closed")
  })

  it("ignores a run belonging to a different thread", async () => {
    listRuns.mockResolvedValue({
      runs: [{ id: 42, conversation_id: 999, status: "running" }],
    })
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await waitFor(() => expect(listRuns).toHaveBeenCalled())
    expect(goalProbe()).toBe("none")
  })

  it("does NOT reopen a failed run", async () => {
    // It would pin an undismissable red tab to that thread for as long as the
    // row exists, with nothing the reader could do about it.
    listRuns.mockResolvedValue({
      runs: [{ id: 42, conversation_id: 7, status: "failed" }],
    })
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await waitFor(() => expect(listRuns).toHaveBeenCalled())
    expect(goalProbe()).toBe("none")
  })

  it("does not even ASK for an unenrolled company", async () => {
    // A request per thread switch, and a 403 in the console, for a feature
    // that company cannot use.
    crucible = false
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await new Promise((r) => setTimeout(r, 50))
    expect(listRuns).not.toHaveBeenCalled()
    expect(goalProbe()).toBe("none")
  })

  it("a failing listing leaves the chat working", async () => {
    listRuns.mockRejectedValue(new Error("down"))
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await waitFor(() => expect(listRuns).toHaveBeenCalled())
    expect(goalProbe()).toBe("none")
    expect(screen.getByTestId("chat-tab-bar")).toBeTruthy()
  })
})

describe("the guards around the restore", () => {
  // Three of these guards previously reverted GREEN — the tests covered the
  // restore itself and nothing that protects it. Each test below is written
  // against the specific revert it must catch.

  it("a run the user just started is not clobbered by an older listing", async () => {
    // The ref guard. The listing is in flight when the user starts a run; if
    // it wins the race it yanks the panel back to a run they did not ask for.
    let release: (v: unknown) => void = () => {}
    listRuns.mockReturnValue(new Promise((r) => { release = r }))
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await waitFor(() => expect(listRuns).toHaveBeenCalled())

    // A run starts while the listing is still open.
    startRun.mockResolvedValue({ id: 99, conversation_id: 7, status: "resolving_goal" })
    await startAGoal("raise net revenue retention")
    await waitFor(() => expect(goalProbe()).toBe("99"))

    // Only now does the older listing land.
    release({ runs: [{ id: 42, conversation_id: 7, status: "running" }] })
    await new Promise((r) => setTimeout(r, 50))
    expect(goalProbe()).toBe("99")
  })

  it("switching away and back restores again", async () => {
    // The ref CLEAR. Without it the ref still holds thread A's run when we
    // return, so the restore declines and the panel never comes back.
    listRuns.mockResolvedValue({
      runs: [
        { id: 42, conversation_id: 7, status: "running" },
        { id: 43, conversation_id: 8, status: "running" },
      ],
    })
    seedPersistedTab(
      { id: "t1", title: "A", dbConvId: 7, messages: [] },
      "t1",
      [{ id: "t2", title: "B", dbConvId: 8, messages: [] }],
    )
    mountApp()
    await waitFor(() => expect(goalProbe()).toBe("42"))

    await switchToTab("B")
    await waitFor(() => expect(goalProbe()).toBe("43"))
    await switchToTab("A")
    await waitFor(() => expect(goalProbe()).toBe("42"))
  })

  it("two tabs on ONE conversation still re-run the restore", async () => {
    // `activeTabId` in the deps. Two tabs can share a conversation id, so
    // without it the effect does not re-run on the switch: the content reset
    // clears the panel and nothing ever puts it back.
    listRuns.mockResolvedValue({
      runs: [{ id: 42, conversation_id: 7, status: "running" }],
    })
    seedPersistedTab(
      { id: "t1", title: "A", dbConvId: 7, messages: [] },
      "t1",
      [{ id: "t2", title: "B", dbConvId: 7, messages: [] }],
    )
    mountApp()
    await waitFor(() => expect(goalProbe()).toBe("42"))
    await switchToTab("B")
    await waitFor(() => expect(goalProbe()).toBe("42"))
  })
})

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
const getRun = vi.fn()
const confirmRun = vi.fn()
const approveRun = vi.fn()
const listTurns = vi.fn()

/** A plan thin enough to render and real enough to approve. */
const PLAN = {
  goal_text: "raise net revenue retention",
  definition_text: "NRR, all paying accounts, trailing 90 days",
  currency: "USD",
  total_signals: 12,
  sources: [{ source_type: "slack", signal_count: 12, witnesses: "what people said" }],
  cannot_answer: [],
  will_produce: ["themes"],
}

vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    apiErrorMessage: (_s: number, b: unknown) =>
      (b as { detail?: string })?.detail || "",
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
      // SLOW ON PURPOSE, so a tab can actually be observed mid-hydration. The
      // restore has to wait for this and then RUN.
      listTurns: (...a: unknown[]) => listTurns(...a),
    },
    goalAnalysisApi: {
      list: (...a: unknown[]) => listRuns(...a),
      start: (...a: unknown[]) => startRun(...a),
      get: (...a: unknown[]) => getRun(...a),
      confirm: (...a: unknown[]) => confirmRun(...a),
      approve: (...a: unknown[]) => approveRun(...a),
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
/** Drive a started run all the way through BOTH gates, in the thread.
 *
 *  The panel no longer opens when a run starts — the gates are a conversation
 *  and they happen in the conversation, so the panel opens only once there is a
 *  document-shaped thing to show. Every guard below therefore has to be
 *  exercised at APPROVE time, which is where the panel now appears.
 */
async function answerBothGatesInThread(runId: number) {
  await waitFor(() => expect(screen.getByTestId("goal-gate-definition")).toBeTruthy())
  await act(async () => {
    fireEvent.change(screen.getByLabelText("What this goal means"),
      { target: { value: "NRR, all paying accounts, trailing 90 days" } })
  })
  getRun.mockResolvedValue({
    id: runId, status: "awaiting_approval", prioritisation: { plan: PLAN },
  })
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /confirm and plan/i }))
  })
  await waitFor(() => expect(screen.getByTestId("goal-gate-plan")).toBeTruthy())
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /approve|start reading/i }))
  })
}

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
  listTurns.mockReset()
  listTurns.mockResolvedValue({ turns: [] })
  getRun.mockReset()
  confirmRun.mockReset()
  approveRun.mockReset()
  confirmRun.mockResolvedValue({})
  approveRun.mockResolvedValue({})
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

  it("stays closed while you remain on the thread, once you close it", async () => {
    // The claim is per VISIT, not per session. Within a visit it must hold:
    // a reader who dismisses the panel should not have it shoved back by the
    // next re-render.
    //
    // This deliberately no longer asserts that it is still closed after
    // switching away and back. It used to, and that was asserting the bug:
    // the tab-switch reconcile closes the panel on the way out, so "closed on
    // return" was indistinguishable from the run being unreachable again —
    // which is #1283. Coming back is a new visit, and the sibling test above
    // ("switching away and back restores again") is what pins that.
    listRuns.mockResolvedValue({
      runs: [{ id: 42, conversation_id: 7, status: "ready" }],
    })
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await waitFor(() => expect(panelProbe()).toBe("goal"))

    fireEvent.click(screen.getByTestId("close-panel"))
    await waitFor(() => expect(panelProbe()).toBe("closed"))

    // Give the effect every chance to re-fire: the close itself changes the
    // state it keys on, which is exactly when a missing claim would reopen it.
    await new Promise((r) => setTimeout(r, 60))
    expect(panelProbe()).toBe("closed")
    expect(goalProbe()).toBe("42")   // still restored, just not forced on screen
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
    // it wins the race it yanks the panel to a run they did not ask for.
    //
    // STARTING NO LONGER OPENS THE PANEL — the gates are answered in the thread
    // — so the guard is observed where the panel now appears: after approve it
    // must be the run the user started, not the one the stale listing carried.
    let release: (v: unknown) => void = () => {}
    listRuns.mockReturnValue(new Promise((r) => { release = r }))
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await waitFor(() => expect(listRuns).toHaveBeenCalled())

    startRun.mockResolvedValue({ id: 99, conversation_id: 7, status: "resolving_goal" })
    getRun.mockResolvedValue({ id: 99, status: "awaiting_confirmation", prioritisation: {} })
    await startAGoal("raise net revenue retention")

    // Only now does the older listing land — while the user is mid-gate.
    release({ runs: [{ id: 42, conversation_id: 7, status: "running" }] })
    await new Promise((r) => setTimeout(r, 50))

    await answerBothGatesInThread(99)
    await waitFor(() => expect(goalProbe()).toBe("99"))
    await waitFor(() => expect(panelProbe()).toBe("goal"))
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
    // AND ON SCREEN. This test's own comment says "the panel never comes
    // back", and for a while it asserted only the slot — so the panel not
    // coming back is precisely what it could not see. The tab-switch reconcile
    // closes the panel on the way out, so without retiring the per-tab claim
    // the restore declines on return and the run is unreachable again.
    await waitFor(() => expect(panelProbe()).toBe("goal"))
  })

  it("a run the reader APPROVED also stays closed once dismissed", async () => {
    // The per-tab claim. A run the reader drove themselves opens the panel
    // directly, so unless the approve path claims the tab, the reader's close
    // satisfies every guard and the auto-open effect shoves the panel back.
    //
    // The file says this out loud one tab over, at the reports hand-off:
    // "Claim the tab: this IS its one auto-open, so closing the panel here
    // must not hand straight over to the auto-open effect below."
    listRuns.mockResolvedValue({ runs: [] })
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await waitFor(() => expect(listRuns).toHaveBeenCalled())

    startRun.mockResolvedValue({ id: 99, conversation_id: 7, status: "resolving_goal" })
    getRun.mockResolvedValue({ id: 99, status: "awaiting_confirmation", prioritisation: {} })
    await startAGoal("raise net revenue retention")
    await answerBothGatesInThread(99)
    await waitFor(() => expect(panelProbe()).toBe("goal"))

    fireEvent.click(screen.getByTestId("close-panel"))
    await waitFor(() => expect(panelProbe()).toBe("closed"))
    await new Promise((r) => setTimeout(r, 60))
    expect(panelProbe()).toBe("closed")
  })

  it("claims the tab the run was ANSWERED on, not the one last seen", async () => {
    // The claim has to name the tab the reader is actually on. It used to be
    // read from `activeTabId` inside a callback rebuilt only when the
    // conversation changes — stale in exactly the two cases this file calls out
    // by name: two tabs on one conversation, and two brand-new chats (both
    // `activeConvId === null`). Filing against the tab the reader LEFT means
    // the run reopens the instant they dismiss it.
    listRuns.mockResolvedValue({ runs: [] })
    seedPersistedTab(
      { id: "t1", title: "A", dbConvId: 7, messages: [] },
      "t1",
      [{ id: "t2", title: "B", dbConvId: 7, messages: [] }],   // SAME conversation
    )
    mountApp()
    await waitFor(() => expect(listRuns).toHaveBeenCalled())

    await switchToTab("B")
    startRun.mockResolvedValue({ id: 99, conversation_id: 7, status: "resolving_goal" })
    getRun.mockResolvedValue({ id: 99, status: "awaiting_confirmation", prioritisation: {} })
    await startAGoal("raise net revenue retention")
    // The claim is filed where the panel actually opens: at approve. The tab
    // id is passed through from the turn being answered rather than read from
    // a closure, so it cannot go stale the way `activeTabId` could.
    await answerBothGatesInThread(99)
    await waitFor(() => expect(panelProbe()).toBe("goal"))

    fireEvent.click(screen.getByTestId("close-panel"))
    await waitFor(() => expect(panelProbe()).toBe("closed"))
    await new Promise((r) => setTimeout(r, 60))
    expect(panelProbe()).toBe("closed")
  })

  it("a refused confirm says why AND leaves the gate answerable", async () => {
    // Moved here with the gate, and asserting the opposite of what it used to.
    // A 422 means the server refused the BODY before claiming anything: the run
    // is still sitting at its gate, so the card has to stay answerable. An
    // earlier version of this test asserted the card was gone, which locked in
    // exactly the dead end it should have caught — a run still waiting for the
    // reader, with nothing on screen to answer it.
    listRuns.mockResolvedValue({ runs: [] })
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await waitFor(() => expect(listRuns).toHaveBeenCalled())

    startRun.mockResolvedValue({ id: 99, conversation_id: 7, status: "resolving_goal" })
    getRun.mockResolvedValue({ id: 99, status: "awaiting_confirmation", prioritisation: {} })
    await startAGoal("raise net revenue retention")
    await waitFor(() => expect(screen.getByTestId("goal-gate-definition")).toBeTruthy())

    // A refusal as it actually arrives — `status` and `body` on the error. The
    // local mock's `ApiError` hardcodes `status = 0` and would not be one.
    confirmRun.mockRejectedValue(Object.assign(new Error("refused"), {
      status: 422, body: { detail: "That definition is too long." },
    }))
    await act(async () => {
      fireEvent.change(screen.getByLabelText("What this goal means"),
        { target: { value: "NRR, all paying accounts" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /confirm and plan/i }))
    })

    // The reason is on the turn...
    await waitFor(() =>
      expect(document.body.textContent).toContain("too long"))
    // ...and the gate is still there to answer.
    expect(screen.getByTestId("goal-gate-definition")).toBeTruthy()
    expect(
      (screen.getByRole("button", { name: /confirm and plan/i }) as HTMLButtonElement)
        .disabled,
    ).toBe(false)
  })

  it("re-arms an unanswered gate after a reload, even in a chat that already ran one", async () => {
    // THE DEAD END THIS EXISTS TO STOP. The gate lives on the thread, and the
    // thread lives in sessionStorage. A new session came back with the run at
    // its gate, the panel deferring to the chat, and the chat holding no card:
    // nothing anywhere could answer it.
    //
    // The first guard asked "has this thread EVER held a gate", so the settled
    // record of a previous run — the artefact this change exists to keep —
    // blocked the rebuild for the next one. Keyed on the RUN now.
    listRuns.mockResolvedValue({
      runs: [{ id: 99, conversation_id: 7, status: "awaiting_confirmation" }],
    })
    getRun.mockResolvedValue({
      id: 99, status: "awaiting_confirmation", goal_text: "raise NRR",
      prioritisation: { ask: "What counts as retained?" },
    })
    // A thread that ALREADY carries a finished run's settled record.
    seedPersistedTab({
      id: "t1", title: "chat", dbConvId: 7,
      thread: [{ id: "old", query: "reduce churn",
        goalGateResolved: { kind: "definition", definition: "logo churn" } }],
    }, "t1")
    mountApp()

    await waitFor(() => expect(getRun).toHaveBeenCalledWith(99))
    await waitFor(() =>
      expect(screen.getByTestId("goal-gate-definition")).toBeTruthy())
    expect(document.body.textContent).toContain("What counts as retained?")
  })

  it("a pending gate does not come back from storage as a permanent spinner", async () => {
    // `pending` is an in-flight indicator whose poll died with the page — the
    // same class as `prdCommandThinking` and `ticketSetRunning`, both stripped
    // on save. Restored it would sit forever AND satisfy the has-this-run guard
    // against itself, blocking the rebuild that would have replaced it.
    listRuns.mockResolvedValue({
      runs: [{ id: 99, conversation_id: 7, status: "awaiting_confirmation" }],
    })
    getRun.mockResolvedValue({
      id: 99, status: "awaiting_confirmation", goal_text: "raise NRR",
      prioritisation: { ask: "What counts as retained?" },
    })
    seedPersistedTab({
      id: "t1", title: "chat", dbConvId: 7,
      thread: [{ id: "p", query: "raise NRR",
        goalGate: { kind: "pending", goalText: "raise NRR" } }],
    }, "t1")
    mountApp()

    // The rebuild runs rather than being blocked by the stale spinner.
    await waitFor(() =>
      expect(screen.getByTestId("goal-gate-definition")).toBeTruthy())
    expect(screen.queryByTestId("goal-gate-pending")).toBeNull()
  })

  it("re-arms the PLAN gate after the definition was already answered", async () => {
    // Drives the REAL confirm, because the bug is in what confirm LEAVES
    // BEHIND: it settled the turn but kept `goalGate` set — invisible, since
    // the settled card renders first — and the restore's run-keyed guard reads
    // exactly that field. An answered definition therefore went on claiming
    // "this run is already on screen" and blocked the plan gate's rebuild.
    // Seeding the post-confirm state by hand would encode the FIXED shape and
    // test nothing.
    listRuns.mockResolvedValue({ runs: [] })
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, thread: [] }, "t1")
    mountApp()
    await waitFor(() => expect(listRuns).toHaveBeenCalled())

    startRun.mockResolvedValue({ id: 99, conversation_id: 7, status: "resolving_goal" })
    getRun.mockResolvedValue({ id: 99, status: "awaiting_confirmation", prioritisation: {} })
    await startAGoal("raise NRR")
    await waitFor(() => expect(screen.getByTestId("goal-gate-definition")).toBeTruthy())

    // Confirm, but the plan never arrives in this session — the run moves on
    // server-side while nothing is watching. `getRun` stays at
    // `awaiting_confirmation` so `awaitGoalRun` never resolves a plan here.
    await act(async () => {
      fireEvent.change(screen.getByLabelText("What this goal means"),
        { target: { value: "NRR, 90 days" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /confirm and plan/i }))
    })
    await waitFor(() =>
      expect(screen.getByTestId("goal-gate-definition-done")).toBeTruthy())

    // The persisted thread is what the next session sees. The stale gate, if
    // confirm left one, blocks the restore below.
    const persisted = JSON.parse(
      sessionStorage.getItem("sprntly_chat_tabs_anon_acme") ?? "[]")
    const turn = persisted[0].thread.find(
      (t: Record<string, unknown>) => t.goalGateResolved)
    expect(turn.goalGate ?? null).toBeNull()
  })

  it("restores a gate on a tab whose thread was still being fetched", async () => {
    // Bailing on a hydrating tab without listing it as a dependency meant
    // bailing FOREVER — nothing else in the effect's deps changes when the
    // fetch lands. Opening a live-gate conversation from the history rail
    // therefore never restored its gate.
    //
    // Reaching it needs the resume flow: `sprntly_resume_conv` spawns the tab
    // and `listTurns` fills it, and while that call is open the tab is
    // `hydrating`.
    listTurns.mockReturnValue(
      new Promise((r) => setTimeout(() => r({ turns: [] }), 150)))
    listRuns.mockResolvedValue({
      runs: [{ id: 99, conversation_id: 7, status: "awaiting_confirmation" }],
    })
    getRun.mockResolvedValue({
      id: 99, status: "awaiting_confirmation", goal_text: "raise NRR",
      prioritisation: { ask: "What counts as retained?" },
    })
    localStorage.setItem("sprntly_resume_conv",
      JSON.stringify({ dbId: 7, title: "raise NRR" }))
    mountApp()

    await waitFor(
      () => expect(screen.queryByTestId("goal-gate-definition")).not.toBeNull(),
      { timeout: 4000 })
    expect(screen.getByRole("button", { name: /confirm and plan/i })).toBeTruthy()
  })

  // NOT TESTED HERE, deliberately, rather than tested falsely: the restore must
  // re-run once a hydrating tab's thread fetch lands (`activeTabHydrating` is a
  // dependency for exactly that). `hydrating` is set only by the history-rail
  // resume flow and is stripped from the persisted tab, so nothing this fixture
  // can seed reaches it — a test written against it passed identically with the
  // dependency removed, which is worse than no test. Reaching it needs the
  // resume flow wired into this harness.

  it("a run that dies mid-approve is not reported as started", async () => {
    // `awaitGoalRun` also returns on `failed`/`cancelled` — a verdict, not a
    // destination. Treating any non-null answer as "it started" opened the
    // panel onto a dead run and told the reader it was running.
    listRuns.mockResolvedValue({ runs: [] })
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, thread: [] }, "t1")
    mountApp()
    await waitFor(() => expect(listRuns).toHaveBeenCalled())

    startRun.mockResolvedValue({ id: 99, conversation_id: 7, status: "resolving_goal" })
    getRun.mockResolvedValue({ id: 99, status: "awaiting_confirmation", prioritisation: {} })
    await startAGoal("raise NRR")
    await waitFor(() => expect(screen.getByTestId("goal-gate-definition")).toBeTruthy())

    // Confirm through to the PLAN gate, but stop there — the approve is what
    // this test is about.
    await act(async () => {
      fireEvent.change(screen.getByLabelText("What this goal means"),
        { target: { value: "NRR, 90 days" } })
    })
    getRun.mockResolvedValue({
      id: 99, status: "awaiting_approval", prioritisation: { plan: PLAN },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /confirm and plan/i }))
    })
    await waitFor(() => expect(screen.getByTestId("goal-gate-plan")).toBeTruthy())

    // The approve response is lost, and the run turns out to have died.
    approveRun.mockRejectedValue(new Error("connection lost"))
    getRun.mockResolvedValue({ id: 99, status: "failed", prioritisation: {} })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /approve and run/i }))
    })
    await waitFor(() =>
      expect(document.body.textContent).toContain("stopped before it could read"))
    // ...and the promise to keep checking, which that verdict just answered,
    // does not survive next to it.
    expect(document.body.textContent).not.toContain("Checking…")
  })

  it("does not mark an innocent tab as already-opened", async () => {
    // The other half of the same bug: a claim misfiled against tab A marks it
    // as already-auto-opened, so A's OWN restored analysis never opens when
    // the reader returns. #1283 again, one tab over — and this one is silent,
    // because nothing about tab B looks wrong.
    listRuns.mockResolvedValue({
      runs: [{ id: 42, conversation_id: 7, status: "ready" }],
    })
    seedPersistedTab(
      { id: "t1", title: "A", dbConvId: 7, messages: [] },
      "t1",
      [{ id: "t2", title: "B", dbConvId: 7, messages: [] }],
    )
    mountApp()
    await waitFor(() => expect(panelProbe()).toBe("goal"))

    await switchToTab("B")
    startRun.mockResolvedValue({ id: 99, conversation_id: 7, status: "resolving_goal" })
    getRun.mockResolvedValue({ id: 99, status: "awaiting_confirmation", prioritisation: {} })
    await startAGoal("raise net revenue retention")
    await answerBothGatesInThread(99)
    await waitFor(() => expect(goalProbe()).toBe("99"))

    // Back to A: a new visit, its claim retired, its own run should show.
    await switchToTab("A")
    await waitFor(() => expect(goalProbe()).toBe("42"))
    await waitFor(() => expect(panelProbe()).toBe("goal"))
  })

  it("does not open on the thread being switched TO, using the old thread's run", async () => {
    // The switch commit reads a FRESH `activeTabId` beside a STALE
    // `content.goalRunId`, because the per-thread reset is itself an effect in
    // the same flush. Unguarded, arriving on a thread with no analysis opened a
    // goal panel that went blank the moment the reset landed.
    //
    // The dismissal matters: it is what leaves `contentPanelTab` legitimately
    // null, so the hijack guard cannot mask the bug.
    listRuns.mockResolvedValue({
      runs: [{ id: 42, conversation_id: 7, status: "ready" }],
    })
    seedPersistedTab(
      { id: "t1", title: "A", dbConvId: 7, messages: [] },
      "t1",
      [{ id: "t2", title: "B", dbConvId: 8, messages: [] }],
    )
    mountApp()
    await waitFor(() => expect(panelProbe()).toBe("goal"))
    fireEvent.click(screen.getByTestId("close-panel"))
    await waitFor(() => expect(panelProbe()).toBe("closed"))

    await switchToTab("B")
    await new Promise((r) => setTimeout(r, 60))
    // B has no run. A goal panel here is either blank or showing A's analysis;
    // both are wrong, and one of them is clickable.
    expect(panelProbe()).not.toBe("goal")
  })

  it("never shows one thread's run on another thread", async () => {
    // The hazard `ChatScreen.tsx:2184` exists to prevent, raised in severity by
    // this feature: leaving the slot set showed thread A's analysis — WITH A
    // LIVE CONFIRM BUTTON — on thread B. While the panel stayed shut that was
    // invisible; opening it makes it actionable, so the guarantee now has to
    // hold visibly.
    //
    // It holds because the per-thread reset is declared ~2,400 lines before the
    // open effect and React runs effects in order, so the open only ever sees a
    // slot already cleared for the thread being entered. That is an ORDERING
    // property, and ordering is exactly what a refactor changes silently — so
    // it is pinned here rather than left to the declaration order.
    listRuns.mockResolvedValue({
      runs: [{ id: 42, conversation_id: 7, status: "awaiting_confirmation" }],
    })
    seedPersistedTab(
      { id: "t1", title: "A", dbConvId: 7, messages: [] },
      "t1",
      [{ id: "t2", title: "B", dbConvId: 8, messages: [] }],
    )
    mountApp()
    await waitFor(() => expect(panelProbe()).toBe("goal"))
    expect(goalProbe()).toBe("42")

    await switchToTab("B")
    // B has no run of its own. Whatever the panel does, it must not be
    // displaying 42 — that is thread A's, and its Confirm button would lock a
    // goal definition against a conversation the reader is not looking at.
    await waitFor(() => expect(goalProbe()).toBe("none"))
    expect(panelProbe()).not.toBe("goal")
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
    // Same point as above: "nothing ever puts it back" is about the PANEL, so
    // the assertion has to look at the panel. Two tabs on one conversation is
    // the harder case — the claim is keyed by tab, so tab B has its own.
    await waitFor(() => expect(panelProbe()).toBe("goal"))
  })
})

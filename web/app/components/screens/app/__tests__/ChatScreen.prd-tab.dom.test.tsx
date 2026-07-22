// @vitest-environment jsdom
//
// ChatScreen — "a PRD opens as a NEW CHAT TAB with the content panel over it".
//
// Every "view/generate PRD" affordance (brief finding cards, brief composer,
// ideation item) hands the PRD off via NavigationContext.openPrdTab, which stores
// a pending request and routes to `/`. ChatScreen consumes it once (openPrdInTab),
// spawning a fresh chat tab, driving the (generate | ready | load) source into
// the shared ContentContext, and flagging the content panel (Evidence / PRD /
// Tickets) to slide open over that tab. These tests mount the REAL ChatScreen
// inside the real Navigation + Content providers and drive openPrdTab through a
// tiny in-tree harness, asserting the tab is created/activated, the source is
// honoured, and the panel is opened.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// openPrdTab calls window.scrollTo (unimplemented in jsdom) — stub it to keep
// the test output clean.
if (typeof window !== "undefined") window.scrollTo = () => {}

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      // Report reduced-motion so AskReplyBody renders replies in full immediately
      // (no simulated typing stream) — keeps thread-text assertions deterministic.
      matches: /prefers-reduced-motion/.test(query), media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

// ── Boundary mocks (network / router / heavy contexts) ─────────────────────
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: {
      current: vi.fn().mockResolvedValue({ id: 1, insights: [] }),
    },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
      // Default: no saved history for a PRD. Individual tests override per prd_id.
      byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
    },
    // A PRD tab mounts PrdInputQuestions, which loads its questions from prdApi;
    // stub it to an empty set so the panel behaviour under test is unaffected.
    prdApi: {
      listInputQuestions: vi.fn().mockResolvedValue([]),
      answerInputQuestion: vi.fn(),
    },
  }
})

const runPrdGeneration = vi.fn().mockResolvedValue({
  ok: true,
  prd: { prd_id: 77, title: "Generated PRD", metaLine: "", sections: [] },
})
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (...args: unknown[]) => runPrdGeneration(...args),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn().mockResolvedValue({
    ok: true, prd: { prd_id: 88, title: "Ideation PRD", metaLine: "", sections: [] },
  }),
  loadPrdById: vi.fn().mockResolvedValue({
    ok: true, prd: { prd_id: 99, title: "Loaded PRD", metaLine: "", sections: [] },
  }),
}))

const runAskGeneration = vi.fn().mockResolvedValue({
  answer: "canned", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
})
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: (...args: unknown[]) => runAskGeneration(...args),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn().mockReturnValue(null),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({ runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn() }),
}))

let pathname = "/"
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => pathname,
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

// Mutable holder so a test can seed the insight→PRD map (drives chatInsightState,
// which resolves the active PRD tab's real prd_id independent of the open path).
const mockPrototypeMap: { entries: Map<number, unknown> } = { entries: new Map() }
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: mockPrototypeMap.entries, refetch: vi.fn() }),
}))

import { NavigationProvider, useNavigation, type PrdTabRequest } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"
import { conversationsApi } from "../../../../lib/api"

// Harness: openPrdTab as a button (the real handoff entry point any surface uses)
// + a probe that surfaces the current content-panel tab, so tests can observe the
// panel opening without mounting the heavy ContentPanel/PrdPanelContent tree.
function Harness({ request }: { request: PrdTabRequest }) {
  const { openPrdTab, contentPanelTab } = useNavigation()
  return React.createElement(
    React.Fragment,
    null,
    React.createElement("button", { onClick: () => openPrdTab(request) }, "open-prd"),
    React.createElement("div", { "data-testid": "panel-probe" }, contentPanelTab ?? "none"),
    React.createElement(ChatScreen),
  )
}

function renderWith(request: PrdTabRequest) {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(Harness, { request })),
    ),
  )
}

const tabBar = () => within(screen.getByTestId("chat-tab-bar"))
const briefSection = () => document.querySelector("section.briefx")
const panelProbe = () => screen.getByTestId("panel-probe").textContent

async function clickOpenPrd() {
  await act(async () => { fireEvent.click(screen.getByText("open-prd")) })
}

beforeEach(() => {
  localStorage.clear()
  pathname = "/"
  runPrdGeneration.mockClear()
  runAskGeneration.mockClear()
  vi.mocked(conversationsApi.byPrd).mockReset().mockResolvedValue({ conversation: null, turns: [] })
  mockPrototypeMap.entries = new Map()
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("ChatScreen — PRD opens as a new chat tab with the panel", () => {
  const READY: PrdTabRequest = {
    title: "PRD · Ready doc",
    source: { kind: "ready", prd: { prd_id: 5, title: "Ready doc", metaLine: "", sections: [] } as never, meta: null },
  }

  it("spawns a new, active chat tab and slides the content panel (PRD) over it", async () => {
    renderWith(READY)
    await clickOpenPrd()

    // A new chat tab chip appears alongside the pinned brief tab, and it's active.
    await waitFor(() => expect(tabBar().getByText("PRD · Ready doc")).toBeTruthy())
    expect(tabBar().getByText("Top Insights")).toBeTruthy()
    expect(briefSection()).toBeNull()
    // The right-side panel opened on the PRD tab.
    await waitFor(() => expect(panelProbe()).toBe("prd"))
    // A ready PRD needs no generation.
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })

  it("drives generation for a `generate` source into its tab + opens the panel", async () => {
    renderWith({
      title: "PRD · Retention",
      source: { kind: "generate", meta: { briefId: 7, insightIndex: 0 } },
    })
    await clickOpenPrd()

    await waitFor(() => expect(tabBar().getByText("PRD · Retention")).toBeTruthy())
    // ChatScreen (not the caller) runs the generation for the new PRD tab; the
    // second arg is the live-preview onPartial callback.
    await waitFor(() => expect(runPrdGeneration).toHaveBeenCalledWith({ briefId: 7, insightIndex: 0 }, expect.any(Function)))
    await waitFor(() => expect(panelProbe()).toBe("prd"))
  })

  it("shows the insight ONCE in the opening insight card — no duplicate seeded turn", async () => {
    renderWith({
      title: "PRD · Retention",
      source: { kind: "generate", meta: { briefId: 7, insightIndex: 0 } },
      insightBody: "Users churn early. Fix onboarding.",
    })
    await clickOpenPrd()

    // The tab opens ON its insight: the opening insight card carries the finding
    // body. That card IS Sprntly presenting the insight, so there is NO separate
    // seeded thread turn repeating the same text below it (the duplication bug).
    const card = await screen.findByTestId("chat-insight-msg")
    expect(within(card).getByText(/Users churn early\./)).toBeTruthy()

    // The insight body appears exactly once, and the thread renders no turns —
    // the only .bc-turn is the insight card itself.
    expect(screen.getAllByText(/Users churn early\./)).toHaveLength(1)
    expect(document.querySelectorAll(".bc-turn:not(.bc-turn--insight)")).toHaveLength(0)
  })

  it("reuses the same tab (by title) instead of stacking duplicates", async () => {
    renderWith(READY)
    await clickOpenPrd()
    await waitFor(() => expect(tabBar().getByText("PRD · Ready doc")).toBeTruthy())
    // Switch to the brief tab, then re-open the same PRD.
    await act(async () => { fireEvent.click(tabBar().getByText("Top Insights")) })
    await clickOpenPrd()

    expect(tabBar().getAllByText("PRD · Ready doc")).toHaveLength(1)
  })

  it("rehydrates the PRD's earlier chat thread (by prd_id) when reopened", async () => {
    // The PRD (prd_id 5) already has a saved conversation from a past session.
    vi.mocked(conversationsApi.byPrd).mockResolvedValue({
      conversation: { id: 42, prd_id: 5 } as never,
      turns: [
        { id: 1, conversation_id: 42, role: "user", content: "How does auth work?", created_at: "t0" },
        { id: 2, conversation_id: 42, role: "assistant", content: "It uses OAuth.", created_at: "t1" },
      ] as never,
    })

    renderWith(READY)
    await clickOpenPrd()

    // Reopening the PRD looked it up by its id and restored the prior turns.
    await waitFor(() => expect(conversationsApi.byPrd).toHaveBeenCalledWith(5))
    expect(await screen.findByText("How does auth work?")).toBeTruthy()
    await waitFor(() => expect(screen.getByText("It uses OAuth.")).toBeTruthy())
  })

  it("rehydrates after a `generate` (find-or-create) resolves to an existing PRD", async () => {
    // "View PRD" degrades to a generate/find-or-create when the insight→PRD map
    // isn't loaded yet; the prd_id is only known AFTER generation resolves
    // (runPrdGeneration → prd_id 77). That resolved PRD already has a saved chat.
    vi.mocked(conversationsApi.byPrd).mockImplementation(async (id: number) =>
      id === 77
        ? { conversation: { id: 43, prd_id: 77 } as never,
            turns: [
              { id: 1, conversation_id: 43, role: "user", content: "Earlier question?", created_at: "t0" },
              { id: 2, conversation_id: 43, role: "assistant", content: "Earlier answer.", created_at: "t1" },
            ] as never }
        : { conversation: null, turns: [] })

    renderWith({
      title: "PRD · Retention",
      source: { kind: "generate", meta: { briefId: 7, insightIndex: 0 } },
    })
    await clickOpenPrd()

    // Hydration waits for the prd_id from generation, then looks it up.
    await waitFor(() => expect(conversationsApi.byPrd).toHaveBeenCalledWith(77))
    expect(await screen.findByText("Earlier question?")).toBeTruthy()
    await waitFor(() => expect(screen.getByText("Earlier answer.")).toBeTruthy())
  })

  it("rehydrates from the insight→PRD map even when the open path never set prdId", async () => {
    // The real live bug: "View PRD" degrades to a generate open (map race), so the
    // open path's prd_id (runPrdGeneration → 77) is unreliable / the tab's prdId
    // can stay null. But the insight→PRD map resolves this insight (index 0) to the
    // REAL prd_id 55, which chatInsightState surfaces. The effect must hydrate off
    // that — independent of the open path — and byPrd(77) returns nothing.
    mockPrototypeMap.entries = new Map([[0, { prd_id: 55, prototype: null, prd_title: "North Star" }]])
    // Open path yields NO prd_id (generation "fails"), mirroring the live case where
    // the tab's prdId stays null — so hydration must come from the insight map (55).
    runPrdGeneration.mockResolvedValueOnce({ ok: false, message: "no prd" })
    vi.mocked(conversationsApi.byPrd).mockImplementation(async (id: number) =>
      id === 55
        ? { conversation: { id: 44, prd_id: 55 } as never,
            turns: [
              { id: 1, conversation_id: 44, role: "user", content: "Mapped question?", created_at: "t0" },
              { id: 2, conversation_id: 44, role: "assistant", content: "Mapped answer.", created_at: "t1" },
            ] as never }
        : { conversation: null, turns: [] })

    renderWith({
      title: "PRD · Retention",
      source: { kind: "generate", meta: { briefId: 7, insightIndex: 0 } },
    })
    await clickOpenPrd()

    // The map resolves insight 0 → prd_id 55, so the effect hydrates off that even
    // though the open path produced no prd_id.
    expect(await screen.findByText("Mapped question?")).toBeTruthy()
    await waitFor(() => expect(screen.getByText("Mapped answer.")).toBeTruthy())
    expect(conversationsApi.byPrd).toHaveBeenCalledWith(55)
  })

  it("leaves the thread empty when the PRD has no saved conversation", async () => {
    // byPrd default (beforeEach) returns no conversation.
    renderWith(READY)
    await clickOpenPrd()

    await waitFor(() => expect(conversationsApi.byPrd).toHaveBeenCalledWith(5))
    // No restored turns rendered (READY carries no insight card either).
    expect(document.querySelectorAll(".bc-turn:not(.bc-turn--insight)")).toHaveLength(0)
  })

  it("closes the panel when switching back to the brief tab (no bleed over the brief)", async () => {
    renderWith(READY)
    await clickOpenPrd()
    // Panel is open over the new PRD tab.
    await waitFor(() => expect(panelProbe()).toBe("prd"))
    // Switch back to the pinned brief tab → the global panel must not linger.
    await act(async () => { fireEvent.click(tabBar().getByText("Top Insights")) })
    await waitFor(() => expect(panelProbe()).toBe("none"))
    // Brief surface is showing, panel is gone.
    expect(briefSection()).toBeTruthy()
  })

  it("closes the panel when starting a NEW chat after opening a PRD", async () => {
    renderWith(READY)
    await clickOpenPrd()
    // Panel is open over the new PRD tab.
    await waitFor(() => expect(panelProbe()).toBe("prd"))
    // Hit the "+" (New chat) affordance → a fresh plain chat tab with no PRD.
    // The global panel must not carry the previous tab's PRD onto it.
    await act(async () => { fireEvent.click(tabBar().getByLabelText("New chat")) })
    await waitFor(() => expect(tabBar().getByText("New chat")).toBeTruthy())
    await waitFor(() => expect(panelProbe()).toBe("none"))
  })

  it("reopens the panel when REFOCUSING the PRD tab after switching away", async () => {
    // The panel must follow the tab: leave a PRD tab (panel closes), come back to
    // it, and the PRD panel should reopen — not stay closed forcing a pin click.
    renderWith(READY)
    await clickOpenPrd()
    await waitFor(() => expect(panelProbe()).toBe("prd"))
    // Switch away to the brief → panel closes.
    await act(async () => { fireEvent.click(tabBar().getByText("Top Insights")) })
    await waitFor(() => expect(panelProbe()).toBe("none"))
    // Refocus the PRD tab → the panel comes back.
    await act(async () => { fireEvent.click(tabBar().getByText("PRD · Ready doc")) })
    await waitFor(() => expect(panelProbe()).toBe("prd"))
  })
})

describe("ChatScreen — PRD-tab asks are grounded on the open PRD", () => {
  const READY: PrdTabRequest = {
    title: "PRD · Ready doc",
    source: { kind: "ready", prd: { prd_id: 5, title: "Ready doc", metaLine: "", sections: [] } as never, meta: null },
  }

  async function sendInThread(text: string) {
    const textarea = document.querySelector(".bc-composer-input") as HTMLTextAreaElement
    expect(textarea).toBeTruthy()
    await act(async () => { fireEvent.change(textarea, { target: { value: text } }) })
    const sendBtn = within(document.querySelector(".bc-composer") as HTMLElement).getByLabelText("Send")
    await act(async () => { fireEvent.click(sendBtn) })
  }

  it("sends the tab's prd_id with the ask so the backend grounds on the PRD", async () => {
    renderWith(READY)
    await clickOpenPrd()
    await waitFor(() => expect(panelProbe()).toBe("prd"))
    await sendInThread("What are the success metrics in this PRD?")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(1))
    const opts = runAskGeneration.mock.calls[0][3] as { prd_id?: number }
    expect(opts?.prd_id).toBe(5)
  })

  it("a plain chat tab sends no prd_id (unchanged request shape)", async () => {
    renderWith(READY)
    // New plain chat tab, no PRD attached.
    await act(async () => { fireEvent.click(tabBar().getByLabelText("New chat")) })
    await waitFor(() => expect(tabBar().getByText("New chat")).toBeTruthy())
    const textarea = document.querySelector(".chat-home-composer-input") as HTMLTextAreaElement
    expect(textarea).toBeTruthy()
    await act(async () => { fireEvent.change(textarea, { target: { value: "What changed last week?" } }) })
    const sendBtn = within(document.querySelector(".chat-home-composer") as HTMLElement).getByLabelText("Send")
    await act(async () => { fireEvent.click(sendBtn) })
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(1))
    const opts = runAskGeneration.mock.calls[0][3] as { prd_id?: number } | undefined
    expect(opts?.prd_id).toBeUndefined()
  })
})

// A HEADER open (brief insight / ideation / backlog) has NO in-chat command turn:
// the insight card IS the tab's opening agent message and must stay at the TOP,
// even after the user starts chatting on it. (Contrast the in-chat command flow,
// where the card renders inline BELOW the command turn — covered in
// ChatScreen.import-command.dom.test.tsx.)
describe("ChatScreen — a brief-insight-opened PRD keeps its card at the top", () => {
  async function sendInThread(text: string) {
    const textarea = document.querySelector(".bc-composer-input") as HTMLTextAreaElement
    expect(textarea).toBeTruthy()
    await act(async () => { fireEvent.change(textarea, { target: { value: text } }) })
    const sendBtn = within(document.querySelector(".bc-composer") as HTMLElement).getByLabelText("Send")
    await act(async () => { fireEvent.click(sendBtn) })
  }

  it("renders the insight card ABOVE the user's first message (no prdInFlow)", async () => {
    renderWith({
      title: "PRD · Retention",
      source: { kind: "generate", meta: { briefId: 7, insightIndex: 0 } },
      insightBody: "Users churn early. Fix onboarding.",
    })
    await clickOpenPrd()
    // Header open: the insight card is the opening message (no command turn).
    const card = await screen.findByTestId("chat-insight-msg")

    // The user chats on the PRD — a real turn is appended to the thread.
    await sendInThread("Can you tighten the goals section?")
    const bubble = await waitFor(() => {
      const el = Array.from(document.querySelectorAll(".bc-user-bubble"))
        .find((n) => n.textContent?.includes("Can you tighten the goals section?"))
      expect(el).toBeTruthy()
      return el as Element
    })

    // The insight card stays PINNED ABOVE the user's message (header behaviour is
    // unchanged): card precedes bubble in document order.
    expect(card.compareDocumentPosition(bubble) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // And it's still the FIRST .bc-turn in the thread.
    const firstTurn = document.querySelector(".bc-thread .bc-turn")
    expect(firstTurn?.getAttribute("data-testid")).toBe("chat-insight-msg")
  })
})

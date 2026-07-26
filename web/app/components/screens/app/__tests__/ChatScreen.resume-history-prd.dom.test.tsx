// @vitest-environment jsdom
//
// ChatScreen — resuming a PRD chat from history re-binds it to its PRD.
//
// When a chat was opened from a PRD, its conversation is stamped with `prd_id`.
// Clicking that row in Chat history (or the command palette) writes the
// `sprntly_resume_conv` handoff — which now carries `prdId`. checkResume must
// re-bind the resumed tab to that PRD so:
//   • the in-chat "View PRD" button renders, and
//   • the content panel auto-reopens with the saved PRD loaded from the DB.
//
// The bug this guards: the handoff dropped `prd_id` at every hop, so a resumed
// PRD chat came back as a plain, PRD-less tab — no button, panel stayed closed
// ("generated in the first tab, but not opened when pulling the chat from
// history").
import * as React from "react"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
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

const byPrd = vi.fn().mockResolvedValue({ conversation: null, turns: [] })
const listTurns = vi.fn().mockResolvedValue({ turns: [] })
const convUpdate = vi.fn().mockResolvedValue({})

vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
      update: (...a: unknown[]) => convUpdate(...a),
      byPrd: (...a: unknown[]) => byPrd(...a),
      listTurns: (...a: unknown[]) => listTurns(...a),
    },
    prdApi: { importDoc: vi.fn() },
  }
})

const runPrdGeneration = vi.fn().mockResolvedValue({
  ok: true, prd: { prd_id: 1, title: "Regenerated", metaLine: "", sections: [] },
})
// Echo the requested id back so the test can assert the RIGHT PRD loaded.
const loadPrdById = vi.fn((id: number) =>
  Promise.resolve({ ok: true, prd: { prd_id: id, title: `PRD ${id}`, metaLine: "", sections: [] } }),
)
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (...a: unknown[]) => runPrdGeneration(...a),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(),
  loadPrdById: (...a: unknown[]) => loadPrdById(...a),
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
import { ChatScreen } from "../ChatScreen"

function Harness() {
  const { contentPanelTab } = useNavigation()
  const { content } = useContent()
  return React.createElement(
    React.Fragment,
    null,
    React.createElement("div", { "data-testid": "panel-probe" }, contentPanelTab ?? "none"),
    React.createElement("div", { "data-testid": "prd-probe" }, content.prd?.prd_id != null ? String(content.prd.prd_id) : "none"),
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
const prdProbe = () => screen.getByTestId("prd-probe").textContent

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  loadPrdById.mockClear()
  runPrdGeneration.mockClear()
  byPrd.mockClear()
  convUpdate.mockClear()
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("ChatScreen — resume a PRD chat from history", () => {
  it("re-binds the resumed tab to its PRD: button renders + panel reopens the saved PRD", async () => {
    // The handoff a PRD-stamped history row now writes: dbId + title + prdId.
    localStorage.setItem("sprntly_resume_conv", JSON.stringify({
      dbId: 42,
      title: "Cart prefill PRD",
      fallbackTurns: [{ role: "user", content: "generate a PRD for cart prefill" }],
      prdId: 88,
    }))

    await act(async () => { mountApp() })

    // The tab became PRD-bound → the reload-restore effect DB-loaded PRD 88 and
    // opened the panel (never a regeneration).
    await waitFor(() => expect(loadPrdById).toHaveBeenCalledWith(88))
    await waitFor(() => expect(panelProbe()).toBe("prd"))
    await waitFor(() => expect(prdProbe()).toBe("88"))
    expect(runPrdGeneration).not.toHaveBeenCalled()

    // …and the in-chat "View PRD" button is present (it was absent when the tab
    // came back PRD-less).
    await waitFor(() => {
      const labels = Array.from(document.querySelectorAll(".bc-action-btn")).map((b) => b.textContent)
      expect(labels).toContain("View PRD")
    })
  })

  it("renders the PRD card AFTER the command turn (inline), not pinned above it", async () => {
    // The out-of-order bug: on resume the tab lost `prdInFlow`, so the PRD card +
    // clarifying questions rendered as a top header ABOVE the user's own "generate
    // a PRD" message. A resumed PRD chat must read chronologically: the command
    // turn first, then the card. We seed the full thread (user + ack) so the tab
    // opens pre-filled, and prdId so it's PRD-bound.
    localStorage.setItem("sprntly_resume_conv", JSON.stringify({
      dbId: 42,
      title: "Cart prefill PRD",
      turns: [
        { role: "user", content: "generate a prd" },
        { role: "assistant", content: "Generating a PRD for that — it'll open on the right." },
      ],
      prdId: 88,
    }))

    await act(async () => { mountApp() })

    await waitFor(() => expect(document.querySelector('[data-testid="chat-insight-msg"]')).toBeTruthy())
    const bubble = Array.from(document.querySelectorAll(".bc-user-bubble"))
      .find((b) => b.textContent === "generate a prd")!
    const card = document.querySelector('[data-testid="chat-insight-msg"]')!
    // The card must FOLLOW the command bubble in document order (inline), never
    // precede it (the header-at-top regression).
    expect(bubble.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it("back-patches the conversation's prd_id once both the PRD id and conv id are known", async () => {
    // The create-time race: command flows (import a doc / "generate a PRD") create
    // the conversation from the seed turn BEFORE the async generate returns the
    // prd_id, so it's first stored with prd_id=null. A tab that carries BOTH a
    // prdId and a bound dbConvId must PATCH the conversation so a later
    // reopen-from-history can rebind to the PRD. Seed exactly that state.
    sessionStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([
      { id: "tab-x", title: "PRD · Cart", thread: [], dbConvId: 99, briefMeta: null, insightBody: null, prdId: 88 },
    ]))
    sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-x")

    await act(async () => { mountApp() })

    await waitFor(() => expect(convUpdate).toHaveBeenCalledWith(99, { prd_id: 88 }))
  })

  it("does NOT patch a plain chat (no prdId) or a PRD tab with no conv id yet", async () => {
    // Plain chat with a conv id → nothing to bind. PRD tab whose create hasn't
    // resolved (dbConvId null) → wait; the effect fires on a later render, not now.
    sessionStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([
      { id: "tab-plain", title: "Just a chat", thread: [], dbConvId: 5, briefMeta: null, insightBody: null, prdId: null },
      { id: "tab-nopatch", title: "PRD · pending", thread: [], dbConvId: null, briefMeta: null, insightBody: null, prdId: 7 },
    ]))
    sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-plain")

    await act(async () => { mountApp() })
    await act(async () => { await Promise.resolve() })

    expect(convUpdate).not.toHaveBeenCalled()
  })

  it("resumes a plain (no prdId) chat WITHOUT opening the panel", async () => {
    // A non-PRD conversation carries no prdId in the handoff → the tab stays a
    // plain chat: no PRD load, panel closed. Guards against over-binding.
    localStorage.setItem("sprntly_resume_conv", JSON.stringify({
      dbId: 7,
      title: "Just a chat",
      fallbackTurns: [{ role: "user", content: "what's our retention?" }],
      prdId: null,
    }))

    await act(async () => { mountApp() })
    await act(async () => { await Promise.resolve() })

    expect(panelProbe()).toBe("none")
    expect(loadPrdById).not.toHaveBeenCalled()
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })
})

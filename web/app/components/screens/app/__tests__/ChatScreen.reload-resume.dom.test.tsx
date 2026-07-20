// @vitest-environment jsdom
//
// ChatScreen — a chat-task PRD survives a page reload MID-GENERATION.
//
// Chat-task PRD tabs carry no briefMeta, so the insight-scoped pending-job
// resume never covered them: a reload during generation restored the tab but
// made zero PRD requests, rendered no PRD card, and orphaned the run in the UI
// even though the server finished it. The fix (a) stamps the tab's `prdId` at
// KICKOFF (it persists to sessionStorage), (b) counts `prdId` into the insight
// card's visibility so a reloaded task tab keeps its View PRD button, and
// (c) on activation of a restored briefMeta-less tab probes the PRD's status
// and re-enters poll+stream when it is still `generating` (the SSE replay
// frame then repaints everything generated so far).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined") window.scrollTo = () => {}

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
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
      byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
    },
    prdApi: {
      // The reload-resume probe reads the PRD row's status; tests override the
      // status per case.
      get: vi.fn().mockResolvedValue({ id: 944, status: "ready" }),
      listInputQuestions: vi.fn().mockResolvedValue([]),
      answerInputQuestion: vi.fn(),
    },
  }
})

const resumePrdGeneration = vi.fn()
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn(),
  resumePrdGeneration: (...args: unknown[]) => resumePrdGeneration(...args),
  runPrdGenerationFromIdeation: vi.fn(),
  loadPrdById: vi.fn().mockResolvedValue({
    ok: true, prd: { prd_id: 99, title: "Loaded PRD", metaLine: "", sections: [] },
  }),
}))

vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(),
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

vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: new Map(), refetch: vi.fn() }),
}))

import { NavigationProvider, useNavigation, type PrdTabRequest } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"
import { prdApi } from "../../../../lib/api"

// Keys must match ChatScreen's (authUserId "anon" via the auth mock, company
// "acme" via the company mock).
const TABS_KEY = "sprntly_chat_tabs_anon_acme"
const ACTIVE_KEY = "sprntly_chat_active_tab_anon_acme"

/** Seed sessionStorage exactly like ChatScreen persists a chat-task PRD tab:
 *  slim shape (no prd/evidence/*Generating), briefMeta null, prdId present. */
function seedRestoredTaskTab(prdId: number | null) {
  sessionStorage.setItem(TABS_KEY, JSON.stringify([{
    id: "tab-1", title: "PRD · CSV export", thread: [], dbConvId: null,
    briefMeta: null, insightBody: null, prdId,
  }]))
  sessionStorage.setItem(ACTIVE_KEY, "tab-1")
}

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

const ANY_REQUEST: PrdTabRequest = {
  title: "PRD · unused",
  source: { kind: "load", prdId: 1, meta: null },
}

function renderWith(request: PrdTabRequest = ANY_REQUEST) {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(Harness, { request })),
    ),
  )
}

const panelProbe = () => screen.getByTestId("panel-probe").textContent

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  pathname = "/"
  resumePrdGeneration.mockReset()
  vi.mocked(prdApi.get).mockReset().mockResolvedValue({ id: 944, status: "ready" } as never)
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
})

describe("ChatScreen — chat-task PRD reload resume", () => {
  it("resumes a restored task tab whose PRD is still generating (probe → poll+stream + panel)", async () => {
    seedRestoredTaskTab(944)
    vi.mocked(prdApi.get).mockResolvedValue({ id: 944, status: "generating" } as never)
    let finish: (v: unknown) => void = () => {}
    resumePrdGeneration.mockReturnValue(new Promise((res) => { finish = res }))

    renderWith()

    // The restored tab probes its PRD's status, finds it in flight, and
    // re-enters the resume poll with a live-preview callback.
    await waitFor(() => expect(prdApi.get).toHaveBeenCalledWith(944))
    await waitFor(() =>
      expect(resumePrdGeneration).toHaveBeenCalledWith(944, undefined, expect.any(Function)))
    // The panel slides open on the PRD (the user was watching it pre-reload) …
    await waitFor(() => expect(panelProbe()).toBe("prd"))
    // … and the insight card shows the run as in-flight.
    const card = await screen.findByTestId("chat-insight-msg")
    expect(within(card).getByText("Generating PRD…")).toBeTruthy()

    // Completion lands the PRD on the tab: the CTA flips to View PRD.
    await act(async () => {
      finish({ ok: true, prd: { prd_id: 944, title: "CSV export", metaLine: "", sections: [] } })
    })
    await waitFor(() => expect(within(card).getByText("View PRD")).toBeTruthy())
  })

  it("keeps the PRD card (View PRD) on a restored task tab whose PRD already finished — and does NOT regenerate", async () => {
    seedRestoredTaskTab(944)
    vi.mocked(prdApi.get).mockResolvedValue({ id: 944, status: "ready" } as never)

    renderWith()

    // Pre-fix the card vanished entirely after reload (prd stripped, no
    // briefMeta, no in-flight flag). The persisted prdId now keeps it.
    const card = await screen.findByTestId("chat-insight-msg")
    await waitFor(() => expect(within(card).getByText("View PRD")).toBeTruthy())
    // A finished PRD is left to the lazy click path — no auto poll, no regen.
    await waitFor(() => expect(prdApi.get).toHaveBeenCalledWith(944))
    expect(resumePrdGeneration).not.toHaveBeenCalled()
  })

  it("a restored plain chat tab (no prdId) neither probes nor renders a PRD card", async () => {
    seedRestoredTaskTab(null)

    renderWith()

    await screen.findByText(/Ask Sprntly anything|New chat/i).catch(() => {})
    expect(prdApi.get).not.toHaveBeenCalled()
    expect(resumePrdGeneration).not.toHaveBeenCalled()
    expect(screen.queryByTestId("chat-insight-msg")).toBeNull()
  })

  it("stamps prdId onto the tab at KICKOFF for a resume-source PRD so it persists mid-generation", async () => {
    // Generation never completes inside the test — the stamp must not wait for it.
    resumePrdGeneration.mockReturnValue(new Promise(() => {}))

    renderWith({
      title: "PRD · Scheduled email",
      source: { kind: "resume", prdId: 950, meta: null, origin: "task" },
    })
    await act(async () => { fireEvent.click(screen.getByText("open-prd")) })

    // The persisted (slim) tab already carries the id while the run is in
    // flight — this is exactly what a reload restores and resumes from.
    await waitFor(() => {
      const saved = JSON.parse(sessionStorage.getItem(TABS_KEY) ?? "[]") as Array<{ title: string; prdId: number | null }>
      const tab = saved.find((t) => t.title === "PRD · Scheduled email")
      expect(tab?.prdId).toBe(950)
    })
  })
})

// @vitest-environment jsdom
//
// ChatScreen's prototype CTA is VIEW-ONLY. The chat surface never offers
// "Generate prototype" — generation lives in the PRD panel — so the button:
//   - is absent entirely while the brief-prototype map is still loading
//     (no "Generate prototype" flash that flips after the fetch lands),
//   - is absent when the insight has a PRD but no ready prototype,
//   - renders as "View prototype" only once the map confirms a ready
//     prototype, and clicking it navigates to the prototype canvas,
//   - navigates via prototype.prd_id (the PRD the prototype is actually
//     attached to) when that differs from the insight's newest PRD (a PRD
//     regenerated after the prototype was built).
//
// Replaces ChatScreen.prototype-generate.dom.test.tsx, which covered the
// removed generate-from-chat flow (GenerateModal/loading-overlay wiring).
// Harness mirrors ChatScreen.insight-message.dom.test.tsx's localStorage
// restore (the simplest way to land a tab in the insight/PRD-bound state).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
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

const { pushSpy } = vi.hoisted(() => ({ pushSpy: vi.fn() }))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushSpy, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(""),
}))

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
    },
  }
})

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
  loadPrdById: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
}))
vi.mock("../../../../lib/runEvidenceGeneration", () => ({
  runEvidenceGeneration: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
  resumeEvidenceGeneration: vi.fn(),
  loadEvidenceByInsight: vi.fn().mockResolvedValue(null),
}))
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn().mockResolvedValue({
    answer: "canned", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
  }),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn().mockReturnValue(null),
  AskCancelledError: class AskCancelledError extends Error {},
}))
vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({ runStatus: { status: "no_runs" }, isTriggering: false, showCompleted: false, triggerRun: vi.fn() }),
}))

vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({ loading: false, profile: null, workspace: { id: 7, design_source: null }, refresh: async () => {} }),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))
vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))

vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    currentScreen: "chat",
    goTo: vi.fn(),
    setAIBarValue: vi.fn(),
    expandAiPanel: vi.fn(),
    pendingSearchHandoff: null,
    setPendingSearchHandoff: vi.fn(),
    pendingOndemandDraft: null,
    setPendingOndemandDraft: vi.fn(),
    pendingChatHandoff: null,
    setPendingChatHandoff: vi.fn(),
    pendingPrdTab: null,
    setPendingPrdTab: vi.fn(),
    openPrdTab: vi.fn(),
    showToast: vi.fn(),
    openContentPanel: vi.fn(),
    closeContentPanel: vi.fn(),
    contentPanelTab: null,
  }),
}))

// The brief→prototype map is the CTA's only source of truth.
const { protoMap, mapState } = vi.hoisted(() => ({ protoMap: new Map<number, unknown>(), mapState: { loading: false } }))
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: protoMap, loading: mapState.loading, error: false, refetch: vi.fn() }),
}))

import { prototypePath } from "../../../../lib/routes"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

const PRD_ID = 796

function seedRestoredTab() {
  sessionStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([
    { id: "tab-reload", title: "PRD · Enterprise expansion is stalled", dbConvId: null, briefMeta: { briefId: 7, insightIndex: 0 } },
  ]))
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-reload")
}

function seedMapEntry(prototype: unknown) {
  protoMap.set(0, {
    insight_index: 0,
    prd_id: PRD_ID,
    prd_title: "Enterprise expansion is stalled",
    prototype,
  })
}

function renderChatScreen() {
  return render(
    <ContentProvider>
      <ChatScreen />
    </ContentProvider>,
  )
}

const insightMsg = () => screen.getByTestId("chat-insight-msg")

beforeEach(() => {
  localStorage.clear()
  protoMap.clear()
  mapState.loading = false
})

afterEach(() => {
  cleanup()
  localStorage.clear()
  protoMap.clear()
  vi.clearAllMocks()
})

// The chat's 2nd action button is ALWAYS the prototype trigger: "Generate
// Prototype" until a ready prototype exists (disabled until a PRD is in scope,
// since a prototype is built FROM a PRD), then "View Prototype" which navigates.
const protoBtn = () => within(insightMsg()).getByTestId("chat-prototype-cta")

describe("ChatScreen — prototype button: Generate until a prototype is ready", () => {
  it("is present but DISABLED while the map is still loading (no PRD known yet)", async () => {
    seedRestoredTab()
    mapState.loading = true
    await act(async () => { renderChatScreen() })

    const btn = protoBtn() as HTMLButtonElement
    expect(btn).toBeTruthy()
    expect(btn.disabled).toBe(true)
    expect(btn.textContent).toBe("Generate Prototype")
  })

  it("reads 'Generate Prototype' and is ENABLED when the insight has a PRD but no ready prototype", async () => {
    seedRestoredTab()
    seedMapEntry(null) // PRD exists (prd_id), no prototype
    await act(async () => { renderChatScreen() })

    const btn = protoBtn() as HTMLButtonElement
    expect(btn.disabled).toBe(false)
    expect(btn.textContent).toBe("Generate Prototype")
    expect(within(insightMsg()).queryByRole("button", { name: "View Prototype" })).toBeNull()
  })
})

describe("ChatScreen — View Prototype appears only when ready, and navigates", () => {
  it("navigates to the prototype canvas", async () => {
    seedRestoredTab()
    seedMapEntry({ ready: true, preview_image_url: null })
    await act(async () => { renderChatScreen() })

    const btn = within(insightMsg()).getByRole("button", { name: "View Prototype" })
    fireEvent.click(btn)
    expect(pushSpy).toHaveBeenCalledWith(prototypePath(PRD_ID))
  })

  it("uses the prototype's OWN prd after a regeneration", async () => {
    const OLD_PRD_ID = 700
    seedRestoredTab()
    // The insight's newest PRD is PRD_ID, but the ready prototype is attached
    // to the older PRD it was generated against.
    seedMapEntry({ ready: true, preview_image_url: null, prd_id: OLD_PRD_ID })
    await act(async () => { renderChatScreen() })

    const btn = within(insightMsg()).getByRole("button", { name: "View Prototype" })
    fireEvent.click(btn)
    expect(pushSpy).toHaveBeenCalledWith(prototypePath(OLD_PRD_ID))
  })
})

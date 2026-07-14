// @vitest-environment jsdom
//
// ChatScreen's PRD-tab "Generate prototype" flow — the migration onto the
// shared useGeneratePrototype() hook (the tab's case 2: PRD exists, no
// prototype yet). This is the surface the reported bug actually reproduces
// through (brief card → View PRD → new PRD tab → "Generate prototype"),
// unlike BriefChat's per-finding-card flow, which already used the hook.
//
// Covers:
//   - clicking the tab's "Generate prototype" button opens the shared
//     GenerateModal wired to THAT tab's prdId,
//   - once GenerateModal fires onGenStart, the mounted GenerationLoadingScreen
//     receives working onCancel + onNotifyWhenReady (both were undefined
//     before this ticket, since ChatScreen never passed them),
//   - the loading overlay's Cancel affordance dismisses immediately with no
//     toast/navigation at that moment,
//   - the Notify affordance dismisses the overlay, shows the processing
//     toast, and dispatches da:generating carrying the in-flight id,
//   - after Notify, the still-mounted GenerateModal's onGenDone shows a
//     persistent, actionable completion toast rather than silently
//     navigating.
//
// Mirrors BriefChat.prototype-generate.dom.test.tsx's mocking convention
// (GenerateModal/GenerationLoadingScreen mocked to capture props directly,
// NavigationContext mocked so showToast can be asserted precisely) combined
// with ChatScreen.insight-message.dom.test.tsx's localStorage-restore harness
// (the simplest way to land a tab in the insight/PRD-bound state without
// driving a live PRD generation).
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
  runPrdGenerationFromBacklog: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
  loadPrdById: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
}))
vi.mock("../../../../lib/runEvidenceGeneration", () => ({
  runEvidenceGeneration: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
  resumeEvidenceGeneration: vi.fn(),
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

// NavigationContext mocked directly (rather than the real provider) so
// showToast — called by BOTH ChatScreen and the shared hook internally — can
// be spied on precisely. Nothing in this render tree mounts a real toast UI.
const { showToast } = vi.hoisted(() => ({ showToast: vi.fn() }))
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
    showToast,
    openContentPanel: vi.fn(),
    closeContentPanel: vi.fn(),
    contentPanelTab: null,
  }),
}))

// The brief→prototype map drives the prototype CTA's View/Generate label —
// same source of truth `chatInsightState` reads from.
const { protoMap, mapState } = vi.hoisted(() => ({ protoMap: new Map<number, unknown>(), mapState: { loading: false } }))
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: protoMap, loading: mapState.loading, error: false, refetch: vi.fn() }),
}))

// Captures the latest props on every render so a test can invoke
// onGenStart/onKickoff/onCancel/onNotifyWhenReady/onGenDone directly, without
// a real backend or SSE stream — same pattern as
// BriefChat.prototype-generate.dom.test.tsx / useGeneratePrototype.test.tsx.
let latestGenerateProps: Record<string, unknown> | null = null
let latestLoadingProps: Record<string, unknown> | null = null

vi.mock("../../../design-agent/GenerateModal", () => ({
  GenerateModal: (props: Record<string, unknown>) => {
    latestGenerateProps = props
    if (!props.open) return null
    return (
      <div role="dialog" aria-label="Generate prototype" data-prd-id={String(props.prdId)}>
        Generate prototype dialog for prd {String(props.prdId)}
      </div>
    )
  },
}))
vi.mock("../../../design-agent/GenerationLoadingScreen", () => ({
  GenerationLoadingScreen: (props: Record<string, unknown>) => {
    latestLoadingProps = props
    if (!props.open) return null
    return <div data-testid="loading-overlay">Generating…</div>
  },
}))

import { prototypePath } from "../../../../lib/routes"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

const PRD_ID = 796

function seedRestoredTab() {
  localStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([
    { id: "tab-reload", title: "PRD · Enterprise expansion is stalled", dbConvId: null, briefMeta: { briefId: 7, insightIndex: 0 } },
  ]))
  localStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-reload")
  // Case 2 — PRD exists, no prototype yet.
  protoMap.set(0, {
    insight_index: 0,
    prd_id: PRD_ID,
    prd_title: "Enterprise expansion is stalled",
    prototype: null,
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

async function openAndArmGeneration() {
  seedRestoredTab()
  await act(async () => { renderChatScreen() })

  const btn = within(insightMsg()).getByRole("button", { name: "Generate prototype" })
  fireEvent.click(btn)
  await screen.findByRole("dialog", { name: "Generate prototype" })

  await act(async () => {
    ;(latestGenerateProps!.onGenStart as (ctx?: unknown) => void)()
    ;(latestGenerateProps!.onKickoff as (id: number) => void)(555)
  })
  expect(await screen.findByTestId("loading-overlay")).toBeTruthy()
  // Isolate the generation-flow assertions below from the unrelated
  // auto-restore PRD load's own toast (loadPrdById is stubbed to fail in this
  // file since PRD loading itself is out of scope for this ticket).
  showToast.mockClear()
}

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
  latestGenerateProps = null
  latestLoadingProps = null
})

describe("ChatScreen — Generate prototype opens the shared modal for the tab's PRD", () => {
  it("test_chatscreen_generate_prototype_opens_shared_modal_for_prd", async () => {
    seedRestoredTab()
    await act(async () => { renderChatScreen() })

    const btn = within(insightMsg()).getByRole("button", { name: "Generate prototype" })
    fireEvent.click(btn)

    const dialog = await screen.findByRole("dialog", { name: "Generate prototype" })
    expect(dialog.getAttribute("data-prd-id")).toBe(String(PRD_ID))
    expect(latestGenerateProps?.prdId).toBe(PRD_ID)
    expect(latestGenerateProps?.open).toBe(true)
  })
})

describe("ChatScreen — loading overlay gains working Cancel + Notify (the migrated gap)", () => {
  it("test_chatscreen_loading_overlay_has_cancel_and_notify", async () => {
    await openAndArmGeneration()

    expect(typeof latestLoadingProps?.onCancel).toBe("function")
    expect(typeof latestLoadingProps?.onNotifyWhenReady).toBe("function")
  })
})

describe("ChatScreen — Cancel dismisses without toast or navigation", () => {
  it("test_chatscreen_cancel_dismisses_without_toast_or_navigation", async () => {
    await openAndArmGeneration()

    await act(async () => {
      ;(latestLoadingProps!.onCancel as () => void)()
    })

    expect(screen.queryByTestId("loading-overlay")).toBeNull()
    expect(showToast).not.toHaveBeenCalled()
    expect(pushSpy).not.toHaveBeenCalled()
  })
})

describe("ChatScreen — Notify dismisses and shows the processing toast", () => {
  it("test_chatscreen_notify_dismisses_and_shows_processing_toast", async () => {
    await openAndArmGeneration()

    const generatingEvents: CustomEvent[] = []
    const onGenerating = (e: Event) => generatingEvents.push(e as CustomEvent)
    window.addEventListener("da:generating", onGenerating)

    await act(async () => {
      ;(latestLoadingProps!.onNotifyWhenReady as () => void)()
    })

    expect(screen.queryByTestId("loading-overlay")).toBeNull()
    expect(showToast).toHaveBeenCalledWith(
      "Prototype is processing",
      "We'll let you know when it's ready.",
    )
    expect(generatingEvents.length).toBe(1)
    expect(generatingEvents[0].detail).toEqual({ prototypeId: 555 })

    window.removeEventListener("da:generating", onGenerating)
  })
})

describe("ChatScreen — notify then completion shows an actionable toast, not a silent navigate", () => {
  it("test_chatscreen_notify_then_completion_shows_actionable_toast", async () => {
    await openAndArmGeneration()

    const doneEvents: Event[] = []
    const onDone = (e: Event) => doneEvents.push(e)
    window.addEventListener("da:generating-done", onDone)

    await act(async () => {
      ;(latestLoadingProps!.onNotifyWhenReady as () => void)()
    })
    showToast.mockClear()
    expect(pushSpy).not.toHaveBeenCalled()

    const proto = { id: 555, status: "ready", bundle_url: "/bundle" }
    await act(async () => {
      ;(latestGenerateProps!.onGenDone as (result?: unknown) => void)({ ok: true, prototype: proto })
    })

    expect(pushSpy).not.toHaveBeenCalled()
    expect(showToast).toHaveBeenCalledTimes(1)
    const [title, sub, action, opts] = showToast.mock.calls[0]
    expect(title).toBe("Prototype ready")
    expect(sub).toBe("Your prototype finished generating.")
    expect(action).toBe("Open")
    expect(opts).toMatchObject({ persist: true })
    expect(typeof opts.onAction).toBe("function")
    expect(doneEvents.length).toBe(1)

    opts.onAction()
    expect(pushSpy).toHaveBeenCalledWith(prototypePath(PRD_ID))

    window.removeEventListener("da:generating-done", onDone)
  })
})

// @vitest-environment jsdom
//
// BriefChat finding-card "Generate prototype" flow — the migration onto the
// shared useGeneratePrototype() hook (case 2 of cardPreview: PRD exists, no
// prototype yet). Covers:
//   - clicking a specific card's "Generate prototype" action opens the shared
//     GenerateModal wired to THAT card's prdId,
//   - a DIFFERENT card's label (driven entirely by useBriefPrototypeMap's
//     batch result, never by the hook's own `cta`) is unaffected by another
//     card's in-flight generation — proving `listenForCrossSurfaceGenerating`
//     was correctly left off for this host,
//   - the loading overlay's Cancel affordance dismisses immediately with no
//     toast/navigation at that moment (the hook's "soft dismiss" contract —
//     no true-abort call exists at this layer),
//   - the "Notify me when ready" affordance dismisses the overlay, shows the
//     processing toast, and — once the background generation later resolves
//     — surfaces the hook's persistent, actionable completion toast (not a
//     silent auto-navigate).
//
// Mirrors the mocking convention of the adjacent BriefChat.test.tsx (api /
// generation-runner / pipeline / workspace / useBriefPrototypeMap stubbed so
// mounting hits no network); additionally mocks GenerateModal +
// GenerationLoadingScreen so the hook's callback props can be driven directly
// (same pattern PrdPanelContent.viewprototype.test.tsx and
// useGeneratePrototype.test.tsx use), without needing a real backend or SSE
// stream. NavigationContext is mocked directly (rather than mounting the real
// provider) so `showToast` calls — including the ones the hook itself makes
// internally — can be asserted on precisely; nothing in this tree renders a
// real toast UI to query against.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const { pushSpy } = vi.hoisted(() => ({ pushSpy: vi.fn() }))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushSpy, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/brief",
}))

vi.mock("../../../lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 0
    body: unknown = null
  },
  askApi: { ask: vi.fn() },
  briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
}))
vi.mock("../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
}))
vi.mock("../../../lib/runEvidenceGeneration", () => ({
  runEvidenceGeneration: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
}))
vi.mock("../../../lib/runMultiAgentGeneration", () => ({
  runMultiAgentGeneration: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
}))
vi.mock("../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: { status: "no_runs" },
    isTriggering: false,
    showCompleted: false,
    triggerRun: vi.fn(),
  }),
}))

vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    loading: false,
    profile: null,
    workspace: { id: 7, design_source: null },
    refresh: async () => {},
  }),
}))

// Mocked directly (not the real provider) so `showToast` — called by BOTH
// BriefChat itself and the shared hook internally — can be spied on
// precisely. Nothing in this render tree mounts a real toast UI to query.
const { showToast } = vi.hoisted(() => ({ showToast: vi.fn() }))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    aiBarValue: "",
    setAIBarValue: vi.fn(),
    openContentPanel: vi.fn(),
    openPrdTab: vi.fn(),
    showToast,
    setPendingChatHandoff: vi.fn(),
  }),
}))

// mapEntries seeds useBriefPrototypeMap's batch result — the SAME source of
// truth cardPreview and each card's label both read from. hasPrd true +
// prototype null = case 2 (PRD exists, no prototype yet) → "Generate
// prototype".
const { mapEntries, mapState } = vi.hoisted(() => ({
  mapEntries: new Map<number, unknown>(),
  mapState: { loading: false },
}))
vi.mock("../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({
    entriesByInsight: mapEntries,
    loading: mapState.loading,
    error: false,
    refetch: vi.fn(),
  }),
}))

// Captures the latest props on every render (even while the mock itself
// renders nothing) so a test can invoke onGenStart/onKickoff/onGenDone
// directly, without a real backend or SSE stream — the same pattern
// useGeneratePrototype.test.tsx and PrdPanelContent.viewprototype.test.tsx use.
let latestGenerateProps: Record<string, unknown> | null = null
let latestLoadingProps: Record<string, unknown> | null = null

vi.mock("../../design-agent/GenerateModal", () => ({
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
vi.mock("../../design-agent/GenerationLoadingScreen", () => ({
  GenerationLoadingScreen: (props: Record<string, unknown>) => {
    latestLoadingProps = props
    if (!props.open) return null
    return <div data-testid="loading-overlay">Generating…</div>
  },
}))

import { ContentProvider, useContent } from "../../../context/ContentContext"
import type { BriefV2CompactFinding, BriefV2HeroFinding, BriefV2State } from "../../../lib/brief-v2-adapter"
import { prototypePath } from "../../../lib/routes"
import { BriefChat } from "../BriefChat"

function baseFinding(detailKey: string, title: string) {
  return {
    detailKey,
    actionAccent: "fix" as const,
    actionLabel: "FIX",
    tagType: "fix" as const,
    tagLabel: "FIX NOW",
    skillType: "reliability" as const,
    skillAccent: "#c0473c",
    skillLabel: "Reliability",
    ctas: [
      { label: "View PRD", style: "primary" },
      { label: "View prototype", style: "ghost" },
    ],
    category: "RETENTION",
    priority: "P0",
    confidence: 0.82,
    prototypeable: true,
    title,
    body: `Body copy for ${title}.`,
    metricHighlight: "41% completion",
    fromSources: ["Amplitude"],
    statTiles: [],
    chart: null,
    convergence: [],
    secondaryCtaLabel: "Generate PRD →",
    secondaryCtaBehavior: "generate_prd" as const,
    askQuestion: "Tell me more",
  }
}

// Two prototypeable findings — insight 0 (hero) and insight 1 (supporting) —
// each with its OWN prdId in the seeded map, so a test can drive one card's
// generation while asserting the other's label is untouched.
const HERO: BriefV2HeroFinding = { kind: "hero", ...baseFinding("finding-0", "Day-30 retention is slipping"), quote: null }
const SUPPORTING: BriefV2CompactFinding = {
  kind: "compact",
  ...baseFinding("finding-1", "Onboarding email open-rate slipping"),
  fromSources: [],
  extraConvergenceCount: 0,
}

const BRIEF: BriefV2State = {
  headline: "This week",
  weekOf: "2026-06-08",
  company: "Acme Health",
  productArea: "Onboarding",
  kpiTiles: [],
  hero: HERO,
  supporting: [SUPPORTING],
  sourcesLine: "Zendesk · Amplitude",
  insufficientEvidence: false,
  emptyReason: null,
}

function InjectBrief() {
  const { setContent } = useContent()
  React.useEffect(() => {
    setContent({
      briefV2: BRIEF,
      userName: "Apurva Jain",
      briefDetails: {
        "finding-0": { meta: { briefId: 1, insightIndex: 0 } },
        "finding-1": { meta: { briefId: 1, insightIndex: 1 } },
      } as never,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setContent])
  return null
}

function renderBrief() {
  return render(
    <ContentProvider>
      <InjectBrief />
      <BriefChat />
    </ContentProvider>,
  )
}

function cardFor(title: string): HTMLElement {
  const heading = screen.getByText(title)
  const card = heading.closest("article.fc") as HTMLElement | null
  if (!card) throw new Error(`No card article found for "${title}"`)
  return card
}

// Seeds both cards' map entries (case 2 — PRD exists, no prototype), renders,
// clicks card 0's ("HERO", prdId 42) "Generate prototype" action, then arms a
// live in-flight generation via the real onGenStart/onKickoff wiring (mirrors
// the real GenerateModal's own kickoff sequence — see GenerateModal.tsx line
// 678, which calls onClose() then onGenStart() in one handler). Leaves the
// loading overlay open with prototypeId 991 armed, ready for a test to invoke
// onCancel/onNotifyWhenReady/onGenDone directly on the captured mock props.
async function openAndArmCardGeneration() {
  mapEntries.set(0, { insight_index: 0, prd_id: 42, prd_title: "Retention PRD", prototype: null })
  mapEntries.set(1, { insight_index: 1, prd_id: 43, prd_title: "Onboarding PRD", prototype: null })
  await act(async () => { renderBrief() })

  fireEvent.click(within(cardFor(HERO.title)).getByRole("button", { name: "Generate prototype" }))
  await screen.findByRole("dialog", { name: "Generate prototype" })

  await act(async () => {
    ;(latestGenerateProps!.onClose as () => void)()
    ;(latestGenerateProps!.onGenStart as (ctx?: unknown) => void)()
    ;(latestGenerateProps!.onKickoff as (id: number) => void)(991)
  })
  expect(await screen.findByTestId("loading-overlay")).toBeTruthy()
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  mapEntries.clear()
  mapState.loading = false
  latestGenerateProps = null
  latestLoadingProps = null
})

describe("BriefChat finding card — Generate prototype opens the shared modal for the clicked card", () => {
  it("test_brief_chat_card_generate_opens_modal_for_clicked_prd", async () => {
    mapEntries.set(0, { insight_index: 0, prd_id: 42, prd_title: "Retention PRD", prototype: null })
    mapEntries.set(1, { insight_index: 1, prd_id: 43, prd_title: "Onboarding PRD", prototype: null })
    await act(async () => { renderBrief() })

    const heroCard = cardFor(HERO.title)
    const btn = within(heroCard).getByRole("button", { name: "Generate prototype" })
    fireEvent.click(btn)

    const dialog = await screen.findByRole("dialog", { name: "Generate prototype" })
    // The modal opened for THIS card's prdId (42), not the other card's (43).
    expect(dialog.getAttribute("data-prd-id")).toBe("42")
    expect(latestGenerateProps?.prdId).toBe(42)
  })
})

describe("BriefChat finding card — batch-map isolation (listenForCrossSurfaceGenerating correctly omitted)", () => {
  it("test_brief_chat_other_cards_unaffected_by_inflight_generation", async () => {
    mapEntries.set(0, { insight_index: 0, prd_id: 42, prd_title: "Retention PRD", prototype: null })
    mapEntries.set(1, { insight_index: 1, prd_id: 43, prd_title: "Onboarding PRD", prototype: null })
    await act(async () => { renderBrief() })

    // Kick off card 0's generation (opens the modal, then starts the overlay).
    fireEvent.click(within(cardFor(HERO.title)).getByRole("button", { name: "Generate prototype" }))
    await screen.findByRole("dialog", { name: "Generate prototype" })
    await act(async () => {
      ;(latestGenerateProps!.onGenStart as (ctx?: unknown) => void)()
    })
    // Card 0's generation is now in flight (the shared loading overlay is up).
    expect(await screen.findByTestId("loading-overlay")).toBeTruthy()

    // Card 1's label is STILL driven only by the batch map (prototype: null,
    // untouched) — it must still read "Generate prototype", never flip to a
    // "generating" state just because a DIFFERENT card started a run. This is
    // the proof that `listenForCrossSurfaceGenerating` was correctly left off:
    // a single BriefChat-level hook instance has no notion of "the active
    // card", so if it listened for the unscoped da:generating signal, EVERY
    // card would incorrectly reflect card 0's in-flight run.
    const supportingCard = cardFor(SUPPORTING.title)
    expect(within(supportingCard).getByRole("button", { name: "Generate prototype" })).toBeTruthy()
    expect(within(supportingCard).queryByRole("button", { name: /generating/i })).toBeNull()
  })
})

describe("BriefChat finding card — generation overlay Cancel (soft dismiss)", () => {
  it("test_brief_chat_generation_overlay_has_cancel", async () => {
    await openAndArmCardGeneration()
    expect(typeof latestLoadingProps?.onCancel).toBe("function")

    await act(async () => {
      ;(latestLoadingProps!.onCancel as () => void)()
    })

    // The overlay is dismissed immediately…
    expect(screen.queryByTestId("loading-overlay")).toBeNull()
    // …with NO toast and NO navigation at the moment of clicking Cancel. There
    // is no true-abort endpoint at this layer (only PrototypeRoute's own,
    // out-of-scope state machine calls designAgentApi.cancel) — Cancel is a
    // soft, local dismiss, not an abort call. (If the background generation
    // later resolves, the SAME persistent completion toast the notify path
    // uses will fire — that convergent behavior is exercised by the hook's
    // own test suite, not re-asserted here.)
    expect(showToast).not.toHaveBeenCalled()
    expect(pushSpy).not.toHaveBeenCalled()
  })
})

describe("BriefChat finding card — generation overlay Notify", () => {
  it("test_brief_chat_generation_overlay_has_notify", async () => {
    await openAndArmCardGeneration()
    expect(typeof latestLoadingProps?.onNotifyWhenReady).toBe("function")

    const generatingEvents: CustomEvent[] = []
    const notifyGenerationEvents: CustomEvent[] = []
    const onGenerating = (e: Event) => generatingEvents.push(e as CustomEvent)
    const onNotifyGeneration = (e: Event) => notifyGenerationEvents.push(e as CustomEvent)
    window.addEventListener("da:generating", onGenerating)
    window.addEventListener("da:notify-generation", onNotifyGeneration)

    await act(async () => {
      ;(latestLoadingProps!.onNotifyWhenReady as () => void)()
    })

    // Overlay dismissed…
    expect(screen.queryByTestId("loading-overlay")).toBeNull()
    // …the processing toast shown…
    expect(showToast).toHaveBeenCalledWith(
      "Prototype is processing",
      "We'll let you know when it's ready.",
    )
    // …da:generating dispatched (armed prototypeId 991, from openAndArmCardGeneration)…
    expect(generatingEvents.length).toBe(1)
    expect(generatingEvents[0].detail).toEqual({ prototypeId: 991 })
    // …and NOT the hand-off event — BriefChat stays mounted, it never unmounts
    // on notify, so it never needs useGenerationNotify's resume-on-remount path.
    expect(notifyGenerationEvents.length).toBe(0)

    window.removeEventListener("da:generating", onGenerating)
    window.removeEventListener("da:notify-generation", onNotifyGeneration)
  })
})

describe("BriefChat finding card — notify-then-completion", () => {
  it("test_brief_chat_notify_then_completion_shows_actionable_toast", async () => {
    await openAndArmCardGeneration()

    const doneEvents: Event[] = []
    const onDone = (e: Event) => doneEvents.push(e)
    window.addEventListener("da:generating-done", onDone)

    await act(async () => {
      ;(latestLoadingProps!.onNotifyWhenReady as () => void)()
    })
    // Isolate the completion toast from the processing toast asserted above.
    showToast.mockClear()
    expect(pushSpy).not.toHaveBeenCalled()

    const proto = { id: 991, status: "ready", bundle_url: "/bundle" }
    await act(async () => {
      ;(latestGenerateProps!.onGenDone as (result?: unknown) => void)({ ok: true, prototype: proto })
    })

    // The mounted GenerateModal's onGenDone still fires on the background
    // generation's real completion (BriefChat never unmounted) — it does NOT
    // silently auto-navigate…
    expect(pushSpy).not.toHaveBeenCalled()
    // …instead a persistent, actionable toast appears…
    expect(showToast).toHaveBeenCalledTimes(1)
    const [title, sub, action, opts] = showToast.mock.calls[0]
    expect(title).toBe("Prototype ready")
    expect(sub).toBe("Your prototype finished generating.")
    expect(action).toBe("Open")
    expect(opts).toMatchObject({ persist: true })
    expect(typeof opts.onAction).toBe("function")
    // …and da:generating-done dispatches so any cross-surface listener stops
    // tracking this run.
    expect(doneEvents.length).toBe(1)

    // Only NOW — on the user clicking "Open" in the toast — does it navigate,
    // to card 0's prdId (42), matching the hook's default onSuccess-less path.
    opts.onAction()
    expect(pushSpy).toHaveBeenCalledWith(prototypePath(42))

    window.removeEventListener("da:generating-done", onDone)
  })
})

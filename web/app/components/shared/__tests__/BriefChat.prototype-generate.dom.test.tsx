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
//     was correctly left off for this host.
//
// Mirrors the mocking convention of the adjacent BriefChat.test.tsx (api /
// generation-runner / pipeline / workspace / useBriefPrototypeMap stubbed so
// mounting hits no network); additionally mocks GenerateModal +
// GenerationLoadingScreen so the hook's callback props can be driven directly
// (same pattern PrdPanelContent.viewprototype.test.tsx and
// useGeneratePrototype.test.tsx use), without needing a real backend or SSE
// stream.
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
import { NavigationProvider } from "../../../context/NavigationContext"
import type { BriefV2CompactFinding, BriefV2HeroFinding, BriefV2State } from "../../../lib/brief-v2-adapter"
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
    <NavigationProvider>
      <ContentProvider>
        <InjectBrief />
        <BriefChat />
      </ContentProvider>
    </NavigationProvider>,
  )
}

function cardFor(title: string): HTMLElement {
  const heading = screen.getByText(title)
  const card = heading.closest("article.fc") as HTMLElement | null
  if (!card) throw new Error(`No card article found for "${title}"`)
  return card
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

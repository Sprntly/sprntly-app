// @vitest-environment jsdom
//
// BriefChat finding-card prototype affordance — GENERATE REMOVED.
//
// The weekly brief no longer offers "Generate prototype" on finding cards
// (prototype generation lives in the PRD flow). The card renders a prototype
// button ONLY when a prototype already exists for the insight (prototypeReady,
// DB-backed via useBriefPrototypeMap), labeled "View prototype", and clicking
// it opens the in-tab canvas at /prototype?prd=<id> — never the generate
// modal. The generate-modal machinery itself (useGeneratePrototype /
// GenerateModal / GenerationLoadingScreen) stays mounted for the composer
// command flow and is exercised by the hook's own test suite.
//
// Mirrors the mocking convention of the adjacent BriefChat.test.tsx (api /
// generation-runner / pipeline / workspace / useBriefPrototypeMap stubbed so
// mounting hits no network); GenerateModal is mocked with a props capture so
// we can assert it is NEVER opened from a card.
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
// truth cardPreview and each card's button both read from.
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

// Captures the latest props on every render so tests can assert the modal is
// never opened by a card click.
let latestGenerateProps: Record<string, unknown> | null = null

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

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  mapEntries.clear()
  mapState.loading = false
  latestGenerateProps = null
})

describe("BriefChat finding card — Generate prototype is removed", () => {
  it("renders NO prototype button when no prototype is built, even with a PRD (the old case-2 generate entry point)", async () => {
    // PRD exists, prototype null — this used to render "Generate prototype".
    mapEntries.set(0, { insight_index: 0, prd_id: 42, prd_title: "Retention PRD", prototype: null })
    mapEntries.set(1, { insight_index: 1, prd_id: 43, prd_title: "Onboarding PRD", prototype: null })
    await act(async () => { renderBrief() })

    for (const f of [HERO, SUPPORTING]) {
      expect(within(cardFor(f.title)).queryByRole("button", { name: /prototype/i })).toBeNull()
    }
    expect(screen.queryByText("Generate prototype")).toBeNull()
    // The generate modal is mounted (composer command flow) but never open.
    expect(screen.queryByRole("dialog", { name: "Generate prototype" })).toBeNull()
    expect(latestGenerateProps?.open).toBeFalsy()
  })
})

describe("BriefChat finding card — View prototype (already built) stays reachable", () => {
  it("renders 'View prototype' only for the insight with a built prototype, and clicking navigates to the canvas (no modal)", async () => {
    mapEntries.set(0, {
      insight_index: 0,
      prd_id: 42,
      prd_title: "Retention PRD",
      prototype: { ready: true, preview_image_url: null },
    })
    mapEntries.set(1, { insight_index: 1, prd_id: 43, prd_title: "Onboarding PRD", prototype: null })
    await act(async () => { renderBrief() })

    // Only the built insight offers the affordance, labeled View (never Generate).
    const viewBtn = within(cardFor(HERO.title)).getByRole("button", { name: "View prototype" })
    expect(within(cardFor(SUPPORTING.title)).queryByRole("button", { name: /prototype/i })).toBeNull()

    fireEvent.click(viewBtn)

    // Straight to the in-tab canvas for THIS card's PRD — no generate modal.
    expect(pushSpy).toHaveBeenCalledWith(prototypePath(42))
    expect(screen.queryByRole("dialog", { name: "Generate prototype" })).toBeNull()
  })
})

// @vitest-environment jsdom
//
// BriefChat finding-card DOM tests covering the two card fixes:
//
//   A. Dismiss / restore a finding card.
//      - Clicking the card's "Dismiss" (X) control greys the card out in place
//        (adds `fc--dismissed`) and hides the heavy detail/viz (the mini chart,
//        the body copy, the action buttons) — the finding is NOT removed.
//      - Clicking the dismissed card (or its restore control) un-greys it back
//        to the full card.
//      - Dismiss is per-card: dismissing one leaves the other untouched.
//      - The dismissed state persists to localStorage (keyed by the brief) so a
//        grey-out survives a remount within the session.
//
//   B. The card chart renders the REAL chart data carried by the insight.
//      - A fixture finding with a known inline chart (labels + values) renders
//        bars whose titles encode those exact "label: value" pairs and axis
//        ticks derived from the labels — not a hardcoded placeholder.
//
// jsdom is opted into per-file (the global vitest config stays node-env), the
// same way the ApproveModal DOM test does. Native DOM matchers only (no
// jest-dom). next/navigation is stubbed so NavigationProvider mounts; the api /
// generation / pipeline modules are mocked so nothing hits the network (these
// tests never trigger an async flow — they only exercise card render + dismiss).
import * as React from "react"
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses the
// classic runtime, so expose React globally (repo test convention).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// NavigationProvider depends on next/navigation. Stub the router/pathname so the
// provider mounts without a Next router context.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/brief",
}))

// BriefChat imports the api + generation runners + pipeline hook at module load.
// None of these tests trigger an async flow, so the mocks just need the named
// exports to exist (importable) and the pipeline hook to return an idle status.
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

// BriefChat now reads the active workspace via useWorkspace(), which throws
// outside a WorkspaceProvider. These card-render tests never exercise workspace
// behaviour, so mock the hook to a stable idle workspace rather than dragging
// the real provider (and its auth/supabase deps) into the harness.
vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    loading: false,
    profile: null,
    workspace: null,
    refresh: async () => {},
  }),
}))

import { ContentProvider, useContent } from "../../../context/ContentContext"
import { NavigationProvider } from "../../../context/NavigationContext"
import type {
  BriefV2CompactFinding,
  BriefV2HeroFinding,
  BriefV2InlineChart,
  BriefV2State,
} from "../../../lib/brief-v2-adapter"
import { BriefChat } from "../BriefChat"

// ── Fixtures ────────────────────────────────────────────────────────────────
// A real inline chart with KNOWN labels + values — the assertions below look
// for these exact pairs in the rendered bars (proving the card renders THIS
// data, not a placeholder shape).
const HERO_CHART: BriefV2InlineChart = {
  kind: "bar",
  title: "Handoff completion by site",
  subtitle: "70% threshold",
  data: [
    { label: "Riverside General", value: 41 },
    { label: "Mercy Health", value: 58 },
    { label: "Coastal Care", value: 88 },
  ],
}

function baseFinding(detailKey: string, title: string, chart: BriefV2InlineChart | null) {
  return {
    detailKey,
    actionAccent: "fix" as const,
    actionLabel: "FIX",
    tagType: "fix" as const,
    tagLabel: "FIX NOW",
    category: "RETENTION",
    priority: "P0",
    confidence: 0.82,
    title,
    body: `Body copy for ${title} that should disappear when the card is dismissed.`,
    metricHighlight: "41% completion",
    statTiles: [{ value: "41%", label: "completion", tone: "negative" as const }],
    chart,
    convergence: [],
    secondaryCtaLabel: "Generate PRD →",
    secondaryCtaBehavior: "generate_prd" as const,
    askQuestion: "Tell me more",
  }
}

const HERO: BriefV2HeroFinding = {
  kind: "hero",
  ...baseFinding("something_wrong-0", "First-handoff completion is dropping", HERO_CHART),
  quote: null,
}

const SUPPORTING: BriefV2CompactFinding = {
  kind: "compact",
  ...baseFinding("something_wrong-1", "Onboarding email open-rate slipping", null),
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
}

// Injects the fixture brief into ContentContext on mount so BriefChat renders it.
function InjectBrief({ brief }: { brief: BriefV2State }) {
  const { setContent } = useContent()
  React.useEffect(() => {
    setContent({ briefV2: brief, userName: "Apurva Jain" })
  }, [setContent, brief])
  return null
}

function renderBrief() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(
        ContentProvider,
        null,
        React.createElement(InjectBrief, { brief: BRIEF }),
        React.createElement(BriefChat),
      ),
    ),
  )
}

// The card for a given finding title (the <article class="fc ...">).
function cardFor(title: string): HTMLElement {
  const heading = screen.getByText(title)
  const card = heading.closest("article.fc") as HTMLElement | null
  if (!card) throw new Error(`No card article found for "${title}"`)
  return card
}

afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.clearAllMocks()
})

describe("BriefChat finding card — single full-system PRD button", () => {
  it("test_one_generate_prd_button: the card shows exactly one 'Generate PRD' button and no duplicate 'Generate PRD first'", async () => {
    await act(async () => {
      renderBrief()
    })

    const card = cardFor(HERO.title)
    // Exactly one PRD button — the old duplicate "Generate PRD first" is gone.
    const prdButtons = within(card).getAllByRole("button", { name: /generate prd/i })
    expect(prdButtons).toHaveLength(1)
    expect(within(card).queryByRole("button", { name: /generate prd first/i })).toBeNull()
    // The single button runs the full multi-agent system (tooltip names the suite).
    expect(prdButtons[0].getAttribute("title")).toMatch(/full system/i)
  })
})

describe("BriefChat finding card — dismiss / restore (Task A)", () => {
  it("test_dismiss_greys_card_and_hides_detail: clicking X adds fc--dismissed and removes the heavy detail", async () => {
    await act(async () => {
      renderBrief()
    })

    const card = cardFor(HERO.title)
    expect(card.className).not.toContain("fc--dismissed")
    // Full card shows the mini chart, the body copy, and the action buttons.
    expect(card.querySelector(".fc-mc")).not.toBeNull()
    expect(within(card).queryByText(/Body copy for First-handoff/)).not.toBeNull()
    expect(within(card).queryByText("View evidence")).not.toBeNull()

    // Click the per-card Dismiss control.
    fireEvent.click(within(card).getByLabelText("Dismiss finding"))

    const dismissed = cardFor(HERO.title)
    expect(dismissed.className).toContain("fc--dismissed")
    // The finding is still present (title visible) — but the detail/viz is gone.
    expect(within(dismissed).getByText(HERO.title)).not.toBeNull()
    expect(dismissed.querySelector(".fc-mc")).toBeNull()
    expect(within(dismissed).queryByText(/Body copy for First-handoff/)).toBeNull()
    expect(within(dismissed).queryByText("View evidence")).toBeNull()
    // The restore affordance is shown.
    expect(within(dismissed).getByText(/click to restore/i)).not.toBeNull()
  })

  it("test_restore_ungreys_card: clicking a dismissed card restores the full card", async () => {
    await act(async () => {
      renderBrief()
    })

    fireEvent.click(within(cardFor(HERO.title)).getByLabelText("Dismiss finding"))
    const dismissed = cardFor(HERO.title)
    expect(dismissed.className).toContain("fc--dismissed")

    // Click the greyed card body → restore.
    fireEvent.click(dismissed)

    const restored = cardFor(HERO.title)
    expect(restored.className).not.toContain("fc--dismissed")
    expect(restored.querySelector(".fc-mc")).not.toBeNull()
    expect(within(restored).queryByText("View evidence")).not.toBeNull()
  })

  it("test_dismiss_is_per_card: dismissing one finding leaves the other untouched", async () => {
    await act(async () => {
      renderBrief()
    })

    fireEvent.click(within(cardFor(HERO.title)).getByLabelText("Dismiss finding"))

    expect(cardFor(HERO.title).className).toContain("fc--dismissed")
    // The supporting card is unaffected — still a full card.
    const other = cardFor(SUPPORTING.title)
    expect(other.className).not.toContain("fc--dismissed")
    expect(within(other).queryByText("View evidence")).not.toBeNull()
  })

  it("test_dismiss_persists_to_localstorage_across_remount: a dismissal survives a fresh mount", async () => {
    const first = renderBrief()
    await act(async () => {})
    fireEvent.click(within(cardFor(HERO.title)).getByLabelText("Dismiss finding"))
    expect(cardFor(HERO.title).className).toContain("fc--dismissed")

    // Some localStorage entry now records the dismissed detailKey.
    const stored = Object.entries({ ...localStorage }).find(
      ([k, v]) => k.includes("dismissed") && String(v).includes("something_wrong-0"),
    )
    expect(stored).toBeDefined()

    // Remount from scratch — the hero card comes back already dismissed.
    first.unmount()
    cleanup()
    renderBrief()
    await act(async () => {})
    expect(cardFor(HERO.title).className).toContain("fc--dismissed")
    // The supporting card was never dismissed, so it restores as a full card.
    expect(cardFor(SUPPORTING.title).className).not.toContain("fc--dismissed")
  })
})

describe("BriefChat finding card — real chart from the insight (Task B)", () => {
  it("test_chart_renders_insight_values_not_placeholder: bars encode the fixture's label:value pairs", async () => {
    await act(async () => {
      renderBrief()
    })

    const card = cardFor(HERO.title)
    const chart = card.querySelector(".fc-mc") as HTMLElement | null
    expect(chart).not.toBeNull()

    // One bar per data point, each titled with the REAL "label: value" pair.
    const bars = Array.from(chart!.querySelectorAll(".fc-mc-bar"))
    expect(bars.length).toBe(HERO_CHART.data.length)
    const barTitles = bars.map((b) => b.getAttribute("title"))
    expect(barTitles).toContain("Riverside General: 41")
    expect(barTitles).toContain("Mercy Health: 58")
    expect(barTitles).toContain("Coastal Care: 88")

    // Axis ticks are derived from the real labels (first-letters of each word).
    const ticks = Array.from(chart!.querySelectorAll(".fc-mc-tick")).map((t) => t.textContent)
    expect(ticks).toContain("RG") // Riverside General
    expect(ticks).toContain("MH") // Mercy Health
    expect(ticks).toContain("CC") // Coastal Care

    // The reference line carries the insight's own threshold label, not a stub.
    expect(within(card).getByText("70% threshold")).not.toBeNull()
  })

  it("test_chart_fallback_from_stat_tiles_when_no_chart_hints: a finding with no chart still renders a data-driven bar", async () => {
    await act(async () => {
      renderBrief()
    })

    // SUPPORTING ships chart: null but carries a numeric stat tile (41%).
    const card = cardFor(SUPPORTING.title)
    const chart = card.querySelector(".fc-mc") as HTMLElement | null
    expect(chart).not.toBeNull()
    const barTitles = Array.from(chart!.querySelectorAll(".fc-mc-bar")).map((b) =>
      b.getAttribute("title"),
    )
    // The fallback bar is built from the stat tile's "completion: 41".
    expect(barTitles.some((t) => t?.includes("41"))).toBe(true)
  })
})

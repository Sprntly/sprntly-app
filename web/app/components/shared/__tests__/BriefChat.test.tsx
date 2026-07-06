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
// provider mounts without a Next router context. `pushSpy` is hoisted + stable so
// a test can assert the exact URL a navigation pushed (the router mock returned a
// fresh push per call before, which was unassertable).
const { pushSpy } = vi.hoisted(() => ({ pushSpy: vi.fn() }))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushSpy, replace: vi.fn(), prefetch: vi.fn() }),
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

// Mock the brief→PRD map hook so we can (a) start from an empty map (button reads
// "Generate PRD") and (b) spy on refetch — the call that lets the card flip to
// "View PRD" in place after a generation completes.
// `mapEntries` is a hoisted, mutable map so a test can seed a ready prototype
// (with a preview_image_url) and assert the card renders NO preview thumbnail.
const { refetchMapSpy, mapEntries } = vi.hoisted(() => ({
  refetchMapSpy: vi.fn(),
  mapEntries: new Map<number, unknown>(),
}))
vi.mock("../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({
    entriesByInsight: mapEntries,
    loading: false,
    error: false,
    refetch: refetchMapSpy,
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
import { BriefChat, prdCtaState, prototypeCtaLabel } from "../BriefChat"
import { runMultiAgentGeneration } from "../../../lib/runMultiAgentGeneration"
import { prototypePath } from "../../../lib/routes"
import { AGENT_NAME } from "../../../lib/agent"

describe("prdCtaState — smart View/Generate PRD button", () => {
  it("offers 'View PRD' when a PRD already exists for the insight", () => {
    expect(prdCtaState({ hasPrd: true, prdId: 12 }, false)).toEqual({
      label: "View PRD",
      isView: true,
    })
    // still 'View PRD' even mid another job (view is a cheap read)
    expect(prdCtaState({ hasPrd: true, prdId: 12 }, true).isView).toBe(true)
  })
  it("offers 'Generate PRD' when none exists (and 'Generating…' in flight)", () => {
    expect(prdCtaState({ hasPrd: false, prdId: null }, false)).toEqual({
      label: "Generate PRD",
      isView: false,
    })
    expect(prdCtaState({ hasPrd: false, prdId: null }, true).label).toBe("Generating…")
  })
  it("does NOT offer view when hasPrd but the prd id is unknown, or no state", () => {
    expect(prdCtaState({ hasPrd: true, prdId: null }, false).isView).toBe(false)
    expect(prdCtaState(null, false)).toEqual({ label: "Generate PRD", isView: false })
    expect(prdCtaState(undefined, false).isView).toBe(false)
  })
})

describe("prototypeCtaLabel — View/Generate prototype relabels on saved-in-DB state", () => {
  it("offers 'View prototype' only once a prototype is built AND saved (prototypeReady)", () => {
    expect(prototypeCtaLabel({ hasPrd: true, prototypeReady: true })).toBe("View prototype")
  })
  it("offers 'Generate prototype' before one is built — PRD exists but no prototype yet", () => {
    expect(prototypeCtaLabel({ hasPrd: true, prototypeReady: false })).toBe("Generate prototype")
  })
  it("offers 'Generate prototype' when there's no PRD/state at all", () => {
    // A dangling prototypeReady with no PRD can't be viewed → still "Generate".
    expect(prototypeCtaLabel({ hasPrd: false, prototypeReady: true })).toBe("Generate prototype")
    expect(prototypeCtaLabel(null)).toBe("Generate prototype")
    expect(prototypeCtaLabel(undefined)).toBe("Generate prototype")
  })
})

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
    body: `Body copy for ${title} that should disappear when the card is dismissed.`,
    metricHighlight: "41% completion",
    fromSources: ["Amplitude", "Zendesk"],
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
  fromSources: [], // honest-provenance case: no sources → no "From" row
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

// Injects the fixture brief into ContentContext on mount so BriefChat renders it.
function InjectBrief({ brief }: { brief: BriefV2State }) {
  const { setContent } = useContent()
  React.useEffect(() => {
    setContent({
      briefV2: brief,
      userName: "Apurva Jain",
      // Minimal per-finding detail meta so card actions (generate/view) have a
      // brief+insight to act on; without it cardGenerateAll early-returns.
      briefDetails: {
        "something_wrong-0": { meta: { briefId: 1, insightIndex: 0 } },
        "something_wrong-1": { meta: { briefId: 1, insightIndex: 1 } },
      } as never,
    })
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
  mapEntries.clear()
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

  it("clicking Generate PRD runs the system then refetches the map so the button can flip to View PRD", async () => {
    vi.mocked(runMultiAgentGeneration).mockResolvedValueOnce({
      ok: true,
      runId: "r1",
      status: { status: "ready" } as never,
      docs: { docs: [] } as never,
    })
    await act(async () => {
      renderBrief()
    })
    const card = cardFor(HERO.title)
    const btn = within(card).getByRole("button", { name: /generate prd/i })
    await act(async () => {
      fireEvent.click(btn)
    })
    expect(runMultiAgentGeneration).toHaveBeenCalledTimes(1)
    // The map refetch is what lets the button flip Generate PRD → View PRD.
    expect(refetchMapSpy).toHaveBeenCalled()
  })
})

describe("BriefChat finding card — dismiss / restore (Task A)", () => {
  it("test_dismiss_greys_card_and_hides_detail: clicking X adds fc--dismissed and removes the heavy detail", async () => {
    await act(async () => {
      renderBrief()
    })

    const card = cardFor(HERO.title)
    expect(card.className).not.toContain("fc--dismissed")
    // Full card shows the source row, the body copy, and the action buttons.
    expect(card.querySelector(".fc-from")).not.toBeNull()
    expect(within(card).queryByText(/Body copy for First-handoff/)).not.toBeNull()
    // No prototype built yet (empty map) → the prototype CTA reads "Generate
    // prototype"; locate it by role so the presence check is label-agnostic.
    expect(within(card).queryByRole("button", { name: /prototype/i })).not.toBeNull()

    // Click the per-card Dismiss control.
    fireEvent.click(within(card).getByLabelText("Dismiss finding"))

    const dismissed = cardFor(HERO.title)
    expect(dismissed.className).toContain("fc--dismissed")
    // The finding is still present (title visible) — but the detail/viz is gone.
    expect(within(dismissed).getByText(HERO.title)).not.toBeNull()
    expect(dismissed.querySelector(".fc-from")).toBeNull()
    expect(within(dismissed).queryByText(/Body copy for First-handoff/)).toBeNull()
    expect(within(dismissed).queryByRole("button", { name: /prototype/i })).toBeNull()
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
    expect(restored.querySelector(".fc-from")).not.toBeNull()
    expect(within(restored).queryByRole("button", { name: /prototype/i })).not.toBeNull()
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
    expect(within(other).queryByRole("button", { name: /prototype/i })).not.toBeNull()
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

describe("BriefChat finding card — 'From' source chips (weekly-brief skill format)", () => {
  it("renders the skill's source chips and NOT the legacy mini-chart / KPI stat columns", async () => {
    await act(async () => {
      renderBrief()
    })

    const card = cardFor(HERO.title)
    // The 'From' provenance row renders each source chip from `_card.sources`.
    const fromRow = card.querySelector(".fc-from") as HTMLElement | null
    expect(fromRow).not.toBeNull()
    expect(within(fromRow!).getByText("From")).not.toBeNull()
    const chips = Array.from(fromRow!.querySelectorAll(".fc-from-src")).map((c) => c.textContent)
    expect(chips).toEqual(["Amplitude", "Zendesk"])

    // The legacy mini bar chart + numeric KPI stat columns are gone — the skill
    // puts numbers in the title/body, with a quiet source row beneath.
    expect(card.querySelector(".fc-mc")).toBeNull()
    expect(card.querySelector(".fc-stat")).toBeNull()
    expect(card.querySelector(".fc-stats-row")).toBeNull()
  })

  it("renders no 'From' row when the finding has no sources (honest provenance)", async () => {
    await act(async () => {
      renderBrief()
    })

    // SUPPORTING is built with fromSources: [] → no row, no empty 'From' label.
    const card = cardFor(SUPPORTING.title)
    expect(card.querySelector(".fc-from")).toBeNull()
  })
})

describe("BriefChat finding card — prototype option gated on prototypeable", () => {
  function renderBriefWith(brief: BriefV2State) {
    return render(
      React.createElement(
        NavigationProvider,
        null,
        React.createElement(
          ContentProvider,
          null,
          React.createElement(InjectBrief, { brief }),
          React.createElement(BriefChat),
        ),
      ),
    )
  }

  it("hides 'View prototype' on a finding the fix can't be visualized (prototypeable=false)", async () => {
    const brief: BriefV2State = {
      ...BRIEF,
      hero: { ...HERO, prototypeable: false },
      supporting: [{ ...SUPPORTING, prototypeable: true }],
    }
    await act(async () => {
      renderBriefWith(brief)
    })
    // Non-visualizable finding → no prototype affordance.
    expect(within(cardFor(HERO.title)).queryByRole("button", { name: /prototype/i })).toBeNull()
    // A sibling visualizable finding still offers it.
    expect(within(cardFor(SUPPORTING.title)).queryByRole("button", { name: /prototype/i })).not.toBeNull()
  })
})

// ── Composer "generate a prototype" → carries the open PRD's id in the URL ─────
// Regression for the gap where the composer prototype command (and the post-build
// reveal) navigated to a BARE /prototype, dropping the ?prd= context, so the route
// landed on its "No PRD selected" empty state and the prototype looked lost. With
// an open PRD in ContentContext, the command must push /prototype?prd=<id>.
describe("BriefChat composer — 'generate a prototype' navigation", () => {
  // Seeds a brief AND an open PRD (prd_id) into ContentContext so prototypeFlow
  // takes its `content.prd` branch (the composer path under test).
  function InjectBriefWithPrd({ prdId }: { prdId: number }) {
    const { setContent } = useContent()
    React.useEffect(() => {
      setContent({
        briefV2: BRIEF,
        userName: "Apurva Jain",
        // Minimal open-PRD state — prototypeFlow only reads content.prd.prd_id.
        prd: { prd_id: prdId } as never,
        prdMeta: { briefId: 1, insightIndex: 0 },
        briefDetails: {
          "something_wrong-0": { meta: { briefId: 1, insightIndex: 0 } },
          "something_wrong-1": { meta: { briefId: 1, insightIndex: 1 } },
        } as never,
      })
    }, [setContent, prdId])
    return null
  }

  it("pushes /prototype?prd=<id> (NOT a bare /prototype) when a PRD is open", async () => {
    await act(async () => {
      render(
        React.createElement(
          NavigationProvider,
          null,
          React.createElement(
            ContentProvider,
            null,
            React.createElement(InjectBriefWithPrd, { prdId: 515 }),
            React.createElement(BriefChat),
          ),
        ),
      )
    })

    const composer = screen.getByPlaceholderText(/Ask anything/i)
    await act(async () => {
      fireEvent.change(composer, { target: { value: "generate a prototype" } })
      fireEvent.keyDown(composer, { key: "Enter" })
    })

    // The navigation carries the PRD context — and is NOT the bare path.
    expect(pushSpy).toHaveBeenCalledWith(prototypePath(515))
    expect(pushSpy).toHaveBeenCalledWith("/prototype?prd=515")
    expect(pushSpy).not.toHaveBeenCalledWith("/prototype")
  })
})

// ── Brief top bar removed ─────────────────────────────────────────────────────
// The brief top bar (the .bh <header> with the "Week of … · <Company>" label and
// the connectors (plug) + more (…) icon buttons) was removed entirely — the tab
// name above the surface is the single source of context, so the strip was
// redundant. The tab bar itself lives in ChatScreen, not here, so it is
// unaffected; the brief now opens straight into its content.
describe("BriefChat header — top bar removed", () => {
  it("renders no .bh header bar (the brief opens straight into its content)", async () => {
    await act(async () => {
      renderBrief()
    })
    // The whole header bar and its chrome are gone…
    expect(document.querySelector("header.bh")).toBeNull()
    expect(document.querySelector(".bh-actions")).toBeNull()
    // …including the connectors (plug) and more (…) icon buttons.
    expect(screen.queryByLabelText("Open connectors")).toBeNull()
    expect(screen.queryByLabelText("More options")).toBeNull()
    // …but the brief content still renders.
    expect(document.querySelector(".bc-scroll")).not.toBeNull()
  })
})

// ── Fixed agent name "Spiky" ──────────────────────────────────────────────────
// The PM agent is no longer user-named: there is ONE fixed display name, "Spiky",
// sourced from the AGENT_NAME constant. The brief/chat header must render that
// name (next to the sparkle mark) — never the old hardcoded "PM Agent". The
// "PM COWORKER" pill is a *role* badge and is intentionally unaffected.
describe("BriefChat header — fixed agent name 'Spiky'", () => {
  it("renders the agent display name as 'Spiky' (not 'PM Agent')", async () => {
    await act(async () => {
      renderBrief()
    })
    // The brief's agent head carries the agent's NAME + role badge.
    const head = document.querySelector(".bc-agent-head") as HTMLElement | null
    expect(head).not.toBeNull()
    // The agent's NAME (the .bc-agent-name span) reads "Spiky".
    const name = head!.querySelector(".bc-agent-name") as HTMLElement | null
    expect(name).not.toBeNull()
    expect(name!.textContent).toBe(AGENT_NAME)
    expect(name!.textContent).toBe("Spiky")
    // The old hardcoded name is gone everywhere.
    expect(screen.queryByText("PM Agent")).toBeNull()
    // The role pill ("PM COWORKER") is unaffected.
    expect(within(head!).getByText("PM COWORKER")).not.toBeNull()
  })

  it("greeting still renders below the Spiky header", async () => {
    await act(async () => {
      renderBrief()
    })
    // The brief greeting line is present (the agent greeting copy), confirming
    // the rename didn't disturb the greeting render path.
    expect(document.querySelector(".bc-greeting")).not.toBeNull()
  })
})

// ── Greeting (single combined paragraph) ─────────────────────────────────────
// The brief opens with ONE greeting paragraph: the personalized salutation, the
// agent's ongoing-value capability line, and a "top N this week" tail — no
// separate persistent-intro paragraph (which used to double-lead "Good day").
describe("BriefChat — greeting paragraph", () => {
  it("renders one combined greeting: salutation + capability + top-N tail", async () => {
    await act(async () => {
      renderBrief()
    })
    const greeting = document.querySelector(".bc-greeting") as HTMLElement | null
    expect(greeting).not.toBeNull()
    const text = greeting!.textContent ?? ""
    // Personalized salutation (first name only).
    expect(text).toContain("Good day, Apurva -")
    // The capability line flows in the same paragraph…
    expect(text).toContain("continuously monitor how your product is being used")
    expect(text).toContain(
      "give you a weekly digest of the most important things worth working on",
    )
    // …and the "top N this week" tail closes it (fixture has 3 findings).
    expect(text).toMatch(/Here's the top \d+ things? worth your attention this week\./)
  })

  it("has no separate persistent-intro paragraph and exactly one 'Good day'", async () => {
    await act(async () => {
      renderBrief()
    })
    expect(document.querySelector(".bc-intro")).toBeNull()
    const greeting = document.querySelector(".bc-greeting")?.textContent ?? ""
    expect((greeting.match(/Good day/g) ?? []).length).toBe(1)
  })

  it("no longer renders the redundant 'Monday brief · …' secondary status line", async () => {
    await act(async () => {
      renderBrief()
    })
    expect(screen.queryByText(/Monday brief ·/)).toBeNull()
  })
})

// ── Broken preview thumbnail removed ──────────────────────────────────────────
// The right-rail prototype-preview thumbnail was removed: the design-agent
// screenshot capture photographed the bundle's raw HTML source (served as
// text/plain), so the thumbnail showed markup, not the prototype. Even with a
// READY prototype that carries a preview_image_url, the card must render NO
// preview tile — only the "View prototype" button remains as the way in.
describe("BriefChat finding card — no prototype preview thumbnail", () => {
  it("renders no .fc-preview tile even when a ready prototype has a preview_image_url", async () => {
    // Seed the brief→prototype map: insight 0 has a ready prototype WITH an image.
    mapEntries.set(0, {
      insight_index: 0,
      prd_id: 42,
      prd_title: "Measurement Stack",
      prototype: { ready: true, preview_image_url: "https://cdn/thumb.png" },
    } as never)

    await act(async () => {
      renderBrief()
    })

    const card = cardFor(HERO.title)
    // The broken thumbnail (and its image) must not render…
    expect(card.querySelector(".fc-preview")).toBeNull()
    expect(card.querySelector(".fc-preview-img")).toBeNull()
    expect(within(card).queryByText("Prototype preview · open design")).toBeNull()
    // …but the prototypeable finding still offers the "View prototype" button
    // (insight 0 has a READY prototype in the seeded map).
    expect(within(card).queryByText("View prototype")).not.toBeNull()
  })
})

// ── Prototype CTA relabels Generate → View once one is built + saved ───────────
// The finding card's prototype button must reflect real DB state (the brief→
// prototype map's prototypeReady), mirroring the chat surface: "Generate
// prototype" until one is built, "View prototype" once it's saved. Previously the
// label was a static adapter CTA string that never flipped.
describe("BriefChat finding card — prototype CTA relabels on built prototype", () => {
  it("reads 'Generate prototype' with no prototype, then 'View prototype' once ready in the DB", async () => {
    // Empty map → no prototype built yet for this insight.
    await act(async () => { renderBrief() })
    expect(within(cardFor(HERO.title)).getByRole("button", { name: "Generate prototype" })).toBeTruthy()
    expect(within(cardFor(HERO.title)).queryByRole("button", { name: "View prototype" })).toBeNull()

    cleanup()
    // Seed a READY prototype for insight 0 (HERO's insightIndex) → CTA flips.
    mapEntries.set(0, {
      insight_index: 0,
      prd_id: 42,
      prd_title: "Measurement Stack",
      prototype: { ready: true, preview_image_url: null },
    } as never)
    await act(async () => { renderBrief() })
    expect(within(cardFor(HERO.title)).getByRole("button", { name: "View prototype" })).toBeTruthy()
    expect(within(cardFor(HERO.title)).queryByRole("button", { name: "Generate prototype" })).toBeNull()
  })
})

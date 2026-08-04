// @vitest-environment jsdom
//
// Top Insights initial-load flash.
//
// Reported symptom: opening (or refreshing onto) the Top Insights tab in a
// workspace that HAS a brief showed the dead-end "Connect a source to see your
// Top Insights" page for a beat, which was then replaced by the brief.
//
// Cause: the surface branches on two values that both load asynchronously and
// both default to "nothing" before they answer — `briefHydration` starts null
// and passes through idle/loading, and `connectedConnectorIds` starts [] until
// AppShell's connectors fetch resolves. On the first paint that reads as "no
// findings, no sources", which is exactly the connect page's condition.
//
// The rule these tests hold: the tab shows the brief OR the connect page, never
// one and then the other. Until both inputs answer, it shows a neutral loading
// placeholder that commits to neither.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.mock("../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
  }
  return {
    ApiError,
    askApi: { ask: vi.fn() },
    briefApi: {
      current: vi.fn(),
      status: vi.fn(),
      regenerate: vi.fn(),
      dismiss: vi.fn().mockResolvedValue({ dismissed: true, theme_id: "t" }),
      defer: vi.fn().mockResolvedValue({ deferred: true, theme_id: "t", deferred_until: "2026-08-03" }),
      restore: vi.fn().mockResolvedValue({ restored: true, theme_id: "t" }),
    },
  }
})

vi.mock("../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null,
    isTriggering: false,
    showCompleted: false,
    triggerRun: vi.fn(),
  }),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/brief",
}))

vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    loading: false,
    profile: null,
    workspace: null,
    refresh: async () => {},
  }),
}))

import { NavigationProvider } from "../../../context/NavigationContext"
import { ContentProvider, useContent } from "../../../context/ContentContext"
import type { AppContentState } from "../../../types/content"
import type { BriefV2State } from "../../../lib/brief-v2-adapter"
import { BriefChat } from "../BriefChat"

const EMPTY_BRIEF: BriefV2State = {
  headline: null,
  weekOf: null,
  company: "Asurion",
  productArea: "",
  kpiTiles: [],
  hero: null,
  supporting: [],
  sourcesLine: "",
  insufficientEvidence: false,
  emptyReason: null,
}

const READY_BRIEF: BriefV2State = {
  ...EMPTY_BRIEF,
  headline: "This week",
  weekOf: "2026-06-08",
  hero: {
    kind: "hero",
    detailKey: "fix-0",
    actionAccent: "fix",
    actionLabel: "FIX",
    tagType: "fix",
    tagLabel: "WHAT'S BROKEN",
    skillType: "reliability",
    skillAccent: "#c0473c",
    skillLabel: "Reliability",
    ctas: [],
    category: "Retention",
    priority: "P0",
    confidence: 0.82,
    prototypeable: false,
    title: "Day-30 retention is slipping",
    body: "Retention dropped 6 points week over week.",
    metricHighlight: "",
    fromSources: [],
    statTiles: [],
    chart: null,
    convergence: [],
    secondaryCtaLabel: "",
    secondaryCtaBehavior: "open_analysis",
    askQuestion: "Why is retention slipping?",
    quote: null,
  },
}

function Harness() {
  const { setContent } = useContent()
  const set = (patch: Partial<AppContentState>) => setContent(patch)
  const button = (id: string, patch: Partial<AppContentState>) =>
    React.createElement("button", { "data-testid": id, onClick: () => set(patch) }, id)
  return React.createElement(
    "div",
    null,
    // The real sequence AppShell drives on a load: hydration ticks to "loading"
    // first, then both fetches answer.
    button("tick-loading", { briefHydration: "loading" }),
    button("brief-arrives", {
      briefHydration: "ready",
      briefV2: READY_BRIEF,
      connectedConnectorIds: ["superset"],
      connectorsHydrated: true,
    }),
    // Brief answered but connectors haven't: still not enough to judge.
    button("brief-only", { briefHydration: "ready", briefV2: EMPTY_BRIEF }),
    // Connectors answered but the brief hasn't: likewise.
    button("connectors-only", { connectedConnectorIds: [], connectorsHydrated: true }),
    // Both answered, genuinely nothing: the connect page is now correct.
    button("both-empty", {
      briefHydration: "ready",
      briefV2: EMPTY_BRIEF,
      connectedConnectorIds: [],
      connectorsHydrated: true,
    }),
    React.createElement(BriefChat),
  )
}

function mountHarness() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(Harness)),
    ),
  )
}

const CONNECT_TITLE = "Connect a source to see your Top Insights"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("BriefChat — initial load", () => {
  it("test_first_paint_shows_no_connect_page: the default state commits to nothing", () => {
    mountHarness()

    // Fresh mount: briefHydration null, connectorsHydrated false. Nothing has
    // answered, so neither branch may render.
    expect(screen.queryByText(CONNECT_TITLE)).toBeNull()
    expect(document.querySelector(".bc-empty")).toBeNull()
    expect(document.querySelector(".bc-greeting")).toBeNull()
    expect(document.querySelector(".bc-loading")).not.toBeNull()
  })

  it("test_no_connect_page_flash_before_a_brief: the reported bug", () => {
    mountHarness()

    act(() => {
      fireEvent.click(screen.getByTestId("tick-loading"))
    })
    // This is the exact frame that used to render the connect page.
    expect(screen.queryByText(CONNECT_TITLE)).toBeNull()
    expect(document.querySelector(".bc-loading")).not.toBeNull()

    act(() => {
      fireEvent.click(screen.getByTestId("brief-arrives"))
    })
    expect(document.querySelector(".fc-title")?.textContent).toBe(
      "Day-30 retention is slipping",
    )
    expect(screen.queryByText(CONNECT_TITLE)).toBeNull()
    expect(document.querySelector(".bc-loading")).toBeNull()
  })

  it("test_one_answer_is_not_enough: either half alone still holds the placeholder", () => {
    mountHarness()

    act(() => {
      fireEvent.click(screen.getByTestId("brief-only"))
    })
    expect(document.querySelector(".bc-loading")).not.toBeNull()
    expect(screen.queryByText(CONNECT_TITLE)).toBeNull()

    cleanup()
    mountHarness()

    act(() => {
      fireEvent.click(screen.getByTestId("connectors-only"))
    })
    expect(document.querySelector(".bc-loading")).not.toBeNull()
    expect(screen.queryByText(CONNECT_TITLE)).toBeNull()
  })

  it("test_placeholder_resolves_to_the_connect_page: a source-less workspace still gets it", () => {
    mountHarness()

    act(() => {
      fireEvent.click(screen.getByTestId("both-empty"))
    })

    // The gate delays the verdict; it must not swallow it. A workspace with no
    // evidence connector still lands on the dead-end page once we know that.
    expect(screen.getByText(CONNECT_TITLE)).not.toBeNull()
    expect(document.querySelector(".bc-loading")).toBeNull()
  })
})

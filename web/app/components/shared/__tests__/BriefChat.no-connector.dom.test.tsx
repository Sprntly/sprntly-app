// @vitest-environment jsdom
//
// Top Insights "connect a source" empty state.
//
// The brief is synthesized from connectors that BRING EVIDENCE IN (analytics,
// customer voice, CRM, docs, design, revenue, monitoring). Three categories
// can't feed it: comms (Slack — a delivery target), pm (Jira — where work is
// tracked) and code (GitHub — what was built). A workspace with only those has
// a brief that will never generate, so BriefChat replaces the surface with a
// page explaining why and a button to Settings → Connectors.
//
// These tests mount the real BriefChat inside the real Navigation + Content
// providers and drive `content` imperatively, proving:
//   1. no connectors            → the connect page renders, greeting does not.
//   2. only Slack / Jira / GitHub → still the connect page (the whole point of
//      the category split — "I connected something" must not unlock it).
//   3. any evidence connector   → the normal brief/greeting, never the page.
//   4. existing findings win    → a workspace with a brief keeps showing it even
//      with no connector (revoked-after-generation must not hide real work).
//   5. generating wins          → the WIP spinner owns the surface first.
//   6. the CTA routes to Settings → Connectors.
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

// The CTA calls goTo("connectors"), which resolves through SCREEN_PATH to
// "/settings?section=connectors" and pushes it — assert on this spy.
const pushSpy = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushSpy, replace: vi.fn(), prefetch: vi.fn() }),
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

// A brief WITH a finding — used to prove existing work is never hidden behind
// the empty state when a connector is later revoked.
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
    // Every patch below carries `connectorsHydrated: true` — these tests are
    // about the SETTLED verdict. The unsettled window (both fetches still in
    // flight) is the loading placeholder's job, covered at the bottom of this
    // file and in BriefChat.initial-load.dom.test.tsx.
    button("set-no-connectors", {
      briefHydration: "ready",
      briefV2: EMPTY_BRIEF,
      connectedConnectorIds: [],
      connectorsHydrated: true,
    }),
    // MS Teams (comms) + Jira (pm) + GitHub (code) — three excluded
    // categories. (Slack no longer belongs here: dual-typed communication +
    // customer-voice since 2026-07-30, so it counts as evidence.)
    button("set-non-evidence-only", {
      briefHydration: "ready",
      briefV2: EMPTY_BRIEF,
      connectedConnectorIds: ["msteams", "jira", "github"],
      connectorsHydrated: true,
    }),
    button("set-evidence-connector", {
      briefHydration: "ready",
      briefV2: EMPTY_BRIEF,
      connectedConnectorIds: ["msteams", "superset"],
      connectorsHydrated: true,
    }),
    button("set-slack-only", {
      briefHydration: "ready",
      briefV2: EMPTY_BRIEF,
      connectedConnectorIds: ["slack"],
      connectorsHydrated: true,
    }),
    button("set-findings-without-connector", {
      briefHydration: "ready",
      briefV2: READY_BRIEF,
      connectedConnectorIds: [],
      connectorsHydrated: true,
    }),
    button("set-generating-without-connector", {
      briefHydration: "generating",
      briefV2: null,
      connectedConnectorIds: [],
      connectorsHydrated: true,
    }),
    // The load window itself: the brief has answered "nothing here", but the
    // connectors fetch hasn't come back yet, so "no sources" is still a guess.
    button("set-connectors-unsettled", {
      briefHydration: "ready",
      briefV2: EMPTY_BRIEF,
      connectedConnectorIds: [],
      connectorsHydrated: false,
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
const CTA_LABEL = "Connect a source"
const EMPTY_GREETING = "add and connect more sources"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("BriefChat — no evidence connector", () => {
  it("test_no_connectors_shows_connect_page: renders the connect page instead of the greeting", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-no-connectors"))
    })

    expect(screen.getByText(CONNECT_TITLE)).not.toBeNull()
    expect(document.querySelector(".bc-empty")).not.toBeNull()
    // It replaces the surface — the greeting paragraph is not rendered…
    expect(document.querySelector(".bc-greeting")).toBeNull()
    expect(screen.queryByText(new RegExp(EMPTY_GREETING))).toBeNull()
    // …and it is not confused with the generating state.
    expect(document.querySelector(".bc-generating")).toBeNull()
  })

  it("test_non_evidence_connectors_still_show_connect_page: Teams/Jira/GitHub don't satisfy the brief", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-non-evidence-only"))
    })

    // The whole point of the category split: having connected *something* must
    // not unlock the brief when none of it brings evidence in.
    expect(screen.getByText(CONNECT_TITLE)).not.toBeNull()
    // The copy names that misread explicitly, so the user isn't left thinking
    // the page is a bug.
    expect(screen.getByText(/Already connected Jira or GitHub\?/)).not.toBeNull()
  })

  it("test_slack_alone_satisfies_the_brief: dual-typed Slack clears the empty state", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-slack-only"))
    })

    // Slack is communication + customer-voice (2026-07-30): its synced
    // channels are evidence, so connecting it unlocks the brief surface.
    expect(screen.queryByText(CONNECT_TITLE)).toBeNull()
    expect(screen.getByText(new RegExp(EMPTY_GREETING))).not.toBeNull()
  })

  it("test_evidence_connector_shows_brief: one evidence connector clears the empty state", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-evidence-connector"))
    })

    // Superset (analytics) qualifies even though Teams alongside it doesn't.
    expect(screen.queryByText(CONNECT_TITLE)).toBeNull()
    expect(document.querySelector(".bc-empty")).toBeNull()
    // The normal empty-brief greeting takes over instead.
    expect(screen.getByText(new RegExp(EMPTY_GREETING))).not.toBeNull()
  })

  it("test_existing_findings_are_never_hidden: a brief with findings survives a revoked connector", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-findings-without-connector"))
    })

    // Real work stays on screen — the empty state must not swallow a brief the
    // user could already see.
    expect(document.querySelector(".fc-title")?.textContent).toBe(
      "Day-30 retention is slipping",
    )
    expect(screen.queryByText(CONNECT_TITLE)).toBeNull()
  })

  it("test_generating_wins_over_connect_page: the WIP state owns the surface", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-generating-without-connector"))
    })

    // A brief already in flight is about to resolve; showing "connect a source"
    // over it would contradict what the user just triggered.
    expect(document.querySelector(".bc-generating")).not.toBeNull()
    expect(screen.queryByText(CONNECT_TITLE)).toBeNull()
  })

  it("test_connect_page_waits_for_the_connector_fetch: no flash while connectors are unknown", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-connectors-unsettled"))
    })

    // `connectedConnectorIds` defaults to [], which is "we haven't asked yet",
    // not "this workspace has none". Rendering the dead-end connect page off
    // that default is what made it flash before the brief on every load.
    expect(screen.queryByText(CONNECT_TITLE)).toBeNull()
    expect(document.querySelector(".bc-empty")).toBeNull()
    expect(document.querySelector(".bc-loading")).not.toBeNull()

    // …and once the fetch answers "genuinely none", the page arrives.
    act(() => {
      fireEvent.click(screen.getByTestId("set-no-connectors"))
    })
    expect(screen.getByText(CONNECT_TITLE)).not.toBeNull()
    expect(document.querySelector(".bc-loading")).toBeNull()
  })

  it("test_cta_routes_to_connector_settings: the button navigates to Settings → Connectors", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-no-connectors"))
    })
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: CTA_LABEL }))
    })

    expect(pushSpy).toHaveBeenCalledWith("/settings?section=connectors")
  })
})

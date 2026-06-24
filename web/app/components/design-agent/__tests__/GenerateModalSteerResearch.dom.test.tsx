/**
 * @vitest-environment jsdom
 *
 * Steerable re-search on the no-match panel: when locate returns no screen to
 * anchor on, the PM can type a direction ("the settings page") and "Search
 * again" re-runs locate WITH that direction as a hint. A hit routes to the
 * located path; a miss returns to the panel with "Generate from the PRD anyway"
 * as the floor — never a dead-end.
 *
 * jsdom + @testing-library/react so we can type into the steer input (real
 * state) and drive the re-run POST→poll loop, asserting the hint actually rides
 * the locate call. The poll interval/timeout are shrunk via _testPoll* so the
 * loop settles fast under waitFor.
 */
import * as React from "react"
import { render, fireEvent, waitFor, act } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), toast: null }),
}))

vi.mock("../DesignAgentDrawer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../DesignAgentDrawer")>()
  return { ...actual, runGenerateFlow: vi.fn().mockResolvedValue(undefined) }
})

import { GenerateModal, isRealCandidate } from "../GenerateModal"
import { runGenerateFlow } from "../DesignAgentDrawer"
import {
  designAgentApi,
  type ConnectionSummary,
  type GitHubRepo,
  type LocateResponse,
} from "../../../lib/api"

// ── Fixtures ────────────────────────────────────────────────────────────────

const SEL_REPO = "org/repo"
const PRD_ID = 77

const GITHUB_CONN: ConnectionSummary[] = [
  {
    id: "c1",
    provider: "github",
    status: "active",
    account_label: "org",
    google_email: null,
    scopes: "repo",
    config: {},
    last_sync_at: null,
    last_sync_error: null,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
  },
]

const REPOS: GitHubRepo[] = [
  {
    full_name: SEL_REPO,
    name: "repo",
    private: false,
    html_url: "https://github.com/org/repo",
    default_branch: "main",
    description: null,
    updated_at: "2024-01-01T00:00:00Z",
    stargazers_count: 0,
  },
]

// Unmapped with NO ranked fallbacks — the no-screen panel that hosts the steer.
const UNMAPPED_EMPTY: LocateResponse = {
  decision: "ranked_confirm",
  chosen: [],
  ranked: [],
  top_confidence: 0,
  threshold: 0.8,
  repo: SEL_REPO,
  posture: "PARTIAL",
  unmapped: true,
  commit_sha: "",
}

// A steered re-search that now FINDS a screen → auto_proceed → located path.
const LOCATED_HIT: LocateResponse = {
  decision: "auto_proceed",
  chosen: [
    {
      id: "/settings",
      route: "/settings",
      entry_component: "SettingsScreen",
      confidence: 0.92,
      rationale: "matches the steer",
      ambiguous: false,
      component_count: 4,
    },
  ],
  ranked: [
    {
      id: "/settings",
      route: "/settings",
      entry_component: "SettingsScreen",
      confidence: 0.92,
      rationale: "matches the steer",
      ambiguous: false,
      component_count: 4,
    },
  ],
  top_confidence: 0.92,
  threshold: 0.8,
  repo: SEL_REPO,
  posture: "CLEAN",
  unmapped: false,
  commit_sha: "shaHIT",
}

// A ranked_confirm whose ONLY candidate is degenerate (empty route, zero
// components) — must route to the recovery body, never the pickable picker.
const DEGENERATE_RANKED: LocateResponse = {
  decision: "ranked_confirm",
  chosen: [],
  ranked: [
    {
      id: "",
      route: "",
      entry_component: "",
      confidence: 0,
      rationale: "no screen can be identified",
      ambiguous: true,
      component_count: 0,
    },
  ],
  top_confidence: 0,
  threshold: 0.8,
  repo: SEL_REPO,
  posture: "PARTIAL",
  unmapped: false,
  commit_sha: "",
}

// A real ambiguous match → the picker phase (the steer must also live here).
const PICKER_RANKED: LocateResponse = {
  decision: "ranked_confirm",
  chosen: [],
  ranked: [
    {
      id: "/dashboard",
      route: "/dashboard",
      entry_component: "DashboardScreen",
      confidence: 0.6,
      rationale: "possible match",
      ambiguous: true,
      component_count: 5,
    },
  ],
  top_confidence: 0.6,
  threshold: 0.8,
  repo: SEL_REPO,
  posture: "PARTIAL",
  unmapped: false,
  commit_sha: "shaPICK",
}

// A NON-ROUTE host (app shell / in-page section): legitimately has an EMPTY
// route but a valid id (the resolution key) and real components. Must be treated
// as REAL and remain pickable — a route-only filter would wrongly drop it.
const NON_ROUTE_HOST: LocateResponse = {
  decision: "ranked_confirm",
  chosen: [],
  ranked: [
    {
      id: "app-shell",
      route: "",
      entry_component: "AppShell",
      confidence: 0.6,
      rationale: "the feature attaches to the app shell",
      ambiguous: true,
      component_count: 7,
    },
  ],
  top_confidence: 0.6,
  threshold: 0.8,
  repo: SEL_REPO,
  posture: "PARTIAL",
  unmapped: false,
  commit_sha: "shaHOST",
}

function steerProps(overrides: Record<string, unknown> = {}) {
  return {
    open: true,
    onClose: vi.fn(),
    prdId: PRD_ID,
    figmaFileKey: null,
    _testConnections: GITHUB_CONN,
    _testRepos: REPOS,
    _testInitSource: "github" as const,
    _testInitRepoSel: SEL_REPO,
    // Land directly on the no-match panel.
    _testFlowPhase: "unmapped-resolve" as const,
    _testLocateResult: UNMAPPED_EMPTY,
    // Zero inter-poll delay so the re-run loop settles fast under waitFor.
    _testPollIntervalMs: 0,
    _testPollTimeoutMs: 5000,
    _testPollMaxRetries: 4,
    ...overrides,
  }
}

function typeSteer(container: HTMLElement, value: string) {
  const input = container.querySelector<HTMLInputElement>(
    '[data-testid="locate-steer-input"]',
  )
  expect(input).toBeTruthy()
  act(() => {
    fireEvent.change(input!, { target: { value } })
  })
}

function clickSearchAgain(container: HTMLElement) {
  const btn = container.querySelector<HTMLButtonElement>(
    '[data-testid="locate-search-again"]',
  )
  expect(btn).toBeTruthy()
  act(() => {
    btn!.click()
  })
}

beforeEach(() => {
  vi.mocked(runGenerateFlow).mockResolvedValue(undefined)
})

afterEach(() => {
  vi.resetAllMocks()
  vi.useRealTimers()
})

// ── the steer rides the re-run locate call ────────────────────────────────────

describe("Search again re-runs locate with the typed direction", () => {
  it("sends the typed hint on the re-run locate POST", async () => {
    const locateSpy = vi
      .spyOn(designAgentApi, "locate")
      .mockResolvedValue({ job_id: "job-steer", status: "running" })
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
      status: "done",
      result: LOCATED_HIT,
    })

    const { container } = render(
      React.createElement(GenerateModal, steerProps()),
    )

    typeSteer(container, "the settings page")
    clickSearchAgain(container)

    await waitFor(() => expect(locateSpy).toHaveBeenCalledTimes(1))
    expect(locateSpy).toHaveBeenCalledWith({
      prd_id: PRD_ID,
      github_repo: SEL_REPO,
      hint: "the settings page",
    })
  })

  it("a steered HIT routes to the located path (kicks off generation on the found screen)", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-hit",
      status: "running",
    })
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
      status: "done",
      result: LOCATED_HIT,
    })

    const { container } = render(
      React.createElement(GenerateModal, steerProps()),
    )

    typeSteer(container, "the settings page")
    clickSearchAgain(container)

    await waitFor(() =>
      expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1),
    )
    const params = (
      vi.mocked(runGenerateFlow).mock.calls[0]![0] as {
        params: Record<string, unknown>
      }
    ).params
    // The located screen the steer surfaced — the proven path that wears the
    // real shell, not the reconstruction fallback.
    expect(params["chosen_screen_route"]).toBe("/settings")
    expect(params["design_source"]).toBe("github")
  })

  it("a steered MISS returns to the panel with Generate-anyway as the floor (never a dead-end)", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-miss",
      status: "running",
    })
    // Re-search still finds nothing → unmapped again.
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
      status: "done",
      result: UNMAPPED_EMPTY,
    })

    const { container } = render(
      React.createElement(GenerateModal, steerProps()),
    )

    typeSteer(container, "nowhere in particular")
    clickSearchAgain(container)

    // Back on the no-match panel…
    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="unmapped-resolve"]'),
      ).toBeTruthy(),
    )
    // …with the PRD escape hatch still offered (the recovery floor).
    expect(
      container.querySelector('[data-testid="generate-anyway"]'),
    ).toBeTruthy()
    // …and the steer input still present so the PM can refine and retry.
    expect(
      container.querySelector('[data-testid="locate-steer-input"]'),
    ).toBeTruthy()
    // No generation kicked off on a miss.
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })
})

// ── the steer also lives on the PICKER (consolidated recovery body) ───────────

describe("the picker phase carries the steer + Search again", () => {
  it("typing a direction + Search again re-runs locate with {prd_id, github_repo, hint}", async () => {
    const locateSpy = vi
      .spyOn(designAgentApi, "locate")
      .mockResolvedValue({ job_id: "job-picker-steer", status: "running" })
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
      status: "done",
      result: LOCATED_HIT,
    })

    const { container } = render(
      React.createElement(
        GenerateModal,
        steerProps({
          // Land directly on the picker phase with a real candidate.
          _testFlowPhase: "picker",
          _testLocateResult: PICKER_RANKED,
        }),
      ),
    )

    // The steer input + Search again are present on the picker.
    expect(
      container.querySelector('[data-testid="locate-steer-input"]'),
    ).toBeTruthy()
    expect(
      container.querySelector('[data-testid="locate-search-again"]'),
    ).toBeTruthy()

    typeSteer(container, "the dashboard")
    clickSearchAgain(container)

    await waitFor(() => expect(locateSpy).toHaveBeenCalledTimes(1))
    expect(locateSpy).toHaveBeenCalledWith({
      prd_id: PRD_ID,
      github_repo: SEL_REPO,
      hint: "the dashboard",
    })
  })
})

// ── a degenerate ranked_confirm never shows a "Suggested" card ────────────────

describe("a degenerate ranked_confirm routes to the recovery body (no picker)", () => {
  it("renders the recovery body — no confirm surface / no Suggested card — but keeps generate-anyway + the steer", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-degen",
      status: "running",
    })
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
      status: "done",
      result: DEGENERATE_RANKED,
    })

    // Start at config and drive the real locate so handleLocateResult does the
    // routing (config → locating → recovery body).
    const { container } = render(
      React.createElement(
        GenerateModal,
        steerProps({ _testFlowPhase: "config", _testLocateResult: null }),
      ),
    )

    const genBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="generate-btn"]',
    )
    expect(genBtn).toBeTruthy()
    act(() => {
      genBtn!.click()
    })

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="unmapped-resolve"]'),
      ).toBeTruthy(),
    )
    // NO pickable confirm surface and NO "Suggested / Use this screen" card for
    // a degenerate placeholder.
    expect(
      container.querySelector('[data-testid="locate-confirm-surface"]'),
    ).toBeNull()
    expect(
      container.querySelector('[data-testid="locate-confirm-use"]'),
    ).toBeNull()
    // …but the recovery floor + steer are present.
    expect(
      container.querySelector('[data-testid="generate-anyway"]'),
    ).toBeTruthy()
    expect(
      container.querySelector('[data-testid="locate-steer-input"]'),
    ).toBeTruthy()
  })
})

// ── isRealCandidate: a non-route host (empty route, valid id) stays REAL ───────

describe("isRealCandidate keeps a non-route host but drops the placeholder", () => {
  it("a non-route host (route='', valid id, component_count>0) is REAL", () => {
    // The app shell / an in-page section: keyed only by id. Route-only filtering
    // would misclassify this as degenerate and vanish a real host.
    expect(
      isRealCandidate({
        id: "app-shell",
        route: "",
        entry_component: "AppShell",
        confidence: 60,
        rationale: "attaches to the shell",
        ambiguous: true,
        component_count: 7,
      }),
    ).toBe(true)
  })

  it("a routed screen (valid route + components) is REAL", () => {
    expect(
      isRealCandidate({
        id: "/settings",
        route: "/settings",
        entry_component: "SettingsScreen",
        confidence: 90,
        rationale: "",
        ambiguous: false,
        component_count: 4,
      }),
    ).toBe(true)
  })

  it("the empty-PRD placeholder (route='' AND id='', count 0, decline) is NOT real", () => {
    expect(
      isRealCandidate({
        id: "",
        route: "",
        entry_component: "",
        confidence: 12,
        rationale: "no screen can be identified",
        ambiguous: true,
        component_count: 0,
      }),
    ).toBe(false)
  })

  it("a host with NO components is NOT real even with a valid id", () => {
    expect(
      isRealCandidate({
        id: "some-section",
        route: "",
        entry_component: "X",
        confidence: 30,
        rationale: "",
        ambiguous: true,
        component_count: 0,
      }),
    ).toBe(false)
  })

  it("a decline rationale is NOT real even if it carries a route", () => {
    expect(
      isRealCandidate({
        id: "/x",
        route: "/x",
        entry_component: "X",
        confidence: 20,
        rationale: "No screen can be identified for this feature",
        ambiguous: true,
        component_count: 3,
      }),
    ).toBe(false)
  })
})

// ── a non-route host stays PICKABLE end-to-end (not routed to bare recovery) ───

describe("a non-route host ranked_confirm reaches the pickable picker", () => {
  it("renders the confirm surface (the host is real) — not a bare recovery body", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-host",
      status: "running",
    })
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
      status: "done",
      result: NON_ROUTE_HOST,
    })

    // Drive the real locate from config so handleLocateResult routes it.
    const { container } = render(
      React.createElement(
        GenerateModal,
        steerProps({ _testFlowPhase: "config", _testLocateResult: null }),
      ),
    )

    const genBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="generate-btn"]',
    )
    expect(genBtn).toBeTruthy()
    act(() => {
      genBtn!.click()
    })

    // The host survived the filter → the pickable confirm surface renders.
    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-confirm-surface"]'),
      ).toBeTruthy(),
    )
    // The steer is still available (consolidated body), but since a real
    // candidate is present this is the picker → the PRD-only generate-anyway
    // floor is HIDDEN (pick or steer, not generate-anyway).
    expect(
      container.querySelector('[data-testid="locate-steer-input"]'),
    ).toBeTruthy()
    expect(
      container.querySelector('[data-testid="generate-anyway"]'),
    ).toBeNull()
  })
})

// ── miss feedback: only a STEERED re-search that misses shows the message ──────

describe("steered-miss feedback (no silent re-render)", () => {
  it("the initial unmapped landing (no hint) does NOT show the miss message", () => {
    const { container } = render(
      React.createElement(GenerateModal, steerProps()),
    )
    expect(
      container.querySelector('[data-testid="locate-steer-missed"]'),
    ).toBeNull()
  })

  it("a steered re-search that comes back unmapped shows the miss message", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-miss-fb",
      status: "running",
    })
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
      status: "done",
      result: UNMAPPED_EMPTY,
    })

    const { container } = render(
      React.createElement(GenerateModal, steerProps()),
    )

    typeSteer(container, "somewhere that does not exist")
    clickSearchAgain(container)

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-steer-missed"]'),
      ).toBeTruthy(),
    )
    expect(
      container.querySelector('[data-testid="locate-steer-missed"]')
        ?.textContent,
    ).toContain("Still couldn")
  })
})

// ── guard: an empty steer cannot fire an unsteered re-run ─────────────────────

describe("the steer button is inert without a direction", () => {
  it("Search again is disabled while the input is blank", () => {
    const { container } = render(
      React.createElement(GenerateModal, steerProps()),
    )
    const btn = container.querySelector<HTMLButtonElement>(
      '[data-testid="locate-search-again"]',
    )
    expect(btn).toBeTruthy()
    expect(btn!.disabled).toBe(true)
  })

  it("clicking the disabled button does not call locate", () => {
    const locateSpy = vi.spyOn(designAgentApi, "locate")
    const { container } = render(
      React.createElement(GenerateModal, steerProps()),
    )
    clickSearchAgain(container)
    expect(locateSpy).not.toHaveBeenCalled()
  })
})

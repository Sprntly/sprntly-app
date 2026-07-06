/**
 * @vitest-environment jsdom
 *
 * Tests for the saved-preference AUTO-SKIP path in GenerateModal.
 *
 * The other GenerateModal suites run in node-env and bypass React effects via
 * _test* injection props. The auto-skip behaviour lives entirely inside a
 * useEffect, so it can only be exercised by actually running effects — hence
 * the jsdom override + @testing-library/react render in THIS file only. The
 * node-env suites (GenerateModalLocate / GenerateModalLocateBody /
 * design-source) are untouched and keep their environment.
 *
 * The regression this guards: a saved github preference used to generate a
 * prototype WITHOUT calling designAgentApi.locate, so no chosen_screen reached
 * the /generate payload and the backend recreate branch never fired (generic
 * output). The fix routes the github auto-skip through the same
 * locate→generate sequence the manual Generate click uses. figma + website
 * auto-skip must NOT call locate (no regression on those paths).
 */
import * as React from "react"
import { render, waitFor, act } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// Sprntly components use the classic JSX runtime; expose React globally so the
// modal body (rendered when the picker / unmapped hint shows) finds it. Same
// shim the sibling node-env suites install.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), toast: null }),
}))

// Spy runGenerateFlow so generation never runs for real; we assert the params
// it receives (the chosen_screen wiring) and whether it is called at all.
vi.mock("../DesignAgentDrawer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../DesignAgentDrawer")>()
  return { ...actual, runGenerateFlow: vi.fn().mockResolvedValue(undefined) }
})

import { GenerateModal } from "../GenerateModal"
import { runGenerateFlow } from "../DesignAgentDrawer"
import {
  ApiError,
  connectorsApi,
  designAgentApi,
  type ConnectionSummary,
  type GitHubRepo,
  type LocateResponse,
} from "../../../lib/api"
import type { DesignSourcePreference } from "../../../lib/onboarding/types"

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

const FIGMA_CONN: ConnectionSummary[] = [
  {
    id: "c2",
    provider: "figma",
    status: "active",
    account_label: "figma-account",
    google_email: null,
    scopes: "file_content:read",
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

/**
 * Mock the async locate contract: POST → job handle, first poll →
 * done(result). Replaces the old single `locate().mockResolvedValue(...)`.
 * Returns the `locate` POST spy so call-count assertions still read naturally.
 */
function mockLocateResolves(result: LocateResponse) {
  const spy = vi.spyOn(designAgentApi, "locate").mockResolvedValue({
    job_id: "job-1",
    status: "running",
  })
  vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
    status: "done",
    result,
  })
  return spy
}

function makeLocate(overrides: Partial<LocateResponse> = {}): LocateResponse {
  return {
    decision: "auto_proceed",
    chosen: [
      {
        id: "/team",
        route: "/team",
        entry_component: "TeamScreen",
        confidence: 0.9,
        rationale: "best match",
        ambiguous: false,
        component_count: 3,
      },
    ],
    ranked: [
      {
        id: "/team",
        route: "/team",
        entry_component: "TeamScreen",
        confidence: 0.9,
        rationale: "best match",
        ambiguous: false,
        component_count: 3,
      },
    ],
    top_confidence: 0.9,
    threshold: 0.8,
    repo: SEL_REPO,
    posture: "CLEAN",
    unmapped: false,
    commit_sha: "shaXYZ",
    ...overrides,
  }
}

const GITHUB_PREF: DesignSourcePreference = {
  design_source: "github",
  github_repo: SEL_REPO,
  figma_file_key: null,
  website_url: null,
}

const FIGMA_PREF: DesignSourcePreference = {
  design_source: "figma",
  figma_file_key: "figma-key-abc",
  github_repo: null,
  website_url: null,
}

const WEBSITE_PREF: DesignSourcePreference = {
  design_source: "website",
  figma_file_key: null,
  github_repo: null,
  website_url: null,
}

function lastGenerateParams(): Record<string, unknown> {
  const calls = vi.mocked(runGenerateFlow).mock.calls
  expect(calls.length).toBeGreaterThan(0)
  const arg = calls[calls.length - 1]![0] as { params: Record<string, unknown> }
  return arg.params
}

beforeEach(() => {
  vi.mocked(runGenerateFlow).mockResolvedValue(undefined)
  // The auto-skip effect fetches connector status + repos unless injected.
  // Inject via _test props in each test so we drive the loaded state directly;
  // still stub the network calls defensively in case any effect path fires.
  vi.spyOn(connectorsApi, "list").mockResolvedValue({ connections: GITHUB_CONN })
  vi.spyOn(connectorsApi, "listAccessibleGithubRepos").mockResolvedValue({
    repositories: REPOS,
  })
})

afterEach(() => {
  vi.resetAllMocks()
})

// ─── saved-github auto-skip routes through locate ─────────────────────────────

describe("saved-github auto-skip routes through locate (recreate fidelity)", () => {
  it("calls designAgentApi.locate with {prd_id, github_repo} on a healthy github preference", async () => {
    const locateSpy = mockLocateResolves(makeLocate())

    render(
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: GITHUB_PREF,
        _testConnections: GITHUB_CONN,
        _testRepos: REPOS,
      }),
    )

    await waitFor(() =>
      expect(locateSpy).toHaveBeenCalledWith({
        prd_id: PRD_ID,
        github_repo: SEL_REPO,
      }),
    )
  })

  it("on auto_proceed the generate body carries chosen_screen_route + chosen_screen_id + map_commit_sha", async () => {
    mockLocateResolves(makeLocate())

    render(
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: GITHUB_PREF,
        _testConnections: GITHUB_CONN,
        _testRepos: REPOS,
      }),
    )

    await waitFor(() => expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1))

    const params = lastGenerateParams()
    // The exact regression that would have caught the generic-output runs:
    // the chosen screen now reaches the payload.
    expect(params["chosen_screen_route"]).toBe("/team")
    expect(params["chosen_screen_id"]).toBe("/team")
    expect(params["map_commit_sha"]).toBe("shaXYZ")
    // Source + repo still threaded correctly.
    expect(params["design_source"]).toBe("github")
    expect(params["github_repo"]).toBe(SEL_REPO)
    // Mutual exclusivity: figma is never carried on the github path.
    expect(params["figma_file_key"]).toBeNull()
  })

  it("fires background generation on the chosen route + repo (no overlay handoff)", async () => {
    mockLocateResolves(makeLocate())
    const onGenStart = vi.fn()

    render(
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: GITHUB_PREF,
        onGenStart,
        _testConnections: GITHUB_CONN,
        _testRepos: REPOS,
      }),
    )

    // Background generation: the build runs server-side (backgroundMode) grounded
    // on the located screen/repo. onGenStart is no longer called — the full-screen
    // overlay handoff was replaced by a toast + backend notification.
    await waitFor(() =>
      expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1),
    )
    const call = vi.mocked(runGenerateFlow).mock.calls[0]![0] as {
      params: Record<string, unknown>
      backgroundMode?: boolean
    }
    expect(call.params["chosen_screen_route"]).toBe("/team")
    expect(call.params["github_repo"]).toBe(SEL_REPO)
    expect(call.backgroundMode).toBe(true)
    expect(onGenStart).not.toHaveBeenCalled()
  })

  it("at ranked_confirm does NOT auto-generate (re-opens the picker, no generate call)", async () => {
    const onClose = vi.fn()
    mockLocateResolves(
      makeLocate({ decision: "ranked_confirm", chosen: [], commit_sha: "" }),
    )

    render(
      React.createElement(GenerateModal, {
        open: true,
        onClose,
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: GITHUB_PREF,
        _testConnections: GITHUB_CONN,
        _testRepos: REPOS,
      }),
    )

    // locate is called, but no auto-generation fires at low confidence.
    await waitFor(() => expect(designAgentApi.locate).toHaveBeenCalledTimes(1))
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })

  it("at unmapped does NOT auto-generate", async () => {
    mockLocateResolves(
      makeLocate({ unmapped: true, chosen: [], decision: "ranked_confirm", commit_sha: "" }),
    )

    render(
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: GITHUB_PREF,
        _testConnections: GITHUB_CONN,
        _testRepos: REPOS,
      }),
    )

    await waitFor(() => expect(designAgentApi.locate).toHaveBeenCalledTimes(1))
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })
})

// ─── figma + website auto-skip must NOT call locate ───────────────────────────

describe("saved figma / website auto-skip never call locate (no regression)", () => {
  it("saved-figma auto-skip generates WITHOUT calling locate", async () => {
    const locateSpy = mockLocateResolves(makeLocate())

    render(
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: FIGMA_PREF,
        _testConnections: FIGMA_CONN,
        _testRepos: null,
      }),
    )

    await waitFor(() => expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1))
    expect(locateSpy).not.toHaveBeenCalled()

    const params = lastGenerateParams()
    expect(params["design_source"]).toBe("figma")
    expect(params["figma_file_key"]).toBe("figma-key-abc")
    // No codebase wiring on the figma path.
    expect(params).not.toHaveProperty("chosen_screen_route")
    expect(params).not.toHaveProperty("map_commit_sha")
  })

  it("saved-website auto-skip generates WITHOUT calling locate", async () => {
    const locateSpy = mockLocateResolves(makeLocate())

    render(
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: WEBSITE_PREF,
        _testConnections: [],
        _testRepos: null,
      }),
    )

    await waitFor(() => expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1))
    expect(locateSpy).not.toHaveBeenCalled()

    const params = lastGenerateParams()
    expect(params["design_source"]).toBe("website")
    expect(params).not.toHaveProperty("chosen_screen_route")
    expect(params).not.toHaveProperty("map_commit_sha")
  })
})

// ─── github render-guard flash suppression ────────────────────────────────────
//
// The render-time guard (not the useEffect) must suppress the config form for
// github too, to prevent a one-frame flash before enterLoadingFlow() fires.
//
// Three cases:
//   1. Data still loading (connections=null OR repos=null) → null
//   2. Data loaded + preference healthy → null (effect will fire)
//   3. Data loaded + preference UNHEALTHY (repo not in list) → form renders
//
// These tests drive the RENDER path only (no waiting for effects to fire locate).

describe("github render-guard flash suppression", () => {
  it("returns null while connections are still loading (connections=null)", () => {
    // _testConnections=null simulates the async connector fetch in flight.
    // The render guard must suppress immediately rather than showing the form.
    const { container } = render(
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: GITHUB_PREF,
        _testConnections: null,
        _testRepos: REPOS,
      }),
    )
    // The modal should render nothing (render guard returns null).
    expect(container.querySelector("#modal-generate")).toBeNull()
  })

  it("returns null while repos are still loading (repos=null, connections loaded)", () => {
    // _testRepos=null simulates the repo list fetch still in flight.
    const { container } = render(
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: GITHUB_PREF,
        _testConnections: GITHUB_CONN,
        _testRepos: null,
      }),
    )
    expect(container.querySelector("#modal-generate")).toBeNull()
  })

  it("never shows the config form when healthy — goes straight to loading state", async () => {
    // Data is loaded and the saved preference is healthy. The render guard
    // suppresses the config form (returns null) while in the config phase, and
    // the auto-skip effect immediately fires enterLoadingFlow(), switching the
    // phase to "loading" — so the loading heartbeat renders instead of the form.
    // The config form ([data-testid="generate-btn"]) must never appear.
    mockLocateResolves(makeLocate())
    const { container } = render(
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: GITHUB_PREF,
        _testConnections: GITHUB_CONN,
        _testRepos: REPOS,
      }),
    )
    // The config form's Generate button must never be visible.
    expect(container.querySelector("[data-testid='generate-btn']")).toBeNull()
    // The modal transitions to the loading phase — the heartbeat is visible.
    await waitFor(() =>
      expect(container.querySelector("[data-testid='generate-loading-heartbeat']")).not.toBeNull(),
    )
  })

  it("falls through to the form when the saved repo is NOT in the loaded list (unhealthy)", () => {
    // The saved preference names a repo that is not in the loaded repos list.
    // The render guard must NOT suppress — the form is the recovery path.
    const missingRepoPref: DesignSourcePreference = {
      design_source: "github",
      github_repo: "org/missing-repo",
      figma_file_key: null,
      website_url: null,
    }
    const { container } = render(
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: missingRepoPref,
        _testConnections: GITHUB_CONN,
        _testRepos: REPOS, // REPOS only contains org/repo, not org/missing-repo
      }),
    )
    // The config form must render so the user can pick a different repo.
    expect(container.querySelector("#modal-generate")).not.toBeNull()
  })
})

// ─── auto-skip locate FAILURE never blanks ───────────────────────────
//
// The tester's repro: on the in-tab /prototype surface, a SAVED github
// preference auto-skips and fires locate; when locate FAILS, the surface used to
// render NOTHING — no loading, no error, no Retry (pure blank). The manual-click
// terminal-failure paths are already covered in GenerateModalLocatePoll; this
// block proves the AUTO-SKIP entry reaches the SAME explicit error phase (so the
// Retry UI renders) and never collapses to a null render while open.
//
// These tests pass _testPoll* overrides (zero interval, small timeout) so the
// POST→poll loop settles fast under waitFor — the production auto-skip tests
// above use real intervals, but a terminal failure surfaces on the first
// POST/poll with no leading sleep, so the overrides only matter for the timeout
// case (which drives the clock explicitly).

describe("auto-skip locate failure surfaces the error state, never blank", () => {
  function renderAutoSkip(extra: Record<string, unknown> = {}) {
    return render(
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: GITHUB_PREF,
        _testConnections: GITHUB_CONN,
        _testRepos: REPOS,
        _testPollIntervalMs: 0,
        _testPollTimeoutMs: 5000,
        _testPollMaxRetries: 4,
        ...extra,
      }),
    )
  }

  it("a terminal POST 403 on the auto-skip path shows the error + Retry, not a blank", async () => {
    // The exact tester repro: SAVED github source → auto-skip fires locate →
    // the locate POST returns 403 (terminal). Before the fix the flow bailed
    // without setting flowPhase="error", leaving the modal in the null-rendering
    // config-auto-skip state → blank screen.
    vi.spyOn(designAgentApi, "locate").mockRejectedValue(
      new ApiError(403, { detail: "forbidden" }),
    )

    const { container } = renderAutoSkip()

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-error-state"]'),
      ).toBeTruthy(),
    )
    // The Retry affordance is present.
    expect(container.querySelector('[data-testid="locate-retry"]')).toBeTruthy()
    // NEVER blank: the modal shell is mounted (not a null render).
    expect(container.querySelector("#modal-generate")).not.toBeNull()
    // It did NOT collapse to the config form and never generated.
    expect(container.querySelector('[data-testid="generate-btn"]')).toBeNull()
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })

  it("a terminal 404 on the auto-skip job poll shows the error state, not a blank", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-as-1",
      status: "running",
    })
    const jobSpy = vi
      .spyOn(designAgentApi, "locateJob")
      .mockRejectedValue(new ApiError(404, { detail: "unknown job" }))

    const { container } = renderAutoSkip()

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-error-state"]'),
      ).toBeTruthy(),
    )
    // 404 is terminal — exactly one poll GET, no retry storm.
    expect(jobSpy).toHaveBeenCalledTimes(1)
    expect(container.querySelector("#modal-generate")).not.toBeNull()
    expect(container.querySelector('[data-testid="generate-btn"]')).toBeNull()
  })

  it("a never-finishing auto-skip job hits the timeout cap → error, not a blank", async () => {
    vi.useFakeTimers()
    try {
      vi.spyOn(designAgentApi, "locate").mockResolvedValue({
        job_id: "job-as-2",
        status: "running",
      })
      vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({ status: "running" })

      const { container } = renderAutoSkip({
        _testPollIntervalMs: 10,
        _testPollTimeoutMs: 30,
      })

      // Advance past the timeout cap, flushing the poll loop's awaits.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200)
      })

      expect(
        container.querySelector('[data-testid="locate-error-state"]'),
      ).toBeTruthy()
      expect(
        container.querySelector('[data-testid="locate-error"]')?.textContent,
      ).toContain("timed out")
      // Never blank, never collapsed to config.
      expect(container.querySelector("#modal-generate")).not.toBeNull()
      expect(container.querySelector('[data-testid="generate-btn"]')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it("ASYNC-SETTLE: a terminal POST 403 still surfaces the error when connector+repo data settle via the effect (live-surface repro)", async () => {
    // The live /prototype surface does NOT inject _testConnections/_testRepos —
    // the auto-skip effect fetches them, so connections settle, THEN repos
    // settle, re-running the effect across several renders before
    // enterLoadingFlow() fires. This drives that real settling path (only the
    // network calls are mocked) and proves the auto-skip locate FAILURE still
    // ends in the error phase rather than a null-rendering config-auto-skip
    // state. The beforeEach already stubs connectorsApi.list +
    // listAccessibleGithubRepos to return GITHUB_CONN / REPOS.
    vi.spyOn(designAgentApi, "locate").mockRejectedValue(
      new ApiError(403, { detail: "forbidden" }),
    )

    const { container } = render(
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: GITHUB_PREF,
        // NO _testConnections / _testRepos — force the real fetch + settle path.
      }),
    )

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-error-state"]'),
      ).toBeTruthy(),
    )
    expect(container.querySelector('[data-testid="locate-retry"]')).toBeTruthy()
    expect(container.querySelector("#modal-generate")).not.toBeNull()
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })

  it("RE-ENTRY STORM: effect re-runs after a failure do NOT auto-re-fire locate (only Retry does)", async () => {
    // On the live surface the auto-skip effect re-runs whenever its deps churn
    // (savedPreference / workspace identity changes on context re-renders). After
    // a locate FAILURE the effect must NOT silently re-enter the loading flow and
    // re-POST in a storm (which thrashes the surface and hammers the failing
    // endpoint). Only the explicit Retry button may re-run locate.
    const postSpy = vi.spyOn(designAgentApi, "locate").mockRejectedValue(
      new ApiError(403, { detail: "forbidden" }),
    )

    const { container, rerender } = render(
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: { ...GITHUB_PREF },
        _testConnections: GITHUB_CONN,
        _testRepos: REPOS,
        _testPollIntervalMs: 0,
        _testPollTimeoutMs: 5000,
      }),
    )

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-error-state"]'),
      ).toBeTruthy(),
    )
    expect(postSpy).toHaveBeenCalledTimes(1)

    // Force several effect re-runs by re-rendering with a fresh savedPreference
    // object identity (same shape) — simulates the live context churn.
    for (let i = 0; i < 3; i++) {
      await act(async () => {
        rerender(
          React.createElement(GenerateModal, {
            open: true,
            onClose: vi.fn(),
            prdId: PRD_ID,
            figmaFileKey: null,
            savedPreference: { ...GITHUB_PREF },
            _testConnections: GITHUB_CONN,
            _testRepos: REPOS,
            _testPollIntervalMs: 0,
            _testPollTimeoutMs: 5000,
          }),
        )
      })
    }

    // Still in the error state, and locate was NOT re-fired by the effect churn.
    expect(container.querySelector('[data-testid="locate-error-state"]')).toBeTruthy()
    expect(postSpy).toHaveBeenCalledTimes(1)
  })

  it("SWITCH SOURCE after an auto-skip failure renders the config form, never blank", async () => {
    // The tester's follow-on repro: SAVED github source → auto-skip fires locate
    // → locate FAILS → the clean error modal renders with Retry + Switch source.
    // Clicking "Switch source" sets flowPhase back to "config".
    // Before the fix the savedPreference render-null guard still saw a HEALTHY
    // github preference and returned null → <main> rendered NOTHING → BLANK.
    // With the fix the guard is gated on the pre-auto-skip window only, so an
    // explicit return to config falls through to the source-picker FORM.
    vi.spyOn(designAgentApi, "locate").mockRejectedValue(
      new ApiError(403, { detail: "forbidden" }),
    )

    const { container } = renderAutoSkip()

    // Auto-skip fired, locate failed → the error state is showing.
    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-error-state"]'),
      ).toBeTruthy(),
    )

    const switchSource = container.querySelector<HTMLButtonElement>(
      '[data-testid="locate-error-switch-source"]',
    )
    expect(switchSource).toBeTruthy()
    act(() => switchSource!.click())

    // FAILS WITHOUT THE FIX: the guard returns null here (githubHealthy is still
    // true), so #modal-generate is null and the config form never renders — the
    // blank-screen bug. With the fix the modal stays mounted AND the config /
    // source-picker form renders so the user can choose a different source.
    expect(container.querySelector("#modal-generate")).not.toBeNull()
    expect(container.querySelector('[data-testid="generate-btn"]')).not.toBeNull()
    // The error state is gone — we are back in config, not stuck.
    expect(container.querySelector('[data-testid="locate-error-state"]')).toBeNull()
    // And Switch source did NOT itself fire a generation.
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })

  // NOTE: the unmapped-resolve panel's "Switch source" affordance was removed in
  // the steer-first recovery polish (close the modal to swap source). The
  // render-null guard it used to exercise is still covered by the error-phase
  // "locate-error-switch-source" test above, so the prior unmapped switch-source
  // test was dropped rather than repointed at a button that no longer exists.

  it("PRE-AUTO-SKIP suppression preserved: a healthy github preference still shows NO config flash on first mount", () => {
    // The switch-source gate must not re-introduce the original config flash. On first
    // mount, before the auto-skip effect has run (autoSkipFiredRef still false),
    // a healthy saved github preference must still suppress the config form — the
    // render path is synchronous, so this assertion runs before any effect fires.
    mockLocateResolves(makeLocate())
    const { container } = render(
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: PRD_ID,
        figmaFileKey: null,
        savedPreference: GITHUB_PREF,
        _testConnections: GITHUB_CONN,
        _testRepos: REPOS,
      }),
    )
    // The config form's Generate button must NOT flash on the first synchronous
    // render (suppression still applies pre-auto-skip).
    expect(container.querySelector("[data-testid='generate-btn']")).toBeNull()
  })

  it("Retry after an auto-skip failure re-runs locate and can then succeed", async () => {
    const postSpy = vi
      .spyOn(designAgentApi, "locate")
      .mockRejectedValueOnce(new ApiError(403, { detail: "forbidden" }))
      .mockResolvedValueOnce({ job_id: "job-as-retry", status: "running" })
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
      status: "done",
      result: makeLocate(),
    })

    const { container } = renderAutoSkip()

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-error-state"]'),
      ).toBeTruthy(),
    )
    expect(postSpy).toHaveBeenCalledTimes(1)

    const retry = container.querySelector<HTMLButtonElement>(
      '[data-testid="locate-retry"]',
    )
    expect(retry).toBeTruthy()
    act(() => retry!.click())

    // Retry re-POSTs and the flow proceeds to generation.
    await waitFor(() => expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1))
    expect(postSpy).toHaveBeenCalledTimes(2)
    expect(container.querySelector('[data-testid="locate-error-state"]')).toBeNull()
  })
})

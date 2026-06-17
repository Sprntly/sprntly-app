/**
 * @vitest-environment jsdom
 *
 * The async locate contract on the frontend: POST → job-id → poll, with
 * an overall timeout, transient-failure backoff retry, and an EXPLICIT error
 * state (message + Retry) that must NOT silently collapse back to the config
 * (PRD) form.
 *
 * jsdom + @testing-library/react so we can drive the real POST→poll loop through
 * its phases (the node-env sibling suites bypass effects via _test* props). The
 * poll interval/timeout are shrunk via _testPoll* so the loop runs fast under
 * waitFor without fake timers; the timeout test drives the clock explicitly.
 */
import * as React from "react"
import { render, waitFor, act } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), toast: null }),
}))

vi.mock("../DesignAgentDrawer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../DesignAgentDrawer")>()
  return { ...actual, runGenerateFlow: vi.fn().mockResolvedValue(undefined) }
})

import { GenerateModal } from "../GenerateModal"
import { runGenerateFlow } from "../DesignAgentDrawer"
import {
  ApiError,
  designAgentApi,
  type ConnectionSummary,
  type GitHubRepo,
  type LocateResponse,
  type LocateJobStatus,
} from "../../../lib/api"

// ── Fixtures ────────────────────────────────────────────────────────────────

const SEL_REPO = "org/repo"
const PRD_ID = 99

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
      {
        id: "/dashboard",
        route: "/dashboard",
        entry_component: "DashboardPage",
        confidence: 0.5,
        rationale: "alt",
        ambiguous: true,
        component_count: 7,
      },
    ],
    top_confidence: 0.9,
    threshold: 0.8,
    repo: SEL_REPO,
    posture: "CLEAN",
    unmapped: false,
    commit_sha: "shaABC",
    ...overrides,
  }
}

function manualProps(overrides: Record<string, unknown> = {}) {
  return {
    open: true,
    onClose: vi.fn(),
    prdId: PRD_ID,
    figmaFileKey: null,
    _testConnections: GITHUB_CONN,
    _testRepos: REPOS,
    _testInitSource: "github" as const,
    _testInitRepoSel: SEL_REPO,
    // Zero inter-poll delay so the loop settles fast under waitFor.
    _testPollIntervalMs: 0,
    _testPollTimeoutMs: 5000,
    _testPollMaxRetries: 4,
    ...overrides,
  }
}

function clickGenerate(container: HTMLElement) {
  const btn = container.querySelector<HTMLButtonElement>(
    '[data-testid="generate-btn"]',
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

// ── POST → poll happy paths (each decision branch) ────────────────────────────

describe("POST → poll happy path reaches each decision branch", () => {
  it("running → done(auto_proceed) kicks off generation on the located screen", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-1",
      status: "running",
    })
    // First poll still running, second poll done — proves the loop polls.
    vi.spyOn(designAgentApi, "locateJob")
      .mockResolvedValueOnce({ status: "running" })
      .mockResolvedValueOnce({ status: "done", result: makeLocate() })

    const { container } = render(React.createElement(GenerateModal, manualProps()))
    clickGenerate(container)

    await waitFor(() => expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1))
    expect(designAgentApi.locate).toHaveBeenCalledWith({
      prd_id: PRD_ID,
      github_repo: SEL_REPO,
    })
    const params = (vi.mocked(runGenerateFlow).mock.calls[0]![0] as {
      params: Record<string, unknown>
    }).params
    expect(params["chosen_screen_route"]).toBe("/team")
  })

  it("running → done(ranked_confirm) surfaces the inline picker (no auto-generate)", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-2",
      status: "running",
    })
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
      status: "done",
      result: makeLocate({ decision: "ranked_confirm", chosen: [] }),
    })

    const { container } = render(React.createElement(GenerateModal, manualProps()))
    clickGenerate(container)

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-confirm-surface"]'),
      ).toBeTruthy(),
    )
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })

  it("running → done(unmapped) surfaces the unmapped-resolve UI (no auto-generate)", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-3",
      status: "running",
    })
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
      status: "done",
      result: makeLocate({ unmapped: true, chosen: [], decision: "ranked_confirm" }),
    })

    const { container } = render(React.createElement(GenerateModal, manualProps()))
    clickGenerate(container)

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="unmapped-resolve"]'),
      ).toBeTruthy(),
    )
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })
})

// ── transient 5xx is retried, then succeeds ───────────────────────────────────

describe("transient poll failures are retried then succeed", () => {
  it("a 503 poll GET is retried and the loop still resolves", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-4",
      status: "running",
    })
    const jobSpy = vi
      .spyOn(designAgentApi, "locateJob")
      .mockRejectedValueOnce(new ApiError(503, { detail: "upstream down" }))
      .mockResolvedValueOnce({ status: "done", result: makeLocate() })

    const { container } = render(React.createElement(GenerateModal, manualProps()))
    clickGenerate(container)

    await waitFor(() => expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1))
    // Two GETs: the 503 (retried) and the successful one.
    expect(jobSpy).toHaveBeenCalledTimes(2)
    // No error surface — the transient failure was absorbed.
    expect(container.querySelector('[data-testid="locate-error-state"]')).toBeNull()
  })

  it("a transient POST 5xx is retried before the job starts", async () => {
    const postSpy = vi
      .spyOn(designAgentApi, "locate")
      .mockRejectedValueOnce(new ApiError(500, { detail: "boom" }))
      .mockResolvedValueOnce({ job_id: "job-5", status: "running" })
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
      status: "done",
      result: makeLocate(),
    })

    const { container } = render(React.createElement(GenerateModal, manualProps()))
    clickGenerate(container)

    await waitFor(() => expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1))
    expect(postSpy).toHaveBeenCalledTimes(2)
  })
})

// ── terminal errors → explicit error state, NO PRD collapse ───────────────────

describe("terminal locate failures surface the explicit error state (no PRD collapse)", () => {
  it("poll status 'error' → error message + Retry, NOT the config form", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-6",
      status: "running",
    })
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
      status: "error",
      error: "mapper crashed",
    })

    const { container } = render(React.createElement(GenerateModal, manualProps()))
    clickGenerate(container)

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-error-state"]'),
      ).toBeTruthy(),
    )
    // The explicit error message + Retry affordance are shown.
    expect(
      container.querySelector('[data-testid="locate-error"]')?.textContent,
    ).toContain("mapper crashed")
    expect(container.querySelector('[data-testid="locate-retry"]')).toBeTruthy()
    // LOAD-BEARING: it did NOT collapse to the config (PRD) form.
    expect(container.querySelector('[data-testid="generate-btn"]')).toBeNull()
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })

  it("404 on the job poll is terminal (not retried) → error state", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-7",
      status: "running",
    })
    const jobSpy = vi
      .spyOn(designAgentApi, "locateJob")
      .mockRejectedValue(new ApiError(404, { detail: "unknown job" }))

    const { container } = render(React.createElement(GenerateModal, manualProps()))
    clickGenerate(container)

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-error-state"]'),
      ).toBeTruthy(),
    )
    // A 404 is terminal: exactly one poll GET, no retry storm.
    expect(jobSpy).toHaveBeenCalledTimes(1)
    expect(container.querySelector('[data-testid="generate-btn"]')).toBeNull()
  })

  it("a 404 on the POST is terminal → error state (not retried)", async () => {
    const postSpy = vi
      .spyOn(designAgentApi, "locate")
      .mockRejectedValue(new ApiError(404, { detail: "feature off" }))

    const { container } = render(React.createElement(GenerateModal, manualProps()))
    clickGenerate(container)

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-error-state"]'),
      ).toBeTruthy(),
    )
    expect(postSpy).toHaveBeenCalledTimes(1)
    expect(container.querySelector('[data-testid="generate-btn"]')).toBeNull()
  })

  it("exhausting the transient retry budget → error state", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-8",
      status: "running",
    })
    // Always 5xx — retries exhaust and the loop fails.
    vi.spyOn(designAgentApi, "locateJob").mockRejectedValue(
      new ApiError(502, { detail: "still down" }),
    )

    const { container } = render(
      React.createElement(GenerateModal, manualProps({ _testPollMaxRetries: 2 })),
    )
    clickGenerate(container)

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-error-state"]'),
      ).toBeTruthy(),
    )
    expect(container.querySelector('[data-testid="generate-btn"]')).toBeNull()
  })
})

// ── timeout → error state (not PRD collapse) ──────────────────────────────────

describe("overall timeout surfaces the error state, not a hang or PRD collapse", () => {
  it("a never-finishing job hits the timeout cap → error message + Retry", async () => {
    vi.useFakeTimers()
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-9",
      status: "running",
    })
    // The job never finishes — every poll says running.
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({ status: "running" })

    const { container } = render(
      React.createElement(
        GenerateModal,
        // Real-ish interval + a small timeout cap so the deadline trips.
        manualProps({ _testPollIntervalMs: 10, _testPollTimeoutMs: 30 }),
      ),
    )
    clickGenerate(container)

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
    // No collapse to config; generation never started.
    expect(container.querySelector('[data-testid="generate-btn"]')).toBeNull()
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })
})

// ── Retry re-runs the whole locate from the POST ──────────────────────────────

describe("Retry re-runs locate from the POST", () => {
  it("clicking Retry after an error re-POSTs and can then succeed", async () => {
    const postSpy = vi
      .spyOn(designAgentApi, "locate")
      .mockResolvedValueOnce({ job_id: "job-a", status: "running" })
      .mockResolvedValueOnce({ job_id: "job-b", status: "running" })
    const jobSpy = vi
      .spyOn(designAgentApi, "locateJob")
      // First flow: error.
      .mockResolvedValueOnce({ status: "error", error: "first attempt failed" })
      // Second flow (after Retry): done.
      .mockResolvedValueOnce({ status: "done", result: makeLocate() })

    const { container } = render(React.createElement(GenerateModal, manualProps()))
    clickGenerate(container)

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-error-state"]'),
      ).toBeTruthy(),
    )
    expect(postSpy).toHaveBeenCalledTimes(1)

    // Click Retry → a second POST fires and the flow proceeds to generation.
    const retry = container.querySelector<HTMLButtonElement>(
      '[data-testid="locate-retry"]',
    )
    expect(retry).toBeTruthy()
    act(() => retry!.click())

    await waitFor(() => expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1))
    expect(postSpy).toHaveBeenCalledTimes(2)
    expect(jobSpy).toHaveBeenCalledTimes(2)
    // The error surface is gone once the retry succeeds.
    expect(container.querySelector('[data-testid="locate-error-state"]')).toBeNull()
  })
})

// ── abort on unmount (no leak, no setState-after-unmount) ──────────────────────

describe("the poll aborts on unmount", () => {
  it("unmounting mid-poll stops the loop and does not start generation", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-c",
      status: "running",
    })
    let pollCount = 0
    vi.spyOn(designAgentApi, "locateJob").mockImplementation(
      async (): Promise<LocateJobStatus> => {
        pollCount++
        return { status: "running" }
      },
    )

    const { container, unmount } = render(
      React.createElement(
        GenerateModal,
        manualProps({ _testPollIntervalMs: 5, _testPollTimeoutMs: 5000 }),
      ),
    )
    clickGenerate(container)

    // Let a poll or two happen.
    await waitFor(() => expect(pollCount).toBeGreaterThanOrEqual(1))

    unmount()
    const countAtUnmount = pollCount

    // Give the loop ample time; it must be aborted (no further polls, no
    // generation, no thrown setState-after-unmount).
    await new Promise((r) => setTimeout(r, 60))
    expect(pollCount).toBeLessThanOrEqual(countAtUnmount + 1)
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })
})

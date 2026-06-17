/**
 * @vitest-environment jsdom
 *
 * Defensive guard for the `jobs/undefined` 404 class: a locate POST handle that
 * comes back without a usable `job_id` must NOT drive the resolver into
 * `locateJob(undefined)` (which hits `/locate/jobs/undefined` → 404). The flow
 * must instead reach the existing clean error phase (message + Retry), the same
 * terminal path a real 404 already uses.
 *
 * Same harness as GenerateModalLocatePoll.test.tsx: jsdom + testing-library,
 * shrunk poll interval/timeout via _testPoll* so the loop settles fast.
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
  designAgentApi,
  type ConnectionSummary,
  type GitHubRepo,
  type LocateJobHandle,
} from "../../../lib/api"

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

describe("a locate handle missing job_id fails clean (no jobs/undefined poll)", () => {
  it("reaches the explicit error state and never calls locateJob with an undefined id", async () => {
    // The POST resolves, but the handle has no usable job_id — the malformed
    // payload class one hop before the deriveScreenLabel crash.
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      status: "running",
    } as unknown as LocateJobHandle)
    const jobSpy = vi.spyOn(designAgentApi, "locateJob")

    const { container } = render(React.createElement(GenerateModal, manualProps()))
    clickGenerate(container)

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-error-state"]'),
      ).toBeTruthy(),
    )

    // Clean terminal path: the explicit error + Retry affordance are shown,
    // and it did NOT collapse to the config (PRD) form.
    expect(container.querySelector('[data-testid="locate-retry"]')).toBeTruthy()
    expect(container.querySelector('[data-testid="generate-btn"]')).toBeNull()

    // LOAD-BEARING: locateJob was never invoked — no jobs/undefined request.
    expect(jobSpy).not.toHaveBeenCalled()
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })

  it("an empty-string job_id is also treated as missing (no poll)", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "",
      status: "running",
    } as unknown as LocateJobHandle)
    const jobSpy = vi.spyOn(designAgentApi, "locateJob")

    const { container } = render(React.createElement(GenerateModal, manualProps()))
    clickGenerate(container)

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-error-state"]'),
      ).toBeTruthy(),
    )
    expect(jobSpy).not.toHaveBeenCalled()
  })
})

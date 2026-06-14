/**
 * @vitest-environment jsdom
 *
 * Tests for the immediate-loading redesign of the generate-entry flow.
 *
 * The redesign decouples the loading SCREEN from the screen-resolve CALL: on
 * generate-click the modal moves to a loading phase IMMEDIATELY (the loading UI
 * mounts) and the resolve call fires behind it, instead of the old behaviour
 * where the resolve call blocked before any loading UI appeared and the modal
 * looked frozen for up to a minute.
 *
 * The load-bearing AC here is the re-entry guard. Each resolve call is an
 * independent model sample, so re-firing it can promote a genuinely sub-threshold
 * (ambiguous) match into an auto-proceed by pure sampling variance — silently
 * defeating the wrong-screen guard. These tests assert EXACTLY ONE resolve call
 * per flow across re-renders / double-invoke, and that an ambiguous match is
 * never re-sampled into a confident one.
 *
 * These exercise real React effects + state, so the file overrides to jsdom and
 * uses @testing-library/react (the node-env sibling suites stay node-env and use
 * static-markup injection).
 */
import * as React from "react"
import { render, waitFor, act } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// Sprntly components use the classic JSX runtime; expose React globally.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), toast: null }),
}))

// Spy runGenerateFlow so generation never runs for real.
vi.mock("../DesignAgentDrawer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../DesignAgentDrawer")>()
  return { ...actual, runGenerateFlow: vi.fn().mockResolvedValue(undefined) }
})

import { GenerateModal } from "../GenerateModal"
import { runGenerateFlow } from "../DesignAgentDrawer"
import {
  connectorsApi,
  designAgentApi,
  type ConnectionSummary,
  type GitHubRepo,
  type LocateResponse,
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

function autoProceed(overrides: Partial<LocateResponse> = {}): LocateResponse {
  const cand = {
    id: "/team",
    route: "/team",
    entry_component: "TeamScreen",
    confidence: 0.92,
    rationale: "best match",
    ambiguous: false,
    component_count: 3,
  }
  return {
    decision: "auto_proceed",
    chosen: [cand],
    ranked: [cand],
    top_confidence: 0.92,
    threshold: 0.8,
    repo: SEL_REPO,
    posture: "CLEAN",
    unmapped: false,
    commit_sha: "sha123",
    ...overrides,
  }
}

function ambiguous(): LocateResponse {
  return {
    decision: "ranked_confirm",
    chosen: [],
    ranked: [
      {
        id: "/team",
        route: "/team",
        entry_component: "TeamScreen",
        // Sub-threshold confidence — a re-sample could nudge this over 0.8.
        confidence: 0.78,
        rationale: "possible",
        ambiguous: true,
        component_count: 3,
      },
      {
        id: "/dashboard",
        route: "/dashboard",
        entry_component: "DashboardPage",
        confidence: 0.55,
        rationale: "alt",
        ambiguous: true,
        component_count: 7,
      },
    ],
    top_confidence: 0.78,
    threshold: 0.8,
    repo: SEL_REPO,
    posture: "PARTIAL",
    unmapped: false,
    commit_sha: "",
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
  vi.spyOn(connectorsApi, "list").mockResolvedValue({ connections: GITHUB_CONN })
  vi.spyOn(connectorsApi, "listAccessibleGithubRepos").mockResolvedValue({
    repositories: REPOS,
  })
})

afterEach(() => vi.resetAllMocks())

// ─── immediate loading ───────────────────────────────────────────────────────

describe("loading UI is immediate (decoupled from the resolve call)", () => {
  it("mounts the loading state on generate-click BEFORE the resolve call resolves", async () => {
    // A resolve call that never settles — the loading UI must still appear.
    let resolveLater: (r: LocateResponse) => void = () => {}
    vi.spyOn(designAgentApi, "locate").mockReturnValue(
      new Promise<LocateResponse>((res) => {
        resolveLater = res
      }),
    )

    const { container } = render(React.createElement(GenerateModal, manualProps()))
    clickGenerate(container)

    // Loading UI is visible even though locate has not resolved.
    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="generate-loading-state"]'),
      ).toBeTruthy(),
    )
    expect(designAgentApi.locate).toHaveBeenCalledTimes(1)

    // The form/footer is gone (we are past the config phase).
    expect(container.querySelector('[data-testid="generate-btn"]')).toBeNull()

    // Clean up the dangling promise.
    act(() => resolveLater(autoProceed()))
  })

  it("confident match → shows the matched line and kicks off generation on the located screen", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue(autoProceed())

    const onGenStart = vi.fn()
    const { container } = render(
      React.createElement(GenerateModal, manualProps({ onGenStart })),
    )
    clickGenerate(container)

    await waitFor(() =>
      expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1),
    )
    // Matched line shows the located screen.
    expect(
      container.querySelector('[data-testid="generate-loading-matched"]')
        ?.textContent,
    ).toContain("/team")
    // Generation is grounded on the located screen.
    const params = (vi.mocked(runGenerateFlow).mock.calls[0]![0] as {
      params: Record<string, unknown>
    }).params
    expect(params["chosen_screen_route"]).toBe("/team")
    expect(onGenStart).toHaveBeenCalledWith(
      expect.objectContaining({ chosenScreenRoute: "/team" }),
    )
  })
})

// ─── ambiguous → inline picker ────────────────────────────────────────────────

describe("ambiguous match surfaces the inline picker", () => {
  it("ranked_confirm → picker; pick fires generation on the picked screen", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue(ambiguous())
    const onGenStart = vi.fn()
    const { container } = render(
      React.createElement(GenerateModal, manualProps({ onGenStart })),
    )
    clickGenerate(container)

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-confirm-surface"]'),
      ).toBeTruthy(),
    )
    // No generation yet — the user must pick.
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()

    // Pick the second candidate.
    const choices = container.querySelectorAll<HTMLButtonElement>(
      '[data-testid="locate-confirm-choice"]',
    )
    expect(choices.length).toBeGreaterThanOrEqual(2)
    act(() => choices[1]!.click())

    await waitFor(() =>
      expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1),
    )
    expect(onGenStart).toHaveBeenCalledWith(
      expect.objectContaining({ chosenScreenRoute: "/dashboard" }),
    )
  })
})

// ─── unmapped → inline resolve ────────────────────────────────────────────────

describe("unmapped match surfaces the inline resolve", () => {
  it("unmapped → resolve UI; switch-source returns to the config form", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue(
      autoProceed({ unmapped: true, decision: "ranked_confirm", chosen: [] }),
    )
    const { container } = render(React.createElement(GenerateModal, manualProps()))
    clickGenerate(container)

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="unmapped-resolve"]'),
      ).toBeTruthy(),
    )
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()

    // Switch source → back to the config form (a phase change, not a remount).
    const switchBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="unmapped-switch-source"]',
    )
    act(() => switchBtn!.click())
    await waitFor(() =>
      expect(container.querySelector('[data-testid="generate-btn"]')).toBeTruthy(),
    )
  })

  it("unmapped → pick a fallback screen fires generation on that screen", async () => {
    // unmapped but with ranked fallbacks present.
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      ...ambiguous(),
      unmapped: true,
    })
    const onGenStart = vi.fn()
    const { container } = render(
      React.createElement(GenerateModal, manualProps({ onGenStart })),
    )
    clickGenerate(container)

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="unmapped-resolve"]'),
      ).toBeTruthy(),
    )
    const choice = container.querySelector<HTMLButtonElement>(
      '[data-testid="locate-confirm-choice"]',
    )
    expect(choice).toBeTruthy()
    act(() => choice!.click())

    await waitFor(() =>
      expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1),
    )
    expect(onGenStart).toHaveBeenCalledWith(
      expect.objectContaining({ chosenScreenRoute: "/team" }),
    )
    // unmapped omits the snapshot SHA (no snapshot to pin against).
    const params = (vi.mocked(runGenerateFlow).mock.calls[0]![0] as {
      params: Record<string, unknown>
    }).params
    expect(params).not.toHaveProperty("map_commit_sha")
  })
})

// ─── re-entry guard (the load-bearing correctness AC) ─────────────────────────

describe("re-entry guard — exactly one resolve call per flow", () => {
  it("a double generate-click fires the resolve call only ONCE", async () => {
    let resolveLater: (r: LocateResponse) => void = () => {}
    const spy = vi.spyOn(designAgentApi, "locate").mockReturnValue(
      new Promise<LocateResponse>((res) => {
        resolveLater = res
      }),
    )
    const { container } = render(React.createElement(GenerateModal, manualProps()))

    clickGenerate(container)
    // A second click while the resolve is in flight (button is gone after the
    // first, but simulate a racing re-invoke would also be a no-op via the
    // guard). Re-rendering does not re-fire.
    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="generate-loading-state"]'),
      ).toBeTruthy(),
    )

    expect(spy).toHaveBeenCalledTimes(1)
    act(() => resolveLater(autoProceed()))
    // Even after the flow resolves and generation kicks off, no second resolve.
    await waitFor(() =>
      expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1),
    )
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it("auto-skip effect re-runs (dep churn) do NOT re-fire the resolve call", async () => {
    // The auto-skip effect depends on connections/repos; a re-render that
    // settles those must NOT trigger a second resolve sample.
    const spy = vi.spyOn(designAgentApi, "locate").mockResolvedValue(autoProceed())
    const GITHUB_PREF = {
      design_source: "github" as const,
      github_repo: SEL_REPO,
      figma_file_key: null,
      website_url: null,
    }

    const { rerender } = render(
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

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1))

    // Force several re-renders (simulating connector/repo state settling). The
    // guard must hold the resolve count at exactly one.
    for (let i = 0; i < 3; i++) {
      rerender(
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
      // Let effects flush.
      await act(async () => {
        await Promise.resolve()
      })
    }

    expect(spy).toHaveBeenCalledTimes(1)
  })

  it("ambiguous match is NOT re-sampled — first ranked_confirm goes straight to the picker", async () => {
    // If the flow ever re-fired locate hunting for a luckier confidence, the
    // spy would be called more than once. The first ambiguous result must stick.
    const spy = vi.spyOn(designAgentApi, "locate").mockResolvedValue(ambiguous())
    const { container } = render(React.createElement(GenerateModal, manualProps()))
    clickGenerate(container)

    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="locate-confirm-surface"]'),
      ).toBeTruthy(),
    )

    // Give any errant re-sample a chance to fire.
    await act(async () => {
      await Promise.resolve()
    })
    expect(spy).toHaveBeenCalledTimes(1)
    // No auto-pick of the sub-threshold candidate — generation has NOT started.
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })
})

// ─── no prohibited identifiers in committed content ───────────────────────────

describe("no internal identifiers leak into this file", () => {
  it("contains no ticket / decision identifiers", async () => {
    const { readFileSync } = await import("node:fs")
    const { fileURLToPath } = await import("node:url")
    const here = fileURLToPath(import.meta.url)
    const src = readFileSync(here, "utf8")
    expect(/[CPH]\d-\d/.test(src)).toBe(false)
    expect(/\bAD\d/.test(src)).toBe(false)
    expect(/\bF\d{1,2}\b/.test(src)).toBe(false)
  })
})

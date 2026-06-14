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
import { render, waitFor } from "@testing-library/react"
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
    const locateSpy = vi.spyOn(designAgentApi, "locate").mockResolvedValue(makeLocate())

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
    vi.spyOn(designAgentApi, "locate").mockResolvedValue(makeLocate())

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

  it("fires onGenStart with the chosen route + repo before generation", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue(makeLocate())
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

    await waitFor(() =>
      expect(onGenStart).toHaveBeenCalledWith(
        expect.objectContaining({
          githubRepo: SEL_REPO,
          chosenScreenRoute: "/team",
        }),
      ),
    )
  })

  it("at ranked_confirm does NOT auto-generate (re-opens the picker, no generate call)", async () => {
    const onClose = vi.fn()
    vi.spyOn(designAgentApi, "locate").mockResolvedValue(
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
    vi.spyOn(designAgentApi, "locate").mockResolvedValue(
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
    const locateSpy = vi.spyOn(designAgentApi, "locate").mockResolvedValue(makeLocate())

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
    const locateSpy = vi.spyOn(designAgentApi, "locate").mockResolvedValue(makeLocate())

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

/**
 * Tests for the /generate body builder in GenerateModal's codebase mode.
 *
 * Verifies that the two new wiring fields — chosen_screen_route +
 * map_commit_sha — only appear on the wire when the modal is in codebase
 * mode AND the locate gate yielded a non-empty SHA. Figma and website modes
 * never carry either key, and an unmapped locate (empty SHA) sends the
 * chosen_screen_route alone (the backend gracefully resolves to None on the
 * snapshot it does not have).
 *
 * Node-env vitest, no DOM. We render the modal, click the Generate button
 * (captureButtons), and assert the params object that runGenerateFlow
 * receives.
 */
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), toast: null }),
}))

vi.mock("../DesignAgentDrawer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../DesignAgentDrawer")>()
  return { ...actual, runGenerateFlow: vi.fn().mockResolvedValue(undefined) }
})

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { GenerateModal } from "../GenerateModal"
import { runGenerateFlow } from "../DesignAgentDrawer"
import {
  designAgentApi,
  type ConnectionSummary,
  type GitHubRepo,
  type LocateResponse,
} from "../../../lib/api"

beforeEach(() => {
  vi.mocked(runGenerateFlow).mockResolvedValue(undefined)
})

afterEach(() => vi.resetAllMocks())

// ── Fixtures ────────────────────────────────────────────────────────────────

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
    full_name: "org/repo",
    name: "repo",
    private: false,
    html_url: "https://github.com/org/repo",
    default_branch: "main",
    description: null,
    updated_at: "2024-01-01T00:00:00Z",
    stargazers_count: 0,
  },
]

const SEL_REPO = "org/repo"
const PRD_ID = 99

function makeLocate(overrides: Partial<LocateResponse> = {}): LocateResponse {
  return {
    decision: "auto_proceed",
    chosen: [
      {
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
    commit_sha: "shaABC",
    ...overrides,
  }
}

// ── Render helpers ──────────────────────────────────────────────────────────

type ModalProps = Parameters<typeof GenerateModal>[0]

function baseCodebaseProps(): ModalProps {
  return {
    open: true,
    onClose: vi.fn(),
    prdId: PRD_ID,
    figmaFileKey: null,
    _testConnections: GITHUB_CONN,
    _testRepos: REPOS,
    _testInitSource: "github",
    _testInitRepoSel: SEL_REPO,
  }
}

function baseFigmaProps(): ModalProps {
  return {
    open: true,
    onClose: vi.fn(),
    prdId: PRD_ID,
    figmaFileKey: "figma-key-abc",
    _testConnections: FIGMA_CONN,
    _testRepos: null,
    _testInitSource: "figma",
  }
}

function baseWebsiteProps(): ModalProps {
  return {
    open: true,
    onClose: vi.fn(),
    prdId: PRD_ID,
    figmaFileKey: null,
    _testConnections: [],
    _testRepos: null,
    _testInitSource: "website",
  }
}

function captureButtons(props: ModalProps): Record<string, unknown>[] {
  const real = (globalThis as { React?: typeof React }).React!
  const realCreate = real.createElement
  const captured: Record<string, unknown>[] = []
  ;(globalThis as { React?: unknown }).React = {
    ...real,
    createElement: (
      type: unknown,
      p: Record<string, unknown> | null,
      ...kids: unknown[]
    ) => {
      if (type === "button") captured.push(p ?? {})
      return (realCreate as (...a: unknown[]) => unknown)(type, p, ...kids)
    },
  }
  try {
    renderToStaticMarkup(
      (realCreate as (...a: unknown[]) => React.ReactElement)(GenerateModal, props),
    )
  } finally {
    ;(globalThis as { React?: unknown }).React = real
  }
  return captured
}

async function flushAsync(): Promise<void> {
  await new Promise<void>((resolve) => setTimeout(resolve, 0))
}

function lastGenerateParams(): Record<string, unknown> {
  const calls = vi.mocked(runGenerateFlow).mock.calls
  expect(calls.length).toBeGreaterThan(0)
  const arg = calls[calls.length - 1]![0] as { params: Record<string, unknown> }
  return arg.params
}

// ─── Codebase mode sends both keys ───────────────────────────────────────────

describe("test_codebase_mode_sends_route_and_commit_sha", () => {
  it("includes chosen_screen_route + map_commit_sha on the auto_proceed path", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue(makeLocate())
    const buttons = captureButtons(baseCodebaseProps())
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    expect(btn).toBeDefined()
    ;(btn!["onClick"] as () => void)()
    await flushAsync()

    const params = lastGenerateParams()
    expect(params["chosen_screen_route"]).toBe("/team")
    expect(params["map_commit_sha"]).toBe("shaABC")
    // The repo and source selectors are still set.
    expect(params["github_repo"]).toBe(SEL_REPO)
    expect(params["design_source"]).toBe("github")
  })
})

// ─── Figma mode never carries either key ─────────────────────────────────────

describe("test_figma_mode_omits_route_and_sha_keys", () => {
  it("does not include chosen_screen_route or map_commit_sha in figma mode", async () => {
    const buttons = captureButtons(baseFigmaProps())
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    expect(btn).toBeDefined()
    ;(btn!["onClick"] as () => void)()
    await flushAsync()

    const params = lastGenerateParams()
    expect(params).not.toHaveProperty("chosen_screen_route")
    expect(params).not.toHaveProperty("map_commit_sha")
    expect(params["design_source"]).toBe("figma")
    expect(params["figma_file_key"]).toBe("figma-key-abc")
  })
})

// ─── Website mode never carries either key ───────────────────────────────────

describe("test_website_mode_omits_route_and_sha_keys", () => {
  it("does not include chosen_screen_route or map_commit_sha in website mode", async () => {
    const buttons = captureButtons(baseWebsiteProps())
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    expect(btn).toBeDefined()
    ;(btn!["onClick"] as () => void)()
    await flushAsync()

    const params = lastGenerateParams()
    expect(params).not.toHaveProperty("chosen_screen_route")
    expect(params).not.toHaveProperty("map_commit_sha")
    expect(params["design_source"]).toBe("website")
  })
})

// ─── Empty SHA → chosen_screen_route alone (sha key omitted) ─────────────────

describe("test_empty_sha_not_sent", () => {
  it("omits map_commit_sha when the locate response has empty commit_sha", async () => {
    // A mapped locate with non-empty chosen but empty SHA — the modal still
    // has a route to send but no snapshot to pin against. The body carries
    // the route alone; the backend gracefully resolves to None.
    vi.spyOn(designAgentApi, "locate").mockResolvedValue(
      makeLocate({ commit_sha: "" }),
    )
    const buttons = captureButtons(baseCodebaseProps())
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    ;(btn!["onClick"] as () => void)()
    await flushAsync()

    const params = lastGenerateParams()
    expect(params["chosen_screen_route"]).toBe("/team")
    expect(params).not.toHaveProperty("map_commit_sha")
  })
})

// ─── Non-codebase auto path — direct runGenerateForRoute call ───────────────

describe("test_codebase_mode_without_route_omits_chosen_screen_route", () => {
  it("does not send chosen_screen_route when no route was ever chosen", async () => {
    // unmapped locate → modal does NOT call runGenerateFlow at all (the
    // unmapped fallback UI takes over). Assert no call landed.
    vi.spyOn(designAgentApi, "locate").mockResolvedValue(
      makeLocate({ unmapped: true, chosen: [], decision: "ranked_confirm" }),
    )
    const buttons = captureButtons(baseCodebaseProps())
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    ;(btn!["onClick"] as () => void)()
    await flushAsync()

    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })
})

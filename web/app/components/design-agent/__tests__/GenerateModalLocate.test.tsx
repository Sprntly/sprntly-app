/**
 * Tests for GenerateModal codebase source mode and locate-UX state machine.
 * Node-env vitest, renderToStaticMarkup for markup assertions;
 * captureButtonProps for onClick wiring (no DOM / no testing-library).
 */
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// useNavigation reaches next/navigation (unavailable in node-env). Stub it out.
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), toast: null }),
}))

// runGenerateFlow drives real network I/O. Replace it with a no-op spy so
// generation does not actually run; we only verify it is (or is not) invoked.
vi.mock("../DesignAgentDrawer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../DesignAgentDrawer")>()
  return { ...actual, runGenerateFlow: vi.fn().mockResolvedValue(undefined) }
})

// Sprntly components use the classic JSX runtime; expose React globally.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { GenerateModal, mapLocateCandidates } from "../GenerateModal"
import { runGenerateFlow } from "../DesignAgentDrawer"
import {
  designAgentApi,
  type ConnectionSummary,
  type GitHubRepo,
  type LocateResponse,
} from "../../../lib/api"

// Ensure runGenerateFlow always returns a Promise so the .catch() in
// runGenerateForRoute never throws "Cannot read properties of undefined".
beforeEach(() => {
  vi.mocked(runGenerateFlow).mockResolvedValue(undefined)
})

afterEach(() => vi.resetAllMocks())

// ─── Fixtures ────────────────────────────────────────────────────────────────

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
const PRD_ID = 42

const AUTO_PROCEED: LocateResponse = {
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
  commit_sha: "",
}

const RANKED_CONFIRM: LocateResponse = {
  decision: "ranked_confirm",
  chosen: [],
  ranked: [
    {
      id: "/team",
      route: "/team",
      entry_component: "TeamScreen",
      confidence: 0.6,
      rationale: "possible match",
      ambiguous: true,
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
  top_confidence: 0.6,
  threshold: 0.8,
  repo: SEL_REPO,
  posture: "PARTIAL",
  unmapped: false,
  commit_sha: "",
}

// ─── Render helpers ──────────────────────────────────────────────────────────

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
    // Poll overrides so the live POST→poll loop completes within a single
    // flushAsync tick (zero inter-poll delay, generous overall timeout).
    _testPollIntervalMs: 0,
    _testPollTimeoutMs: 5000,
  }
}

function renderModal(overrides: Partial<ModalProps> = {}): string {
  return renderToStaticMarkup(
    React.createElement(GenerateModal, { ...baseCodebaseProps(), ...overrides }),
  )
}

/**
 * Captures all button element props rendered by GenerateModal.
 * Matches the captureButtonProps pattern used by sibling test suites.
 */
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

/** Flush pending microtasks so async locate IIFE continuations can complete. */
async function flushAsync(): Promise<void> {
  await new Promise<void>((resolve) => setTimeout(resolve, 0))
}

/**
 * Mock the ASYNC locate contract: the POST returns a job handle and the
 * first poll resolves with the given LocateResponse. This is the poll-shape
 * replacement for the old single `locate().mockResolvedValue(<LocateResponse>)`.
 */
function mockLocateResolves(result: LocateResponse) {
  vi.spyOn(designAgentApi, "locate").mockResolvedValue({
    job_id: "job-1",
    status: "running",
  })
  vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
    status: "done",
    result,
  })
}

// ─── mapLocateCandidates ─────────────────────────────────────────────────────

describe("mapLocateCandidates helper", () => {
  it("maps candidates preserving route/entry_component/component_count, marks first as is_top", () => {
    const result = mapLocateCandidates(AUTO_PROCEED.ranked)
    expect(result).toHaveLength(1)
    expect(result[0]!.route).toBe("/team")
    expect(result[0]!.entry_component).toBe("TeamScreen")
    expect(result[0]!.component_count).toBe(3)
    expect(result[0]!.is_top).toBe(true)
  })

  it("marks is_top only on index 0 for multi-candidate ranked list", () => {
    const result = mapLocateCandidates(RANKED_CONFIRM.ranked)
    expect(result[0]!.is_top).toBe(true)
    expect(result[1]!.is_top).toBe(false)
  })

  it("returns empty array for empty input", () => {
    expect(mapLocateCandidates([])).toEqual([])
  })
})

// ─── codebase source label and enable ───────────────────────────────────

describe("codebase source label and enable", () => {
  it("renders 'From our codebase' as the github source option label", () => {
    const html = renderModal()
    expect(html).toContain("From our codebase")
  })

  it("github source pill has aria-pressed=true when designSource=github", () => {
    const html = renderModal()
    expect(html).toMatch(/data-val="github"[^>]*aria-pressed="true"/)
  })

  it("repo selector renders with injected repos when codebase connected", () => {
    const html = renderModal()
    expect(html).toContain(SEL_REPO)
    expect(html).toContain('aria-label="Select a repo"')
  })

  it("renders the Generate button with data-testid in codebase mode", () => {
    const html = renderModal()
    expect(html).toContain('data-testid="generate-btn"')
  })
})

// ─── generate in codebase mode calls locate and shows analysing ─────────

describe("generate in codebase mode calls locate and shows analysing", () => {
  it("calls designAgentApi.locate with prd_id + github_repo when Generate is clicked", async () => {
    mockLocateResolves(AUTO_PROCEED)
    const spy = vi.mocked(designAgentApi.locate)
    const buttons = captureButtons(baseCodebaseProps())
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    expect(btn).toBeDefined()
    ;(btn!["onClick"] as () => void)()
    await flushAsync()
    expect(spy).toHaveBeenCalledWith({ prd_id: PRD_ID, github_repo: SEL_REPO })
  })

  it("renders the animated loading state (not a static label) when in the locating phase", () => {
    const html = renderModal({ _testFlowPhase: "locating" })
    expect(html).toContain('data-testid="generate-loading-state"')
    // The indicator is an indeterminate heartbeat, NOT a static label that
    // reads frozen at 8-60s.
    expect(html).toContain('data-testid="generate-loading-heartbeat"')
  })
})

// ─── auto_proceed shows matched and starts generation ───────────────────

describe("auto_proceed shows matched and starts generation", () => {
  it("shows the transient 'matched: <screen>' line when a screen resolves", () => {
    const html = renderModal({ _testFlowPhase: "generating", _testMatchedRoute: "/team" })
    expect(html).toContain('data-testid="generate-loading-matched"')
    expect(html).toContain("Matched")
    expect(html).toContain("/team")
  })

  it("shows the proceed note as subtext beneath the matched line", () => {
    const html = renderModal({
      _testFlowPhase: "generating",
      _testMatchedRoute: "/team",
      _testProceedNote: "closest match, lower confidence",
    })
    expect(html).toContain('data-testid="generate-loading-note"')
    expect(html).toContain("closest match")
  })

  it("fires runGenerateFlow after an auto_proceed locate response", async () => {
    mockLocateResolves(AUTO_PROCEED)
    const buttons = captureButtons(baseCodebaseProps())
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    ;(btn!["onClick"] as () => void)()
    await flushAsync()
    expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1)
  })
})

// ─── proceed_with_note starts generation ────────────────────────────────

describe("proceed_with_note starts generation", () => {
  it("fires runGenerateFlow on proceed_with_note without blocking picker", async () => {
    const proceedWithNote: LocateResponse = { ...AUTO_PROCEED, decision: "proceed_with_note" }
    mockLocateResolves(proceedWithNote)
    const buttons = captureButtons(baseCodebaseProps())
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    ;(btn!["onClick"] as () => void)()
    await flushAsync()
    expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1)
  })
})

// ─── ambiguous match shows picker ───────────────────────────────────────

describe("ambiguous match shows picker", () => {
  it("picker renders with candidates in the picker phase", () => {
    const html = renderModal({
      _testFlowPhase: "picker",
      _testLocateResult: RANKED_CONFIRM,
    })
    expect(html).toContain('data-testid="locate-confirm-surface"')
    // Suggested + alternatives layout: the top candidate (/team) leads with its
    // route-info line; the second candidate surfaces as an alt row (its derived
    // name "Dashboard", not its route string).
    expect(html).toContain("/team")
    expect(html).toContain('data-testid="locate-alt-row"')
    expect(html).toContain("Dashboard")
  })

  it("the config-phase action footer (Generate button) is gone in the picker phase", () => {
    const html = renderModal({
      _testFlowPhase: "picker",
      _testLocateResult: RANKED_CONFIRM,
    })
    expect(html).not.toContain('data-testid="generate-btn"')
  })
})

// ─── pick carries chosen route into genstart ────────────────────────────

describe("pick carries chosen route into genstart", () => {
  it("confirming the suggested candidate calls onGenStart with chosenScreenRoute", () => {
    const onGenStart = vi.fn()
    const buttons = captureButtons({
      ...baseCodebaseProps(),
      onGenStart,
      _testFlowPhase: "picker",
      _testLocateResult: RANKED_CONFIRM,
    })
    // The picker leads with the top candidate (/team) in the Suggested slot;
    // "Use this screen" confirms it (clicking an alt row only promotes).
    const useBtn = buttons.find((b) => b["data-testid"] === "locate-confirm-use")
    expect(useBtn).toBeDefined()
    ;(useBtn!["onClick"] as () => void)()
    expect(onGenStart).toHaveBeenCalledWith(
      expect.objectContaining({ chosenScreenRoute: "/team" }),
    )
  })
})

// ─── unmapped shows resolve, no autostart ────────────────────────────────

describe("unmapped shows resolve, no autostart", () => {
  it("renders the unmapped-resolve UI in the unmapped-resolve phase", () => {
    const html = renderModal({ _testFlowPhase: "unmapped-resolve" })
    expect(html).toContain('data-testid="unmapped-resolve"')
    expect(html).toContain('data-testid="locate-unmapped"')
    expect(html).toContain("couldn")
    // Switch-source affordance back to config is present.
    expect(html).toContain('data-testid="unmapped-switch-source"')
  })

  it("offers the ranked fallbacks as a picker when unmapped carries candidates", () => {
    const html = renderModal({
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: { ...RANKED_CONFIRM, unmapped: true },
    })
    expect(html).toContain('data-testid="locate-confirm-use"')
  })

  it("does not call runGenerateFlow when locate returns unmapped", async () => {
    const unmapped: LocateResponse = { ...RANKED_CONFIRM, unmapped: true }
    mockLocateResolves(unmapped)
    const buttons = captureButtons(baseCodebaseProps())
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    ;(btn!["onClick"] as () => void)()
    await flushAsync()
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })
})

// ─── locate error is explicit, no PRD collapse ───────────────────────────
// The load-bearing behaviour: a failed/errored locate surfaces an EXPLICIT
// error phase with a Retry button — it does NOT silently collapse to the config
// (PRD) form. This replaces the old non-fatal fall-back-to-config behaviour.

describe("locate error is explicit, no PRD collapse", () => {
  it("renders the explicit error surface with role=alert in the error phase", () => {
    const msg = "Codebase analysis failed — try again or switch source"
    const html = renderModal({ _testFlowPhase: "error", _testLocateError: msg })
    expect(html).toContain('data-testid="locate-error-state"')
    expect(html).toContain('data-testid="locate-error"')
    expect(html).toContain('role="alert"')
    expect(html).toContain("failed")
    // A Retry affordance is present.
    expect(html).toContain('data-testid="locate-retry"')
  })

  it("does NOT render the config-phase Generate button in the error phase (no PRD collapse)", () => {
    const html = renderModal({ _testFlowPhase: "error", _testLocateError: "boom" })
    expect(html).not.toContain('data-testid="generate-btn"')
  })
})

// ─── codebase mode requires repo, disabled helper text ───────────────────

describe("codebase mode requires repo, disabled helper text", () => {
  it("shows helper text and disables Generate when codebase mode with no repo selected", () => {
    const html = renderModal({ _testInitRepoSel: "" })
    expect(html).toContain("Connect Figma or a codebase to generate")
    expect(html).toContain('data-testid="codebase-no-repo-helper"')
    expect(html).toMatch(/data-testid="generate-btn"[^>]*disabled=""/)
  })

  it("shows the normal asynchronous-generation helper when a repo is selected", () => {
    const html = renderModal()
    expect(html).toContain("Generation is asynchronous")
    expect(html).not.toContain("Connect Figma or a codebase to generate")
  })
})

// ─── figma mode runs without locate call ────────────────────────────────

describe("figma mode runs without locate call", () => {
  it("clicking Generate in figma mode does not call designAgentApi.locate", async () => {
    const spy = vi.spyOn(designAgentApi, "locate")
    const figmaProps: ModalProps = {
      open: true,
      onClose: vi.fn(),
      prdId: PRD_ID,
      figmaFileKey: null,
      _testConnections: FIGMA_CONN,
      _testRepos: [],
      _testInitSource: "figma",
    }
    const buttons = captureButtons(figmaProps)
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    ;(btn!["onClick"] as () => void)()
    await flushAsync()
    expect(spy).not.toHaveBeenCalled()
  })
})

// ─── approve modal mount unchanged, optional context ─────────────────────

describe("approve modal mount unchanged, optional context", () => {
  it("ApproveModal still imports and mounts GenerateModal with onGenStart", () => {
    const src = readFileSync(
      join(process.cwd(), "app", "components", "shared", "ApproveModal.tsx"),
      "utf8",
    )
    expect(src).toContain("import { GenerateModal }")
    expect(src).toContain("<GenerateModal")
    expect(src).toContain("onGenStart={handleGenStart}")
  })

  it("renders without error when onGenStart omits chosenScreenRoute (existing callers unchanged)", () => {
    const onGenStart = vi.fn(
      (_ctx?: { figmaFileKey?: string | null; githubRepo?: string | null }) => {},
    )
    const html = renderModal({ onGenStart })
    expect(html).toContain('data-testid="generate-btn"')
    // The resting config phase shows the form, not the loading/matched state.
    expect(html).not.toContain('data-testid="generate-loading-matched"')
  })
})

// ─── no prohibited tokens in appended lines ─────────────────────────────

describe("no prohibited tokens in appended lines", () => {
  it("no ticket-series or decision IDs in the new test file", () => {
    const src = readFileSync(
      join(
        process.cwd(),
        "app",
        "components",
        "design-agent",
        "__tests__",
        "GenerateModalLocate.test.tsx",
      ),
      "utf8",
    )
    // Split into separate patterns to avoid the combined literal triggering itself.
    expect(/[CPH]\d-\d/.test(src), "ticket-series ID in test file").toBe(false)
    expect(/\bAD\d/.test(src), "AD-series token in test file").toBe(false)
    expect(/\bF\d{1,2}\b/.test(src), "function-req token in test file").toBe(false)
  })

  it("no ticket/decision IDs in the GenerateModal locate-gate section", () => {
    const src = readFileSync(
      join(process.cwd(), "app", "components", "design-agent", "GenerateModal.tsx"),
      "utf8",
    )
    const section = src.slice(src.indexOf("FlowPhase"))
    expect(/[CPH]\d-\d/.test(section), "ticket-series ID in GenerateModal locate section").toBe(false)
    expect(/\bAD\d/.test(section), "AD-series token in GenerateModal locate section").toBe(false)
    expect(/\bF\d{1,2}\b/.test(section), "function-req token in GenerateModal locate section").toBe(false)
  })
})

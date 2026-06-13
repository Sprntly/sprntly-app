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
  commit_sha: "",
}

const RANKED_CONFIRM: LocateResponse = {
  decision: "ranked_confirm",
  chosen: [],
  ranked: [
    {
      route: "/team",
      entry_component: "TeamScreen",
      confidence: 0.6,
      rationale: "possible match",
      ambiguous: true,
      component_count: 3,
    },
    {
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

// ─── test_codebase_source_label_and_enable ───────────────────────────────────

describe("test_codebase_source_label_and_enable", () => {
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

// ─── test_generate_in_codebase_mode_calls_locate_and_shows_analysing ─────────

describe("test_generate_in_codebase_mode_calls_locate_and_shows_analysing", () => {
  it("calls designAgentApi.locate with prd_id + github_repo when Generate is clicked", async () => {
    const spy = vi.spyOn(designAgentApi, "locate").mockResolvedValue(AUTO_PROCEED)
    const buttons = captureButtons(baseCodebaseProps())
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    expect(btn).toBeDefined()
    ;(btn!["onClick"] as () => void)()
    await flushAsync()
    expect(spy).toHaveBeenCalledWith({ prd_id: PRD_ID, github_repo: SEL_REPO })
  })

  it("renders 'Analysing your codebase' when locateState=analysing", () => {
    const html = renderModal({ _testLocateState: "analysing" })
    expect(html).toContain('data-testid="locate-analysing"')
    expect(html).toContain("Analysing your codebase")
  })
})

// ─── test_auto_proceed_shows_chip_and_starts_generation ──────────────────────

describe("test_auto_proceed_shows_chip_and_starts_generation", () => {
  it("chip renders with chosen route when locateState=chip", () => {
    const html = renderModal({ _testLocateState: "chip", _testChosenRouteForChip: "/team" })
    expect(html).toContain('data-testid="locate-chip"')
    expect(html).toContain("Generating on top of")
    expect(html).toContain("/team")
    expect(html).toContain("Not this screen?")
  })

  it("fires runGenerateFlow after an auto_proceed locate response", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue(AUTO_PROCEED)
    const buttons = captureButtons(baseCodebaseProps())
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    ;(btn!["onClick"] as () => void)()
    await flushAsync()
    expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1)
  })
})

// ─── test_proceed_with_note_starts_generation ────────────────────────────────

describe("test_proceed_with_note_starts_generation", () => {
  it("fires runGenerateFlow on proceed_with_note without blocking picker", async () => {
    const proceedWithNote: LocateResponse = { ...AUTO_PROCEED, decision: "proceed_with_note" }
    vi.spyOn(designAgentApi, "locate").mockResolvedValue(proceedWithNote)
    const buttons = captureButtons(baseCodebaseProps())
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    ;(btn!["onClick"] as () => void)()
    await flushAsync()
    expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1)
  })
})

// ─── test_ranked_confirm_blocks_until_pick ───────────────────────────────────

describe("test_ranked_confirm_blocks_until_pick", () => {
  it("LocateConfirmView renders with candidates when locateState=ranked_confirm", () => {
    const html = renderModal({
      _testLocateState: "ranked_confirm",
      _testLocateResult: RANKED_CONFIRM,
    })
    expect(html).toContain('data-testid="locate-confirm-surface"')
    expect(html).toContain("/team")
    expect(html).toContain("/dashboard")
  })

  it("Generate button is disabled while locateState=ranked_confirm", () => {
    const html = renderModal({
      _testLocateState: "ranked_confirm",
      _testLocateResult: RANKED_CONFIRM,
    })
    expect(html).toMatch(/data-testid="generate-btn"[^>]*disabled=""/)
  })
})

// ─── test_pick_carries_chosen_route_into_genstart ────────────────────────────

describe("test_pick_carries_chosen_route_into_genstart", () => {
  it("clicking a picker candidate calls onGenStart with chosenScreenRoute", () => {
    const onGenStart = vi.fn()
    const buttons = captureButtons({
      ...baseCodebaseProps(),
      onGenStart,
      _testLocateState: "ranked_confirm",
      _testLocateResult: RANKED_CONFIRM,
    })
    const choiceBtn = buttons.find((b) => b["data-testid"] === "locate-confirm-choice")
    expect(choiceBtn).toBeDefined()
    ;(choiceBtn!["onClick"] as () => void)()
    expect(onGenStart).toHaveBeenCalledWith(
      expect.objectContaining({ chosenScreenRoute: "/team" }),
    )
  })
})

// ─── test_unmapped_shows_fallback_no_autostart ───────────────────────────────

describe("test_unmapped_shows_fallback_no_autostart", () => {
  it("renders the unmapped fallback message when locateState=unmapped", () => {
    const html = renderModal({ _testLocateState: "unmapped" })
    expect(html).toContain('data-testid="locate-unmapped"')
    expect(html).toContain("couldn")
  })

  it("does not call runGenerateFlow when locate returns unmapped", async () => {
    const unmapped: LocateResponse = { ...RANKED_CONFIRM, unmapped: true }
    vi.spyOn(designAgentApi, "locate").mockResolvedValue(unmapped)
    const buttons = captureButtons(baseCodebaseProps())
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    ;(btn!["onClick"] as () => void)()
    await flushAsync()
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
  })
})

// ─── test_locate_error_is_non_fatal_falls_back ───────────────────────────────

describe("test_locate_error_is_non_fatal_falls_back", () => {
  it("renders inline error with role=alert when _testLocateError is set", () => {
    const msg = "Couldn't analyse the codebase — pick a screen or switch source"
    const html = renderModal({ _testLocateError: msg })
    expect(html).toContain('data-testid="locate-error"')
    expect(html).toContain('role="alert"')
    expect(html).toContain("Couldn")
  })

  it("calls locate but does not start generation when locate throws", async () => {
    const onClose = vi.fn()
    vi.spyOn(designAgentApi, "locate").mockRejectedValue(new Error("net error"))
    const buttons = captureButtons({ ...baseCodebaseProps(), onClose })
    const btn = buttons.find((b) => b["data-testid"] === "generate-btn")
    ;(btn!["onClick"] as () => void)()
    await flushAsync()
    expect(designAgentApi.locate).toHaveBeenCalledTimes(1)
    expect(vi.mocked(runGenerateFlow)).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
  })
})

// ─── test_codebase_mode_requires_repo_disabled_helper_text ───────────────────

describe("test_codebase_mode_requires_repo_disabled_helper_text", () => {
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

// ─── test_figma_mode_runs_without_locate_call ────────────────────────────────

describe("test_figma_mode_runs_without_locate_call", () => {
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

// ─── test_approve_modal_mount_unchanged_optional_context ─────────────────────

describe("test_approve_modal_mount_unchanged_optional_context", () => {
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
    expect(html).not.toContain('data-testid="locate-chip"')
  })
})

// ─── test_no_prohibited_tokens_in_appended_lines ─────────────────────────────

describe("test_no_prohibited_tokens_in_appended_lines", () => {
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
    const section = src.slice(src.indexOf("LocateFlowState"))
    expect(/[CPH]\d-\d/.test(section), "ticket-series ID in GenerateModal locate section").toBe(false)
    expect(/\bAD\d/.test(section), "AD-series token in GenerateModal locate section").toBe(false)
    expect(/\bF\d{1,2}\b/.test(section), "function-req token in GenerateModal locate section").toBe(false)
  })
})

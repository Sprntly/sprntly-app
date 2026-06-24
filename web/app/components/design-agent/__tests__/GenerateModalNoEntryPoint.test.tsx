/**
 * Tests for the codebase no-screen escape hatch in GenerateModal: when locate
 * finds no screen to anchor on (empty-ranked unmapped) or fails outright, the
 * user can still "Generate from the PRD anyway" — a github generation with no
 * chosen screen route. Mirrors the node-env / renderToStaticMarkup /
 * captureButtons patterns of the sibling locate suites.
 */
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// useNavigation reaches next/navigation (unavailable in node-env). Stub it out.
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), toast: null }),
}))

// runGenerateFlow drives real network I/O. Replace it with a no-op spy so
// generation does not actually run; we only verify it is invoked + with what.
vi.mock("../DesignAgentDrawer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../DesignAgentDrawer")>()
  return { ...actual, runGenerateFlow: vi.fn().mockResolvedValue(undefined) }
})

// Sprntly components use the classic JSX runtime; expose React globally.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { GenerateModal } from "../GenerateModal"
import { runGenerateFlow } from "../DesignAgentDrawer"
import {
  type ConnectionSummary,
  type GitHubRepo,
  type LocateResponse,
} from "../../../lib/api"

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

// Unmapped with NO ranked fallbacks — the real dead-end the escape hatch fixes.
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

const UNMAPPED_WITH_RANKED: LocateResponse = {
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
  ],
  top_confidence: 0.6,
  threshold: 0.8,
  repo: SEL_REPO,
  posture: "PARTIAL",
  unmapped: true,
  commit_sha: "",
}

// Mapped (build_map succeeded → a shell exists) with a real snapshot SHA and a
// ranked candidate → the picker phase. "Generate anyway" here must carry the SHA
// so the backend rebuilds the map (cache hit) and the shell-grounded fallback
// (Tier-2) can read the app shell.
const MAPPED_WITH_SHA: LocateResponse = {
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
  posture: "CLEAN",
  unmapped: false,
  commit_sha: "sha-abc123",
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

function figmaProps(): ModalProps {
  return {
    open: true,
    onClose: vi.fn(),
    prdId: PRD_ID,
    figmaFileKey: null,
    _testConnections: FIGMA_CONN,
    _testRepos: [],
    _testInitSource: "figma",
  }
}

function renderModal(overrides: Partial<ModalProps> = {}): string {
  return renderToStaticMarkup(
    React.createElement(GenerateModal, { ...baseCodebaseProps(), ...overrides }),
  )
}

/** Captures all button element props rendered by GenerateModal. */
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

// ─── empty-ranked unmapped offers the escape hatch ───────────────────────────

describe("empty-ranked unmapped offers the PRD escape hatch", () => {
  it("renders the generate-anyway action (not a bare hint/switch-only)", () => {
    const html = renderModal({
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: UNMAPPED_EMPTY,
    })
    expect(html).toContain('data-testid="unmapped-resolve"')
    expect(html).toContain('data-testid="generate-anyway"')
    expect(html).toContain("Generate from the PRD anyway")
    // The steer-first ladder: "Search again" is the accent primary; the
    // PRD-anyway fallback is a de-emphasized text link (not a button-row CTA).
    expect(html).toContain('data-testid="locate-search-again"')
    expect(html).toContain('class="locate-generate-link"')
    // Switch source was removed from this panel (close the modal to swap).
    expect(html).not.toContain('data-testid="unmapped-switch-source"')
  })

  it("with real candidates present, the picker shows + generate-anyway is HIDDEN (pick or steer)", () => {
    const html = renderModal({
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: UNMAPPED_WITH_RANKED,
    })
    // Picker shows for the (real) fallback candidate…
    expect(html).toContain('data-testid="locate-confirm-use"')
    // …and the PRD-only floor is NOT offered when there are real screens to pick
    // or steer toward — it belongs only to the no-candidate variant.
    expect(html).not.toContain('data-testid="generate-anyway"')
    // Copy stays source-honest: with REAL candidates present the consolidated
    // recovery body leads with "found some candidate screens" and offers the
    // steer ("search again"); the empty-ranked "anchor on" wording is gone.
    expect(html).toContain("candidate screens")
    expect(html).toContain("search again")
    expect(html).not.toContain("find a screen to anchor on in this repo")
  })
})

// ─── error phase offers the escape hatch (codebase only) ─────────────────────

describe("error phase offers the PRD escape hatch for codebase", () => {
  it("renders generate-anyway alongside retry in the codebase error phase", () => {
    const html = renderModal({
      _testFlowPhase: "error",
      _testLocateError: "Codebase analysis failed — try again or switch source",
    })
    expect(html).toContain('data-testid="locate-error-state"')
    expect(html).toContain('data-testid="locate-retry"')
    expect(html).toContain('data-testid="generate-anyway"')
    expect(html).toContain("Generate from the PRD anyway")
  })
})

// ─── clicking generate-anyway runs a github gen with no screen route ─────────

describe("generate-anyway runs a github generation with no screen route", () => {
  it("invokes runGenerateFlow with design_source github and no chosen_screen_route", () => {
    const buttons = captureButtons({
      ...baseCodebaseProps(),
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: UNMAPPED_EMPTY,
    })
    const btn = buttons.find((b) => b["data-testid"] === "generate-anyway")
    expect(btn).toBeDefined()
    ;(btn!["onClick"] as () => void)()
    expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1)
    const arg = vi.mocked(runGenerateFlow).mock.calls[0]![0] as {
      params: { design_source?: string | null; chosen_screen_route?: string | null }
    }
    expect(arg.params.design_source).toBe("github")
    // No screen to anchor on → the recreate keys are omitted entirely.
    expect(arg.params.chosen_screen_route).toBeUndefined()
  })

  it("invokes runGenerateFlow from the error phase generate-anyway action", () => {
    const buttons = captureButtons({
      ...baseCodebaseProps(),
      _testFlowPhase: "error",
      _testLocateError: "boom",
    })
    const btn = buttons.find((b) => b["data-testid"] === "generate-anyway")
    expect(btn).toBeDefined()
    ;(btn!["onClick"] as () => void)()
    expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1)
    const arg = vi.mocked(runGenerateFlow).mock.calls[0]![0] as {
      params: { design_source?: string | null }
    }
    expect(arg.params.design_source).toBe("github")
  })
})

// ─── Tier-2 wire + generate-anyway is unmapped-only ───────────────────────────
// "Generate from the PRD anyway" renders ONLY when there is no real candidate:
// on the picker (real candidates) it is hidden (pick or steer, Tier-1). Tier-2
// (shell-grounded, no specific screen) is reached when the map BUILT (commit_sha
// present) but locate surfaced no REAL candidate — there the generate-anyway path
// must still carry map_commit_sha so the backend rebuilds the map and grounds on
// the shell. Without the SHA the backend skips the build → Tier-3 (no shell).

// Mapped (map built → SHA present) but locate returned only a degenerate
// placeholder → no real candidate → the recovery body's generate-anyway path.
const MAPPED_NO_REAL_CANDIDATES: LocateResponse = {
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
  commit_sha: "sha-abc123",
}

describe("generate-anyway is unmapped-only; the mapped no-screen path still carries the SHA (Tier-2)", () => {
  it("the PICKER (real candidates) HIDES generate-anyway — pick or steer, no PRD-only floor", () => {
    const html = renderModal({
      _testFlowPhase: "picker",
      _testLocateResult: MAPPED_WITH_SHA,
    })
    // Picker lets you pick a screen…
    expect(html).toContain('data-testid="locate-confirm-use"')
    // …and the PRD-only floor is NOT offered here (only on the unmapped variant).
    expect(html).not.toContain('data-testid="generate-anyway"')
  })

  it("mapped-but-no-real-candidate: generate-anyway IS shown and carries map_commit_sha (Tier-2)", () => {
    const html = renderModal({
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: MAPPED_NO_REAL_CANDIDATES,
    })
    // No "Suggested / Use this screen" card for the degenerate placeholder…
    expect(html).not.toContain('data-testid="locate-confirm-use"')
    // …but the PRD-only floor IS offered (no real screen to pick).
    expect(html).toContain('data-testid="generate-anyway"')

    const buttons = captureButtons({
      ...baseCodebaseProps(),
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: MAPPED_NO_REAL_CANDIDATES,
    })
    const btn = buttons.find((b) => b["data-testid"] === "generate-anyway")
    expect(btn).toBeDefined()
    ;(btn!["onClick"] as () => void)()
    expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1)
    const arg = vi.mocked(runGenerateFlow).mock.calls[0]![0] as {
      params: {
        design_source?: string | null
        chosen_screen_route?: string | null
        chosen_screen_id?: string | null
        map_commit_sha?: string | null
      }
    }
    expect(arg.params.design_source).toBe("github")
    // No screen chosen → located stays None on the backend (Tier-2, not Tier-1).
    expect(arg.params.chosen_screen_route).toBeUndefined()
    expect(arg.params.chosen_screen_id).toBeUndefined()
    // …but the SHA travels so the backend builds the map and grounds the shell.
    expect(arg.params.map_commit_sha).toBe("sha-abc123")
  })

  it("unmapped generate-anyway carries NO sha (Tier-3 — there is no shell)", () => {
    const buttons = captureButtons({
      ...baseCodebaseProps(),
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: UNMAPPED_EMPTY,
    })
    const btn = buttons.find((b) => b["data-testid"] === "generate-anyway")
    ;(btn!["onClick"] as () => void)()
    const arg = vi.mocked(runGenerateFlow).mock.calls[0]![0] as {
      params: { map_commit_sha?: string | null }
    }
    // commit_sha is "" on an unmapped response → no map_commit_sha sent.
    expect(arg.params.map_commit_sha).toBeUndefined()
  })
})

// ─── github-only: figma/website error UX is unchanged ────────────────────────

describe("generate-anyway is github-only (figma/website unchanged)", () => {
  it("does NOT render generate-anyway in the error phase for a figma source", () => {
    const html = renderToStaticMarkup(
      React.createElement(GenerateModal, {
        ...figmaProps(),
        _testFlowPhase: "error",
        _testLocateError: "boom",
      }),
    )
    expect(html).toContain('data-testid="locate-error-state"')
    expect(html).not.toContain('data-testid="generate-anyway"')
  })
})

// ─── source-honest copy ──────────────────────────────────────────────────────

describe("source-honest unmapped copy", () => {
  it("uses the no-screen-to-anchor copy and drops the old 'pick a screen' wording", () => {
    const html = renderModal({
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: UNMAPPED_EMPTY,
    })
    expect(html).toContain('data-testid="locate-unmapped"')
    expect(html).toContain("couldn")
    expect(html).toContain("find a screen to anchor on in this repo")
    expect(html).toContain("Generate")
    // The old wording is gone.
    expect(html).not.toContain("match your codebase to a screen — pick a screen")
    // Copy must not promise an affordance the panel doesn't offer — there is no
    // "point at a page" control, so that clause is dropped.
    expect(html).not.toContain("point us at a specific page")
    // "Switch source" was removed from this panel — its copy clause is dropped.
    expect(html).not.toContain("switch source")
    // The PRD-anyway fallback renders as a de-emphasized text link.
    expect(html).toContain('class="locate-generate-link"')
  })
})

// ─── recovery layout: candidates-first on picker, steer-first on unmapped ─────

describe("recovery panel orders by variant", () => {
  it("UNMAPPED (no candidates): steer leads and 'Search again' is the accent primary", () => {
    const buttons = captureButtons({
      ...baseCodebaseProps(),
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: UNMAPPED_EMPTY,
    })
    const searchAgain = buttons.find(
      (b) => b["data-testid"] === "locate-search-again",
    )
    expect(searchAgain).toBeDefined()
    // Sole action on the unmapped variant → accent/primary.
    expect(searchAgain!["className"]).toBe("btn btn-accent")
  })

  it("PICKER (real candidates): candidates render ABOVE the steer, and 'Search again' is plain/secondary", () => {
    const html = renderModal({
      _testFlowPhase: "picker",
      _testLocateResult: MAPPED_WITH_SHA,
    })
    // The pickable candidate surface appears before the steer row in the DOM —
    // "the picker and the options always appear at the top."
    const confirmIdx = html.indexOf('data-testid="locate-confirm-surface"')
    const steerIdx = html.indexOf('data-testid="locate-steer"')
    expect(confirmIdx).toBeGreaterThan(-1)
    expect(steerIdx).toBeGreaterThan(-1)
    expect(confirmIdx).toBeLessThan(steerIdx)

    const buttons = captureButtons({
      ...baseCodebaseProps(),
      _testFlowPhase: "picker",
      _testLocateResult: MAPPED_WITH_SHA,
    })
    const searchAgain = buttons.find(
      (b) => b["data-testid"] === "locate-search-again",
    )
    expect(searchAgain).toBeDefined()
    // Demoted below the candidate cards → plain, so it doesn't compete with the
    // accent "Use this screen" buttons.
    expect(searchAgain!["className"]).toBe("btn")
  })
})

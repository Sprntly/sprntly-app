/**
 * Tests for the external-entry-point recovery in GenerateModal: when
 * codebase-locate detects (via the SAME locate call — see
 * LocateResponse.external_surface) that the PRD's entry point genuinely lives
 * outside the connected codebase, the recovery panel offers a "generate a
 * placeholder" CTA instead of leaving the PM with only the plain PRD-anyway
 * floor. Generalized: covers two DIFFERENT non-email surface types (SMS +
 * a third-party partner portal) to prove nothing here is email-specific.
 * Mirrors the node-env / renderToStaticMarkup / captureButtons patterns of
 * GenerateModalNoEntryPoint.test.tsx.
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

/** An unmapped-shaped result carrying an external_surface signal — SMS. */
const SMS_EXTERNAL: LocateResponse = {
  decision: "ranked_confirm",
  chosen: [],
  ranked: [],
  top_confidence: 0,
  threshold: 0.8,
  repo: SEL_REPO,
  posture: "PARTIAL",
  unmapped: false,
  commit_sha: "sha-sms123",
  external_surface: {
    detected: true,
    surface_description: "an SMS text the user receives on their phone",
    confidence: 88,
  },
}

/** A DIFFERENT external-surface type — a third-party partner portal — to
 * prove the UI is not secretly email-specific. */
const PARTNER_PORTAL_EXTERNAL: LocateResponse = {
  decision: "ranked_confirm",
  chosen: [],
  ranked: [],
  top_confidence: 0,
  threshold: 0.8,
  repo: SEL_REPO,
  posture: "PARTIAL",
  unmapped: false,
  commit_sha: "sha-partner456",
  external_surface: {
    detected: true,
    surface_description: "the external partner booking website",
    confidence: 82,
  },
}

/** Ordinary weak-match unmapped result with NO external signal at all — the
 * false-positive guard: an internal-only flow must never show the CTA. */
const NO_EXTERNAL_SIGNAL: LocateResponse = {
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

// ─── Detection renders the placeholder CTA, generalized across surface types ──

describe("external-entry-point detected offers the generate-placeholder CTA", () => {
  it("SMS-flavored surface: shows the CTA with the SMS description", () => {
    const html = renderModal({
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: SMS_EXTERNAL,
    })
    expect(html).toContain('data-testid="locate-external-surface"')
    expect(html).toContain("an SMS text the user receives on their phone")
    expect(html).toContain('data-testid="generate-external-placeholder"')
  })

  it("partner-portal surface (a DIFFERENT, non-email/non-SMS type): shows the CTA with its own description", () => {
    const html = renderModal({
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: PARTNER_PORTAL_EXTERNAL,
    })
    expect(html).toContain('data-testid="locate-external-surface"')
    expect(html).toContain("the external partner booking website")
    expect(html).toContain('data-testid="generate-external-placeholder"')
  })

  it("the two surface types render genuinely DIFFERENT copy — proving this isn't a single hardcoded template", () => {
    const smsHtml = renderModal({
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: SMS_EXTERNAL,
    })
    const portalHtml = renderModal({
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: PARTNER_PORTAL_EXTERNAL,
    })
    expect(smsHtml).not.toContain("the external partner booking website")
    expect(portalHtml).not.toContain("an SMS text the user receives on their phone")
  })
})

// ─── False-positive guard: no signal → no CTA ─────────────────────────────────

describe("no external signal never renders the CTA (false-positive guard)", () => {
  it("an ordinary weak-match/unmapped result with no external_surface renders the plain recovery panel only", () => {
    const html = renderModal({
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: NO_EXTERNAL_SIGNAL,
    })
    expect(html).not.toContain('data-testid="locate-external-surface"')
    expect(html).not.toContain('data-testid="generate-external-placeholder"')
    // The ordinary escape hatch still renders — no regression.
    expect(html).toContain('data-testid="generate-anyway"')
  })
})

// ─── Clicking the CTA runs a github generation carrying the hint ─────────────

describe("clicking generate-external-placeholder threads the description into /generate", () => {
  it("invokes runGenerateFlow with design_source github, no chosen screen, and external_surface_hint set", () => {
    const buttons = captureButtons({
      ...baseCodebaseProps(),
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: SMS_EXTERNAL,
    })
    const btn = buttons.find((b) => b["data-testid"] === "generate-external-placeholder")
    expect(btn).toBeDefined()
    ;(btn!["onClick"] as () => void)()
    expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1)
    const arg = vi.mocked(runGenerateFlow).mock.calls[0]![0] as {
      params: {
        design_source?: string | null
        chosen_screen_route?: string | null
        chosen_screen_id?: string | null
        external_surface_hint?: string | null
        map_commit_sha?: string | null
      }
    }
    expect(arg.params.design_source).toBe("github")
    expect(arg.params.chosen_screen_route).toBeUndefined()
    expect(arg.params.chosen_screen_id).toBeUndefined()
    expect(arg.params.external_surface_hint).toBe(
      "an SMS text the user receives on their phone",
    )
    // The SHA travels so the backend builds the map for the shell-grounded fallback.
    expect(arg.params.map_commit_sha).toBe("sha-sms123")
  })
})

// ─── Screenshot precedence: a real screenshot wins over the placeholder CTA ──

describe("a user-attached screenshot takes precedence over the placeholder CTA", () => {
  it("with no screenshot attached, the CTA renders (baseline)", () => {
    const html = renderModal({
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: SMS_EXTERNAL,
    })
    expect(html).toContain('data-testid="generate-external-placeholder"')
    expect(html).not.toContain(
      'data-testid="locate-external-surface-screenshot-note"',
    )
  })

  it("the screenshot-attach control is offered on the external-detected variant even though it has no ranked candidates", () => {
    // Today this control is gated to `realRanked.length > 0` OR the
    // external-detected variant — proving the manual-screenshot path is
    // actually reachable in the exact scenario this feature targets, not
    // just on the (irrelevant here) mapped/picker variant.
    const html = renderModal({
      _testFlowPhase: "unmapped-resolve",
      _testLocateResult: SMS_EXTERNAL,
    })
    expect(html).toContain('data-testid="locate-image-attach"')
  })
})

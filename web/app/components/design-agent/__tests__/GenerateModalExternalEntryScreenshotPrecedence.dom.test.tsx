/**
 * @vitest-environment jsdom
 *
 * Screenshot precedence over the external-entry-point placeholder CTA: per
 * the hard rule, a user-provided screenshot of the real external screen must
 * always win over the auto-generated placeholder. This is the reachable-path
 * half of that contract — the manual-screenshot attach control is offered on
 * the external-detected recovery variant (extending the existing mapped-only
 * gate, byte-identical for every OTHER unmapped fixture — see
 * GenerateModalImageSteer.dom.test.tsx's unchanged "absent on unmapped"
 * coverage) — and once attached, the placeholder CTA backs off in favor of a
 * "using your screenshot" note.
 *
 * jsdom + @testing-library/react, mirroring GenerateModalImageSteer.dom.test.tsx.
 */
import * as React from "react"
import { render, fireEvent, waitFor, act } from "@testing-library/react"
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
  type ConnectionSummary,
  type GitHubRepo,
  type LocateResponse,
} from "../../../lib/api"

const SEL_REPO = "org/repo"
const PRD_ID = 91
const STUB_DATA_URL = "data:image/png;base64,STUBDATA"

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

const EMAIL_EXTERNAL: LocateResponse = {
  decision: "ranked_confirm",
  chosen: [],
  ranked: [],
  top_confidence: 0,
  threshold: 0.8,
  repo: SEL_REPO,
  posture: "PARTIAL",
  unmapped: false,
  commit_sha: "sha-email789",
  external_surface: {
    detected: true,
    surface_description: "a confirmation email sent to the customer",
    confidence: 90,
  },
}

function steerProps(overrides: Record<string, unknown> = {}) {
  return {
    open: true,
    onClose: vi.fn(),
    prdId: PRD_ID,
    figmaFileKey: null,
    _testConnections: GITHUB_CONN,
    _testRepos: REPOS,
    _testInitSource: "github" as const,
    _testInitRepoSel: SEL_REPO,
    _testFlowPhase: "unmapped-resolve" as const,
    _testLocateResult: EMAIL_EXTERNAL,
    _testPollIntervalMs: 0,
    _testPollTimeoutMs: 5000,
    _testPollMaxRetries: 4,
    _testDownscale: vi.fn().mockResolvedValue(STUB_DATA_URL),
    ...overrides,
  }
}

function q(container: HTMLElement, testid: string) {
  return container.querySelector(`[data-testid="${testid}"]`)
}

function attachFile(container: HTMLElement, file: File) {
  const input = container.querySelector<HTMLInputElement>(
    '[data-testid="locate-image-input"]',
  )
  expect(input).toBeTruthy()
  act(() => {
    fireEvent.change(input!, { target: { files: [file] } })
  })
}

function pngFile(name = "the-real-email.png", type = "image/png") {
  return new File(["x"], name, { type })
}

beforeEach(() => {
  vi.mocked(runGenerateFlow).mockResolvedValue(undefined)
})

afterEach(() => {
  vi.resetAllMocks()
  vi.useRealTimers()
})

describe("the screenshot-attach control is reachable on the external-detected variant", () => {
  it("renders the attach control even though there are no ranked candidates", () => {
    const { container } = render(React.createElement(GenerateModal, steerProps()))

    expect(q(container, "locate-external-surface")).toBeTruthy()
    expect(q(container, "generate-external-placeholder")).toBeTruthy()
    expect(q(container, "locate-image-attach")).toBeTruthy()
  })
})

describe("attaching a screenshot hides the placeholder CTA and shows the precedence note", () => {
  it("before attaching: CTA visible, no note", () => {
    const { container } = render(React.createElement(GenerateModal, steerProps()))

    expect(q(container, "generate-external-placeholder")).toBeTruthy()
    expect(q(container, "locate-external-surface-screenshot-note")).toBeNull()
  })

  it("after attaching: CTA hidden, precedence note shown instead", async () => {
    const { container } = render(React.createElement(GenerateModal, steerProps()))

    attachFile(container, pngFile())

    await waitFor(() => expect(q(container, "locate-image-chip")).toBeTruthy())
    expect(q(container, "generate-external-placeholder")).toBeNull()
    expect(q(container, "locate-external-surface-screenshot-note")).toBeTruthy()
    expect(q(container, "locate-external-surface-screenshot-note")!.textContent).toContain(
      "your attached screenshot",
    )
  })

  it("removing the screenshot restores the placeholder CTA", async () => {
    const { container } = render(React.createElement(GenerateModal, steerProps()))

    attachFile(container, pngFile())
    await waitFor(() => expect(q(container, "locate-image-chip")).toBeTruthy())
    expect(q(container, "generate-external-placeholder")).toBeNull()

    act(() => {
      q(container, "locate-image-remove")!.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      )
    })

    await waitFor(() => expect(q(container, "locate-image-chip")).toBeNull())
    expect(q(container, "generate-external-placeholder")).toBeTruthy()
  })
})

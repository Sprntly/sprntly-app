/**
 * @vitest-environment jsdom
 *
 * Image-as-steer on the MAPPED recovery modal. Extends the
 * steerable re-search: on the mapped recovery variant the PM can
 * attach a screenshot of the screen they want; locate reads its on-screen
 * text/route cues and re-ranks. This is a SECOND, optional steer on the SAME
 * recovery modal — it must NOT render on the unmapped variant (no map to
 * re-rank), and it must never imply the screenshot was used when the server
 * fell open to text-only.
 *
 * jsdom + @testing-library/react. The canvas downscale is stubbed via the
 * `_testDownscale` prop (jsdom has no real canvas / image decode), so the
 * data-URL path is deterministic. Mirrors GenerateModalSteerResearch's mock
 * setup (NavigationContext + DesignAgentDrawer.runGenerateFlow).
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
  designAgentApi,
  type ConnectionSummary,
  type GitHubRepo,
  type LocateResponse,
} from "../../../lib/api"

// ── Fixtures ────────────────────────────────────────────────────────────────

const SEL_REPO = "org/repo"
const PRD_ID = 77
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

// Unmapped with NO ranked fallbacks — no map to re-rank → the image affordance
// must NOT render here.
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

// A real ambiguous match → the MAPPED recovery (picker) variant where the image
// affordance lives.
const PICKER_RANKED: LocateResponse = {
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
  posture: "PARTIAL",
  unmapped: false,
  commit_sha: "shaPICK",
}

// Mapped result where the attached screenshot WAS applied: cues ride the result.
const PICKER_APPLIED: LocateResponse = {
  ...PICKER_RANKED,
  read_cues: ["/planning", "Backlog", "Sprint"],
  image_status: "applied",
}

// Mapped result where the server fell open to text-only (oversize). NO cues.
const PICKER_IGNORED_OVERSIZE: LocateResponse = {
  ...PICKER_RANKED,
  read_cues: [],
  image_status: "ignored_oversize",
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
    _testFlowPhase: "picker" as const,
    _testLocateResult: PICKER_RANKED,
    _testPollIntervalMs: 0,
    _testPollTimeoutMs: 5000,
    _testPollMaxRetries: 4,
    // Deterministic downscale — jsdom has no canvas.
    _testDownscale: vi.fn().mockResolvedValue(STUB_DATA_URL),
    ...overrides,
  }
}

function q(container: HTMLElement, testid: string) {
  return container.querySelector(`[data-testid="${testid}"]`)
}

function attachFile(
  container: HTMLElement,
  file: File,
) {
  const input = container.querySelector<HTMLInputElement>(
    '[data-testid="locate-image-input"]',
  )
  expect(input).toBeTruthy()
  act(() => {
    fireEvent.change(input!, { target: { files: [file] } })
  })
}

function pngFile(name = "planning-board.png", type = "image/png") {
  return new File(["x"], name, { type })
}

beforeEach(() => {
  vi.mocked(runGenerateFlow).mockResolvedValue(undefined)
})

afterEach(() => {
  vi.resetAllMocks()
  vi.useRealTimers()
})

// ── attach + chip + remove (mapped variant) ───────────────────────────────────

describe("attach a screenshot, see a chip, remove it", () => {
  it("mapped variant renders the attach control; selecting a file shows a chip with the filename; remove clears it", async () => {
    const { container } = render(
      React.createElement(GenerateModal, steerProps()),
    )

    // The attach affordance is present on the mapped variant.
    expect(q(container, "locate-image-attach")).toBeTruthy()
    expect(q(container, "locate-image-chip")).toBeNull()

    attachFile(container, pngFile())

    await waitFor(() => expect(q(container, "locate-image-chip")).toBeTruthy())
    expect(q(container, "locate-image-chip")!.textContent).toContain(
      "planning-board.png",
    )

    // Remove (×) clears the chip.
    act(() => {
      q(container, "locate-image-remove")!.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      )
    })
    await waitFor(() => expect(q(container, "locate-image-chip")).toBeNull())
  })
})

// ── NO image control on the unmapped variant (load-bearing) ───────────────────

describe("the image affordance is absent on the unmapped variant", () => {
  it("unmapped recovery renders the text steer + PRD-anyway link but NO image attach control", () => {
    const { container } = render(
      React.createElement(
        GenerateModal,
        steerProps({
          _testFlowPhase: "unmapped-resolve",
          _testLocateResult: UNMAPPED_EMPTY,
        }),
      ),
    )

    // Load-bearing: the image control must NOT exist on the unmapped variant.
    expect(q(container, "locate-image-attach")).toBeNull()
    expect(q(container, "locate-image-input")).toBeNull()
    expect(q(container, "locate-image-chip")).toBeNull()

    // …while the text steer + the PRD-anyway floor are still present.
    expect(q(container, "locate-steer-input")).toBeTruthy()
    expect(q(container, "generate-anyway")).toBeTruthy()
  })
})

// ── "Search again" enables on EITHER signal ───────────────────────────────────

describe("Search again enables on text OR image", () => {
  it("is disabled when both empty, enabled with text only, enabled with image only", async () => {
    const { container } = render(
      React.createElement(GenerateModal, steerProps()),
    )
    const btn = () =>
      container.querySelector<HTMLButtonElement>(
        '[data-testid="locate-search-again"]',
      )!

    // Both empty → disabled.
    expect(btn().disabled).toBe(true)

    // Text only → enabled.
    act(() => {
      fireEvent.change(
        container.querySelector('[data-testid="locate-steer-input"]')!,
        { target: { value: "the dashboard" } },
      )
    })
    expect(btn().disabled).toBe(false)

    // Clear the text, attach an image only → still enabled (image alone).
    act(() => {
      fireEvent.change(
        container.querySelector('[data-testid="locate-steer-input"]')!,
        { target: { value: "" } },
      )
    })
    expect(btn().disabled).toBe(true)
    attachFile(container, pngFile())
    await waitFor(() => expect(q(container, "locate-image-chip")).toBeTruthy())
    expect(btn().disabled).toBe(false)
  })
})

// ── send the image on Search again ────────────────────────────────────────────

describe("the attached image rides the re-run locate POST", () => {
  it("sends image (and an empty hint) on Search again with an image only", async () => {
    const locateSpy = vi
      .spyOn(designAgentApi, "locate")
      .mockResolvedValue({ job_id: "job-img", status: "running" })
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
      status: "done",
      result: PICKER_APPLIED,
    })

    const { container } = render(
      React.createElement(GenerateModal, steerProps()),
    )

    attachFile(container, pngFile())
    await waitFor(() => expect(q(container, "locate-image-chip")).toBeTruthy())

    act(() => {
      container
        .querySelector<HTMLButtonElement>(
          '[data-testid="locate-search-again"]',
        )!
        .click()
    })

    await waitFor(() => expect(locateSpy).toHaveBeenCalledTimes(1))
    expect(locateSpy).toHaveBeenCalledWith({
      prd_id: PRD_ID,
      github_repo: SEL_REPO,
      image: STUB_DATA_URL,
    })
    // No `image` key in the body when nothing is attached is covered by the
    // existing steer test (the hint-only call asserts an exact body).
  })
})

// ── honesty — applied vs fall-open ────────────────────────────────────────────

describe("no silent image drop", () => {
  it("applied status renders the cues read off the screenshot", async () => {
    vi.spyOn(designAgentApi, "locate").mockResolvedValue({
      job_id: "job-applied",
      status: "running",
    })
    vi.spyOn(designAgentApi, "locateJob").mockResolvedValue({
      status: "done",
      result: PICKER_APPLIED,
    })

    const { container } = render(
      React.createElement(GenerateModal, steerProps()),
    )

    attachFile(container, pngFile())
    await waitFor(() => expect(q(container, "locate-image-chip")).toBeTruthy())

    act(() => {
      container
        .querySelector<HTMLButtonElement>(
          '[data-testid="locate-search-again"]',
        )!
        .click()
    })

    await waitFor(() => expect(q(container, "locate-image-cues")).toBeTruthy())
    const cues = q(container, "locate-image-cues")!.textContent ?? ""
    expect(cues).toContain("/planning")
    expect(cues).toContain("Backlog")
    // No fall-open notice on an applied result.
    expect(q(container, "locate-image-notice")).toBeNull()
  })

  it("a fall-open (ignored_oversize) shows the notice and NO cues / no re-rank claim", () => {
    const { container } = render(
      React.createElement(
        GenerateModal,
        steerProps({ _testLocateResult: PICKER_IGNORED_OVERSIZE }),
      ),
    )

    // The notice is shown…
    const notice = q(container, "locate-image-notice")
    expect(notice).toBeTruthy()
    expect(notice!.textContent).toContain("searched on your text instead")
    // …and NO cues are rendered, and nothing claims the image was applied.
    expect(q(container, "locate-image-cues")).toBeNull()
    expect(container.textContent ?? "").not.toMatch(/re-rank/i)
  })
})

// ── client reject (client half) ───────────────────────────────────────────────

describe("client rejects bad files before upload", () => {
  it("a non-image file shows the inline error and sets no chip", async () => {
    const { container } = render(
      React.createElement(GenerateModal, steerProps()),
    )

    attachFile(container, new File(["x"], "notes.txt", { type: "text/plain" }))

    await waitFor(() => expect(q(container, "locate-image-error")).toBeTruthy())
    expect(q(container, "locate-image-chip")).toBeNull()
  })

  it("an oversized image (>5MB) shows the inline error and sets no chip", async () => {
    const { container } = render(
      React.createElement(GenerateModal, steerProps()),
    )

    const big = pngFile("huge.png")
    Object.defineProperty(big, "size", { value: 6 * 1024 * 1024 })
    attachFile(container, big)

    await waitFor(() => expect(q(container, "locate-image-error")).toBeTruthy())
    expect(q(container, "locate-image-chip")).toBeNull()
  })
})

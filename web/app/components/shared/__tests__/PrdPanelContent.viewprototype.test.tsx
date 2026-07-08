// @vitest-environment jsdom
//
// The PRD footer's "Generate/View Prototype" button is a thin
// <GeneratePrototypeCTA> mount now (see PrdPanelContent.tsx's
// ViewPrototypeButton). GeneratePrototypeCTA and the underlying
// useGeneratePrototype() hook are exercised directly by their own test
// files — GeneratePrototypeCTA.test.tsx and useGeneratePrototype.test.tsx —
// so this file stays focused on the INTEGRATION contract: that
// PrdPanelContent wires prdId/figmaFileKey through correctly and that the
// button's observable behavior (label text, click routing, generate-then-
// navigate) is unchanged (or, where explicitly sanctioned, improved) by the
// migration.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const mocks = vi.hoisted(() => ({
  pushSpy: vi.fn(),
  showToast: vi.fn(),
  setContent: vi.fn(),
  refresh: vi.fn(async () => {}),
  updateWorkspace: vi.fn(async () => {}),
  getByPrd: vi.fn(),
  generateSpy: vi.fn(),
}))

let content: Record<string, unknown>
let workspace: { id: number; design_source: unknown } | null
// Captured on every render of the mocked GenerateModal/GenerationLoadingScreen
// so tests can invoke the hook's callback props directly (the same pattern
// useGeneratePrototype.test.tsx uses) without needing a real backend or real
// SSE stream.
let latestGenerateProps: Record<string, unknown> | null = null
let latestLoadingProps: Record<string, unknown> | null = null

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.pushSpy, replace: vi.fn(), prefetch: vi.fn() }),
}))

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: mocks.showToast }),
}))

vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content, setContent: mocks.setContent }),
}))

vi.mock("../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme" }),
}))

vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ workspace, refresh: mocks.refresh }),
}))

vi.mock("../../../lib/onboarding/store", () => ({
  updateWorkspace: mocks.updateWorkspace,
}))

vi.mock("../../../lib/api", () => {
  class ApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.status = status
    }
  }
  return {
    ApiError,
    designAgentApi: {
      getByPrd: mocks.getByPrd,
      generate: mocks.generateSpy,
    },
    multiAgentApi: {
      getQaScenarios: vi.fn(async () => ({ doc: null })),
    },
    prdApi: {
      latest: vi.fn(async () => { throw new ApiError(404, "none") }),
      update: vi.fn(async () => ({})),
      listVersions: vi.fn(async () => []),
      listGenerations: vi.fn(async () => []),
      sendToClaudeCode: vi.fn(async () => ({ llm_part: "spec" })),
    },
  }
})

// Mocks the two mounts <GeneratePrototypeCTA> owns internally (via
// useGeneratePrototype). Capturing props on every render — even while
// `open` is false and the mock itself renders nothing — lets tests invoke
// onGenStart/onKickoff/onGenDone/onSavePreference directly, exactly like the
// hook's own test file does, instead of trying to drive a real GenerateModal
// form.
vi.mock("../../design-agent/GenerateModal", () => ({
  GenerateModal: (props: Record<string, unknown>) => {
    latestGenerateProps = props
    if (!props.open) return null
    return (
      <div role="dialog" aria-label="Generate prototype">
        {!props.savedPreference && <div>Choose a design source</div>}
        <button
          type="button"
          onClick={() =>
            (props.onSavePreference as (pref: unknown) => Promise<void>)({
              design_source: "github",
              github_repo: "org/repo",
              figma_file_key: null,
              website_url: null,
            })
          }
        >
          Save preference
        </button>
      </div>
    )
  },
}))

vi.mock("../../design-agent/GenerationLoadingScreen", () => ({
  GenerationLoadingScreen: (props: Record<string, unknown>) => {
    latestLoadingProps = props
    if (!props.open) return null
    return <div data-testid="loading-overlay">Generating…</div>
  },
}))

import { PrdPanelContent } from "../PrdPanelContent"

const PRD = {
  prd_id: 42,
  title: "Retention PRD",
  metaLine: "From Brief",
  sections: [{ type: "p", text: "Improve retention." }],
  figma_file_key: "fig-file",
}

function renderPanel() {
  return render(<PrdPanelContent />)
}

async function openGeneratePopup() {
  mocks.getByPrd.mockResolvedValueOnce(null)
  renderPanel()
  // No ready prototype → the CTA settles on "Generate Prototype"; clicking it
  // opens the generate popup.
  const btn = await screen.findByRole("button", { name: "Generate Prototype" })
  fireEvent.click(btn)
  await screen.findByRole("dialog", { name: "Generate prototype" })
}

beforeEach(() => {
  vi.resetAllMocks()
  latestGenerateProps = null
  latestLoadingProps = null
  mocks.refresh.mockResolvedValue(undefined)
  mocks.updateWorkspace.mockResolvedValue(undefined)
  content = {
    prd: PRD,
    prdGenerating: false,
  }
  workspace = { id: 7, design_source: null }
  mocks.getByPrd.mockResolvedValue({ id: 88, status: "ready", bundle_url: "/bundle" })
})

afterEach(cleanup)

describe("PrdPanelContent View Prototype footer action", () => {
  it("test_prototype_cta_renders_on_every_prd", async () => {
    const { container } = renderPanel()
    const actions = container.querySelector(".prd-bottom-actions")
    expect(actions).toBeTruthy()
    const buttons = within(actions as HTMLElement).getAllByRole("button")
    // First button is the prototype CTA (its label resolves async from getByPrd);
    // second is Send to Claude Code.
    expect(buttons[0].className).toContain("prd-send-claude-btn")
    expect(buttons[1].textContent).toContain("Send to Claude Code")
    // Default mock resolves a READY prototype → the CTA settles on "View Prototype".
    await waitFor(() =>
      expect(within(actions as HTMLElement).getAllByRole("button")[0].textContent).toBe("View Prototype"),
    )
  })

  // Regression — AC3: unchanged label contract from before the migration.
  it("test_prd_panel_button_shows_loading_then_generate_label", async () => {
    mocks.getByPrd.mockReset()
    mocks.getByPrd.mockResolvedValueOnce(null)
    renderPanel()
    // Existence unknown on first render → neutral, disabled (no premature label).
    const loading = screen.getByRole("button", { name: "Loading…" })
    expect((loading as HTMLButtonElement).disabled).toBe(true)
    // No ready row → the label settles on "Generate Prototype".
    const generate = await screen.findByRole("button", { name: "Generate Prototype" })
    expect((generate as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByRole("button", { name: "View Prototype" })).toBeNull()
  })

  // Regression — AC3: unchanged label contract from before the migration.
  it("test_prd_panel_button_shows_view_label_when_ready", async () => {
    renderPanel()
    const loading = screen.getByRole("button", { name: "Loading…" })
    expect((loading as HTMLButtonElement).disabled).toBe(true)
    // Default mock → ready prototype with bundle_url → flips to "View Prototype".
    const view = await screen.findByRole("button", { name: "View Prototype" })
    expect((view as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByRole("button", { name: "Generate Prototype" })).toBeNull()
  })

  // Regression: clicking with cta === "view" re-verifies (a SECOND
  // getByPrd call) before navigating — matches the shared hook's
  // handleCtaClick "view" contract, not a stale-navigation shortcut.
  it("test_prd_panel_view_click_navigates_after_reverify", async () => {
    renderPanel()
    await waitFor(() => expect(mocks.getByPrd).toHaveBeenCalledWith(42))
    fireEvent.click(await screen.findByRole("button", { name: "View Prototype" }))
    await waitFor(() => expect(mocks.getByPrd).toHaveBeenCalledTimes(2))
    expect(mocks.pushSpy).toHaveBeenCalledWith("/prototype?prd=42")
    expect(screen.queryByRole("dialog", { name: "Generate prototype" })).toBeNull()
  })

  // Creation / edge case — AC1 (the sanctioned kill): the user stays on the
  // PRD screen while a generation is in flight — no router.push until the
  // outcome is terminal — and the shared loading overlay (previously entirely
  // absent on this surface) is now visible.
  it("test_prd_panel_generate_stays_on_prd_screen_until_success", async () => {
    await openGeneratePopup()
    expect(latestGenerateProps).toBeTruthy()

    // Simulate the real GenerateModal's kickoff sequence (it calls onClose()
    // then onGenStart() in the same handler — see GenerateModal.tsx line 678).
    await act(async () => {
      ;(latestGenerateProps!.onClose as () => void)()
      ;(latestGenerateProps!.onGenStart as (ctx?: unknown) => void)()
    })

    expect(screen.queryByRole("dialog", { name: "Generate prototype" })).toBeNull()
    expect(await screen.findByTestId("loading-overlay")).toBeTruthy()
    expect(mocks.pushSpy).not.toHaveBeenCalled()
  })

  // Creation / edge case — AC2/AC9 (the sanctioned kill): on success,
  // router.push is called exactly once with the bare prototypePath(prdId) —
  // an exact string-equality assertion (not `toContain`), proving no `&pid=`
  // handoff param is ever appended.
  it("test_prd_panel_generate_success_navigates_without_pid_param", async () => {
    await openGeneratePopup()
    const proto = { id: 991, status: "ready", bundle_url: "/bundle" }

    await act(async () => {
      ;(latestGenerateProps!.onKickoff as (id: number) => void)(991)
      ;(latestGenerateProps!.onGenDone as (result?: unknown) => void)({ ok: true, prototype: proto })
    })

    expect(mocks.pushSpy).toHaveBeenCalledTimes(1)
    expect(mocks.pushSpy).toHaveBeenCalledWith("/prototype?prd=42")
  })

  it("shows 'Generate Prototype' when no ready prototype exists (404 → null)", async () => {
    mocks.getByPrd.mockReset()
    mocks.getByPrd.mockRejectedValueOnce({ status: 404 })
    renderPanel()
    expect(await screen.findByRole("button", { name: "Generate Prototype" })).toBeTruthy()
    expect(screen.queryByRole("button", { name: "View Prototype" })).toBeNull()
  })

  it("test_no_proto_null_resolve_opens_popup", async () => {
    mocks.getByPrd.mockReset()
    mocks.getByPrd.mockResolvedValueOnce(null)
    renderPanel()
    // No prototype → label resolves to "Generate Prototype"; clicking opens popup.
    fireEvent.click(await screen.findByRole("button", { name: "Generate Prototype" }))
    expect(await screen.findByRole("dialog", { name: "Generate prototype" })).toBeTruthy()
    expect(mocks.pushSpy).not.toHaveBeenCalled()
  })

  it("test_getbyprd_reject_defensive_opens_popup", async () => {
    mocks.getByPrd.mockReset()
    mocks.getByPrd.mockRejectedValueOnce({ status: 500 })
    renderPanel()
    // A defensive reject degrades to "no ready prototype" → "Generate Prototype".
    fireEvent.click(await screen.findByRole("button", { name: "Generate Prototype" }))
    expect(await screen.findByRole("dialog", { name: "Generate prototype" })).toBeTruthy()
    expect(mocks.pushSpy).not.toHaveBeenCalled()
  })

  it("test_generate_modal_no_autofire_on_mount", async () => {
    renderPanel()
    await waitFor(() => expect(latestGenerateProps).toBeTruthy())
    expect(latestGenerateProps?.open).toBe(false)
    expect(mocks.generateSpy).not.toHaveBeenCalled()
    expect(mocks.pushSpy).not.toHaveBeenCalled()
  })

  it("test_generate_modal_receives_saved_preference", async () => {
    const pref = {
      design_source: "github",
      github_repo: "org/repo",
      figma_file_key: null,
      website_url: null,
    }
    workspace = { id: 7, design_source: pref }
    renderPanel()
    await waitFor(() => expect(latestGenerateProps?.savedPreference).toBe(pref))
  })

  it("test_generate_modal_shows_config_when_no_preference", async () => {
    await openGeneratePopup()
    expect(screen.getByText("Choose a design source")).toBeTruthy()
  })

  it("test_on_save_preference_persists", async () => {
    await openGeneratePopup()
    fireEvent.click(screen.getByRole("button", { name: "Save preference" }))
    await waitFor(() => {
      expect(mocks.updateWorkspace).toHaveBeenCalledWith(7, {
        design_source: {
          design_source: "github",
          github_repo: "org/repo",
          figma_file_key: null,
          website_url: null,
        },
      })
    })
    expect(mocks.refresh).toHaveBeenCalled()
  })

  it("test_send_to_claude_code_still_renders", () => {
    renderPanel()
    expect(screen.getByTestId("prd-send-claude").textContent).toContain("Send to Claude Code")
  })

  it("test_prototype_section_flag_removed", () => {
    const src = readFileSync(
      resolve(process.cwd(), "app/components/shared/PrdPanelContent.tsx"),
      "utf8",
    )
    expect(src).not.toContain("SHOW_PROTOTYPE_SECTION")
    expect(src).not.toContain("function PrototypeSection")
  })

  // AC9 — the `&pid=` handoff construction is gone entirely from this file.
  it("test_no_pid_handoff_construction_in_source", () => {
    const src = readFileSync(
      resolve(process.cwd(), "app/components/shared/PrdPanelContent.tsx"),
      "utf8",
    )
    expect(src).not.toMatch(/pid=/)
  })
})

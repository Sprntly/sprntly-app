// @vitest-environment jsdom
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
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
let latestGenerateProps: Record<string, unknown> | null = null

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

vi.mock("../../design-agent/GenerateModal", () => ({
  GenerateModal: (props: Record<string, unknown>) => {
    latestGenerateProps = props
    if (!props.open) return null
    return (
      <div role="dialog" aria-label="Generate prototype">
        {!props.savedPreference && <div>Choose a design source</div>}
        <button type="button" onClick={() => (props.onKickoff as (id: number) => void)(991)}>
          Kick off
        </button>
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

  it("shows a disabled 'Loading…' while checking, then 'View Prototype' when one is ready", async () => {
    renderPanel()
    // Existence unknown on first render → neutral, disabled (no premature label).
    const loading = screen.getByRole("button", { name: "Loading…" })
    expect((loading as HTMLButtonElement).disabled).toBe(true)
    // Default mock → ready prototype → flips to an enabled "View Prototype".
    const view = await screen.findByRole("button", { name: "View Prototype" })
    expect((view as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByRole("button", { name: "Generate Prototype" })).toBeNull()
  })

  it("shows 'Generate Prototype' when no ready prototype exists (404 → null)", async () => {
    mocks.getByPrd.mockReset()
    mocks.getByPrd.mockRejectedValueOnce({ status: 404 })
    renderPanel()
    expect(await screen.findByRole("button", { name: "Generate Prototype" })).toBeTruthy()
    expect(screen.queryByRole("button", { name: "View Prototype" })).toBeNull()
  })

  it("test_view_prototype_existing_navigates_direct", async () => {
    renderPanel()
    await waitFor(() => expect(mocks.getByPrd).toHaveBeenCalledWith(42))
    // Ready prototype → label resolves to "View Prototype"; clicking navigates.
    fireEvent.click(await screen.findByRole("button", { name: "View Prototype" }))
    expect(mocks.pushSpy).toHaveBeenCalledWith("/prototype?prd=42")
    expect(screen.queryByRole("dialog", { name: "Generate prototype" })).toBeNull()
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

  it("test_proto_record_navigates", async () => {
    mocks.getByPrd.mockReset()
    mocks.getByPrd.mockResolvedValueOnce({ id: 88, status: "ready", bundle_url: "/bundle" })
    renderPanel()
    fireEvent.click(await screen.findByRole("button", { name: "View Prototype" }))
    expect(mocks.pushSpy).toHaveBeenCalledWith("/prototype?prd=42")
    expect(screen.queryByRole("dialog", { name: "Generate prototype" })).toBeNull()
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

  it("test_kickoff_closes_popup_and_navigates", async () => {
    await openGeneratePopup()
    fireEvent.click(screen.getByRole("button", { name: "Kick off" }))
    await waitFor(() =>
      expect(mocks.pushSpy).toHaveBeenCalledWith("/prototype?prd=42&pid=991"),
    )
    expect(screen.queryByRole("dialog", { name: "Generate prototype" })).toBeNull()
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
})

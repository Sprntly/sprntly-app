// @vitest-environment jsdom
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
  mocks.getByPrd.mockRejectedValueOnce({ status: 404 })
  renderPanel()
  fireEvent.click(screen.getByRole("button", { name: "View Prototype" }))
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
  it("test_view_prototype_button_renders_on_every_prd", () => {
    const { container } = renderPanel()
    const actions = container.querySelector(".prd-bottom-actions")
    expect(actions).toBeTruthy()
    const buttons = within(actions as HTMLElement).getAllByRole("button")
    expect(buttons[0].textContent).toBe("View Prototype")
    expect(buttons[0].className).toContain("prd-send-claude-btn")
    expect(buttons[1].textContent).toContain("Send to Claude Code")
  })

  it("test_view_prototype_button_label_is_static", async () => {
    const { rerender } = renderPanel()
    expect(screen.getByRole("button", { name: "View Prototype" })).toBeTruthy()
    await waitFor(() => expect(mocks.getByPrd).toHaveBeenCalledWith(42))

    mocks.getByPrd.mockRejectedValueOnce({ status: 404 })
    rerender(<PrdPanelContent />)
    expect(screen.getByRole("button", { name: "View Prototype" })).toBeTruthy()
  })

  it("test_view_prototype_button_visible_when_no_prototype_404", async () => {
    mocks.getByPrd.mockRejectedValueOnce({ status: 404 })
    renderPanel()
    expect(screen.getByRole("button", { name: "View Prototype" })).toBeTruthy()
    await waitFor(() => expect(latestGenerateProps?.open).toBe(false))
    expect(screen.getByRole("button", { name: "View Prototype" })).toBeTruthy()
  })

  it("test_view_prototype_existing_navigates_direct", async () => {
    renderPanel()
    await waitFor(() => expect(mocks.getByPrd).toHaveBeenCalledWith(42))
    await act(async () => {
      await mocks.getByPrd.mock.results[0].value
    })
    fireEvent.click(screen.getByRole("button", { name: "View Prototype" }))
    expect(mocks.pushSpy).toHaveBeenCalledWith("/prototype?prd=42")
    expect(screen.queryByRole("dialog", { name: "Generate prototype" })).toBeNull()
  })

  it("test_view_prototype_none_opens_generate_popup", async () => {
    mocks.getByPrd.mockRejectedValueOnce({ status: 404 })
    renderPanel()
    fireEvent.click(screen.getByRole("button", { name: "View Prototype" }))
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
    await waitFor(() => expect(mocks.pushSpy).toHaveBeenCalledWith("/prototype?prd=42"))
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

// @vitest-environment jsdom
//
// Container mount test for onboarding step 02 — "Import your context" (client
// feedback 2026-07-22). Covers the show-then-copy disclosure (the prompt is
// hidden until asked for, and Copy lives inside the revealed panel), the .md
// upload path, and the two properties that make an import safe:
//
//   * a successful import prefills ONLY workspace fields that are still empty
//     — it must never overwrite something the user already typed;
//   * an unreadable export surfaces its `note` instead of claiming success.
//
// There is deliberately no "Connect Claude" path to test: it was removed
// because an Anthropic token cannot read claude.ai conversation history (see
// backend/app/llm_context.py), so the step is prompt-and-upload only.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const updateWorkspaceMock = vi.fn()
const upsertProductMock = vi.fn()
const advanceStepMock = vi.fn()
const promptMock = vi.fn()
const importFileMock = vi.fn()
const writeTextMock = vi.fn()

vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
  upsertPrimaryProduct: (...a: unknown[]) => upsertProductMock(...a),
  advanceOnboardingStep: (...a: unknown[]) => advanceStepMock(...a),
  // applyImportedContext serializes imported metrics into the KPI-tree shape;
  // a light stand-in keeps the flat list observable in the patch assertion.
  serializeKpiTree: (tree: { metrics: Array<{ name: string }> }) => ({
    primary_metrics: tree.metrics.map((m) => ({ metric: m.name })),
  }),
}))
vi.mock("../../../../lib/api", () => ({
  llmContextApi: {
    prompt: (...a: unknown[]) => promptMock(...a),
    importFile: (...a: unknown[]) => importFileMock(...a),
  },
}))

import { ImportContextStep } from "../ImportContextStep"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

const PROMPT = "You are helping me export the context...\n\n## Company\n- Name:"

function mount(workspace = makeWorkspace({ onboarding_step: 2 })) {
  onboardingMock.mockReturnValue(
    makeOnboardingCtx({ workspace, setWorkspace: vi.fn(), loading: false }),
  )
  return render(React.createElement(ImportContextStep))
}

/** Fire a file selection at the hidden .md input. */
function uploadMd(container: HTMLElement, body = "## Portfolio\nOne app.\n") {
  const input = container.querySelector(
    'input[type="file"]',
  ) as HTMLInputElement
  const file = new File([body], "context.md", { type: "text/markdown" })
  fireEvent.change(input, { target: { files: [file] } })
  return file
}

beforeEach(() => {
  vi.clearAllMocks()
  promptMock.mockResolvedValue({ prompt: PROMPT, format_version: "1" })
  updateWorkspaceMock.mockImplementation(async (_id, patch) =>
    makeWorkspace({ ...patch }),
  )
  upsertProductMock.mockResolvedValue({ id: "p-1" })
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: writeTextMock },
    configurable: true,
  })
  writeTextMock.mockResolvedValue(undefined)
})

afterEach(cleanup)

describe("ImportContextStep (onboarding step 02 — import your context)", () => {
  it("renders on step 2 of the dots, with no connect-an-account option", async () => {
    const { container } = mount()
    await waitFor(() => expect(promptMock).toHaveBeenCalled())

    expect(
      (container.querySelector(".onb-dots") as HTMLElement).getAttribute(
        "data-step",
      ),
    ).toBe("2")
    expect(container.textContent).toContain("Copy a prompt for your own AI")
    // The OAuth path is gone — it must not reappear as dead UI.
    expect(container.textContent).not.toContain("Connect Claude")
  })

  it("hides the prompt until asked, then reveals it with Copy inside", async () => {
    const { container } = mount()
    await waitFor(() => expect(promptMock).toHaveBeenCalled())

    // Collapsed: the prompt text is not on the page and Copy isn't offered.
    expect(container.querySelector(".onb-prompt-panel")).toBeNull()
    expect(container.textContent).not.toContain("Copy prompt")
    const toggle = screen.getByRole("button", { name: "Show prompt" })
    expect(toggle.getAttribute("aria-expanded")).toBe("false")

    fireEvent.click(toggle)

    // Revealed: the prompt is readable, and Copy now lives in the panel.
    const panel = container.querySelector(".onb-prompt-panel") as HTMLElement
    expect(panel).not.toBeNull()
    const box = panel.querySelector("textarea") as HTMLTextAreaElement
    expect(box.value).toContain("## Company")
    expect(screen.getByRole("button", { name: "Hide prompt" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "Copy prompt" })).toBeTruthy()
  })

  it("copies the exact prompt the backend served, and confirms it", async () => {
    mount()
    await waitFor(() => expect(promptMock).toHaveBeenCalled())

    fireEvent.click(screen.getByRole("button", { name: "Show prompt" }))
    fireEvent.click(screen.getByRole("button", { name: "Copy prompt" }))

    // The copied text is the server's, never a client-side duplicate that
    // could drift from what the parser expects to read back.
    expect(writeTextMock).toHaveBeenCalledWith(PROMPT)
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy(),
    )
  })

  it("copies the user's edits, not the pristine server copy", async () => {
    const { container } = mount()
    await waitFor(() => expect(promptMock).toHaveBeenCalled())
    fireEvent.click(screen.getByRole("button", { name: "Show prompt" }))

    const box = container.querySelector(
      "textarea.onb-prompt-panel-body",
    ) as HTMLTextAreaElement
    fireEvent.change(box, { target: { value: "Only the Nutrition workspace." } })
    fireEvent.click(screen.getByRole("button", { name: "Copy prompt" }))

    expect(writeTextMock).toHaveBeenCalledWith("Only the Nutrition workspace.")
  })

  it("offers Reset only once edited, and restores the served prompt", async () => {
    const { container } = mount()
    await waitFor(() => expect(promptMock).toHaveBeenCalled())
    fireEvent.click(screen.getByRole("button", { name: "Show prompt" }))

    // Unedited: nothing to reset.
    expect(screen.queryByRole("button", { name: "Reset" })).toBeNull()

    const box = container.querySelector(
      "textarea.onb-prompt-panel-body",
    ) as HTMLTextAreaElement
    fireEvent.change(box, { target: { value: "narrowed" } })
    fireEvent.click(screen.getByRole("button", { name: "Reset" }))

    expect(box.value).toBe(PROMPT)
    expect(screen.queryByRole("button", { name: "Reset" })).toBeNull()
  })

  it("collapses again on a second click", async () => {
    const { container } = mount()
    await waitFor(() => expect(promptMock).toHaveBeenCalled())

    fireEvent.click(screen.getByRole("button", { name: "Show prompt" }))
    expect(container.querySelector(".onb-prompt-panel")).not.toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Hide prompt" }))
    expect(container.querySelector(".onb-prompt-panel")).toBeNull()
  })

  it("prefills only the workspace fields that are still empty", async () => {
    // The company already has a mission; the export carries a different one.
    const workspace = makeWorkspace({
      onboarding_step: 2,
      display_name: "",
      mission: "The mission the user already typed.",
    })
    importFileMock.mockResolvedValue({
      ok: true,
      fields: {
        company_name: "Samsung Health",
        mission: "A mission from the export.",
        portfolio: "Watch, Ring.",
      },
      unmapped: {},
      format_version: "1",
      note: null,
    })

    const { container } = mount(workspace)
    await waitFor(() => expect(promptMock).toHaveBeenCalled())
    uploadMd(container)

    await waitFor(() => expect(updateWorkspaceMock).toHaveBeenCalled())
    const patch = updateWorkspaceMock.mock.calls[0][1]
    expect(patch.display_name).toBe("Samsung Health")
    expect(patch.portfolio).toBe("Watch, Ring.")
    // The user's own words survive the import untouched.
    expect(patch).not.toHaveProperty("mission")

    await waitFor(() =>
      expect(container.textContent).toContain("Context imported."),
    )
  })

  it("reports an unreadable export instead of claiming success", async () => {
    importFileMock.mockResolvedValue({
      ok: false,
      fields: {},
      unmapped: {},
      format_version: null,
      note: "We couldn't find any of the expected sections in that file.",
    })

    const { container } = mount()
    await waitFor(() => expect(promptMock).toHaveBeenCalled())
    uploadMd(container, "nothing recognisable")

    await waitFor(() =>
      expect(container.textContent).toContain(
        "couldn't find any of the expected sections",
      ),
    )
    expect(container.textContent).not.toContain("Context imported.")
    // Nothing was written to the workspace on a failed read.
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
  })

  it("lets the user skip to the connectors step without importing", async () => {
    mount()
    await waitFor(() => expect(promptMock).toHaveBeenCalled())

    fireEvent.click(screen.getByRole("button", { name: "Fill it in manually" }))
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("stays usable when the prompt fetch fails", async () => {
    promptMock.mockRejectedValue(new Error("offline"))
    const { container } = mount()

    await waitFor(() =>
      expect(container.textContent).toContain("Couldn't load the prompt"),
    )
    // Show is disabled (there is nothing to show), but upload still works.
    expect(
      (screen.getByRole("button", { name: "Show prompt" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true)
    expect(
      (screen.getByRole("button", { name: "Upload .md" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false)
  })
})

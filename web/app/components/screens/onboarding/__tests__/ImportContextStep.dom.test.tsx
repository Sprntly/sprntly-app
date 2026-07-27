// @vitest-environment jsdom
//
// Container mount test for onboarding step 02 — "Import your context" (client
// feedback 2026-07-22; moved back behind the company step 2026-07-27). Covers
// the show-then-copy disclosure (the prompt is hidden until asked for, and Copy
// lives inside the revealed panel), the .md upload path, and the four
// properties that make an import safe and useful:
//
//   * the prompt is requested WITH the company name + website step 1 collected,
//     so the assistant it is pasted into searches for the right company;
//   * a successful import prefills ONLY workspace fields that are still empty
//     — it must never overwrite something the user already typed;
//   * an unreadable export surfaces its `note` instead of claiming success;
//   * a deep-link with no company row yet still creates one before uploading —
//     the upload endpoint is tenant-scoped and would otherwise 403.
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

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const createWorkspaceMock = vi.fn()
const updateWorkspaceMock = vi.fn()
const upsertProductMock = vi.fn()
const saveWorkspaceFieldsMock = vi.fn()
const advanceStepMock = vi.fn()
const promptMock = vi.fn()
const importFileMock = vi.fn()
const writeTextMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  createWorkspace: (...a: unknown[]) => createWorkspaceMock(...a),
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
  upsertPrimaryProduct: (...a: unknown[]) => upsertProductMock(...a),
  saveWorkspaceOwnedFields: (...a: unknown[]) => saveWorkspaceFieldsMock(...a),
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
import { makeWorkspace, makeOnboardingCtx, makeProduct } from "./fixtures"

const PROMPT = "You are helping me export the context...\n\n## Company\n- Name:"

function mount(
  workspace: ReturnType<typeof makeWorkspace> | null = makeWorkspace({
    onboarding_step: 1,
  }),
  ctx: Record<string, unknown> = {},
) {
  onboardingMock.mockReturnValue(
    makeOnboardingCtx({ workspace, setWorkspace: vi.fn(), loading: false, ...ctx }),
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
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" } })
  promptMock.mockResolvedValue({ prompt: PROMPT, format_version: "2" })
  saveWorkspaceFieldsMock.mockResolvedValue(undefined)
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

// The prompt is fetched asynchronously after mount; "Show prompt" is disabled
// and the panel is gated on `prompt` until it resolves. Waiting only for the
// fetch to be CALLED (not resolved) before clicking is a microtask race that
// no-ops the click under CI timing — this waits for the button to be ENABLED,
// then reveals the panel deterministically.
async function revealPrompt() {
  const toggle = await waitFor(() => {
    const b = screen.getByRole("button", { name: "Show prompt" }) as HTMLButtonElement
    expect(b.disabled).toBe(false)
    return b
  })
  fireEvent.click(toggle)
}

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

  it("asks for the prompt with the company step's name and website", async () => {
    // The whole reason `company` runs first: the backend writes these into the
    // prompt's confirmed-values block, so the assistant starts with the entity
    // locked instead of guessing which company the file is about.
    const workspace = makeWorkspace({
      onboarding_step: 2,
      display_name: "Samsung Health",
      product: makeProduct({ website: "https://www.samsung.com/health" }),
    })
    const { container } = mount(workspace)

    await waitFor(() =>
      expect(promptMock).toHaveBeenCalledWith({
        companyName: "Samsung Health",
        companyWebsite: "https://www.samsung.com/health",
      }),
    )
    // …and the card says so, so the user knows what they're about to paste.
    expect(container.textContent).toContain("It already names")
    expect(container.textContent).toContain("Samsung Health")
  })

  it("still serves a prompt when there is no company row to fill it from", async () => {
    // A deep-link straight to step 2. The block ships empty rather than the
    // fetch failing — the user can type their own name into the textarea.
    mount(null)
    await waitFor(() =>
      expect(promptMock).toHaveBeenCalledWith({
        companyName: "",
        companyWebsite: "",
      }),
    )
  })

  it("leads with the plain-language pitch, above the card and always visible", async () => {
    // The whole step turns on the user realising the prompt is for the
    // assistant they already use. That has to land before the buttons do, and
    // without a click — so it sits in the step body, not inside the collapsed
    // prompt panel.
    const { container } = mount()
    await waitFor(() => expect(promptMock).toHaveBeenCalled())

    const lead = container.querySelector(".ctx-import-lead") as HTMLElement
    expect(lead).not.toBeNull()
    expect(lead.textContent).toContain("Already use Claude or ChatGPT for work?")
    expect(lead.textContent).toContain("Copy the prompt below")
    // Above the card it introduces, not below it.
    const card = container.querySelector(".onb-import-options") as HTMLElement
    expect(lead.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it("hides the prompt until asked, then reveals it with Copy inside", async () => {
    const { container } = mount()
    await waitFor(() => expect(promptMock).toHaveBeenCalled())

    // Collapsed: the prompt text is not on the page and Copy isn't offered.
    expect(container.querySelector(".onb-prompt-panel")).toBeNull()
    expect(container.textContent).not.toContain("Copy prompt")
    const toggle = screen.getByRole("button", { name: "Show prompt" })
    expect(toggle.getAttribute("aria-expanded")).toBe("false")

    await revealPrompt()

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
    await revealPrompt()
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
    await revealPrompt()

    const box = container.querySelector(
      "textarea.onb-prompt-panel-body",
    ) as HTMLTextAreaElement
    fireEvent.change(box, { target: { value: "Only the Nutrition workspace." } })
    fireEvent.click(screen.getByRole("button", { name: "Copy prompt" }))

    expect(writeTextMock).toHaveBeenCalledWith("Only the Nutrition workspace.")
  })

  it("offers Reset only once edited, and restores the served prompt", async () => {
    const { container } = mount()
    await revealPrompt()

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
    await revealPrompt()
    expect(container.querySelector(".onb-prompt-panel")).not.toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Hide prompt" }))
    expect(container.querySelector(".onb-prompt-panel")).toBeNull()
  })

  it("prefills only the workspace fields that are still empty", async () => {
    // The company already has a mission; the export carries a different one.
    const workspace = makeWorkspace({
      onboarding_step: 1,
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

  it("lets the user skip on to connectors without importing", async () => {
    mount()
    await waitFor(() => expect(promptMock).toHaveBeenCalled())

    fireEvent.click(screen.getByRole("button", { name: "Fill it in manually" }))
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("skipping the step creates nothing — the company step already owns that", async () => {
    mount(null)
    await waitFor(() => expect(promptMock).toHaveBeenCalled())

    fireEvent.click(screen.getByRole("button", { name: "Fill it in manually" }))
    expect(createWorkspaceMock).not.toHaveBeenCalled()
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("goes back to the company step", async () => {
    mount()
    await waitFor(() => expect(promptMock).toHaveBeenCalled())

    fireEvent.click(screen.getByRole("button", { name: "Back" }))
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/company")
  })

  it("creates the company row before uploading when there isn't one yet", async () => {
    // The deep-link safety net: normally step 1 made this row. `/llm-context/
    // import` is tenant-scoped, so uploading without one 403s — and the row is
    // created UNNAMED, because a guessed name would reach the company step
    // looking like the user's own answer.
    createWorkspaceMock.mockResolvedValue(makeWorkspace({ id: "ws-new", display_name: "" }))
    importFileMock.mockResolvedValue({
      ok: true,
      fields: { company_name: "Samsung Health" },
      unmapped: {},
      format_version: "2",
      note: null,
      job_id: 7,
    })
    const startContextImport = vi.fn()
    const { container } = mount(null, { startContextImport })
    await waitFor(() => expect(promptMock).toHaveBeenCalled())
    uploadMd(container)

    await waitFor(() => expect(importFileMock).toHaveBeenCalled())
    expect(createWorkspaceMock).toHaveBeenCalledTimes(1)
    const arg = createWorkspaceMock.mock.calls[0][0] as Record<string, unknown>
    expect(arg.companyName).toBe("")
    // Blank name → no product row (products_name_nonempty).
    expect(arg.productName).toBe("")
    // The resume marker points BACK at the company step, which collects the
    // name this row is missing.
    expect(arg.onboardingStep).toBe(1)
    // The import lands on the row we just created, not on a stale null.
    await waitFor(() => expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-new", expect.anything()))
    expect(startContextImport).toHaveBeenCalledWith(7, "ws-new")
  })

  it("names the fields back once the background extraction lands", async () => {
    // The normal path since the v3 prompt: the upload carries no fields at all
    // (the LLM pass is the only reader), so the confirmation has to come from
    // the extraction result the provider hands back — not from the POST.
    importFileMock.mockResolvedValue({
      ok: false,
      fields: {},
      unmapped: {},
      format_version: "3",
      note: null,
      job_id: 9,
      filed: true,
    })

    const { container } = mount(makeWorkspace({ onboarding_step: 1 }), {
      contextImport: "done",
      contextImportFields: {
        company_name: "Samsung Health",
        competitors: ["Apple Health", "Oura"],
      },
    })
    await waitFor(() => expect(promptMock).toHaveBeenCalled())

    expect(container.textContent).toContain("Context imported.")
    expect(container.textContent).toContain("company, competitors")
  })

  it("says so plainly when the extraction read nothing", async () => {
    // "Read it, found nothing for the form" is not the same as losing the file:
    // it is already filed and feeding the knowledge graph, so the copy must not
    // read as a failure.
    const { container } = mount(makeWorkspace({ onboarding_step: 1 }), {
      contextImport: "done",
      contextImportFields: {},
    })
    await waitFor(() => expect(promptMock).toHaveBeenCalled())

    expect(container.textContent).toContain("couldn't fill anything in")
    expect(container.textContent).toContain("saved to your documents")
    expect(container.textContent).not.toContain("Context imported.")
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

// @vitest-environment jsdom
//
// Container mount test for onboarding step 07 — "How your team decides" (v6
// screenshot spec 2026-07-17, NEW step). Optional and fully skippable. TWO
// upload-OR-type blocks:
//   - How does your team make decisions? — upload → companyDocsApi.upload
//     (file, "decision_process"); typed → companies.decision_process
//   - Anything else you want to share — upload → doc_type
//     "additional_context"; typed → companies.additional_context
//
// Covers: both blocks render; a doc upload calls the documents API with its
// doc_type; typed text persists on Continue via updateWorkspace
// (decision_process/additional_context + onboarding_step 8) →
// /onboarding/invite; the footer "Skip" advances without persisting text;
// "Skip to end ⇥" persists then jumps to review.
//
// Matchers: native DOM only.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const advanceStepMock = vi.fn()
const updateWorkspaceMock = vi.fn()
const docUploadMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  advanceOnboardingStep: (...a: unknown[]) => advanceStepMock(...a),
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
}))
vi.mock("../../../../lib/onboarding/useFormDraft", () => ({
  saveDraft: vi.fn(),
  loadDraft: () => null,
  clearDraft: vi.fn(),
}))
vi.mock("../../../../lib/api", () => ({
  companyDocsApi: { upload: (...a: unknown[]) => docUploadMock(...a) },
}))

import { DecisionsStep } from "../DecisionsStep"
import { ONBOARDING_STEP_COUNT } from "../../../../lib/onboarding/types"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

const DECISIONS_TITLE = "How does your team make decisions?"
const EXTRA_TITLE = "Anything else you want to share"

function mount(workspace = makeWorkspace({ onboarding_step: 7 })) {
  onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace }))
  return render(React.createElement(DecisionsStep))
}

function block(field: "decision-process" | "additional-context"): HTMLElement {
  return document.querySelector(`[data-field="${field}"]`) as HTMLElement
}

function typeInsteadToggle(
  field: "decision-process" | "additional-context",
): HTMLButtonElement {
  return Array.from(block(field).querySelectorAll("button")).find((b) =>
    /Type instead/.test(b.textContent ?? ""),
  ) as HTMLButtonElement
}

function continueBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /^next$/i.test((b.textContent ?? "").trim()),
  ) as HTMLButtonElement
}

function skipLink(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find(
    (b) => (b.textContent ?? "").trim() === "Skip",
  ) as HTMLButtonElement
}

function skipToEndBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /Skip to end/.test(b.textContent ?? ""),
  ) as HTMLButtonElement
}

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("DecisionsStep (onboarding step 07 — decisions + extra context, upload or type)", () => {
  it("renders the heading and BOTH upload-or-type blocks on step 7", () => {
    const { container } = mount()
    expect(screen.getByText(/decides\./)).not.toBeNull()
    expect(block("decision-process")).not.toBeNull()
    expect(block("additional-context")).not.toBeNull()
    expect(screen.getByText(DECISIONS_TITLE)).not.toBeNull()
    expect(screen.getByText(EXTRA_TITLE)).not.toBeNull()
    expect(typeInsteadToggle("decision-process")).not.toBeUndefined()
    expect(typeInsteadToggle("additional-context")).not.toBeUndefined()
    expect(
      (container.querySelector(".onb-dots") as HTMLElement).getAttribute("data-step"),
    ).toBe("7")
  })

  it("a decisions upload calls companyDocsApi.upload with doc_type decision_process", async () => {
    docUploadMock.mockResolvedValue({
      ok: true,
      id: "d-1",
      doc_type: "decision_process",
      filename: "raci.pdf",
      content_type: "application/pdf",
      extracted_chars: 88,
      uploaded_at: null,
    })
    mount()

    const input = block("decision-process").querySelector(
      `input[aria-label="${DECISIONS_TITLE} file"]`,
    ) as HTMLInputElement
    const file = new File(["raci"], "raci.pdf", { type: "application/pdf" })
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } })
    })

    expect(docUploadMock).toHaveBeenCalledTimes(1)
    expect((docUploadMock.mock.calls[0][0] as File).name).toBe("raci.pdf")
    expect(docUploadMock.mock.calls[0][1]).toBe("decision_process")
    expect(
      block("decision-process").querySelector('[data-uploaded="true"]'),
    ).not.toBeNull()
    expect(screen.getByText(/uploaded just now/i)).not.toBeNull()
  })

  it("an extra-context upload calls companyDocsApi.upload with doc_type additional_context", async () => {
    docUploadMock.mockResolvedValue({
      ok: true,
      id: "d-2",
      doc_type: "additional_context",
      filename: "glossary.pdf",
      content_type: "application/pdf",
      extracted_chars: 44,
      uploaded_at: null,
    })
    mount()

    const input = block("additional-context").querySelector(
      `input[aria-label="${EXTRA_TITLE} file"]`,
    ) as HTMLInputElement
    const file = new File(["glossary"], "glossary.pdf", { type: "application/pdf" })
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } })
    })

    expect(docUploadMock).toHaveBeenCalledTimes(1)
    expect(docUploadMock.mock.calls[0][1]).toBe("additional_context")
  })

  it("a failed upload surfaces a non-blocking notice, step not blocked", async () => {
    docUploadMock.mockRejectedValue(new Error("network error"))
    mount()

    const input = block("decision-process").querySelector(
      `input[aria-label="${DECISIONS_TITLE} file"]`,
    ) as HTMLInputElement
    const file = new File(["x"], "raci.pdf", { type: "application/pdf" })
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } })
    })

    expect(screen.getByText(/won't block setup/i)).not.toBeNull()
    expect(block("decision-process").querySelector('[data-uploaded="true"]')).toBeNull()
    expect(continueBtn().disabled).toBe(false)
  })

  it("typed text persists on Continue (step 8) and routes to invite", async () => {
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 8 }))
    mount()

    fireEvent.click(typeInsteadToggle("decision-process"))
    fireEvent.change(screen.getByLabelText(DECISIONS_TITLE), {
      target: { value: "RICE with a weekly triage." },
    })
    fireEvent.click(typeInsteadToggle("additional-context"))
    fireEvent.change(screen.getByLabelText(EXTRA_TITLE), {
      target: { value: "We size in t-shirt sizes." },
    })

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/invite")
    })
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      decision_process: "RICE with a weekly triage.",
      additional_context: "We size in t-shirt sizes.",
      onboarding_step: 8,
    })
    expect(advanceStepMock).not.toHaveBeenCalled()
  })

  it("the footer 'Skip' advances to step 8 WITHOUT persisting typed text", async () => {
    advanceStepMock.mockResolvedValue(makeWorkspace({ onboarding_step: 8 }))
    mount()

    await act(async () => {
      skipLink().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/invite")
    })
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 8)
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
  })

  it("'Skip to end ⇥' persists with step 9 and routes to review", async () => {
    updateWorkspaceMock.mockResolvedValue(
      makeWorkspace({ onboarding_step: ONBOARDING_STEP_COUNT }),
    )
    mount()

    await act(async () => {
      skipToEndBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/review")
    })
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      decision_process: null,
      additional_context: null,
      onboarding_step: ONBOARDING_STEP_COUNT,
    })
  })

  it("Back routes to the strategy step", () => {
    mount()
    fireEvent.click(screen.getByText("Back").closest("button") as HTMLElement)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/strategy")
  })

  it("shows the loading shell while the workspace is loading", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(DecisionsStep))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(DecisionsStep))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/company")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})

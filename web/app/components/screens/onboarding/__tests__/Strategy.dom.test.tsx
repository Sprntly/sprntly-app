// @vitest-environment jsdom
//
// Container mount test for onboarding step 06 — "Strategy & roadmap" (v6
// screenshot spec 2026-07-17). Optional and fully skippable. TWO upload-OR-
// type blocks:
//   - Team strategy: upload → companyDocsApi.upload(file, "team_strategy");
//     "Type instead" → textarea → companies.team_strategy
//   - Team roadmap:  upload → roadmapDocApi.upload(file); typed →
//     companies.team_roadmap
//
// Covers: both blocks render; uploads call the right API + flip to the
// uploaded state; a failed upload surfaces a non-blocking notice; typed text
// persists on Continue via updateWorkspace (team_strategy/team_roadmap +
// onboarding_step 7) → /onboarding/decisions; the footer "Skip" advances
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
const roadmapUploadMock = vi.fn()
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
  roadmapDocApi: { upload: (...a: unknown[]) => roadmapUploadMock(...a) },
}))

import { Strategy } from "../Strategy"
import { ONBOARDING_STEP_COUNT } from "../../../../lib/onboarding/types"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

function mount(workspace = makeWorkspace({ onboarding_step: 6 })) {
  onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace }))
  return render(React.createElement(Strategy))
}

function block(field: "team-strategy" | "team-roadmap"): HTMLElement {
  return document.querySelector(`[data-field="${field}"]`) as HTMLElement
}

function typeInsteadToggle(field: "team-strategy" | "team-roadmap"): HTMLButtonElement {
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

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Strategy (onboarding step 06 — 'Strategy & roadmap' upload-or-type)", () => {
  it("renders the heading and BOTH upload-or-type blocks", () => {
    mount()
    expect(screen.getByText(/roadmap\./)).not.toBeNull()
    expect(block("team-strategy")).not.toBeNull()
    expect(block("team-roadmap")).not.toBeNull()
    expect(screen.getByText("Team strategy")).not.toBeNull()
    expect(screen.getByText("Team roadmap")).not.toBeNull()
    // Each block starts as an upload card with a "Type instead" toggle.
    expect(typeInsteadToggle("team-strategy")).not.toBeUndefined()
    expect(typeInsteadToggle("team-roadmap")).not.toBeUndefined()
  })

  it("a strategy upload calls companyDocsApi.upload with doc_type team_strategy + shows confirmation", async () => {
    docUploadMock.mockResolvedValue({
      ok: true,
      id: "d-1",
      doc_type: "team_strategy",
      filename: "strategy.pdf",
      content_type: "application/pdf",
      extracted_chars: 120,
      uploaded_at: null,
    })
    mount()

    const input = block("team-strategy").querySelector(
      'input[aria-label="Team strategy file"]',
    ) as HTMLInputElement
    const file = new File(["strategy"], "strategy.pdf", { type: "application/pdf" })
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } })
    })

    expect(docUploadMock).toHaveBeenCalledTimes(1)
    expect((docUploadMock.mock.calls[0][0] as File).name).toBe("strategy.pdf")
    expect(docUploadMock.mock.calls[0][1]).toBe("team_strategy")
    expect(
      block("team-strategy").querySelector('[data-uploaded="true"]'),
    ).not.toBeNull()
    expect(screen.getByText(/uploaded just now/i)).not.toBeNull()
    expect(roadmapUploadMock).not.toHaveBeenCalled()
  })

  it("a roadmap upload calls roadmapDocApi.upload + shows confirmation", async () => {
    roadmapUploadMock.mockResolvedValue({
      ok: true,
      filename: "roadmap.pdf",
      extracted_chars: 420,
      version: 1,
    })
    mount()

    const input = block("team-roadmap").querySelector(
      'input[aria-label="Team roadmap file"]',
    ) as HTMLInputElement
    const file = new File(["roadmap"], "roadmap.pdf", { type: "application/pdf" })
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } })
    })

    expect(roadmapUploadMock).toHaveBeenCalledTimes(1)
    expect((roadmapUploadMock.mock.calls[0][0] as File).name).toBe("roadmap.pdf")
    expect(
      block("team-roadmap").querySelector('[data-uploaded="true"]'),
    ).not.toBeNull()
    expect(docUploadMock).not.toHaveBeenCalled()
  })

  it("a failed upload surfaces a non-blocking notice, step not blocked", async () => {
    docUploadMock.mockRejectedValue(new Error("network error"))
    mount()

    const input = block("team-strategy").querySelector(
      'input[aria-label="Team strategy file"]',
    ) as HTMLInputElement
    const file = new File(["x"], "strategy.pdf", { type: "application/pdf" })
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } })
    })

    expect(docUploadMock).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/won't block setup/i)).not.toBeNull()
    expect(block("team-strategy").querySelector('[data-uploaded="true"]')).toBeNull()
    expect(continueBtn().disabled).toBe(false)
  })

  it("typed text persists on Continue (step 7) and routes to decisions", async () => {
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 7 }))
    mount()

    fireEvent.click(typeInsteadToggle("team-strategy"))
    fireEvent.change(screen.getByLabelText("Team strategy"), {
      target: { value: "Win SMB fintech this half." },
    })
    fireEvent.click(typeInsteadToggle("team-roadmap"))
    fireEvent.change(screen.getByLabelText("Team roadmap"), {
      target: { value: "Q3: reconciliation v2." },
    })

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/decisions")
    })
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      team_strategy: "Win SMB fintech this half.",
      team_roadmap: "Q3: reconciliation v2.",
      onboarding_step: 8,
    })
    expect(advanceStepMock).not.toHaveBeenCalled()
  })

  it("the footer 'Skip' advances to step 7 WITHOUT persisting typed text", async () => {
    advanceStepMock.mockResolvedValue(makeWorkspace({ onboarding_step: 7 }))
    mount()

    await act(async () => {
      skipLink().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/decisions")
    })
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 8)
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
  })

  it("Back routes to the team step", () => {
    mount()
    fireEvent.click(screen.getByText("Back").closest("button") as HTMLElement)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/team")
  })

  it("shows the loading shell while the workspace is loading", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(Strategy))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })
})

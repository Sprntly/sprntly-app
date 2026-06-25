// @vitest-environment jsdom
//
// Container mount test for the onboarding step 05 — "Strategy, leadership &
// your roadmap" (design scene onbstrat). In the redesign this is the FINAL
// step. This PR rebuilds its CONTENT: a 2×2 grid of typed document-upload cards
// (CEO memo / team priorities / research / company strategy → POST
// /v1/company/documents) + the existing roadmap-doc upload (POST
// /v1/company/roadmap-doc) as its own section. Then it COMPLETES onboarding —
// kicks the first brief, calls completeOnboarding, and enters the app at /brief.
//
// Covers: the 4 doc cards + roadmap card render; a doc-card upload calls the
// documents API with its doc_type + shows the "uploaded" confirmation; the
// roadmap upload calls its API + shows confirmation; a failed upload surfaces a
// non-blocking notice without halting the step; "Finish setup" and "Skip" both
// complete onboarding + redirect to /brief.
//
// Matchers: native DOM only.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const completeOnboardingMock = vi.fn()
const roadmapUploadMock = vi.fn()
const docUploadMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ setContent: vi.fn() }),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  completeOnboarding: (...a: unknown[]) => completeOnboardingMock(...a),
}))
vi.mock("../../../../lib/workspace-brief", () => ({
  ensureDatasetForWorkspace: vi.fn().mockResolvedValue(undefined),
  seedWorkspaceContextFiles: vi.fn().mockResolvedValue(undefined),
  fetchBriefWhenReady: vi.fn().mockResolvedValue(null),
  startBriefGeneration: vi.fn().mockResolvedValue(undefined),
}))
vi.mock("../../../../lib/brief-adapter", () => ({ briefToContentPatch: () => ({}) }))
vi.mock("../../../../lib/onboarding/useFormDraft", () => ({
  saveDraft: vi.fn(),
  loadDraft: () => null,
  clearDraft: vi.fn(),
}))
vi.mock("../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../lib/api")>(
    "../../../../lib/api",
  )
  return {
    ...actual,
    roadmapDocApi: { upload: (...a: unknown[]) => roadmapUploadMock(...a) },
    companyDocsApi: { upload: (...a: unknown[]) => docUploadMock(...a) },
  }
})

import { Strategy } from "../Strategy"
import { makeOnboardingCtx } from "./fixtures"

const DOC_TYPES = ["ceo_memo", "team_priorities", "research", "company_strategy"]

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Strategy (onboarding step 05 — onbstrat upload cards, completes onboarding)", () => {
  it("renders the heading, the 4 typed doc cards, and the roadmap-doc card", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    render(React.createElement(Strategy))

    expect(screen.getByText(/your roadmap/i)).not.toBeNull()
    // The 2×2 grid of typed cards.
    expect(document.querySelector(".onb-up-grid")).not.toBeNull()
    for (const dt of DOC_TYPES) {
      expect(document.querySelector(`[data-field="doc-${dt}"]`)).not.toBeNull()
    }
    // The roadmap card remains, as its own section.
    expect(document.querySelector('[data-field="roadmap-doc"]')).not.toBeNull()
    // The verbatim card copy is present.
    expect(screen.getByText(/CEO memo \/ priorities for the half/i)).not.toBeNull()
    expect(screen.getByText(/Research & insights/i)).not.toBeNull()
    expect(screen.getByText(/Company strategy/i)).not.toBeNull()
  })

  it("a doc card upload calls the documents API with its doc_type + shows confirmation", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    docUploadMock.mockResolvedValue({
      ok: true,
      id: "d-1",
      doc_type: "ceo_memo",
      filename: "memo.pdf",
      content_type: "application/pdf",
      extracted_chars: 120,
      uploaded_at: null,
    })

    render(React.createElement(Strategy))
    // The CEO-memo card's hidden file input.
    const card = document.querySelector('[data-field="doc-ceo_memo"]') as HTMLElement
    const input = card.parentElement!.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    const file = new File(["memo"], "memo.pdf", { type: "application/pdf" })
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } })
    })

    expect(docUploadMock).toHaveBeenCalledTimes(1)
    expect((docUploadMock.mock.calls[0][0] as File).name).toBe("memo.pdf")
    expect(docUploadMock.mock.calls[0][1]).toBe("ceo_memo")
    expect(
      document.querySelector('[data-field="doc-ceo_memo"][data-uploaded="true"]'),
    ).not.toBeNull()
    expect(screen.getByText(/uploaded just now/i)).not.toBeNull()
  })

  it("a failed doc upload surfaces a non-blocking notice, step not blocked", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    docUploadMock.mockRejectedValue(new Error("network error"))

    render(React.createElement(Strategy))
    const card = document.querySelector('[data-field="doc-research"]') as HTMLElement
    const input = card.parentElement!.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    const file = new File(["x"], "study.pdf", { type: "application/pdf" })
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } })
    })

    expect(docUploadMock).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/won't block setup/i)).not.toBeNull()
    expect(
      document.querySelector('[data-field="doc-research"][data-uploaded="true"]'),
    ).toBeNull()
    const finishBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /finish setup/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    expect(finishBtn.disabled).toBe(false)
  })

  it("the roadmap-doc upload calls the REAL API and shows the uploaded confirmation", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    roadmapUploadMock.mockResolvedValue({
      ok: true,
      filename: "roadmap.pdf",
      extracted_chars: 420,
      version: 1,
    })

    render(React.createElement(Strategy))
    const card = document.querySelector('[data-field="roadmap-doc"]') as HTMLElement
    const input = card.parentElement!.querySelector(
      'input[aria-label="Roadmap document"]',
    ) as HTMLInputElement
    const file = new File(["roadmap"], "roadmap.pdf", { type: "application/pdf" })
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } })
    })

    expect(roadmapUploadMock).toHaveBeenCalledTimes(1)
    expect((roadmapUploadMock.mock.calls[0][0] as File).name).toBe("roadmap.pdf")
    expect(
      document.querySelector('[data-field="roadmap-doc"][data-uploaded="true"]'),
    ).not.toBeNull()
  })

  it("'Finish setup' completes onboarding and enters /brief", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    completeOnboardingMock.mockResolvedValue(undefined)

    render(React.createElement(Strategy))
    const finishBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /finish setup/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      finishBtn.click()
    })

    expect(completeOnboardingMock).toHaveBeenCalledWith("ws-1", "u-1")
    expect(routerMock.replace).toHaveBeenCalledWith("/brief")
  })

  it("'Skip' completes onboarding and enters /brief", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    completeOnboardingMock.mockResolvedValue(undefined)

    render(React.createElement(Strategy))
    const skip = Array.from(document.querySelectorAll("button")).find((b) =>
      /^skip$/i.test((b.textContent ?? "").trim()),
    ) as HTMLButtonElement
    await act(async () => {
      skip.click()
    })

    expect(completeOnboardingMock).toHaveBeenCalledWith("ws-1", "u-1")
    expect(routerMock.replace).toHaveBeenCalledWith("/brief")
  })
})

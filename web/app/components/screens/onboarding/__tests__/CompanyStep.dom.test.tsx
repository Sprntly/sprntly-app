// @vitest-environment jsdom
//
// Container mount test for onboarding step 01 — "Tell us about your company"
// (v6 screenshot spec 2026-07-17). Covers: the name/website/mission/strategy
// fields render (seeded from the saved workspace) with only the name starred;
// an empty name blocks Continue (field error, no persistence, no navigation);
// the website is OPTIONAL for everyone — an empty one saves fine and simply
// skips the background analysis; a successful save with a workspace present
// goes updateWorkspace (incl. portfolio/planning_cycle/onboarding_step 2) +
// upsertPrimaryProduct → background website analysis → push(/onboarding/
// product); a first-time save (no workspace) creates one with account_type
// "company" (the personal split is retired).
//
// product-helpers (validateProductWebsite / normalizeProductWebsite) run REAL
// — they're pure and accept an empty website.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const createWorkspaceMock = vi.fn()
const updateWorkspaceMock = vi.fn()
const upsertProductMock = vi.fn()
const docUploadMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  createWorkspace: (...a: unknown[]) => createWorkspaceMock(...a),
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
  upsertPrimaryProduct: (...a: unknown[]) => upsertProductMock(...a),
}))
vi.mock("../../../../lib/onboarding/useFormDraft", () => ({
  saveDraft: vi.fn(),
  loadDraft: () => null,
  clearDraft: vi.fn(),
}))
vi.mock("../../../../lib/api", () => ({
  companyDocsApi: { upload: (...a: unknown[]) => docUploadMock(...a) },
}))

import { CompanyStep } from "../CompanyStep"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

function makeProduct(over: Record<string, unknown> = {}) {
  return {
    id: "p-1",
    company_id: "ws-1",
    name: "Acme",
    website: null,
    description: null,
    is_primary: true,
    surfaces: [],
    personas: [],
    positioning: null,
    monetization: [],
    users_description: null,
    maturity: null,
    ...over,
  }
}

const analysisSpy = vi.fn()

function mount(workspace: ReturnType<typeof makeWorkspace> | null = makeWorkspace()) {
  onboardingMock.mockReturnValue(
    makeOnboardingCtx({
      workspace,
      startWebsiteAnalysis: analysisSpy,
    }),
  )
  return render(React.createElement(CompanyStep))
}

function nameInput(): HTMLInputElement {
  return document.querySelector(
    'input[placeholder="Legal or brand name of your organization"]',
  ) as HTMLInputElement
}

function websiteInput(): HTMLInputElement {
  return document.querySelector(
    'input[placeholder="https://yourcompany.com"]',
  ) as HTMLInputElement
}

function continueBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /^next$/i.test((b.textContent ?? "").trim()),
  ) as HTMLButtonElement
}

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("CompanyStep (onboarding step 01 — company name* + optional context)", () => {
  it("renders name/website/mission/strategy fields seeded from the workspace — only the name is starred", () => {
    mount()
    expect(screen.getByText(/Tell us about your/)).not.toBeNull()
    expect(nameInput()).not.toBeNull()
    expect(websiteInput()).not.toBeNull()
    // Seeded from the workspace: display_name in, no product website yet.
    expect(nameInput().value).toBe("Acme")
    expect(websiteInput().value).toBe("")
    // Only the company name is required; the website is explicitly optional.
    const nameField = document.querySelector('[data-field="companyName"]') as HTMLElement
    expect(nameField.querySelector(".req")).not.toBeNull()
    const websiteField = document.querySelector('[data-field="website"]') as HTMLElement
    expect(websiteField.querySelector(".req")).toBeNull()
    expect(websiteField.querySelector(".opt")).not.toBeNull()
    // Mission + strategy textareas are visible (not behind a disclosure).
    expect(
      document.querySelector('textarea[placeholder="Why the company exists, in a sentence or two"]'),
    ).not.toBeNull()
    expect(
      document.querySelector('[data-field="strategy"] textarea'),
    ).not.toBeNull()
    // Portfolio + planning cycle sit behind the "Add more" disclosure.
    expect(screen.getByText(/Add more — portfolio, planning cycle/)).not.toBeNull()
  })

  it("Continue with an empty company name shows a field error and does NOT persist or navigate", async () => {
    mount()
    fireEvent.change(nameInput(), { target: { value: "" } })
    await act(async () => {
      continueBtn().click()
    })
    expect(screen.getByText("Enter your company name.")).not.toBeNull()
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(createWorkspaceMock).not.toHaveBeenCalled()
    expect(upsertProductMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("an EMPTY website saves fine (optional for everyone) — no analysis kicked", async () => {
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 2 }))
    upsertProductMock.mockResolvedValue(makeProduct())
    mount()

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/product")
    })
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      display_name: "Acme",
      mission: null,
      strategy: null,
      portfolio: null,
      planning_cycle: null,
      onboarding_step: 2,
    })
    expect(upsertProductMock).toHaveBeenCalledWith("ws-1", {
      name: "Acme",
      website: null,
    })
    // No website → the background analysis is never kicked.
    expect(analysisSpy).not.toHaveBeenCalled()
    expect(createWorkspaceMock).not.toHaveBeenCalled()
  })

  it("a successful save with a workspace present goes updateWorkspace + upsertPrimaryProduct → analysis → product step", async () => {
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 2 }))
    upsertProductMock.mockResolvedValue(makeProduct({ website: "https://acme.com" }))
    mount()

    fireEvent.change(websiteInput(), { target: { value: "acme.com" } })
    fireEvent.change(
      document.querySelector(
        'textarea[placeholder="Why the company exists, in a sentence or two"]',
      ) as HTMLTextAreaElement,
      { target: { value: "Make payments boring." } },
    )
    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/product")
    })
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      display_name: "Acme",
      mission: "Make payments boring.",
      strategy: null,
      portfolio: null,
      planning_cycle: null,
      onboarding_step: 2,
    })
    // The typed website is normalized to https and seeded onto the product.
    expect(upsertProductMock).toHaveBeenCalledWith("ws-1", {
      name: "Acme",
      website: "https://acme.com",
    })
    // The analysis kicks in the BACKGROUND with the saved product website.
    expect(analysisSpy).toHaveBeenCalledWith("https://acme.com", "ws-1")
    expect(createWorkspaceMock).not.toHaveBeenCalled()
  })

  it("first-time save (no workspace yet) creates one with account_type 'company' — the personal split is retired", async () => {
    createWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 2 }))
    mount(null)

    fireEvent.change(nameInput(), { target: { value: "Solo Co" } })
    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/product")
    })
    expect(createWorkspaceMock).toHaveBeenCalledTimes(1)
    const arg = createWorkspaceMock.mock.calls[0][0] as Record<string, unknown>
    expect(arg.companyName).toBe("Solo Co")
    expect(arg.productName).toBe("Solo Co")
    expect(arg.productWebsite).toBeNull()
    // Sign-up always writes account_type "company" since v6.
    expect(arg.accountType).toBe("company")
    expect(arg.userId).toBe("u-1")
    // No portfolio/planning cycle typed → no follow-up patch.
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
  })

  it("shows the loading shell while the workspace is loading", () => {
    authMock.mockReturnValue({ kind: "loading" })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(CompanyStep))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })
})

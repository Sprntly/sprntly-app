// @vitest-environment jsdom
//
// Container mount test for onboarding step 01 — "Tell us about your company"
// (registration spec 2026-07, Company section). Covers: the name + website
// fields render (seeded from the saved workspace); COMPANY accounts are
// blocked on an empty website (field error, no persistence, no navigation);
// PERSONAL accounts continue freely with an empty website (updateWorkspace or
// createWorkspace as appropriate) and record the skip via
// markSkippedFields("u-1", ["company_website"]); a successful save with a
// workspace present goes updateWorkspace + upsertPrimaryProduct → background
// website analysis → push(/onboarding/product).
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
const markSkippedMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  createWorkspace: (...a: unknown[]) => createWorkspaceMock(...a),
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
  upsertPrimaryProduct: (...a: unknown[]) => upsertProductMock(...a),
  markSkippedFields: (...a: unknown[]) => markSkippedMock(...a),
}))
vi.mock("../../../../lib/onboarding/useFormDraft", () => ({
  saveDraft: vi.fn(),
  loadDraft: () => null,
  clearDraft: vi.fn(),
}))

import { CompanyStep } from "../CompanyStep"
import { makeWorkspace, makeOnboardingCtx, makeProfile } from "./fixtures"

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
    maturity: null,
    ...over,
  }
}

const analysisSpy = vi.fn()

function mount(
  accountType: "company" | "personal" = "company",
  workspace: ReturnType<typeof makeWorkspace> | null = makeWorkspace(),
) {
  onboardingMock.mockReturnValue(
    makeOnboardingCtx({
      workspace,
      profile: makeProfile({ account_type: accountType }),
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
    /^continue$/i.test((b.textContent ?? "").trim()),
  ) as HTMLButtonElement
}

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("CompanyStep (onboarding step 01 — company name + website)", () => {
  it("renders the name + website fields, seeded from the saved workspace", () => {
    mount("company")
    expect(screen.getByText(/Tell us about your/)).not.toBeNull()
    expect(nameInput()).not.toBeNull()
    expect(websiteInput()).not.toBeNull()
    // Seeded from the workspace: display_name in, no product website yet.
    expect(nameInput().value).toBe("Acme")
    expect(websiteInput().value).toBe("")
    // The website is starred for COMPANY accounts.
    const websiteField = document.querySelector('[data-field="website"]') as HTMLElement
    expect(websiteField.querySelector(".req")).not.toBeNull()
  })

  it("COMPANY: Continue with an empty website shows a field error and does NOT navigate", async () => {
    mount("company")
    await act(async () => {
      continueBtn().click()
    })
    expect(screen.getByText("Enter your company website.")).not.toBeNull()
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(createWorkspaceMock).not.toHaveBeenCalled()
    expect(upsertProductMock).not.toHaveBeenCalled()
    expect(markSkippedMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("PERSONAL (workspace present): empty website proceeds — updateWorkspace + skip recorded", async () => {
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 2 }))
    upsertProductMock.mockResolvedValue(makeProduct())
    markSkippedMock.mockResolvedValue(undefined)
    mount("personal")

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
      onboarding_step: 2,
    })
    expect(upsertProductMock).toHaveBeenCalledWith("ws-1", {
      name: "Acme",
      website: null,
    })
    expect(markSkippedMock).toHaveBeenCalledWith("u-1", ["company_website"])
    // No website → the background analysis is never kicked.
    expect(analysisSpy).not.toHaveBeenCalled()
  })

  it("PERSONAL (no workspace yet): empty website proceeds — createWorkspace + skip recorded", async () => {
    createWorkspaceMock.mockResolvedValue(
      makeWorkspace({ onboarding_step: 2, account_type: "personal" }),
    )
    markSkippedMock.mockResolvedValue(undefined)
    mount("personal", null)

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
    expect(arg.accountType).toBe("personal")
    expect(arg.userId).toBe("u-1")
    expect(markSkippedMock).toHaveBeenCalledWith("u-1", ["company_website"])
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
  })

  it("COMPANY: a successful save with a workspace present goes updateWorkspace + upsertPrimaryProduct → analysis → product step", async () => {
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 2 }))
    upsertProductMock.mockResolvedValue(makeProduct({ website: "https://acme.com" }))
    mount("company")

    fireEvent.change(websiteInput(), { target: { value: "acme.com" } })
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
      onboarding_step: 2,
    })
    // The typed website is normalized to https and seeded onto the product.
    expect(upsertProductMock).toHaveBeenCalledWith("ws-1", {
      name: "Acme",
      website: "https://acme.com",
    })
    // The analysis kicks in the BACKGROUND with the saved product website.
    expect(analysisSpy).toHaveBeenCalledWith("https://acme.com", "ws-1")
    // Nothing was skipped on the mandatory path.
    expect(markSkippedMock).not.toHaveBeenCalled()
    expect(createWorkspaceMock).not.toHaveBeenCalled()
  })

  it("shows the loading shell while the workspace is loading", () => {
    authMock.mockReturnValue({ kind: "loading" })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(CompanyStep))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })
})

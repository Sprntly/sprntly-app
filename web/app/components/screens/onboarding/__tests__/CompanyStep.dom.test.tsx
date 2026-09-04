// @vitest-environment jsdom
//
// Container mount test for onboarding step 01 — "Tell us about your company and
// product".
//
// FOUR FIELDS SINCE 2026-09-03: company name*, company website, product name,
// product website. Mission, strategy / OKRs, portfolio and planning cycle came
// off this page and are edited in Settings; the tests that used to drive them
// here are gone with them, and CompanyProfileSettings covers them now. Nothing
// was migrated — those columns are untouched, so this file only stops asserting
// that this step writes them.
//
// Covers: the four fields render (seeded fill-only from the saved workspace)
// with only the name starred and no trace of the removed ones; an empty name
// blocks Continue; both websites are OPTIONAL — empty ones save fine and skip
// the background analysis; a save with a workspace present splits the two URLs
// across updateWorkspace (`website`) and upsertPrimaryProduct (`website`) and
// kicks the analysis on the COMPANY site; the analysis falls back to the
// product site when only that one is filled; a pre-split company seeds its
// company-website box from the product row; a first-time save creates a
// workspace carrying both URLs with account_type "company"; and — since the
// workspace step was removed — this save also names the company's default
// workspace "Main workspace" (best-effort, and only while it is still
// unnamed).
//
// product-helpers (validateProductWebsite / normalizeProductWebsite) run REAL —
// they're pure and accept an empty website.
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
const saveWorkspaceOwnedFieldsMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  createWorkspace: (...a: unknown[]) => createWorkspaceMock(...a),
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
  upsertPrimaryProduct: (...a: unknown[]) => upsertProductMock(...a),
  saveWorkspaceOwnedFields: (...a: unknown[]) => saveWorkspaceOwnedFieldsMock(...a),
}))
vi.mock("../../../../lib/onboarding/useFormDraft", () => ({
  saveDraft: vi.fn(),
  loadDraft: () => null,
  clearDraft: vi.fn(),
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

const byPlaceholder = (p: string) =>
  document.querySelector(`input[placeholder="${p}"]`) as HTMLInputElement

const nameInput = () => byPlaceholder("Legal or brand name of your organization")
const companySiteInput = () => byPlaceholder("https://yourcompany.com")
const productNameInput = () =>
  byPlaceholder("The product you're onboarding (you can add more later)")
const productSiteInput = () => byPlaceholder("https://yourproduct.com")

function continueBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /^next$/i.test((b.textContent ?? "").trim()),
  ) as HTMLButtonElement
}

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
  saveWorkspaceOwnedFieldsMock.mockResolvedValue(undefined)
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  saveWorkspaceOwnedFieldsMock.mockResolvedValue(undefined)
})

describe("CompanyStep (onboarding step 01 — company + product basics)", () => {
  it("renders exactly the four fields, seeded from the workspace, with only the name starred", () => {
    mount()
    expect(screen.getByText(/Tell us about your/)).not.toBeNull()
    expect(nameInput()).not.toBeNull()
    expect(companySiteInput()).not.toBeNull()
    expect(productNameInput()).not.toBeNull()
    expect(productSiteInput()).not.toBeNull()

    // Seeded fill-only from the workspace: display_name in, no product yet.
    expect(nameInput().value).toBe("Acme")
    expect(companySiteInput().value).toBe("")
    expect(productNameInput().value).toBe("")
    expect(productSiteInput().value).toBe("")

    // Only the company name is required; the other three are explicitly optional.
    const nameField = document.querySelector('[data-field="companyName"]') as HTMLElement
    expect(nameField.querySelector(".req")).not.toBeNull()
    for (const f of ["companyWebsite", "productName", "productWebsite"]) {
      const field = document.querySelector(`[data-field="${f}"]`) as HTMLElement
      expect(field, f).not.toBeNull()
      expect(field.querySelector(".req"), f).toBeNull()
      expect(field.querySelector(".opt"), f).not.toBeNull()
    }
  })

  it("no longer asks for strategy, mission, portfolio or a planning cycle", () => {
    // They moved to Settings (Company Profile / Process & Planning) rather than
    // being dropped — this asserts the STEP stopped asking, which is the whole
    // change. A textarea anywhere on this page means one crept back.
    mount()
    expect(document.querySelector('[data-field="strategy"]')).toBeNull()
    expect(document.querySelector("textarea")).toBeNull()
    expect(screen.queryByText(/Add more/)).toBeNull()
    expect(screen.queryByText(/Planning cycle/i)).toBeNull()
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

  it("EMPTY websites save fine (optional for everyone) — no analysis kicked", async () => {
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 2 }))
    upsertProductMock.mockResolvedValue(makeProduct())
    mount()

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/plan")
    })
    // Only the company's own columns — mission/strategy/portfolio/planning_cycle
    // are no longer this step's business.
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      display_name: "Acme",
      website: null,
      onboarding_step: 2,
    })
    // Product name still falls back to the company name: products.name rejects
    // an empty string.
    expect(upsertProductMock).toHaveBeenCalledWith("ws-1", {
      name: "Acme",
      website: null,
    })
    expect(analysisSpy).not.toHaveBeenCalled()
    expect(createWorkspaceMock).not.toHaveBeenCalled()
    // `makeWorkspace()` carries `team_name: null` — the removed workspace step's
    // sentinel — so this save also names the default workspace for them.
    expect(saveWorkspaceOwnedFieldsMock).toHaveBeenCalledWith(
      "Main workspace",
      { team_scope: expect.stringContaining("You own the whole workflow") },
    )
  })

  it("does NOT rename a workspace someone has already named", async () => {
    // A company past this point has a real name on its default workspace —
    // walking back through the company step and saving again must not stomp it.
    // The mocked `updateWorkspace` echoes it back, matching what the real
    // endpoint does: it returns the CURRENT row, name included.
    updateWorkspaceMock.mockResolvedValue(
      makeWorkspace({ onboarding_step: 2, team_name: "Growth Pod" }),
    )
    upsertProductMock.mockResolvedValue(makeProduct())
    mount(makeWorkspace({ team_name: "Growth Pod" }))

    await act(async () => {
      continueBtn().click()
    })
    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/plan")
    })
    expect(saveWorkspaceOwnedFieldsMock).not.toHaveBeenCalled()
  })

  it("a failure naming the workspace never blocks Continue", async () => {
    // Best-effort: a label on a row that already exists. Failing the whole
    // company step over it would be the wrong trade.
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 2 }))
    upsertProductMock.mockResolvedValue(makeProduct())
    saveWorkspaceOwnedFieldsMock.mockRejectedValue(new Error("network"))
    mount()

    await act(async () => {
      continueBtn().click()
    })
    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/plan")
    })
    expect(saveWorkspaceOwnedFieldsMock).toHaveBeenCalled()
  })

  it("splits the two websites across the company and product rows, and analyses the COMPANY site", async () => {
    // THE POINT OF THE NEW COLUMN. Both fields are on one page now, so a single
    // shared `products.website` would have let whichever saved last win.
    updateWorkspaceMock.mockResolvedValue(
      makeWorkspace({ onboarding_step: 2, website: "https://acme.com" }),
    )
    upsertProductMock.mockResolvedValue(makeProduct({ website: "https://acme.app" }))
    mount()

    fireEvent.change(companySiteInput(), { target: { value: "acme.com" } })
    fireEvent.change(productNameInput(), { target: { value: "Acme Pay" } })
    fireEvent.change(productSiteInput(), { target: { value: "acme.app" } })
    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/plan")
    })
    // Both normalized to https, and landing in DIFFERENT places.
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      display_name: "Acme",
      website: "https://acme.com",
      onboarding_step: 2,
    })
    expect(upsertProductMock).toHaveBeenCalledWith("ws-1", {
      name: "Acme Pay",
      website: "https://acme.app",
    })
    // The sweep researches the ORGANIZATION, so it takes the company site.
    expect(analysisSpy).toHaveBeenCalledWith("https://acme.com", "ws-1")
  })

  it("falls back to the product site for the analysis when only that one is filled", async () => {
    // Nobody who fills in one field loses the prefill they used to get, and this
    // is also the pre-split company's path: their URL lives on the product row.
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 2 }))
    upsertProductMock.mockResolvedValue(makeProduct({ website: "https://acme.app" }))
    mount()

    fireEvent.change(productSiteInput(), { target: { value: "acme.app" } })
    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/plan")
    })
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      display_name: "Acme",
      website: null,
      onboarding_step: 2,
    })
    expect(analysisSpy).toHaveBeenCalledWith("https://acme.app", "ws-1")
  })

  it("seeds the company-website box from the product for a company onboarded before the split", () => {
    // Their site was recorded on `products.website` by the single old field.
    // Showing this box blank would read as having lost it.
    mount(
      makeWorkspace({
        website: null,
        product: makeProduct({ website: "https://legacy.com" }) as never,
      }),
    )
    expect(companySiteInput().value).toBe("https://legacy.com")
    expect(productSiteInput().value).toBe("https://legacy.com")
  })

  it("first-time save (no workspace yet) creates one carrying both URLs", async () => {
    createWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 2 }))
    mount(null)

    fireEvent.change(nameInput(), { target: { value: "Solo Co" } })
    fireEvent.change(companySiteInput(), { target: { value: "solo.com" } })
    fireEvent.change(productSiteInput(), { target: { value: "app.solo.com" } })
    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/plan")
    })
    expect(createWorkspaceMock).toHaveBeenCalledTimes(1)
    const arg = createWorkspaceMock.mock.calls[0][0] as Record<string, unknown>
    expect(arg.companyName).toBe("Solo Co")
    expect(arg.website).toBe("https://solo.com")
    // Untyped product name still falls back to the company name.
    expect(arg.productName).toBe("Solo Co")
    expect(arg.productWebsite).toBe("https://app.solo.com")
    // Sign-up always writes account_type "company" since v6.
    expect(arg.accountType).toBe("company")
    expect(arg.userId).toBe("u-1")
    // The resume marker points at the step AFTER this one — the import step,
    // whose prompt is filled with the name just entered.
    expect(arg.onboardingStep).toBe(2)
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
  })

  it("shows the loading shell while the workspace is loading", () => {
    authMock.mockReturnValue({ kind: "loading" })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(CompanyStep))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })
})

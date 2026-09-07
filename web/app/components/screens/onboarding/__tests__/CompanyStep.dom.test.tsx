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
// ALL FOUR FIELDS ARE REQUIRED as of 2026-09-07 (only the name was, before),
// and the explanatory hint under Company website is gone. Tests whose subject
// is what happens after a successful save go through `fillAll`.
//
// CONTINUE GOES TO THE NEXT STEP, not to payment. Until 2026-09-07 this step
// pushed straight to `/onboarding/plan` — the payment gate sat between creating
// the company row and the rest of the flow. Payment is the LAST step now, so
// this one hands on like any other.
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

/** Fill every field. ALL FOUR ARE REQUIRED since 2026-09-07, so any test whose
 *  subject is what happens AFTER a successful save has to get past the
 *  validators first — the blocking behaviour has its own tests below. */
function fillAll({
  name = "Acme",
  companySite = "acme.com",
  productName = "Acme Pay",
  productSite = "acme.app",
} = {}) {
  fireEvent.change(nameInput(), { target: { value: name } })
  fireEvent.change(companySiteInput(), { target: { value: companySite } })
  fireEvent.change(productNameInput(), { target: { value: productName } })
  fireEvent.change(productSiteInput(), { target: { value: productSite } })
}

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
  it("renders exactly the four fields, seeded from the workspace, all four starred", () => {
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

    // ALL FOUR REQUIRED (2026-09-07). Three of them used to be optional; the
    // company site in particular is what the background analysis reads to draft
    // the business context, so a signup that skipped it reached the review step
    // with nothing to review.
    for (const f of ["companyName", "companyWebsite", "productName", "productWebsite"]) {
      const field = document.querySelector(`[data-field="${f}"]`) as HTMLElement
      expect(field, f).not.toBeNull()
      expect(field.querySelector(".req"), f).not.toBeNull()
      expect(field.querySelector(".opt"), f).toBeNull()
    }
  })

  it("does not explain the website field, it just asks for it", () => {
    // The hint under Company website ("We'll read this in the background to
    // draft your business context…") came off on 2026-09-07. It described our
    // plumbing on the first screen after signup, where nobody has context for
    // what a "business context" or "the prompt on the next step" is yet.
    mount()
    expect(screen.queryByText(/read this in the background/i)).toBeNull()
    expect(screen.queryByText(/business context/i)).toBeNull()
    expect(document.querySelector(".onb-field-hint")).toBeNull()
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

  it("EMPTY websites and product name now BLOCK Continue, each with its own error", async () => {
    // The reverse of what this step did until 2026-09-07, when the three
    // non-name fields were optional and an empty save was a supported finish.
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 2 }))
    upsertProductMock.mockResolvedValue(makeProduct())
    mount()

    await act(async () => {
      continueBtn().click()
    })

    // The name is seeded from the workspace, so the other three are what fail.
    expect(screen.getByText("Enter your company website.")).not.toBeNull()
    expect(screen.getByText("Enter your product name.")).not.toBeNull()
    expect(screen.getByText("Enter your product website.")).not.toBeNull()
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(upsertProductMock).not.toHaveBeenCalled()
    expect(createWorkspaceMock).not.toHaveBeenCalled()
    expect(analysisSpy).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("a filled form saves the company\'s own columns and names the default workspace", async () => {
    // What the old "empty websites save fine" test covered on the far side of
    // the save: the columns this step writes (mission/strategy/portfolio/
    // planning_cycle are no longer its business) and the workspace naming.
    updateWorkspaceMock.mockResolvedValue(
      makeWorkspace({ onboarding_step: 2, website: "https://acme.com" }),
    )
    upsertProductMock.mockResolvedValue(makeProduct({ website: "https://acme.app" }))
    mount()

    fillAll()
    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
    })
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      display_name: "Acme",
      website: "https://acme.com",
      onboarding_step: 2,
    })
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

    fillAll()
    await act(async () => {
      continueBtn().click()
    })
    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
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

    fillAll()
    await act(async () => {
      continueBtn().click()
    })
    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
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
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
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

  it("falls back to the product site for the analysis when the saved row has no company website", async () => {
    // The pre-split company's path: their URL lives on the product row, so the
    // company row comes back with a null `website` even though the (now
    // required) field was filled. The fallback is what stops them losing the
    // prefill they used to get.
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 2 }))
    upsertProductMock.mockResolvedValue(makeProduct({ website: "https://acme.app" }))
    mount()

    fillAll()
    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
    })
    // The field was filled and written, as it must be — what this covers is the
    // ROW coming back without it, which is the pre-split company's shape.
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      display_name: "Acme",
      website: "https://acme.com",
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

    fillAll({
      name: "Solo Co",
      companySite: "solo.com",
      productName: "Solo Pay",
      productSite: "app.solo.com",
    })
    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
    })
    expect(createWorkspaceMock).toHaveBeenCalledTimes(1)
    const arg = createWorkspaceMock.mock.calls[0][0] as Record<string, unknown>
    expect(arg.companyName).toBe("Solo Co")
    expect(arg.website).toBe("https://solo.com")
    expect(arg.productName).toBe("Solo Pay")
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

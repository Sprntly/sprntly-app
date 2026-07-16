// @vitest-environment jsdom
//
// Container mount test for onboarding step 02 — "Your product" (registration
// spec 2026-07, Product section). Covers: the product name/URL fields and the
// 4 surface chips (Web / Mobile / API / Hardware) render; COMPANY accounts are
// blocked with no surfaces picked (error, no persistence, no navigation);
// picking a surface + URL persists via upsertPrimaryProduct (with surfaces),
// advances to step 3 and routes to /onboarding/metrics; PERSONAL accounts
// skip URL + surfaces freely and record the skipped fields.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const advanceStepMock = vi.fn()
const markSkippedMock = vi.fn()
const upsertProductMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  advanceOnboardingStep: (...a: unknown[]) => advanceStepMock(...a),
  markSkippedFields: (...a: unknown[]) => markSkippedMock(...a),
  upsertPrimaryProduct: (...a: unknown[]) => upsertProductMock(...a),
}))
vi.mock("../../../../lib/onboarding/useFormDraft", () => ({
  saveDraft: vi.fn(),
  loadDraft: () => null,
  clearDraft: vi.fn(),
}))

import { ProductStep } from "../ProductStep"
import { makeWorkspace, makeOnboardingCtx, makeProfile } from "./fixtures"

const SURFACE_LABELS = ["Web", "Mobile", "API", "Hardware"]

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

function mount(accountType: "company" | "personal" = "company") {
  onboardingMock.mockReturnValue(
    makeOnboardingCtx({
      workspace: makeWorkspace({ onboarding_step: 2 }),
      profile: makeProfile({ account_type: accountType }),
    }),
  )
  return render(React.createElement(ProductStep))
}

function urlInput(): HTMLInputElement {
  return document.querySelector(
    'input[placeholder="https://yourproduct.com"]',
  ) as HTMLInputElement
}

function nameInput(): HTMLInputElement {
  return document.querySelector(
    'input[placeholder="The product you\'re onboarding (you can add more later)"]',
  ) as HTMLInputElement
}

function surfaceChip(label: string): HTMLButtonElement {
  return screen.getByText(label).closest("button") as HTMLButtonElement
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

describe("ProductStep (onboarding step 02 — product URL + surfaces)", () => {
  it("renders the product name/URL fields and the 4 surface chips", () => {
    const { container } = mount("company")
    expect(nameInput()).not.toBeNull()
    // Seeded from the workspace: display_name is the natural product name.
    expect(nameInput().value).toBe("Acme")
    expect(urlInput()).not.toBeNull()
    // The 4 surface chips render as toggle buttons, none pre-selected.
    for (const label of SURFACE_LABELS) {
      const chip = surfaceChip(label)
      expect(chip).not.toBeNull()
      expect(chip.classList.contains("metric")).toBe(true)
      expect(chip.getAttribute("aria-pressed")).toBe("false")
    }
    expect(container.querySelectorAll(".metric-chips .metric.sel").length).toBe(0)
  })

  it("COMPANY: Continue with no surfaces shows an error and does NOT persist or navigate", async () => {
    mount("company")
    fireEvent.change(urlInput(), { target: { value: "https://acme.com" } })
    await act(async () => {
      continueBtn().click()
    })
    expect(screen.getByText("Pick at least one surface.")).not.toBeNull()
    expect(upsertProductMock).not.toHaveBeenCalled()
    expect(advanceStepMock).not.toHaveBeenCalled()
    expect(markSkippedMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("COMPANY: a surface + URL persists surfaces via upsertPrimaryProduct, advances to 3 and routes to metrics", async () => {
    upsertProductMock.mockResolvedValue(
      makeProduct({ website: "https://acme.com", surfaces: ["web"] }),
    )
    advanceStepMock.mockResolvedValue(makeWorkspace({ onboarding_step: 3 }))
    mount("company")

    fireEvent.click(surfaceChip("Web"))
    expect(surfaceChip("Web").getAttribute("aria-pressed")).toBe("true")
    fireEvent.change(urlInput(), { target: { value: "https://acme.com" } })

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/metrics")
    })
    expect(upsertProductMock).toHaveBeenCalledWith("ws-1", {
      name: "Acme",
      website: "https://acme.com",
      surfaces: ["web"],
      personas: [],
      monetization: [],
    })
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 3)
    // Nothing was skipped on the mandatory path.
    expect(markSkippedMock).not.toHaveBeenCalled()
  })

  it("PERSONAL: skips URL + surfaces freely and records the skipped fields", async () => {
    upsertProductMock.mockResolvedValue(makeProduct())
    advanceStepMock.mockResolvedValue(makeWorkspace({ onboarding_step: 3 }))
    markSkippedMock.mockResolvedValue(undefined)
    mount("personal")

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/metrics")
    })
    expect(upsertProductMock).toHaveBeenCalledWith("ws-1", {
      name: "Acme",
      website: null,
      surfaces: [],
      personas: [],
      monetization: [],
    })
    expect(markSkippedMock).toHaveBeenCalledWith("u-1", [
      "product_url",
      "product_surfaces",
    ])
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 3)
  })

  it("shows the loading shell while the workspace is loading", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(ProductStep))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(ProductStep))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/company")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})

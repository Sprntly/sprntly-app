// @vitest-environment jsdom
//
// Container mount test for onboarding step 02 — "Your product" (v6 screenshot
// spec 2026-07-17). Covers: the product name/URL fields, the 4 surface chips
// (Web / Mobile app / API / Hardware), the SINGLE monetization select and the
// users textarea render; name + surfaces are required for EVERYONE (error, no
// persistence, no navigation when missing); a valid Continue persists the
// product via upsertPrimaryProduct (surfaces + 0/1-element monetization array
// + usersDescription) and the competitors via updateWorkspace (parsed from the
// comma-separated disclosure field, onboarding_step 3), then routes to
// Plus unit coverage for the exported parseCompetitors helper.
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
const updateWorkspaceMock = vi.fn()
const upsertProductMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  advanceOnboardingStep: (...a: unknown[]) => advanceStepMock(...a),
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
  upsertPrimaryProduct: (...a: unknown[]) => upsertProductMock(...a),
}))
vi.mock("../../../../lib/onboarding/useFormDraft", () => ({
  saveDraft: vi.fn(),
  loadDraft: () => null,
  clearDraft: vi.fn(),
}))

import { ProductStep, parseCompetitors } from "../ProductStep"
import {
  MONETIZATION_OPTIONS,
  ONBOARDING_STEP_COUNT,
} from "../../../../lib/onboarding/types"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

const SURFACE_LABELS = ["Web", "Mobile app", "API", "Hardware"]

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

function mount(workspace = makeWorkspace({ onboarding_step: 2 })) {
  onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace }))
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

function monetizationSelect(): HTMLSelectElement {
  return document.querySelector(
    'select[aria-label="Monetization"]',
  ) as HTMLSelectElement
}

function usersTextarea(): HTMLTextAreaElement {
  return document.querySelector(
    'textarea[placeholder="Your main user or customer types, in your own words"]',
  ) as HTMLTextAreaElement
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

describe("parseCompetitors — comma-separated competitors field", () => {
  it("splits, trims, and drops empty segments", () => {
    expect(parseCompetitors("Fitbit, Oura ,  Garmin")).toEqual([
      "Fitbit",
      "Oura",
      "Garmin",
    ])
    expect(parseCompetitors("  ,, ,")).toEqual([])
    expect(parseCompetitors("")).toEqual([])
  })

  it("dedupes case-insensitively, keeping the first casing", () => {
    expect(parseCompetitors("Fitbit, fitbit, FITBIT, Oura")).toEqual([
      "Fitbit",
      "Oura",
    ])
  })
})

describe("ProductStep (onboarding step 02 — name* + surfaces* + monetization + users)", () => {
  it("renders name/URL, the 4 surface chips, the single monetization select and the users textarea", () => {
    const { container } = mount()
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
    // Monetization is a SINGLE select: placeholder + the full vocabulary.
    const sel = monetizationSelect()
    expect(sel).not.toBeNull()
    const options = Array.from(sel.options)
    expect(options[0].value).toBe("")
    expect(options.slice(1).map((o) => o.value)).toEqual(
      MONETIZATION_OPTIONS.map((o) => o.value),
    )
    expect(sel.value).toBe("")
    // The users prose textarea is visible; competitors sit behind a disclosure.
    expect(usersTextarea()).not.toBeNull()
    expect(screen.getByText(/Add competitors/)).not.toBeNull()
  })

  it("Continue with no surfaces shows an error and does NOT persist or navigate (required for everyone)", async () => {
    mount()
    fireEvent.change(urlInput(), { target: { value: "https://acme.com" } })
    await act(async () => {
      continueBtn().click()
    })
    expect(screen.getByText("Pick at least one surface.")).not.toBeNull()
    expect(upsertProductMock).not.toHaveBeenCalled()
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(advanceStepMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("Continue with an empty product name shows an error and does NOT persist", async () => {
    mount()
    fireEvent.change(nameInput(), { target: { value: "" } })
    fireEvent.click(surfaceChip("Web"))
    await act(async () => {
      continueBtn().click()
    })
    expect(screen.getByText("Enter your product name.")).not.toBeNull()
    expect(upsertProductMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("a valid Continue persists the product + competitors and routes to metrics", async () => {
    upsertProductMock.mockResolvedValue(
      makeProduct({
        website: "https://acme.com",
        surfaces: ["web"],
        monetization: ["subscription"],
      }),
    )
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 3 }))
    mount()

    fireEvent.click(surfaceChip("Web"))
    expect(surfaceChip("Web").getAttribute("aria-pressed")).toBe("true")
    fireEvent.change(urlInput(), { target: { value: "https://acme.com" } })
    fireEvent.change(monetizationSelect(), { target: { value: "subscription" } })
    fireEvent.change(usersTextarea(), {
      target: { value: "Ops leads at SMB fintechs" },
    })
    // Competitors live behind the disclosure, comma-separated.
    fireEvent.click(screen.getByText(/Add competitors/))
    fireEvent.change(
      document.querySelector('[data-field="competitors"] textarea') as HTMLTextAreaElement,
      { target: { value: "Fitbit, Oura, fitbit" } },
    )

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/metrics")
    })
    // The single monetization pick is stored as a 1-element array.
    expect(upsertProductMock).toHaveBeenCalledWith("ws-1", {
      name: "Acme",
      website: "https://acme.com",
      surfaces: ["web"],
      monetization: ["subscription"],
      usersDescription: "Ops leads at SMB fintechs",
    })
    // Competitors are parsed/deduped onto the company row with the step bump.
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      competitors: ["Fitbit", "Oura"],
      onboarding_step: 3,
    })
    expect(advanceStepMock).not.toHaveBeenCalled()
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

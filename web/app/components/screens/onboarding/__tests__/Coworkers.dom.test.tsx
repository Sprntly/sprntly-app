// @vitest-environment jsdom
//
// Container-level mount + behavior tests for the onboarding coworkers step —
// "Introducing your AI coworker." (design-v4 page 07 on OnboardingChrome).
// Mounts the real container under jsdom with mocked onboarding/router and the
// coworkers network client, covering: rendering ONLY the visible Product
// coworker (pd / ds / admin hidden), GET prefill, the live handle pill, launch
// validation (only Product must be named), the PUT → advance(5) →
// /onboarding/first-brief happy path — the PUT still sends defaults for the
// hidden slots so the backend contract stays valid — the PUT-failure path
// (error shown, no advance), and Back → connectors.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const advanceMock = vi.fn()
const getMock = vi.fn()
const putMock = vi.fn()

vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  advanceOnboardingStep: (...args: unknown[]) => advanceMock(...args),
}))
// Keep the pure helpers (VISIBLE_COWORKERS, emptyCoworkerNames,
// canLaunchWorkspace, coworkerHandle, withCoworkerDefaults) real; only stub the
// network client so the mount is offline.
vi.mock("../../../../lib/onboarding/coworkersApi", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../../../lib/onboarding/coworkersApi")
  >()
  return {
    ...actual,
    coworkersApi: {
      get: (...args: unknown[]) => getMock(...args),
      put: (...args: unknown[]) => putMock(...args),
    },
  }
})

import { Coworkers } from "../Coworkers"
import {
  VISIBLE_COWORKERS,
  withCoworkerDefaults,
} from "../../../../lib/onboarding/coworkersApi"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

afterEach(() => {
  cleanup()
  // resetAllMocks (not clearAllMocks): getMock/putMock implementations set
  // via mockResolvedValue must not leak across tests.
  vi.resetAllMocks()
})

// Mounts the loaded container and lets the GET-prefill effect settle inside
// act() so it can't clobber names typed later in the test.
async function mountLoaded(
  ctxOver: Record<string, unknown> = {},
  prefill: Record<string, string> = {},
) {
  getMock.mockResolvedValue(prefill)
  onboardingMock.mockReturnValue(
    makeOnboardingCtx({
      workspace: makeWorkspace({ onboarding_step: 4 }),
      ...ctxOver,
    }),
  )
  let utils!: ReturnType<typeof render>
  await act(async () => {
    utils = render(React.createElement(Coworkers))
  })
  return utils
}

function nameInput(label: string): HTMLInputElement {
  return screen.getByLabelText(`Name for ${label}`) as HTMLInputElement
}

// Only the Product coworker is surfaced now; naming it is all that launch
// requires.
function nameAll() {
  fireEvent.change(nameInput("Product coworker"), { target: { value: "Maya" } })
}

function launchButton(): HTMLButtonElement {
  const btn = Array.from(document.querySelectorAll("button")).find((b) =>
    /Launch workspace/.test(b.textContent ?? ""),
  )
  expect(btn).toBeTruthy()
  return btn as HTMLButtonElement
}

describe("Coworkers (container) — coworkers", () => {
  it("renders the v4 chrome with ONLY the visible Product coworker", async () => {
    const { container } = await mountLoaded()

    const heading = container.querySelector(".onb-h")
    expect(heading?.textContent).toBe(
      "Introducing your AI coworker. Give it a name.",
    )
    // OnboardingChrome shell, coworkers = numbered step 4.
    expect(container.querySelector(".onb-shell")).not.toBeNull()
    expect(
      container.querySelector(".onb-dots")?.getAttribute("data-step"),
    ).toBe("4")

    // Exactly one row — the Product coworker — renders.
    const rows = container.querySelectorAll(".cowork-list .cowork")
    expect(rows.length).toBe(VISIBLE_COWORKERS.length)
    expect(rows.length).toBe(1)
    for (const c of VISIBLE_COWORKERS) {
      expect(screen.getByText(c.label)).not.toBeNull()
      expect(screen.getByText(c.blurb)).not.toBeNull()
      expect(nameInput(c.label)).not.toBeNull()
      expect(container.querySelector(`.cowork-av.${c.color}`)).not.toBeNull()
    }

    // The hidden coworkers do NOT render anywhere in the teammate section.
    expect(screen.queryByText("Design coworker")).toBeNull()
    expect(screen.queryByText("Data Science coworker")).toBeNull()
    expect(screen.queryByText("Admin coworker")).toBeNull()
    expect(container.querySelector('[data-field="pd"]')).toBeNull()
    expect(container.querySelector('[data-field="ds"]')).toBeNull()
    expect(container.querySelector('[data-field="admin"]')).toBeNull()

    // Old InterviewLayout shell is gone.
    expect(container.querySelector(".interview-shell")).toBeNull()
  })

  it("prefills the Product name from coworkersApi.get() on mount", async () => {
    // GET still returns all four slots; only Product surfaces in the UI.
    await mountLoaded({}, { pm: "Maya", ds: "Vera" })

    expect(getMock).toHaveBeenCalledTimes(1)
    expect(nameInput("Product coworker").value).toBe("Maya")
    // Hidden slots have no rendered input.
    expect(screen.queryByLabelText("Name for Data Science coworker")).toBeNull()
    expect(screen.queryByLabelText("Name for Design coworker")).toBeNull()
  })

  it("previews the live handle pill as the user types (Maya → maya_pm)", async () => {
    const { container } = await mountLoaded()

    const pills = () =>
      Array.from(container.querySelectorAll(".cowork-handle")).map(
        (p) => p.textContent,
      )
    // Only the Product pill renders; untouched it reads like the placeholder.
    expect(pills()).toEqual(["name_pm"])

    // Lowercased + sanitized (spaces and punctuation stripped).
    fireEvent.change(nameInput("Product coworker"), {
      target: { value: "  Jo Ann! " },
    })
    expect(pills()[0]).toBe("joann_pm")
  })

  it("blocks launch with a Product-row error until it is named", async () => {
    const { container } = await mountLoaded()

    // Nothing typed yet: 0 of 1 named, launch disabled.
    expect(screen.getByText(/0 of 1 named/) ?? null).not.toBeNull()
    expect(screen.getByText(/name your coworker to launch/)).not.toBeNull()

    await act(async () => {
      fireEvent.click(launchButton())
    })

    // Nothing persisted, no step advance, no navigation…
    expect(putMock).not.toHaveBeenCalled()
    expect(advanceMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
    // …and only the (single) Product row shows an error.
    expect(container.querySelectorAll(".onb-field-error").length).toBe(1)
    expect(
      container.querySelector('[data-field="pm"] .onb-field-error'),
    ).not.toBeNull()
  })

  it("launches with only Product named — PUT fills hidden-slot defaults, advances to step 5, routes to first-brief", async () => {
    putMock.mockResolvedValue({ ok: true })
    const updated = makeWorkspace({ onboarding_step: 5 })
    advanceMock.mockResolvedValue(updated)
    const setWorkspace = vi.fn()
    await mountLoaded({ setWorkspace })

    nameAll() // names Product only
    expect(screen.getByText(/1 of 1 named/)).not.toBeNull()
    expect(screen.getByText(/ready to launch/)).not.toBeNull()

    await act(async () => {
      fireEvent.click(launchButton())
    })

    // The PUT still carries all four slots so the backend contract stays
    // valid: Product = the typed name, the hidden slots = their defaults.
    const expected = withCoworkerDefaults({
      pm: "Maya",
      pd: "",
      ds: "",
      admin: "",
    })
    expect(putMock).toHaveBeenCalledWith(expected)
    expect(expected.pm).toBe("Maya")
    expect(expected.pd.length).toBeGreaterThan(0)
    expect(expected.ds.length).toBeGreaterThan(0)
    expect(expected.admin.length).toBeGreaterThan(0)

    expect(advanceMock).toHaveBeenCalledWith("ws-1", 5)
    expect(setWorkspace).toHaveBeenCalledWith(updated)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/first-brief")
  })

  it("shows the PUT error and does not advance when persisting fails", async () => {
    putMock.mockRejectedValue(new Error("names did not save"))
    const { container } = await mountLoaded()

    nameAll()
    await act(async () => {
      fireEvent.click(launchButton())
    })

    expect(container.querySelector(".onb-form-error")?.textContent).toBe(
      "names did not save",
    )
    expect(advanceMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("Back routes to the connectors step", async () => {
    await mountLoaded()
    const back = Array.from(document.querySelectorAll("button")).find((b) =>
      /Back/.test(b.textContent ?? ""),
    )
    expect(back).toBeTruthy()
    fireEvent.click(back as HTMLButtonElement)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("shows the loading shell while the workspace is loading", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(Coworkers))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(Coworkers))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/business-info")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})

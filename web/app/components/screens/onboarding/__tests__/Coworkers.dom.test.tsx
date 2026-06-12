// @vitest-environment jsdom
//
// Container-level mount + behavior tests for the onboarding coworkers step —
// "Introducing your AI coworkers." (design-v4 page 07 on OnboardingChrome).
// Mounts the real container under jsdom with mocked onboarding/router and the
// coworkers network client, covering: slot rendering from the COWORKERS
// catalog, GET prefill, the live handle pill, launch validation (all four
// must be named), the PUT → advance(5) → /onboarding/first-brief happy path,
// the PUT-failure path (error shown, no advance), and Back → connectors.
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
// Keep the pure helpers (COWORKERS, emptyCoworkerNames, canLaunchWorkspace,
// coworkerHandle, withCoworkerDefaults) real; only stub the network client so
// the mount is offline.
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
  COWORKERS,
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

function nameAll() {
  fireEvent.change(nameInput("Product coworker"), { target: { value: "Maya" } })
  fireEvent.change(nameInput("Design coworker"), { target: { value: "Juno" } })
  fireEvent.change(nameInput("Data Science coworker"), {
    target: { value: "Vera" },
  })
  fireEvent.change(nameInput("Admin coworker"), { target: { value: "Ada" } })
}

function launchButton(): HTMLButtonElement {
  const btn = Array.from(document.querySelectorAll("button")).find((b) =>
    /Launch workspace/.test(b.textContent ?? ""),
  )
  expect(btn).toBeTruthy()
  return btn as HTMLButtonElement
}

describe("Coworkers (container) — coworkers", () => {
  it("renders the v4 chrome with every slot from the COWORKERS catalog", async () => {
    const { container } = await mountLoaded()

    const heading = container.querySelector(".onb-h")
    expect(heading?.textContent).toBe(
      "Introducing your AI coworkers. Give them a name.",
    )
    // OnboardingChrome shell, coworkers = numbered step 4.
    expect(container.querySelector(".onb-shell")).not.toBeNull()
    expect(
      container.querySelector(".onb-dots")?.getAttribute("data-step"),
    ).toBe("4")

    const rows = container.querySelectorAll(".cowork-list .cowork")
    expect(rows.length).toBe(COWORKERS.length)
    for (const c of COWORKERS) {
      expect(screen.getByText(c.label)).not.toBeNull()
      expect(screen.getByText(c.blurb)).not.toBeNull()
      expect(nameInput(c.label)).not.toBeNull()
    }
    // Avatar tile per row, with the slot's color variant.
    for (const c of COWORKERS) {
      expect(container.querySelector(`.cowork-av.${c.color}`)).not.toBeNull()
    }
    // Old InterviewLayout shell is gone.
    expect(container.querySelector(".interview-shell")).toBeNull()
  })

  it("prefills names from coworkersApi.get() on mount", async () => {
    await mountLoaded({}, { pm: "Maya", ds: "Vera" })

    expect(getMock).toHaveBeenCalledTimes(1)
    expect(nameInput("Product coworker").value).toBe("Maya")
    expect(nameInput("Data Science coworker").value).toBe("Vera")
    expect(nameInput("Design coworker").value).toBe("")
  })

  it("previews the live handle pill as the user types (Maya → maya_pm)", async () => {
    const { container } = await mountLoaded()

    const pills = () =>
      Array.from(container.querySelectorAll(".cowork-handle")).map(
        (p) => p.textContent,
      )
    // Untouched pills read like the mock placeholders.
    expect(pills()).toEqual(["name_pm", "name_pd", "name_ds", "name_admin"])

    fireEvent.change(nameInput("Product coworker"), { target: { value: "Maya" } })
    expect(pills()[0]).toBe("maya_pm")

    // Lowercased + sanitized (spaces and punctuation stripped).
    fireEvent.change(nameInput("Design coworker"), {
      target: { value: "  Jo Ann! " },
    })
    expect(pills()[1]).toBe("joann_pd")
  })

  it("blocks launch with per-row errors until every coworker is named", async () => {
    const { container } = await mountLoaded()

    fireEvent.change(nameInput("Product coworker"), { target: { value: "Maya" } })
    expect(
      screen.getByText(/1 of 4 named/) ?? null,
    ).not.toBeNull()
    expect(screen.getByText(/name each coworker to launch/)).not.toBeNull()

    await act(async () => {
      fireEvent.click(launchButton())
    })

    // Nothing persisted, no step advance, no navigation…
    expect(putMock).not.toHaveBeenCalled()
    expect(advanceMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
    // …and the three unnamed rows show an error.
    expect(container.querySelectorAll(".onb-field-error").length).toBe(3)
    expect(
      container.querySelector('[data-field="pd"] .onb-field-error'),
    ).not.toBeNull()
    expect(
      container.querySelector('[data-field="pm"] .onb-field-error'),
    ).toBeNull()
  })

  it("launch PUTs the names, advances to step 5, and routes to first-brief", async () => {
    putMock.mockResolvedValue({ ok: true })
    const updated = makeWorkspace({ onboarding_step: 5 })
    advanceMock.mockResolvedValue(updated)
    const setWorkspace = vi.fn()
    await mountLoaded({ setWorkspace })

    nameAll()
    expect(screen.getByText(/4 of 4 named/)).not.toBeNull()
    expect(screen.getByText(/ready to launch/)).not.toBeNull()

    await act(async () => {
      fireEvent.click(launchButton())
    })

    expect(putMock).toHaveBeenCalledWith(
      withCoworkerDefaults({ pm: "Maya", pd: "Juno", ds: "Vera", admin: "Ada" }),
    )
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

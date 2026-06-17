// @vitest-environment jsdom
//
// Container-level mount + behavior tests for the pre-onboarding profile gate —
// "What should we call you?" (the unnumbered /onboarding/your-name route).
// Mounts the real component under jsdom with mocked auth / workspace / router
// and a stubbed updateUserProfile, covering: rendering the first/last name
// inputs; prefilling from auth.user.user_metadata (first_name/last_name first,
// then Google's given_name/family_name); submit with a first name →
// updateUserProfile called with the right args → workspace refresh → navigate
// to /onboarding/business-info; and empty-first-name blocks submit.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const refreshMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const updateProfileMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({
  useAuth: () => authMock(),
}))
vi.mock("../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ refresh: refreshMock }),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  updateUserProfile: (...args: unknown[]) => updateProfileMock(...args),
}))

import { YourName } from "../YourName"

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

function authedWith(meta: Record<string, unknown>) {
  authMock.mockReturnValue({
    kind: "authed",
    user: { id: "user-1", email: "u@example.com", user_metadata: meta },
  })
}

function firstInput(): HTMLInputElement {
  return screen.getByLabelText("First name") as HTMLInputElement
}
function lastInput(): HTMLInputElement {
  return screen.getByLabelText("Last name") as HTMLInputElement
}
function continueButton(): HTMLButtonElement {
  const btn = Array.from(document.querySelectorAll("button")).find((b) =>
    /Continue|Saving/.test(b.textContent ?? ""),
  )
  expect(btn).toBeTruthy()
  return btn as HTMLButtonElement
}

describe("YourName (pre-onboarding profile gate)", () => {
  it("renders the unnumbered chrome with first + last name inputs and no progress dots", () => {
    authedWith({})
    const { container } = render(React.createElement(YourName))

    expect(container.querySelector(".onb-shell")).not.toBeNull()
    expect(container.querySelector(".onb-h")?.textContent).toBe(
      "What should we call you?",
    )
    expect(firstInput()).not.toBeNull()
    expect(lastInput()).not.toBeNull()
    // Deliberately NOT a numbered step → no progress dots.
    expect(container.querySelector(".onb-dots")).toBeNull()
  })

  it("prefills from explicit first_name/last_name metadata", () => {
    authedWith({ first_name: "Ada", last_name: "Lovelace" })
    render(React.createElement(YourName))
    expect(firstInput().value).toBe("Ada")
    expect(lastInput().value).toBe("Lovelace")
  })

  it("prefills from Google's given_name/family_name when first/last are absent", () => {
    authedWith({ given_name: "Grace", family_name: "Hopper" })
    render(React.createElement(YourName))
    expect(firstInput().value).toBe("Grace")
    expect(lastInput().value).toBe("Hopper")
  })

  it("splits a single display name when no structured names exist", () => {
    authedWith({ name: "Alan Mathison Turing" })
    render(React.createElement(YourName))
    expect(firstInput().value).toBe("Alan")
    expect(lastInput().value).toBe("Mathison Turing")
  })

  it("submitting with a first name saves the profile, refreshes, and navigates to business-info", async () => {
    authedWith({ given_name: "Grace", family_name: "Hopper" })
    updateProfileMock.mockResolvedValue({})
    refreshMock.mockResolvedValue(undefined)

    render(React.createElement(YourName))

    await act(async () => {
      fireEvent.click(continueButton())
    })

    expect(updateProfileMock).toHaveBeenCalledWith("user-1", {
      first_name: "Grace",
      last_name: "Hopper",
      role: null,
    })
    expect(refreshMock).toHaveBeenCalledTimes(1)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/business-info")
  })

  it("includes a selected role in the saved profile", async () => {
    authedWith({ first_name: "Ada" })
    updateProfileMock.mockResolvedValue({})
    refreshMock.mockResolvedValue(undefined)

    render(React.createElement(YourName))
    fireEvent.change(screen.getByLabelText("Your role"), {
      target: { value: "PM" },
    })

    await act(async () => {
      fireEvent.click(continueButton())
    })

    expect(updateProfileMock).toHaveBeenCalledWith("user-1", {
      first_name: "Ada",
      last_name: "",
      role: "PM",
    })
  })

  it("blocks submit with an empty first name — no save, no navigation, error shown", async () => {
    authedWith({})
    const { container } = render(React.createElement(YourName))

    expect(firstInput().value).toBe("")
    await act(async () => {
      fireEvent.click(continueButton())
    })

    expect(updateProfileMock).not.toHaveBeenCalled()
    expect(refreshMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
    expect(container.querySelector(".onb-form-error")?.textContent).toBe(
      "Enter your first name.",
    )
  })
})

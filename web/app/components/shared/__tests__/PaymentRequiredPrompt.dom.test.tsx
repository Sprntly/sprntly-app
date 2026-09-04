// @vitest-environment jsdom
//
// What a 402 looks like to a person.
//
// The backend has always returned a structured, actionable body — the reason,
// the message, and for a credit shortfall the numbers. Nothing on the client
// read it, so a customer whose subscription had lapsed clicked Generate and got
// whatever generic failure that surface happened to render, with no mention of
// billing anywhere. These pin the two reasons apart, because they need
// different words and different actions.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const push = vi.fn()
const router = { push, replace: vi.fn() }
vi.mock("next/navigation", () => ({ useRouter: () => router }))

import { PaymentRequiredPrompt } from "../PaymentRequiredPrompt"

/** What `api.ts` dispatches when a request comes back 402. */
function fire(detail: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new CustomEvent("sprntly:payment-required", { detail }))
  })
}

beforeEach(() => push.mockClear())
afterEach(() => cleanup())

describe("nothing is shown until a 402 actually happens", () => {
  it("renders nothing at rest", () => {
    const { container } = render(<PaymentRequiredPrompt />)
    expect(container.firstChild).toBeNull()
  })
})

describe("a lapsed subscription", () => {
  const lapsed = {
    reason: "subscription_inactive",
    message: "Your subscription is not active. Update billing to continue.",
  }

  it("names the problem instead of failing generically", () => {
    render(<PaymentRequiredPrompt />)
    fire(lapsed)
    expect(screen.getByText("Your subscription has ended")).toBeTruthy()
    expect(screen.getByText(/not active/i)).toBeTruthy()
  })

  it("offers a plan, not a top-up — a top-up would not fix it", () => {
    render(<PaymentRequiredPrompt />)
    fire(lapsed)
    expect(screen.getByTestId("pay-req-cta").textContent).toBe("Choose a plan")
  })

  it("takes them to billing", () => {
    render(<PaymentRequiredPrompt />)
    fire(lapsed)
    fireEvent.click(screen.getByTestId("pay-req-cta"))
    expect(push).toHaveBeenCalledWith("/settings?section=billing")
  })
})

describe("a credit shortfall", () => {
  const broke = {
    reason: "insufficient_credits",
    message: "This needs 25 credits and you have 8.",
    needed: 25,
    balance: 8,
    feature: "prd",
  }

  it("offers a top-up rather than a plan — the subscription is fine", () => {
    render(<PaymentRequiredPrompt />)
    fire(broke)
    expect(screen.getByText("You're out of credits")).toBeTruthy()
    expect(screen.getByTestId("pay-req-cta").textContent).toBe("Buy more credits")
  })

  it("shows the actual numbers, which is the whole reason they are sent", () => {
    render(<PaymentRequiredPrompt />)
    fire(broke)
    const nums = screen.getByText(/This costs/i)
    expect(nums.textContent).toContain("25")
    expect(nums.textContent).toContain("8")
  })

  it("does not show the numbers line for a lapse, where they mean nothing", () => {
    render(<PaymentRequiredPrompt />)
    fire({ reason: "subscription_inactive", message: "gone" })
    expect(screen.queryByText(/This costs/i)).toBeNull()
  })
})

describe("it is never a trap", () => {
  it("can be dismissed", () => {
    const { container } = render(<PaymentRequiredPrompt />)
    fire({ reason: "subscription_inactive", message: "gone" })
    fireEvent.click(screen.getByTestId("pay-req-dismiss"))
    expect(container.firstChild).toBeNull()
    expect(push).not.toHaveBeenCalled()
  })

  it("survives a body it does not recognise", () => {
    // A 402 from somewhere that did not send our envelope must still say
    // something rather than render "undefined".
    render(<PaymentRequiredPrompt />)
    fire({ reason: "", message: "Your subscription is not active. Choose a plan to keep generating." })
    expect(screen.getByRole("dialog")).toBeTruthy()
    expect(screen.queryByText("undefined")).toBeNull()
  })
})

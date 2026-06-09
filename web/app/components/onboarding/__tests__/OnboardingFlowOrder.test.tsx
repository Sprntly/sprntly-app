// @vitest-environment jsdom
//
// Integrity tests for the restructured page-05 onboarding flow:
//   Company (1) → [analyzing] → Metrics (2) → Optimizing (3) → Business
//   context (4) → Connectors (5) → Coworkers (6) → First brief (7).
//
// Asserts the route→screen remap renders the right component per numbered step
// (no gaps), that the progress chrome renders exactly ONBOARDING_STEP_COUNT
// dots with the active one matching the step, and that the loader is not a
// counted step.
import * as React from "react"
import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// Stub each screen with a marker so we can assert which one a route renders,
// without dragging in their hooks/contexts.
vi.mock("../../screens/onboarding", () => ({
  Onboarding1: () => React.createElement("div", { "data-screen": "company" }),
  Onboarding2: () => React.createElement("div", { "data-screen": "optimizing" }),
  Onboarding3: () => React.createElement("div", { "data-screen": "business-context" }),
  Onboarding4: () => React.createElement("div", { "data-screen": "metrics" }),
  Onboarding5: () => React.createElement("div", { "data-screen": "connectors" }),
  Onboarding6: () => React.createElement("div", { "data-screen": "coworkers" }),
  Onboarding7: () => React.createElement("div", { "data-screen": "first-brief" }),
}))

import { OnboardingStep } from "../../../(app)/onboarding/[step]/OnboardingStep"
import { OnboardingChrome } from "../OnboardingChrome"
import { ONBOARDING_STEP_COUNT } from "../../../lib/onboarding/types"

afterEach(cleanup)

const EXPECTED_ORDER: Record<string, string> = {
  "1": "company",
  "2": "metrics",
  "3": "optimizing",
  "4": "business-context",
  "5": "connectors",
  "6": "coworkers",
  "7": "first-brief",
}

describe("onboarding flow order — route → screen", () => {
  it("renders the metrics page at route 2 (moved up, right after the loader)", () => {
    const { container } = render(React.createElement(OnboardingStep, { step: "2" }))
    expect(container.querySelector('[data-screen="metrics"]')).not.toBeNull()
  })

  it("maps every numbered step 1..7 to the expected screen with no gaps", () => {
    for (let n = 1; n <= ONBOARDING_STEP_COUNT; n++) {
      const { container, unmount } = render(
        React.createElement(OnboardingStep, { step: String(n) }),
      )
      const el = container.querySelector("[data-screen]") as HTMLElement
      expect(el).not.toBeNull()
      expect(el.getAttribute("data-screen")).toBe(EXPECTED_ORDER[String(n)])
      unmount()
    }
  })
})

describe("OnboardingChrome — progress dots", () => {
  it("renders exactly ONBOARDING_STEP_COUNT dots", () => {
    const { container } = render(
      React.createElement(OnboardingChrome, {
        step: 1,
        title: "T",
        children: null,
      }),
    )
    expect(container.querySelectorAll(".onb-dots .od").length).toBe(
      ONBOARDING_STEP_COUNT,
    )
  })

  it("marks the current step active and prior steps done", () => {
    const { container } = render(
      React.createElement(OnboardingChrome, {
        step: 2,
        title: "T",
        children: null,
      }),
    )
    const dots = Array.from(container.querySelectorAll(".onb-dots .od"))
    expect(dots[0].className).toContain("done")
    expect(dots[1].className).toContain("cur")
    expect(dots[2].className).not.toContain("cur")
  })

  it("renders Back/Continue only when handlers are provided (back-next integrity)", () => {
    const onBack = vi.fn()
    const onContinue = vi.fn()
    const { container } = render(
      React.createElement(OnboardingChrome, {
        step: 2,
        title: "T",
        children: null,
        onBack,
        onContinue,
      }),
    )
    const labels = Array.from(container.querySelectorAll("button")).map((b) =>
      (b.textContent ?? "").trim(),
    )
    expect(labels.some((l) => /Back/.test(l))).toBe(true)
    expect(labels.some((l) => /Continue/.test(l))).toBe(true)
  })

  it("omits Back when no onBack is given (e.g. the first step)", () => {
    const { container } = render(
      React.createElement(OnboardingChrome, {
        step: 1,
        title: "T",
        children: null,
        onContinue: vi.fn(),
      }),
    )
    const labels = Array.from(container.querySelectorAll("button")).map((b) =>
      (b.textContent ?? "").trim(),
    )
    expect(labels.some((l) => /^Back$/.test(l))).toBe(false)
  })
})

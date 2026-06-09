// Node-env SSR render assertion (no jsdom) — same View pattern as the
// connector/PRD component tests. InterviewLayout is purely presentational
// (props only, no hooks), so it renders to static markup directly. It is
// the shared v4 onboarding shell for pages 05/06/07.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { InterviewLayout, useFieldValidation } from "../InterviewLayout"
import type { FieldCheck } from "../../../lib/onboarding/validation"

const noop = () => {}

function render(override: Partial<React.ComponentProps<typeof InterviewLayout>> = {}): string {
  const defaults: React.ComponentProps<typeof InterviewLayout> = {
    step: 7,
    eyebrow: "Saved",
    title: "Introducing your AI coworkers. Give them a name.",
    agentMessage: "Three specialists plus an Admin join your workspace.",
    children: React.createElement("div", null, "form body"),
    rightPane: React.createElement("div", null, "preview"),
  }
  return renderToStaticMarkup(
    React.createElement(InterviewLayout, { ...defaults, ...override }),
  )
}

describe("InterviewLayout (v4 onboarding shell)", () => {
  it("renders the title, agent message, and right pane", () => {
    const html = render()
    expect(html).toContain("Introducing your AI coworkers")
    expect(html).toContain("Three specialists plus an Admin")
    expect(html).toContain("preview")
  })

  it("shows the step progress label and a 7-dot indicator", () => {
    const html = render({ step: 5 })
    expect(html).toContain("Step 5 of 7")
    // one dot element per step (match the className attribute, not CSS rules)
    expect((html.match(/class="interview-dot/g) ?? []).length).toBe(7)
  })

  it("marks done/active dots relative to the current step", () => {
    const html = render({ step: 6 })
    expect(html).toContain("interview-dot done") // steps before current
    expect(html).toContain("interview-dot  active") // current step
  })

  it("renders a custom continue label (e.g. Launch workspace)", () => {
    const html = render({ onContinue: noop, continueLabel: "Launch workspace" })
    expect(html).toContain("Launch workspace")
  })

  it("disables Continue when continueDisabled is set", () => {
    const html = render({ onContinue: noop, continueDisabled: true })
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Continue<\/button>/)
  })

  it("renders an enabled (non-disabled) primary Continue by default", () => {
    // Regression for the "invisible until hover" bug: the enabled primary
    // button must render without the disabled attribute and carry the
    // btn-primary class (which globals.css styles with a dark, opaque bg).
    const html = render({ onContinue: noop })
    expect(html).toMatch(/<button[^>]*class="btn btn-primary"[^>]*>Continue<\/button>/)
    expect(html).not.toMatch(/<button[^>]*disabled[^>]*>Continue<\/button>/)
  })

  it("renders Back and Skip when their handlers are provided", () => {
    const html = render({ onBack: noop, onSkip: noop, skipLabel: "Connect later" })
    expect(html).toContain(">Back<")
    expect(html).toContain("Connect later")
  })
})

// Harness exercising the useFieldValidation hook through SSR. The hook owns
// the per-field error map + first-invalid focus target; its pure core
// (validateRequired) is covered in onboarding-validation.test.ts. Here we
// confirm the hook starts clean and exposes a containerRef + validate API.
function Harness({ checks }: { checks: FieldCheck[] }) {
  const { errors, validate, clearError, containerRef } = useFieldValidation(
    () => checks,
  )
  return React.createElement(
    "div",
    { ref: containerRef, "data-error-count": Object.keys(errors).length },
    React.createElement("button", { onClick: () => validate() }, "validate"),
    React.createElement("button", { onClick: () => clearError("x") }, "clear"),
  )
}

describe("useFieldValidation", () => {
  it("starts with no errors before validate runs", () => {
    const html = renderToStaticMarkup(
      React.createElement(Harness, {
        checks: [{ key: "x", valid: false, message: "required" }],
      }),
    )
    expect(html).toContain('data-error-count="0"')
  })
})

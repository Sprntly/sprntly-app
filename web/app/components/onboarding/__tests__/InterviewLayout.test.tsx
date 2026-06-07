// Node-env SSR render assertion (no jsdom) — same View pattern as the
// connector/PRD component tests. InterviewLayout is purely presentational
// (props only, no hooks), so it renders to static markup directly. It is
// the shared v4 onboarding shell for pages 05/06/07.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { InterviewLayout } from "../InterviewLayout"

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

  it("shows the step progress label and an 8-dot indicator", () => {
    const html = render({ step: 5 })
    expect(html).toContain("Step 5 of 8")
    // one dot element per step (match the className attribute, not CSS rules)
    expect((html.match(/class="interview-dot/g) ?? []).length).toBe(8)
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

  it("renders Back and Skip when their handlers are provided", () => {
    const html = render({ onBack: noop, onSkip: noop, skipLabel: "Connect later" })
    expect(html).toContain(">Back<")
    expect(html).toContain("Connect later")
  })
})

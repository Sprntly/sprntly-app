// View tests for the onboarding metrics page — "Set your success metrics" (the
// REDESIGNED pick-exactly-3 page). renderToStaticMarkup pattern (node-env, no
// jsdom, no hooks): the stateful container wires hooks, while MetricsSetupView
// is pure and renders to static markup directly.
//
// The redesign DROPS the explicit North-Star input and the supporting-metric
// split. Instead it shows a FLAT list of candidate metric cards; the user picks
// exactly 3, and selected cards carry the green `.sel` selected state. The
// North Star is inferred server-side, so there is NO north-star input here.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { MetricsSetupView, type MetricsSetupViewProps } from "../Metrics"

function noop() {}

function render(override: Partial<MetricsSetupViewProps> = {}): string {
  const defaults: MetricsSetupViewProps = {
    industry: "Fintech",
    businessType: "Marketplace",
    candidates: [
      { name: "Reconciled volume", description: "Weekly total." },
      { name: "Active connected accounts", description: "Live syncs." },
      { name: "Incremental revenue", description: "" },
      { name: "Conversion rate", description: "" },
    ],
    selected: [],
    customMetric: "",
    errors: {},
    error: null,
    limitWarning: null,
    onChangeIndustry: noop,
    onChangeBusinessType: noop,
    onToggle: noop,
    onChangeCustomMetric: noop,
    onAddCustom: noop,
  }
  return renderToStaticMarkup(
    React.createElement(MetricsSetupView, { ...defaults, ...override }),
  )
}

describe("MetricsSetupView — NO explicit North Star / supporting split", () => {
  it("renders no north-star input and no supporting-metric section", () => {
    const html = render()
    // The dedicated North Star input + its hint chips are gone.
    expect(html).not.toContain("your North Star")
    expect(html).not.toContain("Common for")
    expect(html).not.toContain("The one metric that best captures product value")
    // The old "Supporting metrics" section + tree source are gone.
    expect(html).not.toContain("Supporting metrics")
    expect(html).not.toContain("mt-source")
    expect(html).not.toContain("Primary leads to")
  })

  it("prompts to pick 3 to 5 metrics", () => {
    const html = render()
    expect(html).toContain("Pick your success metrics")
    expect(html).toContain("choose 3 to 5")
  })
})

describe("MetricsSetupView — flat candidate list with green selected state", () => {
  it("renders every candidate as a toggleable card", () => {
    const html = render()
    expect(html).toContain('id="metricCandidates"')
    expect(html).toContain('data-metric="Reconciled volume"')
    expect(html).toContain('data-metric="Conversion rate"')
    // candidate descriptions surface when present
    expect(html).toContain("Weekly total.")
  })

  it("marks selected cards with the green .sel selected state", () => {
    const html = render({
      selected: ["Reconciled volume", "Incremental revenue"],
    })
    // selected cards carry .sel + aria-selected/aria-pressed true
    expect(html).toMatch(
      /<button[^>]*class="mt-target metric-card sel"[^>]*data-metric="Reconciled volume"/,
    )
    expect(html).toContain('aria-selected="true"')
    expect(html).toContain('aria-pressed="true"')
    // an unselected candidate is NOT marked selected
    expect(html).toMatch(
      /<button[^>]*class="mt-target metric-card "[^>]*data-metric="Conversion rate"/,
    )
  })

  it("aria-disables (but never hard-disables) unselected cards once the max (5) is picked", () => {
    const html = render({
      candidates: [
        { name: "M1", description: "" },
        { name: "M2", description: "" },
        { name: "M3", description: "" },
        { name: "M4", description: "" },
        { name: "M5", description: "" },
        { name: "M6", description: "" },
      ],
      selected: ["M1", "M2", "M3", "M4", "M5"],
    })
    // The 6th, unselected card is aria-disabled (hint) but stays clickable so a
    // click can surface the "up to 5" warning — i.e. NOT hard-`disabled=""`.
    expect(html).toMatch(/<button[^>]*data-metric="M6"[^>]*aria-disabled="true"/)
    expect(html).not.toMatch(/<button[^>]*data-metric="M6"[^>]*disabled=""/)
    // A selected card is not aria-disabled (toggle-off must stay obvious).
    expect(html).toMatch(/<button[^>]*data-metric="M1"[^>]*aria-disabled="false"/)
  })

  it("does NOT disable unselected cards below the max (only 3 of 5 picked)", () => {
    const html = render({
      selected: ["Reconciled volume", "Active connected accounts", "Incremental revenue"],
    })
    expect(html).toMatch(/<button[^>]*data-metric="Conversion rate"[^>]*aria-disabled="false"/)
  })
})

describe("MetricsSetupView — the pick counter", () => {
  it("shows the selected count with a 'pick more' nudge below the min (3)", () => {
    expect(render({ selected: [] })).toContain("0</strong> selected")
    expect(render({ selected: [] })).toContain("pick 3 more")
    expect(render({ selected: ["Reconciled volume"] })).toContain("pick 2 more")
  })

  it("shows a ready state from the min (3) up to the max (5)", () => {
    const three = render({
      selected: ["Reconciled volume", "Active connected accounts", "Incremental revenue"],
    })
    expect(three).toContain("3</strong> selected")
    expect(three).toContain("ready")
    expect(three).toContain("add up to 2 more")

    const five = render({
      candidates: [
        { name: "M1", description: "" },
        { name: "M2", description: "" },
        { name: "M3", description: "" },
        { name: "M4", description: "" },
        { name: "M5", description: "" },
      ],
      selected: ["M1", "M2", "M3", "M4", "M5"],
    })
    expect(five).toContain("5</strong> selected")
    expect(five).toContain("max 5")
  })

  it("surfaces the pick-at-least-3 validation error", () => {
    const html = render({
      errors: { metrics: "Pick at least 3 metrics to continue." },
    })
    expect(html).toContain("Pick at least 3 metrics to continue.")
  })

  it("shows the transient 'up to 5' warning when limitWarning is set", () => {
    const html = render({ limitWarning: "You can pick up to 5 metrics — deselect one to swap." })
    expect(html).toContain("up to 5 metrics")
    expect(html).toContain('role="alert"')
  })
})

describe("MetricsSetupView — add your own + dropdowns", () => {
  it("renders the custom metric input and an Add button", () => {
    const html = render()
    expect(html).toContain("Or write your own")
    expect(html).toContain('aria-label="Custom metric name"')
    expect(html).toContain("Add</button>")
  })

  it("disables Add when the custom name is empty", () => {
    // Boolean `disabled=""` (the Add button) — distinct from cards' aria-disabled.
    expect(render({ customMetric: "" })).toContain('disabled=""')
    expect(render({ customMetric: "Net new logos" })).not.toContain('disabled=""')
  })

  it("renders editable, pre-filled industry / business-type dropdowns", () => {
    const html = render({ industry: "Fintech", businessType: "Marketplace" })
    expect(html).toContain('aria-label="Industry"')
    expect(html).toContain('aria-label="Business type"')
    expect(html).toMatch(/<option[^>]*selected[^>]*>Fintech<\/option>/)
    expect(html).toMatch(/<option[^>]*selected[^>]*>Marketplace<\/option>/)
    expect(html).not.toMatch(/<select[^>]*disabled/)
    expect(html).toContain("Gaming / Entertainment")
  })

  it("renders an error banner when error is set", () => {
    const html = render({ error: "Save failed" })
    expect(html).toContain("onb-form-error")
    expect(html).toContain("Save failed")
  })
})

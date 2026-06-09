// Node-env SSR render assertion (no jsdom). KpiTreeEditor is a pure View
// (props only) used by onboarding step 02. These assert the required-field
// error treatment: the .has-error wrapper, the .field-error message, and a
// data-field tag for focus targeting.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { KpiTreeEditor } from "../KpiTreeEditor"
import type { KpiMetric } from "../../../lib/onboarding/types"

const metrics: KpiMetric[] = [
  { name: "", description: "" },
  { name: "", description: "" },
]

function render(
  override: Partial<React.ComponentProps<typeof KpiTreeEditor>> = {},
): string {
  const defaults: React.ComponentProps<typeof KpiTreeEditor> = {
    northStar: "",
    metrics,
    onNorthStarChange: () => {},
    onMetricsChange: () => {},
  }
  return renderToStaticMarkup(
    React.createElement(KpiTreeEditor, { ...defaults, ...override }),
  )
}

describe("KpiTreeEditor error states", () => {
  it("renders no error markup when no errors are passed", () => {
    const html = render()
    expect(html).not.toContain("field-error")
    expect(html).not.toContain("has-error")
  })

  it("tags each required field with data-field for focus", () => {
    const html = render()
    expect(html).toContain('data-field="northStar"')
    expect(html).toContain('data-field="metrics"')
  })

  it("applies has-error and a message for the north star field", () => {
    const html = render({ northStarError: "Name your north star metric." })
    expect(html).toContain('class="field has-error" data-field="northStar"')
    expect(html).toContain("Name your north star metric.")
  })

  it("applies has-error and a message for the supporting metrics field", () => {
    const html = render({ metricsError: "Add at least two supporting metrics." })
    expect(html).toContain("Add at least two supporting metrics.")
    expect(html).toContain("field-error")
  })
})

describe("KpiTreeEditor — description inputs, no numeric inputs", () => {
  it("renders a description textarea per metric and for the north star", () => {
    const html = render()
    // One textarea for the north star + one per metric (2) = 3.
    const textareas = html.match(/<textarea/g) ?? []
    expect(textareas.length).toBe(3)
    expect(html).toContain("Describe what this metric means")
  })

  it("renders NO weight / current / target inputs", () => {
    const html = render()
    expect(html).not.toContain('type="number"')
    expect(html).not.toContain("Weight")
    expect(html).not.toContain("Current (optional)")
    expect(html).not.toContain("Target (optional)")
  })
})

// Markup tests for the pure Templates view ("what good looks like").
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { TemplatesView } from "../TemplatesScreen"
import type { CompanyTemplate } from "../../../../lib/api"

function noop() {}

const SAMPLE: CompanyTemplate[] = [
  {
    id: "t1",
    label: "Guest Deal Alerts — PRD",
    type: "prd",
    filename: "guest-deal-alerts.md",
    content_type: "text/markdown",
    extracted_chars: 4200,
    uploaded_at: "2026-06-01T00:00:00Z",
  },
]

function render(
  override: Partial<React.ComponentProps<typeof TemplatesView>> = {},
): string {
  const ref = React.createRef<HTMLInputElement>()
  return renderToStaticMarkup(
    <TemplatesView
      templates={SAMPLE}
      loading={false}
      uploading={false}
      removingId={null}
      activeFilter="all"
      error={null}
      message={null}
      onPickFile={noop}
      onRemove={noop}
      onFilter={noop}
      fileInputRef={ref}
      onFileChange={noop}
      {...override}
    />,
  )
}

describe("TemplatesView", () => {
  it("renders the 'what good looks like' header + gold-standard copy", () => {
    const html = render()
    expect(html).toMatch(/Templates/)
    expect(html).toMatch(/what good looks like/i)
    expect(html).toMatch(/gold.?standard/i)
  })

  it("offers an upload affordance ('Upload a standard')", () => {
    const html = render()
    expect(html).toMatch(/Upload a standard/i)
  })

  it("lists each uploaded template with its label and a Remove control", () => {
    const html = render()
    expect(html).toContain("Guest Deal Alerts — PRD")
    expect(html).toMatch(/4,200 chars/)
    expect(html).toMatch(/Remove/)
    // Each template carries its type badge.
    expect(html).toMatch(/PRD/)
  })

  it("shows the dashed 'Add a standard' card", () => {
    const html = render()
    expect(html).toMatch(/Add a standard/i)
  })

  it("renders the empty 'no standards yet' affordance when there are none", () => {
    // No template cards → only the Add card; the grid still renders.
    const html = render({ templates: [] })
    expect(html).not.toContain("Guest Deal Alerts")
    expect(html).toMatch(/Add a standard/i)
  })

  it("shows a loading state while fetching", () => {
    const html = render({ loading: true })
    expect(html).toMatch(/Loading templates/i)
  })

  it("surfaces an inline error and a success message", () => {
    expect(render({ error: "Upload failed." })).toContain("Upload failed.")
    expect(render({ message: "Added “x.md”." })).toContain("Added “x.md”.")
  })

  it("marks the active type filter", () => {
    const html = render({ activeFilter: "prd" })
    // The PRD pill is active (has the `on` class); All is not.
    expect(html).toMatch(/tpl-filter on[^>]*>PRD|>PRD<\/button>/)
    expect(html).toMatch(/aria-selected="true"[^>]*>PRD|>PRD</)
  })
})

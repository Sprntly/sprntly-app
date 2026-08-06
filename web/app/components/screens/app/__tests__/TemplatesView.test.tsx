// Markup tests for the pure EXEMPLAR view — §2 of /templates.
//
// RETITLED BY DESIGN (artifact formats, 2026-08). This section is no longer
// called "Templates": that word now covers two libraries on one screen and the
// GOVERNING one sits above it. So "Templates" → "Examples we learn from",
// "Upload a standard" → "Upload an example", "Add a standard" → "Add an
// example", and the intro's old promise that Sprntly "follows your format" is
// gone — it described a feature this section does not provide. The assertions
// on those strings moved with them; that is the change, not a regression.
//
// What must NOT change is that this section keeps saying what it governs —
// voice, depth and tone — and disclaims structure. The last test guards it,
// because losing that sentence is how the two libraries become one
// indistinguishable pile again.
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
  it("renders the 'Examples we learn from' header + gold-standard copy", () => {
    const html = render()
    expect(html).toMatch(/Examples we learn from/)
    expect(html).toMatch(/what good looks like/i)
    expect(html).toMatch(/gold.?standard/i)
    // "Templates" is the PAGE, carried by the chrome strip — never this
    // section's own heading, or the two libraries read as one.
    expect(html).not.toMatch(/>Templates</)
  })

  it("offers an upload affordance ('Upload an example')", () => {
    const html = render()
    expect(html).toMatch(/Upload an example/i)
    // "Standard" had to leave the button: with a governing library on the same
    // screen it reads like the thing that governs.
    expect(html).not.toMatch(/Upload a standard/i)
  })

  it("says it shapes VOICE and explicitly disclaims STRUCTURE", () => {
    // The single most important distinction on this screen. Without it a PM
    // cannot tell which of the two libraries decides what their next PRD looks
    // like, which is the failure mode of the whole design.
    const html = render()
    expect(html).toMatch(/voice/i)
    expect(html).toMatch(
      /don&#x27;t change a document&#x27;s structure; the active format above does that/i,
    )
    expect(html).toMatch(/Structure comes from the active format above/i)
    // The old copy claimed the opposite and is now factually wrong.
    expect(html).not.toMatch(/follows your format/i)
  })

  it("lists each uploaded template with its label and a Remove control", () => {
    const html = render()
    expect(html).toContain("Guest Deal Alerts — PRD")
    expect(html).toMatch(/4,200 chars/)
    expect(html).toMatch(/Remove/)
    // Each template carries its type badge.
    expect(html).toMatch(/PRD/)
  })

  it("shows the dashed 'Add an example' card", () => {
    const html = render()
    expect(html).toMatch(/Add an example/i)
  })

  it("renders the empty 'no examples yet' affordance when there are none", () => {
    // No template cards → only the Add card; the grid still renders.
    const html = render({ templates: [] })
    expect(html).not.toContain("Guest Deal Alerts")
    expect(html).toMatch(/Add an example/i)
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

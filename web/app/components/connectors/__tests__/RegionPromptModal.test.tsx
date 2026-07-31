// View tests for RegionPromptModal — the pre-redirect deployment picker used
// by connectors that run more than one regional install (Marvin).
// Same node-env SSR pattern as the sibling connector component tests.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { RegionPromptModalView } from "../RegionPromptModal"

function noop() {}

type Props = React.ComponentProps<typeof RegionPromptModalView>

const REGIONS = [
  { value: "us", label: "US / Global" },
  { value: "eu", label: "EU" },
]

function render(override: Partial<Props> = {}): string {
  const defaults: Props = {
    open: true,
    connectorName: "Marvin",
    regions: REGIONS,
    value: "us",
    submitting: false,
    error: null,
    onChange: noop,
    onSubmit: noop,
    onClose: noop,
  }
  return renderToStaticMarkup(
    React.createElement(RegionPromptModalView, { ...defaults, ...override }),
  )
}

describe("RegionPromptModalView", () => {
  it("renders nothing when closed", () => {
    expect(render({ open: false })).toBe("")
  })

  it("renders every region as an option, with the current one selected", () => {
    const html = render({ value: "eu" })
    expect(html).toContain('<option value="us">US / Global</option>')
    expect(html).toContain('<option value="eu" selected="">EU</option>')
  })

  it("names the connector in the heading, the label and the CTA", () => {
    const html = render()
    expect(html).toContain("Connect Marvin")
    expect(html).toContain("Marvin region")
    expect(html).toContain("Continue to Marvin")
  })

  it("keeps the modal a CHILD of the overlay so the CSS reveal applies", () => {
    // `.modal-overlay.open .modal` is the reveal selector — a sibling would
    // render invisibly. Same contract as ApiKeyPromptModal.
    expect(render()).toMatch(
      /<div class="modal-overlay open"[^>]*><div class="modal modal-sm"/,
    )
  })

  it("shows an inline error and keeps the picker usable", () => {
    const html = render({ error: "Marvin OAuth is not configured on the server" })
    expect(html).toContain("Marvin OAuth is not configured on the server")
    expect(html).toContain('role="alert"')
    expect(html).toContain('id="conn-region"')
  })

  it("disables the CTA while the OAuth start is in flight", () => {
    const html = render({ submitting: true })
    expect(html).toContain("Connecting…")
    expect(html).toContain("disabled=\"\"")
  })

  it("renders help text when supplied", () => {
    const html = render({ helpText: "Pick the workspace your team uses." })
    expect(html).toContain("Pick the workspace your team uses.")
  })
})

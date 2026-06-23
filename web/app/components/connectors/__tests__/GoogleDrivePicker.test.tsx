// Same node-env SSR pattern as the other connector component tests: render the
// pure View with renderToStaticMarkup. The live Picker JS is an external global
// (window.google.picker) and is intentionally not exercised here.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import type { GoogleDrivePickedFile } from "../../../lib/api"
import { GoogleDrivePickerView } from "../GoogleDrivePicker"

const FILES: GoogleDrivePickedFile[] = [
  { id: "file0001", name: "Product Plan" },
  { id: "file0002" }, // no name → falls back to id
]

const noop = () => {}

type Props = React.ComponentProps<typeof GoogleDrivePickerView>

function render(override: Partial<Props> = {}): string {
  const defaults: Props = {
    savedFiles: FILES,
    configured: true,
    busy: false,
    error: null,
    onAddFiles: noop,
  }
  return renderToStaticMarkup(
    React.createElement(GoogleDrivePickerView, { ...defaults, ...override }),
  )
}

describe("GoogleDrivePickerView", () => {
  it("renders each saved file (name, or id when unnamed)", () => {
    const html = render()
    expect(html).toContain("Product Plan")
    expect(html).toContain("file0002") // unnamed file falls back to its id
  })

  it("renders the 'Add Drive files' button", () => {
    const html = render()
    expect(html).toContain("Add Drive files")
  })

  it("shows the empty state when there are no saved files", () => {
    const html = render({ savedFiles: [] })
    expect(html).toContain("No Drive files selected yet")
  })

  it("disables the button and shows 'Opening…' while busy", () => {
    const html = render({ busy: true })
    expect(html).toContain("Opening…")
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Opening…<\/button>/)
  })

  it("surfaces an error message when one is set", () => {
    const html = render({ error: "Token fetch failed" })
    expect(html).toContain("Token fetch failed")
  })

  it("renders the 'not configured' message when the API key is absent", () => {
    const html = render({ configured: false })
    expect(html).toContain("isn")
    expect(html.toLowerCase()).toContain("configured")
    // The Add button is not rendered in the unconfigured state.
    expect(html).not.toContain("Add Drive files")
  })
})

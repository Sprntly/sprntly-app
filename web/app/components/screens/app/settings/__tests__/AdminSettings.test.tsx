// View tests for the Admin pane (per-company Claude API key).
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { AdminSettingsView } from "../AdminSettings"

function noop() {}

function render(
  override: Partial<React.ComponentProps<typeof AdminSettingsView>> = {},
): string {
  return renderToStaticMarkup(
    <AdminSettingsView
      status={{ configured: false, masked: null }}
      restricted={false}
      loading={false}
      keyInput=""
      saving={false}
      removing={false}
      testing={false}
      error={null}
      message={null}
      onKeyInputChange={noop}
      onSave={noop}
      onRemove={noop}
      onTest={noop}
      {...override}
    />,
  )
}

describe("AdminSettingsView", () => {
  it("shows a restricted message for non-admins", () => {
    const html = render({ restricted: true })
    expect(html).toMatch(/restricted to owners and admins/i)
    // No key input is exposed when restricted.
    expect(html).not.toMatch(/sk-ant-/)
  })

  it("renders the key field + Save when unconfigured", () => {
    const html = render()
    expect(html).toMatch(/Claude API key/i)
    expect(html).toMatch(/placeholder="sk-ant-…"/)
    expect(html).toMatch(/Save key/)
    // Test / Remove only appear once a key exists.
    expect(html).not.toMatch(/Test key/)
    expect(html).not.toMatch(/Remove key/)
  })

  it("disables Save when the input is empty", () => {
    const html = render({ keyInput: "" })
    expect(html).toMatch(/<button[^>]*type="submit"[^>]*disabled/)
  })

  it("enables Save when a key is entered", () => {
    const html = render({ keyInput: "sk-ant-abc" })
    expect(html).not.toMatch(/<button[^>]*type="submit"[^>]*disabled/)
  })

  it("shows the masked key + Replace/Test/Remove when configured", () => {
    const html = render({ status: { configured: true, masked: "sk-ant-…WXYZ" } })
    expect(html).toContain("sk-ant-…WXYZ")
    expect(html).toMatch(/Replace key/)
    expect(html).toMatch(/Test key/)
    expect(html).toMatch(/Remove key/)
  })

  it("surfaces an inline error and a success message", () => {
    expect(render({ error: "Anthropic rejected this key." })).toContain(
      "Anthropic rejected this key.",
    )
    expect(render({ message: "Key is valid" })).toContain("Key is valid")
  })
})

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

  it("gives the stored key its own row with the actions pinned right", () => {
    const html = render({ status: { configured: true, masked: "sk-ant-…WXYZ" } })
    expect(html).toMatch(/akey-current/)
    expect(html).toMatch(/Current key/)
    // Test + Remove live on that row, not down with the submit button.
    const row = html.slice(
      html.indexOf('class="akey-current"'),
      html.indexOf('class="field"'),
    )
    expect(row).toContain("sk-ant-…WXYZ")
    expect(row).toMatch(/Test key/)
    expect(row).toMatch(/aria-label="Remove key"/)
  })

  it("renders Remove as an icon button carrying its name accessibly", () => {
    const html = render({ status: { configured: true, masked: "sk-ant-…WXYZ" } })
    // Icon-only: an <svg> inside, and the name comes from aria-label/title
    // rather than visible text, so it stays announceable and hoverable.
    expect(html).toMatch(/akey-icon-btn/)
    expect(html).toMatch(/aria-label="Remove key"[^>]*title="Remove key"/)
    expect(html).toMatch(/akey-icon-btn[^>]*>\s*<svg/)
    // The old visible-text variant is gone.
    expect(html).not.toMatch(/>Remove key</)
  })

  it("puts the submit button inline with the input", () => {
    const html = render({ status: { configured: true, masked: "sk-ant-…WXYZ" } })
    const rowStart = html.indexOf('class="akey-input-row"')
    expect(rowStart).toBeGreaterThan(-1)
    const row = html.slice(rowStart)
    // Input first, then its submit — same row, in that order.
    expect(row.indexOf("<input")).toBeLessThan(row.indexOf("<button"))
    expect(row).toMatch(/Replace key/)
  })

  it("keeps Remove usable when the stored key cannot be previewed", () => {
    // TOKEN_ENCRYPTION_KEY rotated: configured, but no masked preview.
    const html = render({ status: { configured: true, masked: null } })
    expect(html).toMatch(/preview unavailable/i)
    expect(html).toMatch(/aria-label="Remove key"/)
  })

  it("shows no current-key row before a key is saved", () => {
    const html = render()
    expect(html).not.toMatch(/akey-current/)
    expect(html).not.toMatch(/Current key/)
  })

  it("labels the input so the field-label is clickable", () => {
    const html = render()
    expect(html).toMatch(/for="anthropic-api-key"/)
    expect(html).toMatch(/id="anthropic-api-key"/)
  })

  it("surfaces an inline error and a success message", () => {
    expect(render({ error: "Anthropic rejected this key." })).toContain(
      "Anthropic rejected this key.",
    )
    expect(render({ message: "Key is valid" })).toContain("Key is valid")
  })
})

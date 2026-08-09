// View tests for the Admin pane (AI provider choice + per-provider API key).
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { AdminSettingsView } from "../AdminSettings"
import type { LlmConfig } from "../../../../../lib/api"

function noop() {}

function config(over: Partial<LlmConfig> = {}): LlmConfig {
  return {
    provider: "anthropic",
    providers: {
      anthropic: { configured: false, masked: null },
      openai: { configured: false, masked: null },
    },
    ...over,
  }
}

function render(
  override: Partial<React.ComponentProps<typeof AdminSettingsView>> = {},
): string {
  return renderToStaticMarkup(
    <AdminSettingsView
      config={config()}
      restricted={false}
      loading={false}
      switching={false}
      keyInput=""
      saving={false}
      removing={false}
      testing={false}
      error={null}
      message={null}
      onProviderChange={noop}
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
    // No key input and no provider choice is exposed when restricted.
    expect(html).not.toMatch(/sk-ant-/)
    expect(html).not.toMatch(/prov-card/)
  })

  // ── provider choice ────────────────────────────────────────────────────────

  it("offers both providers as a radiogroup", () => {
    const html = render()
    expect(html).toMatch(/role="radiogroup"/)
    expect(html).toMatch(/>Claude</)
    expect(html).toMatch(/>OpenAI</)
    // Two radios, exactly one checked.
    expect(html.match(/role="radio"/g)).toHaveLength(2)
    expect(html.match(/aria-checked="true"/g)).toHaveLength(1)
  })

  it("marks the active provider with a label, not colour alone", () => {
    const html = render({ config: config({ provider: "openai" }) })
    // The checked radio is the OpenAI one...
    const openaiCard = html.slice(html.indexOf(">OpenAI<") - 400, html.indexOf(">OpenAI<") + 400)
    expect(openaiCard).toMatch(/aria-checked="true"/)
    // ...and it carries a visible "In use" badge as the second signal.
    expect(html).toMatch(/In use/)
  })

  it("shows each provider's own key status, including the inactive one", () => {
    // A stored key must not look lost just because the other provider is live.
    const html = render({
      config: config({
        provider: "openai",
        providers: {
          anthropic: { configured: true, masked: "sk-ant-…WXYZ" },
          openai: { configured: false, masked: null },
        },
      }),
    })
    expect(html).toMatch(/Your key is saved/)
    expect(html).toMatch(/No key — runs on Sprntly/)
  })

  it("locks both cards while a switch is in flight", () => {
    const html = render({ switching: true })
    expect(html.match(/role="radio"[^>]*disabled/g)).toHaveLength(2)
  })

  // ── the key panel follows the active provider ─────────────────────────────

  it("renders the Claude key field + Save when unconfigured", () => {
    const html = render()
    expect(html).toMatch(/Anthropic API key/i)
    expect(html).toMatch(/placeholder="sk-ant-…"/)
    expect(html).toMatch(/Save key/)
    // Test / Remove only appear once a key exists.
    expect(html).not.toMatch(/Test key/)
    expect(html).not.toMatch(/Remove key/)
  })

  it("retargets the key field at OpenAI when OpenAI is active", () => {
    const html = render({ config: config({ provider: "openai" }) })
    expect(html).toMatch(/OpenAI API key/i)
    expect(html).toMatch(/placeholder="sk-…"/)
    expect(html).toMatch(/for="openai-api-key"/)
    expect(html).toMatch(/id="openai-api-key"/)
    // The Anthropic placeholder must not survive the switch.
    expect(html).not.toMatch(/placeholder="sk-ant-…"/)
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
    const html = render({
      config: config({
        providers: {
          anthropic: { configured: true, masked: "sk-ant-…WXYZ" },
          openai: { configured: false, masked: null },
        },
      }),
    })
    expect(html).toContain("sk-ant-…WXYZ")
    expect(html).toMatch(/Replace key/)
    expect(html).toMatch(/Test key/)
    expect(html).toMatch(/Remove key/)
  })

  it("gives the stored key its own row with the actions pinned right", () => {
    const html = render({
      config: config({
        providers: {
          anthropic: { configured: true, masked: "sk-ant-…WXYZ" },
          openai: { configured: false, masked: null },
        },
      }),
    })
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
    const html = render({
      config: config({
        providers: {
          anthropic: { configured: true, masked: "sk-ant-…WXYZ" },
          openai: { configured: false, masked: null },
        },
      }),
    })
    // Icon-only: an <svg> inside, and the name comes from aria-label/title
    // rather than visible text, so it stays announceable and hoverable.
    expect(html).toMatch(/akey-icon-btn/)
    expect(html).toMatch(/aria-label="Remove key"[^>]*title="Remove key"/)
    expect(html).toMatch(/akey-icon-btn[^>]*>\s*<svg/)
    // The old visible-text variant is gone.
    expect(html).not.toMatch(/>Remove key</)
  })

  it("puts the submit button inline with the input", () => {
    const html = render({
      config: config({
        providers: {
          anthropic: { configured: true, masked: "sk-ant-…WXYZ" },
          openai: { configured: false, masked: null },
        },
      }),
    })
    const rowStart = html.indexOf('class="akey-input-row"')
    expect(rowStart).toBeGreaterThan(-1)
    const row = html.slice(rowStart)
    // Input first, then its submit — same row, in that order.
    expect(row.indexOf("<input")).toBeLessThan(row.indexOf("<button"))
    expect(row).toMatch(/Replace key/)
  })

  it("keeps Remove usable when the stored key cannot be previewed", () => {
    // TOKEN_ENCRYPTION_KEY rotated: configured, but no masked preview.
    const html = render({
      config: config({
        providers: {
          anthropic: { configured: true, masked: null },
          openai: { configured: false, masked: null },
        },
      }),
    })
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

  it("says what happens while no key is saved", () => {
    // The keyless state is a working one (we cover it), and the reader should
    // know that rather than assume the product is broken.
    expect(render()).toMatch(/key at our cost/i)
  })

  it("surfaces an inline error and a success message", () => {
    expect(render({ error: "Anthropic rejected this key." })).toContain(
      "Anthropic rejected this key.",
    )
    expect(render({ message: "Key is valid" })).toContain("Key is valid")
  })
})

// First test file for the PRD-screen prototype preview card. The card is a pure
// leaf taking a resolved PrototypeRecord prop, so it SSR-renders cleanly under
// node-env vitest (no DOM, no router, no @testing-library) — mirroring the
// DesignAgentLauncher / CompletionBar convention: renderToStaticMarkup for the
// markup assertions, a direct functional call for the click handler, and an fs
// read of the source for the marker-removal invariant.
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses the
// classic runtime, so expose React globally (repo test convention).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { PrototypePreviewCard, previewVersionLabel } from "../PrototypePreviewCard"
import type { PrototypeRecord } from "../../../lib/api"

const HERE = dirname(fileURLToPath(import.meta.url))
const SOURCE_PATH = join(HERE, "..", "PrototypePreviewCard.tsx")

function rec(over: Partial<PrototypeRecord> = {}): PrototypeRecord {
  return {
    id: 54,
    status: "ready",
    bundle_url: "https://cdn/x/bundle/index.html",
    error: null,
    is_complete: false,
    share_mode: "private",
    share_token: null,
    ...over,
  }
}

const noop = () => {}

describe("PrototypePreviewCard — creation", () => {
  it("renders the title, sub-line, and open affordance (test_preview_card_renders_title_sub_and_open)", () => {
    const prototype = rec({ id: 54 })
    const html = renderToStaticMarkup(
      React.createElement(PrototypePreviewCard, {
        prototype,
        prdTitle: "Checkout flow",
        onOpen: noop,
      }),
    )
    // Title: "{prdTitle} · prototype".
    expect(html).toContain("Checkout flow · prototype")
    // Sub-line carries the version-ish handle from previewVersionLabel.
    expect(html).toContain(previewVersionLabel(prototype))
    expect(html).toContain("click to open the design")
    // Open affordance.
    expect(html).toContain("da-preview-open")
    // The card is a real button addressable by its test id.
    expect(html).toContain('data-testid="da-prototype-preview-card"')
  })

  it("labels the card and aria-label with the PRD title (test_preview_card_uses_prd_title_label)", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypePreviewCard, {
        prototype: rec(),
        prdTitle: "Onboarding",
        onOpen: noop,
      }),
    )
    // Title text contains the PRD title.
    expect(html).toContain("Onboarding")
    // aria-label is derived from the PRD title too.
    expect(html).toContain('aria-label="Open the design for Onboarding"')
  })
})

describe("PrototypePreviewCard — interaction (pure)", () => {
  it("fires onOpen exactly once when the card is activated (test_preview_card_onOpen_fires)", () => {
    const onOpen = vi.fn()
    // The card is a pure leaf returning a <button>; call it directly and invoke
    // the bound handler — no DOM needed under node-env SSR.
    const el = PrototypePreviewCard({
      prototype: rec(),
      prdTitle: "Onboarding",
      onOpen,
    }) as React.ReactElement
    expect(el.type).toBe("button")
    ;(el.props as { onClick: () => void }).onClick()
    expect(onOpen).toHaveBeenCalledTimes(1)
  })
})

describe("PrototypePreviewCard — edge cases", () => {
  it("falls back to the placeholder thumbnail when bundle_url is null (test_preview_card_null_bundle_url_renders_placeholder)", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypePreviewCard, {
        prototype: rec({ bundle_url: null }),
        prdTitle: "Onboarding",
        onOpen: noop,
      }),
    )
    expect(html).toContain("da-preview-thumb-empty")
    expect(html).not.toContain("<iframe")
  })

  it("renders a click-inert iframe thumbnail for a non-null bundle_url (test_preview_card_with_bundle_url_renders_inert_iframe)", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypePreviewCard, {
        prototype: rec({ bundle_url: "https://cdn/x/bundle/index.html" }),
        prdTitle: "Onboarding",
        onOpen: noop,
      }),
    )
    expect(html).toContain("<iframe")
    // Inert: not tabbable and sandboxed with no tokens.
    expect(html).toContain('tabindex="-1"')
    expect(html).toContain('sandbox=""')
  })
})

describe("PrototypePreviewCard — non-breakage (source-read)", () => {
  it("carries no throwaway exploration marker in its source (test_preview_card_source_durable)", () => {
    // Read the working-tree source via fs (never a historical git rev — CI
    // shallow-clones), mirroring the design-agent-css source-read pattern.
    const source = readFileSync(SOURCE_PATH, "utf8")
    expect(source).not.toContain("UX-EXPLORE (throwaway — REVERT)")
  })
})

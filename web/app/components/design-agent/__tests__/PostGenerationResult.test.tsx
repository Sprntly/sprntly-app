// P2-12 — PostGenerationResult tests. Node-env vitest (no DOM, no router, no
// @testing-library), so — following the CompletionBar / DesignAgentDrawer
// convention — we SSR-render the pure view via renderToStaticMarkup and
// unit-test the extracted pure helper directly.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses
// the classic runtime, so expose React globally (PrdSections/CompletionBar test
// convention) rather than touch the shared vitest config.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  PostGenerationResult,
  PostGenerationResultView,
  resolveViewHref,
  type PostGenerationResultViewProps,
} from "../PostGenerationResult"
import type { PrototypeRecord } from "../../../lib/api"

afterEach(() => {
  vi.restoreAllMocks()
})

function renderView(
  over: Partial<PostGenerationResultViewProps> = {},
): string {
  return renderToStaticMarkup(
    React.createElement(PostGenerationResultView, {
      prototypeId: 42,
      isComplete: false,
      shareMode: "private",
      shareToken: null,
      bundleUrl: null,
      ...over,
    }),
  )
}

function proto(over: Partial<PrototypeRecord> = {}): PrototypeRecord {
  return {
    id: 42,
    status: "ready",
    bundle_url: null,
    error: null,
    ...over,
  }
}

describe("PostGenerationResultView — editable CompletionBar (AC1)", () => {
  it("renders an editable Mark Complete button for a WIP prototype (test_renders_editable_completion_bar_with_prototype_id)", () => {
    const html = renderView({ isComplete: false })
    expect(html).toContain('data-testid="mark-complete-btn"')
    expect(html).toContain("Mark Complete")
  })

  it("mounts the result wrapper", () => {
    const html = renderView()
    expect(html).toContain('data-testid="post-generation-result"')
  })
})

describe("PostGenerationResultView — ShareMenu (AC2)", () => {
  it("renders the ShareMenu with the initial mode checked (test_renders_share_menu_with_initial_mode)", () => {
    const html = renderView({ shareMode: "public" })
    expect(html).toContain('data-testid="share-menu"')
    // Three radios render; the current mode's radio carries `checked`.
    expect(html).toMatch(/<input[^>]*checked[^>]*value="public"[^>]*>/)
    expect(html).not.toMatch(/<input[^>]*checked[^>]*value="private"[^>]*>/)
  })

  it("checks the private radio when private", () => {
    const html = renderView({ shareMode: "private" })
    expect(html).toMatch(/<input[^>]*checked[^>]*value="private"[^>]*>/)
  })
})

describe("PostGenerationResultView — editable, not read-only (AC3)", () => {
  it("does NOT render the read-only completion bar (test_is_editable_not_readonly)", () => {
    const html = renderView({ isComplete: false })
    expect(html).not.toContain('data-testid="completion-bar-readonly"')
    // The editable container is mounted instead.
    expect(html).toContain('data-testid="completion-bar"')
  })
})

describe("PostGenerationResultView — complete state (AC4)", () => {
  it("reflects the complete state — resume/download/copy render when isComplete (test_complete_state_reflects_after_onStateChange)", () => {
    // The container's onStateChange handler feeds the new isComplete straight
    // into this prop; rendering with isComplete=true is the post-change view.
    const html = renderView({ isComplete: true })
    expect(html).toContain('data-testid="resume-btn"')
    expect(html).toContain('data-testid="download-md-btn"')
    expect(html).toContain('data-testid="copy-md-btn"')
    expect(html).not.toContain('data-testid="mark-complete-btn"')
  })
})

describe("PostGenerationResultView — View prototype link (AC: view affordance)", () => {
  it("renders the link when a bundle_url is present (test_view_link_present_when_bundle_or_token)", () => {
    const html = renderView({ bundleUrl: "https://cdn/x/bundle/index.html" })
    expect(html).toContain('data-testid="view-prototype-link"')
    expect(html).toContain("https://cdn/x/bundle/index.html")
  })

  it("falls back to the /p/<token> link when no bundle but shared", () => {
    const html = renderView({ bundleUrl: null, shareToken: "tok-123" })
    expect(html).toContain('data-testid="view-prototype-link"')
    expect(html).toContain("/p/tok-123")
  })

  it("hides the link when there is neither a bundle nor a token", () => {
    const html = renderView({ bundleUrl: null, shareToken: null })
    expect(html).not.toContain('data-testid="view-prototype-link"')
  })
})

describe("resolveViewHref (pure)", () => {
  it("prefers the bundle url", () => {
    expect(resolveViewHref("https://b/x", "tok")).toBe("https://b/x")
  })
  it("falls back to the public token link", () => {
    expect(resolveViewHref(null, "tok")).toBe("/p/tok")
  })
  it("returns null when neither is available", () => {
    expect(resolveViewHref(null, null)).toBeNull()
  })
})

describe("PostGenerationResult container — defaults from the prototype record (AC9)", () => {
  it("mounts the editable chrome from a full record", () => {
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto({
          is_complete: false,
          share_mode: "public",
          share_token: "abc",
        }),
      }),
    )
    expect(html).toContain('data-testid="post-generation-result"')
    expect(html).toContain('data-testid="mark-complete-btn"')
    expect(html).toMatch(/<input[^>]*checked[^>]*value="public"[^>]*>/)
  })

  it("seeds the Complete view from a record with is_complete=true", () => {
    // Guards the staleness class the launcher `key={result.id}` fix targets:
    // the container seeds is_complete from the prop at mount, so a complete
    // record must render the complete-state controls (not the WIP button).
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto({ is_complete: true, share_mode: "private" }),
      }),
    )
    expect(html).toContain('data-testid="resume-btn"')
    expect(html).toContain('data-testid="download-md-btn"')
    expect(html).not.toContain('data-testid="mark-complete-btn"')
  })

  it("defaults share_mode→private / is_complete→false when the columns are absent", () => {
    // Older / partial rows that don't surface the P2-06 columns.
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto(),
      }),
    )
    expect(html).toContain('data-testid="mark-complete-btn"') // is_complete→false
    expect(html).toMatch(/<input[^>]*checked[^>]*value="private"[^>]*>/) // share_mode→private
  })
})

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
  reseedStep,
  type PostGenerationResultViewProps,
  type ReseedBaseline,
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

  it("the iframe + View href follow a refreshed bundle_url (test_preview_refreshes_after_iterate, AC4)", () => {
    // bundle_url is read straight from the prop in the view, so a refreshed
    // record (post-iterate, same id) re-renders the iframe src + View href onto
    // the NEW bundle with no manual remount — the #5 staleness this ticket fixes.
    const oldHtml = renderView({ bundleUrl: "https://cdn/OLD/index.html" })
    expect(oldHtml).toContain("https://cdn/OLD/index.html")
    const newHtml = renderView({ bundleUrl: "https://cdn/NEW/index.html" })
    expect(newHtml).toContain("https://cdn/NEW/index.html")
    expect(newHtml).not.toContain("https://cdn/OLD/index.html")
  })
})

// ─── P6-05 (#5): guarded re-seed of the local isComplete on a refetch ────────

describe("reseedStep — guarded local-isComplete re-seed (AC4/AC5/AC10)", () => {
  it("re-seeds on a genuine checkpoint advance with a differing prop is_complete (test_post_generation_result_reseeds_is_complete_on_genuine_advance)", () => {
    // bundle_url changed AND prop is_complete (true) differs from baseline (false).
    const base: ReseedBaseline = { bundle: "old/bundle", complete: false }
    const out = reseedStep(base, "new/bundle", true)
    expect(out.setComplete).toBe(true)
    expect(out.baseline).toEqual({ bundle: "new/bundle", complete: true })
  })

  it("does NOT re-seed when only the bundle changed but prop is_complete equals the baseline", () => {
    // A checkpoint advance whose prop is_complete (false) matches baseline (false):
    // the baseline advances to the new bundle but the local copy is left alone.
    const base: ReseedBaseline = { bundle: "old/bundle", complete: false }
    const out = reseedStep(base, "new/bundle", false)
    expect(out.setComplete).toBeNull()
    expect(out.baseline).toEqual({ bundle: "new/bundle", complete: false })
  })

  it("no-ops when the bundle_url did not change (no checkpoint advance)", () => {
    const base: ReseedBaseline = { bundle: "same/bundle", complete: false }
    const out = reseedStep(base, "same/bundle", true)
    expect(out.setComplete).toBeNull()
    expect(out.baseline).toBe(base) // baseline unchanged
  })

  it("does NOT clobber a user's local Mark-Complete across prop changes (test_reseed_does_not_clobber_local_mark_complete)", () => {
    // Sequence: prop seeds is_complete=false (baseline). User marks complete
    // locally (the LOCAL copy is true; the baseline stays the prop-derived false).
    // 1) A prop re-render with the SAME bundle must not re-seed → local stays true.
    let baseline: ReseedBaseline = { bundle: "b1", complete: false }
    const sameBundle = reseedStep(baseline, "b1", false)
    expect(sameBundle.setComplete).toBeNull() // local Mark-Complete survives
    baseline = sameBundle.baseline

    // 2) A genuine checkpoint advance whose prop is_complete (false) equals the
    //    last prop-derived baseline (false) → still no spurious revert.
    const advanceSameComplete = reseedStep(baseline, "b2", false)
    expect(advanceSameComplete.setComplete).toBeNull()
    baseline = advanceSameComplete.baseline
    expect(baseline).toEqual({ bundle: "b2", complete: false })

    // 3) A checkpoint advance whose prop is_complete (true) differs from baseline
    //    (false) DOES re-seed — a real prop change, not a clobber of local state.
    const advanceDiffComplete = reseedStep(baseline, "b3", true)
    expect(advanceDiffComplete.setComplete).toBe(true)
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

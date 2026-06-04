// P2-12 — PostGenerationResult tests. Node-env vitest (no DOM, no router, no
// @testing-library), so — following the CompletionBar / DesignAgentDrawer
// convention — we SSR-render the pure view via renderToStaticMarkup and
// unit-test the extracted pure helper directly.
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
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
import { ShareMenu } from "../ShareMenu"
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

function countOccurrences(haystack: string, needle: string): number {
  let count = 0
  let idx = haystack.indexOf(needle)
  while (idx !== -1) {
    count += 1
    idx = haystack.indexOf(needle, idx + needle.length)
  }
  return count
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

// ─── P6-16 (UX-6): always-shown full-screen View affordance ──────────────────
// INVERTED from the obsolete "View prototype link" block: the `view-prototype-link`
// anchor (silently hidden when resolveViewHref returned null; a chrome-less raw
// new tab otherwise) is replaced by an ALWAYS-shown "View full screen" trigger
// (enabled when a bundle exists, disabled-with-label otherwise) + a full-screen
// overlay that reuses the P6-12 device frame. resolveViewHref is KEPT (its pure
// describe below still passes) but no longer gates a hidden link.
describe("PostGenerationResultView — View affordance never dead (P6-16 AC1/AC4b)", () => {
  // Regression — fails on unfixed code (`{viewHref && <a/>}` rendered NOTHING
  // when resolveViewHref returned null).
  it("test_view_affordance_never_hidden_when_no_bundle — always renders a (disabled) View control with no bundle or token", () => {
    const html = renderView({ bundleUrl: null, shareToken: null })
    expect(html).toContain('data-testid="view-fullscreen-trigger"')
    // Disabled + explanatory label — present, NOT removed.
    expect(html).toMatch(/data-testid="view-fullscreen-trigger"[^>]*disabled/)
    expect(html).toContain("Prototype building")
    // The obsolete dead-end anchor is gone entirely.
    expect(html).not.toContain('data-testid="view-prototype-link"')
  })

  it("test_view_affordance_enabled_when_bundle — enabled 'View full screen' trigger when a bundle exists", () => {
    const html = renderView({ bundleUrl: "https://cdn/x/bundle/index.html" })
    expect(html).toContain('data-testid="view-fullscreen-trigger"')
    expect(html).toContain("View full screen")
    expect(html).not.toMatch(/data-testid="view-fullscreen-trigger"[^>]*disabled/)
  })

  it("no bundle but shared (token present) still shows the always-present control, not the old /p link dead-end", () => {
    const html = renderView({ bundleUrl: null, shareToken: "tok-123" })
    // Always-shown control present (disabled — no bundle to open yet).
    expect(html).toContain('data-testid="view-fullscreen-trigger"')
    expect(html).toMatch(/data-testid="view-fullscreen-trigger"[^>]*disabled/)
    // No raw new-tab /p link affordance remains here (ShareMenu owns sharing).
    expect(html).not.toContain('data-testid="view-prototype-link"')
  })

  it("the inline iframe follows a refreshed bundle_url (test_preview_refreshes_after_iterate, AC4)", () => {
    // bundle_url is read straight from the prop, so a refreshed record
    // (post-iterate, same id) re-renders the inline viewer iframe onto the NEW
    // bundle with no manual remount — the #5 staleness P6-05 fixed, preserved here.
    const oldHtml = renderView({ bundleUrl: "https://cdn/OLD/index.html" })
    expect(oldHtml).toContain("https://cdn/OLD/index.html")
    const newHtml = renderView({ bundleUrl: "https://cdn/NEW/index.html" })
    expect(newHtml).toContain("https://cdn/NEW/index.html")
    expect(newHtml).not.toContain("https://cdn/OLD/index.html")
  })
})

describe("PostGenerationResultView — full-screen overlay (P6-16 AC2/AC3/AC3b)", () => {
  const BUNDLE = "https://cdn/x/bundle/index.html"

  it("test_trigger_opens_overlay — fullscreenOpen renders the proto-fullscreen dialog; closed renders none", () => {
    const open = renderView({ bundleUrl: BUNDLE, fullscreenOpen: true })
    expect(open).toContain('data-testid="proto-fullscreen"')
    expect(open).toMatch(/role="dialog"/)
    expect(open).toMatch(/aria-modal="true"/)
    expect(open).toContain('data-testid="proto-fullscreen-close"')

    const closed = renderView({ bundleUrl: BUNDLE, fullscreenOpen: false })
    expect(closed).not.toContain('data-testid="proto-fullscreen"')
  })

  it("never renders the overlay without a bundle even if fullscreenOpen is true", () => {
    const html = renderView({ bundleUrl: null, fullscreenOpen: true })
    expect(html).not.toContain('data-testid="proto-fullscreen"')
  })

  it("test_overlay_mounts_device_frame — the open overlay mounts a <PrototypeViewer> (proto-frame device chrome), not a bare iframe", () => {
    const html = renderView({ bundleUrl: BUNDLE, fullscreenOpen: true })
    // The device frame (P6-12) is present inside the overlay.
    expect(html).toContain('class="proto-frame"')
    // The viewer's iframe carries the locked className (inside proto-frame),
    // i.e. it is a PrototypeViewer, not a top-level bare <iframe>.
    expect(html).toContain('class="da-prototype-iframe"')
    // The same bundle url is passed to the overlay viewer.
    expect(html).toContain(BUNDLE)
  })

  it("test_overlay_viewer_does_not_shadow_inline_edit_iframe — at most ONE da-prototype-iframe at any instant (AC3b)", () => {
    // Overlay closed: exactly one (inline) iframe, with the inline editor.
    const closed = renderView({ bundleUrl: BUNDLE, fullscreenOpen: false })
    expect(countOccurrences(closed, 'class="da-prototype-iframe"')).toBe(1)
    expect(closed).toContain('data-testid="manual-edit-overlay"')

    // Overlay open: the inline viewer (its iframe AND its ManualEditOverlay
    // editor) is unmounted, leaving exactly one (overlay, view-only) iframe — so
    // ManualEditOverlay's GLOBAL querySelector can never resolve to a shadowing
    // second iframe. The live collision is tester-verified (AC8).
    const open = renderView({ bundleUrl: BUNDLE, fullscreenOpen: true })
    expect(countOccurrences(open, 'class="da-prototype-iframe"')).toBe(1)
    expect(open).not.toContain('data-testid="manual-edit-overlay"')
  })
})

// ─── P6-13 (UX-3): two-column design-pane (viewer + comments slot) ───────────

describe("PostGenerationResultView — two-column design-pane (AC1/AC4)", () => {
  const sentinel = () =>
    React.createElement(
      "div",
      { "data-testid": "sentinel-comments" },
      "SENTINEL_COMMENTS",
    )

  it("wraps the viewer (main cell) + comments (aside cell) as siblings in a design-pane (test_design_pane_wraps_viewer_and_comments)", () => {
    const html = renderView({
      bundleUrl: "https://cdn/x/bundle/index.html",
      comments: sentinel(),
    })
    // The grid container + both cells render.
    expect(html).toContain('class="design-pane"')
    expect(html).toContain('class="design-pane-main"')
    expect(html).toContain('class="design-pane-aside"')
    // The viewer (its bundle url) sits in the main cell; the comments node in the aside.
    expect(html).toContain("https://cdn/x/bundle/index.html")
    expect(html).toContain('data-testid="sentinel-comments"')
    // Siblings, in order, inside the pane: pane → main → aside.
    const paneIdx = html.indexOf('class="design-pane"')
    const mainIdx = html.indexOf('class="design-pane-main"')
    const asideIdx = html.indexOf('class="design-pane-aside"')
    expect(paneIdx).toBeGreaterThanOrEqual(0)
    expect(mainIdx).toBeGreaterThan(paneIdx)
    expect(asideIdx).toBeGreaterThan(mainIdx)
  })

  it("renders the viewer with NO comments cell when comments is absent — public-viewer shape (test_no_comments_cell_when_comments_null)", () => {
    const html = renderView({ bundleUrl: "https://cdn/x/bundle/index.html" })
    // No comments slot → no aside cell and no grid wrapper (degrades to one column).
    expect(html).not.toContain("design-pane-aside")
    expect(html).not.toContain('class="design-pane"')
    // The viewer still renders.
    expect(html).toContain("https://cdn/x/bundle/index.html")
  })

  it("keeps the View affordance OUTSIDE the design-pane (chrome stays full-width)", () => {
    // P6-16: the View affordance is now the always-shown `view-fullscreen-trigger`
    // (replacing the obsolete `view-prototype-link` anchor). It still follows the
    // pane, not nested in a grid cell.
    const html = renderView({
      bundleUrl: "https://cdn/x/bundle/index.html",
      comments: sentinel(),
    })
    const asideEnd = html.indexOf('data-testid="sentinel-comments"')
    const triggerIdx = html.indexOf('data-testid="view-fullscreen-trigger"')
    expect(triggerIdx).toBeGreaterThan(asideEnd)
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

// ─── P6-20 (#14): forwards onShared down to <ShareMenu> ──────────────────────
// The view is pure → call it directly and inspect the <ShareMenu> child element's
// props (no DOM render, so the real ShareMenu is inspected as an element, not run).
describe("PostGenerationResultView — forwards onShared to ShareMenu (P6-20 AC2)", () => {
  function shareMenuEl(
    over: Partial<PostGenerationResultViewProps> = {},
  ): React.ReactElement | undefined {
    const tree = PostGenerationResultView({
      prototypeId: 42,
      isComplete: false,
      shareMode: "private",
      shareToken: null,
      bundleUrl: null,
      ...over,
    }) as React.ReactElement
    const kids = React.Children.toArray(
      (tree.props as { children: React.ReactNode }).children,
    ) as React.ReactElement[]
    return kids.find((c) => c.type === ShareMenu)
  }

  it("passes its onShared prop down to <ShareMenu> (test_post_generation_result_forwards_on_shared)", () => {
    const onShared = vi.fn()
    const share = shareMenuEl({ onShared })
    expect(share).toBeTruthy()
    expect((share!.props as { onShared?: unknown }).onShared).toBe(onShared)
  })

  it("ShareMenu receives onShared=undefined on the public-composition path (no handler supplied)", () => {
    const share = shareMenuEl({})
    expect(share).toBeTruthy()
    expect((share!.props as { onShared?: unknown }).onShared).toBeUndefined()
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

// ─── P6-13 (UX-3): CSS structural + public-viewer-unchanged invariants ───────
// WORKING-TREE content invariants read via fs — NEVER `git show <historical-rev>`
// / `git diff <sha>`, which fails under CI's shallow clone (fetch-depth=1). The
// "design-pane grid present" + "PublicTokenViewer does not use PostGenerationResult"
// checks assert the CURRENT tree's content (the AC intent), method free.

const HERE = dirname(fileURLToPath(import.meta.url))
// __tests__ → design-agent → components → app
const APP_DIR = join(HERE, "..", "..", "..")
const CSS = readFileSync(join(HERE, "..", "design-agent.css"), "utf8")
const PUBLIC_VIEWER = readFileSync(
  join(APP_DIR, "p", "[token]", "PublicTokenViewer.tsx"),
  "utf8",
)

describe("design-agent.css — two-column design-pane appended + scoped (AC2)", () => {
  it("defines a scoped .design-pane grid at 1fr/320px (test_css_design_pane_appended_and_scoped)", () => {
    // The grid container is scoped under .design-agent-surface (P6-11's
    // scoping-invariant test independently enforces this for every selector).
    const block = CSS.match(
      /\.design-agent-surface\s+\.design-pane\s*\{([^}]*)\}/,
    )
    expect(block).not.toBeNull()
    const body = block![1]
    expect(body).toMatch(/display:\s*grid/)
    expect(body).toMatch(/grid-template-columns:\s*1fr\s+320px/)
  })

  it("collapses to a single column at ≤1080px via a media query", () => {
    // The @media block flips grid-template-columns to a single 1fr track.
    expect(CSS).toMatch(/@media\s*\(max-width:\s*1080px\)/)
    const media = CSS.match(
      /@media\s*\(max-width:\s*1080px\)\s*\{([\s\S]*?)\n\}/,
    )
    expect(media).not.toBeNull()
    expect(media![1]).toMatch(
      /\.design-agent-surface\s+\.design-pane\s*\{[^}]*grid-template-columns:\s*1fr\s*;/,
    )
  })

  it("introduces no new colour literals in the appended block", () => {
    // The appended P6-13 values are layout-only (grid/px) — no hex / rgb / hsl.
    // (P6-11's palette test enforces this file-wide; this is a local guard.)
    expect(CSS).toContain(".design-pane")
    const paneRegion = CSS.slice(CSS.indexOf(".design-pane"))
    expect(paneRegion).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(paneRegion).not.toMatch(/rgba?\(/)
    expect(paneRegion).not.toMatch(/hsla?\(/)
  })
})

describe("design-agent.css — full-screen overlay block appended + scoped (P6-16 AC6)", () => {
  it("test_css_fullscreen_block_appended_and_scoped", () => {
    expect(CSS).toContain(".design-agent-surface .proto-fullscreen")
    expect(CSS).toContain(".design-agent-surface .proto-fullscreen-body")
    expect(CSS).toContain(".design-agent-surface .proto-fullscreen-close")
    expect(CSS).toContain(".design-agent-surface .proto-fullscreen-trigger")
    // Every proto-fullscreen* selector occurrence is scoped under the surface.
    const fsSelectors = CSS.match(/^\s*\.[^\n{]*proto-fullscreen[^\n{]*\{/gm) ?? []
    expect(fsSelectors.length).toBeGreaterThan(0)
    for (const sel of fsSelectors) {
      expect(sel.trimStart()).toMatch(/^\.design-agent-surface\s/)
    }
    // The appended block uses tokens only — NO new colour literal (the scrim
    // reuses var(--ink-alpha-45)); no documented-scrim exception is needed.
    const block = CSS.slice(
      CSS.indexOf(".design-agent-surface .proto-fullscreen-trigger"),
    )
    expect(block).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(block).not.toMatch(/rgba?\(/)
    expect(block).not.toMatch(/hsla?\(/)
  })
})

describe("public viewer unaffected (AC4)", () => {
  it("PublicTokenViewer does NOT use PostGenerationResult (test_public_viewer_unchanged)", () => {
    // AC4: the public /p/<token> surface composes its own chrome and never
    // mounts PostGenerationResult, so the two-column relocation cannot touch it.
    expect(PUBLIC_VIEWER).not.toContain("PostGenerationResult")
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

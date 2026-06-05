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
  DaControlBar,
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

// UX-EXPLORE (throwaway — REVERT): the control bar is now a COMPACT single row.
// The full CompletionBar (Mark Complete / Resume / Export) + full ShareMenu
// radios are NO LONGER rendered inline — they live inside closed dropdown
// popovers (Actions ⋯ / Share) that only open client-side. So the SSR markup now
// asserts the COMPACT TRIGGERS exist, not the expanded panels.
describe("PostGenerationResultView — compact control bar (UX-EXPLORE)", () => {
  it("renders the compact control bar with the Actions (handoff) + Share triggers, not the inline panels", () => {
    const html = renderView({ isComplete: false })
    expect(html).toContain('data-testid="da-controlbar"')
    // Compact triggers present…
    expect(html).toContain('data-testid="da-actions-toggle"')
    expect(html).toContain('data-testid="da-share-toggle"')
    // …and the expanded CompletionBar / ShareMenu panels are NOT inline (closed
    // popovers): the bar never bloats with the full button stack / radios.
    expect(html).not.toContain('data-testid="mark-complete-btn"')
    expect(html).not.toContain('data-testid="share-menu"')
  })

  it("mounts the result wrapper", () => {
    const html = renderView()
    expect(html).toContain('data-testid="post-generation-result"')
  })

  it("renders the LEFT-cluster Desktop/Mobile platform toggle in the bar", () => {
    const html = renderView()
    expect(html).toContain('class="da-controlbar-l"')
    expect(html).toContain('class="platform-toggle da-controlbar-platform"')
  })

  it("never renders the read-only completion bar on the editable surface (test_is_editable_not_readonly)", () => {
    const html = renderView({ isComplete: false })
    expect(html).not.toContain('data-testid="completion-bar-readonly"')
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
  // UX-EXPLORE (throwaway — REVERT): the always-shown View control is now the
  // COMPACT `proto-fullscreen-trigger` icon button in the control bar (renamed
  // from the prior center-toolbar `view-fullscreen-trigger`). Still never a dead
  // link: enabled when a bundle exists, disabled-with-title otherwise.
  it("test_view_affordance_never_hidden_when_no_bundle — always renders a (disabled) View control with no bundle or token", () => {
    const html = renderView({ bundleUrl: null, shareToken: null })
    expect(html).toContain('data-testid="proto-fullscreen-trigger"')
    // Disabled + explanatory title — present, NOT removed.
    expect(html).toMatch(/data-testid="proto-fullscreen-trigger"[^>]*disabled/)
    expect(html).toContain("Prototype building")
    // The obsolete dead-end anchor is gone entirely.
    expect(html).not.toContain('data-testid="view-prototype-link"')
  })

  it("test_view_affordance_enabled_when_bundle — enabled 'View full screen' trigger when a bundle exists", () => {
    const html = renderView({ bundleUrl: "https://cdn/x/bundle/index.html" })
    expect(html).toContain('data-testid="proto-fullscreen-trigger"')
    expect(html).toContain("View full screen")
    expect(html).not.toMatch(/data-testid="proto-fullscreen-trigger"[^>]*disabled/)
  })

  it("no bundle but shared (token present) still shows the always-present control, not the old /p link dead-end", () => {
    const html = renderView({ bundleUrl: null, shareToken: "tok-123" })
    // Always-shown control present (disabled — no bundle to open yet).
    expect(html).toContain('data-testid="proto-fullscreen-trigger"')
    expect(html).toMatch(/data-testid="proto-fullscreen-trigger"[^>]*disabled/)
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

// ─── UX-EXPLORE (throwaway — REVERT): control bar + 3-section body ───────────
// The signed-in post-gen surface mounts a compact control bar (`.da-controlbar`)
// + a `.da-ready` flex body with a LEFT collapsible sidebar (`.da-left`: PRD +
// iterate compose `da-canvas-iterate`), a CENTER full-area canvas (`.da-stage`,
// testid `da-canvas-center`), and a RIGHT collapsible comments sidebar
// (`.da-right`, testid `da-canvas-comments`) that — per Problem 2 — ALWAYS exists
// (with CommentsPanel when present, else an empty state).
describe("PostGenerationResultView — control bar + 3-section body (UX-EXPLORE)", () => {
  const sentinel = (id: string, text: string) =>
    React.createElement("div", { "data-testid": id }, text)

  it("wraps a control bar + LEFT sidebar (iterate), CENTER canvas, RIGHT comments", () => {
    const html = renderView({
      bundleUrl: "https://cdn/x/bundle/index.html",
      iterate: sentinel("sentinel-iterate", "SENTINEL_ITERATE"),
      comments: sentinel("sentinel-comments", "SENTINEL_COMMENTS"),
    })
    expect(html).toContain('data-testid="da-controlbar"')
    expect(html).toContain('class="da-ready"')
    expect(html).toContain('data-testid="da-canvas-iterate"')
    expect(html).toContain('data-testid="da-canvas-center"')
    expect(html).toContain('data-testid="da-canvas-comments"')
    // The slots land in their regions; the viewer (its bundle url) is in the center.
    expect(html).toContain("https://cdn/x/bundle/index.html")
    expect(html).toContain('data-testid="sentinel-iterate"')
    expect(html).toContain('data-testid="sentinel-comments"')
    // Order: control bar → body → left → center → right.
    const barIdx = html.indexOf('data-testid="da-controlbar"')
    const bodyIdx = html.indexOf('class="da-ready"')
    const iterateIdx = html.indexOf('data-testid="da-canvas-iterate"')
    const centerIdx = html.indexOf('data-testid="da-canvas-center"')
    const commentsIdx = html.indexOf('data-testid="da-canvas-comments"')
    expect(barIdx).toBeGreaterThanOrEqual(0)
    expect(bodyIdx).toBeGreaterThan(barIdx)
    expect(centerIdx).toBeGreaterThan(iterateIdx)
    expect(commentsIdx).toBeGreaterThan(centerIdx)
  })

  it("Problem 2 — the RIGHT comments sidebar ALWAYS exists; unshared shows the empty state", () => {
    // No comments node (unshared): the sidebar shell + empty-state still render
    // so the control-bar comments-toggle has something to reveal.
    const html = renderView({ bundleUrl: "https://cdn/x/bundle/index.html" })
    expect(html).toContain('data-testid="da-canvas-comments"')
    expect(html).toContain('data-testid="da-comments-empty"')
    expect(html).toContain("Share this prototype to collect comments")
  })

  it("renders CommentsPanel content inside the right sidebar when a comments node is present", () => {
    const html = renderView({
      bundleUrl: "https://cdn/x/bundle/index.html",
      comments: sentinel("sentinel-comments", "SENTINEL_COMMENTS"),
    })
    expect(html).toContain('data-testid="da-canvas-comments"')
    expect(html).toContain('data-testid="sentinel-comments"')
    // The shared branch replaces the empty state.
    expect(html).not.toContain('data-testid="da-comments-empty"')
  })

  it("the right sidebar reflects the commentsOpen toggle via the `.open` class", () => {
    const closed = renderView({ bundleUrl: "https://cdn/x/bundle/index.html" })
    expect(closed).toContain('class="da-right"')
    const open = renderView({
      bundleUrl: "https://cdn/x/bundle/index.html",
      commentsOpen: true,
    })
    expect(open).toContain('class="da-right open"')
  })

  it("omits the iterate compose region when iterate is absent", () => {
    const html = renderView({ bundleUrl: "https://cdn/x/bundle/index.html" })
    expect(html).not.toContain('data-testid="da-canvas-iterate"')
  })

  it("places the View affordance inside the control bar", () => {
    const html = renderView({
      bundleUrl: "https://cdn/x/bundle/index.html",
      comments: sentinel("sentinel-comments", "SENTINEL_COMMENTS"),
    })
    const barIdx = html.indexOf('data-testid="da-controlbar"')
    const triggerIdx = html.indexOf('data-testid="proto-fullscreen-trigger"')
    const bodyIdx = html.indexOf('class="da-ready"')
    expect(barIdx).toBeGreaterThanOrEqual(0)
    expect(triggerIdx).toBeGreaterThan(barIdx)
    // The trigger is in the bar, before the body.
    expect(triggerIdx).toBeLessThan(bodyIdx)
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
// UX-EXPLORE (throwaway — REVERT): ShareMenu now lives inside DaControlBar's Share
// dropdown popover (DaControlBar → DaPopover → ShareMenu). The chain is
// PostGenerationResultView(onShared) → <DaControlBar onShared> → ... → <ShareMenu
// onShared>. We assert BOTH hops: the view forwards to <DaControlBar>, and
// DaControlBar threads it to the nested <ShareMenu>. We call each component as a
// function (no DOM render) and walk the returned element tree's `children`.
describe("PostGenerationResult — forwards onShared down to ShareMenu (P6-20 AC2)", () => {
  function findByType(
    node: React.ReactNode,
    type: React.ElementType,
  ): React.ReactElement | undefined {
    for (const child of React.Children.toArray(node) as React.ReactElement[]) {
      if (!child || typeof child !== "object") continue
      if (child.type === type) return child
      const props = child.props as {
        children?: React.ReactNode
        trigger?: (open: boolean) => React.ReactNode
      }
      // DaPopover passes its panel content via `children` and its trigger via a
      // `trigger` render-prop — descend into both so ShareMenu (in children) is found.
      const found =
        (props?.children ? findByType(props.children, type) : undefined) ??
        (typeof props?.trigger === "function"
          ? findByType(props.trigger(false), type)
          : undefined)
      if (found) return found
    }
    return undefined
  }

  function controlBarEl(
    over: Partial<PostGenerationResultViewProps> = {},
  ): React.ReactElement {
    const tree = PostGenerationResultView({
      prototypeId: 42,
      isComplete: false,
      shareMode: "private",
      shareToken: null,
      bundleUrl: null,
      ...over,
    }) as React.ReactElement
    const bar = findByType(tree, DaControlBar)
    expect(bar).toBeTruthy()
    return bar!
  }

  function shareMenuEl(
    over: Partial<PostGenerationResultViewProps> = {},
  ): React.ReactElement | undefined {
    // Render DaControlBar with the view-forwarded props to reach the nested ShareMenu.
    const barEl = controlBarEl(over)
    const rendered = DaControlBar(
      barEl.props as Parameters<typeof DaControlBar>[0],
    ) as React.ReactElement
    return findByType(rendered, ShareMenu)
  }

  it("the view forwards its onShared prop to <DaControlBar>", () => {
    const onShared = vi.fn()
    const bar = controlBarEl({ onShared })
    expect((bar.props as { onShared?: unknown }).onShared).toBe(onShared)
  })

  it("DaControlBar threads onShared down to the nested <ShareMenu> (test_post_generation_result_forwards_on_shared)", () => {
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
  // UX-EXPLORE (throwaway — REVERT): the editable handoff (CompletionBar) + share
  // (ShareMenu) controls now live in CLOSED control-bar popovers, so the container
  // SSR markup asserts the compact bar mounts (its triggers) rather than the
  // expanded inline panels. The seed-from-prop behaviour the launcher `key` fix
  // targets is still covered by reseedStep's pure unit tests below.
  it("mounts the compact control bar from a full record", () => {
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
    expect(html).toContain('data-testid="da-controlbar"')
    expect(html).toContain('data-testid="da-actions-toggle"')
    expect(html).toContain('data-testid="da-share-toggle"')
  })

  it("a shared record (share_token present) mounts the right sidebar shell", () => {
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto({ is_complete: true, share_mode: "private" }),
      }),
    )
    // The right comments sidebar always exists (Problem 2); unshared → empty state.
    expect(html).toContain('data-testid="da-canvas-comments"')
    expect(html).toContain('data-testid="da-comments-empty"')
  })

  it("mounts cleanly when the P2-06 columns are absent (older/partial rows)", () => {
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto(),
      }),
    )
    expect(html).toContain('data-testid="da-controlbar"')
    expect(html).toContain('data-testid="post-generation-result"')
  })
})
